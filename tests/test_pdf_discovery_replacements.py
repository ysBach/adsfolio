"""Discovery does not hide content changes before the watcher starts."""

from pathlib import Path

from pdf_identity_fixtures import write_pdf

from adsfolio.pdf.catalog import PdfCatalog
from adsfolio.pdf.files import sha256_file
from adsfolio.pdf.identifier import PdfIdentifier
from adsfolio.pdf.readiness import METADATA_STALE_KEY, WATCH_PENDING_KEY

DOC = {"bibcode": "2024ApJ...900..100A", "doi": ["10.1000/old"], "title": ["Paper"]}


def test_discovery_hashes_changed_active_path_before_updating_facts(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    path = root / "paper.pdf"
    write_pdf(path, doi="10.1000/old")
    catalog = PdfCatalog(tmp_path / "catalog.sqlite")
    stat = path.stat()
    old = catalog.observe_file(
        root,
        path.name,
        sha256=sha256_file(path),
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        device=stat.st_dev,
        inode=stat.st_ino,
        metadata=PdfIdentifier(None).build_record(DOC, path),
        status="resolved",
    )
    write_pdf(path, doi="10.1000/new-longer")
    catalog.discover_root(root)
    row = catalog.get_file(root, path.name)
    assert row["sha256"] == sha256_file(path)
    assert row["metadata"][METADATA_STALE_KEY] is True
    assert row["status"] == "unresolved"
    assert row["document_id"] is None
    assert len(catalog.content_versions(old.attachment_id)) == 2
    catalog.close()


def test_new_file_after_prior_import_is_pending_but_initial_baseline_is_not(
    tmp_path: Path,
):
    root = tmp_path / "library"
    root.mkdir()
    first = root / "first.pdf"
    write_pdf(first)
    catalog = PdfCatalog(tmp_path / "catalog.sqlite")
    catalog.discover_root(root)
    assert not catalog.get_file(root, first.name)["metadata"].get(WATCH_PENDING_KEY)
    first.unlink()
    second = root / "replacement.pdf"
    write_pdf(second, doi="10.1000/new")
    catalog.discover_root(root)
    assert catalog.get_file(root, second.name)["metadata"][WATCH_PENDING_KEY] is True
    assert catalog.get_file(root, first.name)["missing_at"] is not None
    catalog.close()
