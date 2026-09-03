from pathlib import Path

from click.testing import CliRunner

from adsapi.cli.commands import main


def test_set_command_interactive_saves_token_and_profile(monkeypatch, tmp_path):
    calls = {}

    def fake_save_token(token: str) -> Path:
        calls["token"] = token
        return tmp_path / "token"

    def fake_save_user_setup(
        orcid: str,
        query_names: list[str] | None = None,
        debut_year: int | None = None,
        retire_year: int | None = None,
        library_ids: list[str] | None = None,
    ) -> Path:
        calls["setup"] = {
            "orcid": orcid,
            "query_names": query_names,
            "debut_year": debut_year,
            "retire_year": retire_year,
            "library_ids": library_ids,
        }
        return tmp_path / "setup.json"

    monkeypatch.setattr("adsapi.cli.commands.Config.save_token", fake_save_token)
    monkeypatch.setattr(
        "adsapi.cli.commands.Config.save_user_setup",
        fake_save_user_setup,
    )
    monkeypatch.setattr(
        "adsapi.cli.commands._refresh_publication_cache",
        lambda profile, token: tmp_path / "publications.json",
    )
    monkeypatch.setattr("adsapi.cli.commands.Config.get_user_setup", lambda: {})
    monkeypatch.setattr("adsapi.cli.commands._load_existing_token", lambda: "")

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["set"],
        input="\n".join(
            [
                "tok-123",
                "0000-0002-1825-0097",
                "Bach, Y. P.; Bach, Yoonsoo",
                "LIB123;LIB456",
                "2010",
                "2030",
            ]
        )
        + "\n",
    )

    assert result.exit_code == 0
    assert calls["token"] == "tok-123"
    assert calls["setup"] == {
        "orcid": "0000-0002-1825-0097",
        "query_names": ["Bach, Y. P.", "Bach, Yoonsoo"],
        "debut_year": 2010,
        "retire_year": 2030,
        "library_ids": ["LIB123", "LIB456"],
    }
    assert "Identify profile saved to" in result.output
    assert "https://scixplorer.org/user/settings/token" in result.output


def test_set_command_can_reuse_existing_token_and_profile(monkeypatch, tmp_path):
    calls = {}

    def fake_save_token(token: str) -> Path:
        calls["token"] = token
        return tmp_path / "token"

    def fake_save_user_setup(
        orcid: str,
        query_names: list[str] | None = None,
        debut_year: int | None = None,
        retire_year: int | None = None,
        library_ids: list[str] | None = None,
    ) -> Path:
        calls["setup"] = {
            "orcid": orcid,
            "query_names": query_names,
            "debut_year": debut_year,
            "retire_year": retire_year,
            "library_ids": library_ids,
        }
        return tmp_path / "setup.json"

    monkeypatch.setattr("adsapi.cli.commands.Config.save_token", fake_save_token)
    monkeypatch.setattr(
        "adsapi.cli.commands.Config.save_user_setup",
        fake_save_user_setup,
    )
    monkeypatch.setattr(
        "adsapi.cli.commands._refresh_publication_cache",
        lambda profile, token: tmp_path / "publications.json",
    )
    monkeypatch.setattr(
        "adsapi.cli.commands.Config.get_user_setup",
        lambda: {
            "orcid": "0000-0002-1825-0097",
            "query_names": ["Bach, Y. P."],
            "debut_year": 2010,
            "retire_year": 2030,
            "library_ids": ["LIB123"],
        },
    )
    monkeypatch.setattr(
        "adsapi.cli.commands._load_existing_token",
        lambda: "tok-existing-abcdef",
    )

    runner = CliRunner()
    # Accept all existing values via default "yes" on each confirm prompt.
    result = runner.invoke(main, ["set"], input="\n\n\n\n\n\n")

    assert result.exit_code == 0
    assert calls["token"] == "tok-existing-abcdef"
    assert calls["setup"] == {
        "orcid": "0000-0002-1825-0097",
        "query_names": ["Bach, Y. P."],
        "debut_year": 2010,
        "retire_year": 2030,
        "library_ids": ["LIB123"],
    }
    assert "Found existing ADS API token" in result.output
    assert "Found existing ORCID" in result.output
    assert "Found existing query names" in result.output
    assert "Found existing library IDs" in result.output


def test_set_command_immediately_builds_and_reports_publication_cache(
    monkeypatch, tmp_path
):
    cache = tmp_path / "publications.json"
    calls = {}

    monkeypatch.setattr(
        "adsapi.cli.commands.Config.save_token",
        lambda token: tmp_path / "token",
    )
    monkeypatch.setattr(
        "adsapi.cli.commands.Config.save_user_setup",
        lambda **kwargs: tmp_path / "setup.json",
    )

    def fake_refresh(profile, token):
        calls["profile"] = profile
        calls["token"] = token
        return cache

    monkeypatch.setattr("adsapi.cli.commands._refresh_publication_cache", fake_refresh)

    result = CliRunner().invoke(
        main,
        [
            "set",
            "--token",
            "tok-123",
            "--orcid",
            "0000-0002-1825-0097",
            "--query-name",
            "Bach, Y. P.",
            "--debut-year",
            "2010",
            "--end-year",
            "2030",
            "--library-id",
            "LIB123",
        ],
    )

    assert result.exit_code == 0
    assert calls["token"] == "tok-123"
    assert calls["profile"].orcid == "0000-0002-1825-0097"
    assert f"Publication cache: {cache}" in result.output
    assert "adsapi cache clear" in result.output


def test_set_command_keeps_saved_setup_when_cache_refresh_fails(monkeypatch, tmp_path):
    saved: dict[str, str] = {}

    monkeypatch.setattr(
        "adsapi.cli.commands.Config.save_token",
        lambda token: saved.setdefault("token", token) and tmp_path / "token",
    )
    monkeypatch.setattr(
        "adsapi.cli.commands.Config.save_user_setup",
        lambda **kwargs: (
            saved.setdefault("orcid", kwargs["orcid"]) and tmp_path / "setup.json"
        ),
    )
    monkeypatch.setattr(
        "adsapi.cli.commands._refresh_publication_cache",
        lambda profile, token: (_ for _ in ()).throw(OSError("cache is read-only")),
    )

    result = CliRunner().invoke(
        main,
        [
            "set",
            "--token",
            "tok-123",
            "--orcid",
            "0000-0002-1825-0097",
            "--query-name",
            "Example, A.",
            "--debut-year",
            "2010",
            "--end-year",
            "2030",
            "--library-id",
            "LIB123",
        ],
    )

    assert result.exit_code == 0
    assert saved == {"token": "tok-123", "orcid": "0000-0002-1825-0097"}
    assert (
        "Warning: could not build publication cache: cache is read-only"
        in result.output
    )


def test_set_command_rejects_invalid_year_window():
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "set",
            "--token",
            "tok-123",
            "--orcid",
            "0000-0002-1825-0097",
            "--query-name",
            "Bach, Yoonsoo",
            "--debut-year",
            "2035",
            "--retire-year",
            "2030",
            "--library-id",
            "LIB123",
        ],
    )
    assert result.exit_code == 1
    assert "debut year cannot be greater than retire year" in result.output
