"""Catalog discovery, indexed lookups, and one-time legacy metadata import."""

import json
from pathlib import Path

import pytest

from adsfolio.config import Config
from adsfolio.pdf.catalog import PdfCatalog
from adsfolio.pdf.files import sha256_file
from adsfolio.pdf.identify_settings import IdentifySettings
from adsfolio.pdf.registry import PdfRegistry


@pytest.fixture(autouse=True)
def _isolated_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )


def _observe(catalog: PdfCatalog, root: Path, name: str, sha256: str | None) -> None:
    catalog.observe_file(root, name, sha256=sha256, size_bytes=1, mtime_ns=1)


def test_discovery_accepts_uppercase_pdf_suffix(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    for name in ("Upper.PDF", "mixed.Pdf", "note.txt"):
        (root / name).write_bytes(b"%PDF-1.4")
    catalog = PdfCatalog()

    assert catalog.discover_root(root) == 2
    names = {record["relative_path"] for record in catalog.query_files(root=root)}
    assert names == {"Upper.PDF", "mixed.Pdf"}
    catalog.close()


def test_query_files_filters_by_identifier_and_digest(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    catalog = PdfCatalog()
    _observe(catalog, root, "b.pdf", "x" * 64)
    _observe(catalog, root, "a.pdf", "x" * 64)
    _observe(catalog, root, "c.pdf", "y" * 64)
    _observe(catalog, root, "d.pdf", None)
    target = catalog.get_file(root, "c.pdf")

    by_attachment = catalog.query_files(
        root=root, attachment_id=target["attachment_id"]
    )
    by_location = catalog.query_files(root=root, location_id=target["location_id"])
    assert [r["relative_path"] for r in by_attachment] == ["c.pdf"]
    assert [r["relative_path"] for r in by_location] == ["c.pdf"]

    single = catalog.query_files(root=root, sha256="x" * 64)
    assert [r["relative_path"] for r in single] == ["a.pdf", "b.pdf"]
    several = catalog.query_files(root=root, sha256=["y" * 64, "x" * 64])
    assert [r["relative_path"] for r in several] == ["a.pdf", "b.pdf", "c.pdf"]
    assert catalog.query_files(root=root, sha256=[]) == []
    catalog.close()


def test_digest_collection_exceeds_legacy_sql_variable_limit(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    catalog = PdfCatalog()
    _observe(catalog, root, "kept.pdf", "k" * 64)
    digests = {f"{index:064x}" for index in range(1_500)} | {"k" * 64}

    records = catalog.query_files(root=root, sha256=digests)

    assert [r["relative_path"] for r in records] == ["kept.pdf"]
    catalog.close()


def test_registry_lookups_return_current_locations_in_path_order(
    tmp_path: Path,
) -> None:
    registry = PdfRegistry(tmp_path)
    for name, digest in (("b.pdf", "d" * 64), ("a.pdf", "d" * 64), ("m.pdf", "e" * 64)):
        _observe(registry.catalog, tmp_path, name, digest)
    first = registry.catalog.get_file(tmp_path, "a.pdf")
    gone = registry.catalog.get_file(tmp_path, "m.pdf")

    assert registry.find_file_by_sha256("d" * 64)["relative_path"] == "a.pdf"
    assert registry.find_file_by_id(first["attachment_id"])["relative_path"] == "a.pdf"
    assert (
        registry.find_location_by_id(first["location_id"])["relative_path"] == "a.pdf"
    )
    assert [
        r["relative_path"] for r in registry.find_files_by_sha256(["e" * 64, " "])
    ] == ["m.pdf"]

    registry.catalog.mark_missing_locations(tmp_path, {"a.pdf", "b.pdf"})

    assert registry.find_file_by_sha256("e" * 64) is None
    assert registry.find_file_by_id(gone["attachment_id"]) is None
    assert registry.find_location_by_id(gone["location_id"]) is None
    assert registry.find_file_by_sha256("missing") is None
    registry.close()


def _write_sidecar(root: Path, settings: IdentifySettings) -> None:
    (root / ".adsfolio-pdfs.json").write_text(
        json.dumps(
            {
                "version": 2,
                "files": {"p.pdf": {"current_filename": "p.pdf", "bibcode": "B"}},
                "identify_settings": settings.to_mapping(),
            }
        ),
        encoding="utf-8",
    )


def test_sidecar_settings_import_once_and_keep_later_catalog_changes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    _write_sidecar(root, IdentifySettings(recursive=True))
    registry = PdfRegistry(root)
    key = f"root:{registry.directory}:identify_settings"
    imported = IdentifySettings.from_mapping(json.loads(registry.catalog.get_meta(key)))
    assert imported == IdentifySettings(recursive=True)
    changed = IdentifySettings(output_unresolved="pending")
    registry.catalog.set_meta(key, json.dumps(changed.to_mapping()))
    registry.close()

    reopened = PdfRegistry(root)

    stored = IdentifySettings.from_mapping(json.loads(reopened.catalog.get_meta(key)))
    assert stored == changed
    reopened.close()


def test_existing_catalog_settings_win_over_first_sidecar_import(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    _write_sidecar(root, IdentifySettings(recursive=True))
    catalog = PdfCatalog()
    key = f"root:{catalog._normalize_root(root)}:identify_settings"
    current = IdentifySettings(output_unresolved="pending")
    catalog.set_meta(key, json.dumps(current.to_mapping()))

    catalog.migrate_legacy_root(root)

    stored = IdentifySettings.from_mapping(json.loads(catalog.get_meta(key)))
    assert stored == current
    catalog.close()


def test_completed_sources_are_not_decoded_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    _write_sidecar(root, IdentifySettings())
    catalog = PdfCatalog()
    catalog.migrate_legacy_root(root)

    def fail(*_: object) -> None:
        raise AssertionError("migrated source was decoded again")

    monkeypatch.setattr(PdfCatalog, "_read_legacy_sidecar", staticmethod(fail))
    monkeypatch.setattr("adsfolio.pdf.catalog.Sidecar", fail)

    assert catalog.migrate_legacy_root(root).imported == 0
    catalog.close()


def _resolved_file_with_reused_inode(root: Path) -> tuple[PdfCatalog, int]:
    catalog = PdfCatalog()
    first = catalog.observe_file(
        root,
        "a.pdf",
        sha256="a" * 64,
        size_bytes=10,
        mtime_ns=1,
        device=1,
        inode=7,
        metadata={"bibcode": "B"},
        status="resolved",
    )
    return catalog, first.attachment_id


@pytest.mark.parametrize("missing", [True, False])
@pytest.mark.parametrize(
    ("sha256", "matched_by", "same_attachment"),
    [
        (None, "new", False),
        ("b" * 64, "new", False),
        ("a" * 64, "filesystem identity", True),
    ],
)
def test_inode_with_known_digest_matches_only_equal_known_digest(
    tmp_path: Path,
    missing: bool,
    sha256: str | None,
    matched_by: str,
    same_attachment: bool,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    catalog, original = _resolved_file_with_reused_inode(root)
    if missing:
        catalog.mark_missing_locations(root, set())

    observed = catalog.observe_file(
        root, "b.pdf", sha256=sha256, size_bytes=99, mtime_ns=2, device=1, inode=7
    )

    assert observed.matched_by == matched_by
    assert (observed.attachment_id == original) is same_attachment
    status = catalog.get_file(root, "b.pdf")["status"]
    assert status == ("resolved" if same_attachment else "unresolved")
    catalog.close()


def test_inode_with_unknown_stored_digest_keeps_identity_match(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    catalog = PdfCatalog()
    first = catalog.observe_file(
        root, "a.pdf", sha256=None, size_bytes=1, mtime_ns=1, device=1, inode=7
    )

    moved = catalog.observe_file(
        root, "b.pdf", sha256=None, size_bytes=1, mtime_ns=1, device=1, inode=7
    )

    assert moved.attachment_id == first.attachment_id
    assert moved.matched_by == "filesystem identity"
    catalog.close()


def test_unknown_digest_location_does_not_outvote_known_conflicting_digest(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    catalog, original = _resolved_file_with_reused_inode(root)
    unknown = catalog.observe_file(
        root, "twin.pdf", sha256=None, size_bytes=1, mtime_ns=1, device=1, inode=8
    )
    with catalog._connection:
        catalog._connection.execute(
            "UPDATE file_locations SET inode = 7 WHERE id = ?", (unknown.location_id,)
        )

    observed = catalog.observe_file(
        root, "c.pdf", sha256="b" * 64, size_bytes=10, mtime_ns=1, device=1, inode=7
    )

    assert observed.attachment_id not in {original, unknown.attachment_id}
    assert observed.matched_by == "new"
    catalog.close()


def test_identity_with_equal_digest_beats_duplicate_content_elsewhere(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    catalog, original = _resolved_file_with_reused_inode(root)
    catalog.observe_file(
        root, "copy.pdf", sha256="a" * 64, size_bytes=10, mtime_ns=1, device=1, inode=9
    )
    catalog.mark_missing_locations(root, {"copy.pdf"})

    observed = catalog.observe_file(
        root, "back.pdf", sha256="a" * 64, size_bytes=10, mtime_ns=1, device=1, inode=7
    )

    assert observed.attachment_id == original
    catalog.close()


def _write_pdf(path: Path, content: bytes) -> None:
    path.write_bytes(b"%PDF-1.4 " + content)


def _seed_resolved_file(
    root: Path, name: str, content: bytes
) -> tuple[PdfCatalog, int]:
    _write_pdf(root / name, content)
    catalog = PdfCatalog()
    stat = (root / name).stat()
    first = catalog.observe_file(
        root,
        name,
        sha256=sha256_file(root / name),
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        device=stat.st_dev,
        inode=stat.st_ino,
        metadata={"bibcode": "B"},
        status="resolved",
    )
    return catalog, first.attachment_id


def test_discovery_restores_renamed_file_with_unchanged_content(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    catalog, original = _seed_resolved_file(root, "a.pdf", b"one")
    (root / "a.pdf").rename(root / "renamed.pdf")

    catalog.discover_root(root)

    record = catalog.get_file(root, "renamed.pdf")
    assert record["attachment_id"] == original
    assert record["status"] == "resolved"
    assert record["sha256"] == sha256_file(root / "renamed.pdf")
    catalog.close()


def test_discovery_gives_edited_and_renamed_file_a_new_attachment(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    catalog, original = _seed_resolved_file(root, "a.pdf", b"one")
    (root / "a.pdf").rename(root / "renamed.pdf")
    _write_pdf(root / "renamed.pdf", b"two, edited")

    catalog.discover_root(root)

    record = catalog.get_file(root, "renamed.pdf")
    assert record["attachment_id"] != original
    assert record["status"] == "unresolved"
    catalog.close()


def test_discovery_does_not_hash_unchanged_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    catalog, _ = _seed_resolved_file(root, "a.pdf", b"one")
    calls: list[Path] = []
    monkeypatch.setattr(
        "adsfolio.pdf.catalog.sha256_file", lambda path: calls.append(path) or "x"
    )

    catalog.discover_root(root)

    assert calls == []
    catalog.close()


def test_discovery_hash_error_aborts_before_changing_the_catalog(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    catalog, original = _seed_resolved_file(root, "a.pdf", b"one")
    (root / "a.pdf").rename(root / "renamed.pdf")

    def unreadable(path: Path) -> str:
        raise PermissionError(path)

    monkeypatch.setattr("adsfolio.pdf.catalog.sha256_file", unreadable)

    with pytest.raises(PermissionError):
        catalog.discover_root(root)

    old = catalog.get_file(root, "a.pdf")
    assert old["attachment_id"] == original
    assert old["missing_at"] is None
    assert catalog.get_file(root, "renamed.pdf") is None
    catalog.close()


def test_discovery_skips_file_that_vanishes_before_hashing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    catalog, _ = _seed_resolved_file(root, "a.pdf", b"one")
    (root / "a.pdf").rename(root / "renamed.pdf")

    def vanished(path: Path) -> str:
        raise FileNotFoundError(path)

    monkeypatch.setattr("adsfolio.pdf.catalog.sha256_file", vanished)

    assert catalog.discover_root(root) == 0
    assert catalog.get_file(root, "renamed.pdf") is None
    catalog.close()
