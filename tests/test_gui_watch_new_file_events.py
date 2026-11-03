"""Check which watcher events represent newly added PDF paths."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6")

from adsfolio.gui.watch import _LibraryWatchWorker
from adsfolio.pdf.watch_session import FileSnapshot, WatchSession, WatchSource


def test_new_file_flag_excludes_baseline_changes_and_repeated_events(tmp_path: Path):
    existing = tmp_path / "existing.pdf"
    existing.write_bytes(b"old")
    worker = _LibraryWatchWorker(tmp_path, "session")
    worker._session = WatchSession(
        tmp_path, "session", [FileSnapshot.from_path(existing)]
    )
    worker._baseline_ready = True
    detected = []
    worker.detected.connect(detected.append)

    existing.write_bytes(b"new content")
    worker._pending[(existing, WatchSource.MODIFIED)] = None
    worker._drain()
    assert detected[-1].newly_added is False

    added = tmp_path / "new.pdf"
    added.write_bytes(b"new file")
    worker._pending[(added, WatchSource.CREATED)] = None
    worker._drain()
    assert detected[-1].path == added
    assert detected[-1].newly_added is True
    count = len(detected)

    worker._pending[(added, WatchSource.MODIFIED)] = None
    worker._drain()
    assert len(detected) == count


def test_reconciliation_marks_unseen_file_but_not_replacement(
    tmp_path: Path, monkeypatch
):
    existing = tmp_path / "existing.pdf"
    existing.write_bytes(b"old")
    worker = _LibraryWatchWorker(tmp_path, "session")
    worker._session = WatchSession(
        tmp_path, "session", [FileSnapshot.from_path(existing)]
    )
    worker._baseline_ready = True
    detected = []
    worker.detected.connect(detected.append)

    replacement = tmp_path / "replacement.tmp"
    replacement.write_bytes(b"replaced")
    replacement.replace(existing)
    added = tmp_path / "added.pdf"
    added.write_bytes(b"added")
    monkeypatch.setattr(
        worker,
        "_scan_snapshots",
        lambda: [FileSnapshot.from_path(existing), FileSnapshot.from_path(added)],
    )
    monkeypatch.setattr(worker, "_mark_missing", lambda snapshots: 0)
    worker._reconcile()

    assert {event.path.name: event.newly_added for event in detected} == {
        "existing.pdf": False,
        "added.pdf": True,
    }


def test_events_from_startup_do_not_request_sound(tmp_path: Path):
    path = tmp_path / "startup.pdf"
    path.write_bytes(b"pdf")
    worker = _LibraryWatchWorker(tmp_path, "session")
    worker._session = WatchSession.starting(tmp_path, "session")
    detected = []
    worker.detected.connect(detected.append)
    worker._pending[(path, WatchSource.CREATED)] = None
    worker._drain()

    assert len(detected) == 1
    assert detected[0].newly_added is False


def test_restarted_watcher_does_not_sound_for_existing_pdfs(
    tmp_path: Path, monkeypatch, qtbot
):
    import adsfolio.gui.watch as watch_module

    known = tmp_path / "known.pdf"
    known.write_bytes(b"previously registered")
    pending = tmp_path / "pending.pdf"
    pending.write_bytes(b"catalogued while stopped")
    untracked = tmp_path / "untracked.pdf"
    untracked.write_bytes(b"added while stopped")
    snapshot = FileSnapshot.from_path(known)
    pending_snapshot = FileSnapshot.from_path(pending)
    registry = MagicMock()
    registry.registry.catalog.query_files.return_value = [
        {
            "current_path": known.name,
            "size_bytes": snapshot.size_bytes,
            "mtime_ns": snapshot.mtime_ns,
        },
        {
            "current_path": pending.name,
            "size_bytes": pending_snapshot.size_bytes,
            "mtime_ns": pending_snapshot.mtime_ns,
            "_adsfolio_watch_pending": True,
        },
    ]
    monkeypatch.setattr(watch_module, "open_registry_state", lambda root: registry)
    monkeypatch.setattr(watch_module, "ADSClient", MagicMock())
    worker = _LibraryWatchWorker(tmp_path, "restarted")
    monkeypatch.setattr(worker, "_start_observer", lambda: None)
    monkeypatch.setattr(worker, "_mark_missing", lambda snapshots: 0)
    detected = []
    failures = []
    worker.detected.connect(detected.append)
    worker.failed.connect(failures.append)

    try:
        worker.start()
        assert worker._baseline_ready
        assert not failures
        assert [event.path for event in detected] == [pending]
        worker._reconcile()
        assert all(not event.newly_added for event in detected)

        added = tmp_path / "arrived.pdf"
        added.write_bytes(b"added after restart")
        worker._pending[(added, WatchSource.CREATED)] = None
        worker._drain()
        assert detected[-1].path == added
        assert detected[-1].newly_added
    finally:
        worker._cleanup()
