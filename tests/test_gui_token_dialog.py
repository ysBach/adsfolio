"""Token setup and noninteractive token lookup."""

from __future__ import annotations

import sys

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QDialog, QLineEdit

from adsfolio.config import AuthenticationError, Config
from adsfolio.gui.token_dialog import TokenDialog


@pytest.fixture(autouse=True)
def isolated_token(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv(Config.ENV_VAR, raising=False)
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        Config, "get_token_paths", classmethod(lambda cls: [tmp_path / "token"])
    )


def test_save_masked_token(qtbot, tmp_path) -> None:
    dialog = TokenDialog()
    qtbot.addWidget(dialog)
    assert dialog.token.echoMode() == QLineEdit.EchoMode.Password
    dialog.token.setText("  test-token  ")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert (tmp_path / "token").read_text() == "test-token"
    assert Config.get_token(prompt=False) == "test-token"
    assert dialog.token.text() == ""


def test_cancel_preserves_token(qtbot, tmp_path) -> None:
    Config.save_token("original")
    dialog = TokenDialog()
    qtbot.addWidget(dialog)
    dialog.token.setText("replacement")
    dialog.reject()
    assert (tmp_path / "token").read_text() == "original"


def test_blank_token_keeps_dialog_open(qtbot, tmp_path) -> None:
    dialog = TokenDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.accept()
    assert dialog.isVisible()
    assert "Paste a token" in dialog.error.text()
    assert not (tmp_path / "token").exists()


def test_save_error_keeps_dialog_open_without_disclosing_token(
    qtbot, monkeypatch
) -> None:
    def deny_save(cls, token: str) -> None:
        raise PermissionError(token)

    monkeypatch.setattr(Config, "save_token", classmethod(deny_save))
    dialog = TokenDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.token.setText("secret-test-token")
    dialog.accept()
    assert dialog.isVisible()
    assert "Could not save" in dialog.error.text()
    assert "secret-test-token" not in dialog.error.text()


def test_environment_token_precedes_saved_token(qtbot, monkeypatch) -> None:
    monkeypatch.setenv(Config.ENV_VAR, "environment-token")
    Config.save_token("saved-token")
    dialog = TokenDialog()
    qtbot.addWidget(dialog)
    assert not dialog.environment_notice.isHidden()
    assert Config.get_token(prompt=False) == "environment-token"


@pytest.mark.parametrize("prompt", [False, True])
def test_missing_token_without_stdin_raises_actionable_error(
    monkeypatch, prompt
) -> None:
    monkeypatch.setattr(sys, "stdin", None)
    with pytest.raises(AuthenticationError, match="Tools → NASA ADS Token"):
        Config.get_token(prompt=prompt)


def test_blank_environment_token_falls_back_to_file(monkeypatch) -> None:
    Config.save_token("saved-token")
    monkeypatch.setenv(Config.ENV_VAR, "  ")
    assert Config.get_token(prompt=False) == "saved-token"


class FakeReply(QObject):
    finished = Signal()

    def __init__(self, status: int | None, error=None) -> None:
        super().__init__()
        self.status = status
        self.network_error = QNetworkReply.NoError if error is None else error
        self.aborted = False

    def attribute(self, attribute):
        return self.status

    def error(self):
        return self.network_error

    def errorString(self) -> str:
        return "Host not found"

    def abort(self) -> None:
        self.aborted = True


class FakeNetwork:
    def __init__(self, reply: FakeReply) -> None:
        self.reply = reply
        self.requests: list[QNetworkRequest] = []

    def get(self, request: QNetworkRequest) -> FakeReply:
        self.requests.append(request)
        return self.reply


def test_dialog_shows_where_the_active_token_comes_from(
    qtbot, tmp_path, monkeypatch
) -> None:
    dialog = TokenDialog()
    qtbot.addWidget(dialog)
    assert "No token is saved" in dialog.source_label.text()
    Config.save_token("saved")
    assert Config.token_source() == str(tmp_path / "token")
    monkeypatch.setenv(Config.ENV_VAR, "from-env")
    dialog = TokenDialog()
    qtbot.addWidget(dialog)
    assert Config.ENV_VAR in dialog.source_label.text()


@pytest.mark.parametrize(
    ("status", "error", "message"),
    [
        (200, None, "ADS accepted the token."),
        (401, QNetworkReply.AuthenticationRequiredError, "ADS rejected"),
        (429, QNetworkReply.UnknownContentError, "rate limit"),
        (None, QNetworkReply.HostNotFoundError, "Could not reach ADS"),
    ],
)
def test_test_token_sends_one_ads_search_with_the_pasted_token(
    qtbot, status, error, message
) -> None:
    dialog = TokenDialog()
    qtbot.addWidget(dialog)
    reply = FakeReply(status, error)
    network = FakeNetwork(reply)
    dialog._network = network
    dialog.token.setText(" pasted ")
    dialog.test_button.click()
    dialog.test_button.click()
    assert len(network.requests) == 1
    request = network.requests[0]
    assert request.url().toString().startswith(f"{Config.API_BASE}/search/query?")
    assert request.rawHeader("Authorization").data() == b"Bearer pasted"
    assert request.attribute(QNetworkRequest.RedirectPolicyAttribute) == (
        QNetworkRequest.SameOriginRedirectPolicy
    )
    assert not dialog.test_button.isEnabled()
    reply.finished.emit()
    assert message in dialog.test_status.text()
    assert dialog.test_button.isEnabled()
    assert not (Config.get_config_dir() / "token").exists()


def test_test_token_without_any_token_asks_for_one(qtbot) -> None:
    dialog = TokenDialog()
    qtbot.addWidget(dialog)
    network = FakeNetwork(FakeReply(200))
    dialog._network = network
    dialog.test_button.click()
    assert network.requests == []
    assert "Paste a token" in dialog.test_status.text()


def test_closing_aborts_a_running_token_test(qtbot) -> None:
    dialog = TokenDialog()
    qtbot.addWidget(dialog)
    reply = FakeReply(200)
    dialog._network = FakeNetwork(reply)
    dialog.token.setText("pasted")
    dialog.test_button.click()
    dialog.reject()
    assert reply.aborted
