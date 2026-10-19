"""Tests for shared PDF file enumeration."""

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
