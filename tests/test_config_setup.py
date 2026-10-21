import json
import os
from pathlib import Path

import pytest

from adsfolio.config import Config
from adsfolio.pdf.identify_settings import IdentifySettings
from adsfolio.pdf.rename_settings import RenameSettings
from adsfolio.user_profile import UserProfile


def _use_tmp_config(monkeypatch, tmp_path):
    monkeypatch.setattr(
        Config,
        "get_config_dir",
        classmethod(lambda cls: tmp_path),
    )


def test_save_and_load_user_setup_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(
        Config,
        "get_config_dir",
        classmethod(lambda cls: tmp_path),
    )

    path = Config.save_user_setup(
        orcid="0000-0002-1825-0097",
        query_names=["Bach, Y. P.", "Bach, Yoonsoo"],
        debut_year=2010,
        retire_year=2030,
        library_ids=["LIB123", "LIB456"],
    )

    assert path.exists()
    assert path.name == Config.SETUP_FILENAME
    loaded = Config.get_user_setup()
    assert loaded == {
        "orcid": "0000-0002-1825-0097",
        "query_names": ["Bach, Y. P.", "Bach, Yoonsoo"],
        "debut_year": 2010,
        "end_year": 2030,
        "library_ids": ["LIB123", "LIB456"],
    }
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["schema_version"] == Config.SETUP_SCHEMA_VERSION
    assert raw["user_profile"]["author_names"] == [
        "Bach, Y. P.",
        "Bach, Yoonsoo",
    ]


def test_get_user_setup_returns_empty_dict_on_invalid_json(monkeypatch, tmp_path):
    monkeypatch.setattr(
        Config,
        "get_config_dir",
        classmethod(lambda cls: tmp_path),
    )
    setup_path = Config.get_setup_path()
    setup_path.parent.mkdir(parents=True, exist_ok=True)
    setup_path.write_text("{invalid", encoding="utf-8")

    assert Config.get_user_setup() == {}


def test_save_user_setup_trims_blank_names(monkeypatch, tmp_path):
    monkeypatch.setattr(
        Config,
        "get_config_dir",
        classmethod(lambda cls: tmp_path),
    )
    Config.save_user_setup(
        orcid=" id ",
        query_names=[" ", "Bach, Y. P.", "", "Bach, Yoonsoo"],
        library_ids=[" ", "LIB123", "", "LIB456"],
    )
    data = json.loads(Config.get_setup_path().read_text(encoding="utf-8"))
    assert data["user_profile"]["orcid"] == "id"
    assert data["user_profile"]["author_names"] == [
        "Bach, Y. P.",
        "Bach, Yoonsoo",
    ]
    assert data["user_profile"]["ads_library_ids"] == ["LIB123", "LIB456"]


def test_global_identify_settings_roundtrip_and_setup_preserves_options(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        Config,
        "get_config_dir",
        classmethod(lambda cls: tmp_path),
    )
    Config.save_identify_settings(
        IdentifySettings(
            recursive=True,
            output_unresolved="unresolved",
            skip_unresolved_directory=True,
            verbose=True,
        )
    )

    Config.save_user_setup(orcid="0001", debut_year=2010, end_year=2030)

    assert Config.load_user_profile() == UserProfile(
        orcid="0001",
        first_publication_year=2010,
        last_publication_year=2030,
    )
    assert Config.load_identify_settings() == IdentifySettings(
        recursive=True,
        output_unresolved="unresolved",
        skip_unresolved_directory=True,
        verbose=True,
    )


def test_profile_save_preserves_identify_behavior_and_unknown_keys(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        Config,
        "get_config_dir",
        classmethod(lambda cls: tmp_path),
    )
    Config.get_setup_path().write_text(
        json.dumps(
            {
                "future_key": {"enabled": True},
                "recursive": True,
                "output_unresolved": "pending",
                "orcid": "legacy",
            }
        ),
        encoding="utf-8",
    )

    Config.save_user_profile(UserProfile(orcid="new"))

    raw = json.loads(Config.get_setup_path().read_text(encoding="utf-8"))
    assert raw["future_key"] == {"enabled": True}
    assert raw["user_profile"]["orcid"] == "new"
    assert raw["identify"]["recursive"] is True
    assert raw["identify"]["output_unresolved"] == "pending"


def test_partial_nested_setup_falls_back_field_by_field_to_legacy_values(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        Config,
        "get_config_dir",
        classmethod(lambda cls: tmp_path),
    )
    Config.get_setup_path().write_text(
        json.dumps(
            {
                "orcid": "legacy-orcid",
                "query_names": ["Legacy, A."],
                "debut_year": 2001,
                "recursive": True,
                "output_unresolved": "legacy-unresolved",
                "user_profile": {"orcid": "canonical-orcid"},
                "identify": {"recursive": False},
            }
        ),
        encoding="utf-8",
    )

    assert Config.load_user_profile() == UserProfile(
        orcid="canonical-orcid",
        author_names=("Legacy, A.",),
        first_publication_year=2001,
    )
    assert Config.load_identify_settings() == IdentifySettings(
        recursive=False,
        output_unresolved="legacy-unresolved",
    )


def test_save_user_setup_replaces_atomically(monkeypatch, tmp_path):
    monkeypatch.setattr(
        Config,
        "get_config_dir",
        classmethod(lambda cls: tmp_path),
    )
    replace_calls = []
    original_replace = os.replace

    def tracking_replace(source, destination):
        replace_calls.append((Path(source), Path(destination)))
        original_replace(source, destination)

    monkeypatch.setattr("adsfolio.config.os.replace", tracking_replace)

    Config.save_user_setup(orcid="0000")

    assert replace_calls == [
        (
            Config.get_setup_path().with_name("setup.json.tmp"),
            Config.get_setup_path(),
        )
    ]
    assert not Config.get_setup_path().with_name("setup.json.tmp").exists()


def test_global_rename_settings_preserve_other_setup_sections(monkeypatch, tmp_path):
    _use_tmp_config(monkeypatch, tmp_path)
    Config.save_identify_settings(IdentifySettings(recursive=True))
    expected = RenameSettings(
        template="{bibcode}",
        char_policy="ascii",
        max_authors=4,
        authors_when_truncated=2,
    )
    Config.save_rename_settings(expected)
    assert Config.load_rename_settings() == expected
    assert Config.load_identify_settings().recursive
    raw = json.loads(Config.get_setup_path().read_text(encoding="utf-8"))
    assert raw["rename"]["authors_when_truncated"] == 2


def test_rename_save_preserves_unknown_keys_and_legacy_fallback(monkeypatch, tmp_path):
    _use_tmp_config(monkeypatch, tmp_path)
    Config.get_setup_path().write_text(
        json.dumps(
            {
                "custom_tool": {"setting": 42},
                "template": "{year}_{bibcode}",
                "max_authors": 5,
                "user_profile": {"orcid": "0000"},
            }
        ),
        encoding="utf-8",
    )
    assert Config.load_rename_settings() == RenameSettings(
        template="{year}_{bibcode}",
        max_authors=5,
    )
    Config.save_rename_settings(RenameSettings(template="{bibcode}"))
    raw = json.loads(Config.get_setup_path().read_text(encoding="utf-8"))
    assert raw["custom_tool"] == {"setting": 42}
    assert raw["user_profile"]["orcid"] == "0000"
    assert raw["rename"]["template"] == "{bibcode}"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_token_and_profile_are_private_from_creation(monkeypatch) -> None:
    previous = os.umask(0)
    try:
        config_dir = Config.get_config_dir()
        config_dir.mkdir(parents=True)
        config_dir.chmod(0o755)
        stale = config_dir / "token.tmp"
        stale.write_text("old")
        stale.chmod(0o644)
        modes: list[int] = []
        real_replace = os.replace

        def record(source, destination) -> None:
            modes.append(Path(source).stat().st_mode & 0o777)
            real_replace(source, destination)

        monkeypatch.setattr("adsfolio.config.os.replace", record)
        token_path = Config.save_token(" secret ")
        setup_path = Config.save_user_setup("0000-0002-1825-0097")
    finally:
        os.umask(previous)
    assert modes == [0o600, 0o600]
    assert token_path.read_text() == "secret"
    assert token_path.stat().st_mode & 0o777 == 0o600
    assert setup_path.stat().st_mode & 0o777 == 0o600
    assert config_dir.stat().st_mode & 0o777 == 0o700
    assert not stale.exists()
