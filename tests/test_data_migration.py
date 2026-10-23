"""Data-directory snapshots and preserved catalog metadata."""

import json
import sqlite3
from pathlib import Path

from adsfolio.config import Config
from adsfolio.data_migration import copy_data_directory
from adsfolio.pdf.catalog import PdfCatalog
from adsfolio.pdf.sidecar import Sidecar


def test_directory_copy_snapshots_live_wal_and_keeps_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    settings = {
        "roots": ["/papers"],
        "notes": "Keep reserved-looking keys in user text",
    }
    (source / "gui.json").write_text(json.dumps(settings))
    database = sqlite3.connect(source / "pdf-library.sqlite")
    try:
        database.execute("PRAGMA journal_mode=WAL")
        database.execute("CREATE TABLE history (id INTEGER PRIMARY KEY, path TEXT)")
        database.execute("INSERT INTO history VALUES (42, 'old/name.pdf')")
        database.commit()
        copy_data_directory(source, target)
        copied = sqlite3.connect(target / "pdf-library.sqlite")
        try:
            assert copied.execute("SELECT * FROM history").fetchall() == [
                (42, "old/name.pdf")
            ]
        finally:
            copied.close()
        assert json.loads((target / "gui.json").read_text()) == settings
        assert database.execute("SELECT count(*) FROM history").fetchone()[0] == 1
        (target / "gui.json").write_text("new user settings")
        copy_data_directory(source, target)
        assert (target / "gui.json").read_text() == "new user settings"
    finally:
        database.close()


def test_configuration_uses_copied_settings(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "config/ads"
    source.mkdir(parents=True)
    (source / "setup.json").write_text('{"notes": "original"}')
    destination = Config.get_config_dir()
    assert destination.name == "adsfolio"
    assert (destination / "setup.json").read_text() == (
        source / "setup.json"
    ).read_text()


def test_catalog_reopen_keeps_identity_history_and_field_locks(tmp_path: Path) -> None:
    path = tmp_path / "library.sqlite"
    root = tmp_path / "papers"
    root.mkdir()
    catalog = PdfCatalog(path)
    observation = catalog.observe_file(
        root,
        "paper.pdf",
        sha256="digest",
        size_bytes=1,
        mtime_ns=1,
        status="resolved",
        metadata={
            "bibcode": "2024Test",
            "title": ["Manual title"],
            "_adsfolio_locked_fields": ["title"],
        },
    )
    catalog.close()
    catalog = PdfCatalog(path)
    try:
        record = catalog.get_file(root, "paper.pdf")
        assert record["id"] == observation.attachment_id
        assert record["version_id"] == observation.version_id
        assert record["metadata"]["_adsfolio_locked_fields"] == ["title"]
        catalog.observe_file(
            root,
            "paper.pdf",
            sha256="digest",
            size_bytes=1,
            mtime_ns=1,
            status="resolved",
            metadata={"title": ["Provider title"]},
        )
        assert catalog.get_file(root, "paper.pdf")["metadata"]["title"] == [
            "Manual title"
        ]
    finally:
        catalog.close()


def test_sidecar_reads_metadata_without_changing_source(tmp_path: Path) -> None:
    path = tmp_path / ".adsfolio-pdfs.json"
    payload = {
        "version": 1,
        "files": {
            "paper.pdf": {"title": ["Retained"], "_adsfolio_locked_fields": ["title"]}
        },
    }
    path.write_text(json.dumps(payload))
    sidecar = Sidecar(tmp_path)
    assert sidecar.files["paper.pdf"]["_adsfolio_locked_fields"] == ["title"]
    assert json.loads(path.read_text()) == payload
