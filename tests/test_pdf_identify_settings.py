import pytest

from adsfolio.pdf.doi_providers import DoiProvider
from adsfolio.pdf.identify_settings import (
    RECOMMENDED_IDENTIFY_STRATEGIES,
    IdentifySettings,
    IdentifyStrategy,
)


def test_identify_settings_preserves_ads_permission_and_explicit_false():
    saved = IdentifySettings.from_mapping({"allow_ads": True})
    assert saved.allow_ads
    assert IdentifySettings.from_mapping(saved.to_mapping()) == saved
    assert not saved.with_overrides(allow_ads=False).allow_ads
    assert not IdentifySettings.from_mapping(
        {"allow_ads": False}, fallback=saved
    ).allow_ads
    assert IdentifySettings.from_mapping({"allow_ads": "yes"}, fallback=saved).allow_ads
    assert not IdentifySettings.from_mapping({}).allow_ads


def test_identify_settings_normalizes_behavior_and_invalid_fields():
    fallback = IdentifySettings(recursive=True, output_unresolved="pending")

    settings = IdentifySettings.from_mapping(
        {
            "orcid": "legacy identity is ignored",
            "recursive": "yes",
            "skip_unresolved_directory": True,
            "verbose": True,
        },
        fallback=fallback,
    )

    assert settings == IdentifySettings(
        recursive=True,
        output_unresolved="pending",
        skip_unresolved_directory=True,
        verbose=True,
    )
    assert "orcid" not in settings.to_mapping()


def test_identify_settings_explicit_overrides_can_clear_values():
    settings = IdentifySettings(
        output_unresolved="pending",
        skip_unresolved_directory=True,
    )

    changed = settings.with_overrides(
        output_unresolved=None,
        recursive=True,
        skip_unresolved_directory=False,
    )

    assert changed.output_unresolved is None
    assert changed.recursive is True
    assert changed.skip_unresolved_directory is False


def test_identify_settings_ignores_malformed_skip_value():
    settings = IdentifySettings.from_mapping(
        {"skip_unresolved_directory": "yes"},
        fallback=IdentifySettings(skip_unresolved_directory=True),
    )

    assert settings.skip_unresolved_directory is True


def test_identify_settings_roundtrips_ordered_strategies():
    settings = IdentifySettings.from_mapping(
        {"strategies": ["first_page_doi", "filename_bibcode"]}
    )
    assert settings.strategies == (
        IdentifyStrategy.FIRST_PAGE_DOI,
        IdentifyStrategy.FILENAME_BIBCODE,
    )
    assert settings.to_mapping()["strategies"] == [
        "first_page_doi",
        "filename_bibcode",
    ]


def test_legacy_infer_true_migrates_to_recommended_policy():
    settings = IdentifySettings.from_mapping(
        {"infer_from_filenames": True},
        fallback=IdentifySettings(strategies=(IdentifyStrategy.FILENAME_DOI,)),
    )
    assert settings.strategies == RECOMMENDED_IDENTIFY_STRATEGIES
    assert "infer_from_filenames" not in settings.to_mapping()


def test_identify_settings_rejects_duplicate_explicit_strategies():
    with pytest.raises(ValueError, match="duplicate identification strategy"):
        IdentifySettings(
            strategies=(
                IdentifyStrategy.FILENAME_DOI,
                IdentifyStrategy.FILENAME_DOI,
            )
        )


def test_identify_settings_rejects_empty_explicit_strategies():
    with pytest.raises(ValueError, match="At least one identification strategy"):
        IdentifySettings(strategies=())


def test_identify_settings_rejects_unknown_explicit_strategy():
    with pytest.raises(ValueError, match="Unknown identification strategy"):
        IdentifySettings(strategies=("not_a_strategy",))


def test_identify_settings_normalizes_persisted_strategies_with_warnings_and_fallback():
    settings = IdentifySettings.from_mapping(
        {
            "strategies": [
                "first_page_doi",
                "invalid_strategy",
                "first_page_doi",
                "filename_bibcode",
            ]
        }
    )
    assert settings.strategies == (
        IdentifyStrategy.FIRST_PAGE_DOI,
        IdentifyStrategy.FILENAME_BIBCODE,
    )

    recovered_settings = IdentifySettings.from_mapping(
        {"strategies": ["completely_invalid"]},
        fallback=IdentifySettings(strategies=(IdentifyStrategy.PDF_METADATA_DOI,)),
    )
    assert recovered_settings.strategies == RECOMMENDED_IDENTIFY_STRATEGIES


def test_identify_settings_with_overrides_strategies():
    settings = IdentifySettings()
    updated = settings.with_overrides(strategies=(IdentifyStrategy.FILENAME_BIBCODE,))
    assert updated.strategies == (IdentifyStrategy.FILENAME_BIBCODE,)


def test_identify_settings_roundtrips_providers():
    settings = IdentifySettings.from_mapping(
        {"providers": ["datacite", "crossref", "ads"]}
    )
    assert settings.providers == (
        DoiProvider.DATACITE,
        DoiProvider.CROSSREF,
        DoiProvider.ADS,
    )
    assert settings.to_mapping()["providers"] == ["datacite", "crossref", "ads"]


def test_identify_settings_rejects_duplicate_providers():
    with pytest.raises(ValueError, match="duplicate metadata provider"):
        IdentifySettings(providers=(DoiProvider.ADS, DoiProvider.ADS))


def test_identify_settings_rejects_empty_providers():
    with pytest.raises(ValueError, match="At least one metadata provider"):
        IdentifySettings(providers=())


def test_identify_settings_overrides_providers():
    settings = IdentifySettings()
    updated = settings.with_overrides(providers=(DoiProvider.CROSSREF,))
    assert updated.providers == (DoiProvider.CROSSREF,)
