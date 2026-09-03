"""Operating-system integration for opening and revealing library files."""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path


def _qt_open_path(path: Path) -> bool:
    """Open a local path through Qt's system integration."""
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
    except ImportError as exc:  # pragma: no cover - exercised without gui extra
        raise RuntimeError(
            "PySide6 is required for desktop file integration; "
            "install with `pip install 'adsapi[gui]'`."
        ) from exc
    return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))))


def _qt_open_url(url: str) -> bool:
    """Open an external URL through Qt's system integration."""
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
    except ImportError as exc:  # pragma: no cover - exercised without gui extra
        raise RuntimeError(
            "PySide6 is required for desktop file integration; "
            "install with `pip install 'adsapi[gui]'`."
        ) from exc
    return bool(QDesktopServices.openUrl(QUrl(str(url))))


def _launch_application(application: str, target: str) -> bool:
    """Launch a configured application command with one target argument."""
    try:
        command = shlex.split(application)
    except ValueError:
        return False
    if not command:
        return False
    if sys.platform == "darwin" and len(command) == 1 and command[0].endswith(".app"):
        command = ["open", "-a", command[0]]
    try:
        subprocess.Popen([*command, target], start_new_session=True)
    except OSError:
        return False
    return True


def open_path(path: Path, application: str | None = None) -> bool:
    """Open `path` with the operating system's default application.

    Parameters
    ----------
    path : `Path`
        File to open.

    Returns
    -------
    bool
        `False` when the file does not exist or Qt cannot open it.
    """
    path = Path(path)
    if not path.exists():
        return False
    if application and application.strip():
        return _launch_application(application, str(path))
    return _qt_open_path(path)


def open_url(url: str, application: str | None = None) -> bool:
    """Open an external URL with a configured or system-default browser."""
    if not str(url).strip():
        return False
    if application and application.strip():
        return _launch_application(application, str(url))
    return _qt_open_url(str(url))


def _local_file_url(path: Path) -> str:
    """Return a percent-encoded ``file:`` URL without requiring Qt."""
    absolute = path if path.is_absolute() else Path.cwd() / path
    # ``Path.as_uri`` provides the same URL shape as QUrl.fromLocalFile for
    # local paths while keeping reveal_command usable in headless test runs.
    return absolute.as_uri()


def reveal_command(path: Path, platform: str) -> list[str] | None:
    """Build the argv that reveals `path` in a platform file manager.

    Parameters
    ----------
    path : `Path`
        File to select in the file manager.
    platform : `str`
        A `sys.platform` value.  This argument is explicit so the pure builder
        can be tested on any host operating system.

    Returns
    -------
    list[str] or `None`
        Platform-specific argv, or `None` when no strategy is known.
    """
    path = Path(path)
    if platform == "darwin":
        return ["open", "-R", str(path)]
    if platform.startswith("win"):
        # Explorer requires this as one argument and conventionally returns 1
        # even when selection succeeds.
        return ["explorer", f"/select,{path}"]
    if platform.startswith("linux"):
        return [
            "dbus-send",
            "--session",
            "--print-reply",
            "--dest=org.freedesktop.FileManager1",
            "/org/freedesktop/FileManager1",
            "org.freedesktop.FileManager1.ShowItems",
            f"array:string:{_local_file_url(path)}",
            "string:",
        ]
    return None


def _open_parent(path: Path) -> bool:
    """Open the parent directory as a reveal fallback."""
    parent = path.parent
    if not parent.exists():
        return False
    return _qt_open_path(parent)


def reveal_in_file_manager(path: Path) -> bool:
    """Reveal `path` in the platform file manager.

    Missing files are represented by revealing their existing parent directory.
    Linux uses the freedesktop D-Bus method first, then falls back to opening
    the parent directory when the utility is unavailable or fails.
    """
    path = Path(path)
    target = path if path.exists() else path.parent
    command = reveal_command(target, sys.platform)
    if command is None:
        return _open_parent(path)

    try:
        result = subprocess.run(command, check=False, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        result = None

    if result is not None:
        # Explorer's documented success path returns 1, so only its launch
        # failure (an OSError) is treated as failure above.
        if sys.platform.startswith("win") or result.returncode == 0:
            return True

    if sys.platform.startswith("linux"):
        return _open_parent(path)
    return False


__all__ = [
    "open_path",
    "open_url",
    "reveal_command",
    "reveal_in_file_manager",
]
