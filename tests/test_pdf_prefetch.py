from pathlib import Path
from types import SimpleNamespace

from adsfolio.pdf.prefetch import (
    PublicationProfile,
    build_publication_index,
    estimated_query_budget,
    load_profile,
    match_pdf_from_prefetch,
    query_publications,
)
from adsfolio.user_profile import UserProfile


def test_load_profile_from_saved_setup(monkeypatch):
    monkeypatch.setattr(
        "adsfolio.pdf.prefetch.Config.load_user_profile",
        lambda: UserProfile(
            orcid="0000-0002-1825-0097",
            author_names=("Bach, Y. P.",),
            first_publication_year=2015,
            last_publication_year=2040,
            ads_library_ids=("LIB123",),
        ),
    )
    profile = load_profile()

    assert profile.orcid == "0000-0002-1825-0097"
    assert profile.query_names == ["Bach, Y. P."]
    assert profile.debut_year == 2015
    assert profile.retire_year == 2040
    assert profile.library_ids == ["LIB123"]
    assert profile.has_identity is True


def test_query_publications_uses_or_query_and_year_window():
    calls = []

    class _Search:
        def query_all(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                docs=[
                    {"bibcode": "A"},
                    {"bibcode": "B"},
                    {"bibcode": "A"},
                ]
            )

    client = SimpleNamespace(search=_Search())
    profile = PublicationProfile(
        orcid="0000-0002-1825-0097",
        query_names=["Bach, Y. P.", "Bach, Yoonsoo"],
        debut_year=2010,
        retire_year=2030,
    )

    docs = query_publications(client, profile)
    assert len(docs) == 2
    assert calls
    assert (
        calls[0]["q"] == '(orcid_pub:"0000-0002-1825-0097" OR author:"Bach, Y. P." OR '
        'author:"Bach, Yoonsoo") AND year:[2010 TO 2030]'
    )
    assert calls[0]["page_size"] == 2000
    assert calls[0]["max_records"] == 10000


def test_query_publications_escapes_quoted_identity_values():
    calls = []

    class _Search:
        def query_all(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(docs=[])

    query_publications(
        SimpleNamespace(search=_Search()),
        PublicationProfile(query_names=['Doe, A. "Quoted" \\ Name']),
    )

    assert calls[0]["q"] == 'author:"Doe, A. \\"Quoted\\" \\\\ Name"'


def test_query_publications_uses_library_bibcodes():
    bigquery_calls = []

    class _Search:
        def query_all(self, **kwargs):
            return SimpleNamespace(docs=[])

        def bigquery(self, bibcodes, fl=None, rows=None):
            bigquery_calls.append((list(bibcodes), list(fl or []), rows))
            return SimpleNamespace(
                docs=[
                    {"bibcode": "B1", "author": ["A, A."], "year": "2020"},
                    {"bibcode": "B2", "author": ["B, B."], "year": "2021"},
                ]
            )

    class _Libraries:
        def get_library(self, library_id, raw=False, start=0, rows=2000):
            assert library_id == "LIB123"
            assert raw is True
            return SimpleNamespace(documents=["B1", "B2"], num_documents=2)

    client = SimpleNamespace(search=_Search(), libraries=_Libraries())
    profile = PublicationProfile(library_ids=["LIB123"])
    docs = query_publications(client, profile)

    assert {doc["bibcode"] for doc in docs} == {"B1", "B2"}
    assert len(bigquery_calls) == 1
    assert bigquery_calls[0][0] == ["B1", "B2"]
    assert "reference" not in bigquery_calls[0][1]


def test_match_pdf_from_prefetch_uses_doi_first():
    documents = [
        {
            "bibcode": "2024A&A...684A..80B",
            "doi": ["10.1234/example"],
            "author": ["Bach, Y. P."],
            "year": "2024",
            "bibstem": ["A&A"],
            "volume": "684",
            "page": ["80"],
        }
    ]
    index = build_publication_index(documents)

    identifier = SimpleNamespace(
        extract_doi_candidates=lambda _: ("10.1234/example", None),
    )

    matched = match_pdf_from_prefetch(Path("sample.pdf"), identifier, index)
    assert matched is not None
    assert matched["bibcode"] == "2024A&A...684A..80B"


def test_match_pdf_from_prefetch_ignores_filename_hints_without_doi():
    documents = [
        {
            "bibcode": "2021A&A...654A.113B",
            "author": ["Bach, Y. P."],
            "year": "2021",
            "bibstem": ["A&A"],
            "volume": "654",
            "page": ["113"],
            "pub": "Astronomy and Astrophysics",
        }
    ]
    index = build_publication_index(documents)

    identifier = SimpleNamespace(
        extract_doi_candidates=lambda _: (None, None),
    )

    matched = match_pdf_from_prefetch(
        Path("Bach_2021_A&A_654_113.pdf"), identifier, index
    )
    assert matched is None


def test_match_pdf_from_prefetch_ignores_complex_filename_hints_without_doi():
    documents = [
        {
            "bibcode": "2014ApBu...69..211A",
            "author": ["Afanasiev, V. L.", "Rosenbush, V. K."],
            "year": "2014",
            "bibstem": ["ApBU"],
            "volume": "69",
            "page": ["211"],
            "pub": "Astrophysical Bulletin",
        }
    ]
    index = build_publication_index(documents)
    identifier = SimpleNamespace(
        extract_doi_candidates=lambda _: (None, None),
    )
    matched = match_pdf_from_prefetch(
        Path(
            "AfanasievVL+RosenbushVK+KiselevNN_2014_ApBU_69_211_"
            "Polarimetry_of_major_Uranian_moons.pdf"
        ),
        identifier,
        index,
    )
    assert matched is None


def test_match_pdf_from_prefetch_requires_unique_candidate():
    documents = [
        {
            "bibcode": "A",
            "doi": ["10.1234/example"],
            "author": ["Bach, Y. P."],
            "year": "2024",
            "bibstem": ["A&A"],
            "volume": "684",
            "page": ["80"],
        },
        {
            "bibcode": "B",
            "doi": ["10.1234/example"],
            "author": ["Bach, Y. P."],
            "year": "2024",
            "bibstem": ["A&A"],
            "volume": "684",
            "page": ["80"],
        },
    ]
    index = build_publication_index(documents)
    identifier = SimpleNamespace(
        extract_doi_candidates=lambda _: ("10.1234/example", None),
    )
    assert match_pdf_from_prefetch(Path("dup.pdf"), identifier, index) is None


def test_estimated_query_budget():
    assert estimated_query_budget(0) == 0
    assert estimated_query_budget(10) == 40
    assert estimated_query_budget(10, strategies_per_file=2) == 20
