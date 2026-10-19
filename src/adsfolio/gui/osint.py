"""Operating-system integration for opening and revealing library files."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def _split_application(application: str) -> list[str]:
    """Split a configured application command without invoking a shell."""
    if sys.platform.startswith("win"):
        import ctypes
        from ctypes import wintypes

        if not application.strip():
            return []
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        parse = shell32.CommandLineToArgvW
        parse.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
        parse.restype = ctypes.POINTER(wintypes.LPWSTR)
        kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
        kernel32.LocalFree.restype = wintypes.HLOCAL
        count = ctypes.c_int()
        arguments = parse(application.strip(), ctypes.byref(count))
        if not arguments:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return list(arguments[: count.value])
        finally:
            kernel32.LocalFree(ctypes.cast(arguments, wintypes.HLOCAL))
    return shlex.split(application)


def _without_frozen_paths(value: str | None, root: str | None) -> str | None:
    """Remove search-path entries rooted in a frozen application directory."""
    if not value or not root:
        return value
    try:
        root_path = Path(root).resolve()
    except (OSError, RuntimeError):
        return value

    kept: list[str] = []
    for entry in value.split(os.pathsep):
        if not entry:
            kept.append(entry)
            continue
        try:
            entry_path = Path(entry).resolve()
            if entry_path == root_path or root_path in entry_path.parents:
                continue
        except (OSError, RuntimeError):
            pass
        kept.append(entry)
    return os.pathsep.join(kept) if kept else None


def _set_windows_dll_directory(path: str | None) -> None:
    """Set the Windows DLL search directory when running a frozen app."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.SetDllDirectoryW.argtypes = [wintypes.LPCWSTR]
    kernel32.SetDllDirectoryW.restype = wintypes.BOOL
    if not kernel32.SetDllDirectoryW(path):
        raise ctypes.WinError(ctypes.get_last_error())


@contextmanager
def _external_process_environment() -> Iterator[None]:
    """Temporarily remove PyInstaller library-path changes for child apps."""
    if not getattr(sys, "frozen", False):
        yield
        return

    # Only restore keys we change; unrelated updates by workers must survive.
    keys = (
        "LD_LIBRARY_PATH",
        "DYLD_LIBRARY_PATH",
        "PATH",
        "QT_PLUGIN_PATH",
        "QML2_IMPORT_PATH",
    )
    saved_environment = {key: os.environ.get(key) for key in keys}
    meipass = getattr(sys, "_MEIPASS", None)
    try:
        for key in ("QT_PLUGIN_PATH", "QML2_IMPORT_PATH"):
            sanitized = _without_frozen_paths(os.environ.get(key), meipass)
            if sanitized is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = sanitized
        if sys.platform.startswith("linux"):
            original = os.environ.get("LD_LIBRARY_PATH_ORIG")
            if original is None:
                os.environ.pop("LD_LIBRARY_PATH", None)
            else:
                os.environ["LD_LIBRARY_PATH"] = original
        elif sys.platform == "darwin":
            sanitized = _without_frozen_paths(
                os.environ.get("DYLD_LIBRARY_PATH"), meipass
            )
            if sanitized is None:
                os.environ.pop("DYLD_LIBRARY_PATH", None)
            else:
                os.environ["DYLD_LIBRARY_PATH"] = sanitized
        elif sys.platform.startswith("win"):
            path = _without_frozen_paths(os.environ.get("PATH"), meipass)
            if path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = path
            if meipass:
                _set_windows_dll_directory(None)
        yield
    finally:
        for key, value in saved_environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if sys.platform.startswith("win") and meipass:
            _set_windows_dll_directory(str(meipass))


def _qt_open_path(path: Path) -> bool:
    """Open a local path through Qt's system integration."""
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
    except ImportError as exc:  # pragma: no cover - exercised without gui extra
        raise RuntimeError(
            "PySide6 is required for desktop file integration; "
            "install with `pip install 'adsfolio[gui]'`."
        ) from exc
    with _external_process_environment():
        return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))))


def _qt_open_url(url: str) -> bool:
    """Open an external URL through Qt's system integration."""
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
    except ImportError as exc:  # pragma: no cover - exercised without gui extra
        raise RuntimeError(
            "PySide6 is required for desktop file integration; "
            "install with `pip install 'adsfolio[gui]'`."
        ) from exc
    with _external_process_environment():
        return bool(QDesktopServices.openUrl(QUrl(str(url))))


def _launch_application(application: str, target: str) -> bool:
    """Launch a configured application command with one target argument."""
    try:
        command = _split_application(application)
    except (ValueError, OSError):
        return False
    if not command:
        return False
    if sys.platform == "darwin" and len(command) == 1 and command[0].endswith(".app"):
        command = ["open", "-a", command[0]]
    try:
        with _external_process_environment():
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
        with _external_process_environment():
            result = subprocess.run(command, check=False, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        result = None

    # Explorer may return 1 after successfully selecting the file.
    if result is not None and (
        sys.platform.startswith("win") or result.returncode == 0
    ):
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
