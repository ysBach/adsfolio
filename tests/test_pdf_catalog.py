import json
import sqlite3
from pathlib import Path

import pytest

from adsfolio.config import Config
from adsfolio.pdf.catalog import CATALOG_FILENAME, CATALOG_SCHEMA_VERSION, PdfCatalog
from adsfolio.pdf.registry import PdfRegistry


def _catalog(monkeypatch, tmp_path: Path) -> PdfCatalog:
    config_dir = tmp_path / "config"
    monkeypatch.setattr(
        Config,
        "get_config_dir",
        classmethod(lambda cls: config_dir),
    )
    return PdfCatalog()


def test_catalog_uses_one_database_for_multiple_roots(monkeypatch, tmp_path):
    catalog = _catalog(monkeypatch, tmp_path)
    root_a = tmp_path / "library-a"
    root_b = tmp_path / "library-b"

    first = catalog.observe_file(
        root_a,
        "paper.pdf",
        sha256="a" * 64,
        size_bytes=10,
        mtime_ns=20,
    )
    second = catalog.observe_file(
        root_b,
        "copy.pdf",
        sha256="a" * 64,
        size_bytes=10,
        mtime_ns=30,
    )

    assert catalog.path == tmp_path / "config" / CATALOG_FILENAME
    assert first.attachment_id == second.attachment_id
    assert len(catalog.query_files()) == 2
    assert not (root_a / ".adsapi-pdfs.sqlite").exists()
    assert not (root_b / ".adsapi-pdfs.sqlite").exists()

    catalog.close()


def test_catalog_changed_content_creates_version_without_new_attachment(
    monkeypatch, tmp_path
):
    catalog = _catalog(monkeypatch, tmp_path)
    root = tmp_path / "library"

    original = catalog.observe_file(
        root,
        "paper.pdf",
        sha256="a" * 64,
        size_bytes=10,
        mtime_ns=20,
        metadata={"title": ["Local title"]},
    )
    changed = catalog.observe_file(
        root,
        "paper.pdf",
        sha256="b" * 64,
        size_bytes=12,
        mtime_ns=30,
    )

    assert changed.attachment_id == original.attachment_id
    versions = catalog.content_versions(original.attachment_id)
    assert [version["sha256"] for version in versions] == ["a" * 64, "b" * 64]
    record = catalog.get_file(root, "paper.pdf")
    assert record is not None
    assert record["sha256"] == "b" * 64
    assert record["metadata"]["title"] == ["Local title"]

    catalog.close()


def test_catalog_records_expose_location_first_seen_at(monkeypatch, tmp_path) -> None:
    catalog = _catalog(monkeypatch, tmp_path)
    root = tmp_path / "library"
    observed = catalog.observe_file(
        root,
        "paper.pdf",
        sha256="a" * 64,
        size_bytes=10,
        mtime_ns=20,
    )
    expected = "2026-08-29T01:02:03+00:00"
    with catalog._connection:
        catalog._connection.execute(
            "UPDATE file_locations SET first_seen_at = ? WHERE id = ?",
            (expected, observed.location_id),
        )

    record = catalog.get_file(root, "paper.pdf")
    records = catalog.query_files(root=root)

    assert record is not None
    assert record["first_seen_at"] == expected
    assert records[0]["first_seen_at"] == expected
    catalog.close()


def test_catalog_move_preserves_location_first_seen_at(monkeypatch, tmp_path) -> None:
    catalog = _catalog(monkeypatch, tmp_path)
    root = tmp_path / "library"
    observed = catalog.observe_file(
        root,
        "before.pdf",
        sha256="a" * 64,
        size_bytes=10,
        mtime_ns=20,
    )
    expected = "2026-08-29T01:02:03+00:00"
    with catalog._connection:
        catalog._connection.execute(
            "UPDATE file_locations SET first_seen_at = ? WHERE id = ?",
            (expected, observed.location_id),
        )

    assert catalog.move_file(root, "before.pdf", "after.pdf")
    moved = catalog.get_file(root, "after.pdf")

    assert moved is not None
    assert moved["location_id"] == observed.location_id
    assert moved["first_seen_at"] == expected
    catalog.close()


def test_catalog_move_reuses_path_with_only_missing_history(monkeypatch, tmp_path):
    catalog = _catalog(monkeypatch, tmp_path)
    root = tmp_path / "library"
    catalog.observe_file(
        root,
        "target.pdf",
        sha256="a" * 64,
        size_bytes=10,
        mtime_ns=20,
    )
    catalog.mark_missing_locations(root, set())
    catalog.observe_file(
        root,
        "source.pdf",
        sha256="b" * 64,
        size_bytes=12,
        mtime_ns=30,
    )

    assert catalog.move_file(root, "source.pdf", "target.pdf")
    assert {record["relative_path"] for record in catalog.query_files(root=root)} == {
        "target.pdf"
    }

    catalog.close()


def test_catalog_move_to_same_path_is_idempotent(monkeypatch, tmp_path):
    catalog = _catalog(monkeypatch, tmp_path)
    root = tmp_path / "library"
    catalog.observe_file(
        root,
        "paper.pdf",
        sha256="a" * 64,
        size_bytes=10,
        mtime_ns=20,
    )

    assert catalog.move_file(root, "paper.pdf", "paper.pdf")
    assert [record["relative_path"] for record in catalog.query_files(root=root)] == [
        "paper.pdf"
    ]

    catalog.close()


def test_catalog_deletes_one_duplicate_location_without_deleting_sibling(
    monkeypatch, tmp_path
):
    catalog = _catalog(monkeypatch, tmp_path)
    root = tmp_path / "library"
    first = catalog.observe_file(
        root,
        "first.pdf",
        sha256="1" * 64,
        size_bytes=10,
        mtime_ns=20,
    )
    second = catalog.observe_file(
        root,
        "second.pdf",
        sha256="1" * 64,
        size_bytes=10,
        mtime_ns=21,
    )

    assert catalog.delete_locations(root, [first.location_id]) == 1
    assert catalog.query_files(root=root) == [catalog.get_file(root, "second.pdf")]
    assert first.attachment_id == second.attachment_id

    catalog.close()


def test_catalog_resolved_status_requires_document(monkeypatch, tmp_path):
    catalog = _catalog(monkeypatch, tmp_path)
    root = tmp_path / "library"
    catalog.observe_file(
        root,
        "missing-bibcode.pdf",
        sha256="2" * 64,
        size_bytes=10,
        mtime_ns=20,
        status="resolved",
    )

    assert catalog.query_files(root=root, resolved=True) == []
    records = catalog.query_files(root=root, resolved=False)
    assert len(records) == 1
    assert records[0]["status"] == "unresolved"

    catalog.close()


def test_catalog_discovers_pdfs_without_hashing_or_downgrading_known_records(
    monkeypatch, tmp_path
):
    catalog = _catalog(monkeypatch, tmp_path)
    root = tmp_path / "library"
    root.mkdir()
    known_path = root / "known.pdf"
    new_path = root / "new.pdf"
    known_path.write_bytes(b"known")
    new_path.write_bytes(b"new")
    known = catalog.observe_file(
        root,
        "known.pdf",
        sha256="3" * 64,
        size_bytes=known_path.stat().st_size,
        mtime_ns=known_path.stat().st_mtime_ns,
        metadata={"bibcode": "B1"},
        status="resolved",
    )

    assert catalog.discover_root(root) == 2

    known_record = catalog.get_file(root, "known.pdf")
    new_record = catalog.get_file(root, "new.pdf")
    assert known_record is not None
    assert new_record is not None
    assert known_record["status"] == "resolved"
    assert known_record["sha256"] == "3" * 64
    assert len(catalog.content_versions(known.attachment_id)) == 1
    assert new_record["status"] == "unresolved"
    assert new_record["sha256"] == ""

    catalog.close()


def test_catalog_links_resolved_attachment_to_document(monkeypatch, tmp_path):
    catalog = _catalog(monkeypatch, tmp_path)
    observation = catalog.observe_file(
        tmp_path / "library",
        "paper.pdf",
        sha256="d" * 64,
        size_bytes=10,
        mtime_ns=20,
        metadata={"bibcode": "B1", "title": ["Paper"]},
        status="resolved",
    )

    document = catalog.document_for_attachment(observation.attachment_id)

    assert document is not None
    assert document["bibcode"] == "B1"
    assert document["metadata"]["title"] == ["Paper"]

    catalog.close()


def test_catalog_ads_snapshot_updates_canonical_document(monkeypatch, tmp_path):
    catalog = _catalog(monkeypatch, tmp_path)
    root = tmp_path / "library"
    observation = catalog.observe_file(
        root,
        "paper.pdf",
        sha256="e" * 64,
        size_bytes=10,
        mtime_ns=20,
        metadata={"bibcode": "B1", "title": ["Local title"]},
        status="resolved",
    )

    catalog.upsert_ads_document(
        {"bibcode": "B1", "title": ["ADS title"], "year": "2024"},
        source="test",
    )

    document = catalog.document_for_attachment(observation.attachment_id)
    assert document is not None
    assert document["metadata"]["title"] == ["ADS title"]
    assert document["metadata"]["year"] == "2024"

    catalog.close()


def test_catalog_can_unlink_document_from_explicit_metadata_replacement(
    monkeypatch, tmp_path
):
    catalog = _catalog(monkeypatch, tmp_path)
    root = tmp_path / "library"
    observation = catalog.observe_file(
        root,
        "paper.pdf",
        sha256="f" * 64,
        size_bytes=10,
        mtime_ns=20,
        metadata={"bibcode": "B1"},
        status="resolved",
    )

    assert catalog.replace_file_metadata(root, "paper.pdf", {"title": ["Manual"]})
    assert catalog.document_for_attachment(observation.attachment_id) is not None

    assert catalog.replace_file_metadata(
        root,
        "paper.pdf",
        {"bibcode": "", "title": ["Manual"]},
    )
    assert catalog.document_for_attachment(observation.attachment_id) is None

    catalog.close()


def test_catalog_ads_batch_rolls_back_on_invalid_document(monkeypatch, tmp_path):
    catalog = _catalog(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="require a bibcode"):
        catalog.upsert_ads_documents(
            [{"bibcode": "B1"}, {"title": ["invalid"]}], source="test"
        )

    assert catalog.find_ads_document_by_bibcode("B1") is None
    catalog.close()


def test_catalog_migrates_legacy_ads_cache(monkeypatch, tmp_path):
    catalog = _catalog(monkeypatch, tmp_path)
    root = tmp_path / "legacy-library"
    root.mkdir()
    legacy_path = root / ".adsapi-pdfs.sqlite"
    legacy = sqlite3.connect(legacy_path)
    legacy.execute(
        """
        CREATE TABLE ads_documents (
            bibcode TEXT PRIMARY KEY,
            doi_json TEXT NOT NULL,
            title_json TEXT NOT NULL,
            author_json TEXT NOT NULL,
            year TEXT,
            bibstem_json TEXT NOT NULL,
            volume TEXT,
            page_json TEXT NOT NULL,
            pub TEXT,
            raw_json TEXT NOT NULL,
            source TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        )
        """
    )
    legacy.execute(
        """
        INSERT INTO ads_documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "B1",
            json.dumps(["10.0/example"]),
            json.dumps(["Cached title"]),
            "[]",
            "2024",
            "[]",
            None,
            "[]",
            None,
            json.dumps({"bibcode": "B1", "title": ["Cached title"]}),
            "legacy",
            "now",
        ),
    )
    legacy.commit()
    legacy.close()

    report = catalog.migrate_legacy_root(root)

    assert report.imported == 0
    cached = catalog.find_ads_document_by_bibcode("B1")
    assert cached is not None
    assert cached["title"] == ["Cached title"]

    catalog.close()


def test_catalog_upgrades_schema_without_document_column(monkeypatch, tmp_path):
    config_dir = tmp_path / "config"
    monkeypatch.setattr(
        Config,
        "get_config_dir",
        classmethod(lambda cls: config_dir),
    )
    config_dir.mkdir()
    path = config_dir / CATALOG_FILENAME
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE catalog_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO catalog_meta VALUES ('schema_version', '1');
        CREATE TABLE attachments (
            id INTEGER PRIMARY KEY,
            status TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    connection.commit()
    connection.close()

    catalog = PdfCatalog()

    assert catalog.schema_version == CATALOG_SCHEMA_VERSION
    columns = {
        row["name"]
        for row in catalog._connection.execute("PRAGMA table_info(attachments)")
    }
    assert "document_id" in columns

    catalog.close()


def test_catalog_persists_unresolved_observation(monkeypatch, tmp_path):
    catalog = _catalog(monkeypatch, tmp_path)
    root = tmp_path / "library"

    observation = catalog.observe_file(
        root,
        "nested/unresolved.pdf",
        sha256=None,
        size_bytes=123,
        mtime_ns=456,
        status="unresolved",
        metadata={"title": ["Filename hint"]},
    )

    record = catalog.get_file(root, "nested/unresolved.pdf")
    assert record is not None
    assert record["id"] == observation.attachment_id
    assert record["status"] == "unresolved"
    assert record["size_bytes"] == 123
    assert record["metadata"]["title"] == ["Filename hint"]

    catalog.close()


def test_catalog_migrates_legacy_registry_once_without_mutating_source(
    monkeypatch, tmp_path
):
    catalog = _catalog(monkeypatch, tmp_path)
    root = tmp_path / "legacy-library"
    legacy_path = root / ".adsapi-pdfs.sqlite"
    root.mkdir()
    legacy = sqlite3.connect(legacy_path)
    legacy.executescript(
        """
        CREATE TABLE files (
            id INTEGER PRIMARY KEY,
            root TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            current_path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL,
            status TEXT NOT NULL,
            bibcode TEXT,
            doi TEXT,
            title_json TEXT NOT NULL,
            author_json TEXT NOT NULL,
            year TEXT,
            bibstem_json TEXT NOT NULL,
            volume TEXT,
            page_json TEXT NOT NULL,
            pub TEXT,
            source TEXT,
            identified_at TEXT,
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE evidence (id INTEGER PRIMARY KEY, note TEXT);
        """
    )
    legacy.execute(
        """
        INSERT INTO files (
            root, relative_path, current_path, sha256, size_bytes, mtime_ns,
            status, bibcode, doi, title_json, author_json, year, bibstem_json,
            volume, page_json, pub, source, identified_at, metadata_json,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(root),
            "original.pdf",
            "renamed.pdf",
            "a" * 64,
            10,
            20,
            "resolved",
            "B1",
            None,
            "[]",
            "[]",
            None,
            "[]",
            None,
            "[]",
            None,
            None,
            None,
            json.dumps({"bibcode": "B1", "title": ["Paper"]}),
            "now",
            "now",
        ),
    )
    legacy.commit()
    legacy.close()
    legacy_bytes = legacy_path.read_bytes()

    first = catalog.migrate_legacy_root(root)
    second = catalog.migrate_legacy_root(root)

    assert first.imported == 1
    assert second.imported == 0
    assert legacy_path.read_bytes() == legacy_bytes
    records = catalog.query_files(root=root)
    assert len(records) == 1
    assert records[0]["relative_path"] == "renamed.pdf"
    assert records[0]["metadata"]["bibcode"] == "B1"
    check = sqlite3.connect(legacy_path)
    assert check.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 0
    check.close()

    catalog.close()


def test_catalog_migrates_sidecar_without_deleting_it(monkeypatch, tmp_path):
    catalog = _catalog(monkeypatch, tmp_path)
    root = tmp_path / "sidecar-library"
    root.mkdir()
    sidecar_path = root / ".adsapi-pdfs.json"
    sidecar_path.write_text(
        json.dumps(
            {
                "version": 2,
                "files": {
                    "paper.pdf": {
                        "current_filename": "paper.pdf",
                        "sha256": "b" * 64,
                        "bibcode": "B2",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    sidecar_bytes = sidecar_path.read_bytes()

    report = catalog.migrate_legacy_root(root)

    assert report.imported == 1
    assert sidecar_path.read_bytes() == sidecar_bytes
    assert catalog.get_file(root, "paper.pdf")["metadata"]["bibcode"] == "B2"

    catalog.close()


def test_registry_views_multiple_roots_through_one_catalog(monkeypatch, tmp_path):
    config_dir = tmp_path / "config"
    monkeypatch.setattr(
        Config,
        "get_config_dir",
        classmethod(lambda cls: config_dir),
    )
    root_a = tmp_path / "library-a"
    root_b = tmp_path / "library-b"
    registry_a = PdfRegistry(root_a)
    registry_b = PdfRegistry(root_b)

    registry_a.upsert_file(
        root=root_a,
        relative_path="paper.pdf",
        current_path="paper.pdf",
        sha256="c" * 64,
        size_bytes=10,
        mtime_ns=20,
        status="unresolved",
    )
    registry_b.upsert_file(
        root=root_b,
        relative_path="copy.pdf",
        current_path="copy.pdf",
        sha256="c" * 64,
        size_bytes=10,
        mtime_ns=30,
        status="unresolved",
    )

    assert registry_a.path == config_dir / CATALOG_FILENAME
    assert registry_b.path == registry_a.path
    assert (
        registry_a.find_file_by_relative_path(root_a, "paper.pdf")["id"]
        == (registry_b.find_file_by_relative_path(root_b, "copy.pdf")["id"])
    )
    assert not (root_a / ".adsapi-pdfs.sqlite").exists()
    assert not (root_b / ".adsapi-pdfs.sqlite").exists()

    registry_a.close()
    registry_b.close()
