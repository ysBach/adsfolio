"""Check evidence-based groups without changing stored ADS status."""

from __future__ import annotations

import copy

import pytest

from adsfolio.pdf.classification import (
    ResolutionState,
    current_identity_proof,
    identification_group,
    resolution_state,
)
from adsfolio.pdf.identity import IDENTITY_KEY, target_identity
from adsfolio.pdf.readiness import EXTERNAL_METADATA_KEY, METADATA_SHA256_KEY
from adsfolio.pdf.registry import PdfRegistry


def external_record(status="verified", authority="provider_document"):
    metadata = {
        "doi": ["10.5555/example"],
        "title": ["Example article"],
        "author": ["Roe, A."],
        "year": "2024",
        EXTERNAL_METADATA_KEY: True,
        METADATA_SHA256_KEY: "current",
    }
    metadata[IDENTITY_KEY] = {
        "status": status,
        "authority": authority,
        "sha256": "current",
        "identity": target_identity(metadata).fingerprint,
    }
    return {"status": "unresolved", "sha256": "current", "metadata": metadata}


@pytest.mark.parametrize(
    "status,authority",
    [
        ("verified", "provider_document"),
        ("manual_confirmed", "manual"),
        ("manual_confirmed", "manual_conflict_override"),
    ],
)
def test_current_external_evidence_is_identified_without_ads(status, authority):
    record = external_record(status, authority)
    before = copy.deepcopy(record)
    assert resolution_state(record) is ResolutionState.IDENTIFIED_EXTERNAL
    assert identification_group(record) == "identified_without_ads"
    assert record == before
    assert record["status"] == "unresolved"


@pytest.mark.parametrize(
    "damage", ["hash", "identity", "missing_hash", "conflict", "absent"]
)
def test_stale_or_missing_identity_evidence_needs_identification(damage):
    record = external_record()
    if damage == "hash":
        record["sha256"] = "changed"
    elif damage == "identity":
        record["metadata"]["doi"] = ["10.5555/different"]
    elif damage == "missing_hash":
        record.pop("sha256")
    elif damage == "conflict":
        record["metadata"][IDENTITY_KEY]["status"] = "conflict"
    else:
        record["metadata"].pop(IDENTITY_KEY)
    assert current_identity_proof(record) is None
    assert identification_group(record) == "needs_identification"


def test_identity_does_not_depend_on_complete_rename_metadata():
    record = external_record()
    record["metadata"].pop("year")
    assert identification_group(record) == "identified_without_ads"


def test_changed_ads_link_needs_review_and_retains_its_bibcode():
    record = {
        "status": "resolved",
        "bibcode": "2024Example",
        "sha256": "changed",
        "metadata": {METADATA_SHA256_KEY: "previous"},
    }
    assert resolution_state(record) is ResolutionState.CHANGED
    assert identification_group(record) == "needs_identification"
    assert record["bibcode"] == "2024Example"


@pytest.mark.parametrize("bibcode", [None, "", [], [""], [None]])
def test_missing_bibcode_never_counts_as_an_ads_link(bibcode):
    assert (
        identification_group({"status": "resolved", "bibcode": bibcode}) != "ads_linked"
    )


def test_registry_filters_before_paging_and_preserves_catalog(tmp_path):
    root = tmp_path / "library"
    registry = PdfRegistry(root)
    try:
        for name in ("a.pdf", "c.pdf"):
            record = external_record()
            registry.upsert_file(
                root=root,
                relative_path=name,
                current_path=name,
                sha256="current",
                size_bytes=1,
                mtime_ns=1,
                status="unresolved",
                metadata=record["metadata"],
            )
        registry.upsert_file(
            root=root,
            relative_path="b.pdf",
            current_path="b.pdf",
            sha256="unknown",
            size_bytes=1,
            mtime_ns=1,
            status="unresolved",
            metadata={},
        )
        before = registry.query_files()
        selected = registry.query_files(
            identification="identified_without_ads", limit=1, offset=1
        )
        assert [row["current_path"] for row in selected] == ["c.pdf"]
        assert (
            registry.count_files_matching(identification="identified_without_ads") == 2
        )
        assert registry.count_files_matching(identification="needs_identification") == 1
        assert (
            registry.count_files_matching(
                identification="identified_without_ads", search="c.pdf"
            )
            == 1
        )
        assert registry.count_files_matching(resolved=False) == 3
        assert registry.query_files() == before
        with pytest.raises(ValueError, match="Unknown identification group"):
            registry.count_files_matching(identification="unknown")
    finally:
        registry.close()
