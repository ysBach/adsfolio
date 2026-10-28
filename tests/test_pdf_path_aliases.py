"""Tests for filesystem path aliases and same-file moves."""

from __future__ import annotations

import os
import unicodedata
from contextlib import closing
from pathlib import Path

import pytest
from pypdf import PdfWriter

from adsfolio.config import Config
from adsfolio.pdf.files import sha256_file
from adsfolio.pdf.registry import open_registry_state
from adsfolio.pdf.renamer import PdfRenamer
from adsfolio.pdf.tracked_move import (
    TrackedMoveConflictError,
    is_same_file_alias,
    move_tracked_file,
)

DOI = "10.1038/s41586-026-00001-x"
BIBCODE = "2026SciA...12i9107S"
DOC = {
    "bibcode": BIBCODE,
    "doi": [DOI],
    "title": ["Nucleosynthetic constraints on Bennu"],
    "author": ["Schönbächler, M."],
    "year": "2026",
}


@pytest.fixture(autouse=True)
def configured(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )


def make_valid_pdf(path: Path, *, doi: str = DOI) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_metadata({"/Title": "Nucleosynthetic constraints", "/doi": doi})
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def register_valid_record(root: Path, name: str, doc: dict) -> tuple[str, dict]:
    path = make_valid_pdf(root / name)
    digest = sha256_file(path)
    with closing(open_registry_state(root)) as state:
        state.upsert_file(
            name,
            {
                "current_filename": name,
                "sha256": digest,
                "status": "resolved",
                "_adsfolio_metadata_sha256": digest,
                "bibcode": doc["bibcode"],
                "doi": doc["doi"],
                "title": doc["title"],
                "author": doc["author"],
                "year": doc["year"],
            },
        )
        state.registry.upsert_ads_documents([doc], source="test")
        state.save()
        record = state.registry.find_file_by_relative_path(root, name)
    return digest, record


def test_is_same_file_alias_unicode_normalization(tmp_path: Path):
    """NFC and NFD spellings of the same directory entry are identified as an alias."""
    nfd_name = unicodedata.normalize("NFD", "SchönbächlerM.pdf")
    nfc_name = unicodedata.normalize("NFC", "SchönbächlerM.pdf")

    source = tmp_path / nfd_name
    source.write_bytes(b"%PDF-test")
    destination = tmp_path / nfc_name

    if not destination.exists():
        pytest.skip("Filesystem does not fold Unicode normalizations")

    assert is_same_file_alias(source, destination) is True
    assert is_same_file_alias(destination, source) is True


def test_is_same_file_alias_rejects_distinct_hardlinks(tmp_path: Path):
    """Distinct hard links sharing an inode are not aliases."""
    source = tmp_path / "original.pdf"
    source.write_bytes(b"%PDF-test")
    hardlink = tmp_path / "hardlink.pdf"
    os.link(source, hardlink)

    assert os.path.samefile(source, hardlink)
    assert is_same_file_alias(source, hardlink) is False
    assert is_same_file_alias(hardlink, source) is False


def test_is_same_file_alias_rejects_symlinks(tmp_path: Path):
    """Symlinks sharing content/target are rejected as aliases."""
    source = tmp_path / "original.pdf"
    source.write_bytes(b"%PDF-test")
    symlink = tmp_path / "symlink.pdf"
    symlink.symlink_to(source)

    assert is_same_file_alias(source, symlink) is False
    assert is_same_file_alias(symlink, source) is False


def test_move_tracked_file_no_ops_on_same_file_alias(tmp_path: Path):
    """move_tracked_file returns source without raising TrackedMoveConflictError on alias."""
    root = tmp_path / "library"
    root.mkdir()
    nfd_name = unicodedata.normalize("NFD", "Schönbächler.pdf")
    nfc_name = unicodedata.normalize("NFC", "Schönbächler.pdf")

    source = root / nfd_name
    make_valid_pdf(source)

    destination = root / nfc_name
    if not destination.exists():
        pytest.skip("Filesystem does not fold Unicode normalizations")

    initial_sha = sha256_file(source)
    with closing(open_registry_state(root)) as state:
        state.upsert_file(
            nfd_name,
            {"current_filename": nfd_name, "sha256": initial_sha},
        )
        before_locations = list(state.registry.catalog.query_files(root=root))

        result = move_tracked_file(state, source, destination)
        assert result == source
        assert source.exists()
        assert sha256_file(source) == initial_sha

        after_locations = list(state.registry.catalog.query_files(root=root))
        assert len(before_locations) == len(after_locations)
        assert before_locations[0]["current_path"] == after_locations[0]["current_path"]


def test_move_tracked_file_raises_conflict_error_on_distinct_file(tmp_path: Path):
    """move_tracked_file raises TrackedMoveConflictError with source and destination attributes."""
    root = tmp_path / "library"
    root.mkdir()
    source = root / "paper1.pdf"
    make_valid_pdf(source)
    destination = root / "paper2.pdf"
    make_valid_pdf(destination, doi="10.1038/other")

    sha_source_before = sha256_file(source)
    sha_dest_before = sha256_file(destination)

    with closing(open_registry_state(root)) as state:
        state.upsert_file(
            source.name,
            {"current_filename": source.name, "sha256": sha_source_before},
        )

        with pytest.raises(TrackedMoveConflictError) as exc_info:
            move_tracked_file(state, source, destination)

        assert isinstance(exc_info.value, FileExistsError)
        assert exc_info.value.source == source
        assert exc_info.value.destination == destination

    assert sha256_file(source) == sha_source_before
    assert sha256_file(destination) == sha_dest_before


def test_renamer_no_ops_on_same_file_alias(tmp_path: Path):
    """PdfRenamer.rename_tracked_file returns source without moving when alias matches."""
    root = tmp_path / "library"
    root.mkdir()
    nfd_name = unicodedata.normalize("NFD", "Schönbächler.pdf")
    nfc_name = unicodedata.normalize("NFC", "Schönbächler.pdf")

    source = root / nfd_name
    make_valid_pdf(source)
    destination = root / nfc_name
    if not destination.exists():
        pytest.skip("Filesystem does not fold Unicode normalizations")

    initial_sha, record = register_valid_record(root, nfd_name, DOC)
    metadata = {**record["metadata"], "sha256": initial_sha, "status": "resolved"}

    with closing(open_registry_state(root)) as state:
        res_live = PdfRenamer().rename_tracked_file(
            state,
            source,
            metadata,
            template="{first_author}",
            char_policy="keep",
            dry_run=False,
        )
        assert res_live == source
        assert source.exists()
        assert sha256_file(source) == initial_sha
