"""Tests for thread-owned ADS library jobs."""

from __future__ import annotations

import threading
from dataclasses import FrozenInstanceError, is_dataclass

import pytest
import requests

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QThread

from adsfolio.api.libraries import Library
from adsfolio.config import ADSError, AuthenticationError, RateLimitError
from adsfolio.library_sync import (
    AddDocumentsResult,
    LibraryDestination,
    LibraryExportResult,
)


class FakeLibraries:
    """Record library API calls without making network requests."""

    def __init__(
        self,
        calls: list[tuple[object, ...]],
        *,
        error: Exception | None = None,
        on_add: object | None = None,
        documents: tuple[str, ...] = ("A",),
        permission: str = "owner",
        on_document: object | None = None,
    ) -> None:
        self.calls = calls
        self.error = error
        self.on_add = on_add
        self.documents = documents
        self.permission = permission
        self.on_document = on_document

    def iter_libraries(self):
        self.calls.append(("list", threading.get_ident()))
        if self.error is not None:
            raise self.error
        return iter(
            (
                Library(
                    id="L1",
                    name="Writable",
                    permission="owner",
                    public=False,
                    num_documents=0,
                ),
            )
        )

    def iter_documents(self, library_id: str, *, raw: bool = False):
        self.calls.append(("load", library_id, raw, threading.get_ident()))
        if self.error is not None:
            raise self.error
        for index, document in enumerate(self.documents):
            yield document
            if self.on_document is not None:
                self.on_document(index)

    def get_library(
        self,
        library_id: str,
        raw: bool = False,
        start: int = 0,
        rows: int = 2_000,
    ) -> Library:
        self.calls.append(
            (
                "get",
                library_id,
                raw,
                start,
                rows,
                threading.get_ident(),
            )
        )
        if self.error is not None:
            raise self.error
        return Library(
            id=library_id,
            name="Remote library",
            permission=self.permission,
            num_documents=len(self.documents),
            documents=list(self.documents),
        )

    def create_library(
        self,
        name: str,
        description: str,
        public: bool,
        bibcodes: list[str] | None,
    ) -> Library:
        self.calls.append(
            ("create", name, description, public, bibcodes, threading.get_ident())
        )
        if self.error is not None:
            raise self.error
        return Library(
            id="L-created",
            name=name,
            description=description,
            permission="owner",
            public=public,
            num_documents=0,
        )

    def add_documents(self, library_id: str, bibcodes: list[str]) -> dict[str, int]:
        self.calls.append(("add", library_id, tuple(bibcodes), threading.get_ident()))
        if self.on_add is not None:
            self.on_add()
        if self.error is not None:
            raise self.error
        return {"num_added": len(bibcodes)}


class FakeClient:
    """Client with observable worker-thread ownership and cleanup."""

    def __init__(
        self,
        calls: list[tuple[object, ...]],
        *,
        error: Exception | None = None,
        on_add: object | None = None,
        documents: tuple[str, ...] = ("A",),
        permission: str = "owner",
        on_document: object | None = None,
        export_error: Exception | None = None,
    ) -> None:
        self.calls = calls
        self.creation_thread = threading.get_ident()
        self.libraries = FakeLibraries(
            calls,
            error=error,
            on_add=on_add,
            documents=documents,
            permission=permission,
            on_document=on_document,
        )
        self.export = FakeExport(calls, error=export_error)
        self.close_thread: int | None = None

    def close(self) -> None:
        self.close_thread = threading.get_ident()
        self.calls.append(("close", self.close_thread))


class FakeExport:
    """Record export requests without making network calls."""

    def __init__(
        self,
        calls: list[tuple[object, ...]],
        *,
        error: Exception | None = None,
    ) -> None:
        self.calls = calls
        self.error = error

    def export_complete(
        self,
        bibcodes: tuple[str, ...],
        *,
        format: str = "bibtex",
        cancelled=None,
    ) -> str:
        del cancelled
        self.calls.append(("export", tuple(bibcodes), format, threading.get_ident()))
        if self.error is not None:
            raise self.error
        return "exported"


def _run_job(qtbot, request, client_factory):
    from adsfolio.gui.ads_library_jobs import AdsLibraryJob

    job = AdsLibraryJob(request, client_factory=client_factory)
    successes: list[object] = []
    failures: list[object] = []
    finished: list[None] = []
    job.succeeded.connect(successes.append)
    job.failed.connect(failures.append)
    job.finished.connect(lambda: finished.append(None))
    with qtbot.waitSignal(job.finished, timeout=2_000):
        job.start()
    return job, successes, failures, finished


@pytest.mark.parametrize(
    ("operation", "request_kwargs", "expected_type"),
    [
        ("LIST", {}, tuple),
        ("LOAD_DOCUMENTS", {"library_id": "L1"}, tuple),
        (
            "CREATE",
            {"name": "New", "description": "Description", "public": True},
            LibraryDestination,
        ),
        (
            "ADD",
            {"library_id": "L1", "bibcodes": ("A",)},
            AddDocumentsResult,
        ),
        (
            "RETRY",
            {"library_id": "L1", "bibcodes": ("A", "B")},
            AddDocumentsResult,
        ),
        (
            "EXPORT",
            {"library_id": "L1", "export_format": "ris"},
            LibraryExportResult,
        ),
    ],
)
def test_each_operation_uses_worker_owned_client_and_typed_success(
    qtbot,
    operation,
    request_kwargs,
    expected_type,
) -> None:
    from adsfolio.gui.ads_library_jobs import AdsLibraryOperation, AdsLibraryRequest

    calls: list[tuple[object, ...]] = []
    clients: list[FakeClient] = []

    def factory() -> FakeClient:
        client = FakeClient(calls)
        clients.append(client)
        return client

    request = AdsLibraryRequest(
        operation=getattr(AdsLibraryOperation, operation),
        **request_kwargs,
    )
    job, successes, failures, finished = _run_job(qtbot, request, factory)

    assert len(clients) == 1
    assert clients[0].creation_thread != threading.get_ident()
    assert clients[0].close_thread == clients[0].creation_thread
    assert len(successes) == 1
    assert len(failures) == 0
    assert len(finished) == 1
    assert isinstance(successes[0], expected_type)
    assert not job.running


def test_retry_dispatches_reconciliation_and_adds_only_missing_documents(qtbot) -> None:
    from adsfolio.gui.ads_library_jobs import AdsLibraryOperation, AdsLibraryRequest

    calls: list[tuple[object, ...]] = []

    def factory() -> FakeClient:
        return FakeClient(calls)

    request = AdsLibraryRequest(
        AdsLibraryOperation.RETRY,
        library_id="L1",
        bibcodes=("A", "B"),
    )

    _job, successes, failures, _finished = _run_job(qtbot, request, factory)

    assert failures == []
    assert successes == [
        AddDocumentsResult(
            acknowledged=("B",),
            already_present=("A",),
        )
    ]
    assert [call[0] for call in calls] == ["load", "list", "add", "close"]
    assert calls[2][2] == ("B",)


def test_cancellation_is_observed_before_next_add_request(qtbot) -> None:
    from adsfolio.gui.ads_library_jobs import AdsLibraryOperation, AdsLibraryRequest

    calls: list[tuple[object, ...]] = []
    holder: dict[str, object] = {}

    def on_add() -> None:
        holder["job"].cancel()  # type: ignore[union-attr]

    def factory() -> FakeClient:
        return FakeClient(calls, on_add=on_add)

    request = AdsLibraryRequest(
        AdsLibraryOperation.ADD,
        library_id="L1",
        bibcodes=tuple(f"B{i}" for i in range(2_001)),
    )
    from adsfolio.gui.ads_library_jobs import AdsLibraryJob

    job = AdsLibraryJob(request, client_factory=factory)
    holder["job"] = job
    successes: list[AddDocumentsResult] = []
    job.succeeded.connect(successes.append)
    with qtbot.waitSignal(job.finished, timeout=2_000):
        job.start()

    assert len(successes) == 1
    assert successes[0].acknowledged == tuple(f"B{i}" for i in range(2_000))
    assert successes[0].unattempted == ("B2000",)
    assert successes[0].cancelled is True
    assert [call[0] for call in calls] == ["list", "add", "close"]


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (AuthenticationError("bad token"), "authentication"),
        (RateLimitError("slow down"), "rate_limit"),
        pytest.param(requests.Timeout("timed out"), "timeout", id="timeout"),
        pytest.param(
            requests.ConnectionError("offline"), "connection", id="connection"
        ),
        (ADSError("ADS rejected request"), "ads"),
        (ValueError("programming error"), "unexpected"),
    ],
)
def test_errors_map_to_distinct_failures_and_still_close_client(
    qtbot,
    error,
    kind,
) -> None:
    from adsfolio.gui.ads_library_jobs import (
        AdsLibraryFailure,
        AdsLibraryOperation,
        AdsLibraryRequest,
    )

    calls: list[tuple[object, ...]] = []
    clients: list[FakeClient] = []

    def factory() -> FakeClient:
        client = FakeClient(calls, error=error)
        clients.append(client)
        return client

    request = AdsLibraryRequest(AdsLibraryOperation.LIST)
    job, successes, failures, finished = _run_job(qtbot, request, factory)

    assert successes == []
    assert len(failures) == 1
    assert failures[0] == AdsLibraryFailure(kind=kind, message=str(error))
    assert len(finished) == 1
    assert clients[0].close_thread == clients[0].creation_thread
    assert not job.running


def test_request_and_signal_values_are_immutable_and_request_has_no_gui_state() -> None:
    from adsfolio.gui.ads_library_jobs import (
        AdsLibraryFailure,
        AdsLibraryOperation,
        AdsLibraryRequest,
    )

    request = AdsLibraryRequest(
        AdsLibraryOperation.ADD,
        library_id="L1",
        bibcodes=("A",),
    )
    failure = AdsLibraryFailure(kind="ads", message="failure")

    assert is_dataclass(request)
    assert is_dataclass(failure)
    assert {field.name for field in request.__dataclass_fields__.values()} == {
        "operation",
        "library_id",
        "bibcodes",
        "name",
        "description",
        "public",
        "already_present",
        "export_format",
    }
    assert isinstance(request.bibcodes, tuple)
    with pytest.raises(FrozenInstanceError):
        request.library_id = "other"
    with pytest.raises(FrozenInstanceError):
        failure.message = "other"
    with pytest.raises(TypeError):
        AdsLibraryRequest(AdsLibraryOperation.LIST, registry=object())


def test_cancel_only_sets_event_and_does_not_terminate_thread(qtbot) -> None:
    from adsfolio.gui.ads_library_jobs import (
        AdsLibraryJob,
        AdsLibraryOperation,
        AdsLibraryRequest,
    )

    calls: list[tuple[object, ...]] = []
    request = AdsLibraryRequest(AdsLibraryOperation.LIST)
    job = AdsLibraryJob(request, client_factory=lambda: FakeClient(calls))
    terminate_calls: list[None] = []
    original_terminate = QThread.terminate
    QThread.terminate = lambda _thread: terminate_calls.append(None)  # type: ignore[method-assign]
    try:
        job.cancel()
    finally:
        QThread.terminate = original_terminate  # type: ignore[method-assign]

    assert terminate_calls == []


def test_export_job_uses_complete_remote_read_only_library(qtbot) -> None:
    from adsfolio.gui.ads_library_jobs import AdsLibraryOperation, AdsLibraryRequest

    calls: list[tuple[object, ...]] = []
    clients: list[FakeClient] = []

    def factory() -> FakeClient:
        client = FakeClient(
            calls,
            documents=("REMOTE-B", "REMOTE-A"),
            permission="read",
        )
        clients.append(client)
        return client

    request = AdsLibraryRequest(
        AdsLibraryOperation.EXPORT,
        library_id="L-read",
        bibcodes=("LOCAL-SELECTION",),
        export_format="ris",
    )
    _job, successes, failures, _finished = _run_job(qtbot, request, factory)

    assert failures == []
    assert successes == [
        LibraryExportResult(
            text="exported",
            bibcodes=("REMOTE-A", "REMOTE-B"),
        )
    ]
    export_calls = [call for call in calls if call[0] == "export"]
    assert export_calls == [
        ("export", ("REMOTE-A", "REMOTE-B"), "ris", export_calls[0][3])
    ]
    assert [call[0] for call in calls] == ["get", "load", "export", "close"]
    assert clients[0].close_thread == clients[0].creation_thread


def test_export_job_reports_export_failure_and_closes_client(qtbot) -> None:
    from adsfolio.gui.ads_library_jobs import (
        AdsLibraryFailure,
        AdsLibraryOperation,
        AdsLibraryRequest,
    )

    calls: list[tuple[object, ...]] = []
    error = ADSError("export failed")
    clients: list[FakeClient] = []

    def factory() -> FakeClient:
        client = FakeClient(calls, export_error=error)
        clients.append(client)
        return client

    request = AdsLibraryRequest(AdsLibraryOperation.EXPORT, library_id="L1")
    _job, successes, failures, _finished = _run_job(qtbot, request, factory)

    assert successes == []
    assert failures == [AdsLibraryFailure(kind="ads", message=str(error))]
    assert clients[0].close_thread == clients[0].creation_thread


def test_export_job_cancellation_before_network_returns_empty_result(qtbot) -> None:
    from adsfolio.gui.ads_library_jobs import (
        AdsLibraryJob,
        AdsLibraryOperation,
        AdsLibraryRequest,
    )

    calls: list[tuple[object, ...]] = []
    request = AdsLibraryRequest(AdsLibraryOperation.EXPORT, library_id="L1")
    job = AdsLibraryJob(request, client_factory=lambda: FakeClient(calls))
    successes: list[object] = []
    failures: list[object] = []
    job.succeeded.connect(successes.append)
    job.failed.connect(failures.append)
    job.cancel()
    with qtbot.waitSignal(job.finished, timeout=2_000):
        job.start()

    assert failures == []
    assert successes == [LibraryExportResult(text="", bibcodes=(), cancelled=True)]
    assert [call[0] for call in calls] == ["close"]


def test_export_job_cancellation_during_remote_enumeration_skips_export(qtbot) -> None:
    from adsfolio.gui.ads_library_jobs import (
        AdsLibraryJob,
        AdsLibraryOperation,
        AdsLibraryRequest,
    )

    calls: list[tuple[object, ...]] = []
    holder: dict[str, AdsLibraryJob] = {}

    def on_document(index: int) -> None:
        if index == 0:
            holder["job"].cancel()

    def factory() -> FakeClient:
        return FakeClient(
            calls,
            documents=("REMOTE-A", "REMOTE-B"),
            on_document=on_document,
        )

    request = AdsLibraryRequest(AdsLibraryOperation.EXPORT, library_id="L1")
    job = AdsLibraryJob(request, client_factory=factory)
    holder["job"] = job
    successes: list[object] = []
    failures: list[object] = []
    job.succeeded.connect(successes.append)
    job.failed.connect(failures.append)
    with qtbot.waitSignal(job.finished, timeout=2_000):
        job.start()

    assert failures == []
    assert successes == [LibraryExportResult(text="", bibcodes=(), cancelled=True)]
    assert [call[0] for call in calls] == ["get", "load", "close"]
