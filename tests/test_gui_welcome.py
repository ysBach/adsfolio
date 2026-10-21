"""First-run guidance while no library folder exists."""

from __future__ import annotations

from pathlib import Path

from adsfolio.config import Config
from adsfolio.gui.config import GuiConfig, RootEntry, save_config
from adsfolio.gui.main_window import MainWindow
from adsfolio.user_profile import UserProfile


def make_window(qtbot) -> MainWindow:
    window = MainWindow()
    qtbot.addWidget(window)
    return window


def test_new_user_sees_welcome_steps_instead_of_empty_panes(qtbot, monkeypatch):
    monkeypatch.delenv(Config.ENV_VAR, raising=False)
    monkeypatch.setattr(Config, "get_token_paths", classmethod(lambda cls: []))
    window = make_window(qtbot)
    assert not window.welcome.isHidden()
    assert window.splitter.isHidden()
    assert window.welcome.token_status.text() == "No token yet."
    assert window.welcome.profile_status.text() == "No profile yet."
    assert "GitHub" in window.welcome.network_note.text()
    assert "Automatically Check for Updates" in window.welcome.network_note.text()
    assert window.start_library_button is window.welcome.add_folder_button


def test_welcome_steps_open_folder_token_and_profile(qtbot, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "adsfolio.gui.main_window.QFileDialog.getExistingDirectory",
        lambda *args: calls.append("folder") or "",
    )

    class Dialog:
        def __init__(self, name: str) -> None:
            self.name = name

        def exec(self) -> int:
            calls.append(self.name)
            return 0

    monkeypatch.setattr(
        "adsfolio.gui.main_window.TokenDialog", lambda parent: Dialog("token")
    )
    monkeypatch.setattr(
        "adsfolio.gui.main_window.UserProfileDialog",
        lambda *args, **kwargs: Dialog("profile"),
    )
    window = make_window(qtbot)
    window.welcome.add_folder_button.click()
    window.welcome.token_button.click()
    window.welcome.profile_button.click()
    assert calls == ["folder", "token", "profile"]
    assert window.registries == {}


def test_welcome_reports_existing_token_and_profile(qtbot, monkeypatch):
    monkeypatch.delenv(Config.ENV_VAR, raising=False)
    path = Config.save_token("secret")
    Config.save_user_profile(UserProfile(orcid="0000-0002-1825-0097"))
    window = make_window(qtbot)
    assert window.welcome.token_status.text() == "Token found."
    assert str(path) in window.welcome.token_status.toolTip()
    assert "secret" not in window.welcome.token_status.toolTip()
    assert window.welcome.token_button.text() == "Change Token…"
    assert window.welcome.profile_button.text() == "Edit Profile…"


def test_welcome_hides_once_a_library_exists(qtbot, tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    save_config(GuiConfig(roots=[RootEntry(str(root))]))
    window = make_window(qtbot)
    assert window.welcome.isHidden()
    assert not window.splitter.isHidden()
