"""Deterministic MainWindow integration tests for library watching."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QDialog, QMessageBox

from adsapi.config import Config
from adsapi.gui.config import GuiConfig, RootEntry
from adsapi.gui.main_window import MainWindow
from adsapi.pdf.identify_settings import IdentifySettings, IdentifyStrategy
from adsapi.pdf.registry import CentralPdfRegistryState
from adsapi.pdf.rename_settings import RenameSettings
from adsapi.pdf.watch_session import (
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
    import adsapi.gui.main_window as main_window_module

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
    import adsapi.gui.main_window as main_window_module

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
    window, controller, _saves = _window_factory(
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
    import adsapi.gui.main_window as main_window_module

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
    import adsapi.gui.main_window as main_window_module

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
    import adsapi.gui.main_window as main_window_module

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
    import adsapi.gui.main_window as main_window_module

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
    import adsapi.gui.main_window as main_window_module

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
    import adsapi.gui.main_window as main_window_module

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
    import adsapi.gui.main_window as main_window_module

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
    import adsapi.gui.main_window as main_window_module

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


def test_watched_identify_job_resumes_after_start_failure(monkeypatch, qtbot, tmp_path):
    import adsapi.gui.main_window as main_window_module

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
    import adsapi.gui.main_window as main_window_module

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
