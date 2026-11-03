"""Enumerate PDF files, and move PDFs without overwriting a destination or losing the
source bytes or catalog history.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from adsfolio.pdf import files
from adsfolio.pdf.files import iter_pdf_paths


def test_iter_pdf_paths_matches_suffix_case_insensitively(tmp_path: Path) -> None:
    (tmp_path / "lower.pdf").write_bytes(b"x")
    (tmp_path / "UPPER.PDF").write_bytes(b"x")
    (tmp_path / "notes.txt").write_bytes(b"x")
    nested = tmp_path / "sub"
    nested.mkdir()
    (nested / "Mixed.Pdf").write_bytes(b"x")

    flat = {p.name for p in iter_pdf_paths(tmp_path, recursive=False)}
    deep = {p.name for p in iter_pdf_paths(tmp_path, recursive=True)}

    assert flat == {"lower.pdf", "UPPER.PDF"}
    assert deep == {"lower.pdf", "UPPER.PDF", "Mixed.Pdf"}


def test_iter_pdf_paths_symlink_scope(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "target.pdf").write_bytes(b"x")
    (outside / "inside-dir.pdf").write_bytes(b"x")
    (root / "link.pdf").symlink_to(outside / "target.pdf")
    (root / "dangling.pdf").symlink_to(outside / "missing.pdf")
    (root / "linked-dir").symlink_to(outside, target_is_directory=True)
    (root / "dir.pdf").mkdir()

    names = {p.name for p in iter_pdf_paths(root, recursive=True)}

    assert names == {"link.pdf"}


def test_iter_pdf_paths_propagates_listing_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "a.pdf").write_bytes(b"x")
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    real_scandir = os.scandir

    def deny(path):
        if Path(path) == blocked:
            raise PermissionError("denied")
        return real_scandir(path)

    monkeypatch.setattr(files.os, "scandir", deny)

    with pytest.raises(PermissionError):
        list(iter_pdf_paths(tmp_path, recursive=True))
    assert [p.name for p in iter_pdf_paths(tmp_path, recursive=False)] == ["a.pdf"]


def test_unresolved_move_does_not_overwrite_a_racing_destination(tmp_path, monkeypatch):
    from adsfolio.pdf import cli_common

    source = tmp_path / "paper.pdf"
    source.write_bytes(b"Original PDF bytes")
    output = tmp_path / "unresolved"
    output.mkdir()
    destination = output / source.name

    def racing_destination(directory, filename):
        destination.write_bytes(b"Other PDF bytes")
        return destination

    monkeypatch.setattr(cli_common, "unique_destination", racing_destination)

    with pytest.raises(FileExistsError):
        cli_common.handle_unresolved(source, output, apply=True)
    assert source.read_bytes() == b"Original PDF bytes"
    assert destination.read_bytes() == b"Other PDF bytes"


def test_exclusive_move_preserves_source_when_unlink_fails(tmp_path, monkeypatch):
    source = tmp_path / "source.pdf"
    destination = tmp_path / "destination.pdf"
    source.write_bytes(b"Original bytes")
    real_unlink = Path.unlink

    def unlink(path, *args, **kwargs):
        if path == source:
            raise PermissionError("Source is busy")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    with pytest.raises(PermissionError):
        files.move_file_exclusive(source, destination)
    assert source.read_bytes() == b"Original bytes"
    assert not destination.exists()


def test_external_tracked_move_rolls_back_when_history_update_fails(
    tmp_path, monkeypatch
):
    from adsfolio.pdf.registry import open_registry_state
    from adsfolio.pdf.tracked_move import move_tracked_file_outside_root

    root = tmp_path / "library"
    root.mkdir()
    source = root / "paper.pdf"
    source.write_bytes(b"Keep original bytes")
    destination = tmp_path / "outside/paper.pdf"
    state = open_registry_state(root)
    state.upsert_file(
        source.name, {"sha256": files.sha256_file(source), "status": "unresolved"}
    )
    before = state.get_file(source.name)

    def fail(*args):
        raise RuntimeError("Catalog write failed")

    monkeypatch.setattr(state.registry.catalog, "mark_location_missing", fail)
    try:
        with pytest.raises(RuntimeError, match="Catalog write failed"):
            move_tracked_file_outside_root(state, source, destination)
        assert source.read_bytes() == b"Keep original bytes"
        assert not destination.exists()
        assert state.get_file(source.name) == before
    finally:
        state.close()
