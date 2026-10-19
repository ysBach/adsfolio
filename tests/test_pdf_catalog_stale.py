"""Tests for catalog stale-safety and renamer non-ADS workflows."""

from __future__ import annotations

from pathlib import Path

import pytest

from adsfolio.config import Config
from adsfolio.pdf.catalog import PdfCatalog
from adsfolio.pdf.cli_common import is_resolved_record
from adsfolio.pdf.files import sha256_file
from adsfolio.pdf.readiness import (
    EXTERNAL_METADATA_KEY,
    METADATA_SHA256_KEY,
    METADATA_STALE_KEY,
    metadata_ready,
)
from adsfolio.pdf.registry import PdfRegistry, open_registry_state
from adsfolio.pdf.renamer import DEFAULT_SCHEME, PdfRenamer
from adsfolio.pdf.sidecar import Sidecar


@pytest.fixture(autouse=True)
def isolated_catalog(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))


def test_stale_replacement_unlinks_document_and_blocks_resolved_queries(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    catalog = PdfCatalog(catalog_path)
    root = tmp_path / "lib"
    root.mkdir()
    pdf_file = root / "paper.pdf"
    pdf_file.write_bytes(b"%PDF version 1")

    # Initial observation: resolved paper
    obs = catalog.observe_file(
        root,
        "paper.pdf",
        sha256=sha256_file(pdf_file),
        size_bytes=pdf_file.stat().st_size,
        mtime_ns=pdf_file.stat().st_mtime_ns,
        status="resolved",
        metadata={
            "bibcode": "2024ApJ...900..100S",
            "title": ["Original Title"],
            "author": ["Smith, J."],
            "year": "2024",
        },
    )
    rec = catalog.get_file(root, "paper.pdf")
    assert rec is not None
    assert rec["status"] == "resolved"
    assert rec["document_id"] is not None
    assert rec.get(METADATA_STALE_KEY) is not True
    assert is_resolved_record(rec) is True

    # Content is replaced on disk
    pdf_file.write_bytes(b"%PDF completely different paper content")
    new_sha = sha256_file(pdf_file)

    # Re-observe changed content without fresh identification
    obs2 = catalog.observe_file(
        root,
        "paper.pdf",
        sha256=new_sha,
        size_bytes=pdf_file.stat().st_size,
        mtime_ns=pdf_file.stat().st_mtime_ns,
        status="unresolved",
    )
    assert obs2.attachment_id == obs.attachment_id

    stale_rec = catalog.get_file(root, "paper.pdf")
    assert stale_rec is not None
    # Stale marker is True
    assert stale_rec[METADATA_STALE_KEY] is True
    assert stale_rec["status"] == "unresolved"
    assert stale_rec["document_id"] is None
    # Old metadata/history is NOT deleted
    assert stale_rec["metadata"]["bibcode"] == "2024ApJ...900..100S"
    assert stale_rec["metadata"]["title"] == ["Original Title"]

    # is_resolved_record rejects stale record
    assert is_resolved_record(stale_rec) is False

    # Registry find_files_by_bibcode excludes stale record
    reg = PdfRegistry(root)
    assert reg.find_files_by_bibcode(root, "2024ApJ...900..100S") == []

    catalog.close()
    reg.close()


def test_unchanged_control_retains_resolved_and_document_id(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    catalog = PdfCatalog(catalog_path)
    root = tmp_path / "lib"
    root.mkdir()
    pdf_file = root / "paper.pdf"
    pdf_file.write_bytes(b"%PDF stable")
    sha = sha256_file(pdf_file)

    catalog.observe_file(
        root,
        "paper.pdf",
        sha256=sha,
        size_bytes=pdf_file.stat().st_size,
        mtime_ns=pdf_file.stat().st_mtime_ns,
        status="resolved",
        metadata={
            "bibcode": "2024ApJ...900..100S",
            "title": ["Stable Paper"],
            "author": ["Author, A."],
            "year": "2024",
        },
    )

    # Re-observe unchanged content
    catalog.observe_file(
        root,
        "paper.pdf",
        sha256=sha,
        size_bytes=pdf_file.stat().st_size,
        mtime_ns=pdf_file.stat().st_mtime_ns,
        status=None,
    )

    rec = catalog.get_file(root, "paper.pdf")
    assert rec is not None
    assert rec["status"] == "resolved"
    assert rec["document_id"] is not None
    assert rec.get(METADATA_STALE_KEY) is not True
    assert is_resolved_record(rec) is True

    catalog.close()


def test_fresh_ads_identification_with_matching_digest_clears_stale(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    catalog = PdfCatalog(catalog_path)
    root = tmp_path / "lib"
    root.mkdir()
    pdf_file = root / "paper.pdf"
    pdf_file.write_bytes(b"%PDF replaced")
    sha = sha256_file(pdf_file)

    # Observe as stale
    catalog.observe_file(
        root,
        "paper.pdf",
        sha256=sha,
        size_bytes=pdf_file.stat().st_size,
        mtime_ns=pdf_file.stat().st_mtime_ns,
        status="unresolved",
        metadata={
            METADATA_STALE_KEY: True,
            "bibcode": "OLD_BIB",
            "title": ["Old Title"],
        },
    )
    rec = catalog.get_file(root, "paper.pdf")
    assert rec[METADATA_STALE_KEY] is True

    # Fresh identification with matching digest clears stale
    catalog.observe_file(
        root,
        "paper.pdf",
        sha256=sha,
        size_bytes=pdf_file.stat().st_size,
        mtime_ns=pdf_file.stat().st_mtime_ns,
        status="resolved",
        metadata={
            "bibcode": "2025NEW...123A",
            "title": ["New Title"],
            "author": ["New Author"],
            "year": "2025",
            METADATA_SHA256_KEY: sha,
        },
    )

    rec2 = catalog.get_file(root, "paper.pdf")
    assert rec2.get(METADATA_STALE_KEY) is not True
    assert rec2["status"] == "resolved"
    assert rec2["document_id"] is not None
    assert is_resolved_record(rec2) is True

    catalog.close()


def test_renamer_non_ads_metadata_ready_default_and_custom_scheme(
    tmp_path: Path,
) -> None:
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF content for non-ads")
    sha = sha256_file(source)

    sidecar = Sidecar(tmp_path)
    metadata = {
        "current_filename": "paper.pdf",
        "title": ["Non-ADS Title"],
        "author": ["Curie, Marie"],
        "year": "1903",
        "status": "unresolved",
        "sha256": sha,
        METADATA_SHA256_KEY: sha,
        EXTERNAL_METADATA_KEY: {"provider": "crossref", "doi": "10.1000/123"},
    }
    sidecar.upsert_file("paper.pdf", metadata)

    renamer = PdfRenamer()

    # Default scheme falls back to {authors}_{year}-{title} for non-bibcode
    plans = renamer.batch_rename(sidecar, template=DEFAULT_SCHEME, dry_run=True)
    assert len(plans) == 1
    old_name, new_name = plans[0]
    assert old_name == "paper.pdf"
    assert new_name == "CurieM_1903-Non-ADS_Title.pdf"

    # Custom scheme is respected
    custom_plans = renamer.batch_rename(
        sidecar, template="{year}_{title}", dry_run=True
    )
    assert len(custom_plans) == 1
    assert custom_plans[0][1] == "1903_Non-ADS_Title.pdf"


def test_renamer_hash_guard_prevents_renaming_mutated_content(tmp_path: Path) -> None:
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF original content")
    sha = sha256_file(source)

    sidecar = open_registry_state(tmp_path)
    metadata = {
        "current_filename": "paper.pdf",
        "bibcode": "2024ApJ...900..100S",
        "title": ["Paper Title"],
        "author": ["Author, A."],
        "year": "2024",
        "status": "resolved",
        "sha256": sha,
    }
    sidecar.upsert_file("paper.pdf", metadata)

    # Mutate the file on disk behind the registry's back
    source.write_bytes(b"%PDF secretly changed content")

    renamer = PdfRenamer()

    # rename_tracked_file must raise ValueError due to hash mismatch
    with pytest.raises(ValueError, match="File content hash mismatch"):
        renamer.rename_tracked_file(
            sidecar,
            source,
            metadata,
            dry_run=False,
        )

    # batch_rename skips the file with warning
    plans = renamer.batch_rename(sidecar, dry_run=True)
    assert plans == []

    sidecar.close()


def test_editing_notes_cannot_revalidate_replaced_content(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    source = root / "paper.pdf"
    source.write_bytes(b"%PDF original")
    catalog = PdfCatalog(tmp_path / "catalog.sqlite")
    original = {
        "bibcode": "2024ApJ...900..100S",
        "title": ["Original"],
        "author": ["Author, A."],
        "year": "2024",
    }
    for content, status, metadata in [
        (b"%PDF original", "resolved", original),
        (b"%PDF replacement", "unresolved", {}),
    ]:
        source.write_bytes(content)
        catalog.observe_file(
            root,
            source.name,
            sha256=sha256_file(source),
            size_bytes=source.stat().st_size,
            mtime_ns=source.stat().st_mtime_ns,
            status=status,
            metadata=metadata,
        )
    before = catalog.get_file(root, source.name)
    edited = {
        **before["metadata"],
        "notes": "Keep this note",
        METADATA_STALE_KEY: False,
    }
    catalog.replace_file_metadata(root, source.name, edited)
    after = catalog.get_file(root, source.name)
    assert after["id"] == before["id"]
    assert after["metadata"]["title"] == ["Original"]
    assert after["metadata"]["notes"] == "Keep this note"
    assert after["metadata"][METADATA_STALE_KEY]
    assert after["document_id"] is None
    assert not metadata_ready(after)
    catalog.close()
