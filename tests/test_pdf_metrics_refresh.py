"""Tests for ADS citation-metric cache normalization."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter

from adsfolio.config import Config
from adsfolio.pdf.cli import (
    _ads_match_candidates,
    _citation_metrics_payload,
    _parse_ads_identifier,
    _refresh_bibcode_metadata,
    _refresh_file_metadata,
)
from adsfolio.pdf.files import sha256_file
from adsfolio.pdf.registry import PdfRegistry


def write_pdf(path: Path, doi: str) -> str:
    """Write a PDF whose own DocInfo names doi; return its digest."""
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_metadata({"/DOI": doi})
    with path.open("wb") as handle:
        writer.write(handle)
    return sha256_file(path)


@pytest.fixture(autouse=True)
def _use_test_catalog(monkeypatch, tmp_path):
    monkeypatch.setattr(
        Config,
        "get_config_dir",
        classmethod(lambda cls: tmp_path / "config"),
    )


def test_citation_metrics_payload_includes_non_refereed_difference() -> None:
    metrics = SimpleNamespace(
        citation_stats={"total number of citations": 19},
        citation_stats_refereed={"total number of citations": 12},
    )

    assert _citation_metrics_payload(metrics) == {
        "all_citations": 19,
        "refereed_citations": 12,
        "non_refereed_citations": 7,
    }


def test_citation_metrics_payload_preserves_unknown_counts() -> None:
    metrics = SimpleNamespace(citation_stats=None, citation_stats_refereed=None)

    assert _citation_metrics_payload(metrics) == {
        "all_citations": None,
        "refereed_citations": None,
        "non_refereed_citations": None,
    }


def test_ads_match_candidates_include_bibcode_first_author_and_title() -> None:
    """Ambiguous coordinate results retain the fields needed for a human choice."""
    candidates = _ads_match_candidates(
        [
            {
                "bibcode": "1965Natur.207.1381B",
                "author": ["Bastin, J. A.", "Other, A."],
                "title": ["Lunar Hot Spots"],
            }
        ]
    )

    assert candidates == [
        {
            "bibcode": "1965Natur.207.1381B",
            "first_author": "Bastin, J. A.",
            "title": "Lunar Hot Spots",
        }
    ]


def test_refresh_file_metadata_replaces_only_unlocked_ads_fields(
    tmp_path: Path,
) -> None:
    registry = PdfRegistry(tmp_path)
    digest = write_pdf(tmp_path / "paper.pdf", "10.1000/new")
    registry.upsert_file(
        root=tmp_path,
        relative_path="paper.pdf",
        current_path="paper.pdf",
        sha256=digest,
        size_bytes=1,
        mtime_ns=2,
        status="unresolved",
        metadata={
            "title": ["Manually corrected title"],
            "doi": ["10.0/old"],
            "_adsfolio_locked_fields": ["title"],
        },
    )

    assert _refresh_file_metadata(
        registry,
        tmp_path,
        "paper.pdf",
        {
            "bibcode": "2022StGal...5....4B",
            "title": ["ADS title"],
            "doi": ["10.1000/new"],
            "abstract": "ADS abstract",
        },
    )

    record = registry.find_file_by_relative_path(tmp_path, "paper.pdf")
    assert record is not None
    assert record["metadata"]["title"] == ["Manually corrected title"]
    assert record["metadata"]["doi"] == ["10.1000/new"]
    assert record["metadata"]["bibcode"] == "2022StGal...5....4B"
    assert record["status"] == "resolved"


def test_refresh_bibcode_metadata_updates_every_matching_file(tmp_path: Path) -> None:
    """A bulk ADS refresh repairs each file record for the same bibcode."""
    registry = PdfRegistry(tmp_path)
    for relative_path in ("first.pdf", "second.pdf"):
        digest = write_pdf(tmp_path / relative_path, "10.1000/icarus")
        registry.upsert_file(
            root=tmp_path,
            relative_path=relative_path,
            current_path=relative_path,
            sha256=digest,
            size_bytes=1,
            mtime_ns=2,
            status="resolved",
            metadata={
                "bibcode": "1996Icar..119..209L",
                "author": ["LupishkoDF"],
            },
        )

    refreshed = _refresh_bibcode_metadata(
        registry,
        tmp_path,
        "1996Icar..119..209L",
        {
            "bibcode": "1996Icar..119..209L",
            "doi": ["10.1000/icarus"],
            "author": ["Lupishko, D. F.", "Mohamed, R. A."],
        },
    )

    assert refreshed == 2
    for relative_path in ("first.pdf", "second.pdf"):
        record = registry.find_file_by_relative_path(tmp_path, relative_path)
        assert record is not None
        assert record["metadata"]["author"] == ["Lupishko, D. F.", "Mohamed, R. A."]


def test_parse_ads_identifier_accepts_doi_and_journal_coordinates() -> None:
    """Update identifiers support DOI and bibstem-volume-page forms."""
    assert _parse_ads_identifier("DOI: 10.1234/example") == (
        "doi",
        "10.1234/example",
    )
    assert _parse_ads_identifier("A&A-259-333") == (
        "query",
        '(bibstem:"A&A" OR pub:"A&A") volume:"259" page:"333"',
    )
    assert _parse_ads_identifier("1992A&A...259..333K") == (
        "bibcode",
        "1992A&A...259..333K",
    )


@pytest.mark.parametrize(
    "identifier, journal, volume, page",
    [
        ("Nature-359-42", "Nature", "359", "42"),
        (
            "The Astrophysical Journal-900-100",
            "The Astrophysical Journal",
            "900",
            "100",
        ),
        (
            "Astronomy & Astrophysics:654:A113",
            "Astronomy & Astrophysics",
            "654",
            "A113",
        ),
        (
            "Monthly Notices of the Royal Astronomical Society 469 443",
            "Monthly Notices of the Royal Astronomical Society",
            "469",
            "443",
        ),
        (
            "Astronomy and Astrophysics_654_A113",
            "Astronomy and Astrophysics",
            "654",
            "A113",
        ),
        (
            "Journal of Physics: Condensed Matter-36-1",
            "Journal of Physics: Condensed Matter",
            "36",
            "1",
        ),
        ("A&A-1992-L42", "A&A", "1992", "L42"),
    ],
)
def test_parse_ads_identifier_accepts_full_journal_coordinates(
    identifier: str, journal: str, volume: str, page: str
) -> None:
    """Coordinates accept full journal names, punctuation, and lettered pages."""
    assert _parse_ads_identifier(identifier) == (
        "query",
        f'(bibstem:"{journal}" OR pub:"{journal}") volume:"{volume}" page:"{page}"',
    )


@pytest.mark.parametrize(
    "identifier, author, year, journal",
    [
        ("fulle-1992-natur", "fulle", "1992", "natur"),
        ("Fulle-1992-Nature", "Fulle", "1992", "Nature"),
        ("Fulle 1992 Nature", "Fulle", "1992", "Nature"),
        ("Fulle_1992_Nature", "Fulle", "1992", "Nature"),
        (
            "Smith-2024-The Astrophysical Journal",
            "Smith",
            "2024",
            "The Astrophysical Journal",
        ),
        (
            "O'Connor-2024-Monthly Notices of the Royal Astronomical Society",
            "O'Connor",
            "2024",
            "Monthly Notices of the Royal Astronomical Society",
        ),
        (
            "García-López-2024-Astronomy & Astrophysics",
            "García-López",
            "2024",
            "Astronomy & Astrophysics",
        ),
        ("de la Cruz-1892-Nature", "de la Cruz", "1892", "Nature"),
    ],
)
def test_parse_ads_identifier_accepts_author_year_journal(
    identifier: str, author: str, year: str, journal: str
) -> None:
    """Author-year references retain the full author and journal text."""
    assert _parse_ads_identifier(identifier) == (
        "query",
        f'(bibstem:"{journal}" OR pub:"{journal}") author:"{author}" year:"{year}"',
    )


def test_parse_ads_identifier_escapes_author_and_journal_phrases() -> None:
    """User text cannot introduce additional ADS clauses inside a phrase."""
    assert _parse_ads_identifier('Smith" OR *:*-2024-Journal "A" \\ Physics') == (
        "query",
        r'(bibstem:"Journal \"A\" \\ Physics" OR pub:"Journal \"A\" \\ Physics") author:"Smith\" OR *:*" year:"2024"',
    )


def test_parse_ads_identifier_keeps_publisher_urls_out_of_reference_queries() -> None:
    """An unsupported publisher URL stays an identifier, not journal coordinates."""
    value = "https://publisher.example/1992/42"
    assert _parse_ads_identifier(value) == ("bibcode", value)
