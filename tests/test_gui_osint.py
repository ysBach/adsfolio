from pathlib import Path

from adsapi.gui.osint import open_path, open_url, reveal_command


def test_reveal_command_macos() -> None:
    assert reveal_command(Path("/tmp/paper.pdf"), "darwin") == [
        "open",
        "-R",
        "/tmp/paper.pdf",
    ]


def test_reveal_command_windows_uses_single_select_argument() -> None:
    command = reveal_command(Path(r"C:\Papers\paper.pdf"), "win32")
    assert command == ["explorer", r"/select,C:\Papers\paper.pdf"]
    assert len(command) == 2
    assert command[1].startswith("/select,")


def test_reveal_command_linux_uses_dbus() -> None:
    command = reveal_command(Path("/tmp/paper with spaces.pdf"), "linux")
    assert command is not None
    assert command[0] == "dbus-send"
    assert "org.freedesktop.FileManager1.ShowItems" in command
    assert "array:string:file:///tmp/paper%20with%20spaces.pdf" in command


def test_reveal_command_unknown_platform_returns_none() -> None:
    assert reveal_command(Path("paper.pdf"), "plan9") is None


def test_open_path_uses_configured_application(tmp_path, monkeypatch) -> None:
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF")
    calls = []
    monkeypatch.setattr(
        "adsapi.gui.osint.subprocess.Popen",
        lambda argv, **kwargs: calls.append((argv, kwargs)),
    )

    assert open_path(path, application="pdf-viewer")
    assert calls[0][0] == ["pdf-viewer", str(path)]


def test_open_url_uses_configured_browser(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        "adsapi.gui.osint.subprocess.Popen",
        lambda argv, **kwargs: calls.append((argv, kwargs)),
    )

    assert open_url("https://example.test/paper", application="web-browser")
    assert calls[0][0] == ["web-browser", "https://example.test/paper"]
