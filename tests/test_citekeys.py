"""Citation keys for papers inside and outside ADS."""

from __future__ import annotations

import pytest

from adsfolio.citekeys import (
    KeyPolicy,
    KeyStyle,
    assign_keys,
    citation_key,
    sanitize_key,
    scholar_key,
)

ADS = {
    "bibcode": "2024DEMO....1....1R",
    "doi": ["10.1234/ads.1"],
    "title": ["Dust in comet atmospheres"],
    "author": ["Rivera, Ana", "Chen, Mei"],
    "year": "2024",
}
OTHER = {
    "bibcode": "",
    "doi": "10.5555/Book(2020)",
    "title": ["The Theory of Everything"],
    "author": ["Müller, Jürgen"],
    "year": "2020",
}


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        (
            {
                "author": ["Einstein, A."],
                "year": "1905",
                "title": ["On the Electrodynamics of Moving Bodies"],
            },
            "einstein1905electrodynamics",
        ),
        (OTHER, "muller2020theory"),
        (
            {"author": ["Jane Doe"], "year": "2021", "title": ["A <i>new</i> look"]},
            "doe2021new",
        ),
        (
            {"author": ["O'Brien, K."], "year": "2019-05", "title": ["X-ray flares"]},
            "obrien2019x",
        ),
        ({"author": [], "year": "2020", "title": ["No author"]}, ""),
        ({"author": ["Roe, A."], "year": "", "title": ["No year"]}, ""),
    ],
)
def test_scholar_style(metadata, expected):
    assert scholar_key(metadata) == expected


def test_default_policy_uses_bibcode_for_ads_and_doi_otherwise():
    policy = KeyPolicy()
    assert citation_key(ADS, policy) == "2024DEMO....1....1R"
    assert citation_key(OTHER, policy) == "10.5555/Book_2020"


@pytest.mark.parametrize("style", list(KeyStyle))
def test_each_style_for_ads_papers(style):
    policy = KeyPolicy(ads=style, template="{first_author_surname}{year}")
    expected = {
        KeyStyle.BIBCODE: "2024DEMO....1....1R",
        KeyStyle.DOI: "10.1234/ads.1",
        KeyStyle.TEMPLATE: "Rivera2024",
        KeyStyle.SCHOLAR: "rivera2024dust",
        KeyStyle.MANUAL: "",
    }[style]
    assert citation_key(ADS, policy) == expected


def test_own_key_always_wins_and_is_sanitized():
    policy = KeyPolicy(ads=KeyStyle.SCHOLAR, other=KeyStyle.MANUAL)
    assert citation_key(ADS, policy, own_key="Rivera 2024, dust") == "Rivera_2024_dust"
    assert citation_key(OTHER, policy, own_key="mykey") == "mykey"


def test_sanitize_keeps_doi_slashes_and_dots():
    assert sanitize_key(" 10.1000/a.b{c} ") == "10.1000/a.b_c"


def test_bibcode_style_is_not_allowed_for_other_papers():
    with pytest.raises(ValueError, match="bibcode"):
        KeyPolicy(other=KeyStyle.BIBCODE)


def test_invalid_template_is_rejected():
    with pytest.raises(ValueError, match="template"):
        KeyPolicy(other=KeyStyle.TEMPLATE, template="{unknown_field}")


def test_policy_round_trips_through_settings():
    policy = KeyPolicy(
        ads=KeyStyle.SCHOLAR, other=KeyStyle.TEMPLATE, template="{year}x"
    )
    assert KeyPolicy.from_mapping(policy.to_mapping()) == policy
    assert KeyPolicy.from_mapping(None) == KeyPolicy()


def test_assign_keys_reports_missing_and_shared_keys():
    twin = {**OTHER, "doi": ""}
    papers = [
        (1, ADS, ""),
        (2, OTHER, ""),
        (3, twin, ""),
        (4, {**ADS, "bibcode": "2024DEMO....2....2R"}, ""),
    ]
    scholar = assign_keys(
        papers, KeyPolicy(ads=KeyStyle.SCHOLAR, other=KeyStyle.SCHOLAR)
    )
    assert scholar.clashes == {"rivera2024dust": [1, 4], "muller2020theory": [2, 3]}
    assert not scholar.complete
    by_doi = assign_keys(papers, KeyPolicy())
    assert by_doi.missing == [3]
    assert by_doi.keys[2] == "10.5555/Book_2020"
    fixed = assign_keys([(1, ADS, "a"), (4, ADS, "b")], KeyPolicy(ads=KeyStyle.SCHOLAR))
    assert fixed.complete and fixed.keys == {1: "a", 4: "b"}


def test_policy_is_saved_in_setup_without_touching_other_sections(
    monkeypatch, tmp_path
):
    from adsfolio.config import Config
    from adsfolio.user_profile import UserProfile

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    Config.save_user_profile(UserProfile(orcid="0000-0002-1825-0097"))
    assert Config.load_key_policy() == KeyPolicy()
    policy = KeyPolicy(ads=KeyStyle.SCHOLAR, other=KeyStyle.TEMPLATE)
    Config.save_key_policy(policy)
    assert Config.load_key_policy() == policy
    assert Config.load_user_profile().orcid == "0000-0002-1825-0097"
    Config.save_user_profile(UserProfile(orcid="0000-0002-1825-0097"))
    assert Config.load_key_policy() == policy


def test_invalid_saved_policy_falls_back_to_defaults(monkeypatch, tmp_path):
    import json

    from adsfolio.config import Config

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    Config.get_setup_path().write_text(
        json.dumps({"citation_keys": {"other": "bibcode"}}), encoding="utf-8"
    )
    assert Config.load_key_policy() == KeyPolicy()
