"""Test account settings with isolated files and fake ADS replies."""

from __future__ import annotations

import json
import os
from pathlib import Path

import platformdirs
import pytest
from PySide6.QtCore import QObject, QUrlQuery, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from adsfolio.config import Config
from adsfolio.gui.dialogs import UserProfileDialog
from adsfolio.gui.preferences_account import AccountPreferences
from adsfolio.gui.token_dialog import TokenDialog
from adsfolio.user_profile import UserProfile

_ORIGINAL_CONFIG_DIR = Config.__dict__["get_config_dir"]


@pytest.fixture(autouse=True)
def isolated_account_config(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv(Config.ENV_VAR, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.chdir(tmp_path)

    def prevent_platform_directory_lookup(*args, **kwargs):
        pytest.fail("Unexpected platform configuration directory lookup")

    monkeypatch.setattr(
        platformdirs, "user_config_dir", prevent_platform_directory_lookup
    )
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "configured-root")
    )

    def prevent_network(*args, **kwargs):
        pytest.fail("Unexpected network access")

    monkeypatch.setattr(QNetworkAccessManager, "get", prevent_network)


class FakeReply(QObject):
    finished = Signal()

    def __init__(self, status=200, error=QNetworkReply.NetworkError.NoError):
        super().__init__()
        self.status = status
        self.network_error = error
        self.aborted = False

    def attribute(self, attribute):
        return self.status

    def error(self):
        return self.network_error

    def errorString(self):
        return "fictional-secret"

    def abort(self):
        self.aborted = True
        self.finished.emit()


class FakeNetwork:
    def __init__(self, reply):
        self.reply = reply
        self.requests = []

    def get(self, request):
        self.requests.append(request)
        return self.reply


def save_button(dialog):
    buttons = dialog.findChild(QDialogButtonBox)
    return buttons.button(QDialogButtonBox.StandardButton.Save)


def test_page_embeds_both_dialogs_without_writing(qtbot, tmp_path):
    outer = QDialog()
    qtbot.addWidget(outer)
    page = AccountPreferences(outer)
    QVBoxLayout(outer).addWidget(page)
    outer.show()

    assert isinstance(page, QWidget)
    assert isinstance(page.token_dialog, TokenDialog)
    assert isinstance(page.profile_dialog, UserProfileDialog)
    assert [page.tabs.tabText(i) for i in range(page.tabs.count())] == [
        "ADS token",
        "Publication profile",
    ]
    for dialog in (page.token_dialog, page.profile_dialog):
        page.tabs.setCurrentWidget(dialog)
        buttons = dialog.findChild(QDialogButtonBox)
        assert buttons.button(QDialogButtonBox.StandardButton.Cancel).isHidden()
        dialog.reject()
        assert not dialog.isHidden()
    assert page.token_dialog.token.echoMode() == QLineEdit.EchoMode.Password
    assert not (tmp_path / "configured-root").exists()


def test_token_save_stays_open_and_uses_private_storage(qtbot, tmp_path):
    outer = QDialog()
    qtbot.addWidget(outer)
    page = AccountPreferences(outer)
    QVBoxLayout(outer).addWidget(page)
    outer.show()
    token = page.token_dialog
    saved = []
    page.token_saved.connect(lambda: saved.append(True))
    token.token.setText("  fictional-secret  ")

    save_button(token).click()

    path = tmp_path / "configured-root" / "token"
    assert path.read_text() == "fictional-secret"
    if os.name != "nt":
        assert path.stat().st_mode & 0o077 == 0
    assert token.token.text() == ""
    assert token.save_status.text() == "Token saved."
    assert saved == [True]
    assert outer.isVisible() and token.isVisible()
    assert "fictional-secret" not in token.source_label.text()
    token.token.setText("changed")
    assert token.save_status.text() == ""


def test_blank_and_failed_token_saves_do_not_expose_token(
    qtbot, monkeypatch, tmp_path, capsys, caplog
):
    Config.save_token("previous-secret")
    page = AccountPreferences()
    qtbot.addWidget(page)
    page.show()
    token = page.token_dialog
    saved = []
    page.token_saved.connect(lambda: saved.append(True))
    save_button(token).click()
    assert "Paste a token" in token.save_status.text()

    def deny_save(cls, value):
        raise PermissionError(value)

    monkeypatch.setattr(Config, "save_token", classmethod(deny_save))
    token.token.setText("fictional-secret")
    save_button(token).click()
    assert "Could not save" in token.save_status.text()
    assert token.token.text() == "fictional-secret"
    assert page.isVisible() and token.isVisible()
    assert "fictional-secret" not in token.save_status.text()
    assert "fictional-secret" not in str(capsys.readouterr())
    assert "fictional-secret" not in caplog.text
    assert (tmp_path / "configured-root" / "token").read_text() == "previous-secret"
    assert saved == []


def test_environment_override_and_explicit_token_test(qtbot, monkeypatch, tmp_path):
    monkeypatch.setenv(Config.ENV_VAR, "environment-secret")
    Config.save_token("stored-secret")
    page = AccountPreferences()
    qtbot.addWidget(page)
    page.show()
    token = page.token_dialog
    reply = FakeReply()
    network = FakeNetwork(reply)
    token._network = network

    assert not token.environment_notice.isHidden()
    assert Config.ENV_VAR in token.source_label.text()
    assert network.requests == []
    token.test_button.click()
    token.test_button.click()
    assert len(network.requests) == 1
    request = network.requests[0]
    assert request.rawHeader("Authorization").data() == b"Bearer environment-secret"
    assert request.url().toString().startswith(f"{Config.API_BASE}/search/query?")
    query = QUrlQuery(request.url())
    assert query.queryItemValue("q") == "year:2000"
    assert query.queryItemValue("rows") == "1"
    assert query.queryItemValue("fl") == "bibcode"
    assert request.attribute(QNetworkRequest.RedirectPolicyAttribute) == (
        QNetworkRequest.SameOriginRedirectPolicy
    )
    assert request.transferTimeout() == 15_000
    reply.finished.emit()
    assert token.test_status.text() == "ADS accepted the token."
    assert token.test_button.isEnabled()
    assert (tmp_path / "configured-root" / "token").read_text() == "stored-secret"

    token.token.setText("replacement-secret")
    save_button(token).click()
    assert (tmp_path / "configured-root" / "token").read_text() == "replacement-secret"
    assert Config.get_token(prompt=False) == "environment-secret"
    assert Config.ENV_VAR in token.source_label.text()


@pytest.mark.parametrize(
    ("status", "error", "expected"),
    [
        (401, QNetworkReply.NetworkError.AuthenticationRequiredError, "rejected"),
        (403, QNetworkReply.NetworkError.ContentAccessDenied, "rejected"),
        (429, QNetworkReply.NetworkError.UnknownContentError, "rate limit"),
        (None, QNetworkReply.NetworkError.HostNotFoundError, "Could not reach ADS"),
        (503, QNetworkReply.NetworkError.NoError, "HTTP 503"),
    ],
)
def test_pasted_token_test_reports_result_without_secrets(
    qtbot, monkeypatch, tmp_path, status, error, expected
):
    monkeypatch.setenv(Config.ENV_VAR, "environment-secret")
    page = AccountPreferences()
    qtbot.addWidget(page)
    page.show()
    token = page.token_dialog
    reply = FakeReply(status, error)
    network = FakeNetwork(reply)
    token._network = network
    token.token.setText("  fictional-secret  ")

    token.test_button.click()
    assert len(network.requests) == 1
    assert network.requests[0].rawHeader("Authorization").data() == (
        b"Bearer fictional-secret"
    )
    reply.finished.emit()
    assert expected in token.test_status.text()
    assert "fictional-secret" not in token.test_status.text()
    assert "environment-secret" not in token.test_status.text()
    assert not (tmp_path / "configured-root").exists()


def test_missing_token_test_does_not_send_a_request(qtbot, tmp_path):
    page = AccountPreferences()
    qtbot.addWidget(page)
    reply = FakeReply()
    network = FakeNetwork(reply)
    page.token_dialog._network = network
    page.token_dialog.test_button.click()
    assert network.requests == []
    assert "Paste a token" in page.token_dialog.test_status.text()
    assert not (tmp_path / "configured-root").exists()


def test_empty_token_field_tests_stored_token_without_displaying_it(qtbot):
    Config.save_token("stored-secret")
    page = AccountPreferences()
    qtbot.addWidget(page)
    reply = FakeReply()
    network = FakeNetwork(reply)
    page.token_dialog._network = network

    page.token_dialog.test_button.click()

    assert (
        network.requests[0].rawHeader("Authorization").data() == b"Bearer stored-secret"
    )
    assert page.token_dialog.token.text() == ""
    reply.finished.emit()
    assert all(
        "stored-secret" not in label.text() for label in page.findChildren(QLabel)
    )


def test_token_lookup_error_does_not_expose_its_message(qtbot, monkeypatch, caplog):
    page = AccountPreferences()
    qtbot.addWidget(page)
    page.show()

    def fail_lookup(cls, *, prompt):
        raise OSError("fictional-secret")

    monkeypatch.setattr(Config, "get_token", classmethod(fail_lookup))
    page.token_dialog.test_button.click()
    assert "Could not test" in page.token_dialog.test_status.text()
    assert "fictional-secret" not in page.token_dialog.test_status.text()
    assert "fictional-secret" not in caplog.text
    assert page.token_dialog.test_button.isEnabled()


def test_saving_during_token_test_cancels_test_and_keeps_page_open(qtbot, tmp_path):
    page = AccountPreferences()
    qtbot.addWidget(page)
    page.show()
    token = page.token_dialog
    reply = FakeReply()
    token._network = FakeNetwork(reply)
    token.token.setText("fictional-secret")
    token.test_button.click()

    save_button(token).click()

    assert reply.aborted
    assert (tmp_path / "configured-root" / "token").read_text() == "fictional-secret"
    assert token.save_status.text() == "Token saved."
    assert page.isVisible() and token.isVisible()


def test_token_test_and_cancel_use_existing_dialog_methods(qtbot, monkeypatch):
    original_test = TokenDialog.test_token
    original_done = TokenDialog.done
    calls = []

    def record_test(dialog):
        calls.append("test")
        original_test(dialog)

    def record_done(dialog, result):
        calls.append(result)
        original_done(dialog, result)

    monkeypatch.setattr(TokenDialog, "test_token", record_test)
    monkeypatch.setattr(TokenDialog, "done", record_done)
    page = AccountPreferences()
    qtbot.addWidget(page)
    page.show()
    reply = FakeReply()
    page.token_dialog._network = FakeNetwork(reply)
    page.token_dialog.token.setText("fictional-secret")

    page.token_dialog.test_button.click()
    page.close()

    assert calls == ["test", QDialog.DialogCode.Rejected]
    assert reply.aborted


def test_profile_save_validates_normalizes_and_preserves_other_settings(
    qtbot, tmp_path
):
    config_dir = tmp_path / "configured-root"
    config_dir.mkdir()
    setup = config_dir / "setup.json"
    setup.write_text(json.dumps({"other_setting": {"preserve": True}}))
    outer = QDialog()
    qtbot.addWidget(outer)
    page = AccountPreferences(outer)
    QVBoxLayout(outer).addWidget(page)
    outer.show()
    page.tabs.setCurrentWidget(page.profile_dialog)
    profile = page.profile_dialog
    saved = []
    page.profile_saved.connect(lambda: saved.append(True))
    profile.orcid.setText("https://orcid.org/0000-0002-1825-0097")
    profile.author_names.setPlainText(" Doe, Jane \nDoe, J.\nDoe, Jane")
    profile.first_year.setValue(2000)
    profile.last_year.setValue(2020)
    profile.library_ids.setPlainText("LIB1\n LIB2 \nLIB1")

    save_button(profile).click()

    assert Config.load_user_profile() == UserProfile(
        orcid="0000-0002-1825-0097",
        author_names=("Doe, Jane", "Doe, J."),
        first_publication_year=2000,
        last_publication_year=2020,
        ads_library_ids=("LIB1", "LIB2"),
    )
    assert json.loads(setup.read_text())["other_setting"] == {"preserve": True}
    assert profile.save_status.text() == "Publication profile saved."
    assert saved == [True]
    assert outer.isVisible() and profile.isVisible()
    profile.orcid.setText("")
    assert profile.save_status.text() == ""


def test_invalid_profile_does_not_save(qtbot, tmp_path):
    page = AccountPreferences()
    qtbot.addWidget(page)
    page.show()
    page.tabs.setCurrentWidget(page.profile_dialog)
    profile = page.profile_dialog
    saved = []
    page.profile_saved.connect(lambda: saved.append(True))
    profile.orcid.setText("0000-0002-1825-0098")
    save_button(profile).click()
    assert "valid ORCID" in profile.save_status.text()
    assert not (tmp_path / "configured-root").exists()

    profile.orcid.clear()
    profile.first_year.setValue(2025)
    profile.last_year.setValue(2020)
    save_button(profile).click()
    assert "First publication year" in profile.save_status.text()
    assert not (tmp_path / "configured-root").exists()
    assert page.isVisible() and profile.isVisible()
    assert saved == []


def test_profile_save_error_keeps_page_open(qtbot, monkeypatch, tmp_path):
    Config.save_user_profile(UserProfile(author_names=("Existing, Author",)))
    setup_path = tmp_path / "configured-root" / "setup.json"
    before = setup_path.read_bytes()

    def deny_save(cls, value):
        raise PermissionError("fictional-secret")

    page = AccountPreferences()
    qtbot.addWidget(page)
    page.show()
    page.tabs.setCurrentWidget(page.profile_dialog)
    profile = page.profile_dialog
    profile.author_names.setPlainText("Doe, Jane")
    monkeypatch.setattr(Config, "save_user_profile", classmethod(deny_save))
    save_button(profile).click()
    assert "Could not save" in profile.save_status.text()
    assert "fictional-secret" not in profile.save_status.text()
    assert profile.author_names.toPlainText() == "Doe, Jane"
    assert page.isVisible() and profile.isVisible()
    assert setup_path.read_bytes() == before


def test_close_discards_unsaved_changes_without_network(qtbot, tmp_path):
    Config.save_token("original-secret")
    Config.save_user_profile(UserProfile(author_names=("Existing, Author",)))
    before = {
        path.name: path.read_bytes()
        for path in (tmp_path / "configured-root").iterdir()
    }
    outer = QDialog()
    qtbot.addWidget(outer)
    page = AccountPreferences(outer)
    QVBoxLayout(outer).addWidget(page)
    outer.show()
    page.token_dialog.token.setText("unsaved-secret")
    page.profile_dialog.author_names.setPlainText("Unsaved, Author")

    outer.reject()

    after = {
        path.name: path.read_bytes()
        for path in (tmp_path / "configured-root").iterdir()
    }
    assert after == before


@pytest.mark.parametrize("close_action", ["close", "reject", "accept"])
def test_closing_outer_window_aborts_test(qtbot, close_action):
    outer = QDialog()
    qtbot.addWidget(outer)
    page = AccountPreferences(outer)
    QVBoxLayout(outer).addWidget(page)
    outer.show()
    reply = FakeReply()
    network = FakeNetwork(reply)
    token = page.token_dialog
    token._network = network
    token.token.setText("fictional-secret")
    token.test_button.click()
    assert len(network.requests) == 1

    getattr(outer, close_action)()

    assert reply.aborted
    assert token._reply is None
    assert token.test_button.isEnabled()
    assert token.test_status.text() == "Token test canceled."
    outer.show()
    assert token.isVisible()


def test_hiding_page_or_token_tab_aborts_test(qtbot):
    outer = QDialog()
    qtbot.addWidget(outer)
    stack = QStackedWidget(outer)
    page = AccountPreferences(stack)
    stack.addWidget(page)
    stack.addWidget(QWidget())
    QVBoxLayout(outer).addWidget(stack)
    outer.show()
    token = page.token_dialog
    token.token.setText("fictional-secret")
    first_reply = FakeReply()
    token._network = FakeNetwork(first_reply)
    token.test_button.click()
    page.tabs.setCurrentWidget(page.profile_dialog)
    assert first_reply.aborted

    page.tabs.setCurrentWidget(token)
    assert token.isVisible()
    second_reply = FakeReply()
    token._network = FakeNetwork(second_reply)
    token.test_button.click()
    stack.setCurrentIndex(1)
    assert second_reply.aborted

    third_reply = FakeReply()
    token._network = FakeNetwork(third_reply)
    token.test_button.click()
    outer.close()
    assert third_reply.aborted


def test_editing_token_cancels_its_in_flight_test(qtbot):
    page = AccountPreferences()
    qtbot.addWidget(page)
    page.show()
    token = page.token_dialog
    reply = FakeReply()
    token._network = FakeNetwork(reply)
    token.token.setText("first-secret")
    token.test_button.click()

    token.token.setText("second-secret")

    assert reply.aborted
    assert token._reply is None
    assert token.test_button.isEnabled()
    assert token.test_status.text() == "Token test canceled."
    assert token.isVisible()


def test_existing_config_migration_precedes_page_open(qtbot, monkeypatch, tmp_path):
    legacy = tmp_path / "ads"
    legacy.mkdir()
    (legacy / "token").write_text("legacy-secret")
    (legacy / "setup.json").write_text(
        json.dumps({"user_profile": {"author_names": ["Existing, Author"]}})
    )
    before = {path.name: path.read_bytes() for path in legacy.iterdir()}
    destination = tmp_path / "configured-root"
    monkeypatch.setattr(
        platformdirs,
        "user_config_dir",
        lambda appname, *args, **kwargs: str(
            legacy if appname == "ads" else destination
        ),
    )
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(_ORIGINAL_CONFIG_DIR.__func__)
    )
    # Simulate the application's configuration load before opening Settings.
    assert Config.get_config_dir() == destination
    migrated = {path.name: path.read_bytes() for path in destination.iterdir()}
    page = AccountPreferences()
    qtbot.addWidget(page)
    page.show()
    assert page.profile_dialog.author_names.toPlainText() == "Existing, Author"
    assert (destination / "token").read_text() == "legacy-secret"
    assert not page.load_button.isVisible()
    assert {path.name: path.read_bytes() for path in destination.iterdir()} == migrated
    assert {path.name: path.read_bytes() for path in legacy.iterdir()} == before

    reply = FakeReply()
    network = FakeNetwork(reply)
    page.token_dialog._network = network
    page.token_dialog.test_button.click()
    assert (
        network.requests[0].rawHeader("Authorization").data() == b"Bearer legacy-secret"
    )
    reply.finished.emit()
    page.token_dialog.token.setText("replacement-secret")
    save_button(page.token_dialog).click()
    assert page.token_dialog.save_status.text() == "Token saved."
    page.tabs.setCurrentWidget(page.profile_dialog)
    page.profile_dialog.author_names.setPlainText("Changed, Author")
    save_button(page.profile_dialog).click()
    assert page.profile_dialog.save_status.text() == "Publication profile saved."
    page.close()

    assert {path.name: path.read_bytes() for path in legacy.iterdir()} == before
    assert all(
        "legacy-secret" not in label.text() for label in page.findChildren(QLabel)
    )


def test_custom_config_directory_is_used_with_other_settings_present(qtbot, tmp_path):
    legacy = tmp_path / "ads"
    legacy.mkdir()
    (legacy / "token").write_text("legacy-secret")
    Config.save_user_profile(UserProfile(author_names=("Existing, Author",)))
    page = AccountPreferences()
    qtbot.addWidget(page)
    page.show()
    assert page.profile_dialog.author_names.toPlainText() == "Existing, Author"

    page.token_dialog.token.setText("replacement-secret")
    save_button(page.token_dialog).click()
    page.tabs.setCurrentWidget(page.profile_dialog)
    page.profile_dialog.author_names.setPlainText("Changed, Author")
    save_button(page.profile_dialog).click()

    assert (tmp_path / "configured-root" / "token").read_text() == "replacement-secret"
    assert Config.load_user_profile().author_names == ("Changed, Author",)
    assert (legacy / "token").read_text() == "legacy-secret"
    assert not (tmp_path / "adsfolio").exists()


def test_open_and_close_do_not_save(qtbot, monkeypatch, tmp_path):
    def prevent_save(cls, value):
        pytest.fail("Unexpected save")

    monkeypatch.setattr(Config, "save_token", classmethod(prevent_save))
    monkeypatch.setattr(Config, "save_user_profile", classmethod(prevent_save))
    page = AccountPreferences()
    qtbot.addWidget(page)
    page.show()
    page.close()
    assert not (tmp_path / "configured-root").exists()


def test_load_error_keeps_settings_untouched(qtbot, monkeypatch, tmp_path):
    legacy = tmp_path / "ads"
    legacy.mkdir()
    (legacy / "token").write_text("fictional-secret")

    def fail_load(cls):
        raise PermissionError("fictional-secret")

    monkeypatch.setattr(Config, "load_user_profile", classmethod(fail_load))
    page = AccountPreferences()
    qtbot.addWidget(page)
    page.show()
    page.load_button.click()

    assert "Could not load" in page.load_status.text()
    assert "fictional-secret" not in page.load_status.text()
    assert page.load_button.isVisible()
    assert page.token_dialog is None
    assert not (tmp_path / "configured-root").exists()
    assert (legacy / "token").read_text() == "fictional-secret"
