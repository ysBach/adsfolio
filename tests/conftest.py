"""Keep application tests separate from the user's configuration and cache."""

from pathlib import Path

import platformdirs
import pytest


@pytest.fixture(autouse=True)
def isolated_application_directories(monkeypatch, tmp_path: Path) -> None:
    """Give every test its own platform configuration and cache directories."""
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
