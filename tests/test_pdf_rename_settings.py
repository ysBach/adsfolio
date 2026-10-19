import logging

import pytest

from adsfolio.pdf.rename_settings import RenameSettings
from adsfolio.pdf.renamer import DEFAULT_SCHEME


def test_rename_settings_defaults_and_roundtrip():
    settings = RenameSettings()
    assert settings.template == DEFAULT_SCHEME
    assert settings.author_format == "surname_initials"
    assert settings.max_authors == 3
    assert settings.authors_when_truncated == 1
    assert settings.char_policy == "keep"
    assert settings.author_separator == "+"
    assert settings.author_truncation_suffix == "+"
    assert RenameSettings.from_mapping(settings.to_mapping()) == settings


def test_rename_settings_normalizes_each_malformed_field_independently():
    fallback = RenameSettings(
        template="{bibcode}", max_authors=7, authors_when_truncated=3
    )
    loaded = RenameSettings.from_mapping(
        {
            "template": 3,
            "author_format": "invalid",
            "max_authors": True,
            "authors_when_truncated": True,
            "char_policy": "invalid",
            "author_separator": 1,
            "author_truncation_suffix": [],
        },
        fallback=fallback,
    )
    assert loaded == fallback


def test_rename_settings_accepts_ads_custom_and_valid_author_formats():
    custom = RenameSettings(author_format="ads_custom:%3.1m")
    assert RenameSettings.from_mapping(custom.to_mapping()) == custom

    surname = RenameSettings(author_format="surname", char_policy="ascii")
    assert RenameSettings.from_mapping(surname.to_mapping()) == surname

    last_comma = RenameSettings(author_format="last_comma_first")
    assert RenameSettings.from_mapping(last_comma.to_mapping()) == last_comma


def test_rename_settings_roundtrips_custom_author_join_settings():
    settings = RenameSettings(
        max_authors=2,
        authors_when_truncated=2,
        author_separator=";",
        author_truncation_suffix=" et al.",
    )

    assert RenameSettings.from_mapping(settings.to_mapping()) == settings


def test_rename_settings_normalizes_retained_count_to_changed_threshold(caplog):
    fallback = RenameSettings(max_authors=5, authors_when_truncated=3)

    with caplog.at_level(logging.WARNING):
        loaded = RenameSettings.from_mapping({"max_authors": 2}, fallback=fallback)

    assert loaded == RenameSettings(max_authors=2, authors_when_truncated=2)
    assert not any(
        "authors_when_truncated" in record.message for record in caplog.records
    )


@pytest.mark.parametrize(
    ("max_authors", "authors_when_truncated"),
    [(0, 1), (2, 0), (2, 3), (100, 1)],
)
def test_rename_settings_rejects_invalid_author_limits(
    max_authors, authors_when_truncated
):
    with pytest.raises(ValueError, match="authors"):
        RenameSettings(
            max_authors=max_authors,
            authors_when_truncated=authors_when_truncated,
        )


def test_rename_settings_rejects_out_of_range_max_authors_and_empty_template():
    fallback = RenameSettings(template="{bibcode}", max_authors=5)

    assert (
        RenameSettings.from_mapping({"max_authors": 0}, fallback=fallback).max_authors
        == 5
    )
    assert (
        RenameSettings.from_mapping({"max_authors": 100}, fallback=fallback).max_authors
        == 5
    )
    assert (
        RenameSettings.from_mapping({"max_authors": -1}, fallback=fallback).max_authors
        == 5
    )
    assert (
        RenameSettings.from_mapping({"template": "   "}, fallback=fallback).template
        == "{bibcode}"
    )


def test_rename_settings_from_mapping_none_or_non_mapping():
    fallback = RenameSettings(max_authors=12, authors_when_truncated=2)
    assert RenameSettings.from_mapping(None) == RenameSettings()
    assert RenameSettings.from_mapping(None, fallback=fallback) == fallback
    assert RenameSettings.from_mapping("invalid", fallback=fallback) == fallback
