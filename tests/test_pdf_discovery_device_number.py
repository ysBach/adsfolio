"""A new device number alone does not make the startup scan reprocess PDFs.

macOS can give a volume a new device number after a restart. Every file keeps
its inode, size, modification time, and bytes.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pdf_identity_fixtures import write_pdf

from adsfolio.pdf import catalog as catalog_module
from adsfolio.pdf.catalog import PdfCatalog
from adsfolio.pdf.files import sha256_file
from adsfolio.pdf.readiness import WATCH_PENDING_KEY


@pytest.fixture
def library(tmp_path: Path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    catalog = PdfCatalog(tmp_path / "catalog.sqlite")
    # Identified PDFs have a saved hash, like the PDFs of a real library.
    for name in ("first.pdf", "second.pdf"):
        path = root / name
        write_pdf(path, doi=f"10.1000/{name}")
        stat = path.stat()
        catalog.observe_file(
            root,
            name,
            sha256=sha256_file(path),
            size_bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            device=stat.st_dev,
            inode=stat.st_ino,
        )
    catalog.discover_root(root)
    hashed: list[Path] = []
    original = catalog_module.sha256_file

    def counting(path: Path) -> str:
        hashed.append(Path(path))
        return original(path)

    monkeypatch.setattr(catalog_module, "sha256_file", counting)
    yield root, catalog, hashed
    catalog.close()


def _pending(catalog: PdfCatalog, root: Path, name: str) -> bool:
    return bool(catalog.get_file(root, name)["metadata"].get(WATCH_PENDING_KEY))


def test_new_device_number_alone_neither_hashes_nor_flags(library) -> None:
    root, catalog, hashed = library
    with catalog._connection:
        catalog._connection.execute("UPDATE file_locations SET device = device + 2")

    catalog.discover_root(root)

    assert hashed == []
    assert not _pending(catalog, root, "first.pdf")
    assert not _pending(catalog, root, "second.pdf")
    assert (
        catalog.get_file(root, "first.pdf")["device"]
        == (root / "first.pdf").stat().st_dev
    )


def test_touched_file_with_the_same_bytes_is_not_flagged(library) -> None:
    root, catalog, hashed = library
    path = root / "first.pdf"
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))

    catalog.discover_root(root)

    assert hashed == [path]
    assert not _pending(catalog, root, "first.pdf")


def test_changed_bytes_are_flagged(library) -> None:
    root, catalog, _hashed = library
    write_pdf(root / "first.pdf", doi="10.1000/a-different-and-longer-doi")

    catalog.discover_root(root)

    assert _pending(catalog, root, "first.pdf")
    assert not _pending(catalog, root, "second.pdf")


def test_repair_clears_only_flags_without_new_content_once(library) -> None:
    """Flags from a device number alone are cleared. A real change keeps its flag."""
    root, catalog, _hashed = library
    connection = catalog._connection
    with connection:
        connection.execute("DELETE FROM catalog_migrations")
        connection.execute(
            "UPDATE attachments SET updated_at = '2026-11-07T13:48:32+00:00', "
            f"metadata_json = json_set(metadata_json, '$.{WATCH_PENDING_KEY}', "
            "json('true'))"
        )
        # first.pdf: content seen in an earlier scan, flagged by a later one.
        # second.pdf: new content seen by the scan that flagged it.
        for name, observed in (
            ("first.pdf", "2026-11-01T10:00:00+00:00"),
            ("second.pdf", "2026-11-07T13:48:32+00:00"),
        ):
            connection.execute(
                "UPDATE content_versions SET observed_at = ? WHERE id = ("
                "SELECT current_version_id FROM file_locations "
                "WHERE relative_path = ?)",
                (observed, name),
            )

    catalog.discover_root(root)

    assert not _pending(catalog, root, "first.pdf")
    assert _pending(catalog, root, "second.pdf")

    # The repair runs once. A later flag stays until the watcher handles it.
    with connection:
        connection.execute(
            "UPDATE attachments SET "
            f"metadata_json = json_set(metadata_json, '$.{WATCH_PENDING_KEY}', "
            "json('true'))"
        )
    catalog.discover_root(root)
    assert _pending(catalog, root, "first.pdf")


def test_manual_refresh_ignores_a_new_device_number(
    tmp_path: Path, monkeypatch
) -> None:
    from adsfolio.config import Config
    from adsfolio.pdf.registry import PdfRegistry

    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    root = tmp_path / "library"
    root.mkdir()
    path = root / "paper.pdf"
    write_pdf(path, doi="10.1000/paper")
    stat = path.stat()
    registry = PdfRegistry(root)
    try:
        registry.upsert_file(
            root=root,
            relative_path=path.name,
            current_path=path.name,
            sha256=sha256_file(path),
            size_bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            status="unresolved",
        )
        connection = registry.catalog._connection
        with connection:
            connection.execute("UPDATE file_locations SET device = device + 2")
        hashed: list[Path] = []
        monkeypatch.setattr(
            "adsfolio.pdf.registry.sha256_file",
            lambda target: hashed.append(target) or "",
        )
        result = registry.reconcile_file_facts(root)
        assert (result.updated, hashed) == (0, [])
    finally:
        registry.close()
