import os
import subprocess
import sys
from pathlib import Path

import pytest

from adsfolio.gui import osint
from adsfolio.gui.osint import open_path, open_url, reveal_command


def test_reveal_command_macos(tmp_path) -> None:
    path = tmp_path / "paper.pdf"
    assert reveal_command(path, "darwin") == [
        "open",
        "-R",
        str(path),
    ]


def test_reveal_command_windows_uses_single_select_argument() -> None:
    command = reveal_command(Path(r"C:\Papers\paper.pdf"), "win32")
    assert command == ["explorer", r"/select,C:\Papers\paper.pdf"]
    assert len(command) == 2
    assert command[1].startswith("/select,")


def test_reveal_command_linux_uses_dbus(tmp_path) -> None:
    path = tmp_path / "paper with spaces.pdf"
    command = reveal_command(path, "linux")
    assert command is not None
    assert command[0] == "dbus-send"
    assert "org.freedesktop.FileManager1.ShowItems" in command
    assert f"array:string:{path.as_uri()}" in command


def test_reveal_command_unknown_platform_returns_none() -> None:
    assert reveal_command(Path("paper.pdf"), "plan9") is None


def test_open_path_uses_configured_application(tmp_path, monkeypatch) -> None:
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF")
    calls = []
    monkeypatch.setattr(
        "adsfolio.gui.osint.subprocess.Popen",
        lambda argv, **kwargs: calls.append((argv, kwargs)),
    )

    assert open_path(path, application="pdf-viewer")
    assert calls[0][0] == ["pdf-viewer", str(path)]


def test_open_url_uses_configured_browser(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        "adsfolio.gui.osint.subprocess.Popen",
        lambda argv, **kwargs: calls.append((argv, kwargs)),
    )

    assert open_url("https://example.test/paper", application="web-browser")
    assert calls[0][0] == ["web-browser", "https://example.test/paper"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows command-line API")
def test_windows_command_preserves_paths_and_escaped_arguments() -> None:
    arguments = [
        r"C:\Program Files\PDF Viewer\reader.exe",
        "--folder=C:\\Papers with spaces\\",
        'a "quoted" value',
    ]
    assert osint._split_application(subprocess.list2cmdline(arguments)) == arguments


def test_frozen_external_launch_restores_environment_on_failure(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("LD_LIBRARY_PATH", str(tmp_path))
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/system-libraries")
    monkeypatch.setenv("QT_PLUGIN_PATH", str(tmp_path / "qt"))
    observed = {}

    def fail_launch(*args, **kwargs) -> None:
        observed.update(os.environ)
        raise OSError("test launch error")

    monkeypatch.setattr(osint.subprocess, "Popen", fail_launch)
    assert not open_url("https://example.test", "browser")
    assert observed["LD_LIBRARY_PATH"] == "/system-libraries"
    assert "QT_PLUGIN_PATH" not in observed
    assert os.environ["LD_LIBRARY_PATH"] == str(tmp_path)
    assert os.environ["QT_PLUGIN_PATH"] == str(tmp_path / "qt")


def test_frozen_environment_preserves_unrelated_updates(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", str(tmp_path))
    monkeypatch.setenv("ADSFOLIO_TEST_ENV", "before")
    with osint._external_process_environment():
        assert "LD_LIBRARY_PATH" not in os.environ
        os.environ["ADSFOLIO_TEST_ENV"] = "after"
    assert os.environ["ADSFOLIO_TEST_ENV"] == "after"
    assert os.environ["LD_LIBRARY_PATH"] == str(tmp_path)
