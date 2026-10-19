"""Thread-owned one-shot jobs for ADS library operations."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, TypeAlias

import requests
from PySide6.QtCore import QObject, QThread, Signal, Slot

from ..client import ADSClient
from ..config import ADSError, AuthenticationError, RateLimitError
from ..library_sync import (
    AddDocumentsResult,
    AdsLibraryService,
    LibraryDestination,
    LibraryExportResult,
)

__all__ = [
    "AdsLibraryFailure",
    "AdsLibraryJob",
    "AdsLibraryOperation",
    "AdsLibraryRequest",
]

AdsLibraryResult: TypeAlias = (
    tuple[LibraryDestination, ...]
    | tuple[str, ...]
    | LibraryDestination
    | AddDocumentsResult
    | LibraryExportResult
)


class AdsLibraryOperation(str, Enum):
    """One ADS library operation handled by an `AdsLibraryJob`."""

    LIST = "list"
    LOAD_DOCUMENTS = "load_documents"
    CREATE = "create"
    ADD = "add"
    RETRY = "retry"
    EXPORT = "export"


@dataclass(frozen=True)
class AdsLibraryRequest:
    """Immutable input for one ADS library job."""

    operation: AdsLibraryOperation
    library_id: str = ""
    bibcodes: tuple[str, ...] = ()
    name: str = ""
    description: str = ""
    public: bool = False
    already_present: tuple[str, ...] = ()
    export_format: str = "bibtex"

    def __post_init__(self) -> None:
        """Freeze sequence inputs as tuples at the thread boundary."""
        object.__setattr__(self, "operation", AdsLibraryOperation(self.operation))
        object.__setattr__(self, "bibcodes", tuple(self.bibcodes))
        object.__setattr__(self, "already_present", tuple(self.already_present))


@dataclass(frozen=True)
class AdsLibraryFailure:
    """Immutable, categorized failure from an ADS library job."""

    kind: Literal[
        "authentication",
        "rate_limit",
        "timeout",
        "connection",
        "ads",
        "unexpected",
    ]
    message: str


def _failure_for(error: Exception) -> AdsLibraryFailure:
    """Map an ADS or transport exception to its UI-facing category."""
    if isinstance(error, AuthenticationError):
        kind = "authentication"
    elif isinstance(error, RateLimitError):
        kind = "rate_limit"
    elif isinstance(error, requests.Timeout):
        kind = "timeout"
    elif isinstance(error, requests.ConnectionError):
        kind = "connection"
    elif isinstance(error, ADSError):
        kind = "ads"
    else:
        kind = "unexpected"
    return AdsLibraryFailure(kind=kind, message=str(error))


class _AdsLibraryWorker(QObject):
    """Execute one request while owning its ADS client and service."""

    succeeded = Signal(object)
    failed = Signal(object)
    finished = Signal()

    def __init__(
        self,
        request: AdsLibraryRequest,
        client_factory: Callable[[], Any],
        cancellation: threading.Event,
    ) -> None:
        super().__init__()
        self._request = request
        self._client_factory = client_factory
        self._cancellation = cancellation

    @Slot()
    def run(self) -> None:
        """Create resources, execute one operation, and shut down cleanly."""
        client: Any | None = None
        result: AdsLibraryResult | None = None
        failure: AdsLibraryFailure | None = None
        try:
            client = self._client_factory()
            service = AdsLibraryService(client)
            result = self._dispatch(service, client)
        except Exception as error:  # noqa: BLE001 - map unexpected job failures
            failure = _failure_for(error)
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception as error:  # noqa: BLE001 - cleanup must not escape
                    if failure is None:
                        failure = _failure_for(error)

        if failure is None:
            self.succeeded.emit(result)
        else:
            self.failed.emit(failure)
        self.finished.emit()
        thread = self.thread()
        if thread is not None:
            thread.quit()

    def _dispatch(self, service: AdsLibraryService, client: Any) -> AdsLibraryResult:
        request = self._request
        if request.operation is AdsLibraryOperation.LIST:
            return service.list_destinations()
        if request.operation is AdsLibraryOperation.LOAD_DOCUMENTS:
            return tuple(client.libraries.iter_documents(request.library_id))
        if request.operation is AdsLibraryOperation.EXPORT:
            return service.export_library(
                request.library_id,
                format=request.export_format,
                cancelled=self._cancellation.is_set,
            )
        if request.operation is AdsLibraryOperation.CREATE:
            return service.create_destination(
                request.name,
                request.description,
                request.public,
            )
        if request.operation is AdsLibraryOperation.ADD:
            return service.add_documents(
                request.library_id,
                request.bibcodes,
                already_present=request.already_present,
                cancelled=self._cancellation.is_set,
            )
        if request.operation is AdsLibraryOperation.RETRY:
            return service.reconcile_and_retry(
                request.library_id,
                request.bibcodes,
                cancelled=self._cancellation.is_set,
            )
        raise ValueError(f"Unsupported ADS library operation: {request.operation!r}")


class AdsLibraryJob(QObject):
    """Run exactly one ADS library request outside the GUI thread."""

    succeeded = Signal(object)
    failed = Signal(object)
    finished = Signal()

    def __init__(
        self,
        request: AdsLibraryRequest,
        parent: QObject | None = None,
        *,
        client_factory: Callable[[], Any] = ADSClient,
    ) -> None:
        super().__init__(parent)
        self.request = request
        self._cancellation = threading.Event()
        self._started = False
        self._finished = False
        self._terminal_received = False
        self._worker_finished = False
        self._thread_finished = False
        self._thread = QThread(self)
        self._worker = _AdsLibraryWorker(
            request,
            client_factory,
            self._cancellation,
        )
        self._worker.moveToThread(self._thread)
        self._worker.succeeded.connect(self._on_succeeded)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.finished.connect(self._on_worker_finished)
        self._thread.started.connect(self._worker.run)
        self._thread.finished.connect(self._on_thread_finished)

    def start(self) -> None:
        """Start the one-shot worker once and return immediately."""
        if self._started:
            return
        self._started = True
        self._thread.start()

    def cancel(self) -> None:
        """Request cancellation before the next cancellable service request."""
        self._cancellation.set()

    @property
    def running(self) -> bool:
        """Whether the worker thread is still running."""
        return self._started and not self._finished

    @Slot(object)
    def _on_succeeded(self, result: object) -> None:
        self._terminal_received = True
        self.succeeded.emit(result)
        self._maybe_emit_finished()

    @Slot(object)
    def _on_failed(self, failure: AdsLibraryFailure) -> None:
        self._terminal_received = True
        self.failed.emit(failure)
        self._maybe_emit_finished()

    @Slot()
    def _on_worker_finished(self) -> None:
        self._worker_finished = True
        self._maybe_emit_finished()

    @Slot()
    def _on_thread_finished(self) -> None:
        # QThread.finished precedes thread-local cleanup. Join before callers
        # can destroy this job and its thread object.
        self._thread.wait()
        self._thread.deleteLater()
        self._thread_finished = True
        self._maybe_emit_finished()

    def _maybe_emit_finished(self) -> None:
        if not (
            self._terminal_received and self._worker_finished and self._thread_finished
        ):
            return
        if self._finished:
            return
        self._finished = True
        self.finished.emit()
