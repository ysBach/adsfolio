from click.testing import CliRunner

from adsfolio import pubcache
from adsfolio.cli.commands import main


def test_cache_commands_refresh_status_and_clear(monkeypatch, tmp_path):
    path = tmp_path / "publications.json"
    monkeypatch.setattr(pubcache, "cache_path", lambda: path)
    monkeypatch.setattr("adsfolio.cli.commands.cache_path", lambda: path)
    monkeypatch.setattr(
        "adsfolio.cli.commands.Config.get_user_setup",
        lambda: {"orcid": "0000", "end_year": 2026},
    )
    monkeypatch.setattr("adsfolio.cli.commands._load_existing_token", lambda: "token")
    monkeypatch.setattr(
        "adsfolio.cli.commands.query_publications",
        lambda client, profile: [{"bibcode": "B1"}],
    )

    runner = CliRunner()
    refreshed = runner.invoke(main, ["cache", "refresh"])
    assert refreshed.exit_code == 0
    assert "Cached 1 publications" in refreshed.output

    status = runner.invoke(main, ["cache", "status"])
    assert status.exit_code == 0
    assert "Documents: 1" in status.output

    cleared = runner.invoke(main, ["cache", "clear"])
    assert cleared.exit_code == 0
    assert not path.exists()
