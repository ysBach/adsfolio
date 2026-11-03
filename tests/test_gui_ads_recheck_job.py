"""Automatic identifier rechecks run offline on fictional libraries.

The tests cover root order, per-library settings, missing ADS tokens, watcher
pauses, cancellation, and settings guards. The real-backend test also checks
that renamed PDF bytes do not change.
"""

from __future__ import annotations

import socket
import sys
import threading
from copy import deepcopy
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
import requests
from pypdf import PdfWriter
from PySide6.QtCore import QItemSelectionModel, QObject, Qt, Signal
from PySide6.QtWidgets import QMessageBox

from adsfolio.config import AuthenticationError, Config
from adsfolio.gui import ads_recheck_job, main_window
from adsfolio.gui.config import GuiConfig, RootEntry
from adsfolio.gui.main_window import ADS_RECHECK_INTERVAL_MS, MainWindow
from adsfolio.gui.preferences import PreferencesDialog
from adsfolio.pdf.doi_providers import DoiProvider
from adsfolio.pdf.files import sha256_file
from adsfolio.pdf.identifier import PdfIdentifier
from adsfolio.pdf.identify_settings import IdentifySettings
from adsfolio.pdf.registry import CentralPdfRegistryState, open_registry_state
from adsfolio.pdf.rename_settings import RenameSettings


class FakeWatchController(QObject):
    started = Signal(object)
    stopped = Signal(object)
    detected = Signal(object)
    outcome = Signal(object)
    failed = Signal(object)
    paused = Signal(object)
    reconciled = Signal(object)

    def __init__(self, *, auto_ack: bool = True) -> None:
        super().__init__()
        self.auto_ack = auto_ack
        self.active: dict[Path, str] = {}
        self.pauses: list[Path] = []
        self.resumes: list[Path] = []

    def start_root(self, root: Path, *, recursive: bool) -> str:
        root = root.resolve()
        session = f"session-{len(self.active)}"
        self.active[root] = session
        self.started.emit(session)
        return session

    def stop_root(self, root: Path) -> bool:
        session = self.active.pop(root.resolve(), None)
        if session is not None:
            self.stopped.emit(session)
        return session is not None

    def stop_all(self) -> bool:
        for root in tuple(self.active):
            self.stop_root(root)
        return True

    def is_watching(self, root: Path) -> bool:
        return root.resolve() in self.active

    def session_id(self, root: Path) -> str | None:
        return self.active.get(root.resolve())

    def pause_writes(self, root: Path) -> None:
        self.pauses.append(root.resolve())
        if self.auto_ack:
            self.paused.emit(self.session_id(root))

    def resume_writes(self, root: Path) -> None:
        self.resumes.append(root.resolve())


@pytest.fixture(autouse=True)
def isolated_no_network(monkeypatch, tmp_path: Path) -> None:
    """Prevent all requests and keep configuration outside the user's home."""
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    monkeypatch.setattr(
        Config, "get_token", classmethod(lambda cls, *, prompt=True: "test-token")
    )

    def reject_network(*args, **kwargs):
        raise AssertionError("Network access is forbidden in this test")

    monkeypatch.setattr(requests.Session, "request", reject_network)
    monkeypatch.setattr(socket.socket, "connect", reject_network)
    monkeypatch.setattr(socket, "create_connection", reject_network)


def _result(**values):
    return SimpleNamespace(
        **{
            "checked": 0,
            "updated": 0,
            "renamed": 0,
            "failures": [],
            "cancelled": False,
            **values,
        }
    )


@pytest.fixture
def backend(monkeypatch):
    """Replace the backend and track resource ownership in each worker."""
    calls = []
    clients = []
    resources = []
    handler = SimpleNamespace(run=lambda *args, **kwargs: _result())
    module = ModuleType("adsfolio.pdf.ads_recheck")

    def recheck(state, client, rename_settings, **kwargs):
        calls.append((state.directory, rename_settings, kwargs, threading.get_ident()))
        clients.append(client)
        return handler.run(state, client, rename_settings, **kwargs)

    module.recheck_existing = recheck
    monkeypatch.setitem(sys.modules, "adsfolio.pdf.ads_recheck", module)
    open_state = ads_recheck_job.open_registry_state

    class State:
        def __init__(self, root):
            self._state = open_state(root)
            resources.append(("open", root, threading.get_ident()))

        def __getattr__(self, name):
            return getattr(self._state, name)

        def close(self):
            resources.append(("close", self.directory, threading.get_ident()))
            self._state.close()

    class Client:
        def __init__(self, *, token):
            assert token == "test-token"
            resources.append(("client-open", None, threading.get_ident()))

        def close(self):
            resources.append(("client-close", None, threading.get_ident()))

    monkeypatch.setattr(ads_recheck_job, "open_registry_state", State)
    monkeypatch.setattr(ads_recheck_job, "ADSClient", Client)
    return SimpleNamespace(
        calls=calls, clients=clients, resources=resources, handler=handler
    )


def _window(monkeypatch, qtbot, config, *, controller=None):
    controller = controller or FakeWatchController()
    monkeypatch.setattr(main_window, "load_config", lambda: deepcopy(config))
    monkeypatch.setattr(main_window, "save_config", lambda _config: None)
    window = MainWindow(watch_controller=controller)
    qtbot.addWidget(window)
    return window, controller


def _wait_for_cycle(qtbot, window):
    qtbot.waitUntil(
        lambda: window._ads_recheck_roots is None and window._ads_recheck_job is None,
        timeout=5000,
    )


def test_startup_periodic_settings_and_late_root(monkeypatch, qtbot, tmp_path, backend):
    watched, unwatched, external = (tmp_path / name for name in ("a", "b", "c"))
    for root in (watched, unwatched, external):
        root.mkdir()
    missing = tmp_path / "missing"
    global_rename = RenameSettings(template="global-{bibcode}")
    local_rename = RenameSettings(template="local-{bibcode}", char_policy="ascii")
    global_identify = IdentifySettings(allow_ads=False)
    local_identify = IdentifySettings(
        allow_ads=False, skip_unresolved_directory=True, output_unresolved="excluded"
    )
    Config.save_identify_settings(global_identify)
    Config.save_rename_settings(global_rename)
    config = GuiConfig(
        roots=[
            RootEntry(str(watched), recursive=True, watch_on_start=True),
            RootEntry(str(unwatched)),
            RootEntry(str(external)),
            RootEntry(str(missing)),
        ],
        identify_scopes={str(external): "library", str(unwatched): "library"},
        rename_scopes={str(unwatched): "library"},
    )
    window, controller = _window(monkeypatch, qtbot, config)
    external_identify = IdentifySettings(providers=(DoiProvider.CROSSREF,))
    CentralPdfRegistryState(window.registries[str(external)]).set_identify_settings(
        external_identify
    )
    CentralPdfRegistryState(window.registries[str(unwatched)]).set_identify_settings(
        local_identify
    )
    CentralPdfRegistryState(window.registries[str(unwatched)]).set_rename_settings(
        local_rename
    )

    qtbot.waitUntil(lambda: len(backend.calls) == 3, timeout=5000)
    _wait_for_cycle(qtbot, window)
    assert [call[0] for call in backend.calls] == [watched, unwatched, external]
    assert [call[1] for call in backend.calls] == [
        global_rename,
        local_rename,
        global_rename,
    ]
    assert [call[2]["ads_enabled"] for call in backend.calls] == [True, True, False]
    assert all(call[2]["apply"] for call in backend.calls)
    assert [call[2]["identify_settings"] for call in backend.calls] == [
        global_identify,
        local_identify,
        external_identify,
    ]
    assert [call[2]["recursive"] for call in backend.calls] == [True, False, False]
    assert backend.clients[0] is not None
    assert backend.clients[1] is not None
    assert backend.clients[2] is None
    assert all(call[3] != threading.get_ident() for call in backend.calls)
    assert controller.pauses == [watched]
    assert controller.resumes == [watched]
    assert window._ads_recheck_timer.interval() == ADS_RECHECK_INTERVAL_MS
    assert window._ads_recheck_timer.isActive()
    for root in (watched, unwatched, external):
        opened = next(
            event for event in backend.resources if event[:2] == ("open", root)
        )
        closed = next(
            event for event in backend.resources if event[:2] == ("close", root)
        )
        assert opened[2] == closed[2]
    assert sum(event[0] == "client-open" for event in backend.resources) == 2
    assert sum(event[0] == "client-close" for event in backend.resources) == 2

    late = tmp_path / "late"
    late.mkdir()
    window._register_root(late)
    qtbot.waitUntil(lambda: len(backend.calls) == 4, timeout=5000)
    _wait_for_cycle(qtbot, window)
    assert backend.calls[3][0] == late
    window._ads_recheck_timer.timeout.emit()
    qtbot.waitUntil(lambda: len(backend.calls) == 8, timeout=5000)
    _wait_for_cycle(qtbot, window)
    assert [call[0] for call in backend.calls[4:]] == [
        watched,
        unwatched,
        external,
        late,
    ]
    assert controller.pauses == [watched, watched]
    assert controller.resumes == [watched, watched]


def test_root_added_before_startup_is_checked_once(
    monkeypatch, qtbot, tmp_path, backend
):
    first, late = tmp_path / "first", tmp_path / "late"
    first.mkdir()
    late.mkdir()
    window, _ = _window(monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(first))]))
    window._register_root(late)
    qtbot.waitUntil(lambda: len(backend.calls) == 2, timeout=5000)
    _wait_for_cycle(qtbot, window)
    qtbot.wait(30)
    assert [call[0] for call in backend.calls] == [first, late]


def test_root_added_during_recheck_joins_current_cycle(
    monkeypatch, qtbot, tmp_path, backend
):
    first, late = tmp_path / "first", tmp_path / "late"
    first.mkdir()
    late.mkdir()
    entered, release = threading.Event(), threading.Event()

    def run(state, *args, **kwargs):
        if state.directory == first:
            entered.set()
            assert release.wait(5)
        return _result()

    backend.handler.run = run
    window, _ = _window(monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(first))]))
    try:
        qtbot.waitUntil(entered.is_set, timeout=5000)
        window._register_root(late)
        window._register_root(late)
        release.set()
        qtbot.waitUntil(lambda: len(backend.calls) == 2, timeout=5000)
        _wait_for_cycle(qtbot, window)
        qtbot.wait(30)
        assert [call[0] for call in backend.calls] == [first, late]
    finally:
        release.set()


def test_ads_root_added_during_external_recheck_checks_token(
    monkeypatch, qtbot, tmp_path, backend
):
    external, ads = tmp_path / "external", tmp_path / "ads"
    external.mkdir()
    ads.mkdir()
    Config.save_identify_settings(IdentifySettings(providers=(DoiProvider.CROSSREF,)))
    checked = []

    def token(cls, *, prompt=True):
        assert not prompt
        checked.append(True)
        return "test-token"

    monkeypatch.setattr(Config, "get_token", classmethod(token))
    entered, release = threading.Event(), threading.Event()

    def run(state, *args, **kwargs):
        if state.directory == external:
            entered.set()
            assert release.wait(5)
        return _result()

    backend.handler.run = run
    window, _ = _window(monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(external))]))
    try:
        qtbot.waitUntil(entered.is_set, timeout=5000)
        assert checked == []
        window._register_root(ads)
        window.config.identify_scopes[str(ads)] = "library"
        CentralPdfRegistryState(window.registries[str(ads)]).set_identify_settings(
            IdentifySettings(providers=(DoiProvider.ADS,))
        )
        release.set()
        qtbot.waitUntil(lambda: len(backend.calls) == 2, timeout=5000)
        _wait_for_cycle(qtbot, window)
        assert [call[0] for call in backend.calls] == [external, ads]
        assert [call[2]["ads_enabled"] for call in backend.calls] == [False, True]
        assert backend.clients[0] is None
        assert backend.clients[1] is not None
        assert len(checked) == 2
    finally:
        release.set()


@pytest.mark.parametrize("domain", ["identify", "rename"])
@pytest.mark.parametrize(
    "scope,has_root", [("global", False), ("global", True), ("library", True)]
)
def test_settings_saves_wait_until_recheck_stops(
    monkeypatch, qtbot, tmp_path, backend, domain, scope, has_root
):
    root = tmp_path / "library"
    root.mkdir()
    entered, release = threading.Event(), threading.Event()

    def run(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return _result()

    backend.handler.run = run
    window, _ = _window(monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(root))]))
    identify_before = Config.load_identify_settings()
    rename_before = Config.load_rename_settings()
    identify_scopes = dict(window.config.identify_scopes)
    rename_scopes = dict(window.config.rename_scopes)
    state = CentralPdfRegistryState(window.registries[str(root)])
    initial_library = (state.identify_settings, state.rename_settings)
    identify = IdentifySettings(providers=(DoiProvider.CROSSREF,))
    rename = RenameSettings(template="new-{bibcode}")
    save = (
        window._persist_identify_defaults
        if domain == "identify"
        else window._persist_rename_defaults
    )
    settings = identify if domain == "identify" else rename
    target = root if has_root else None
    try:
        qtbot.waitUntil(entered.is_set, timeout=5000)
        with pytest.raises(ValueError, match="Wait for the identifier recheck"):
            save(target, scope, settings)
        assert Config.load_identify_settings() == identify_before
        assert Config.load_rename_settings() == rename_before
        assert window.config.identify_scopes == identify_scopes
        assert window.config.rename_scopes == rename_scopes
        assert (state.identify_settings, state.rename_settings) == initial_library
        release.set()
        _wait_for_cycle(qtbot, window)
        save(target, scope, settings)
        if domain == "identify":
            assert (
                state.identify_settings
                if scope == "library"
                else Config.load_identify_settings()
            ) == identify
        else:
            assert (
                state.rename_settings
                if scope == "library"
                else Config.load_rename_settings()
            ) == rename
    finally:
        release.set()


def test_preferences_reports_guard_and_saves_unrelated_setting(
    monkeypatch, qtbot, tmp_path, backend
):
    root = tmp_path / "library"
    root.mkdir()
    entered, release = threading.Event(), threading.Event()

    def run(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return _result()

    backend.handler.run = run
    window, _ = _window(monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(root))]))
    try:
        qtbot.waitUntil(entered.is_set, timeout=5000)
        dialog = PreferencesDialog(window)
        qtbot.addWidget(dialog)
        item = dialog.categories.findItems("Identify and Rename", Qt.MatchExactly)[0]
        dialog.categories.setCurrentRow(dialog.categories.row(item))
        errors = []
        monkeypatch.setattr(
            QMessageBox, "critical", lambda *args: errors.append(args[2])
        )
        before_identify = Config.load_identify_settings()
        before_rename = Config.load_rename_settings()
        dialog.identify.recursive.setChecked(True)
        dialog.identify.save_defaults()
        dialog.rename.template.setText("new-{bibcode}")
        assert not dialog.rename._save()
        assert len(errors) == 2
        assert all("Wait for the identifier recheck" in message for message in errors)
        assert Config.load_identify_settings() == before_identify
        assert Config.load_rename_settings() == before_rename
        item = dialog.categories.findItems("General", Qt.MatchExactly)[0]
        dialog.categories.setCurrentRow(dialog.categories.row(item))
        dialog.sound.set_sound("chime")
        dialog._save_general()
        assert window.config.watch_sound == "chime"
        release.set()
        _wait_for_cycle(qtbot, window)
    finally:
        release.set()


def test_missing_token_skips_without_opening_resources(
    monkeypatch, qtbot, tmp_path, backend
):
    root = tmp_path / "library"
    root.mkdir()
    Config.save_identify_settings(IdentifySettings(providers=(DoiProvider.ADS,)))
    checked = threading.Event()

    def no_token(cls, *, prompt=True):
        assert not prompt
        checked.set()
        raise AuthenticationError("No token")

    monkeypatch.setattr(Config, "get_token", classmethod(no_token))
    window, _ = _window(monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(root))]))
    qtbot.waitUntil(checked.is_set, timeout=5000)
    _wait_for_cycle(qtbot, window)
    assert backend.calls == []
    assert backend.resources == []
    assert not window.notice_bar.queued_notices()
    assert not window.status.currentMessage().startswith("Checking identifiers")


@pytest.mark.parametrize(
    "providers",
    [
        (DoiProvider.CROSSREF,),
        (DoiProvider.DATACITE,),
        (DoiProvider.DATACITE, DoiProvider.CROSSREF),
    ],
)
def test_ads_disabled_runs_without_token_check(
    monkeypatch, qtbot, tmp_path, backend, providers
):
    root = tmp_path / "library"
    root.mkdir()
    settings = IdentifySettings(providers=providers)
    Config.save_identify_settings(settings)
    checked = []

    def token(cls, *, prompt=True):
        checked.append(True)
        return "test-token"

    monkeypatch.setattr(Config, "get_token", classmethod(token))
    window, _ = _window(monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(root))]))
    qtbot.waitUntil(lambda: len(backend.calls) == 1, timeout=5000)
    _wait_for_cycle(qtbot, window)
    assert checked == []
    assert backend.clients == [None]
    assert backend.calls[0][2]["ads_enabled"] is False
    assert backend.calls[0][2]["identify_settings"] == settings
    assert [item[0] for item in backend.resources] == ["open", "close"]


@pytest.mark.parametrize("provider", [DoiProvider.CROSSREF, DoiProvider.DATACITE])
@pytest.mark.parametrize("token_error", [AuthenticationError, OSError])
def test_missing_token_skips_ads_only_roots_and_runs_external_providers(
    monkeypatch, qtbot, tmp_path, backend, provider, token_error
):
    ads_only, mixed, external = (tmp_path / name for name in ("ads", "mixed", "ext"))
    for root in (ads_only, mixed, external):
        root.mkdir()
    settings = IdentifySettings(
        providers=(DoiProvider.ADS, provider),
        skip_unresolved_directory=True,
        output_unresolved="exclude",
    )
    Config.save_identify_settings(settings)
    token_checks = []

    def no_token(cls, *, prompt=True):
        assert not prompt
        token_checks.append(True)
        raise token_error("Token unavailable")

    monkeypatch.setattr(Config, "get_token", classmethod(no_token))
    config = GuiConfig(
        roots=[
            RootEntry(str(ads_only)),
            RootEntry(str(mixed), recursive=True, watch_on_start=True),
            RootEntry(str(external)),
        ],
        identify_scopes={str(ads_only): "library", str(external): "library"},
        last_root=str(external),
    )
    window, controller = _window(monkeypatch, qtbot, config)
    CentralPdfRegistryState(window.registries[str(ads_only)]).set_identify_settings(
        IdentifySettings(providers=(DoiProvider.ADS,))
    )
    external_settings = IdentifySettings(providers=(provider,))
    CentralPdfRegistryState(window.registries[str(external)]).set_identify_settings(
        external_settings
    )
    qtbot.waitUntil(lambda: len(backend.calls) == 2, timeout=5000)
    _wait_for_cycle(qtbot, window)
    assert token_checks == [True]
    assert [call[0] for call in backend.calls] == [mixed, external]
    assert [call[2]["identify_settings"] for call in backend.calls] == [
        settings,
        external_settings,
    ]
    assert [call[2]["recursive"] for call in backend.calls] == [True, False]
    assert all(call[2]["ads_enabled"] is False for call in backend.calls)
    assert backend.clients == [None, None]
    assert controller.pauses == [mixed]
    assert controller.resumes == [mixed]
    assert window.current_root == external
    notices = window.notice_bar.queued_notices()
    assert bool(notices) is (token_error is OSError)
    if notices:
        assert "Token unavailable" in notices[0].details


@pytest.mark.parametrize("token_error", [AuthenticationError, OSError])
def test_token_disappears_after_check_runs_external_providers(
    monkeypatch, qtbot, tmp_path, backend, token_error
):
    root = tmp_path / "library"
    root.mkdir()
    calls = []

    def token(cls, *, prompt=True):
        assert not prompt
        calls.append(True)
        if len(calls) == 1:
            return "test-token"
        raise token_error("Token unavailable")

    monkeypatch.setattr(Config, "get_token", classmethod(token))
    window, _ = _window(monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(root))]))
    qtbot.waitUntil(lambda: len(backend.calls) == 1, timeout=5000)
    _wait_for_cycle(qtbot, window)
    assert len(calls) == 2
    assert backend.clients == [None]
    assert backend.calls[0][2]["ads_enabled"] is False
    assert not any(item[0] == "client-open" for item in backend.resources)
    notices = window.notice_bar.queued_notices()
    assert bool(notices) is (token_error is OSError)
    if notices:
        assert "Token unavailable" in notices[0].details


def test_token_check_does_not_block_foreground_work(
    monkeypatch, qtbot, tmp_path, backend
):
    root = tmp_path / "library"
    root.mkdir()
    Config.save_identify_settings(IdentifySettings(providers=(DoiProvider.ADS,)))
    entered, release = threading.Event(), threading.Event()

    def no_token(cls, *, prompt=True):
        entered.set()
        assert release.wait(5)
        raise AuthenticationError("No token")

    monkeypatch.setattr(Config, "get_token", classmethod(no_token))
    window, _ = _window(monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(root))]))
    try:
        qtbot.waitUntil(entered.is_set, timeout=5000)
        assert window.refresh_action.isEnabled()
        window._refresh_local_files(root)
        assert window.job is not None
        release.set()
        qtbot.waitUntil(lambda: window.job is None, timeout=5000)
        _wait_for_cycle(qtbot, window)
        assert backend.calls == []
    finally:
        release.set()


def test_close_waits_for_token_check(monkeypatch, qtbot, tmp_path, backend):
    root = tmp_path / "library"
    root.mkdir()
    entered, release = threading.Event(), threading.Event()

    def slow_token(cls, *, prompt=True):
        entered.set()
        assert release.wait(5)
        return "test-token"

    monkeypatch.setattr(Config, "get_token", classmethod(slow_token))
    window, _ = _window(monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(root))]))
    window.show()
    try:
        qtbot.waitUntil(entered.is_set, timeout=5000)
        window.close()
        assert window.isVisible()
        assert window._ads_token_check.running
        release.set()
        qtbot.waitUntil(lambda: not window.isVisible(), timeout=5000)
        assert backend.calls == []
    finally:
        release.set()


def test_timer_coalesces_while_job_runs(monkeypatch, qtbot, tmp_path, backend):
    root = tmp_path / "library"
    root.mkdir()
    entered, release = threading.Event(), threading.Event()
    concurrent = []

    def run(*args, **kwargs):
        concurrent.append("start")
        if len(concurrent) == 1:
            entered.set()
            assert release.wait(5)
        concurrent.append("end")
        return _result()

    backend.handler.run = run
    window, _ = _window(monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(root))]))
    try:
        qtbot.waitUntil(entered.is_set, timeout=5000)
        window._ads_recheck_timer.timeout.emit()
        window._ads_recheck_timer.timeout.emit()
        assert len(backend.calls) == 1
        window._remove_root(root)
        assert str(root) in window.registries
        release.set()
        qtbot.waitUntil(lambda: len(backend.calls) == 2, timeout=5000)
        _wait_for_cycle(qtbot, window)
        assert concurrent == ["start", "end", "start", "end"]
    finally:
        release.set()


def test_waits_for_foreground_job(monkeypatch, qtbot, tmp_path, backend):
    root = tmp_path / "library"
    root.mkdir()
    window, _ = _window(monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(root))]))
    window.job = SimpleNamespace(running=True)
    qtbot.waitUntil(lambda: window._ads_recheck_roots is not None, timeout=5000)
    assert backend.calls == []
    window.job = None
    window._ads_recheck_retry_timer.timeout.emit()
    qtbot.waitUntil(lambda: len(backend.calls) == 1, timeout=5000)
    _wait_for_cycle(qtbot, window)


def test_settings_are_rechecked_after_watcher_pause(
    monkeypatch, qtbot, tmp_path, backend
):
    root = tmp_path / "library"
    root.mkdir()
    controller = FakeWatchController(auto_ack=False)
    config = GuiConfig(
        roots=[RootEntry(str(root), watch_on_start=True)],
        identify_scopes={str(root): "library"},
    )
    window, _ = _window(monkeypatch, qtbot, config, controller=controller)
    qtbot.waitUntil(lambda: window._pending_watch_dialog is not None, timeout=5000)
    CentralPdfRegistryState(window.registries[str(root)]).set_identify_settings(
        IdentifySettings(providers=(DoiProvider.CROSSREF,))
    )
    controller.paused.emit(controller.session_id(root))
    qtbot.waitUntil(lambda: len(backend.calls) == 1, timeout=5000)
    _wait_for_cycle(qtbot, window)
    assert backend.calls[0][2]["identify_settings"].providers == (DoiProvider.CROSSREF,)
    assert backend.calls[0][2]["ads_enabled"] is False
    assert backend.clients == [None]
    assert controller.resumes == [root]


def test_no_token_after_settings_change_resumes_watcher(
    monkeypatch, qtbot, tmp_path, backend
):
    root = tmp_path / "library"
    root.mkdir()
    Config.save_identify_settings(IdentifySettings(providers=(DoiProvider.CROSSREF,)))

    def no_token(cls, *, prompt=True):
        raise AuthenticationError("No token")

    monkeypatch.setattr(Config, "get_token", classmethod(no_token))
    controller = FakeWatchController(auto_ack=False)
    config = GuiConfig(
        roots=[RootEntry(str(root), watch_on_start=True)],
        identify_scopes={str(root): "library"},
    )
    window, _ = _window(monkeypatch, qtbot, config, controller=controller)
    qtbot.waitUntil(lambda: window._pending_watch_dialog is not None, timeout=5000)
    CentralPdfRegistryState(window.registries[str(root)]).set_identify_settings(
        IdentifySettings(providers=(DoiProvider.ADS,))
    )
    controller.paused.emit(controller.session_id(root))
    _wait_for_cycle(qtbot, window)
    assert backend.calls == []
    assert backend.resources == []
    assert controller.pauses == [root]
    assert controller.resumes == [root]


def test_close_waits_for_cancelled_worker(monkeypatch, qtbot, tmp_path, backend):
    root = tmp_path / "library"
    root.mkdir()
    entered, saw_cancel, release = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )

    def run(_state, _client, _settings, *, cancelled, **kwargs):
        entered.set()
        while not cancelled():
            if release.wait(0.01):
                return _result()
        saw_cancel.set()
        assert release.wait(5)
        return _result(cancelled=True)

    backend.handler.run = run
    window, _ = _window(monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(root))]))
    window.show()
    try:
        qtbot.waitUntil(entered.is_set, timeout=5000)
        window.close()
        qtbot.waitUntil(saw_cancel.is_set, timeout=5000)
        assert window.isVisible()
        assert window._ads_recheck_job.running
        assert not window._ads_recheck_timer.isActive()
        release.set()
        qtbot.waitUntil(lambda: not window.isVisible(), timeout=5000)
        qtbot.wait(30)
        assert len(backend.calls) == 1
        assert window._ads_recheck_job is None
        assert backend.resources[-1][0] == "close"
    finally:
        release.set()


@pytest.mark.parametrize("external_only", [False, True])
def test_cancel_stops_the_remaining_roots(
    monkeypatch, qtbot, tmp_path, backend, external_only
):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    if external_only:
        Config.save_identify_settings(
            IdentifySettings(providers=(DoiProvider.CROSSREF,))
        )
    entered, stopped = threading.Event(), threading.Event()

    def run(_state, _client, _settings, *, cancelled, **kwargs):
        entered.set()
        while not cancelled():
            stopped.wait(0.01)
        stopped.set()
        return _result(cancelled=True)

    backend.handler.run = run
    config = GuiConfig(roots=[RootEntry(str(first)), RootEntry(str(second))])
    window, _ = _window(monkeypatch, qtbot, config)
    try:
        qtbot.waitUntil(entered.is_set, timeout=5000)
        window._ads_recheck_timer.timeout.emit()
        window._cancel_job()
        qtbot.waitUntil(stopped.is_set, timeout=5000)
        _wait_for_cycle(qtbot, window)
        qtbot.wait(50)
        assert [call[0] for call in backend.calls] == [first]
    finally:
        if window._ads_recheck_job is not None:
            window._ads_recheck_job.cancel()


def test_external_recheck_keeps_selection_and_passes_library_state(
    monkeypatch, qtbot, tmp_path, backend
):
    root = tmp_path / "library"
    root.mkdir()
    indexed, new = root / "indexed.pdf", root / "new.pdf"
    for path in (indexed, new):
        writer = PdfWriter()
        writer.add_blank_page(width=32, height=32)
        writer.write(path)
    hashes = {path: sha256_file(path) for path in (indexed, new)}
    state = open_registry_state(root)
    try:
        state.upsert_file(
            indexed.name,
            {"sha256": hashes[indexed], "status": "unresolved", "title": "Example"},
        )
        assert state.registry.catalog.get_file(root, new.name) is None
    finally:
        state.close()
    settings = IdentifySettings(
        providers=(DoiProvider.CROSSREF,),
        skip_unresolved_directory=True,
        output_unresolved="excluded",
    )
    Config.save_identify_settings(settings)
    entered, release = threading.Event(), threading.Event()
    observed = []

    def run(state, client, _rename, **kwargs):
        observed.append(
            (
                state.registry.catalog.get_file(root, indexed.name) is not None,
                state.registry.catalog.get_file(root, new.name) is not None,
                new.is_file(),
                client,
                kwargs,
            )
        )
        entered.set()
        assert release.wait(5)
        return _result(updated=1)

    backend.handler.run = run
    config = GuiConfig(
        roots=[RootEntry(str(root), recursive=True, watch_on_start=True)],
        last_root=str(root),
    )
    window, controller = _window(monkeypatch, qtbot, config)
    try:
        qtbot.waitUntil(entered.is_set, timeout=5000)
        row = next(
            row
            for row in range(window.model.rowCount())
            if window.model.record_at(row)["current_path"] == indexed.name
        )
        index = window._view_table_index(window.model.index(row, 0))
        assert index.isValid()
        window.table.selectionModel().setCurrentIndex(
            index, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows
        )
        selected = window._selected_edit_target()
        assert selected is not None and selected[2] == indexed
        release.set()
        _wait_for_cycle(qtbot, window)
        qtbot.waitUntil(
            lambda: (
                not window._watch_refresh_timer.isActive()
                and not window._pending_watch_refresh_roots
            ),
            timeout=5000,
        )
        selected = window._selected_edit_target()
        assert selected is not None and selected[2] == indexed
        assert observed[0][:4] == (True, True, True, None)
        assert observed[0][4]["identify_settings"] == settings
        assert observed[0][4]["recursive"] is True
        assert observed[0][4]["ads_enabled"] is False
        assert controller.pauses == [root]
        assert controller.resumes == [root]
        assert {path: sha256_file(path) for path in (indexed, new)} == hashes
    finally:
        release.set()


def test_close_before_startup_does_not_start_work(
    monkeypatch, qtbot, tmp_path, backend
):
    root = tmp_path / "library"
    root.mkdir()
    window, controller = _window(
        monkeypatch,
        qtbot,
        GuiConfig(roots=[RootEntry(str(root), watch_on_start=True)]),
    )
    window.show()
    window.close()
    qtbot.wait(300)
    assert not window.isVisible()
    assert backend.calls == []
    assert controller.active == {}


def test_close_before_watcher_ack_does_not_start_worker(
    monkeypatch, qtbot, tmp_path, backend
):
    root = tmp_path / "library"
    root.mkdir()
    controller = FakeWatchController(auto_ack=False)
    config = GuiConfig(roots=[RootEntry(str(root), watch_on_start=True)])
    window, _ = _window(monkeypatch, qtbot, config, controller=controller)
    window.show()
    qtbot.waitUntil(lambda: window._pending_watch_dialog is not None, timeout=5000)
    session = controller.session_id(root)
    window.close()
    controller.paused.emit(session)
    qtbot.wait(30)
    assert not window.isVisible()
    assert window._ads_recheck_job is None
    assert backend.calls == []
    assert controller.resumes == [root]


def test_changed_result_refreshes_and_aggregates_failures(
    monkeypatch, qtbot, tmp_path, backend
):
    root = tmp_path / "library"
    root.mkdir()
    backend.handler.run = lambda *args, **kwargs: _result(
        checked=3,
        updated=1,
        renamed=1,
        failures=["one failed", "two failed"],
        activities=["paper.pdf: verified and updated as 2022Example."],
    )
    window, _ = _window(monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(root))]))
    refreshed = []
    original_refresh = window.refresh

    def capture_refresh(root=None):
        refreshed.append(root)
        original_refresh(root)

    monkeypatch.setattr(window, "refresh", capture_refresh)
    qtbot.waitUntil(lambda: len(backend.calls) == 1, timeout=5000)
    _wait_for_cycle(qtbot, window)
    qtbot.waitUntil(lambda: root in refreshed, timeout=5000)
    notices = window.notice_bar.queued_notices()
    assert len(notices) == 1
    assert notices[0].key == "ads-recheck-failures"
    assert "one failed" in notices[0].details
    assert "two failed" in notices[0].details
    assert str(root) in notices[0].details
    assert "paper.pdf: verified and updated as 2022Example." in window.log.toPlainText()


def test_changed_root_refreshes_other_visible_library(
    monkeypatch, qtbot, tmp_path, backend
):
    first, visible = tmp_path / "first", tmp_path / "visible"
    first.mkdir()
    visible.mkdir()
    backend.handler.run = lambda state, *args, **kwargs: _result(
        updated=int(state.directory == first)
    )
    config = GuiConfig(
        roots=[RootEntry(str(first)), RootEntry(str(visible))], last_root=str(visible)
    )
    window, _ = _window(monkeypatch, qtbot, config)
    refreshed = []
    original_refresh = window.refresh

    def capture_refresh(root=None):
        refreshed.append(root)
        original_refresh(root)

    monkeypatch.setattr(window, "refresh", capture_refresh)
    qtbot.waitUntil(lambda: len(backend.calls) == 2, timeout=5000)
    _wait_for_cycle(qtbot, window)
    qtbot.waitUntil(lambda: visible in refreshed, timeout=5000)
    assert first in refreshed


@pytest.mark.parametrize("external_only", [False, True])
def test_worker_error_releases_resources_and_checks_next_root(
    monkeypatch, qtbot, tmp_path, backend, external_only
):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    if external_only:
        Config.save_identify_settings(
            IdentifySettings(providers=(DoiProvider.DATACITE,))
        )

    def run(state, *args, **kwargs):
        if state.directory == first:
            raise RuntimeError("Simulated ADS failure")
        return _result()

    backend.handler.run = run
    config = GuiConfig(roots=[RootEntry(str(first)), RootEntry(str(second))])
    window, _ = _window(monkeypatch, qtbot, config)
    qtbot.waitUntil(lambda: len(backend.calls) == 2, timeout=5000)
    _wait_for_cycle(qtbot, window)
    assert sum(item[0] == "close" for item in backend.resources) == 2
    assert sum(item[0] == "client-close" for item in backend.resources) == (
        0 if external_only else 2
    )
    notices = window.notice_bar.queued_notices()
    assert len(notices) == 1
    assert "Simulated ADS failure" in notices[0].details
    assert str(first) in notices[0].details


def test_older_backend_cannot_bypass_library_scope(
    monkeypatch, qtbot, tmp_path, backend
):
    root = tmp_path / "library"
    root.mkdir()
    calls = []

    def old_recheck(state, client, settings, *, ads_enabled, apply, cancelled):
        calls.append(state)
        return _result()

    monkeypatch.setattr(
        sys.modules["adsfolio.pdf.ads_recheck"], "recheck_existing", old_recheck
    )
    window, _ = _window(monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(root))]))
    qtbot.waitUntil(lambda: bool(window.notice_bar.queued_notices()), timeout=5000)
    _wait_for_cycle(qtbot, window)
    assert calls == []
    assert backend.resources == []
    assert "cannot apply this library's saved scope" in (
        window.notice_bar.queued_notices()[0].details
    )


@pytest.mark.parametrize("recursive", [False, True])
def test_real_backend_updates_and_renames_without_changing_pdf(
    monkeypatch, qtbot, tmp_path, recursive
):
    root = tmp_path / "library"
    root.mkdir()
    path = root / "paper.pdf"
    doi = "10.1234/verified-example"
    doc = {
        "bibcode": "2024ApJ...900..100E",
        "doi": [doi],
        "title": ["An Example Paper"],
        "author": ["Example, A."],
        "year": "2024",
    }
    external = {key: value for key, value in doc.items() if key != "bibcode"}
    external["provider"] = "crossref"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=400)
    writer.add_metadata({"/DOI": doi, "/Title": "An Example Paper"})
    writer.write(path)
    metadata = PdfIdentifier(None).build_record(external, path)
    metadata["notes"] = "Keep the user's notes"
    metadata["_adsfolio_locked_fields"] = ["title"]
    digest = sha256_file(path)
    nested = root / "nested" / "nested.pdf"
    excluded = root / "unresolved" / "excluded.pdf"
    nested.parent.mkdir()
    excluded.parent.mkdir()
    nested_doi = "10.1234/nested-example"
    excluded_doi = "10.1234/excluded-example"
    nested_doc = {
        "bibcode": "2024ApJ...900..101N",
        "doi": [nested_doi],
        "title": ["A Nested Paper"],
        "author": ["Nested, A."],
        "year": "2024",
    }
    extra_metadata = {}
    for extra_path, extra_doi, title in (
        (nested, nested_doi, "A Nested Paper"),
        (excluded, excluded_doi, "An Excluded Paper"),
    ):
        extra_writer = PdfWriter()
        extra_writer.add_blank_page(width=300, height=400)
        extra_writer.add_metadata({"/DOI": extra_doi, "/Title": title})
        extra_writer.write(extra_path)
        extra_metadata[extra_path] = PdfIdentifier(None).build_record(
            {"doi": [extra_doi], "title": [title], "provider": "crossref"},
            extra_path,
        )
    excluded_digest = sha256_file(excluded)
    nested_digest = sha256_file(nested)
    state = open_registry_state(root)
    state.upsert_file(path.name, metadata)
    for extra_path, record in extra_metadata.items():
        relative = extra_path.relative_to(root).as_posix()
        record["current_filename"] = relative
        state.upsert_file(relative, record)
    original = state.registry.catalog.get_file(root, path.name)
    original_excluded = state.registry.catalog.get_file(root, "unresolved/excluded.pdf")
    original_nested = state.registry.catalog.get_file(root, "nested/nested.pdf")
    state.close()
    Config.save_identify_settings(
        IdentifySettings(
            allow_ads=False,
            skip_unresolved_directory=True,
            output_unresolved="unresolved",
        )
    )
    Config.save_rename_settings(RenameSettings(template="{bibcode}"))
    monkeypatch.setattr(Config, "check_rate_limit", classmethod(lambda cls: True))
    queries = []
    closed = threading.Event()

    class Client:
        def __init__(self, *, token):
            assert token == "test-token"
            self.search = self

        def query(self, *, q, fl, rows, start=0):
            queries.append(q)
            documents = [item for item in (doc, nested_doc) if item["doi"][0] in q]
            return SimpleNamespace(
                num_found=len(documents), docs=documents, start=start
            )

        def close(self):
            closed.set()

    monkeypatch.setattr(ads_recheck_job, "ADSClient", Client)
    window, _ = _window(
        monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(root), recursive=recursive)])
    )
    qtbot.waitUntil(closed.is_set, timeout=5000)
    _wait_for_cycle(qtbot, window)
    target = root / f"{doc['bibcode']}.pdf"
    notices = window.notice_bar.queued_notices()
    assert target.is_file(), notices
    assert not path.exists()
    assert sha256_file(target) == digest
    state = open_registry_state(root)
    try:
        saved = state.registry.catalog.get_file(root, target.name)
        assert saved["attachment_id"] == original["attachment_id"]
        assert saved["bibcode"] == doc["bibcode"]
        assert saved["metadata"]["notes"] == "Keep the user's notes"
        assert saved["metadata"]["_adsfolio_locked_fields"] == ["title"]
        assert state.registry.catalog.get_file(root, "unresolved/excluded.pdf") == (
            original_excluded
        )
        if not recursive:
            assert state.registry.catalog.get_file(root, "nested/nested.pdf") == (
                original_nested
            )
    finally:
        state.close()
    assert queries
    assert excluded_doi not in " ".join(queries)
    assert (nested_doi in " ".join(queries)) is recursive
    assert sha256_file(excluded) == excluded_digest
    nested_final = (
        nested.with_name(f"{nested_doc['bibcode']}.pdf") if recursive else nested
    )
    assert sha256_file(nested_final) == nested_digest
    assert set(root.rglob("*.pdf")) == {target, nested_final, excluded}
    assert not notices
