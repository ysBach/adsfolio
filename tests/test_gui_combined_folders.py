"""Test root-relative filenames in the combined library."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from adsfolio.config import Config
from adsfolio.gui.combined_library import CombinedLibrary, combined_columns
from adsfolio.gui.library import BASE_COLUMNS
from adsfolio.pdf.registry import PdfRegistry


@pytest.fixture
def combined_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    roots = [tmp_path / "first", tmp_path / "second"]
    for root in roots:
        root.mkdir()
    registries = {str(root): PdfRegistry(root) for root in roots}
    files = [
        (roots[0], "papers/alpha/same.pdf", b"alpha"),
        (roots[0], "papers/beta/alpha.pdf", b"beta alpha"),
        (roots[0], "papers/beta/same.pdf", b"shared"),
        (roots[0], "outside.pdf", b"outside"),
        (roots[1], "chapters/intro/same.pdf", b"shared"),
    ]
    for root, relative, data in files:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        stat = path.stat()
        registries[str(root)].upsert_file(
            root=root,
            relative_path=relative,
            current_path=relative,
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            status="unresolved",
        )
    try:
        yield roots, registries, files
    finally:
        for registry in registries.values():
            registry.close()


def test_combined_filename_shows_full_paths_for_equal_basenames(combined_files):
    roots, registries, _ = combined_files
    labels = {str(roots[0]): "First", str(roots[1]): "Second"}
    columns = combined_columns(BASE_COLUMNS, labels)
    filename = next(column for column in columns if column.key == "filename")

    rows = CombinedLibrary(registries, labels).query_files()

    assert {
        (row["root"], row["current_path"]): filename.getter(row) for row in rows
    } == {
        (str(roots[0]), "papers/alpha/same.pdf"): "First / papers/alpha/same.pdf",
        (str(roots[0]), "papers/beta/alpha.pdf"): "First / papers/beta/alpha.pdf",
        (str(roots[0]), "papers/beta/same.pdf"): "First / papers/beta/same.pdf",
        (str(roots[0]), "outside.pdf"): "First / outside.pdf",
        (str(roots[1]), "chapters/intro/same.pdf"): "Second / chapters/intro/same.pdf",
    }


@pytest.mark.parametrize("descending", [False, True])
def test_filename_sort_matches_displayed_paths(combined_files, descending):
    roots, registries, _ = combined_files
    labels = {str(roots[0]): "Shelf", str(roots[1]): "Reading"}
    columns = combined_columns(BASE_COLUMNS, labels)
    filename = next(column for column in columns if column.key == "filename")

    rows = CombinedLibrary(registries, labels).query_files(
        order_by="filename", descending=descending
    )
    displayed = [filename.getter(row) for row in rows]

    assert displayed == sorted(displayed, reverse=descending)


def test_combined_queries_preserve_files_and_existing_filters(combined_files):
    roots, registries, files = combined_files
    hashes = {
        root / relative: hashlib.sha256((root / relative).read_bytes()).hexdigest()
        for root, relative, _ in files
    }
    combined = CombinedLibrary(registries)
    filename = next(
        column
        for column in combined_columns(BASE_COLUMNS, {})
        if column.key == "filename"
    )

    for row in combined.query_files(order_by="filename"):
        filename.getter(row)
    assert [row["current_path"] for row in combined.query_files(search="beta")] == [
        "papers/beta/alpha.pdf",
        "papers/beta/same.pdf",
    ]
    assert combined.count_files_matching(search="beta") == 2
    duplicates = combined.query_files(duplicates_only=True)
    assert {(row["root"], row["current_path"]) for row in duplicates} == {
        (str(roots[0]), "papers/beta/same.pdf"),
        (str(roots[1]), "chapters/intro/same.pdf"),
    }
    assert combined.count_files_matching(duplicates_only=True) == len(duplicates)
    assert {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in hashes
    } == hashes
