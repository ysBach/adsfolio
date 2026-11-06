"""Stop tests from reading or writing the user's real ADSfolio data.

Old tests wrote 505 fake papers into the user's real catalog. This guard
records the real configuration and cache directories and the ADS token files
before the test configuration replaces ``platformdirs``. A Python audit hook
then rejects file access under those paths.

The hook sees Python file access and ``sqlite3.connect``. It does not see
child processes or file access from C++ code such as Qt. It does not see
``Path.exists()``, so it rejects a token file only when the file exists.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import platformdirs

# Application names: the current name and the name before the rename.
_APP_NAMES = ("adsfolio", "ads")

# Number of leading path arguments that each audit event reports.
_PATH_ARGUMENT_COUNTS = {
    "open": 1,
    "sqlite3.connect": 1,
    "os.listdir": 1,
    "os.scandir": 1,
    "os.mkdir": 1,
    "os.remove": 1,
    "os.rmdir": 1,
    "os.rename": 2,
    "os.replace": 2,
    "shutil.copyfile": 2,
    "shutil.copytree": 2,
    "shutil.rmtree": 1,
}

_protected_prefixes: tuple[str, ...] = ()
_installed = False
# Test ID that the hook records with each rejected access.
current_test = "<outside a test>"
# Rejected accesses as (test ID, event, path). Cleared when reported.
violations: list[tuple[str, str, str]] = []


class UserDataAccessError(RuntimeError):
    """A test accessed the user's real ADSfolio configuration or cache.

    This is not an `OSError`, so application error handling does not hide it.
    """


def protected_paths() -> list[Path]:
    """Return the real user paths that tests must not access.

    Call this function before tests replace the ``platformdirs`` functions.

    Returns
    -------
    list of `Path`
        The configuration and cache directories for each application name,
        and the ADS token locations outside them. These are the token paths
        of ``Config.get_token_paths()``.
    """
    paths = []
    for name in _APP_NAMES:
        paths.append(Path(platformdirs.user_config_dir(name, appauthor=False)))
        paths.append(Path(platformdirs.user_cache_dir(name, appauthor=False)))
    paths.append(Path.home() / ".config" / "ads")
    paths.append(Path.home() / ".ads")
    paths.append(Path.cwd() / ".ads-token")
    return paths


def _comparable(path: str) -> str:
    # Case-insensitive file systems (macOS, Windows) treat case variants as
    # one path. Casefolding on other systems only makes the guard stricter.
    return os.path.abspath(path).casefold()


def _path_text(value: object, event: str) -> str | None:
    if isinstance(value, int) or value is None:
        return None
    try:
        text = os.fsdecode(os.fspath(value))
    except TypeError:
        return None
    if event == "sqlite3.connect":
        if text.startswith("file:"):
            text = text[len("file:") :].split("?", 1)[0]
        if text in ("", ":memory:"):
            return None
    return text


def _audit(event: str, args: tuple) -> None:
    count = _PATH_ARGUMENT_COUNTS.get(event)
    if count is None:
        return
    for value in args[:count]:
        text = _path_text(value, event)
        if text is None:
            continue
        comparable = _comparable(text)
        for prefix in _protected_prefixes:
            if comparable == prefix or comparable.startswith(prefix + os.sep):
                violations.append((current_test, event, text))
                raise UserDataAccessError(
                    f"Test {current_test} tried {event} on real user data: {text}. "
                    "Use tmp_path or the isolated platformdirs directories."
                )


def install(session_directory: Path) -> None:
    """Protect the real user paths and redirect ``platformdirs``.

    Call this function once, when pytest imports the test configuration.
    Pytest imports test modules later, so module-level code cannot see real
    paths.

    Parameters
    ----------
    session_directory : `Path`
        Temporary directory that replaces the user directories for code that
        runs outside a test, for example during collection.
    """
    global _protected_prefixes, _installed
    if _installed:
        return
    prefixes = set()
    for path in protected_paths():
        prefixes.add(_comparable(str(path)))
        prefixes.add(_comparable(os.path.realpath(path)))
    _protected_prefixes = tuple(sorted(prefixes))
    platformdirs.user_config_dir = lambda appname, *args, **kwargs: str(
        session_directory / "config" / appname
    )
    platformdirs.user_cache_dir = lambda appname, *args, **kwargs: str(
        session_directory / "cache" / appname
    )
    sys.addaudithook(_audit)
    _installed = True


def take_violations() -> list[tuple[str, str, str]]:
    """Return the rejected accesses and clear the record.

    Returns
    -------
    list of `tuple`
        One ``(test ID, event, path)`` item for each rejected access.
    """
    found = list(violations)
    violations.clear()
    return found
