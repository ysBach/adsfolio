"""Thread-owned PDF watch workers and their GUI-thread controller."""

from __future__ import annotations

import logging
import stat
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from PySide6.QtCore import QEventLoop, QObject, Qt, QThread, QTimer, Signal, Slot

from ..client import ADSClient
from ..config import AuthenticationError
from ..pdf.files import iter_pdf_paths
from ..pdf.identify import PdfIdentifier
from ..pdf.identify_settings import IdentifySettings
from ..pdf.registry import CentralPdfRegistryState, open_registry_state
from ..pdf.rename_settings import RenameSettings
from ..pdf.renamer import PdfRenamer
from ..pdf.watch_session import (
    ArrivalIdentity,
    FileSnapshot,
    WatchEvent,
    WatchOutcome,
    WatchSession,
    WatchSource,
    WatchState,
)
from ..pdf.watcher import PdfWatcher

logger = logging.getLogger(__name__)

__all__ = ["LibraryWatchController", "WatchFailure"]


@dataclass(frozen=True)
class WatchFailure:
    """Immutable failure emitted by a watch worker or controller."""

    root: Path
    session_id: str
    message: str
    event: WatchEvent | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.root, Path):
            object.__setattr__(self, "root", Path(self.root))


class _WorkerProtocol(Protocol):
    started: Any
    stopped: Any
    detected: Any
    outcome: Any
    failed: Any
    paused: Any

    def start(self) -> None: ...

    def stop(self) -> None: ...


class _LibraryWatchWorker(QObject):
    """Own all filesystem, registry, and ADS resources for one root."""

    started = Signal(object)
    stopped = Signal(object)
    detected = Signal(object)
    outcome = Signal(object)
    failed = Signal(object)
    paused = Signal(object)
    pending_ready = Signal()

    stop_requested = Signal()
    process_requested = Signal(object, object, object)
    dismiss_requested = Signal(object)
    retry_requested = Signal(object)
    pause_requested = Signal()
    resume_requested = Signal()

    def __init__(self, root: Path, session_id: str) -> None:
        super().__init__()
        self.root = Path(root).resolve()
        self.session_id = session_id
        self._recursive = False
        self._registry: CentralPdfRegistryState | None = None
        self._client: ADSClient | None = None
        self._watcher: PdfWatcher | None = None
        self._session: WatchSession | None = None
        self._observer: Any = None
        self._timer: QTimer | None = None
        self._stopping = False
        self._paused = False
        self._pending: dict[tuple[Path, WatchSource], None] = {}
        self._pending_lock = threading.Lock()
        self._pending_processes: dict[
            ArrivalIdentity,
            tuple[WatchEvent, IdentifySettings, RenameSettings],
        ] = {}
        self._pending_process_scheduled = False
        self.stop_requested.connect(self.stop)
        self.process_requested.connect(self.process)
        self.dismiss_requested.connect(self.dismiss)
        self.retry_requested.connect(self.retry)
        self.pause_requested.connect(self.pause_writes)
        self.resume_requested.connect(self.resume_writes)
        self.pending_ready.connect(self._drain, Qt.QueuedConnection)

    @Slot()
    def start(self) -> None:
        """Create worker-owned resources, start intake, and baseline the root."""
        try:
            if not self.root.is_dir():
                raise FileNotFoundError(f"Watch root does not exist: {self.root}")
            root_stat = self.root.stat()
            if root_stat.st_dev == 0 or root_stat.st_ino == 0:
                raise RuntimeError("stable filesystem identity unavailable")
            self._registry = open_registry_state(self.root)
            try:
                self._client = ADSClient()
            except AuthenticationError:
                self._client = None
            identifier = PdfIdentifier(self._client)
            renamer = PdfRenamer(client=self._client)
            self._watcher = PdfWatcher(identifier=identifier, renamer=renamer)
            self._session = WatchSession.starting(self.root, self.session_id)
            self._start_observer()
            baseline = self._scan_snapshots()
            self._drain()
            self._session.finish_baseline(baseline)
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._reconcile)
            self._timer.start(10_000)
            self.started.emit(self.session_id)
        except Exception as exc:
            self._emit_failure(str(exc))
            self._cleanup()

    def configure(self, recursive: bool) -> None:
        """Set root recursion before the worker starts."""
        self._recursive = recursive

    def _start_observer(self) -> None:
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError as exc:
            raise RuntimeError("watchdog is required for GUI watching") from exc

        worker = self

        class Handler(FileSystemEventHandler):
            def on_created(self, event: Any) -> None:
                if not event.is_directory:
                    worker._enqueue(Path(event.src_path), WatchSource.CREATED)

            def on_modified(self, event: Any) -> None:
                if not event.is_directory:
                    worker._enqueue(Path(event.src_path), WatchSource.MODIFIED)

            def on_moved(self, event: Any) -> None:
                if event.is_directory:
                    return
                source = Path(event.src_path).resolve()
                destination = Path(event.dest_path).resolve()
                if not destination.is_relative_to(worker.root):
                    return
                source_kind = (
                    WatchSource.MODIFIED
                    if source.is_relative_to(worker.root)
                    else WatchSource.MOVED_IN
                )
                worker._enqueue(destination, source_kind)

        observer = Observer()
        observer.schedule(Handler(), str(self.root), recursive=self._recursive)
        observer.start()
        self._observer = observer

    def _scan_snapshots(self) -> list[FileSnapshot]:
        """Snapshot every PDF under the root.

        Raises
        ------
        OSError
            If a directory cannot be listed or a file cannot be examined for a
            reason other than having disappeared. The scan is then incomplete
            and must not be reconciled.

        Notes
        -----
        Symbolic links to files outside the root are skipped.
        """
        snapshots: list[FileSnapshot] = []
        for path in iter_pdf_paths(self.root, self._recursive):
            if path.name.startswith("."):
                continue
            try:
                file_stat = path.lstat()
                if stat.S_ISLNK(file_stat.st_mode):
                    # A symbolic link to a file outside the root is not part
                    # of the library, as in PdfWatcher._changed_pdf_paths.
                    # Other entries need no resolve(): the root is resolved
                    # and iter_pdf_paths never enters symlinked directories.
                    if not path.resolve().is_relative_to(self.root):
                        continue
                    file_stat = path.stat()
            except FileNotFoundError:
                continue
            snapshot = FileSnapshot.from_stat(path, file_stat)
            if snapshot.device == 0 or snapshot.inode == 0:
                raise RuntimeError("stable filesystem identity unavailable")
            snapshots.append(snapshot)
        return snapshots

    def _enqueue(self, path: Path, source: WatchSource) -> None:
        candidate = Path(path).resolve()
        if candidate.suffix.lower() != ".pdf" or candidate.name.startswith("."):
            return
        if not candidate.is_relative_to(self.root):
            return
        with self._pending_lock:
            self._pending[(candidate, source)] = None
        self.pending_ready.emit()

    @Slot()
    def _drain(self) -> None:
        if self._session is None:
            return
        with self._pending_lock:
            pending = list(self._pending)
            self._pending.clear()
        for path, source in pending:
            try:
                snapshot = FileSnapshot.from_path(path)
            except OSError:
                continue
            if snapshot.device == 0 or snapshot.inode == 0:
                self._emit_failure("stable filesystem identity unavailable")
                continue
            event = self._session.observe(snapshot, source)
            if event is not None:
                self.detected.emit(event)

    @Slot()
    def _reconcile(self) -> None:
        if self._session is None:
            return
        try:
            self._session.start_scan()
            events = self._session.reconcile(self._scan_snapshots())
        except Exception as exc:
            self._emit_failure(str(exc))
            return
        for event in events:
            self.detected.emit(event)

    @Slot(object, object, object)
    def process(
        self,
        event: WatchEvent,
        identify_settings: IdentifySettings,
        rename_settings: RenameSettings,
    ) -> None:
        if self._stopping or self._session is None or self._watcher is None:
            return
        if event.session_id != self.session_id or event.root.resolve() != self.root:
            return
        if self._paused:
            self._pending_processes[event.identity] = (
                event,
                identify_settings,
                rename_settings,
            )
            return
        if self._registry is None:
            return
        stored_snapshot = self._session.snapshot(event.identity)
        if stored_snapshot is None:
            self._emit_stale(event, "Arrival is no longer current")
            return
        try:
            actual_snapshot = FileSnapshot.from_path(stored_snapshot.path)
        except OSError as exc:
            self._emit_stale(event, str(exc), path=stored_snapshot.path)
            return
        actual_key = (
            actual_snapshot.device,
            actual_snapshot.inode,
            actual_snapshot.birth_time_ns,
        )
        identity_key = (
            event.identity.device,
            event.identity.inode,
            event.identity.birth_time_ns,
        )
        if actual_key != identity_key:
            self._emit_stale(
                event,
                "Arrival filesystem identity was replaced",
                path=stored_snapshot.path,
            )
            return
        try:
            current = self._session.begin_processing(event.identity)
        except ValueError as exc:
            self.outcome.emit(
                WatchOutcome(
                    event=replace(event, state=WatchState.STALE), message=str(exc)
                )
            )
            return
        try:
            outcome = self._watcher.process_event(
                current,
                self._registry,
                rename_settings,
                identify_settings,
            )
            self._session.complete(current.identity, outcome.event.state)
            self.outcome.emit(outcome)
        except Exception as exc:
            failed_event = replace(current, state=WatchState.FAILED)
            try:
                self._session.complete(current.identity, WatchState.FAILED)
            except ValueError:
                pass
            self._emit_failure(str(exc), event=failed_event)

    def _emit_stale(
        self,
        event: WatchEvent,
        message: str,
        *,
        path: Path | None = None,
    ) -> None:
        if self._session is not None:
            try:
                self._session.complete(event.identity, WatchState.STALE)
            except ValueError:
                pass
        self.outcome.emit(
            WatchOutcome(
                event=replace(
                    event,
                    path=path if path is not None else event.path,
                    state=WatchState.STALE,
                ),
                message=message,
            )
        )

    @Slot(object)
    def dismiss(self, event: WatchEvent) -> None:
        if self._session is None:
            return
        try:
            self._session.dismiss(event.identity)
        except ValueError as exc:
            self._emit_failure(str(exc), event=event)

    @Slot(object)
    def retry(self, event: WatchEvent) -> None:
        if self._session is None:
            return
        try:
            retried = self._session.retry(event.identity)
        except ValueError as exc:
            self._emit_failure(str(exc), event=event)
            return
        self.detected.emit(retried)

    @Slot()
    def pause_writes(self) -> None:
        if self._paused:
            return
        self._paused = True
        if self._registry is not None:
            self._registry.close()
            self._registry = None
        self.paused.emit(self.session_id)

    @Slot()
    def resume_writes(self) -> None:
        if self._stopping or not self._paused or self._session is None:
            return
        try:
            self._registry = open_registry_state(self.root)
        except Exception as exc:
            self._emit_failure(str(exc))
            return
        self._paused = False
        self._schedule_pending_process()

    def _schedule_pending_process(self) -> None:
        """Schedule one queued process when the worker can accept work."""
        if (
            self._pending_process_scheduled
            or self._stopping
            or self._paused
            or not self._pending_processes
        ):
            return
        self._pending_process_scheduled = True
        QTimer.singleShot(0, self._process_next_pending)

    @Slot()
    def _process_next_pending(self) -> None:
        """Run one queued process and yield before considering another."""
        self._pending_process_scheduled = False
        if (
            self._stopping
            or self._paused
            or self._session is None
            or self._watcher is None
            or self._registry is None
            or not self._pending_processes
        ):
            return
        identity = next(iter(self._pending_processes))
        event, identify_settings, rename_settings = self._pending_processes.pop(
            identity
        )
        self.process(event, identify_settings, rename_settings)
        self._schedule_pending_process()

    @Slot()
    def stop(self) -> None:
        """Stop intake and release every resource owned by this worker."""
        if self._stopping:
            return
        self._stopping = True
        self._cleanup()
        self.stopped.emit(self.session_id)
        thread = self.thread()
        if thread is not None:
            thread.quit()

    def _emit_failure(self, message: str, event: WatchEvent | None = None) -> None:
        self.failed.emit(
            WatchFailure(
                root=self.root,
                session_id=self.session_id,
                message=message,
                event=event,
            )
        )

    def _cleanup(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None
        if self._observer is not None:
            self._observer.stop()
            self._observer.join()
            self._observer = None
        if self._registry is not None:
            self._registry.close()
            self._registry = None
        if self._client is not None:
            self._client.close()
            self._client = None
        self._watcher = None
        self._session = None
        self._pending_processes.clear()
        self._pending_process_scheduled = False
        with self._pending_lock:
            self._pending.clear()


@dataclass
class _RootHandle:
    root: Path
    session_id: str
    thread: QThread
    worker: _WorkerProtocol
    started: bool = False
    stopped: bool = False
    stopped_ack: threading.Event = field(default_factory=threading.Event)


class LibraryWatchController(QObject):
    """Own one thread-owned watch worker per normalized library root."""

    started = Signal(object)
    stopped = Signal(object)
    detected = Signal(object)
    outcome = Signal(object)
    failed = Signal(object)
    paused = Signal(object)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        worker_factory: Callable[[Path, bool, str], _WorkerProtocol] | None = None,
    ) -> None:
        super().__init__(parent)
        self._handles: dict[Path, _RootHandle] = {}
        self._worker_factory = worker_factory or self._make_worker

    @staticmethod
    def _make_worker(
        root: Path, recursive: bool, session_id: str
    ) -> _LibraryWatchWorker:
        worker = _LibraryWatchWorker(root, session_id)
        worker.configure(recursive)
        return worker

    @staticmethod
    def _root_key(root: Path | str) -> Path:
        return Path(root).expanduser().resolve()

    def start_root(self, root: Path, *, recursive: bool) -> str:
        """Start a fresh watch session for one root."""
        key = self._root_key(root)
        if key in self._handles:
            existing = self._handles[key]
            if not self.stop_root(key):
                return existing.session_id
        session_id = uuid.uuid4().hex
        worker = self._worker_factory(key, recursive, session_id)
        thread = QThread(self)
        handle = _RootHandle(key, session_id, thread, worker)
        self._handles[key] = handle
        worker.started.connect(self._on_started)
        worker.stopped.connect(self._on_stopped)
        worker.stopped.connect(handle.stopped_ack.set, Qt.DirectConnection)
        worker.stopped.connect(thread.quit, Qt.DirectConnection)
        worker.detected.connect(self._forward_detected)
        worker.outcome.connect(self._forward_outcome)
        worker.failed.connect(self._on_failed)
        worker.paused.connect(self._forward_paused)
        worker.moveToThread(thread)
        thread.started.connect(worker.start)
        ready_loop = QEventLoop()
        worker.started.connect(ready_loop.quit)
        worker.failed.connect(ready_loop.quit)
        thread.finished.connect(ready_loop.quit)
        thread.start()
        ready_loop.exec()
        return session_id

    def _handle_for_session(self, session_id: str) -> _RootHandle | None:
        return next(
            (
                handle
                for handle in self._handles.values()
                if handle.session_id == session_id
            ),
            None,
        )

    @Slot(object)
    def _on_started(self, session_id: str) -> None:
        handle = self._handle_for_session(session_id)
        if handle is None:
            return
        handle.started = True
        self.started.emit(session_id)

    @Slot(object)
    def _on_stopped(self, session_id: str) -> None:
        handle = self._handle_for_session(session_id)
        if handle is None:
            return
        handle.stopped = True
        self.stopped.emit(session_id)

    @Slot(object)
    def _forward_detected(self, event: WatchEvent) -> None:
        handle = self._handles.get(self._root_key(event.root))
        if handle is not None and event.session_id == handle.session_id:
            self.detected.emit(event)

    @Slot(object)
    def _forward_outcome(self, outcome: WatchOutcome) -> None:
        event = outcome.event
        handle = self._handles.get(self._root_key(event.root))
        if handle is not None and event.session_id == handle.session_id:
            self.outcome.emit(outcome)

    @Slot(object)
    def _forward_paused(self, session_id: str) -> None:
        if self._handle_for_session(session_id) is not None:
            self.paused.emit(session_id)

    @Slot(object)
    def _on_failed(self, failure: WatchFailure) -> None:
        root = self._root_key(failure.root)
        handle = self._handles.get(root)
        if handle is None or failure.session_id != handle.session_id:
            return
        self.failed.emit(failure)
        if not handle.started:
            self.stop_root(root)

    def stop_root(self, root: Path) -> bool:
        """Stop one root and wait for worker acknowledgement and thread finish."""
        key = self._root_key(root)
        handle = self._handles.get(key)
        if handle is None:
            return False
        self._request(handle.worker, "stop_requested", "stop")
        stopped = handle.thread.wait(5000)
        if not stopped or not handle.stopped_ack.is_set():
            return False
        if not handle.stopped:
            handle.stopped = True
            self.stopped.emit(handle.session_id)
        self._handles.pop(key, None)
        handle.worker.deleteLater()
        handle.thread.deleteLater()
        return True

    def stop_all(self) -> bool:
        """Stop all roots, retaining handles if any worker cannot stop."""
        result = True
        for root in list(self._handles):
            result = self.stop_root(root) and result
        return result

    def is_watching(self, root: Path) -> bool:
        """Return whether a root has a currently started worker."""
        handle = self._handles.get(self._root_key(root))
        return handle is not None and handle.started

    def session_id(self, root: Path) -> str | None:
        """Return the current session identifier for a root."""
        handle = self._handles.get(self._root_key(root))
        return handle.session_id if handle is not None else None

    def _current_event(
        self, event: WatchEvent
    ) -> tuple[_RootHandle, WatchEvent] | None:
        handle = self._handles.get(self._root_key(event.root))
        if handle is None or handle.session_id != event.session_id:
            return None
        return handle, event

    @staticmethod
    def _request(
        worker: _WorkerProtocol,
        signal_name: str,
        method_name: str,
        *args: object,
    ) -> None:
        request_signal = getattr(worker, signal_name, None)
        if request_signal is not None:
            request_signal.emit(*args)
            return
        getattr(worker, method_name)(*args)

    def process(
        self,
        event: WatchEvent,
        identify_settings: IdentifySettings,
        rename_settings: RenameSettings,
    ) -> None:
        """Submit one current-session event for processing."""
        current = self._current_event(event)
        if current is not None:
            self._request(
                current[0].worker,
                "process_requested",
                "process",
                event,
                identify_settings,
                rename_settings,
            )

    def dismiss(self, event: WatchEvent) -> None:
        """Dismiss one current-session event."""
        current = self._current_event(event)
        if current is not None:
            self._request(current[0].worker, "dismiss_requested", "dismiss", event)

    def retry(self, event: WatchEvent) -> None:
        """Retry one current-session failed event."""
        current = self._current_event(event)
        if current is not None:
            self._request(current[0].worker, "retry_requested", "retry", event)

    def pause_writes(self, root: Path) -> None:
        """Pause worker catalog writes while retaining filesystem intake."""
        handle = self._handles.get(self._root_key(root))
        if handle is not None:
            self._request(handle.worker, "pause_requested", "pause_writes")

    def resume_writes(self, root: Path) -> None:
        """Resume worker catalog writes after a foreground job."""
        handle = self._handles.get(self._root_key(root))
        if handle is not None:
            self._request(handle.worker, "resume_requested", "resume_writes")
