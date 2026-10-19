"""Token setup and noninteractive token lookup."""

from __future__ import annotations

import sys

import pytest
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
