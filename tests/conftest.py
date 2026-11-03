"""Keep application tests separate from the user's configuration and cache."""

import sys
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

import platformdirs
import pytest
import requests


@pytest.fixture(scope="session", autouse=True)
def isolated_session_fallback_directories():
    """Keep late Qt teardown away from real user settings and caches."""
    with TemporaryDirectory(prefix="adsfolio-pytest-session-") as directory:
        base = Path(directory)
        platformdirs.user_config_dir = lambda appname, *args, **kwargs: str(
            base / "config" / appname
        )
        platformdirs.user_cache_dir = lambda appname, *args, **kwargs: str(
            base / "cache" / appname
        )

        def deny_network(*args, **kwargs):
            raise requests.ConnectionError("External network is disabled during tests")

        requests.Session.request = deny_network
        yield


@pytest.fixture(autouse=True)
def isolated_application_directories(monkeypatch, tmp_path: Path) -> Iterator[None]:
    """Give every test its own platform configuration and cache directories."""
    _drain_qt_events()
    monkeypatch.setattr(
        platformdirs,
        "user_config_dir",
        lambda appname, *args, **kwargs: str(tmp_path / "config" / appname),
    )
    monkeypatch.setattr(
        platformdirs,
        "user_cache_dir",
        lambda appname, *args, **kwargs: str(tmp_path / "cache" / appname),
    )
    yield
    _drain_qt_events()


def _drain_qt_events() -> None:
    """Complete queued Qt cleanup before a test changes configuration routing."""
    qt = sys.modules.get("PySide6.QtCore")
    if qt is None:
        return
    application = qt.QCoreApplication.instance()
    if application is not None:
        application.processEvents()
        qt.QCoreApplication.sendPostedEvents(None, qt.QEvent.Type.DeferredDelete)
