"""Keep test teardown away from a person's application settings."""

from pathlib import Path
from tempfile import gettempdir

import platformdirs


def test_session_fallback_remains_isolated_after_per_test_patches_are_undone(
    monkeypatch,
):
    monkeypatch.undo()

    config = Path(platformdirs.user_config_dir("adsfolio", appauthor=False))
    cache = Path(platformdirs.user_cache_dir("adsfolio", appauthor=False))
    temporary_root = Path(gettempdir()).resolve()
    assert config.resolve().is_relative_to(temporary_root)
    assert cache.resolve().is_relative_to(temporary_root)
    assert "adsfolio-pytest-session-" in str(config)
