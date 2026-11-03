"""Catalog observations of changed files with Unicode path aliases."""

from __future__ import annotations

import os
import unicodedata
from pathlib import Path

import pytest

from adsfolio.pdf.catalog import LOCKED_METADATA_FIELDS_KEY, PdfCatalog
from adsfolio.pdf.files import sha256_file
from adsfolio.pdf.readiness import METADATA_SHA256_KEY, METADATA_STALE_KEY


@pytest.fixture
def catalog(tmp_path: Path):
    instance = PdfCatalog(tmp_path / "catalog.sqlite")
    yield instance
    instance.close()


@pytest.fixture
def unicode_aliases(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Simulate Unicode-folding lookups within the synthetic test directory."""
    original_lstat = Path.lstat
    test_root = tmp_path.resolve()

    def lstat_with_alias(path: Path, *args, **kwargs):
        try:
            return original_lstat(path, *args, **kwargs)
        except FileNotFoundError as original_error:
            try:
                parts = path.relative_to(test_root).parts
            except ValueError:
                raise original_error
            actual = test_root
            for part in parts:
                names = os.listdir(actual)
                if part in names:
                    actual = actual / part
                    continue
                matches = [
                    name
                    for name in names
                    if unicodedata.normalize("NFC", name)
                    == unicodedata.normalize("NFC", part)
                ]
                if len(matches) != 1:
                    raise
                actual = actual / matches[0]
            return original_lstat(actual, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat_with_alias)


def observe(catalog: PdfCatalog, root: Path, name: str, path: Path, **kwargs):
    stat = path.stat()
    return catalog.observe_file(
        root,
        name,
        sha256=sha256_file(path),
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        device=stat.st_dev,
        inode=stat.st_ino,
        **kwargs,
    )


def replace_content(path: Path, *, atomic: bool) -> None:
    if atomic:
        replacement = path.parent / "replacement.tmp"
        replacement.write_bytes(b"changed synthetic PDF content")
        os.replace(replacement, path)
    else:
        path.write_bytes(b"changed synthetic PDF content")


def resolved_metadata(digest: str) -> dict:
    return {
        "bibcode": "2024Test....1....1A",
        "title": ["Keep the title"],
        "notes": "Keep the note",
        LOCKED_METADATA_FIELDS_KEY: ["title"],
        METADATA_SHA256_KEY: digest,
    }


@pytest.mark.parametrize("atomic", [False, True])
@pytest.mark.parametrize("stored_form", ["NFC", "NFD"])
def test_changed_alias_reuses_location_and_stales_metadata(
    catalog: PdfCatalog,
    tmp_path: Path,
    unicode_aliases: None,
    atomic: bool,
    stored_form: str,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    composed = "Café.pdf"
    stored = unicodedata.normalize(stored_form, composed)
    alias = unicodedata.normalize("NFD" if stored_form == "NFC" else "NFC", composed)
    path = root / stored
    path.write_bytes(b"original synthetic PDF")
    old_digest = sha256_file(path)
    old_stat = path.stat()
    before = observe(
        catalog,
        root,
        stored,
        path,
        status="resolved",
        metadata=resolved_metadata(old_digest),
    )

    replace_content(path, atomic=atomic)
    new_digest = sha256_file(path)
    assert (path.stat().st_ino != old_stat.st_ino) is atomic
    after = observe(
        catalog,
        root,
        alias,
        path,
        status="resolved",
        metadata={"title": ["Must not replace the locked title"]},
    )

    assert after.matched_by == "path"
    assert (after.attachment_id, after.location_id) == (
        before.attachment_id,
        before.location_id,
    )
    record = catalog.get_file(root, alias)
    assert record["sha256"] == new_digest
    assert record["status"] == "unresolved"
    assert record["document_id"] is None
    assert record[METADATA_STALE_KEY] is True
    assert record["metadata"]["title"] == ["Keep the title"]
    assert record["metadata"]["notes"] == "Keep the note"
    assert record["metadata"][LOCKED_METADATA_FIELDS_KEY] == ["title"]
    assert record["metadata"][METADATA_SHA256_KEY] == old_digest
    assert {v["sha256"] for v in catalog.content_versions(before.attachment_id)} == {
        old_digest,
        new_digest,
    }
    assert len(catalog.query_files(root=root, include_missing=True)) == 1
    assert sha256_file(path) == new_digest


@pytest.mark.parametrize("atomic", [False, True])
def test_scan_hashes_changed_alias_before_reusing_location(
    catalog: PdfCatalog,
    tmp_path: Path,
    unicode_aliases: None,
    atomic: bool,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    path = root / "Café.pdf"
    path.write_bytes(b"original synthetic PDF")
    observed_name = next(root.iterdir()).name
    stored_name = (
        unicodedata.normalize("NFC", observed_name)
        if observed_name != unicodedata.normalize("NFC", observed_name)
        else unicodedata.normalize("NFD", observed_name)
    )
    assert stored_name != observed_name
    old_digest = sha256_file(path)
    before = observe(
        catalog,
        root,
        stored_name,
        path,
        status="resolved",
        metadata=resolved_metadata(old_digest),
    )
    replace_content(path, atomic=atomic)
    new_digest = sha256_file(path)

    assert catalog.discover_root(root) == 1

    record = catalog.get_file(root, observed_name)
    assert record["attachment_id"] == before.attachment_id
    assert record["location_id"] == before.location_id
    assert record["sha256"] == new_digest
    assert record[METADATA_STALE_KEY] is True
    assert record["document_id"] is None
    assert record["metadata"]["title"] == ["Keep the title"]
    assert {v["sha256"] for v in catalog.content_versions(before.attachment_id)} == {
        old_digest,
        new_digest,
    }
    assert len(catalog.query_files(root=root, include_missing=True)) == 1
    assert sha256_file(path) == new_digest


def test_alias_with_ascii_nfc_is_not_skipped(
    catalog: PdfCatalog, tmp_path: Path, unicode_aliases: None
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    path = root / "K.pdf"
    path.write_bytes(b"before")
    before = observe(catalog, root, "K.pdf", path)
    path.write_bytes(b"after")

    after = observe(catalog, root, "K.pdf", path)

    assert after.attachment_id == before.attachment_id
    assert after.location_id == before.location_id
    assert len(catalog.content_versions(before.attachment_id)) == 2


def test_missing_exact_history_does_not_hide_changed_active_alias(
    catalog: PdfCatalog, tmp_path: Path, unicode_aliases: None
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    name = "Café.pdf"
    alias = unicodedata.normalize("NFD", name)
    path = root / name
    path.write_bytes(b"before")
    before = observe(catalog, root, name, path)
    with catalog._connection:
        catalog._connection.execute(
            "INSERT INTO file_locations (attachment_id, root_id, relative_path, "
            "size_bytes, mtime_ns, current_version_id, first_seen_at, last_seen_at, "
            "missing_at) SELECT attachment_id, root_id, ?, size_bytes, mtime_ns, "
            "current_version_id, first_seen_at, last_seen_at, ? "
            "FROM file_locations WHERE id = ?",
            (alias, "2026-01-01T00:00:00Z", before.location_id),
        )
    path.write_bytes(b"after")

    after = observe(catalog, root, alias, path)

    assert after.location_id == before.location_id
    locations = catalog.query_files(root=root, include_missing=True)
    assert len(locations) == 2
    assert sum(row["missing_at"] is not None for row in locations) == 1
    assert len(catalog.content_versions(before.attachment_id)) == 2


def test_missing_alias_history_does_not_prove_continuity(
    catalog: PdfCatalog, tmp_path: Path, unicode_aliases: None
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    name = "Café.pdf"
    alias = unicodedata.normalize("NFD", name)
    path = root / name
    path.write_bytes(b"before")
    before = observe(catalog, root, name, path)
    catalog.mark_missing_locations(root, set())
    path.write_bytes(b"after")

    after = observe(catalog, root, alias, path)

    assert after.attachment_id != before.attachment_id
    rows = catalog.query_files(root=root, include_missing=True)
    assert len(rows) == 2
    assert sum(row["missing_at"] is not None for row in rows) == 1


def test_unreadable_directory_does_not_prove_alias(
    catalog: PdfCatalog,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unicode_aliases: None,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    name = "Café.pdf"
    alias = unicodedata.normalize("NFD", name)
    path = root / name
    path.write_bytes(b"before")
    before = observe(catalog, root, name, path)
    path.write_bytes(b"after")
    original_listdir = os.listdir

    def unreadable(directory):
        if Path(directory) == root:
            raise PermissionError("synthetic failure")
        return original_listdir(directory)

    monkeypatch.setattr(os, "listdir", unreadable)
    after = observe(catalog, root, alias, path)

    assert after.attachment_id != before.attachment_id
    assert len(catalog.query_files(root=root)) == 2


@pytest.mark.parametrize("changed", [False, True])
def test_unicode_alias_of_one_symlink_reuses_location(
    catalog: PdfCatalog, tmp_path: Path, unicode_aliases: None, changed: bool
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    target = root / "target.bin"
    target.write_bytes(b"before")
    name = "Café.pdf"
    alias = unicodedata.normalize("NFD", name)
    path = root / name
    path.symlink_to(target.name)
    before = observe(catalog, root, name, path)
    if changed:
        target.write_bytes(b"after")

    after = observe(catalog, root, alias, path)

    assert after.attachment_id == before.attachment_id
    assert after.location_id == before.location_id
    assert len(catalog.query_files(root=root)) == 1
    assert path.is_symlink()


@pytest.mark.parametrize("hard_link", [False, True])
@pytest.mark.parametrize("changed", [False, True])
def test_distinct_unicode_entries_remain_distinct(
    catalog: PdfCatalog,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    hard_link: bool,
    changed: bool,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    first_path = root / "first.bin"
    second_path = root / "second.bin"
    first_path.write_bytes(b"before")
    if hard_link:
        os.link(first_path, second_path)
    else:
        second_path.write_bytes(b"after" if changed else b"before")
    first_name = "Café.pdf"
    second_name = unicodedata.normalize("NFD", first_name)
    before = observe(catalog, root, first_name, first_path)
    if hard_link and changed:
        first_path.write_bytes(b"after")
    original_listdir = os.listdir
    original_lstat = Path.lstat

    def virtual_listdir(directory):
        if Path(directory) == root:
            return [first_name, second_name]
        return original_listdir(directory)

    def virtual_lstat(path: Path, *args, **kwargs):
        if path == root / first_name:
            path = first_path
        elif path == root / second_name:
            path = second_path
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "listdir", virtual_listdir)
    monkeypatch.setattr(Path, "lstat", virtual_lstat)
    after = observe(catalog, root, second_name, second_path)

    assert (after.attachment_id != before.attachment_id) is changed
    assert after.location_id != before.location_id
    assert len(catalog.query_files(root=root)) == 2
    assert (first_path.stat().st_ino == second_path.stat().st_ino) is hard_link


def test_nested_unicode_aliases_reuse_the_same_location(
    catalog: PdfCatalog, tmp_path: Path, unicode_aliases: None
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    parent = root / "Dír"
    parent.mkdir()
    path = parent / "Café.pdf"
    path.write_bytes(b"before")
    name = "Dír/Café.pdf"
    alias = unicodedata.normalize("NFD", name)
    before = observe(catalog, root, name, path)
    path.write_bytes(b"after")

    after = observe(catalog, root, alias, path)

    assert after.attachment_id == before.attachment_id
    assert after.location_id == before.location_id
    assert len(catalog.content_versions(before.attachment_id)) == 2


def test_distinct_normalized_directories_with_hardlinks_stay_distinct(
    catalog: PdfCatalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    first_path = root / "first.bin"
    second_path = root / "second.bin"
    first_path.write_bytes(b"before")
    os.link(first_path, second_path)
    directory = "Dír"
    other_directory = unicodedata.normalize("NFD", directory)
    name = "Café.pdf"
    other_name = unicodedata.normalize("NFD", name)
    before = observe(catalog, root, f"{directory}/{other_name}", first_path)
    first_path.write_bytes(b"after")
    original_listdir = os.listdir

    def virtual_listdir(path):
        if Path(path) == root:
            return [directory, other_directory]
        return original_listdir(path)

    monkeypatch.setattr(os, "listdir", virtual_listdir)
    after = observe(catalog, root, f"{other_directory}/{name}", second_path)

    assert after.attachment_id != before.attachment_id
    assert after.location_id != before.location_id
    assert len(catalog.query_files(root=root)) == 2


def test_alias_through_one_symlinked_directory_reuses_location(
    catalog: PdfCatalog, tmp_path: Path, unicode_aliases: None
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    directory = root / "actual"
    directory.mkdir()
    (root / "link").symlink_to(directory.name)
    path = directory / "Café.pdf"
    path.write_bytes(b"before")
    name = "link/Café.pdf"
    before = observe(catalog, root, name, path)
    path.write_bytes(b"after")

    after = observe(catalog, root, unicodedata.normalize("NFD", name), path)

    assert after.attachment_id == before.attachment_id
    assert after.location_id == before.location_id
    assert len(catalog.query_files(root=root)) == 1


@pytest.mark.parametrize("changed", [False, True])
def test_distinct_unicode_symlinks_to_same_target_remain_distinct(
    catalog: PdfCatalog,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changed: bool,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    target = root / "target.bin"
    target.write_bytes(b"before")
    first_link = root / "first.link"
    second_link = root / "second.link"
    first_link.symlink_to(target.name)
    second_link.symlink_to(target.name)
    first_name = "Café.pdf"
    second_name = unicodedata.normalize("NFD", first_name)
    before = observe(catalog, root, first_name, first_link)
    if changed:
        target.write_bytes(b"after")
    original_listdir = os.listdir
    original_lstat = Path.lstat

    def virtual_listdir(directory):
        if Path(directory) == root:
            return [first_name, second_name]
        return original_listdir(directory)

    def virtual_lstat(path: Path, *args, **kwargs):
        if path == root / first_name:
            path = first_link
        elif path == root / second_name:
            path = second_link
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "listdir", virtual_listdir)
    monkeypatch.setattr(Path, "lstat", virtual_lstat)
    after = observe(catalog, root, second_name, second_link)

    assert (after.attachment_id != before.attachment_id) is changed
    assert after.location_id != before.location_id
    assert len(catalog.query_files(root=root)) == 2
    assert first_link.is_symlink() and second_link.is_symlink()


def test_case_distinct_paths_do_not_bypass_inode_reuse_guard(
    catalog: PdfCatalog, tmp_path: Path
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    before = catalog.observe_file(
        root, "É.pdf", sha256="a" * 64, size_bytes=10, mtime_ns=1, device=1, inode=7
    )

    after = catalog.observe_file(
        root, "é.pdf", sha256="b" * 64, size_bytes=11, mtime_ns=2, device=1, inode=7
    )

    assert after.attachment_id != before.attachment_id
    assert after.matched_by == "new"
    assert len(catalog.query_files(root=root)) == 2


def test_existing_duplicate_attachments_are_not_merged(
    catalog: PdfCatalog,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unicode_aliases: None,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    name = "Café.pdf"
    alias = unicodedata.normalize("NFD", name)
    path = root / name
    path.write_bytes(b"before")
    before = observe(catalog, root, name, path, metadata={"notes": "first"})
    path.write_bytes(b"after")
    with monkeypatch.context() as context:
        context.setattr(
            os, "listdir", lambda directory: (_ for _ in ()).throw(OSError())
        )
        duplicate = observe(catalog, root, alias, path, metadata={"notes": "second"})
    assert duplicate.attachment_id != before.attachment_id
    old_record = catalog.get_file(root, name)

    again = observe(catalog, root, alias, path)

    assert again.attachment_id == duplicate.attachment_id
    assert catalog.get_file(root, name) == old_record
    rows = catalog.query_files(root=root, include_missing=True)
    assert len(rows) == 2
    assert {row["attachment_id"] for row in rows} == {
        before.attachment_id,
        duplicate.attachment_id,
    }


def test_multiple_active_aliases_do_not_choose_one_arbitrarily(
    catalog: PdfCatalog,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unicode_aliases: None,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    path = root / "Å.pdf"
    path.write_bytes(b"first")
    first = observe(catalog, root, "Å.pdf", path)
    path.write_bytes(b"second")
    with monkeypatch.context() as context:
        context.setattr(
            os, "listdir", lambda directory: (_ for _ in ()).throw(OSError())
        )
        second = observe(catalog, root, "Å.pdf", path)
    assert second.attachment_id != first.attachment_id
    first_record = catalog.get_file(root, "Å.pdf")
    second_record = catalog.get_file(root, "Å.pdf")
    path.write_bytes(b"third")

    third = observe(catalog, root, "A\u030a.pdf", path)

    assert third.attachment_id not in {first.attachment_id, second.attachment_id}
    assert catalog.get_file(root, "Å.pdf") == first_record
    assert catalog.get_file(root, "Å.pdf") == second_record
    assert len(catalog.query_files(root=root)) == 3
