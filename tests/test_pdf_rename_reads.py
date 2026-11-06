"""Rename reads a PDF only when the file would get a new name."""

from __future__ import annotations

import pytest
from pdf_identity_fixtures import write_pdf

import adsfolio.pdf.renamer as renamer_module
from adsfolio.pdf.identifier import PdfIdentifier
from adsfolio.pdf.readiness import METADATA_STALE_KEY
from adsfolio.pdf.registry import open_registry_state
from adsfolio.pdf.renamer import PdfRenamer


def _record(path):
    return PdfIdentifier(None).build_record(
        {
            "provider": "crossref",
            "doi": ["10.1234/dust"],
            "author": ["Doe, Jane"],
            "title": ["Dust Tail"],
            "year": "2024",
        },
        path,
    )


def _no_reads(monkeypatch):
    def fail(*_args, **_kwargs):
        pytest.fail("The rename read the PDF")

    monkeypatch.setattr(renamer_module, "sha256_file", fail)
    monkeypatch.setattr(renamer_module, "verify_record_identity", fail)


def test_already_named_pdf_is_not_read(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    path = root / "Dust_Tail.pdf"
    write_pdf(path, doi="10.1234/dust")
    state = open_registry_state(root)
    state.upsert_file(path.name, _record(path))
    _no_reads(monkeypatch)
    renamer = PdfRenamer()
    assert renamer.batch_rename(state, template="{title}", dry_run=True) == []
    assert renamer.unchanged == {"Dust_Tail.pdf": "Dust_Tail.pdf"}
    state.close()


def test_existing_destination_is_reported_without_reading(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    path = root / "download.pdf"
    write_pdf(path, doi="10.1234/dust")
    (root / "Dust_Tail.pdf").write_bytes(b"another copy")
    state = open_registry_state(root)
    state.upsert_file(path.name, _record(path))
    _no_reads(monkeypatch)
    renamer = PdfRenamer()
    assert renamer.batch_rename(state, template="{title}", dry_run=True) == []
    assert renamer.skipped["download.pdf"].startswith("Destination exists:")
    state.close()


def test_stale_record_without_bibcode_is_refused_before_hashing(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    path = root / "scan.pdf"
    write_pdf(path, doi="")
    state = open_registry_state(root)
    state.upsert_file(path.name, {"status": "unresolved", METADATA_STALE_KEY: True})
    _no_reads(monkeypatch)
    monkeypatch.setattr(
        renamer_module,
        "_recover_stale_record",
        lambda *args, **kwargs: pytest.fail("Recovery hashed the PDF"),
    )
    renamer = PdfRenamer()
    assert renamer.batch_rename(state, template="{title}", dry_run=True) == []
    assert "has no ADS bibcode" in renamer.identity_refused["scan.pdf"]
    state.close()


def test_renamed_pdf_is_still_checked(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    path = root / "download.pdf"
    write_pdf(path, doi="10.1234/dust")
    state = open_registry_state(root)
    state.upsert_file(path.name, _record(path))
    checked = []
    verify = renamer_module.verify_record_identity

    def spy(*args, **kwargs):
        checked.append(args[0].name)
        return verify(*args, **kwargs)

    monkeypatch.setattr(renamer_module, "verify_record_identity", spy)
    renamer = PdfRenamer()
    renamer.batch_rename(state, template="{title}", dry_run=True)
    assert checked == ["download.pdf"]
    state.close()
