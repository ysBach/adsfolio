"""Tests for ADS citation-metric cache normalization."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from adsfolio.config import Config
from adsfolio.pdf.cli import (
    _ads_match_candidates,
    _citation_metrics_payload,
    _clear_invalid_bibcode,
    _parse_ads_identifier,
    _refresh_bibcode_metadata,
    _refresh_file_metadata,
)
from adsfolio.pdf.registry import PdfRegistry


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
    registry.upsert_file(
        root=tmp_path,
        relative_path="paper.pdf",
        current_path="paper.pdf",
        sha256="a" * 64,
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
            "doi": ["10.0/new"],
            "abstract": "ADS abstract",
        },
    )

    record = registry.find_file_by_relative_path(tmp_path, "paper.pdf")
    assert record is not None
    assert record["metadata"]["title"] == ["Manually corrected title"]
    assert record["metadata"]["doi"] == ["10.0/new"]
    assert record["metadata"]["bibcode"] == "2022StGal...5....4B"
    assert record["status"] == "resolved"


def test_refresh_bibcode_metadata_updates_every_matching_file(tmp_path: Path) -> None:
    """A bulk ADS refresh repairs each file record for the same bibcode."""
    registry = PdfRegistry(tmp_path)
    for relative_path in ("first.pdf", "second.pdf"):
        registry.upsert_file(
            root=tmp_path,
            relative_path=relative_path,
            current_path=relative_path,
            sha256=relative_path,
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
        'bibstem:"A&A" volume:"259" page:"333"',
    )
    assert _parse_ads_identifier("1992A&A...259..333K") == (
        "bibcode",
        "1992A&A...259..333K",
    )


def test_clear_invalid_bibcode_keeps_pdf_tracking_row(tmp_path: Path) -> None:
    """An invalid Bibcode is removed without deleting the local file record."""
    registry = PdfRegistry(tmp_path)
    registry.upsert_file(
        root=tmp_path,
        relative_path="paper.pdf",
        current_path="paper.pdf",
        sha256="a" * 64,
        size_bytes=1,
        mtime_ns=2,
        status="resolved",
        metadata={"bibcode": "1999.on.the.Flux.of", "title": ["Paper"]},
    )

    assert _clear_invalid_bibcode(registry, tmp_path, "1999.on.the.Flux.of") == 1
    record = registry.find_file_by_relative_path(tmp_path, "paper.pdf")
    assert record is not None
    assert record["metadata"]["bibcode"] == ""
    assert record["status"] == "unresolved"
