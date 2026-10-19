"""Check release comparisons, asynchronous failures, and update controls."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from packaging.version import Version
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction
from PySide6.QtNetwork import QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QApplication, QLabel

from adsfolio import __version__
from adsfolio.config import Config
from adsfolio.gui.about import AboutDialog
from adsfolio.gui.config import GuiConfig, load_config, save_config
from adsfolio.gui.main_window import MainWindow
from adsfolio.gui.notifications import NoticeAction
from adsfolio.gui.updates import (
    LATEST_RELEASE_API,
    REPOSITORY_URL,
    UpdateChecker,
    parse_release,
)


def release_payload(tag: str = "v1.0.0") -> dict[str, object]:
    return {"tag_name": tag, "draft": False, "prerelease": False}


class FakeReply(QObject):
    finished = Signal()

    def __init__(
        self,
        *,
        body: bytes | None = None,
        status: int | None = 200,
        error: QNetworkReply.NetworkError = QNetworkReply.NoError,
    ) -> None:
        super().__init__()
        self.body = json.dumps(release_payload()).encode() if body is None else body
        self.http_status = status
        self.network_error = error
        self.aborted = False

    def attribute(self, attribute: QNetworkRequest.Attribute) -> int | None:
        assert attribute == QNetworkRequest.HttpStatusCodeAttribute
        return self.http_status

    def error(self) -> QNetworkReply.NetworkError:
        return self.network_error

    def errorString(self) -> str:
        return "Connection unavailable"

    def readAll(self) -> bytes:
        return self.body

    def abort(self) -> None:
        self.aborted = True
        self.finished.emit()


class FakeNetwork:
    def __init__(self, reply: FakeReply) -> None:
        self.reply = reply
        self.requests: list[QNetworkRequest] = []

    def get(self, request: QNetworkRequest) -> FakeReply:
        self.requests.append(request)
        return self.reply


@pytest.fixture
def checker(qtbot) -> UpdateChecker:
    return UpdateChecker()


@pytest.mark.parametrize(
    ("current", "latest", "available"),
    [
        ("0.2.1", "v0.2.1", False),
        ("0.2.9", "v0.2.10", True),
        ("0.2.1", "v0.1.0", False),
        ("0.3.0rc1", "v0.3.0", True),
        ("1.0.0", "v2.0.0", True),
    ],
)
def test_release_comparison_uses_version_order(
    checker: UpdateChecker, current: str, latest: str, available: bool
) -> None:
    checker.current_version = Version(current)
    checker.latest = parse_release(release_payload(latest))
    assert checker.update_available is available
    assert str(Version(latest)) in checker.status


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        release_payload(""),
        release_payload("not a version"),
        release_payload("v1.0.0rc1"),
        release_payload("v1.0.0.dev1"),
        release_payload(" v1.0.0"),
        {**release_payload(), "draft": True},
        {**release_payload(), "prerelease": True},
        {**release_payload(), "draft": "false"},
    ],
)
def test_reject_invalid_or_unpublished_release(payload: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        parse_release(payload)


def test_release_link_uses_the_project_repository() -> None:
    release = parse_release(
        {**release_payload(), "html_url": "https://example.com/untrusted"}
    )
    assert release.url == f"{REPOSITORY_URL}/releases/tag/v1.0.0"


def test_check_is_async_deduplicated_and_unauthenticated(
    checker: UpdateChecker,
) -> None:
    reply = FakeReply()
    network = FakeNetwork(reply)
    checker._network = network
    completed = []
    checker.finished.connect(lambda: completed.append(True))
    checker.check()
    checker.check()
    assert checker.busy
    assert checker.latest is None
    assert completed == []
    assert len(network.requests) == 1
    request = network.requests[0]
    assert request.url().toString() == LATEST_RELEASE_API
    assert not request.hasRawHeader("Authorization")
    assert request.rawHeader("User-Agent").data() == f"ADSfolio/{__version__}".encode()
    assert request.rawHeader("Accept-Language").data() == b"en"
    assert request.transferTimeout() > 0
    assert request.attribute(QNetworkRequest.RedirectPolicyAttribute) == (
        QNetworkRequest.SameOriginRedirectPolicy
    )
    reply.finished.emit()
    assert not checker.busy
    assert completed == [True]
    assert checker.update_available
    assert checker.checked_at is not None
    assert checker.error is None


@pytest.mark.parametrize(
    ("status", "error", "body", "message"),
    [
        (403, QNetworkReply.ContentAccessDenied, b"", "limited"),
        (429, QNetworkReply.UnknownContentError, b"", "limited"),
        (404, QNetworkReply.ContentNotFoundError, b"", "No published release"),
        (None, QNetworkReply.TimeoutError, b"", "Could not check"),
        (200, QNetworkReply.NoError, b"<html>Bad response</html>", "Could not read"),
        (200, QNetworkReply.NoError, b"\xff", "Could not read"),
        (200, QNetworkReply.NoError, b"{}", "Could not read"),
        (500, QNetworkReply.NoError, b"", "HTTP 500"),
    ],
)
def test_check_failures_clear_stale_release_results(
    checker: UpdateChecker,
    status: int | None,
    error: QNetworkReply.NetworkError,
    body: bytes,
    message: str,
) -> None:
    reply = FakeReply(status=status, error=error, body=body)
    checker._network = FakeNetwork(reply)
    checker.latest = parse_release(release_payload())
    checker.check()
    reply.finished.emit()
    assert not checker.busy
    assert not checker.update_available
    assert checker.checked_at is None
    assert message in checker.status


def test_cancel_does_not_deliver_a_failed_result(checker: UpdateChecker) -> None:
    reply = FakeReply()
    checker._network = FakeNetwork(reply)
    finished = []
    checker.finished.connect(lambda: finished.append(True))
    checker.check()
    checker.cancel()
    assert reply.aborted
    assert not checker.busy
    assert finished == []
    assert checker.status == "Update check cancelled."


@pytest.fixture
def window(qtbot) -> MainWindow:
    widget = MainWindow()
    qtbot.addWidget(widget)
    return widget


def test_about_is_reusable_and_does_not_start_a_network_check(
    window: MainWindow,
) -> None:
    reply = FakeReply()
    network = FakeNetwork(reply)
    window.update_checker._network = network
    window.about_action.trigger()
    about = window._about_dialog
    assert about is not None
    assert about.isVisible()
    assert __version__ in about.version_label.text()
    assert any(
        "Copyright © 2026 @ysBach" in label.text()
        for label in about.findChildren(QLabel)
    )
    assert window.about_action.menuRole() == QAction.AboutRole
    about.close()
    window.about_action.trigger()
    assert window._about_dialog is about
    assert about.isVisible()
    assert network.requests == []


def test_manual_check_joins_startup_check_and_shows_result(window: MainWindow) -> None:
    reply = FakeReply(body=json.dumps(release_payload("v0.1.0")).encode())
    network = FakeNetwork(reply)
    window.update_checker._network = network
    window.check_updates_on_start()
    window.check_updates_action.trigger()
    about = window._about_dialog
    assert about is not None
    assert not about.check_button.isEnabled()
    assert len(network.requests) == 1
    reply.finished.emit()
    assert about.check_button.isEnabled()
    assert "newer than the latest" in about.update_status.text()
    assert window.notice_bar.queued_keys() == ()


def test_startup_update_notice_keeps_library_view_and_selection(
    window: MainWindow,
) -> None:
    query = window.current_query
    selection = window.table.selectionModel().selectedIndexes()
    reply = FakeReply()
    window.update_checker._network = FakeNetwork(reply)
    window.check_updates_on_start()
    reply.finished.emit()
    assert window._about_dialog is None
    assert window.notice_bar.queued_keys() == ("application-update",)
    assert window.notice_bar.action_button.text() == NoticeAction.VIEW_RELEASE.value
    assert window.current_query == query
    assert window.table.selectionModel().selectedIndexes() == selection
    window.notice_bar.action_button.click()
    assert window._about_dialog is not None
    assert window._about_dialog.isVisible()


def test_automatic_failure_is_silent_and_manual_check_can_retry(
    window: MainWindow,
) -> None:
    reply = FakeReply(status=None, error=QNetworkReply.HostNotFoundError)
    network = FakeNetwork(reply)
    window.update_checker._network = network
    window.check_updates_on_start()
    reply.finished.emit()
    assert window.notice_bar.queued_keys() == ()
    assert window._about_dialog is None
    window.check_updates_action.trigger()
    assert window._about_dialog is not None
    assert len(network.requests) == 2
    reply.finished.emit()
    assert "Could not check" in window._about_dialog.update_status.text()


def test_update_preference_persists_and_controls_next_start(window: MainWindow) -> None:
    window.about_action.trigger()
    about = window._about_dialog
    assert about is not None
    about.automatic_checkbox.setChecked(False)
    assert not window.automatic_updates_action.isChecked()
    assert not load_config().check_updates_automatically
    network = FakeNetwork(FakeReply())
    window.update_checker._network = network
    window.check_updates_on_start()
    assert network.requests == []
    window.automatic_updates_action.trigger()
    assert about.automatic_checkbox.isChecked()
    assert load_config().check_updates_automatically
    window.check_updates_on_start()
    assert len(network.requests) == 1
    window.close()
    assert network.reply.aborted


def test_failed_preference_save_is_visible(window: MainWindow, monkeypatch) -> None:
    def fail(config: GuiConfig) -> None:
        raise OSError("Read-only settings")

    monkeypatch.setattr("adsfolio.gui.main_window.save_config", fail)
    window.automatic_updates_action.setChecked(False)
    assert "Could not save the update preference" in window.notice_bar.message.text()


def test_close_cancels_request_and_about(window: MainWindow) -> None:
    reply = FakeReply()
    window.update_checker._network = FakeNetwork(reply)
    window.check_updates_action.trigger()
    window.close()
    assert reply.aborted
    assert window._about_dialog is not None
    assert not window._about_dialog.isVisible()
    window.check_updates_on_start()
    assert not window.update_checker.busy


@pytest.mark.parametrize("stored", [False, True, "false", 1, None])
def test_update_preference_validates_persisted_boolean(
    stored: object, tmp_path: Path
) -> None:
    config_dir = Config.get_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "gui.json").write_text(
        json.dumps({"check_updates_automatically": stored})
    )
    assert load_config().check_updates_automatically is (
        stored if isinstance(stored, bool) else True
    )


def test_update_preference_roundtrip_preserves_existing_choices() -> None:
    config = GuiConfig(
        check_updates_automatically=False,
        link_activation="single",
        detail_hidden_rows=["title"],
    )
    save_config(config)
    assert load_config() == config


def test_source_about_explains_updates_and_copy_omits_checkout_path(
    qtbot, checker: UpdateChecker, monkeypatch
) -> None:
    monkeypatch.setenv("ADSFOLIO_SOURCE_LAUNCH", "1")
    action = QAction()
    action.setCheckable(True)
    dialog = AboutDialog(checker, action)
    qtbot.addWidget(dialog)
    assert dialog.installation == "Local source launcher"
    assert "replaces this source launcher" in dialog.installation_label.text()
    dialog._copy_details()
    copied = QApplication.clipboard().text()
    assert f"ADSfolio {__version__}" in copied
    assert "System:" in copied
    assert str(Path(__file__).resolve().parents[1]) not in copied


def test_release_link_uses_configured_browser(
    qtbot, checker: UpdateChecker, monkeypatch
) -> None:
    calls = []
    monkeypatch.setattr(
        "adsfolio.gui.about.open_url",
        lambda url, application=None: calls.append((url, application)) or True,
    )
    action = QAction()
    action.setCheckable(True)
    dialog = AboutDialog(checker, action, browser_command="custom-browser")
    qtbot.addWidget(dialog)
    checker.latest = parse_release(release_payload())
    checker.changed.emit()
    dialog.release_button.click()
    assert calls == [(checker.latest.url, "custom-browser")]


def test_disabling_startup_checks_does_not_cancel_a_manual_check(
    window: MainWindow,
) -> None:
    reply = FakeReply()
    window.update_checker._network = FakeNetwork(reply)
    window.check_updates_action.trigger()
    window.automatic_updates_action.setChecked(False)
    assert window.update_checker.busy
    assert not reply.aborted
    reply.finished.emit()
    assert window.update_checker.latest is not None


def test_real_qt_request_keeps_event_loop_responsive_and_sends_no_token(
    checker: UpdateChecker, qtbot, monkeypatch
) -> None:
    from PySide6.QtCore import QTimer
    from PySide6.QtNetwork import QHostAddress, QNetworkProxy, QTcpServer

    server = QTcpServer()
    assert server.listen(QHostAddress.LocalHost)
    checker._network.setProxy(QNetworkProxy(QNetworkProxy.NoProxy))
    monkeypatch.setattr(
        "adsfolio.gui.updates.LATEST_RELEASE_API",
        f"http://127.0.0.1:{server.serverPort()}/latest",
    )
    monkeypatch.setenv(Config.ENV_VAR, "test-private-ads-token")
    checker.check()
    qtbot.waitUntil(server.hasPendingConnections)
    peer = server.nextPendingConnection()
    qtbot.waitUntil(lambda: peer.bytesAvailable() > 0)
    request = bytes(peer.readAll())
    assert request.startswith(b"GET /latest HTTP/1.1")
    assert b"authorization" not in request.lower()
    assert b"test-private-ads-token" not in request
    assert b"Accept-Language: en\r\n" in request
    heartbeat = []
    QTimer.singleShot(0, lambda: heartbeat.append(True))
    qtbot.waitUntil(lambda: bool(heartbeat))
    assert checker.busy
    body = json.dumps(release_payload()).encode()
    peer.write(
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
        + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
        + body
    )
    peer.disconnectFromHost()
    qtbot.waitUntil(lambda: not checker.busy)
    assert checker.update_available
    assert checker.error is None
    server.close()


@pytest.mark.parametrize("system", ["darwin", "win32", "linux"])
def test_desktop_update_instructions_match_the_platform(
    qtbot, checker: UpdateChecker, monkeypatch, system: str
) -> None:
    import sys

    monkeypatch.delenv("ADSFOLIO_SOURCE_LAUNCH", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", system)
    action = QAction()
    action.setCheckable(True)
    dialog = AboutDialog(checker, action)
    qtbot.addWidget(dialog)
    assert dialog.installation == "Desktop application"
    assert ("brew upgrade" in dialog.installation_label.text()) is (system == "darwin")


def test_official_bundle_never_shows_source_launcher_guidance(
    qtbot, checker: UpdateChecker, monkeypatch
) -> None:
    import sys

    monkeypatch.setenv("ADSFOLIO_SOURCE_LAUNCH", "1")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    action = QAction()
    action.setCheckable(True)
    dialog = AboutDialog(checker, action)
    qtbot.addWidget(dialog)
    assert dialog.installation == "Desktop application"
    assert "Local source launcher" not in dialog.installation_label.text()
