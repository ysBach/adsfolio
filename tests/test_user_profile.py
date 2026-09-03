import pytest

from adsapi.user_profile import UserProfile


def test_user_profile_normalizes_canonical_and_legacy_fields():
    profile = UserProfile.from_mapping(
        {
            "orcid": " 0000-0002-1825-0097 ",
            "query_names": [" Bach, Y. P. ", "", "Bach, Y. P."],
            "debut_year": "2010",
            "retire_year": 2030,
            "library_ids": ["LIB1", "", "LIB1"],
        }
    )

    assert profile == UserProfile(
        orcid="0000-0002-1825-0097",
        author_names=("Bach, Y. P.",),
        first_publication_year=2010,
        last_publication_year=2030,
        ads_library_ids=("LIB1",),
    )
    assert profile.to_mapping() == {
        "orcid": "0000-0002-1825-0097",
        "author_names": ["Bach, Y. P."],
        "first_publication_year": 2010,
        "last_publication_year": 2030,
        "ads_library_ids": ["LIB1"],
    }


def test_user_profile_rejects_reversed_publication_window():
    with pytest.raises(ValueError, match="first publication year cannot be greater"):
        UserProfile(first_publication_year=2030, last_publication_year=2020)


@pytest.mark.parametrize("year", [True, "2020"])
def test_user_profile_rejects_non_integer_years(year):
    with pytest.raises(TypeError, match="must be an integer"):
        UserProfile(first_publication_year=year)


@pytest.mark.parametrize("year", [0, -1, 10000])
def test_user_profile_rejects_out_of_range_years(year):
    with pytest.raises(ValueError, match="between 1 and 9999"):
        UserProfile(last_publication_year=year)


def test_user_profile_loads_delimiter_separated_legacy_library_ids():
    profile = UserProfile.from_mapping({"library_ids": "LIB1; LIB2,LIB1"})

    assert profile.ads_library_ids == ("LIB1", "LIB2")


def test_user_profile_builds_prefetch_profile():
    profile = UserProfile(
        orcid="0000",
        author_names=("Example, A.",),
        first_publication_year=2010,
        last_publication_year=2030,
        ads_library_ids=("LIB1",),
    ).publication_profile()

    assert profile.orcid == "0000"
    assert profile.query_names == ["Example, A."]
    assert profile.debut_year == 2010
    assert profile.end_year == 2030
    assert profile.library_ids == ["LIB1"]
