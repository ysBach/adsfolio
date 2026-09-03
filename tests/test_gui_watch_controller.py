"""Tests for the thread-owned GUI watch controller."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from adsapi.pdf.identify_settings import IdentifySettings
from adsapi.pdf.rename_settings import RenameSettings
from adsapi.pdf.watch_session import ArrivalIdentity, WatchEvent, WatchState


class FakeWorker(QObject):
    started = Signal(object)
    stopped = Signal(object)
    detected = Signal(object)
    outcome = Signal(object)
    failed = Signal(object)
    paused = Signal(object)
    stop_requested = Signal()
    process_requested = Signal(object, object, object)
    dismiss_requested = Signal(object)
    retry_requested = Signal(object)
    pause_requested = Signal()
    resume_requested = Signal()

    def __init__(self, root: Path, session_id: str) -> None:
        super().__init__()
        self.root = root
        self.session_id = session_id
        self.recursive = False
        self.stop_calls = 0
        self.commands = []
        self.command_threads = []
        self.stop_requested.connect(self.stop)
        self.process_requested.connect(self.process)
        self.dismiss_requested.connect(self.dismiss)
        self.retry_requested.connect(self.retry)
        self.pause_requested.connect(self.pause_writes)
        self.resume_requested.connect(self.resume_writes)

    def start(self) -> None:
        self.started.emit(self.session_id)

    def stop(self) -> None:
        self.stop_calls += 1
        self.stopped.emit(self.session_id)

    def process(self, event, identify_settings, rename_settings) -> None:
        self.command_threads.append(QThread.currentThread())
        self.commands.append(("process", event, identify_settings, rename_settings))

    def dismiss(self, event) -> None:
        self.command_threads.append(QThread.currentThread())
        self.commands.append(("dismiss", event))

    def retry(self, event) -> None:
        self.command_threads.append(QThread.currentThread())
        self.commands.append(("retry", event))

    def pause_writes(self) -> None:
        self.command_threads.append(QThread.currentThread())
        self.commands.append(("pause",))
        self.paused.emit(self.session_id)

    def resume_writes(self) -> None:
        self.command_threads.append(QThread.currentThread())
        self.commands.append(("resume",))


def _event(root: Path, session_id: str) -> WatchEvent:
    return WatchEvent(
        root=root,
        session_id=session_id,
        identity=ArrivalIdentity(1, 2, None, 0),
        path=root / "new.pdf",
        state=WatchState.DETECTED,
    )


def test_controller_manages_roots_independently(qtbot, tmp_path):
    from adsapi.gui.watch import LibraryWatchController

    workers = {}
    controller = LibraryWatchController(
        worker_factory=lambda root, recursive, session_id: workers.setdefault(
            root, FakeWorker(root, session_id)
        )
    )
    controller.start_root(tmp_path / "one", recursive=False)
    controller.start_root(tmp_path / "two", recursive=True)
    qtbot.wait(20)

    assert controller.is_watching(tmp_path / "one")
    assert controller.stop_root(tmp_path / "one")
    assert not controller.is_watching(tmp_path / "one")
    assert controller.is_watching(tmp_path / "two")
    assert controller.stop_root(tmp_path / "two")


def test_start_root_waits_until_worker_reports_ready(qtbot, tmp_path):
    from adsapi.gui.watch import LibraryWatchController

    class DelayedWorker(FakeWorker):
        def start(self) -> None:
            QTimer.singleShot(20, lambda: self.started.emit(self.session_id))

    controller = LibraryWatchController(
        worker_factory=lambda root, recursive, session_id: DelayedWorker(
            root, session_id
        )
    )
    root = tmp_path / "library"

    session_id = controller.start_root(root, recursive=False)

    try:
        assert controller.is_watching(root)
        assert controller.session_id(root) == session_id
    finally:
        controller.stop_all()


def test_obsolete_session_event_is_discarded(qtbot, tmp_path):
    from adsapi.gui.watch import LibraryWatchController

    workers = []
    received = []

    def worker_factory(root, recursive, session_id):
        del recursive
        worker = FakeWorker(root, session_id)
        workers.append(worker)
        return worker

    controller = LibraryWatchController(worker_factory=worker_factory)
    controller.detected.connect(received.append)
    root = tmp_path / "library"
    controller.start_root(root, recursive=False)
    qtbot.wait(20)
    old_session = controller.session_id(root)
    assert old_session is not None
    old_worker = workers[-1]
    assert controller.stop_root(root)
    controller.start_root(root, recursive=False)
    qtbot.wait(20)

    old_worker.detected.emit(_event(root, old_session))
    qtbot.wait(20)

    assert received == []
    assert controller.stop_root(root)


def test_controller_rejects_unavailable_worker_start(qtbot, tmp_path):
    from adsapi.gui.watch import LibraryWatchController, WatchFailure

    class UnavailableWorker(FakeWorker):
        def start(self) -> None:
            self.failed.emit(
                WatchFailure(
                    root=self.root,
                    session_id=self.session_id,
                    message="stable identity unavailable",
                )
            )

    failures = []
    controller = LibraryWatchController(
        worker_factory=lambda root, recursive, session_id: UnavailableWorker(
            root, session_id
        )
    )
    controller.failed.connect(failures.append)
    controller.start_root(tmp_path / "library", recursive=False)
    qtbot.wait(20)

    assert not controller.is_watching(tmp_path / "library")
    assert len(failures) == 1
    assert failures[0].message == "stable identity unavailable"
    assert controller.stop_all()


def test_stop_root_forwards_worker_acknowledgement(qtbot, tmp_path):
    from adsapi.gui.watch import LibraryWatchController

    stopped = []
    controller = LibraryWatchController(
        worker_factory=lambda root, recursive, session_id: FakeWorker(root, session_id)
    )
    controller.stopped.connect(stopped.append)
    root = tmp_path / "library"
    session_id = controller.start_root(root, recursive=False)

    assert controller.stop_root(root)

    assert stopped == [session_id]


def test_controller_forwards_commands_only_for_current_session(qtbot, tmp_path):
    from adsapi.gui.watch import LibraryWatchController

    workers = {}
    controller = LibraryWatchController(
        worker_factory=lambda root, recursive, session_id: workers.setdefault(
            root, FakeWorker(root, session_id)
        )
    )
    root = tmp_path / "library"
    controller.start_root(root, recursive=False)
    qtbot.wait(20)
    event = _event(root, controller.session_id(root))

    controller.process(event, IdentifySettings(), RenameSettings())
    controller.dismiss(event)
    controller.retry(event)
    controller.pause_writes(root)
    controller.resume_writes(root)
    qtbot.wait(20)

    try:
        assert [command[0] for command in workers[root].commands] == [
            "process",
            "dismiss",
            "retry",
            "pause",
            "resume",
        ]
        assert all(
            thread is workers[root].thread() for thread in workers[root].command_threads
        )
    finally:
        controller.stop_all()


def test_worker_drains_intake_before_finishing_baseline(monkeypatch, tmp_path):
    import adsapi.gui.watch as watch_module

    root = tmp_path / "library"
    source = root / "new.pdf"
    root.mkdir()
    source.write_bytes(b"%PDF")
    timeline = []

    class FakeSession:
        def observe(self, snapshot, source_kind):
            del snapshot, source_kind
            timeline.append("observe")
            return None

        def finish_baseline(self, snapshots):
            del snapshots
            timeline.append("finish")

    session = FakeSession()
    registry = MagicMock()
    client = MagicMock()
    monkeypatch.setattr(watch_module, "open_registry_state", lambda root: registry)
    monkeypatch.setattr(watch_module, "ADSClient", lambda: client)
    monkeypatch.setattr(watch_module, "PdfIdentifier", MagicMock())
    monkeypatch.setattr(watch_module, "PdfRenamer", MagicMock())
    monkeypatch.setattr(watch_module, "PdfWatcher", MagicMock())
    monkeypatch.setattr(
        watch_module.WatchSession,
        "starting",
        classmethod(lambda cls, root, session_id: session),
    )
    worker = watch_module._LibraryWatchWorker(root, "session-1")
    monkeypatch.setattr(
        worker,
        "_start_observer",
        lambda: worker._enqueue(source, watch_module.WatchSource.CREATED),
    )
    monkeypatch.setattr(worker, "_scan_snapshots", lambda: [])

    worker.start()

    assert timeline[:2] == ["observe", "finish"]
    worker._cleanup()


def test_worker_pause_closes_and_resume_reopens_registry(monkeypatch, tmp_path):
    import adsapi.gui.watch as watch_module

    original = MagicMock()
    reopened = MagicMock()
    worker = watch_module._LibraryWatchWorker(tmp_path, "session-1")
    worker._registry = original
    worker._session = MagicMock()
    monkeypatch.setattr(watch_module, "open_registry_state", lambda root: reopened)

    worker.pause_writes()

    original.close.assert_called_once_with()
    assert worker._registry is None

    worker.resume_writes()

    assert worker._registry is reopened
    worker._cleanup()


def test_worker_stop_between_resumed_processes_discards_second(
    monkeypatch, qtbot, tmp_path
) -> None:
    import adsapi.gui.watch as watch_module

    root = tmp_path / "library"
    first = _event(root, "session-1")
    second = WatchEvent(
        root=root,
        session_id="session-1",
        identity=ArrivalIdentity(3, 4, None, 0),
        path=root / "second.pdf",
        state=WatchState.DETECTED,
    )
    worker = watch_module._LibraryWatchWorker(root, "session-1")
    worker._paused = True
    worker._session = MagicMock()
    worker._watcher = MagicMock()
    worker._pending_processes = {
        first.identity: (first, IdentifySettings(), RenameSettings()),
        second.identity: (second, IdentifySettings(), RenameSettings()),
    }
    monkeypatch.setattr(
        watch_module,
        "open_registry_state",
        lambda root: MagicMock(),
    )
    started = []
    monkeypatch.setattr(
        worker,
        "process",
        lambda event, identify, rename: started.append(event),
    )
    monkeypatch.setattr(worker, "thread", lambda: None)

    worker.resume_writes()
    QTimer.singleShot(0, worker.stop)
    qtbot.waitUntil(lambda: worker._stopping)
    qtbot.wait(10)

    assert started == [first]
    assert worker._pending_processes == {}


def test_worker_cleanup_uses_public_ads_client_close(tmp_path):
    import adsapi.gui.watch as watch_module

    client = MagicMock()
    worker = watch_module._LibraryWatchWorker(tmp_path, "session-1")
    worker._client = client

    worker._cleanup()

    client.close.assert_called_once_with()
    client._base.close.assert_not_called()


def test_worker_rejects_replaced_identity_before_processing(tmp_path):
    import adsapi.gui.watch as watch_module

    root = tmp_path / "library"
    source = root / "new.pdf"
    root.mkdir()
    source.write_bytes(b"%PDF")
    event = _event(root, "session-1")
    session = MagicMock()
    session.snapshot.return_value = watch_module.FileSnapshot(
        path=source,
        size_bytes=4,
        mtime_ns=1,
        device=event.identity.device,
        inode=event.identity.inode,
    )
    watcher = MagicMock()
    worker = watch_module._LibraryWatchWorker(root, "session-1")
    worker._session = session
    worker._watcher = watcher
    worker._registry = MagicMock()
    outcomes = []
    worker.outcome.connect(outcomes.append)

    worker.process(event, IdentifySettings(), RenameSettings())

    watcher.process_event.assert_not_called()
    assert outcomes[0].event.state is WatchState.STALE


def test_worker_classifies_move_within_root_as_modified(monkeypatch, tmp_path):
    import watchdog.observers

    import adsapi.gui.watch as watch_module

    root = tmp_path / "library"
    root.mkdir()
    source = root / "before.pdf"
    destination = root / "after.pdf"
    scheduled = {}

    class FakeObserver:
        def schedule(self, handler, path, *, recursive):
            del path, recursive
            scheduled["handler"] = handler

        def start(self):
            pass

    monkeypatch.setattr(watchdog.observers, "Observer", FakeObserver)
    worker = watch_module._LibraryWatchWorker(root, "session-1")
    worker._start_observer()

    event = type(
        "MovedEvent",
        (),
        {
            "is_directory": False,
            "src_path": str(source),
            "dest_path": str(destination),
        },
    )()
    scheduled["handler"].on_moved(event)

    assert (destination.resolve(), watch_module.WatchSource.MODIFIED) in worker._pending
