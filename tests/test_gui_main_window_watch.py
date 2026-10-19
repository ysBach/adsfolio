"""Deterministic MainWindow integration tests for library watching."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtCore import QItemSelectionModel, QObject, Qt, Signal
from PySide6.QtWidgets import QDialog, QMessageBox, QToolBar

from adsfolio.config import Config
from adsfolio.gui.config import GuiConfig, RootEntry
from adsfolio.gui.main_window import MainWindow
from adsfolio.pdf.identify_settings import IdentifySettings, IdentifyStrategy
from adsfolio.pdf.registry import CentralPdfRegistryState
from adsfolio.pdf.rename_settings import RenameSettings
from adsfolio.pdf.watch_session import (
    ArrivalIdentity,
    FileSnapshot,
    WatchEvent,
    WatchOutcome,
    WatchSession,
    WatchSource,
    WatchState,
)


class FakeWatchController(QObject):
    started = Signal(object)
    stopped = Signal(object)
    detected = Signal(object)
    outcome = Signal(object)
    failed = Signal(object)
    paused = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.start_calls: list[tuple[Path, bool]] = []
        self.stop_calls: list[Path] = []
        self.process_calls: list[tuple[object, object, object]] = []
        self.dismiss_calls: list[object] = []
        self.retry_calls: list[object] = []
        self.pause_calls: list[Path] = []
        self.resume_calls: list[Path] = []
        self.active: dict[Path, str] = {}
        self.stop_result = True
        self.stop_all_result = True
        self.auto_ack_pause = True

    def start_root(self, root: Path, *, recursive: bool) -> str:
        normalized = Path(root).resolve()
        session_id = f"session-{len(self.start_calls) + 1}"
        self.start_calls.append((normalized, recursive))
        self.active[normalized] = session_id
        self.started.emit(session_id)
        return session_id

    def stop_root(self, root: Path) -> bool:
        normalized = Path(root).resolve()
        self.stop_calls.append(normalized)
        if not self.stop_result:
            return False
        session_id = self.active.pop(normalized, None)
        if session_id is not None:
            self.stopped.emit(session_id)
        return True

    def stop_all(self) -> bool:
        if not self.stop_all_result:
            return False
        active = tuple(self.active.items())
        self.active.clear()
        for _root, session_id in active:
            self.stopped.emit(session_id)
        return True

    def is_watching(self, root: Path) -> bool:
        return Path(root).resolve() in self.active

    def session_id(self, root: Path) -> str | None:
        return self.active.get(Path(root).resolve())

    def process(self, event, identify_settings, rename_settings) -> None:
        self.process_calls.append((event, identify_settings, rename_settings))

    def dismiss(self, event) -> None:
        self.dismiss_calls.append(event)

    def retry(self, event) -> None:
        self.retry_calls.append(event)

    def pause_writes(self, root: Path) -> None:
        normalized = Path(root).resolve()
        self.pause_calls.append(normalized)
        session_id = self.session_id(normalized)
        if session_id is not None and self.auto_ack_pause:
            self.paused.emit(session_id)

    def resume_writes(self, root: Path) -> None:
        self.resume_calls.append(Path(root).resolve())


@pytest.fixture(autouse=True)
def _redirect_task5_config_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    monkeypatch.setattr(
        Config,
        "get_config_dir",
        classmethod(lambda cls: config_dir),
    )


def _window_factory(monkeypatch, qtbot, config: GuiConfig):
    import adsfolio.gui.main_window as main_window_module

    controller = FakeWatchController()
    saves = []
    monkeypatch.setattr(main_window_module, "load_config", lambda: config)
    monkeypatch.setattr(
        main_window_module,
        "save_config",
        lambda saved: saves.append(deepcopy(saved)),
    )
    window = MainWindow(watch_controller=controller)
    qtbot.addWidget(window)
    return window, controller, saves


def test_task5_main_window_tests_use_temporary_config_dir(
    monkeypatch, qtbot, tmp_path
) -> None:
    _window_factory(monkeypatch, qtbot, GuiConfig())

    assert Config.get_config_dir().resolve() == (tmp_path / "config").resolve()


def _event(root: Path, session_id: str, *, generation: int = 0) -> WatchEvent:
    return WatchEvent(
        root=root.resolve(),
        session_id=session_id,
        identity=ArrivalIdentity(1, 2, None, generation),
        path=root / f"new-{generation}.pdf",
        state=WatchState.DETECTED,
    )


def test_f5_reconciles_files_preserving_selection_and_history(
    monkeypatch, qtbot, tmp_path
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    for name in ("keep.pdf", "move.pdf", "gone.pdf"):
        (root / name).write_bytes(name.encode())
    window, _controller, _saves = _window_factory(
        monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(root))])
    )
    registry = window.registries[str(root)]
    before = {row["current_path"]: row for row in registry.query_files()}
    for row in range(window.model.rowCount()):
        record = window.model.record_at(row)
        if record["current_path"] in {"keep.pdf", "move.pdf"}:
            index = window.model.index(row, 0)
            window.table.selectionModel().select(
                index, QItemSelectionModel.Select | QItemSelectionModel.Rows
            )
            if record["current_path"] == "move.pdf":
                window.table.selectionModel().setCurrentIndex(
                    index, QItemSelectionModel.NoUpdate
                )
    (root / "move.pdf").rename(root / "renamed.pdf")
    (root / "new.pdf").write_bytes(b"new")
    (root / "gone.pdf").unlink()
    window.show()
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)

    qtbot.keyClick(window.table, Qt.Key_F5)
    qtbot.waitUntil(
        lambda: (
            window.job is None and "history retained" in window.status.currentMessage()
        )
    )

    assert {r["current_path"] for r in window._selected_records()} == {
        "keep.pdf",
        "renamed.pdf",
    }
    assert window._selected_record()["current_path"] == "renamed.pdf"
    registry = window.registries[str(root)]
    assert registry.catalog.get_file(root, "gone.pdf")["missing_at"] is not None
    assert (
        registry.catalog.get_file(root, "renamed.pdf")["attachment_id"]
        == before["move.pdf"]["attachment_id"]
    )
    assert "1 added" in window.status.currentMessage()
    assert "1 moved" in window.status.currentMessage()
    assert "1 missing" in window.status.currentMessage()
    assert "history retained" in window.log.toPlainText()
    toolbar = window.findChild(QToolBar, "search_toolbar")
    assert window.refresh_action in toolbar.actions()
    assert (
        window.remove_missing_action not in window.ads_refresh_button.menu().actions()
    )


def test_local_refresh_waits_for_watcher_and_keeps_origin_root(
    monkeypatch, qtbot, tmp_path
) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "gone.pdf").write_bytes(b"gone")
    window, controller, _saves = _window_factory(
        monkeypatch,
        qtbot,
        GuiConfig(roots=[RootEntry(str(first)), RootEntry(str(second))]),
    )
    window._select_root(first)
    window.watch_current_action.trigger()
    controller.auto_ack_pause = False
    (first / "gone.pdf").unlink()

    window.refresh_action.trigger()

    assert window.job is None
    assert window._pending_watch_dialog == ("local_refresh", first.resolve())
    assert not window.refresh_action.isEnabled()
    assert not window.ads_refresh_button.isEnabled()
    window._select_root(second)
    controller.paused.emit(controller.session_id(first))
    qtbot.waitUntil(
        lambda: window.job is None and window._local_refresh_context is None
    )

    assert window.current_root == second
    assert window.registries[str(first)].query_files() == []
    assert controller.resume_calls == [first]
    assert window.refresh_action.isEnabled()


def test_failed_local_refresh_keeps_history_and_restores_controls(
    monkeypatch, qtbot, tmp_path
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    (root / "paper.pdf").write_bytes(b"paper")
    window, _controller, _saves = _window_factory(
        monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(root))])
    )
    root.rename(tmp_path / "offline")

    window.refresh_action.trigger()
    qtbot.waitUntil(
        lambda: window.job is None and window._local_refresh_context is None
    )

    assert len(window.registries[str(root)].query_files()) == 1
    assert window.refresh_action.isEnabled()
    assert "Local refresh failed" in window.status.currentMessage()


def test_close_during_local_refresh_waits_for_worker_cleanup(
    monkeypatch, qtbot, tmp_path
) -> None:
    import adsfolio.gui.refresh_job as refresh_job_module

    root = tmp_path / "library"
    root.mkdir()
    (root / "paper.pdf").write_bytes(b"paper")
    window, _controller, _saves = _window_factory(
        monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(root))])
    )
    entered, release = Event(), Event()
    refresh = refresh_job_module.PdfRegistry.refresh_local_files

    def paused_refresh(registry, **kwargs):
        entered.set()
        assert release.wait(5), "Test worker was not released"
        return refresh(registry, **kwargs)

    monkeypatch.setattr(
        refresh_job_module.PdfRegistry, "refresh_local_files", paused_refresh
    )
    window.show()
    window.refresh_action.trigger()
    qtbot.waitUntil(entered.is_set)
    job = window.job
    try:
        window.close()
        assert window._close_requested
        assert job.running
    finally:
        release.set()
        qtbot.waitUntil(lambda: window.job is None)

    assert not job.running
    assert window._local_refresh_context is None
    assert not window.isVisible()


def test_close_cancels_local_refresh_waiting_for_watcher_pause(
    monkeypatch, qtbot, tmp_path
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    window, controller, _saves = _window_factory(
        monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(root))])
    )
    window.watch_current_action.trigger()
    controller.auto_ack_pause = False
    session_id = controller.session_id(root)
    window.refresh_action.trigger()

    window.close()
    controller.paused.emit(session_id)

    assert window.job is None
    assert window._local_refresh_context is None
    assert window._pending_watch_dialog is None


def test_ads_toolbar_reuses_selected_and_identifier_refresh(
    monkeypatch, qtbot, tmp_path
) -> None:
    from PySide6.QtWidgets import QInputDialog

    root = tmp_path / "library"
    root.mkdir()
    (root / "paper.pdf").write_bytes(b"paper")
    window, _controller, _saves = _window_factory(
        monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(root))])
    )
    registry = window.registries[str(root)]
    registry.replace_file_metadata(root, "paper.pdf", {"bibcode": "B1"})
    registry.update_file_status(root, "paper.pdf", "resolved")
    window.refresh()
    window.table.setCurrentIndex(window.model.index(0, 0))
    calls = []
    monkeypatch.setattr(
        window,
        "_start_ads_refresh",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    window.refresh_selected_ads_action.trigger()
    monkeypatch.setattr(
        QInputDialog, "getText", lambda *args, **kwargs: ("10.1234/paper", True)
    )
    window.update_selected_ads_action.trigger()

    assert calls[0][0][:2] == ("--bibcode", "B1")
    assert calls[1][0][:2] == ("--identifier", "10.1234/paper")
    assert "--relative-path" in calls[0][0]
    assert "--relative-path" in calls[1][0]
    assert all(
        "Rename" in action.text()
        for action in window.ads_refresh_button.menu().actions()
        if not action.isSeparator() and action is not window.link_bibcodes_action
    )


def test_deleted_selected_file_and_empty_views_clear_stale_detail_and_actions(
    monkeypatch, qtbot, tmp_path
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    paper = first / "paper.pdf"
    paper.write_bytes(b"%PDF-1.4\n%test\n")
    window, _controller, _saves = _window_factory(
        monkeypatch,
        qtbot,
        GuiConfig(roots=[RootEntry(str(first)), RootEntry(str(second))]),
    )
    window._select_root(first)
    registry = window.registries[str(first.resolve())]
    registry.replace_file_metadata(
        first.resolve(),
        "paper.pdf",
        {"title": ["Selected paper"], "bibcode": "B1"},
    )
    registry.update_file_status(first.resolve(), "paper.pdf", "resolved")
    window.refresh()
    window.table.setCurrentIndex(window.model.index(0, 0))
    assert window.detail._metadata.get("bibcode") == "B1"
    assert window.refresh_selected_ads_action.isEnabled()
    assert window.update_selected_ads_action.isEnabled()

    # Unrelated root refresh preserves current root's detail pane
    window.refresh(second.resolve())
    assert window.detail._metadata.get("bibcode") == "B1"

    # Filtering to an empty view clears detail and disables single-record ADS actions
    window.search.setText("nonexistent-query-token")
    window._search_changed()
    assert window.model.rowCount() == 0
    assert window.detail._metadata == {}
    assert not window.detail._actions.isEnabled()
    assert not window.refresh_selected_ads_action.isEnabled()
    assert not window.update_selected_ads_action.isEnabled()

    # Restore search and selection, then externally unlink paper and press Refresh
    window.search.clear()
    window._search_changed()
    window.table.setCurrentIndex(window.model.index(0, 0))
    assert window.detail._metadata.get("bibcode") == "B1"

    paper.unlink()
    window.refresh_action.trigger()
    qtbot.waitUntil(
        lambda: window.job is None and window._local_refresh_context is None
    )
    assert window.model.rowCount() == 0
    assert not window.table.currentIndex().isValid()
    assert window.detail._metadata == {}
    assert window.detail._record is None
    assert not window.detail._actions.isEnabled()
    assert not window.refresh_selected_ads_action.isEnabled()
    assert not window.update_selected_ads_action.isEnabled()


def test_watch_notice_keys_distinguish_reused_inode_generations(tmp_path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    path = root / "paper.pdf"
    session = WatchSession(root, "session-1")
    first = session.observe(
        FileSnapshot(path, 10, 100, 7, 11, None),
        WatchSource.CREATED,
    )
    assert first is not None
    session.complete(first.identity, WatchState.RESOLVED)
    session.reconcile([], scan_token=session.start_scan())

    second = session.observe(
        FileSnapshot(path, 20, 200, 7, 11, None),
        WatchSource.CREATED,
    )

    assert second is not None
    assert (first.identity.generation, second.identity.generation) == (0, 1)
    assert MainWindow._watch_notice_key(first) != MainWindow._watch_notice_key(second)


def test_watch_actions_are_per_root_and_persist_immediately(
    monkeypatch, qtbot, tmp_path
):
    root = tmp_path / "library"
    root.mkdir()
    window, controller, saves = _window_factory(
        monkeypatch,
        qtbot,
        GuiConfig(roots=[RootEntry(str(root))]),
    )

    assert not window.watch_current_action.isChecked()
    window.watch_current_action.trigger()
    assert controller.start_calls == [(root.resolve(), False)]

    assert not window.watch_on_start_action.isChecked()
    window.watch_on_start_action.trigger()
    assert window.root_entries[str(root.resolve())].watch_on_start
    assert saves[-1].roots[0].watch_on_start

    assert not window.auto_process_action.isChecked()
    window.auto_process_action.trigger()
    assert window.root_entries[str(root.resolve())].auto_process_new_pdfs
    assert saves[-1].roots[0].auto_process_new_pdfs


def test_watch_policy_save_failure_restores_old_value(monkeypatch, qtbot, tmp_path):
    import adsfolio.gui.main_window as main_window_module

    root = tmp_path / "library"
    root.mkdir()
    window, _controller, _saves = _window_factory(
        monkeypatch,
        qtbot,
        GuiConfig(roots=[RootEntry(str(root))]),
    )
    monkeypatch.setattr(
        main_window_module,
        "save_config",
        lambda config: (_ for _ in ()).throw(OSError("read-only config")),
    )

    window.watch_on_start_action.trigger()

    assert not window.root_entries[str(root.resolve())].watch_on_start
    assert not window.watch_on_start_action.isChecked()
    assert "read-only config" in window.notice_bar.message.text()


def test_startup_only_watches_opted_in_roots(monkeypatch, qtbot, tmp_path):
    root_one = tmp_path / "one"
    root_two = tmp_path / "two"
    root_one.mkdir()
    root_two.mkdir()
    _window, controller, _saves = _window_factory(
        monkeypatch,
        qtbot,
        GuiConfig(
            roots=[
                RootEntry(str(root_one), watch_on_start=True),
                RootEntry(str(root_two), watch_on_start=False),
            ]
        ),
    )

    qtbot.waitUntil(lambda: len(controller.start_calls) == 1)

    assert controller.start_calls == [(root_one.resolve(), False)]


def test_missing_startup_root_produces_notice(monkeypatch, qtbot, tmp_path):
    missing = tmp_path / "missing"
    window, controller, _saves = _window_factory(
        monkeypatch,
        qtbot,
        GuiConfig(roots=[RootEntry(str(missing), watch_on_start=True)]),
    )

    qtbot.waitUntil(lambda: bool(window.notice_bar.queued_keys()))

    assert controller.start_calls == []
    assert "does not exist" in window.notice_bar.message.text()


def test_malformed_startup_root_produces_notice(monkeypatch, qtbot):
    window, controller, _saves = _window_factory(
        monkeypatch,
        qtbot,
        GuiConfig(roots=[RootEntry("\0", watch_on_start=True)]),
    )

    assert controller.start_calls == []
    assert window.notice_bar.queued_keys()
    assert "could not restore" in window.notice_bar.message.text().lower()


def test_remove_root_stops_worker_before_closing_registry(monkeypatch, qtbot, tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    window, controller, _saves = _window_factory(
        monkeypatch,
        qtbot,
        GuiConfig(roots=[RootEntry(str(root))]),
    )
    window.watch_current_action.trigger()
    order = []
    registry = window.registries[str(root.resolve())]
    original_close = registry.close

    def close_registry() -> None:
        order.append("registry")
        original_close()

    registry.close = close_registry
    original_stop = controller.stop_root

    def stop_worker(path: Path) -> bool:
        order.append("worker")
        return original_stop(path)

    controller.stop_root = stop_worker

    window._remove_root(root)

    assert order == ["worker", "registry"]
    assert str(root.resolve()) not in window.registries


def test_window_close_refuses_when_watch_workers_cannot_stop(
    monkeypatch, qtbot, tmp_path
):
    root = tmp_path / "library"
    root.mkdir()
    window, controller, _saves = _window_factory(
        monkeypatch,
        qtbot,
        GuiConfig(roots=[RootEntry(str(root))]),
    )
    registry = window.registries[str(root.resolve())]
    close_calls = []
    original_close = registry.close
    registry.close = lambda: (close_calls.append(True), original_close())[-1]
    controller.stop_all_result = False

    assert not window.close()
    assert close_calls == []
    assert "could not stop" in window.notice_bar.message.text().lower()

    controller.stop_all_result = True
    window.close()


def test_recursive_edit_is_rejected_while_root_is_watched(monkeypatch, qtbot, tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    window, _controller, _saves = _window_factory(
        monkeypatch,
        qtbot,
        GuiConfig(roots=[RootEntry(str(root))]),
    )
    window.watch_current_action.trigger()

    changed = window._set_root_recursive(root, True)

    assert not changed
    assert not window.root_entries[str(root.resolve())].recursive
    assert "stop watching" in window.notice_bar.message.text().lower()


def test_effective_watch_settings_resolve_scopes_independently(
    monkeypatch, qtbot, tmp_path
):
    import adsfolio.gui.main_window as main_window_module

    root = tmp_path / "library"
    root.mkdir()
    global_identify = IdentifySettings(strategies=(IdentifyStrategy.FILENAME_BIBCODE,))
    library_identify = IdentifySettings(strategies=(IdentifyStrategy.FIRST_PAGE_DOI,))
    global_rename = RenameSettings(template="{bibcode}")
    library_rename = RenameSettings(template="{title}")
    config = GuiConfig(
        roots=[RootEntry(str(root))],
        identify_scopes={str(root.resolve()): "library"},
        rename_scopes={str(root.resolve()): "global"},
    )
    window, _controller, _saves = _window_factory(monkeypatch, qtbot, config)
    state = CentralPdfRegistryState(window.registries[str(root.resolve())])
    state.set_identify_settings(library_identify)
    state.set_rename_settings(library_rename)
    monkeypatch.setattr(
        main_window_module.Config,
        "load_identify_settings",
        classmethod(lambda cls: global_identify),
    )
    monkeypatch.setattr(
        main_window_module.Config,
        "load_rename_settings",
        classmethod(lambda cls: global_rename),
    )

    assert window._effective_identify_settings(root) == library_identify
    assert window._effective_rename_settings(root) == global_rename


def test_identify_dialog_rolls_back_domain_settings_when_gui_save_fails(
    monkeypatch, qtbot, tmp_path
) -> None:
    import adsfolio.gui.main_window as main_window_module

    root = tmp_path / "library"
    root.mkdir()
    key = str(root.resolve())
    prior = IdentifySettings(strategies=(IdentifyStrategy.FILENAME_BIBCODE,))
    window, _controller, _saves = _window_factory(
        monkeypatch,
        qtbot,
        GuiConfig(
            roots=[RootEntry(str(root))],
            identify_scopes={key: "library"},
        ),
    )
    state = CentralPdfRegistryState(window.registries[key])
    state.set_identify_settings(prior)
    monkeypatch.setattr(
        main_window_module.Config,
        "load_identify_settings",
        classmethod(lambda cls: IdentifySettings(verbose=True)),
    )
    attempts = []

    def fail_gui_save(config) -> None:
        attempts.append(config.identify_scopes.get(key))
        raise OSError("gui config is read-only")

    monkeypatch.setattr(main_window_module, "save_config", fail_gui_save)
    messages = []
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        lambda parent, title, message: messages.append((title, message)),
    )
    dialogs = []

    def execute(dialog) -> int:
        dialogs.append(dialog)
        dialog.reset_strategies.click()
        dialog.save_defaults()
        return dialog.result()

    monkeypatch.setattr(main_window_module.IdentifyDialog, "exec", execute)

    window._identify()

    assert dialogs[0].result() != QDialog.Accepted
    assert window.config.identify_scopes[key] == "library"
    assert state.identify_settings == prior
    assert window._effective_identify_settings(root) == prior
    assert attempts == ["library", "library"]
    assert "rollback failed" in messages[0][1].lower()


def test_identify_dialog_rejects_invalid_skip_geometry_before_stores(
    monkeypatch,
    qtbot,
    tmp_path,
) -> None:
    import adsfolio.gui.main_window as main_window_module

    root = tmp_path / "library"
    root.mkdir()
    key = str(root.resolve())
    prior = IdentifySettings(output_unresolved="unresolved")
    window, _controller, saves = _window_factory(
        monkeypatch,
        qtbot,
        GuiConfig(
            roots=[RootEntry(str(root))],
            identify_scopes={key: "library"},
        ),
    )
    state = CentralPdfRegistryState(window.registries[key])
    state.set_identify_settings(prior)
    messages = []
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        lambda parent, title, message: messages.append((title, message)),
    )
    dialogs = []

    def execute(dialog) -> int:
        dialogs.append(dialog)
        dialog.output.setText(".")
        dialog.skip_unresolved.setChecked(True)
        dialog.save_defaults()
        return dialog.result()

    monkeypatch.setattr(main_window_module.IdentifyDialog, "exec", execute)

    window._identify()

    assert dialogs[0].result() != QDialog.Accepted
    assert state.identify_settings == prior
    assert window.config.identify_scopes[key] == "library"
    assert saves == []
    assert "contains the watched root" in messages[0][1]


def test_rename_dialog_rolls_back_domain_settings_when_gui_save_fails(
    monkeypatch, qtbot, tmp_path
) -> None:
    import adsfolio.gui.main_window as main_window_module

    root = tmp_path / "library"
    root.mkdir()
    key = str(root.resolve())
    prior = RenameSettings(template="{bibcode}")
    window, _controller, _saves = _window_factory(
        monkeypatch,
        qtbot,
        GuiConfig(
            roots=[RootEntry(str(root))],
            rename_scopes={key: "library"},
        ),
    )
    state = CentralPdfRegistryState(window.registries[key])
    state.set_rename_settings(prior)
    monkeypatch.setattr(
        main_window_module.Config,
        "load_rename_settings",
        classmethod(lambda cls: RenameSettings(max_authors=8)),
    )
    attempts = []

    def fail_gui_save(config) -> None:
        attempts.append(config.rename_scopes.get(key))
        raise OSError("gui config is read-only")

    monkeypatch.setattr(main_window_module, "save_config", fail_gui_save)
    messages = []
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        lambda parent, title, message: messages.append((title, message)),
    )
    dialogs = []

    def execute(dialog) -> int:
        dialogs.append(dialog)
        dialog.template.setText("{title}")
        dialog.accept()
        return dialog.result()

    monkeypatch.setattr(main_window_module.RenameDialog, "exec", execute)

    window._rename()

    assert dialogs[0].result() != QDialog.Accepted
    assert window.config.rename_scopes[key] == "library"
    assert state.rename_settings == prior
    assert window._effective_rename_settings(root) == prior
    assert attempts == ["library", "library"]
    assert "rollback failed" in messages[0][1].lower()


def test_manual_watch_detection_routes_notice_action_and_dismissal(
    monkeypatch, qtbot, tmp_path
):
    root = tmp_path / "library"
    root.mkdir()
    window, controller, _saves = _window_factory(
        monkeypatch,
        qtbot,
        GuiConfig(roots=[RootEntry(str(root))]),
    )
    window.watch_current_action.trigger()
    event = _event(root, controller.session_id(root))

    controller.detected.emit(event)
    window.notice_bar.action_button.click()

    assert window.notice_bar.message.text() == f"New PDF found: {event.path.name}"
    assert controller.process_calls[0][0] == event
    assert isinstance(controller.process_calls[0][1], IdentifySettings)
    assert isinstance(controller.process_calls[0][2], RenameSettings)

    second = _event(root, event.session_id, generation=1)
    controller.detected.emit(second)
    window.notice_bar.dismiss_button.click()

    assert controller.dismiss_calls == [event]


def test_automatic_watch_detection_processes_without_manual_notice(
    monkeypatch, qtbot, tmp_path
):
    root = tmp_path / "library"
    root.mkdir()
    window, controller, _saves = _window_factory(
        monkeypatch,
        qtbot,
        GuiConfig(
            roots=[RootEntry(str(root), auto_process_new_pdfs=True)],
        ),
    )
    window.watch_current_action.trigger()
    event = _event(root, controller.session_id(root))

    controller.detected.emit(event)

    assert [call[0] for call in controller.process_calls] == [event]
    assert window.notice_bar.queued_keys() == ()
    assert window.job is None


@pytest.mark.parametrize("auto_process", [False, True])
def test_watch_detection_inside_skipped_unresolved_directory_is_dismissed(
    monkeypatch, qtbot, tmp_path, auto_process
) -> None:
    import adsfolio.gui.main_window as main_window_module

    root = tmp_path / "library"
    root.mkdir()
    window, controller, _saves = _window_factory(
        monkeypatch,
        qtbot,
        GuiConfig(
            roots=[RootEntry(str(root), auto_process_new_pdfs=auto_process)],
        ),
    )
    monkeypatch.setattr(
        main_window_module.Config,
        "load_identify_settings",
        classmethod(lambda cls: IdentifySettings(skip_unresolved_directory=True)),
    )
    window.watch_current_action.trigger()
    detected = _event(root, controller.session_id(root))
    event = WatchEvent(
        root=detected.root,
        session_id=detected.session_id,
        identity=detected.identity,
        path=root / "unresolved" / "new.pdf",
        state=detected.state,
    )

    controller.detected.emit(event)

    assert controller.dismiss_calls == [event]
    assert controller.process_calls == []
    assert window.notice_bar.queued_keys() == ()


@pytest.mark.parametrize("auto_process", [False, True])
def test_invalid_persisted_skip_geometry_does_not_suppress_watch_detection(
    monkeypatch,
    qtbot,
    tmp_path,
    auto_process,
) -> None:
    import adsfolio.gui.main_window as main_window_module

    root = tmp_path / "library"
    root.mkdir()
    window, controller, _saves = _window_factory(
        monkeypatch,
        qtbot,
        GuiConfig(
            roots=[RootEntry(str(root), auto_process_new_pdfs=auto_process)],
        ),
    )
    monkeypatch.setattr(
        main_window_module.Config,
        "load_identify_settings",
        classmethod(
            lambda cls: IdentifySettings(
                output_unresolved=".",
                skip_unresolved_directory=True,
            )
        ),
    )
    window.watch_current_action.trigger()
    event = _event(root, controller.session_id(root))

    controller.detected.emit(event)

    assert controller.dismiss_calls == []
    if auto_process:
        assert [call[0] for call in controller.process_calls] == [event]
        assert window.notice_bar.queued_keys() == ()
    else:
        assert controller.process_calls == []
        assert window.notice_bar.message.text() == f"New PDF found: {event.path.name}"


def test_failed_watch_outcome_offers_retry_only_for_current_session(
    monkeypatch, qtbot, tmp_path
):
    root = tmp_path / "library"
    root.mkdir()
    window, controller, _saves = _window_factory(
        monkeypatch,
        qtbot,
        GuiConfig(roots=[RootEntry(str(root))]),
    )
    window.watch_current_action.trigger()
    current = _event(root, controller.session_id(root))
    failed = WatchOutcome(
        event=WatchEvent(
            root=current.root,
            session_id=current.session_id,
            identity=current.identity,
            path=current.path,
            state=WatchState.FAILED,
        ),
        message="ADS unavailable",
    )

    controller.outcome.emit(failed)
    window.notice_bar.action_button.click()

    assert window.notice_bar.action_button.text() == "Retry"
    assert window.notice_bar.message.text() == f"{current.path.name}: ADS unavailable"
    assert window.notice_bar.message.toolTip() == str(current.path)
    assert controller.retry_calls == [failed.event]

    obsolete = WatchOutcome(
        event=WatchEvent(
            root=current.root,
            session_id="obsolete",
            identity=current.identity,
            path=current.path,
            state=WatchState.FAILED,
        ),
        message="obsolete failure",
    )
    controller.outcome.emit(obsolete)

    assert "obsolete failure" not in window.notice_bar.message.text()


@pytest.mark.parametrize("relocated", [False, True])
def test_unresolved_watch_notice_identifies_current_file_path(
    monkeypatch, qtbot, tmp_path, relocated
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    window, controller, _saves = _window_factory(
        monkeypatch,
        qtbot,
        GuiConfig(roots=[RootEntry(str(root))]),
    )
    window.watch_current_action.trigger()
    current = _event(root, controller.session_id(root))
    destination = root / "unresolved" / current.path.name if relocated else current.path
    event = WatchEvent(
        root=current.root,
        session_id=current.session_id,
        identity=current.identity,
        path=current.path,
        state=WatchState.UNRESOLVED,
    )

    controller.outcome.emit(
        WatchOutcome(
            event=event,
            destination=destination if relocated else None,
            message="No ADS record identified",
        )
    )

    assert window.notice_bar.message.text() == (
        f"{destination.name}: No ADS record identified"
    )
    assert window.notice_bar.message.toolTip() == str(destination)


def test_dismissing_failed_notice_does_not_dismiss_terminal_worker_state(
    monkeypatch, qtbot, tmp_path
):
    root = tmp_path / "library"
    root.mkdir()
    window, controller, _saves = _window_factory(
        monkeypatch,
        qtbot,
        GuiConfig(roots=[RootEntry(str(root))]),
    )
    window.watch_current_action.trigger()
    detected = _event(root, controller.session_id(root))
    failed_event = WatchEvent(
        root=detected.root,
        session_id=detected.session_id,
        identity=detected.identity,
        path=detected.path,
        state=WatchState.FAILED,
    )
    controller.outcome.emit(WatchOutcome(failed_event, message="ADS unavailable"))

    window.notice_bar.dismiss_button.click()

    assert controller.dismiss_calls == []
    assert window.notice_bar.queued_keys() == ()


def test_watch_outcomes_coalesce_refresh_and_restore_location_selection(
    monkeypatch, qtbot, tmp_path
):
    root = tmp_path / "library"
    root.mkdir()
    window, controller, _saves = _window_factory(
        monkeypatch,
        qtbot,
        GuiConfig(roots=[RootEntry(str(root), auto_process_new_pdfs=True)]),
    )
    window.watch_current_action.trigger()
    refreshes = []
    selections = []
    monkeypatch.setattr(
        window, "refresh", lambda refreshed=None: refreshes.append(refreshed)
    )
    monkeypatch.setattr(window, "_selected_record", lambda: {"location_id": 42})
    monkeypatch.setattr(window, "_select_file_id", selections.append)
    event = _event(root, controller.session_id(root))

    for state in (WatchState.RESOLVED, WatchState.RENAMED):
        controller.outcome.emit(
            WatchOutcome(
                event=WatchEvent(
                    root=event.root,
                    session_id=event.session_id,
                    identity=event.identity,
                    path=event.path,
                    state=state,
                )
            )
        )

    qtbot.waitUntil(lambda: bool(refreshes))

    assert refreshes == [root.resolve()]
    assert selections == [42]


def test_identify_dialog_waits_for_pause_ack_and_resumes_after_cancel(
    monkeypatch, qtbot, tmp_path
):
    import adsfolio.gui.main_window as main_window_module

    root = tmp_path / "library"
    root.mkdir()
    window, controller, _saves = _window_factory(
        monkeypatch,
        qtbot,
        GuiConfig(roots=[RootEntry(str(root))]),
    )
    window.watch_current_action.trigger()
    controller.auto_ack_pause = False
    opened = []

    class FakeIdentifyDialog(QObject):
        job_started = Signal(object)
        accepted = Signal()

        def __init__(self, *args, **kwargs):
            super().__init__()
            opened.append((args, kwargs))

        def exec(self):
            return QDialog.Rejected

        def start_job(self):
            pass

    monkeypatch.setattr(main_window_module, "IdentifyDialog", FakeIdentifyDialog)

    window._identify()

    assert controller.pause_calls == [root.resolve()]
    assert opened == []

    controller.paused.emit(controller.session_id(root))

    assert len(opened) == 1
    assert controller.resume_calls == [root.resolve()]


def test_rename_dialog_cancel_resumes_watched_root(monkeypatch, qtbot, tmp_path):
    import adsfolio.gui.main_window as main_window_module

    root = tmp_path / "library"
    root.mkdir()
    window, controller, _saves = _window_factory(
        monkeypatch,
        qtbot,
        GuiConfig(roots=[RootEntry(str(root))]),
    )
    window.watch_current_action.trigger()

    class FakeRenameDialog(QObject):
        job_started = Signal(object)

        def __init__(self, *args, **kwargs):
            super().__init__()

        def exec(self):
            return QDialog.Rejected

    monkeypatch.setattr(main_window_module, "RenameDialog", FakeRenameDialog)

    window._rename()

    assert controller.pause_calls == [root.resolve()]
    assert controller.resume_calls == [root.resolve()]


class FakeJob(QObject):
    output = Signal(str)
    failed = Signal(str)
    finished = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.running = True

    def cancel(self) -> None:
        self.running = False


class FakeAdsRefreshJob(QObject):
    output = Signal(str)
    failed = Signal(str)
    finished = Signal(int)

    def __init__(
        self,
        argv: list[str],
        parent: QObject | None = None,
        *,
        working_directory: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.argv = argv
        self.working_directory = working_directory
        self.running = False

    def start(self) -> None:
        self.running = True

    def cancel(self) -> None:
        self.running = False


def _ads_refresh_job_factory(monkeypatch, jobs: list[FakeAdsRefreshJob]) -> None:
    import adsfolio.gui.main_window as main_window_module

    def create_job(
        argv: list[str],
        parent: QObject | None = None,
        *,
        working_directory: Path | None = None,
    ) -> FakeAdsRefreshJob:
        job = FakeAdsRefreshJob(
            argv,
            parent=parent,
            working_directory=working_directory,
        )
        jobs.append(job)
        return job

    monkeypatch.setattr(main_window_module, "CommandJob", create_job)


def _argv_value(argv: list[str], option: str) -> str:
    return argv[argv.index(option) + 1]


def test_ads_refresh_waits_for_watcher_pause_before_starting_job(
    monkeypatch, qtbot, tmp_path
):
    root = tmp_path / "library"
    root.mkdir()
    key = str(root.resolve())
    config = GuiConfig(
        roots=[RootEntry(str(root))],
        rename_scopes={key: "library"},
    )
    window, controller, _saves = _window_factory(monkeypatch, qtbot, config)
    window.watch_current_action.trigger()
    controller.auto_ack_pause = False
    jobs: list[FakeAdsRefreshJob] = []
    _ads_refresh_job_factory(monkeypatch, jobs)

    window._start_ads_refresh(
        "--identifier",
        "2020A&A...123..456A",
        selection_id=7,
    )

    assert controller.pause_calls == [root.resolve()]
    assert jobs == []
    assert window._pending_watch_dialog == ("ads_refresh", root.resolve())

    controller.paused.emit(controller.session_id(root))

    assert len(jobs) == 1
    assert jobs[0].running
    assert jobs[0].working_directory == root.resolve()
    assert _argv_value(jobs[0].argv, "--rename-scope") == "library"
    assert _argv_value(jobs[0].argv, "--output-unresolved") == str(root / "unresolved")
    assert _argv_value(jobs[0].argv, "--identifier") == "2020A&A...123..456A"
    jobs[0].finished.emit(0)

    assert controller.resume_calls == [root.resolve()]


def test_ads_refresh_keeps_origin_root_and_settings_after_root_switch(
    monkeypatch, qtbot, tmp_path
):
    import adsfolio.gui.main_window as main_window_module

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    first_key = str(first.resolve())
    second_key = str(second.resolve())
    config = GuiConfig(
        roots=[RootEntry(str(first)), RootEntry(str(second))],
        identify_scopes={first_key: "library", second_key: "global"},
        rename_scopes={first_key: "library", second_key: "global"},
    )
    window, controller, _saves = _window_factory(monkeypatch, qtbot, config)
    state = CentralPdfRegistryState(window.registries[first_key])
    state.set_identify_settings(IdentifySettings(output_unresolved="first-pending"))
    monkeypatch.setattr(
        main_window_module.Config,
        "load_identify_settings",
        classmethod(lambda cls: IdentifySettings(output_unresolved="second-pending")),
    )
    window.current_root = first.resolve()
    window.watch_current_action.trigger()
    controller.auto_ack_pause = False
    jobs: list[FakeAdsRefreshJob] = []
    _ads_refresh_job_factory(monkeypatch, jobs)

    window._start_ads_refresh("--all", selection_id=11)
    window.current_root = second.resolve()

    controller.paused.emit(controller.session_id(first))

    assert len(jobs) == 1
    job = jobs[0]
    assert job.working_directory == first.resolve()
    assert _argv_value(job.argv, "--rename-scope") == "library"
    assert _argv_value(job.argv, "--output-unresolved") == str(first / "first-pending")
    assert "--all" in job.argv
    job.finished.emit(0)

    assert controller.resume_calls == [first.resolve()]


def test_ads_refresh_resumes_watcher_after_start_failure(monkeypatch, qtbot, tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    window, controller, _saves = _window_factory(
        monkeypatch,
        qtbot,
        GuiConfig(roots=[RootEntry(str(root))]),
    )
    window.watch_current_action.trigger()
    controller.auto_ack_pause = False
    jobs: list[FakeAdsRefreshJob] = []
    _ads_refresh_job_factory(monkeypatch, jobs)

    window._start_ads_refresh("--all")
    controller.paused.emit(controller.session_id(root))
    jobs[0].failed.emit("could not start")

    assert controller.resume_calls == [root.resolve()]
    assert window.job is None


def test_closing_window_cancels_pending_ads_refresh(monkeypatch, qtbot, tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    window, controller, _saves = _window_factory(
        monkeypatch,
        qtbot,
        GuiConfig(roots=[RootEntry(str(root))]),
    )
    window.watch_current_action.trigger()
    controller.auto_ack_pause = False
    jobs: list[FakeAdsRefreshJob] = []
    _ads_refresh_job_factory(monkeypatch, jobs)

    window._start_ads_refresh("--all")
    session_id = controller.session_id(root)
    assert session_id is not None
    assert window._pending_ads_refresh is not None

    assert window.close()
    controller.paused.emit(session_id)

    assert jobs == []
    assert window._pending_ads_refresh is None
    assert window._pending_watch_dialog is None
    assert controller.resume_calls == [root.resolve()]


def test_removing_root_cancels_pending_ads_refresh(monkeypatch, qtbot, tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    window, controller, _saves = _window_factory(
        monkeypatch,
        qtbot,
        GuiConfig(roots=[RootEntry(str(root))]),
    )
    window.watch_current_action.trigger()
    controller.auto_ack_pause = False
    jobs: list[FakeAdsRefreshJob] = []
    _ads_refresh_job_factory(monkeypatch, jobs)

    window._start_ads_refresh("--all")
    session_id = controller.session_id(root)
    assert session_id is not None
    window._remove_root(root)
    controller.paused.emit(session_id)

    assert jobs == []
    assert window._pending_ads_refresh is None
    assert window._pending_watch_dialog is None
    assert str(root.resolve()) not in window.registries
    assert controller.resume_calls == [root.resolve()]


def test_ads_refresh_completion_retries_ambiguity_for_origin_root(
    monkeypatch, qtbot, tmp_path
):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    first_key = str(first.resolve())
    second_key = str(second.resolve())
    window, controller, _saves = _window_factory(
        monkeypatch,
        qtbot,
        GuiConfig(roots=[RootEntry(str(first)), RootEntry(str(second))]),
    )
    window.watch_current_action.trigger()
    job = FakeJob()
    window._foreground_paused_root = first.resolve()
    window._ads_refresh_root = first.resolve()
    window._refresh_selection_id = 19
    window._ads_match_candidates = [
        {
            "bibcode": "2020A&A...123..456A",
            "first_author": "Author",
            "title": "Paper",
        }
    ]
    window._ambiguous_ads_match_context = (
        first.resolve(),
        "unresolved/paper.pdf",
        19,
    )
    window._watch_job(job)
    window.current_root = second.resolve()
    refreshed: list[Path | None] = []
    choices: list[tuple[Path, str, int | None]] = []
    monkeypatch.setattr(
        window,
        "refresh",
        lambda root=None: refreshed.append(root),
    )

    def choose(
        candidates: list[dict[str, str]],
        relative_path: str,
        selection_id: int | None,
        *,
        root: Path | None = None,
    ) -> None:
        del candidates
        assert root is not None
        choices.append((root, relative_path, selection_id))

    monkeypatch.setattr(window, "_choose_ambiguous_ads_match", choose)
    job.finished.emit(0)

    assert refreshed == [first.resolve()]
    assert choices == [(first.resolve(), "unresolved/paper.pdf", 19)]
    assert controller.resume_calls == [first.resolve()]
    assert window.current_root == second.resolve()
    assert window._refresh_selection_id is None
    assert first_key in window.registries
    assert second_key in window.registries


def test_watched_identify_job_resumes_after_start_failure(monkeypatch, qtbot, tmp_path):
    import adsfolio.gui.main_window as main_window_module

    root = tmp_path / "library"
    root.mkdir()
    window, controller, _saves = _window_factory(
        monkeypatch,
        qtbot,
        GuiConfig(roots=[RootEntry(str(root))]),
    )
    window.watch_current_action.trigger()
    job = FakeJob()

    class FakeIdentifyDialog(QObject):
        job_started = Signal(object)
        accepted = Signal()

        def __init__(self, *args, **kwargs):
            super().__init__()

        def exec(self):
            self.job_started.emit(job)
            return QDialog.Accepted

        def start_job(self):
            pass

    monkeypatch.setattr(main_window_module, "IdentifyDialog", FakeIdentifyDialog)

    window._identify()
    job.failed.emit("could not start")

    assert controller.resume_calls == [root.resolve()]
    assert window.job is None


def test_watched_rename_job_resumes_after_completion(monkeypatch, qtbot, tmp_path):
    import adsfolio.gui.main_window as main_window_module

    root = tmp_path / "library"
    root.mkdir()
    window, controller, _saves = _window_factory(
        monkeypatch,
        qtbot,
        GuiConfig(roots=[RootEntry(str(root))]),
    )
    window.watch_current_action.trigger()
    job = FakeJob()

    class FakeRenameDialog(QObject):
        job_started = Signal(object)

        def __init__(self, *args, **kwargs):
            super().__init__()

        def exec(self):
            self.job_started.emit(job)
            return QDialog.Accepted

    monkeypatch.setattr(main_window_module, "RenameDialog", FakeRenameDialog)

    window._rename()
    job.finished.emit(0)

    assert controller.resume_calls == [root.resolve()]
    assert window.job is None


def test_closing_window_cancels_deferred_library_queries(
    monkeypatch, qtbot, tmp_path
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    window, _controller, _saves = _window_factory(
        monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(root))])
    )
    window.search.setText("pending search")
    window._schedule_watch_refresh(root)
    assert window.search_timer.isActive()
    assert window._watch_refresh_timer.isActive()

    assert window.close()

    assert not window.search_timer.isActive()
    assert not window._watch_refresh_timer.isActive()
    assert not window._pending_watch_refresh_roots
    qtbot.wait(window.search_timer.interval() + 1)


def test_stored_bibcode_action_uses_pending_metadata_only_lookup(
    monkeypatch, qtbot, tmp_path
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    (root / "paper.pdf").write_bytes(b"paper")
    window, _controller, _saves = _window_factory(
        monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(root))])
    )
    calls = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    monkeypatch.setattr(window, "_start_ads_refresh", lambda *args: calls.append(args))
    window.link_bibcodes_action.trigger()
    assert calls == [("--all", "--no-rename", "--pending-only")]
