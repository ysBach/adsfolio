"""Tests for the thread-owned GUI watch controller."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from adsfolio.pdf.identify_settings import IdentifySettings
from adsfolio.pdf.rename_settings import RenameSettings
from adsfolio.pdf.watch_session import ArrivalIdentity, WatchEvent, WatchState


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
    from adsfolio.gui.watch import LibraryWatchController

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
    from adsfolio.gui.watch import LibraryWatchController

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
    from adsfolio.gui.watch import LibraryWatchController

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
    from adsfolio.gui.watch import LibraryWatchController, WatchFailure

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
    from adsfolio.gui.watch import LibraryWatchController

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
    from adsfolio.gui.watch import LibraryWatchController

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
    import adsfolio.gui.watch as watch_module

    root = tmp_path / "library"
    source = root / "new.pdf"
    root.mkdir()
    source.write_bytes(b"%PDF")
    timeline = []

    class FakeSession:
        def observe(self, snapshot, source_kind):
            del snapshot, source_kind
            timeline.append("observe")

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
    monkeypatch.setattr(worker, "_scan_snapshots", list)

    worker.start()

    assert timeline[:2] == ["observe", "finish"]
    worker._cleanup()


def test_worker_pause_closes_and_resume_reopens_registry(monkeypatch, tmp_path):
    import adsfolio.gui.watch as watch_module

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
    import adsfolio.gui.watch as watch_module

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
    import adsfolio.gui.watch as watch_module

    client = MagicMock()
    worker = watch_module._LibraryWatchWorker(tmp_path, "session-1")
    worker._client = client

    worker._cleanup()

    client.close.assert_called_once_with()
    client._base.close.assert_not_called()


def test_worker_rejects_replaced_identity_before_processing(tmp_path):
    import adsfolio.gui.watch as watch_module

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

    import adsfolio.gui.watch as watch_module

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


def test_scan_snapshots_matches_uppercase_and_skips_hidden(tmp_path):
    import adsfolio.gui.watch as watch_module

    (tmp_path / "A.PDF").write_bytes(b"x")
    (tmp_path / ".hidden.pdf").write_bytes(b"x")
    worker = watch_module._LibraryWatchWorker(tmp_path, "session-1")

    assert [s.path.name for s in worker._scan_snapshots()] == ["A.PDF"]


def test_scan_failure_is_reported_and_not_reconciled(monkeypatch, tmp_path):
    import adsfolio.gui.watch as watch_module

    (tmp_path / "A.pdf").write_bytes(b"x")
    worker = watch_module._LibraryWatchWorker(tmp_path, "session-1")
    worker._session = MagicMock()
    failures: list[object] = []
    monkeypatch.setattr(worker, "_emit_failure", failures.append)

    def deny(path):
        raise PermissionError("denied")

    monkeypatch.setattr(watch_module.Path, "lstat", deny)

    worker._reconcile()

    assert failures == ["denied"]
    worker._session.reconcile.assert_not_called()


def test_scan_snapshots_skips_symlink_outside_root_and_keeps_inside(tmp_path):
    import adsfolio.gui.watch as watch_module

    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"x")
    (root / "real.pdf").write_bytes(b"x")
    (root / "escape.pdf").symlink_to(outside)
    (root / "inside.pdf").symlink_to(root / "real.pdf")
    worker = watch_module._LibraryWatchWorker(root, "session-1")

    names = sorted(s.path.name for s in worker._scan_snapshots())

    assert names == ["inside.pdf", "real.pdf"]


def test_enqueue_ignores_path_outside_root(tmp_path):
    import adsfolio.gui.watch as watch_module

    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"x")
    worker = watch_module._LibraryWatchWorker(root, "session-1")

    worker._enqueue(outside, watch_module.WatchSource.CREATED)

    assert worker._pending == {}


def _worker_with_registry(watch_module, root, rows, *, recursive=False):
    from adsfolio.pdf.watch_session import WatchSession

    worker = watch_module._LibraryWatchWorker(root, "session-1")
    worker.configure(recursive)
    worker._session = WatchSession(root, "session-1")
    worker._registry = MagicMock()
    catalog = worker._registry.registry.catalog
    catalog.query_files.return_value = rows
    catalog.mark_missing_locations.return_value = 1
    return worker, catalog


def test_reconcile_marks_only_absent_paths_missing_and_signals(tmp_path):
    import adsfolio.gui.watch as watch_module

    root = tmp_path.resolve()
    (root / "kept.pdf").write_bytes(b"x")
    (root / ".hidden.pdf").write_bytes(b"x")
    (root / "outside.pdf").symlink_to(tmp_path.parent)
    rows = [
        {"current_path": n}
        for n in ("kept.pdf", ".hidden.pdf", "outside.pdf", "gone.pdf")
    ]
    worker, catalog = _worker_with_registry(watch_module, root, rows)
    signalled = []
    worker.reconciled.connect(signalled.append)

    worker._reconcile()

    (call,) = catalog.mark_missing_locations.call_args_list
    assert call.args[0] == root
    assert call.args[1] == {"kept.pdf", ".hidden.pdf", "outside.pdf"}
    assert call.kwargs == {"recursive": False}
    assert signalled == [root]


def test_reconcile_without_missing_files_emits_no_signal(tmp_path):
    import adsfolio.gui.watch as watch_module

    worker, catalog = _worker_with_registry(watch_module, tmp_path.resolve(), [])
    catalog.mark_missing_locations.return_value = 0
    signalled = []
    worker.reconciled.connect(signalled.append)
    worker._reconcile()
    assert signalled == []


def test_reconcile_with_unreadable_catalog_path_marks_nothing(monkeypatch, tmp_path):
    import adsfolio.gui.watch as watch_module

    root = tmp_path.resolve()
    worker, catalog = _worker_with_registry(
        watch_module, root, [{"current_path": "x.pdf"}]
    )
    failures = []
    monkeypatch.setattr(worker, "_emit_failure", failures.append)
    real_lstat = watch_module.Path.lstat

    def lstat(self, *args, **kwargs):
        if self.name == "x.pdf":
            raise PermissionError("denied")
        return real_lstat(self, *args, **kwargs)

    monkeypatch.setattr(watch_module.Path, "lstat", lstat)
    worker._reconcile()
    catalog.mark_missing_locations.assert_not_called()
    assert failures == ["denied"]


def test_reconcile_while_paused_writes_nothing_and_runs_on_resume(
    monkeypatch, tmp_path
):
    import adsfolio.gui.watch as watch_module

    root = tmp_path.resolve()
    worker, catalog = _worker_with_registry(watch_module, root, [])
    reopened, _ = MagicMock(), None
    reopened.registry.catalog.query_files.return_value = []
    reopened.registry.catalog.mark_missing_locations.return_value = 0
    monkeypatch.setattr(watch_module, "open_registry_state", lambda r: reopened)
    scheduled = []
    monkeypatch.setattr(
        watch_module.QTimer, "singleShot", lambda ms, fn: scheduled.append(fn)
    )
    worker.pause_writes()
    worker._reconcile()
    catalog.mark_missing_locations.assert_not_called()
    assert worker._reconcile_deferred

    worker.resume_writes()

    assert worker._reconcile in scheduled or any(
        getattr(f, "__name__", "") == "_reconcile" for f in scheduled
    )
    worker._reconcile()
    reopened.registry.catalog.mark_missing_locations.assert_called_once()


def test_startup_baseline_queues_only_stale_pending_or_changed_catalog_files(
    tmp_path,
):
    import adsfolio.gui.watch as watch_module
    from adsfolio.pdf.watch_session import FileSnapshot

    root = tmp_path.resolve()
    names = ["plain", "stale", "pending", "facts", "untracked", "nested"]
    snaps = [
        FileSnapshot(root / f"{n}.pdf", 10, 20, 1, i + 1) for i, n in enumerate(names)
    ]

    def row(name, **extra):
        return {
            "current_path": f"{name}.pdf",
            "size_bytes": 10,
            "mtime_ns": 20,
            "metadata": extra,
        }

    rows = [
        row("plain"),
        row("stale", _adsfolio_metadata_stale=True),
        row("pending", **{watch_module.WATCH_PENDING_KEY: True}),
        {**row("facts"), "size_bytes": 9},
        {**row("nested"), "_adsfolio_metadata_stale": True},
    ]
    worker, _ = _worker_with_registry(watch_module, root, rows)

    baseline, changed = worker._split_startup_snapshots(snaps)

    assert {s.path.stem for s in changed} == {"stale", "pending", "facts", "nested"}
    assert {s.path.stem for s in baseline} == {"plain", "untracked"}


def test_start_emits_catalog_changed_file_as_created_arrival(monkeypatch, tmp_path):
    import adsfolio.gui.watch as watch_module

    root = tmp_path.resolve()
    (root / "a.pdf").write_bytes(b"x")
    registry = MagicMock()
    registry.registry.catalog.query_files.return_value = [
        {
            "current_path": "a.pdf",
            "size_bytes": 1,
            "mtime_ns": 0,
            "metadata": {"_adsfolio_metadata_stale": True},
        }
    ]
    monkeypatch.setattr(watch_module, "open_registry_state", lambda r: registry)
    monkeypatch.setattr(watch_module, "ADSClient", lambda: MagicMock())
    for name in ("PdfIdentifier", "PdfRenamer", "PdfWatcher"):
        monkeypatch.setattr(watch_module, name, MagicMock())
    worker = watch_module._LibraryWatchWorker(root, "session-1")
    monkeypatch.setattr(worker, "_start_observer", lambda: None)
    detected = []
    worker.detected.connect(detected.append)

    worker.start()

    assert [e.path.name for e in detected] == ["a.pdf"]
    worker._cleanup()


def test_deleted_and_moved_out_events_request_reconcile(monkeypatch, tmp_path):
    import watchdog.observers

    import adsfolio.gui.watch as watch_module

    root = tmp_path.resolve()
    scheduled = {}

    class FakeObserver:
        def schedule(self, handler, path, *, recursive):
            scheduled["handler"] = handler

        def start(self):
            pass

    monkeypatch.setattr(watchdog.observers, "Observer", FakeObserver)
    worker = watch_module._LibraryWatchWorker(root, "session-1")
    worker._start_observer()
    handler = scheduled["handler"]

    def event(**kw):
        return type("E", (), {"is_directory": False, **kw})()

    handler.on_deleted(event(src_path=str(root / "a.pdf")))
    assert worker._reconcile_wanted
    worker._reconcile_wanted = False
    handler.on_moved(
        event(src_path=str(root / "a.pdf"), dest_path=str(root.parent / "a.pdf"))
    )
    assert worker._reconcile_wanted


def test_process_announces_change_made_while_processing(tmp_path):
    import adsfolio.gui.watch as watch_module
    from adsfolio.pdf.watch_session import (
        FileSnapshot,
        WatchOutcome,
        WatchSession,
        WatchSource,
    )

    root = tmp_path.resolve()
    pdf = root / "a.pdf"
    pdf.write_bytes(b"x")
    worker = watch_module._LibraryWatchWorker(root, "session-1")
    worker._session = WatchSession(root, "session-1")
    worker._registry = MagicMock()
    event = worker._session.observe(FileSnapshot.from_path(pdf), WatchSource.CREATED)

    def process_event(current, *args):
        pdf.write_bytes(b"xyz")
        worker._session.observe(FileSnapshot.from_path(pdf), WatchSource.MODIFIED)
        return WatchOutcome(
            event=type(current)(**{**current.__dict__, "state": WatchState.RESOLVED})
        )

    worker._watcher = MagicMock()
    worker._watcher.process_event.side_effect = process_event
    detected = []
    worker.detected.connect(detected.append)

    worker.process(event, IdentifySettings(), RenameSettings())

    assert [e.identity.generation for e in detected] == [1]


def test_controller_forwards_reconciled_for_current_root(qtbot, tmp_path):
    from adsfolio.gui.watch import LibraryWatchController

    class ReconcileWorker(FakeWorker):
        reconciled = Signal(object)

    root = tmp_path.resolve()
    controller = LibraryWatchController(
        worker_factory=lambda r, rec, sid: ReconcileWorker(r, sid)
    )
    controller.start_root(root, recursive=False)
    received = []
    controller.reconciled.connect(received.append)
    controller._handles[root].worker.reconciled.emit(root)
    assert received == [root]
    controller.stop_all()
