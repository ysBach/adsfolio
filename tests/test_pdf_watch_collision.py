"""Tests for watch collision outcomes and same-file aliases."""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest
from test_pdf_watcher import (
    _Identifier,
    _pdf,
    _rec,
    _state,
    _watch_event,
)

from adsfolio.pdf.files import sha256_file
from adsfolio.pdf.identify_settings import IdentifySettings
from adsfolio.pdf.rename_settings import RenameSettings
from adsfolio.pdf.renamer import PdfRenamer
from adsfolio.pdf.watch_session import WatchState
from adsfolio.pdf.watcher import PdfWatcher


def test_process_event_real_collision_sets_conflict_path(monkeypatch, tmp_path: Path):
    """A distinct collision reports WatchState.FAILED, conflict_path, and preserves source and target bytes."""
    root = tmp_path / "library"
    source = root / "incoming.pdf"
    occupied = root / "2025RNAAS...9..338Z.pdf"

    data_source = _pdf(source, "2025RNAAS...9..338Z")
    data_occupied = b"%PDF-occupied-target-bytes"
    occupied.write_bytes(data_occupied)

    sha_source_before = sha256_file(source)
    sha_occupied_before = sha256_file(occupied)

    state = _state(monkeypatch, root)
    watcher = PdfWatcher(
        _Identifier(_rec("2025RNAAS...9..338Z")),
        PdfRenamer(),
    )

    outcome = watcher.process_event(
        _watch_event(root, source),
        state,
        RenameSettings(template="{bibcode}"),
        IdentifySettings(),
    )

    assert outcome.event.state is WatchState.FAILED
    assert outcome.destination == source
    assert outcome.conflict_path == occupied
    assert str(occupied) in (outcome.message or "")

    # Content hashes of both files must remain intact
    assert sha256_file(source) == sha_source_before
    assert sha256_file(occupied) == sha_occupied_before
    assert source.read_bytes() == data_source
    assert occupied.read_bytes() == data_occupied
    state.close()


def test_process_event_same_file_alias_returns_resolved(monkeypatch, tmp_path: Path):
    """An arrival naming an NFC/NFD alias of an existing record returns WatchState.RESOLVED without mutating bytes."""
    root = tmp_path / "library"
    root.mkdir(parents=True, exist_ok=True)
    nfd_name = unicodedata.normalize("NFD", "Schönbächler.pdf")
    nfc_name = unicodedata.normalize("NFC", "Schönbächler.pdf")

    source = root / nfd_name
    data = _pdf(source, "2026SciA...12i9107S")

    # If the filesystem does not alias Unicode variants, skip
    alias_target = root / nfc_name
    if not alias_target.exists():
        pytest.skip("Filesystem does not fold Unicode normalizations")

    sha_before = sha256_file(source)
    state = _state(monkeypatch, root)
    watcher = PdfWatcher(
        _Identifier(_rec("2026SciA...12i9107S", author=["Schönbächler, M."])),
        PdfRenamer(),
    )

    outcome = watcher.process_event(
        _watch_event(root, source),
        state,
        RenameSettings(template="{first_author}"),
        IdentifySettings(),
    )

    assert outcome.event.state is WatchState.RESOLVED
    assert outcome.destination == source
    assert outcome.conflict_path is None
    assert sha256_file(source) == sha_before
    assert source.read_bytes() == data
    state.close()
