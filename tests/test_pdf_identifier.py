import hashlib
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from adsfolio.api.search import SearchResult
from adsfolio.config import ADSError, RequestTooLongError
from adsfolio.pdf.identifier import PdfIdentifier
from adsfolio.pdf.identify import cached_ads_record
from adsfolio.pdf.identify_settings import IdentifyStrategy


@pytest.fixture(autouse=True)
def _disable_external_network_in_identifier_tests(monkeypatch):
    """Ensure no un-mocked external network calls occur during unit tests."""
    monkeypatch.setattr("adsfolio.pdf.identifier.fetch_crossref_doi", lambda doi: None)
    monkeypatch.setattr("adsfolio.pdf.identifier.fetch_datacite_doi", lambda doi: None)


class _SearchStub:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def query(self, q, fl=None, rows=10, start=0, sort=None, fq=None):
        self.calls.append({"q": q, "fl": fl, "rows": rows})
        return self.results.get(q, SearchResult(num_found=0, start=0, docs=[]))


class _FakePage:
    def __init__(self, text):
        self._text = text

    def extract_text(self):
        return self._text


class _FailingPage:
    def extract_text(self):
        raise AssertionError("text extraction should not run")


class _FakeReader:
    def __init__(self, metadata=None, text=None):
        self.metadata = metadata or {}
        self.pages = []
        if text is not None:
            self.pages = [_FakePage(text)]


def _build_identifier(results):
    search = _SearchStub(results)
    client = SimpleNamespace(search=search)
    return PdfIdentifier(client), search


def test_compute_sha256(tmp_path):
    path = tmp_path / "sample.pdf"
    path.write_bytes(b"%PDF-1.4\npayload\n")

    identifier, _ = _build_identifier({})
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    assert identifier.compute_sha256(path) == expected


def test_bibcode_resolution_is_not_constrained_by_publication_prefetch_window():
    """Direct ADS resolution is independent of the publication-prefetch window."""
    bibcode = "1965Natur.207.1381B"
    query = f'bibcode:"{bibcode}"'
    identifier, search = _build_identifier(
        {query: SearchResult(num_found=1, start=0, docs=[{"bibcode": bibcode}])}
    )
    identifier.debut_year = 2010

    documents = identifier.query_docs_for_bibcodes([bibcode])

    assert documents[bibcode] == {"bibcode": bibcode}
    assert search.calls[0]["q"] == query


def test_query_documents_retains_multiple_coordinate_candidates():
    """A coordinate lookup exposes every candidate instead of discarding ambiguity."""
    query = 'bibstem:"Natur" volume:"207" page:"1381"'
    candidates = [
        {
            "bibcode": "1965Natur.207.1381B",
            "author": ["Bastin, J. A."],
            "title": ["Lunar Hot Spots"],
        },
        {
            "bibcode": "1965Natur.207.1381X",
            "author": ["Example, A."],
            "title": ["A distinct record"],
        },
    ]
    identifier, _ = _build_identifier(
        {query: SearchResult(num_found=2, start=0, docs=candidates)}
    )

    result = identifier.query_documents(query)

    assert result.num_found == 2
    assert list(result.documents) == candidates


def test_extract_doi_from_metadata_uses_metadata_key(tmp_path, monkeypatch):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF")
    identifier, _ = _build_identifier({})
    monkeypatch.setattr(
        identifier,
        "_load_reader",
        lambda _: _FakeReader(metadata={"/doi": "10.1234/ABC.DEF"}),
    )

    assert identifier.extract_doi_from_metadata(path) == "10.1234/ABC.DEF"


def test_extract_doi_and_title_from_first_page_text(tmp_path, monkeypatch):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF")
    identifier, _ = _build_identifier({})
    page_text = (
        "A Physically Motivated Model of Dust\ndoi:10.9999/example.123\nAbstract\n"
    )
    monkeypatch.setattr(
        identifier,
        "_load_reader",
        lambda _: _FakeReader(metadata={}, text=page_text),
    )

    assert identifier.extract_doi_from_text(path) == "10.9999/example.123"
    assert (
        identifier.extract_title_from_text(path)
        == "A Physically Motivated Model of Dust"
    )


def test_extract_title_skips_publisher_banner_and_joins_title_lines(
    tmp_path, monkeypatch
):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF")
    identifier, _ = _build_identifier({})
    page_text = (
        "AAS-PROVIDED PDF • OPEN ACCESS\n\n"
        "Aperture-averaged Polarimetry of the Second Interstellar Comet\n"
        "3I/ATLAS within the Snow Line\n"
        "To cite this article: Maxim Zheltobryukhov et al 2025 "
        "Res. Notes AAS 9 338\n"
    )
    monkeypatch.setattr(
        identifier,
        "_load_reader",
        lambda _: _FakeReader(metadata={}, text=page_text),
    )

    assert identifier.extract_title_from_text(path) == (
        "Aperture-averaged Polarimetry of the Second Interstellar Comet "
        "3I/ATLAS within the Snow Line"
    )


def test_extract_doi_candidates_prefers_metadata_without_text(tmp_path, monkeypatch):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF")
    identifier, _ = _build_identifier({})
    monkeypatch.setattr(
        identifier,
        "_load_reader",
        lambda _: SimpleNamespace(
            metadata={"/Subject": "doi:10.1111/meta-doi"},
            pages=[_FailingPage()],
        ),
    )

    metadata_doi, text_doi = identifier.extract_doi_candidates(path)
    assert metadata_doi == "10.1111/meta-doi"
    assert text_doi is None


def test_extract_doi_candidates_falls_back_to_text(tmp_path, monkeypatch):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF")
    identifier, _ = _build_identifier({})
    monkeypatch.setattr(
        identifier,
        "_load_reader",
        lambda _: _FakeReader(
            metadata={"/Title": "No DOI"}, text="doi:10.2222/text-doi"
        ),
    )

    metadata_doi, text_doi = identifier.extract_doi_candidates(path)
    assert metadata_doi is None
    assert text_doi == "10.2222/text-doi"


def test_extract_bibcode_from_text():
    identifier, _ = _build_identifier({})
    text = "Classic reference: 1927PNAS...13..535W with early polarization work."
    assert identifier.extract_bibcode_from_text(text) == "1927PNAS...13..535W"


def test_extract_bibcode_from_pdf_searches_pages_after_the_first(tmp_path, monkeypatch):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF")
    identifier, _ = _build_identifier({})

    class Page:
        def __init__(self, text):
            self.text = text

        def extract_text(self):
            return self.text

    monkeypatch.setattr(
        "pypdf.PdfReader",
        lambda _: type(
            "Reader",
            (),
            {"metadata": {}, "pages": [Page("cover"), Page("2024ApJ...900..100S")]},
        )(),
    )

    assert identifier.extract_bibcode_from_pdf(path) == "2024ApJ...900..100S"


def test_extract_bibcode_from_first_page_ignores_reference_bibcodes(
    tmp_path, monkeypatch
):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF")
    identifier, _ = _build_identifier({})
    monkeypatch.setattr(
        identifier,
        "_load_reader",
        lambda _: SimpleNamespace(
            metadata={},
            pages=[
                _FakePage("Paper title without a bibcode"),
                _FakePage("Reference: 2024ApJ...900..100S"),
            ],
        ),
    )

    assert identifier.extract_bibcode_from_first_page(path) is None


def test_extract_bibcode_from_metadata(tmp_path, monkeypatch):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF")
    identifier, _ = _build_identifier({})
    monkeypatch.setattr(
        identifier,
        "_load_reader",
        lambda _: _FakeReader(
            metadata={
                "/Subject": "The Astrophysical Journal, 877(2019) 84. 2019ApJ...877...84S",
                "/Title": "Sample title",
            }
        ),
    )
    assert identifier.extract_bibcode_from_metadata(path) == "2019ApJ...877...84S"


def test_parse_filename_extracts_search_hints():
    identifier, _ = _build_identifier({})
    parsed = identifier.parse_filename("Smith_2024_ApJ_900_100.pdf", {"ApJ": {}})

    assert parsed == {
        "author": "Smith",
        "year": "2024",
        "journal": "ApJ",
        "volume": "900",
        "page": "100",
    }


def test_parse_filename_handles_multi_author_stem_with_title_suffix():
    identifier, _ = _build_identifier({})
    parsed = identifier.parse_filename(
        "AfanasievVL+RosenbushVK+KiselevNN_2014_ApBU_69_211_"
        "Polarimetry of major Uranian moons at the 6-m telescope.pdf"
    )
    assert parsed == {
        "author": "Afanasiev",
        "year": "2014",
        "journal": "ApBU",
        "volume": "69",
        "page": "211",
    }


def test_parse_filename_allows_letter_prefixed_page():
    identifier, _ = _build_identifier({})
    parsed = identifier.parse_filename("BachYP_2021_A&A_654_A113_Title.pdf")
    assert parsed == {
        "author": "Bach",
        "year": "2021",
        "journal": "A&A",
        "volume": "654",
        "page": "A113",
    }


def test_first_page_text_restores_pypdf_logger_level(tmp_path, monkeypatch):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF")
    identifier, _ = _build_identifier({})
    monkeypatch.setattr(
        identifier,
        "_load_reader",
        lambda _: _FakeReader(metadata={}, text="sample text"),
    )
    cmap_logger = logging.getLogger("pypdf._cmap")
    old_level = cmap_logger.level
    cmap_logger.setLevel(logging.WARNING)
    try:
        text = identifier._first_page_text(path)
        assert text == "sample text"
        assert cmap_logger.level == logging.WARNING
    finally:
        cmap_logger.setLevel(old_level)


def test_build_record_has_expected_fields(tmp_path):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF")
    identifier, _ = _build_identifier({})
    doc = {"bibcode": "BIB"}

    record = identifier.build_record(doc, path)

    assert record["bibcode"] == "BIB"


def test_resolve_uses_metadata_doi_before_title_fallback(tmp_path):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF-1.4\none\n")
    doc = {
        "bibcode": "2024ApJ...900..100S",
        "doi": ["10.1234/abc"],
        "title": ["Paper Title"],
        "author": ["Smith, J.", "Doe, A."],
        "year": "2024",
        "bibstem": ["ApJ"],
        "volume": "900",
        "page": ["100"],
        "pub": "The Astrophysical Journal",
    }
    query = 'doi:"10.1234/abc"'
    identifier, search = _build_identifier(
        {query: SearchResult(num_found=1, start=0, docs=[doc])}
    )

    identifier.extract_doi_from_metadata = Mock(return_value="10.1234/abc")
    identifier.extract_doi_from_text = Mock(return_value=None)
    identifier.extract_title_candidates = Mock(return_value=None)
    identifier.extract_title_from_metadata = Mock(return_value=None)

    record = identifier.resolve(path)
    assert record is not None
    assert record["bibcode"] == "2024ApJ...900..100S"
    assert search.calls[0]["q"] == query
    identifier.extract_doi_from_text.assert_not_called()


def test_resolve_validates_filename_bibcode_before_doi_and_title(tmp_path):
    bibcode = "2025RNAAS...9..338Z"
    path = tmp_path / f"{bibcode}.pdf"
    path.write_bytes(b"%PDF")
    query = f'bibcode:"{bibcode}"'
    document = {
        "bibcode": bibcode,
        "title": ["Aperture-averaged Polarimetry"],
        "author": ["Zheltobryukhov, Maxim"],
    }
    identifier, search = _build_identifier(
        {query: SearchResult(num_found=1, start=0, docs=[document])}
    )
    identifier.extract_bibcode_from_metadata = Mock(return_value=None)
    identifier.extract_bibcode_from_first_page = Mock(return_value=None)
    identifier.extract_doi_from_metadata = Mock(return_value=None)
    identifier.extract_doi_from_text = Mock(return_value=None)
    identifier.extract_title_candidates = Mock(return_value=None)
    identifier.extract_title_from_metadata = Mock(return_value=None)

    record = identifier.resolve(path)

    assert record is not None
    assert record["bibcode"] == bibcode
    assert search.calls == [{"q": query, "fl": identifier.SEARCH_FIELDS, "rows": 10}]
    identifier.extract_doi_from_metadata.assert_not_called()


def test_resolve_falls_through_when_filename_bibcode_is_not_validated(tmp_path):
    false_candidate = "2009.Error.analysis"
    path = tmp_path / f"{false_candidate}-paper.pdf"
    path.write_bytes(b"%PDF")
    title = "Reliable Paper Title"
    title_query = f'title:"{title}"'
    document = {"bibcode": "2009ApJ...700..100A", "title": [title]}
    identifier, search = _build_identifier(
        {title_query: SearchResult(num_found=1, start=0, docs=[document])}
    )
    identifier.extract_bibcode_from_metadata = Mock(return_value=None)
    identifier.extract_bibcode_from_first_page = Mock(return_value=None)
    identifier.extract_doi_from_metadata = Mock(return_value=None)
    identifier.extract_doi_from_text = Mock(return_value=None)
    identifier.extract_title_candidates = Mock(return_value=title)
    identifier.extract_title_from_metadata = Mock(return_value=None)

    record = identifier.resolve(path)

    assert record is not None
    assert record["bibcode"] == document["bibcode"]
    assert [call["q"] for call in search.calls] == [
        f'bibcode:"{false_candidate}"',
        title_query,
    ]


def test_resolve_zhelto_style_pdf_via_first_page_title(tmp_path, monkeypatch):
    path = tmp_path / "zhelto.pdf"
    path.write_bytes(b"%PDF")
    title = (
        "Aperture-averaged Polarimetry of the Second Interstellar Comet "
        "3I/ATLAS within the Snow Line"
    )
    document = {
        "bibcode": "2025RNAAS...9..338Z",
        "title": [title],
        "author": ["Zheltobryukhov, Maxim"],
    }
    identifier, search = _build_identifier(
        {
            f'title:"{title}"': SearchResult(
                num_found=1,
                start=0,
                docs=[document],
            )
        }
    )
    page_text = (
        "AAS-PROVIDED PDF • OPEN ACCESS\n\n"
        "Aperture-averaged Polarimetry of the Second Interstellar Comet\n"
        "3I/ATLAS within the Snow Line\n"
        "To cite this article: Maxim Zheltobryukhov et al 2025 "
        "Res. Notes AAS 9 338\n"
    )
    monkeypatch.setattr(
        identifier,
        "_load_reader",
        lambda _: _FakeReader(metadata={}, text=page_text),
    )

    search.results['bibcode:"2025RNAAS...9..338Z"'] = SearchResult(
        num_found=1, start=0, docs=[document]
    )
    record = identifier.resolve(path)

    assert record is not None
    assert record["bibcode"] == "2025RNAAS...9..338Z"
    assert [call["q"] for call in search.calls] == [
        f'title:"{title}"',
        f'title:"{title}"',
        'bibcode:"2025RNAAS...9..338Z"',
    ]


def test_resolve_many_extracts_and_validates_filename_bibcodes_by_default(
    tmp_path,
):
    bibcode = "2025RNAAS...9..338Z"
    path = tmp_path / f"{bibcode}.pdf"
    path.write_bytes(b"%PDF")
    document = {"bibcode": bibcode, "title": ["Paper Title"]}
    identifier, search = _build_identifier(
        {
            f'bibcode:"{bibcode}"': SearchResult(
                num_found=1,
                start=0,
                docs=[document],
            )
        }
    )
    identifier.extract_doi_from_metadata = Mock(return_value=None)
    identifier.extract_doi_from_text = Mock(return_value=None)

    records = identifier.resolve_many(
        [path], strategies=(IdentifyStrategy.FILENAME_BIBCODE,)
    )

    assert records[path] is not None
    assert records[path]["bibcode"] == bibcode
    assert [call["q"] for call in search.calls] == [f'bibcode:"{bibcode}"']


def test_resolve_handles_metadata_doi_direct_ads_lookup(tmp_path):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF-1.4\ndoi\n")
    query = 'doi:"10.1234/abc"'
    doc = {
        "bibcode": "2024ApJ...900..100S",
        "doi": ["10.1234/abc"],
        "title": ["Paper Title"],
    }
    identifier, search = _build_identifier(
        {query: SearchResult(num_found=1, start=0, docs=[doc])}
    )

    identifier.extract_doi_from_metadata = Mock(return_value="10.1234/abc")
    identifier.extract_doi_from_text = Mock(return_value=None)
    identifier.extract_title_candidates = Mock(return_value=None)
    identifier.extract_title_from_metadata = Mock(return_value=None)

    record = identifier.resolve(path)
    assert record is not None
    assert record["bibcode"] == "2024ApJ...900..100S"
    assert search.calls[0]["q"] == query
    identifier.extract_title_candidates.assert_not_called()


def test_resolve_uses_title_after_other_strategies(tmp_path):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF-1.4\ntext\n")
    query = 'title:"Interesting Result on Star Formation"'
    doc = {
        "bibcode": "2025A&A...111..222A",
        "title": ["Interesting Result on Star Formation"],
    }
    identifier, search = _build_identifier(
        {query: SearchResult(num_found=1, start=0, docs=[doc])}
    )

    identifier.extract_doi_from_metadata = Mock(return_value=None)
    identifier.extract_doi_from_text = Mock(return_value=None)
    identifier.parse_filename = Mock(return_value={})
    identifier.extract_title_candidates = Mock(
        return_value="Interesting Result on Star Formation"
    )
    identifier.extract_title_from_metadata = Mock(return_value=None)

    record = identifier.resolve(path)
    assert record is not None
    assert record["bibcode"] == "2025A&A...111..222A"
    assert search.calls[0]["q"] == query


def test_resolve_returns_none_for_ambiguous_match(tmp_path):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF")
    query = 'doi:"10.1234/ambiguous"'
    docs = [{"bibcode": "B1"}, {"bibcode": "B2"}]
    identifier, _ = _build_identifier(
        {query: SearchResult(num_found=2, start=0, docs=docs)}
    )

    identifier.extract_doi_from_metadata = Mock(return_value="10.1234/ambiguous")
    identifier.extract_doi_from_text = Mock(return_value=None)
    identifier.parse_filename = Mock(return_value={})
    identifier.extract_title_candidates = Mock(return_value=None)
    identifier.extract_title_from_metadata = Mock(return_value=None)
    observed: list[list[dict]] = []

    assert (
        identifier.resolve(
            path,
            ads_documents_callback=lambda documents: observed.append(documents),
        )
        is None
    )
    assert observed == [docs]


def test_resolve_deduplicates_same_normalized_ambiguous_doi(tmp_path):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF")
    query = 'doi:"10.1234/ambiguous"'
    docs = [
        {"bibcode": "B1", "doi": ["10.1234/ambiguous"]},
        {"bibcode": "B2", "doi": ["10.1234/ambiguous"]},
    ]
    identifier, search = _build_identifier(
        {query: SearchResult(num_found=2, start=0, docs=docs)}
    )
    identifier.extract_doi_from_metadata = Mock(return_value=" 10.1234/AMBIGUOUS ")
    identifier.extract_doi_from_text = Mock(return_value="10.1234/ambiguous")
    identifier.extract_title_candidates = Mock(return_value=None)
    identifier.extract_title_from_metadata = Mock(return_value=None)
    observed: list[list[dict]] = []

    record = identifier.resolve(
        path,
        ads_documents_callback=lambda documents: observed.append(documents),
    )

    assert record is None
    assert len(search.calls) == 1
    assert observed == [docs]


def test_resolve_treats_distinct_trailing_punctuation_as_distinct_complete_doi(
    tmp_path,
):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF")
    query1 = 'doi:"10.1234/ambiguous."'
    query2 = 'doi:"10.1234/ambiguous"'
    docs1 = [{"bibcode": "B1", "doi": ["10.1234/ambiguous."]}]
    docs2 = [{"bibcode": "B2", "doi": ["10.1234/ambiguous"]}]
    identifier, search = _build_identifier(
        {
            query1: SearchResult(num_found=1, start=0, docs=docs1),
            query2: SearchResult(num_found=1, start=0, docs=docs2),
        }
    )
    identifier.extract_doi_from_metadata = Mock(return_value="10.1234/ambiguous.")
    identifier.extract_doi_from_text = Mock(return_value="10.1234/ambiguous")
    identifier.extract_title_candidates = Mock(return_value=None)
    identifier.extract_title_from_metadata = Mock(return_value=None)

    record = identifier.resolve(path)

    assert record is not None
    assert record["bibcode"] == "B1"
    assert len(search.calls) == 1
    assert search.calls[0]["q"] == query1


def test_resolve_many_obeys_configured_strategy_order(tmp_path, monkeypatch):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF")
    identifier, search = _build_identifier(
        {
            'doi:"10.1234/from-file"': SearchResult(
                num_found=1,
                start=0,
                docs=[{"bibcode": "FILE", "doi": ["10.1234/from-file"]}],
            ),
            'doi:"10.1234/from-pdf"': SearchResult(
                num_found=1,
                start=0,
                docs=[{"bibcode": "PDF", "doi": ["10.1234/from-pdf"]}],
            ),
        }
    )
    monkeypatch.setattr(
        identifier, "extract_doi_from_filename", lambda _: "10.1234/from-file"
    )
    monkeypatch.setattr(
        identifier, "extract_doi_from_metadata", lambda _: "10.1234/from-pdf"
    )

    record = identifier.resolve_many(
        [path],
        strategies=(
            IdentifyStrategy.PDF_METADATA_DOI,
            IdentifyStrategy.FILENAME_DOI,
        ),
    )[path]

    assert record is not None
    assert record["bibcode"] == "PDF"
    assert record["resolution_strategy"] == "pdf_metadata_doi"
    assert len(search.calls) == 1


def test_missing_filename_candidate_falls_through_to_first_page_doi(
    tmp_path, monkeypatch
):
    path = tmp_path / "aa43983-22.pdf"
    path.write_bytes(b"%PDF")
    doi = "10.1051/0004-6361/202243983"
    query = f'doi:"{doi}"'
    identifier, search = _build_identifier(
        {
            query: SearchResult(
                num_found=1,
                start=0,
                docs=[{"bibcode": "2022A&A...668A.132B", "doi": [doi]}],
            )
        }
    )
    monkeypatch.setattr(identifier, "extract_doi_from_filename", lambda _: None)
    monkeypatch.setattr(identifier, "extract_doi_from_text", lambda _: doi)

    record = identifier.resolve_many(
        [path],
        strategies=(
            IdentifyStrategy.FILENAME_DOI,
            IdentifyStrategy.FIRST_PAGE_DOI,
        ),
    )[path]

    assert record is not None
    assert record["bibcode"] == "2022A&A...668A.132B"
    assert record["resolution_strategy"] == "first_page_doi"
    assert [call["q"] for call in search.calls] == [query]


def test_ambiguous_doi_falls_through_to_next_strategy(tmp_path, monkeypatch):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF")
    identifier, search = _build_identifier(
        {
            'doi:"10.1234/ambiguous"': SearchResult(
                num_found=2,
                start=0,
                docs=[
                    {"bibcode": "A", "doi": ["10.1234/ambiguous"]},
                    {"bibcode": "B", "doi": ["10.1234/ambiguous"]},
                ],
            ),
            'doi:"10.1234/good"': SearchResult(
                num_found=1,
                start=0,
                docs=[{"bibcode": "GOOD", "doi": ["10.1234/good"]}],
            ),
        }
    )
    monkeypatch.setattr(
        identifier, "extract_doi_from_filename", lambda _: "10.1234/ambiguous"
    )
    monkeypatch.setattr(identifier, "extract_doi_from_text", lambda _: "10.1234/good")

    record = identifier.resolve_many(
        [path],
        strategies=(
            IdentifyStrategy.FILENAME_DOI,
            IdentifyStrategy.FIRST_PAGE_DOI,
        ),
    )[path]

    assert record is not None
    assert record["bibcode"] == "GOOD"
    assert [call["q"] for call in search.calls] == [
        'doi:"10.1234/ambiguous"',
        'doi:"10.1234/good"',
    ]


def test_equal_candidate_is_queried_once_across_strategies(tmp_path, monkeypatch):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF")
    identifier, search = _build_identifier({})
    monkeypatch.setattr(
        identifier, "extract_doi_from_metadata", lambda _: "10.1234/same"
    )
    monkeypatch.setattr(identifier, "extract_doi_from_text", lambda _: "10.1234/same")

    record = identifier.resolve_many(
        [path],
        strategies=(
            IdentifyStrategy.PDF_METADATA_DOI,
            IdentifyStrategy.FIRST_PAGE_DOI,
        ),
    )[path]

    assert record is None
    assert [call["q"] for call in search.calls] == ['doi:"10.1234/same"']


def test_cached_lookup_answers_candidates_without_overriding_priority(
    tmp_path, monkeypatch
):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF")
    identifier, search = _build_identifier({})
    monkeypatch.setattr(
        identifier,
        "extract_bibcode_from_filename",
        lambda _: "2024ApJ...900..100A",
    )
    monkeypatch.setattr(
        identifier, "extract_doi_from_metadata", lambda _: "10.1234/later"
    )
    lookups = []

    def cached_lookup(kind, candidate):
        lookups.append((kind, candidate))
        if kind == "bibcode":
            return True, {"bibcode": "2024ApJ...900..100A"}
        return True, {"bibcode": "LATER", "doi": ["10.1234/later"]}

    record = identifier.resolve_many(
        [path],
        strategies=(
            IdentifyStrategy.FILENAME_BIBCODE,
            IdentifyStrategy.PDF_METADATA_DOI,
        ),
        cached_candidate_lookup=cached_lookup,
    )[path]

    assert record is not None
    assert record["bibcode"] == "2024ApJ...900..100A"
    assert record["resolution_strategy"] == "filename_bibcode"
    assert lookups == [("bibcode", "2024ApJ...900..100A")]
    assert search.calls == []


def test_cached_ads_record_prefers_bibcode_over_ambiguous_doi(tmp_path):
    path = tmp_path / "paper.pdf"
    preferred = {
        "bibcode": "2024ApJ...900..100B",
        "doi": ["10.1234/ambiguous"],
        "year": "2024",
    }
    registry = SimpleNamespace(
        find_ads_document_by_bibcode=Mock(return_value=preferred),
        find_ads_documents_by_doi=Mock(
            return_value=[
                {"bibcode": "2024ApJ...900..100A", "year": "2024"},
                preferred,
            ]
        ),
    )
    identifier, _ = _build_identifier({})
    identifier.build_record = Mock(return_value={"bibcode": preferred["bibcode"]})

    cache_hit, record = cached_ads_record(
        registry,
        identifier,
        path,
        doi="10.1234/ambiguous",
        bibcode=preferred["bibcode"],
        sha256="sha-paper",
    )

    assert cache_hit is True
    assert record == {"bibcode": preferred["bibcode"]}
    registry.find_ads_documents_by_doi.assert_not_called()


def test_cached_ads_record_keeps_doi_candidates_outside_prefetch_window(tmp_path):
    path = tmp_path / "paper.pdf"
    later_document = {
        "bibcode": "2024ApJ...900..100B",
        "doi": ["10.1234/ambiguous"],
        "year": "2024",
    }
    registry = SimpleNamespace(
        find_ads_document_by_bibcode=Mock(return_value=None),
        find_ads_documents_by_doi=Mock(
            return_value=[
                {"bibcode": "2014ApJ...800..100A", "year": "2014"},
                later_document,
            ]
        ),
    )
    identifier, _ = _build_identifier({})
    identifier.debut_year = 2020
    identifier.end_year = 2026
    identifier.build_record = Mock()

    cache_hit, record = cached_ads_record(
        registry,
        identifier,
        path,
        doi="10.1234/ambiguous",
        sha256="sha-paper",
    )

    assert cache_hit is True
    assert record is None
    identifier.build_record.assert_not_called()


def test_cached_ads_record_uses_bibcode_outside_prefetch_window(tmp_path):
    path = tmp_path / "paper.pdf"
    registry = SimpleNamespace(
        find_ads_document_by_bibcode=Mock(
            return_value={"bibcode": "2014ApJ...800..100A", "year": "2014"}
        ),
        find_ads_documents_by_doi=Mock(return_value=[]),
    )
    identifier, _ = _build_identifier({})
    identifier.debut_year = 2020
    identifier.end_year = 2026
    identifier.build_record = Mock(return_value={"bibcode": "2014ApJ...800..100A"})

    cache_hit, record = cached_ads_record(
        registry,
        identifier,
        path,
        bibcode="2014ApJ...800..100A",
    )

    assert cache_hit is True
    assert record == {"bibcode": "2014ApJ...800..100A"}


def test_resolve_many_batches_metadata_doi_queries(tmp_path):
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"
    first.write_bytes(b"%PDF-1.4\nfirst\n")
    second.write_bytes(b"%PDF-1.4\nsecond\n")

    query = 'doi:"10.1234/a" OR doi:"10.1234/b"'
    docs = [
        {
            "bibcode": "2024ApJ...900..100A",
            "doi": ["10.1234/a"],
            "title": ["Title A"],
            "author": ["Author, A"],
            "year": "2024",
            "bibstem": ["ApJ"],
            "volume": "900",
            "page": ["100"],
            "pub": "The Astrophysical Journal",
        },
        {
            "bibcode": "2024ApJ...900..101B",
            "doi": ["10.1234/b"],
            "title": ["Title B"],
            "author": ["Author, B"],
            "year": "2024",
            "bibstem": ["ApJ"],
            "volume": "900",
            "page": ["101"],
            "pub": "The Astrophysical Journal",
        },
    ]
    identifier, search = _build_identifier(
        {query: SearchResult(num_found=2, start=0, docs=docs)}
    )

    doi_map = {first: "10.1234/a", second: "10.1234/b"}
    identifier.extract_doi_from_metadata = Mock(side_effect=lambda path: doi_map[path])
    identifier.extract_doi_from_text = Mock(return_value=None)
    identifier.extract_title_candidates = Mock(return_value=None)
    identifier.extract_title_from_metadata = Mock(return_value=None)

    resolved = identifier.resolve_many([first, second])

    assert resolved[first] is not None
    assert resolved[first]["bibcode"] == "2024ApJ...900..100A"
    assert resolved[second] is not None
    assert resolved[second]["bibcode"] == "2024ApJ...900..101B"
    assert len(search.calls) == 1
    assert search.calls[0]["q"] == query
    identifier.extract_doi_from_text.assert_not_called()


def test_resolve_many_exposes_ambiguous_ads_documents_before_matching(
    tmp_path, monkeypatch
):
    path = tmp_path / "ambiguous.pdf"
    path.write_bytes(b"%PDF")
    query = 'doi:"10.1234/ambiguous"'
    docs = [
        {"bibcode": "2024ApJ...900..100A", "doi": ["10.1234/ambiguous"]},
        {"bibcode": "2024ApJ...900..100B", "doi": ["10.1234/ambiguous"]},
    ]
    identifier, _ = _build_identifier(
        {query: SearchResult(num_found=2, start=0, docs=docs)}
    )
    observed: list[list[dict]] = []
    monkeypatch.setattr(
        identifier,
        "extract_doi_from_metadata",
        lambda _: "10.1234/ambiguous",
    )

    resolved = identifier.resolve_many(
        [path],
        strategies=(IdentifyStrategy.PDF_METADATA_DOI,),
        ads_documents_callback=lambda documents: observed.append(documents),
    )

    assert observed == [docs]
    assert resolved[path] is None


def test_resolve_many_deduplicates_same_metadata_doi(tmp_path):
    first = tmp_path / "same-1.pdf"
    second = tmp_path / "same-2.pdf"
    first.write_bytes(b"%PDF-1.4\nfirst\n")
    second.write_bytes(b"%PDF-1.4\nsecond\n")

    shared_query = 'doi:"10.1234/shared"'
    doc = {
        "bibcode": "2025MNRAS.123..456C",
        "doi": ["10.1234/shared"],
        "title": ["Shared Result"],
        "author": ["Common, C"],
        "year": "2025",
        "bibstem": ["MNRAS"],
        "volume": "123",
        "page": ["456"],
        "pub": "Monthly Notices of the Royal Astronomical Society",
    }
    identifier, search = _build_identifier(
        {shared_query: SearchResult(num_found=1, start=0, docs=[doc])}
    )

    identifier.extract_doi_from_metadata = Mock(return_value="10.1234/shared")
    identifier.extract_doi_from_text = Mock(return_value=None)
    identifier.extract_title_candidates = Mock(return_value=None)
    identifier.extract_title_from_metadata = Mock(return_value=None)

    resolved = identifier.resolve_many([first, second])

    assert resolved[first] is not None
    assert resolved[second] is not None
    assert resolved[first]["bibcode"] == "2025MNRAS.123..456C"
    assert resolved[second]["bibcode"] == "2025MNRAS.123..456C"
    assert len(search.calls) == 1
    assert search.calls[0]["q"] == shared_query


def test_resolve_many_caches_duplicate_title_fallback_queries(tmp_path):
    first = tmp_path / "title-1.pdf"
    second = tmp_path / "title-2.pdf"
    first.write_bytes(b"%PDF-1.4\nfirst\n")
    second.write_bytes(b"%PDF-1.4\nsecond\n")

    query = 'title:"Shared Title"'
    doc = {
        "bibcode": "2026ApJ...777..888D",
        "title": ["Shared Title"],
        "author": ["Dup, D."],
        "year": "2026",
        "bibstem": ["ApJ"],
        "volume": "777",
        "page": ["888"],
        "pub": "The Astrophysical Journal",
    }
    identifier, search = _build_identifier(
        {query: SearchResult(num_found=1, start=0, docs=[doc])}
    )

    identifier.extract_doi_from_metadata = Mock(return_value=None)
    identifier.extract_doi_from_text = Mock(return_value=None)
    identifier.extract_title_candidates = Mock(return_value="Shared Title")
    identifier.extract_title_from_metadata = Mock(return_value=None)

    resolved = identifier.resolve_many([first, second])

    assert resolved[first] is not None
    assert resolved[second] is not None
    assert resolved[first]["bibcode"] == "2026ApJ...777..888D"
    assert resolved[second]["bibcode"] == "2026ApJ...777..888D"
    assert len(search.calls) == 1
    assert search.calls[0]["q"] == query


@pytest.mark.parametrize("fallback", [False, True])
def test_oversized_title_query_preserves_other_files_and_later_strategies(
    tmp_path, caplog, fallback: bool
) -> None:
    first = tmp_path / "first.pdf"
    duplicate = tmp_path / "duplicate.pdf"
    other = tmp_path / "other.pdf"
    for path in (first, duplicate, other):
        path.write_bytes(b"%PDF")
    rejected_title = "Extracted title and body text"
    good_title = "Valid title"
    rejected_query = f'title:"{rejected_title}"'
    good_query = f'title:"{good_title}"'
    document = {"bibcode": "B1", "title": [good_title]}
    identifier, search = _build_identifier({})

    def query(q: str, **kwargs: object) -> SearchResult:
        if q == rejected_query:
            raise RequestTooLongError("ADS rejected the oversized URL")
        assert q == good_query
        return SearchResult(num_found=1, start=0, docs=[document])

    search.query = Mock(side_effect=query)
    identifier.extract_title_from_metadata = Mock(
        side_effect=lambda path: good_title if path == other else rejected_title
    )
    identifier.extract_title_candidates = Mock(
        return_value=good_title if fallback else rejected_title
    )

    resolved = identifier.resolve_many(
        [first, duplicate, other],
        strategies=(
            IdentifyStrategy.PDF_METADATA_TITLE,
            IdentifyStrategy.FIRST_PAGE_TITLE,
        ),
    )

    assert resolved[other]["bibcode"] == "B1"
    for path in (first, duplicate):
        if fallback:
            assert resolved[path]["bibcode"] == "B1"
        else:
            assert resolved[path] is None
        assert str(path) in caplog.text
    assert "oversized title query" in caplog.text
    queries = [call.kwargs["q"] for call in search.query.call_args_list]
    assert queries.count(rejected_query) == 1
    assert queries.count(good_query) == 1


def test_title_lookup_preserves_unrelated_api_failures(tmp_path) -> None:
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF")
    identifier, search = _build_identifier({})
    identifier.extract_title_candidates = Mock(return_value="A title")
    search.query = Mock(side_effect=ADSError("ADS unavailable"))

    with pytest.raises(ADSError, match="ADS unavailable"):
        identifier.resolve(path, strategies=(IdentifyStrategy.FIRST_PAGE_TITLE,))


def test_resolve_many_skips_disabled_structured_filename_strategy(tmp_path):
    first = tmp_path / "BachYP_2024_A&A_684_80_title.pdf"
    second = tmp_path / "BachYP_2021_A&A_654_A113_title.pdf"
    first.write_bytes(b"%PDF-1.4\nfirst\n")
    second.write_bytes(b"%PDF-1.4\nsecond\n")

    query_first = 'title:"Title One"'
    query_second = 'title:"Title Two"'
    docs = {
        query_first: SearchResult(
            num_found=1,
            start=0,
            docs=[{"bibcode": "BIB1", "title": ["Title One"]}],
        ),
        query_second: SearchResult(
            num_found=1,
            start=0,
            docs=[{"bibcode": "BIB2", "title": ["Title Two"]}],
        ),
    }
    identifier, search = _build_identifier(docs)

    identifier.extract_doi_from_metadata = Mock(return_value=None)
    identifier.extract_doi_from_text = Mock(return_value=None)
    identifier.parse_filename = Mock(
        side_effect=AssertionError("normal mode should not parse filename hints")
    )
    title_map = {first: "Title One", second: "Title Two"}
    identifier.extract_title_candidates = Mock(side_effect=lambda path: title_map[path])
    identifier.extract_title_from_metadata = Mock(return_value=None)

    resolved = identifier.resolve_many(
        [first, second],
        strategies=(IdentifyStrategy.FIRST_PAGE_TITLE,),
    )

    assert resolved[first] is not None
    assert resolved[first]["bibcode"] == "BIB1"
    assert resolved[second] is not None
    assert resolved[second]["bibcode"] == "BIB2"
    assert len(search.calls) == 2
    assert {call["q"] for call in search.calls} == {query_first, query_second}


def test_resolve_many_structured_filename_only_skips_pdf_content_extraction(tmp_path):
    parseable = tmp_path / "BachYP_2024_A&A_684_80_title.pdf"
    unknown = tmp_path / "mystery.pdf"
    parseable.write_bytes(b"%PDF-1.4\none\n")
    unknown.write_bytes(b"%PDF-1.4\ntwo\n")

    clause = 'author:"Bach" AND year:2024 AND bibstem:"A&A" AND volume:684 AND page:80'
    query = f"({clause})"
    docs = [
        {
            "bibcode": "2024A&A...684A..80B",
            "doi": ["10.1234/one"],
            "title": ["Title One"],
            "author": ["Bach, Y. P."],
            "year": "2024",
            "bibstem": ["A&A"],
            "volume": "684",
            "page": ["80"],
            "pub": "Astronomy and Astrophysics",
        }
    ]
    identifier, search = _build_identifier(
        {query: SearchResult(num_found=1, start=0, docs=docs)}
    )

    identifier.extract_doi_from_metadata = Mock(
        side_effect=AssertionError("disabled metadata DOI strategy ran")
    )
    identifier.extract_doi_from_text = Mock(
        side_effect=AssertionError("disabled first-page DOI strategy ran")
    )
    identifier.extract_title_candidates = Mock(
        side_effect=AssertionError("disabled first-page title strategy ran")
    )
    identifier.extract_title_from_metadata = Mock(
        side_effect=AssertionError("disabled metadata title strategy ran")
    )

    resolved = identifier.resolve_many(
        [parseable, unknown],
        strategies=(IdentifyStrategy.STRUCTURED_FILENAME,),
    )

    assert resolved[parseable] is not None
    assert resolved[parseable]["bibcode"] == "2024A&A...684A..80B"
    assert resolved[unknown] is None
    assert len(search.calls) == 1
    assert search.calls[0]["q"] == query


@pytest.mark.parametrize(
    "input_doi",
    [
        "10.1000/ab(c)",
        "https://doi.org/10.1000/ab(c)",
        "http://dx.doi.org/10.1000/ab(c)",
        "doi:10.1000/ab(c)",
        "DOI: https://doi.org/10.1000/ab(c)",
        "https://doi.org/10.1000/ab%28c%29",
    ],
)
def test_query_docs_for_dois_matches_bare_and_url_with_closing_paren(input_doi):
    query = 'doi:"10.1000/ab(c)"'
    doc = {"bibcode": "2024Test...1..100X", "doi": ["10.1000/ab(c)"]}
    identifier, search = _build_identifier(
        {query: SearchResult(num_found=1, start=0, docs=[doc])}
    )

    resolved = identifier.query_docs_for_dois([input_doi])

    assert resolved == {"10.1000/ab(c)": doc}
    assert len(search.calls) == 1
    assert search.calls[0]["q"] == query


@pytest.mark.parametrize(
    "metadata_value",
    [
        "10.1000/ab(c)",
        "https://doi.org/10.1000/ab(c)",
        "doi:10.1000/ab(c)",
        "DOI: https://doi.org/10.1000/ab(c)",
    ],
)
def test_extract_doi_from_metadata_preserves_url_and_prefix_with_closing_paren(
    tmp_path, monkeypatch, metadata_value
):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF")
    identifier, _ = _build_identifier({})
    monkeypatch.setattr(
        identifier,
        "_load_reader",
        lambda _: _FakeReader(metadata={"/doi": metadata_value}),
    )

    assert identifier.extract_doi_from_metadata(path) == "10.1000/ab(c)"


class _TypographyPage:
    def __init__(self, runs):
        self.runs = runs

    def extract_text(self, visitor_text=None):
        for text, size, scale in self.runs:
            if visitor_text:
                visitor_text(
                    text, [scale, 0, 0, scale, 0, 0], [1, 0, 0, 1, 0, 0], {}, size
                )
        return "".join(text for text, _, _ in self.runs)


def test_typography_retains_title_below_larger_masthead(tmp_path, monkeypatch):
    identifier, _ = _build_identifier({})
    page = _TypographyPage(
        [
            ("Journal masthead\n", 20, 1),
            ("A title over\n", 8, 2),
            ("two lines\n", 16, 1),
            ("An Author\n", 12, 1),
            ("(Received May 25, 1934)\n", 10, 1),
            ("Body text. " * 100, 10, 1),
        ]
    )
    monkeypatch.setattr(
        identifier, "_load_reader", lambda _: SimpleNamespace(pages=[page])
    )
    candidates = identifier.extract_title_candidates(tmp_path / "paper.pdf")
    assert "A title over two lines" in candidates
    assert candidates[0] == "Journal masthead"
    assert all(
        "Body text" not in value and "Received" not in value for value in candidates
    )


@pytest.mark.parametrize(
    "documents,expected",
    [
        ([{"bibcode": "BIB1", "title": ["Actual title"]}], "BIB1"),
        (
            [
                {"bibcode": "BIB1", "title": ["Actual title"]},
                {"bibcode": "BIB2", "title": ["Journal heading"]},
            ],
            None,
        ),
        ([{"bibcode": "BIB1", "title": ["Actual title extended"]}], None),
    ],
)
def test_typography_candidates_require_one_exact_ads_identity(
    tmp_path, documents, expected
):
    query = 'title:"Journal heading" OR title:"Actual title"'
    identifier, search = _build_identifier(
        {query: SearchResult(num_found=len(documents), start=0, docs=documents)}
    )
    for document in documents:
        search.results[f'bibcode:"{document["bibcode"]}"'] = SearchResult(
            num_found=1, start=0, docs=[document]
        )
    if len(documents) == 2:
        search.results['bibcode:"BIB1" OR bibcode:"BIB2"'] = SearchResult(
            num_found=2, start=0, docs=documents
        )
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF")
    identifier.extract_title_candidates = Mock(
        return_value=("Journal heading", "Actual title")
    )
    result = identifier.resolve(path, strategies=(IdentifyStrategy.FIRST_PAGE_TITLE,))
    assert (result["bibcode"] if result else None) == expected
    assert search.calls[0]["q"] == query


def test_title_batch_splits_incomplete_results_before_accepting(tmp_path):
    doc = {"bibcode": "BIB1", "title": ["Actual title"]}
    identifier, _ = _build_identifier(
        {
            'title:"Journal" OR title:"Actual title"': SearchResult(
                num_found=3000, start=0, docs=[doc]
            ),
            'title:"Journal"': SearchResult(num_found=0, start=0, docs=[]),
            'title:"Actual title"': SearchResult(num_found=2, start=0, docs=[doc]),
        }
    )
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF")
    identifier.extract_title_candidates = Mock(return_value=("Journal", "Actual title"))
    assert (
        identifier.resolve(path, strategies=(IdentifyStrategy.FIRST_PAGE_TITLE,))
        is None
    )


def test_typography_ignores_form_aggregate_and_keeps_title_sentence(
    tmp_path, monkeypatch
):
    class Page:
        def extract_text(self, visitor_text=None):
            matrix = [1, 0, 0, 1, 0, 0]
            for text, size, font in [
                ("A proceedings title. A. Author\n", 12, {}),
                ("Body text. " * 100, 9, {}),
                ("Duplicated whole-page text " * 200, 20, None),
            ]:
                visitor_text(text, matrix, matrix, font, size)
            return ""

    identifier, _ = _build_identifier({})
    monkeypatch.setattr(
        identifier, "_load_reader", lambda _: SimpleNamespace(pages=[Page()])
    )
    candidates = identifier.extract_title_candidates(tmp_path / "paper.pdf")
    assert "A proceedings title." in candidates
    assert all("Duplicated" not in value for value in candidates)


def test_typography_keeps_whitespace_and_normalizes_ligatures(tmp_path, monkeypatch):
    page = _TypographyPage(
        [
            ("Dust", 16, 1),
            (" ", 16, 1),
            ("ﬂow", 16, 1),
            ("\n", 16, 1),
            ("on comets", 16, 1),
            ("Abstract\n", 10, 1),
            ("Body " * 100, 10, 1),
        ]
    )
    identifier, _ = _build_identifier({})
    monkeypatch.setattr(
        identifier, "_load_reader", lambda _: SimpleNamespace(pages=[page])
    )
    assert identifier.extract_title_candidates(tmp_path / "paper.pdf") == (
        "Dust flow on comets",
    )


def test_title_probe_caches_only_verified_full_record(tmp_path):
    doc = {"bibcode": "BIB1", "title": ["Actual title"], "author": ["Author, A."]}
    identifier, _ = _build_identifier(
        {
            'title:"Journal" OR title:"Actual title"': SearchResult(
                num_found=2,
                start=0,
                docs=[
                    {"bibcode": "BIB1", "title": ["Actual title"]},
                    {"bibcode": "OTHER", "title": ["An unrelated journal paper"]},
                ],
            ),
            'bibcode:"BIB1"': SearchResult(num_found=1, start=0, docs=[doc]),
        }
    )
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF")
    identifier.extract_title_candidates = Mock(return_value=("Journal", "Actual title"))
    callback = Mock()
    result = identifier.resolve(
        path,
        strategies=(IdentifyStrategy.FIRST_PAGE_TITLE,),
        ads_documents_callback=callback,
    )
    assert result["bibcode"] == "BIB1"
    callback.assert_called_once_with([doc])
