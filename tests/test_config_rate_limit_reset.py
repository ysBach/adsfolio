"""Check that an expired ADS quota report does not block future requests."""

import pytest

from adsfolio.config import Config


@pytest.mark.parametrize(
    ("remaining", "reset", "allowed"),
    [
        ("11", "200", True),
        ("10", "200", False),
        ("10", "100", True),
        ("10", "99", True),
        ("10", None, False),
        ("10", "invalid", False),
        (None, None, True),
    ],
)
def test_rate_limit_uses_ads_reset_time(monkeypatch, remaining, reset, allowed):
    monkeypatch.setattr("adsfolio.config.time.time", lambda: 100)
    monkeypatch.setattr(Config, "_rate_remaining", remaining)
    monkeypatch.setattr(Config, "_rate_reset", reset)

    assert Config.check_rate_limit() is allowed


def test_fresh_quota_response_controls_requests_after_reset(monkeypatch):
    monkeypatch.setattr("adsfolio.config.time.time", lambda: 100)
    monkeypatch.setattr(Config, "_rate_limit", "5000")
    monkeypatch.setattr(Config, "_rate_remaining", "0")
    monkeypatch.setattr(Config, "_rate_reset", "100")

    assert Config.check_rate_limit()
    Config.update_rate_limits(
        {
            "X-RateLimit-Limit": "5000",
            "X-RateLimit-Remaining": "9",
            "X-RateLimit-Reset": "200",
        }
    )
    assert not Config.check_rate_limit()
