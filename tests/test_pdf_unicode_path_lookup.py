"""Test catalog lookup and rename selection for Unicode path aliases."""

from __future__ import annotations

import os
import unicodedata
from pathlib import Path

import pytest
from pdf_identity_fixtures import confirm_metadata, write_pdf

import adsfolio.pdf.registry as registry_module
from adsfolio.pdf.catalog import PdfCatalog
from adsfolio.pdf.files import sha256_file
from adsfolio.pdf.readiness import METADATA_SHA256_KEY
from adsfolio.pdf.registry import CentralPdfRegistry, CentralPdfRegistryState
from adsfolio.pdf.renamer import PdfRenamer
from adsfolio.pdf.sidecar import Sidecar

BIBCODE = "2024Test....1....1A"
NFD_NAME = "Cafe\u0301.pdf"
NFC_NAME = unicodedata.normalize("NFC", NFD_NAME)


@pytest.fixture
def unicode_aliases(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Simulate normalization-insensitive lstat calls within the fixture root."""
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


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "library"
    root.mkdir()
    catalog = PdfCatalog(tmp_path / "catalog.sqlite")
    monkeypatch.setattr(registry_module, "PdfCatalog", lambda: catalog)
    registry = CentralPdfRegistry(root)
    yield CentralPdfRegistryState(registry)
    registry.close()


def scanned_pdf(state: CentralPdfRegistryState, relative_path: str) -> Path:
    path = state.directory / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    write_pdf(path)
    catalog = state.registry.catalog
    catalog.discover_root(state.directory)
    assert catalog.get_file(state.directory, relative_path) is not None
    digest = sha256_file(path)
    metadata = confirm_metadata(
        path,
        {
            "bibcode": BIBCODE,
            "title": ["Synthetic paper"],
            "author": ["Tester, Ada"],
            "year": "2024",
            METADATA_SHA256_KEY: digest,
        },
    )
    stat = path.stat()
    catalog.observe_file(
        state.directory,
        relative_path,
        sha256=digest,
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        device=stat.st_dev,
        inode=stat.st_ino,
        metadata=metadata,
        status="resolved",
    )
    return path


def copy_location(
    catalog: PdfCatalog, location_id: int, name: str, *, missing: bool
) -> int:
    with catalog._connection:
        cursor = catalog._connection.execute(
            "INSERT INTO file_locations (attachment_id, root_id, relative_path, "
            "size_bytes, mtime_ns, device, inode, current_version_id, "
            "first_seen_at, last_seen_at, missing_at) "
            "SELECT attachment_id, root_id, ?, size_bytes, mtime_ns, device, "
            "inode, current_version_id, first_seen_at, last_seen_at, ? "
            "FROM file_locations WHERE id = ?",
            (name, "2024-01-01T00:00:00Z" if missing else None, location_id),
        )
    return int(cursor.lastrowid)


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("stored_form", ["NFD", "NFC"])
def test_lookup_finds_scanned_location_by_proven_alias(
    state: CentralPdfRegistryState,
    unicode_aliases: None,
    nested: bool,
    stored_form: str,
) -> None:
    original = f"Di\u0301r/{NFD_NAME}" if nested else NFD_NAME
    name = unicodedata.normalize(stored_form, original)
    alias = unicodedata.normalize("NFC" if stored_form == "NFD" else "NFD", original)
    scanned_pdf(state, name)
    catalog = state.registry.catalog
    before = catalog.query_files(root=state.directory, include_missing=True)

    record = catalog.get_file(state.directory, alias)

    assert record is not None
    assert record["relative_path"] == name
    assert record["location_id"] == before[0]["location_id"]
    assert catalog.query_files(root=state.directory, include_missing=True) == before


@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize("stored_form", ["NFD", "NFC"])
def test_selected_alias_renames_scanned_pdf_without_changing_contents(
    state: CentralPdfRegistryState,
    unicode_aliases: None,
    dry_run: bool,
    stored_form: str,
) -> None:
    name = unicodedata.normalize(stored_form, NFD_NAME)
    alias = unicodedata.normalize("NFC" if stored_form == "NFD" else "NFD", name)
    path = scanned_pdf(state, name)
    digest = sha256_file(path)
    untouched = state.directory / "other.txt"
    untouched.write_bytes(b"unrelated synthetic content")

    plans = PdfRenamer().batch_rename(
        state, template="{bibcode}", dry_run=dry_run, current_filenames=[alias]
    )

    assert plans == [(name, f"{BIBCODE}.pdf")]
    target = state.directory / f"{BIBCODE}.pdf"
    if dry_run:
        assert path.exists()
        assert not target.exists()
        assert sha256_file(path) == digest
    else:
        assert not path.exists()
        assert sha256_file(target) == digest
        record = state.registry.catalog.get_file(state.directory, target.name)
        assert record is not None
        assert record["sha256"] == digest
    assert untouched.read_bytes() == b"unrelated synthetic content"


def test_missing_exact_history_takes_priority_over_active_alias(
    state: CentralPdfRegistryState, unicode_aliases: None
) -> None:
    path = scanned_pdf(state, NFD_NAME)
    catalog = state.registry.catalog
    active = catalog.get_file(state.directory, NFD_NAME)
    missing_id = copy_location(catalog, active["location_id"], NFC_NAME, missing=True)
    before = catalog.query_files(root=state.directory, include_missing=True)

    exact = catalog.get_file(state.directory, NFC_NAME)
    assert exact["location_id"] == missing_id
    assert exact["missing_at"] is not None
    renamer = PdfRenamer()
    assert (
        renamer.batch_rename(state, template="{bibcode}", current_filenames=[NFC_NAME])
        == []
    )
    assert renamer.batch_rename(
        state, template="{bibcode}", current_filenames=[NFD_NAME]
    ) == [(NFD_NAME, f"{BIBCODE}.pdf")]
    assert path.exists()
    assert catalog.query_files(root=state.directory, include_missing=True) == before


def test_duplicate_active_aliases_do_not_select_an_ambiguous_location(
    state: CentralPdfRegistryState, unicode_aliases: None
) -> None:
    stored_name = "A\u030a.pdf"
    selected_name = "\u00c5.pdf"
    duplicate_name = "\u212b.pdf"
    scanned_pdf(state, stored_name)
    catalog = state.registry.catalog
    active = catalog.get_file(state.directory, stored_name)
    duplicate_id = copy_location(
        catalog, active["location_id"], duplicate_name, missing=False
    )
    before = catalog.query_files(root=state.directory, include_missing=True)

    assert catalog.get_file(state.directory, selected_name) is None
    assert (
        catalog.get_file(state.directory, duplicate_name)["location_id"] == duplicate_id
    )
    assert (
        PdfRenamer().batch_rename(
            state, template="{bibcode}", current_filenames=[selected_name]
        )
        == []
    )
    assert PdfRenamer().batch_rename(
        state, template="{bibcode}", current_filenames=[stored_name]
    ) == [(stored_name, f"{BIBCODE}.pdf")]
    assert catalog.query_files(root=state.directory, include_missing=True) == before


def test_missing_locations_do_not_resolve_through_an_alias(
    state: CentralPdfRegistryState, unicode_aliases: None
) -> None:
    path = scanned_pdf(state, NFD_NAME)
    catalog = state.registry.catalog
    path.unlink()
    assert catalog.mark_location_missing(state.directory, NFD_NAME)
    before = catalog.query_files(root=state.directory, include_missing=True)

    assert catalog.get_file(state.directory, NFC_NAME) is None
    assert catalog.get_file(state.directory, NFD_NAME)["missing_at"] is not None
    assert (
        PdfRenamer().batch_rename(
            state, template="{bibcode}", current_filenames=[NFC_NAME]
        )
        == []
    )
    assert catalog.query_files(root=state.directory, include_missing=True) == before


def test_inaccessible_directory_does_not_prove_an_alias(
    state: CentralPdfRegistryState,
    unicode_aliases: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scanned_pdf(state, NFD_NAME)
    original_listdir = os.listdir

    def inaccessible_listdir(directory):
        if Path(directory) == state.directory:
            raise PermissionError("synthetic directory access failure")
        return original_listdir(directory)

    monkeypatch.setattr(os, "listdir", inaccessible_listdir)
    assert state.registry.catalog.get_file(state.directory, NFC_NAME) is None
    assert (
        PdfRenamer().batch_rename(
            state, template="{bibcode}", current_filenames=[NFC_NAME]
        )
        == []
    )


@pytest.mark.parametrize("kind", ["file", "hardlink", "symlink"])
def test_distinct_unicode_entries_are_not_aliases(
    state: CentralPdfRegistryState,
    unicode_aliases: None,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    path = scanned_pdf(state, NFD_NAME)
    other = state.directory / "other.bin"
    if kind == "hardlink":
        os.link(path, other)
    elif kind == "symlink":
        other.symlink_to(path.name)
    else:
        other.write_bytes(b"independent synthetic file")
    original_listdir = os.listdir
    original_lstat = Path.lstat

    def listdir_with_distinct_entries(directory):
        names = original_listdir(directory)
        if Path(directory) == state.directory:
            return list(dict.fromkeys([*names, NFD_NAME, NFC_NAME]))
        return names

    def lstat_with_distinct_entry(candidate: Path, *args, **kwargs):
        if candidate == state.directory / NFC_NAME:
            return original_lstat(other, *args, **kwargs)
        return original_lstat(candidate, *args, **kwargs)

    monkeypatch.setattr(os, "listdir", listdir_with_distinct_entries)
    monkeypatch.setattr(Path, "lstat", lstat_with_distinct_entry)
    catalog = state.registry.catalog
    before = catalog.query_files(root=state.directory, include_missing=True)

    assert catalog.get_file(state.directory, NFC_NAME) is None
    assert (
        PdfRenamer().batch_rename(
            state, template="{bibcode}", current_filenames=[NFC_NAME]
        )
        == []
    )
    assert PdfRenamer().batch_rename(
        state, template="{bibcode}", current_filenames=[NFD_NAME]
    ) == [(NFD_NAME, f"{BIBCODE}.pdf")]
    assert catalog.query_files(root=state.directory, include_missing=True) == before


def test_legacy_sidecar_selects_a_proven_alias(
    tmp_path: Path, unicode_aliases: None
) -> None:
    path = tmp_path / NFC_NAME
    write_pdf(path)
    sidecar = Sidecar(tmp_path)
    sidecar.upsert_file(path.name, confirm_metadata(path, {"bibcode": BIBCODE}))

    plans = PdfRenamer().batch_rename(
        sidecar, template="{bibcode}", current_filenames=[NFD_NAME]
    )

    assert plans == [(NFC_NAME, f"{BIBCODE}.pdf")]
