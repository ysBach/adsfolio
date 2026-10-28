"""Verified replacement registration keeps history of an absent PDF."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfWriter

from adsfolio.pdf.catalog import LOCKED_METADATA_FIELDS_KEY, PdfCatalog
from adsfolio.pdf.files import sha256_file
from adsfolio.pdf.identity import IDENTITY_KEY, assert_mutation_allowed

DOC = {
    "bibcode": "2026NatAs.tmp..145O",
    "doi": ["10.1038/example"],
    "title": ["Updated paper"],
    "author": ["Opitom, C."],
    "year": "2026",
}
OTHER = {
    "bibcode": "2025ApJ...900..100S",
    "doi": ["10.3847/other"],
    "title": ["Another paper"],
    "author": ["Smith, J."],
    "year": "2025",
}


def make_pdf(path: Path, doi: str, revision: str = "a") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=300)
    writer.add_metadata({"/DOI": doi, "/Title": revision})
    writer.write(path)


def observe(catalog: PdfCatalog, root: Path, name: str, metadata: dict[str, Any]):
    stat = (root / name).stat()
    return catalog.observe_file(
        root,
        name,
        sha256=sha256_file(root / name),
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        device=stat.st_dev,
        inode=stat.st_ino,
        metadata=metadata,
        status="resolved" if metadata.get("bibcode") else "unresolved",
    )


def verified(path: Path, doc: dict[str, Any]) -> dict[str, Any]:
    digest = sha256_file(path)
    record = {**doc, "sha256": digest}
    record[IDENTITY_KEY] = assert_mutation_allowed(
        path, record, expected_sha256=digest, provider_document=doc
    )
    return record


def counts(catalog: PdfCatalog) -> tuple[int, int, int]:
    return tuple(  # type: ignore[return-value]
        catalog._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("attachments", "file_locations", "content_versions")
    )


def active(catalog: PdfCatalog, root: Path) -> list[str]:
    return sorted(
        row["relative_path"]
        for row in catalog._connection.execute(
            "SELECT relative_path FROM file_locations WHERE missing_at IS NULL"
        )
    )


@pytest.fixture
def setup(tmp_path: Path):
    catalog = PdfCatalog(tmp_path / "catalog.sqlite")
    root = tmp_path / "library"
    root.mkdir()
    yield catalog, root
    catalog.close()


def old_with_user_data(catalog: PdfCatalog, root: Path, name: str = "Opitom.pdf"):
    make_pdf(root / name, "10.1038/example", "old")
    observation = observe(
        catalog,
        root,
        name,
        {
            **DOC,
            "notes": "Keep my notes",
            LOCKED_METADATA_FIELDS_KEY: ["title"],
        },
    )
    (root / name).unlink()
    return observation


def test_renamed_download_rejoins_deleted_attachment(setup) -> None:
    catalog, root = setup
    old = old_with_user_data(catalog, root)
    history = {v["sha256"] for v in catalog.content_versions(old.attachment_id)}
    make_pdf(root / "download.pdf", "10.1038/example", "new")
    result = catalog.register_verified_replacement(
        root, "download.pdf", verified(root / "download.pdf", DOC)
    )
    assert result is not None and result.attachment_id == old.attachment_id
    record = catalog.get_file(root, "download.pdf")
    assert record["metadata"]["notes"] == "Keep my notes"
    assert record["metadata"][LOCKED_METADATA_FIELDS_KEY] == ["title"]
    assert (
        record["metadata"]["title"] == ["Updated paper"]
        or "title" in record["metadata"]
    )
    assert record["metadata"][IDENTITY_KEY]["sha256"] == sha256_file(
        root / "download.pdf"
    )
    versions = {v["sha256"] for v in catalog.content_versions(old.attachment_id)}
    assert history < versions
    assert active(catalog, root) == ["download.pdf"]
    # The old location row stays as history.
    assert counts(catalog)[1] == 2


def test_fresh_arrival_attachment_is_absorbed_without_deleting_rows(setup) -> None:
    catalog, root = setup
    old = old_with_user_data(catalog, root)
    make_pdf(root / "download.pdf", "10.1038/example", "new")
    arrival = observe(
        catalog, root, "download.pdf", verified(root / "download.pdf", DOC)
    )
    result = catalog.register_verified_replacement(
        root, "download.pdf", verified(root / "download.pdf", DOC)
    )
    assert result is not None
    assert result.attachment_id == old.attachment_id
    assert result.location_id == arrival.location_id
    assert (
        catalog.get_file(root, "download.pdf")["metadata"]["notes"] == "Keep my notes"
    )
    assert counts(catalog)[0] == 2  # the arrival attachment remains as history


def test_same_path_same_paper_new_bytes_keeps_notes(setup) -> None:
    catalog, root = setup
    make_pdf(root / "Opitom.pdf", "10.1038/example", "old")
    first = observe(catalog, root, "Opitom.pdf", {**DOC, "notes": "mine"})
    make_pdf(root / "Opitom.pdf", "10.1038/example", "newer bytes")
    result = catalog.register_verified_replacement(
        root, "Opitom.pdf", verified(root / "Opitom.pdf", DOC)
    )
    assert result is not None and result.attachment_id == first.attachment_id
    record = catalog.get_file(root, "Opitom.pdf")
    assert record["metadata"]["notes"] == "mine"
    assert record["sha256"] == sha256_file(root / "Opitom.pdf")
    assert len(catalog.content_versions(first.attachment_id)) == 2


def test_multiple_missing_candidates_refuse(setup) -> None:
    catalog, root = setup
    old_with_user_data(catalog, root, "one.pdf")
    make_pdf(root / "two.pdf", "10.1038/example", "second")
    observe(catalog, root, "two.pdf", {**DOC, "notes": "second"})
    (root / "two.pdf").unlink()
    make_pdf(root / "download.pdf", "10.1038/example", "new")
    before = counts(catalog)
    assert (
        catalog.register_verified_replacement(
            root, "download.pdf", verified(root / "download.pdf", DOC)
        )
        is None
    )
    assert counts(catalog) == before


def test_live_alias_refuses(setup) -> None:
    catalog, root = setup
    old_with_user_data(catalog, root)
    make_pdf(root / "alias.pdf", "10.1038/example", "alias")
    # A second location of the same attachment is still on disk.
    row = catalog._connection.execute("SELECT * FROM file_locations").fetchone()
    with catalog._connection:
        catalog._connection.execute(
            "INSERT INTO file_locations(attachment_id, root_id, relative_path, size_bytes,"
            " mtime_ns, current_version_id, first_seen_at, last_seen_at)"
            " VALUES (?, ?, 'alias.pdf', 1, 1, ?, 'x', 'x')",
            (row["attachment_id"], row["root_id"], row["current_version_id"]),
        )
    make_pdf(root / "download.pdf", "10.1038/example", "new")
    before = counts(catalog)
    assert (
        catalog.register_verified_replacement(
            root, "download.pdf", verified(root / "download.pdf", DOC)
        )
        is None
    )
    assert counts(catalog) == before


def test_different_paper_at_same_path_gets_isolated_attachment(setup) -> None:
    catalog, root = setup
    make_pdf(root / "paper.pdf", "10.3847/other", "old")
    first = observe(catalog, root, "paper.pdf", {**OTHER, "notes": "other notes"})
    make_pdf(root / "paper.pdf", "10.1038/example", "new")
    result = catalog.register_verified_replacement(
        root, "paper.pdf", verified(root / "paper.pdf", DOC)
    )
    assert result is not None and result.attachment_id != first.attachment_id
    record = catalog.get_file(root, "paper.pdf")
    assert "notes" not in record["metadata"]
    assert record["metadata"]["bibcode"] == DOC["bibcode"]
    kept = catalog._connection.execute(
        "SELECT missing_at FROM file_locations WHERE attachment_id = ?",
        (first.attachment_id,),
    ).fetchone()
    assert kept["missing_at"] is not None
    assert len(catalog.content_versions(first.attachment_id)) == 1


def test_unverified_pdf_is_refused_without_writes(setup) -> None:
    catalog, root = setup
    old_with_user_data(catalog, root)
    make_pdf(root / "download.pdf", "10.9999/wrong", "new")
    record = {**DOC, "sha256": sha256_file(root / "download.pdf")}
    before = counts(catalog)
    with pytest.raises(ValueError):
        catalog.register_verified_replacement(root, "download.pdf", record)
    assert counts(catalog) == before


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory permissions")
def test_permission_failure_before_mutation(setup) -> None:
    catalog, root = setup
    make_pdf(root / "sub" / "Opitom.pdf", "10.1038/example", "old")
    observe(catalog, root, "sub/Opitom.pdf", {**DOC, "notes": "n"})
    (root / "sub" / "Opitom.pdf").unlink()
    make_pdf(root / "download.pdf", "10.1038/example", "new")
    payload = verified(root / "download.pdf", DOC)
    before = counts(catalog)
    (root / "sub").chmod(0)
    try:
        if os.access(root / "sub", os.R_OK | os.X_OK):
            pytest.skip("permissions are not enforced")
        with pytest.raises(PermissionError):
            catalog.register_verified_replacement(root, "download.pdf", payload)
    finally:
        (root / "sub").chmod(0o755)
    assert counts(catalog) == before
    assert (
        catalog._connection.execute(
            "SELECT COUNT(*) FROM file_locations WHERE missing_at IS NULL"
        ).fetchone()[0]
        == 1
    )
