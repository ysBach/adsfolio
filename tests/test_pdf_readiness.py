"""Tests for metadata readiness helper."""

from __future__ import annotations

import pytest

from adsfolio.pdf.readiness import (
    EXTERNAL_METADATA_KEY,
    LOCKED_FIELDS_KEY,
    METADATA_SHA256_KEY,
    METADATA_STALE_KEY,
    REFERENCE_METADATA_KEY,
    metadata_ready,
)


def test_resolved_record_with_required_fields_is_ready() -> None:
    record = {
        "title": "A Great Paper",
        "author": ["Einstein, A."],
        "year": "1905",
        "status": "resolved",
    }
    assert metadata_ready(record) is True


def test_nested_metadata_is_supported() -> None:
    record = {
        "status": "resolved",
        "metadata": {
            "title": ["Gravitational Waves"],
            "author": "Abbott, B. P.",
            "year": 2016,
        },
    }
    assert metadata_ready(record) is True


def test_top_level_override_precedes_nested_metadata() -> None:
    record = {
        "status": "resolved",
        "title": "Top-Level Title",
        "author": "Top Author",
        "year": 2024,
        "metadata": {
            "title": "",
            "author": "",
            "year": None,
        },
    }
    assert metadata_ready(record) is True


@pytest.mark.parametrize(
    ("title", "author", "year"),
    [
        ("", "Author", 2020),
        ("   ", "Author", 2020),
        ([], "Author", 2020),
        (["   "], "Author", 2020),
        ("Title", "", 2020),
        ("Title", ["  "], 2020),
        ("Title", "Author", None),
        ("Title", "Author", ""),
        ("Title", "Author", "abcd"),
        ("Title", "Author", True),
        ("Title", "Author", False),
    ],
)
def test_missing_or_invalid_required_fields_reject(title, author, year) -> None:
    record = {
        "title": title,
        "author": author,
        "year": year,
        "status": "resolved",
    }
    assert metadata_ready(record) is False


def test_stale_marker_always_rejects_even_when_resolved_or_has_source() -> None:
    record = {
        "title": "A Great Paper",
        "author": "Einstein, A.",
        "year": 1905,
        "status": "resolved",
        METADATA_STALE_KEY: True,
        REFERENCE_METADATA_KEY: {"source": "manual"},
    }
    assert metadata_ready(record) is False


def test_stale_marker_in_nested_metadata_rejects() -> None:
    record = {
        "status": "resolved",
        "metadata": {
            "title": "A Great Paper",
            "author": "Einstein, A.",
            "year": 1905,
            METADATA_STALE_KEY: True,
        },
    }
    assert metadata_ready(record) is False


def test_sha256_mismatch_rejects() -> None:
    record = {
        "title": "A Great Paper",
        "author": "Einstein, A.",
        "year": 1905,
        "status": "resolved",
        "sha256": "current_digest",
        METADATA_SHA256_KEY: "different_digest",
    }
    assert metadata_ready(record) is False


def test_sha256_match_passes() -> None:
    record = {
        "title": "A Great Paper",
        "author": "Einstein, A.",
        "year": 1905,
        "status": "resolved",
        "sha256": "matching_digest",
        METADATA_SHA256_KEY: "matching_digest",
    }
    assert metadata_ready(record) is True


def test_unresolved_without_provenance_rejects() -> None:
    record = {
        "title": "Unverified Extraction",
        "author": "Unknown",
        "year": 2021,
        "status": "unresolved",
    }
    assert metadata_ready(record) is False


def test_unresolved_with_external_metadata_and_digest_match_is_ready() -> None:
    record = {
        "title": "Crossref DOI Paper",
        "author": "Smith, J.",
        "year": "2023",
        "status": "unresolved",
        "sha256": "digest123",
        METADATA_SHA256_KEY: "digest123",
        EXTERNAL_METADATA_KEY: {"provider": "crossref"},
    }
    assert metadata_ready(record) is True


def test_unresolved_with_external_metadata_but_no_digest_match_rejects() -> None:
    record = {
        "title": "Crossref DOI Paper",
        "author": "Smith, J.",
        "year": 2023,
        "status": "unresolved",
        "sha256": "digest123",
        METADATA_SHA256_KEY: "different_digest",
        EXTERNAL_METADATA_KEY: {"provider": "crossref"},
    }
    assert metadata_ready(record) is False


def test_unresolved_with_reference_proof_is_ready() -> None:
    record = {
        "title": "Manual Reference",
        "author": ["User, A."],
        "year": 2022,
        "status": "unresolved",
        REFERENCE_METADATA_KEY: {"bibcode": "custom2022"},
    }
    assert metadata_ready(record) is True


def test_unresolved_with_locked_fields_and_required_fields_is_ready() -> None:
    record = {
        "title": "Locked Title",
        "author": "Locked Author",
        "year": "2020",
        "status": "unresolved",
        LOCKED_FIELDS_KEY: ["title", "author"],
    }
    assert metadata_ready(record) is True


def test_unresolved_with_locked_fields_missing_year_rejects() -> None:
    record = {
        "title": "Locked Title",
        "author": "Locked Author",
        "year": None,
        "status": "unresolved",
        LOCKED_FIELDS_KEY: ["title", "author"],
    }
    assert metadata_ready(record) is False
