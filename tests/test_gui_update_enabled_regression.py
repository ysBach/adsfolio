"""Check Update by ID across automatic rechecks and watcher failures."""

from __future__ import annotations

import hashlib
import socket
import sys
import threading
from copy import deepcopy
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
import requests

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from pypdf import PdfWriter
from PySide6.QtCore import QItemSelectionModel

from adsfolio.config import Config
from adsfolio.gui import ads_recheck_job, main_window, watch
from adsfolio.gui.config import GuiConfig, RootEntry
from adsfolio.gui.main_window import MainWindow
from adsfolio.pdf.registry import PdfRegistry


@pytest.mark.parametrize(
    ("first_result", "resume_fails"),
    [
        ("success", False),
        ("failure", False),
        ("updated", False),
        ("success", True),
        ("failure", True),
    ],
)
def test_update_by_id_recovers_after_automatic_rechecks(
    monkeypatch, qtbot, tmp_path: Path, first_result: str, resume_fails: bool
) -> None:
    """Keep the selected PDF editable after each recheck cycle ends."""
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    monkeypatch.setattr(
        Config, "get_token", classmethod(lambda cls, *, prompt=True: "test-token")
    )
    network_attempts = []

    def deny_network(*args, **kwargs):
        network_attempts.append(True)
        raise AssertionError("This test must not use the network")

    monkeypatch.setattr(socket.socket, "connect", deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_network)
    monkeypatch.setattr(socket, "create_connection", deny_network)
    monkeypatch.setattr(requests.Session, "request", deny_network)

    root = tmp_path / "library"
    root.mkdir()
    pdf = root / "paper.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=32, height=32)
    writer.write(pdf)
    original_hash = hashlib.sha256(pdf.read_bytes()).hexdigest()
    stat = pdf.stat()
    registry = PdfRegistry(root)
    try:
        registry.upsert_file(
            root=root,
            relative_path=pdf.name,
            current_path=pdf.name,
            sha256=original_hash,
            size_bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            status="unresolved",
            metadata={"title": ["Fictional paper"]},
        )
    finally:
        registry.close()

    entered = threading.Event()
    release = threading.Event()
    recheck_calls = []
    backend = ModuleType("adsfolio.pdf.ads_recheck")

    def recheck(state, client, rename_settings, **kwargs):
        recheck_calls.append(state.directory)
        entered.set()
        if not release.wait(10):
            raise RuntimeError("The test did not release the recheck")
        if first_result == "failure" and len(recheck_calls) == 1:
            raise RuntimeError("Fictional recheck failure")
        return SimpleNamespace(
            checked=1,
            updated=int(first_result == "updated" and len(recheck_calls) == 1),
            renamed=0,
            failures=[],
            cancelled=False,
        )

    backend.recheck_existing = recheck
    monkeypatch.setitem(sys.modules, "adsfolio.pdf.ads_recheck", backend)

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def close(self):
            pass

    monkeypatch.setattr(ads_recheck_job, "ADSClient", FakeClient)
    monkeypatch.setattr(watch, "ADSClient", FakeClient)

    original_open = watch.open_registry_state
    opens = []

    def open_watcher_catalog(path):
        opens.append(path)
        if resume_fails and len(opens) == 2:
            raise OSError("Fictional watcher resume failure")
        return original_open(path)

    monkeypatch.setattr(watch, "open_registry_state", open_watcher_catalog)
    original_pause = watch._LibraryWatchWorker.pause_writes
    original_resume = watch._LibraryWatchWorker.resume_writes
    pauses = []
    resumes = []

    def record_pause(worker):
        already_paused = worker._paused
        original_pause(worker)
        pauses.append(already_paused)

    def record_resume(worker):
        original_resume(worker)
        resumes.append(worker._paused)

    monkeypatch.setattr(watch._LibraryWatchWorker, "pause_writes", record_pause)
    monkeypatch.setattr(watch._LibraryWatchWorker, "resume_writes", record_resume)
    config = GuiConfig(
        roots=[RootEntry(str(root), watch_on_start=True)],
        last_root=str(root),
        combined_view=True,
        combined_roots=[str(root)],
        check_updates_automatically=False,
        citation_styles=[],
    )
    monkeypatch.setattr(main_window, "load_config", lambda: deepcopy(config))
    monkeypatch.setattr(main_window, "save_config", lambda config: None)
    window = MainWindow()
    qtbot.addWidget(window)
    watcher_failures = []
    window.watch_controller.failed.connect(
        lambda failure: watcher_failures.append(failure.message)
    )
    try:
        qtbot.waitUntil(entered.is_set, timeout=5000)
        index = window._view_table_index(window.model.index(0, 0))
        assert index.isValid()
        window.table.expandAll()
        window.table.selectionModel().setCurrentIndex(
            index, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows
        )
        assert window._selected_edit_target() is not None

        release.set()
        qtbot.waitUntil(
            lambda: (
                window._ads_recheck_roots is None
                and bool(resumes)
                and not window._watch_refresh_timer.isActive()
                and not window._pending_watch_refresh_roots
            ),
            timeout=5000,
        )
        if resume_fails:
            qtbot.waitUntil(lambda: bool(watcher_failures), timeout=5000)
            assert watcher_failures == ["Fictional watcher resume failure"]
            assert resumes == [True]
        else:
            assert not watcher_failures
            assert resumes == [False]
        assert window.job is None
        assert window._ads_recheck_active_root is None
        assert window._selected_edit_target() is not None
        assert window.detail.update_button.isEnabled()
        assert window.update_selected_ads_action.isEnabled()

        # Emit the existing periodic timer rather than waiting 24 hours.
        window._ads_recheck_timer.timeout.emit()
        qtbot.waitUntil(
            lambda: len(pauses) >= 2 or window._ads_recheck_roots is None,
            timeout=5000,
        )
        qtbot.waitUntil(lambda: window._ads_recheck_roots is None, timeout=5000)
        qtbot.waitUntil(lambda: len(resumes) >= 2, timeout=5000)
        assert resumes[-1] is False
        assert len(recheck_calls) == 2
        assert window._ads_recheck_active_root is None
        assert window._pending_watch_dialog is None
        assert window._selected_edit_target() is not None
        assert window.detail.update_button.isEnabled()
        assert window.update_selected_ads_action.isEnabled()
    finally:
        release.set()
        window.close()
        assert not network_attempts
        assert hashlib.sha256(pdf.read_bytes()).hexdigest() == original_hash
