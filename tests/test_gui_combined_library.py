"""Tests for read-only queries across PDF library roots."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from adsfolio.config import Config
from adsfolio.gui.combined_library import CombinedLibrary, combined_columns
from adsfolio.gui.library import BASE_COLUMNS
from adsfolio.pdf.registry import PdfRegistry


@pytest.fixture
def libraries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    roots = [tmp_path / "first", tmp_path / "second"]
    for root in roots:
        root.mkdir()
    registries = {str(root): PdfRegistry(root) for root in roots}
    try:
        yield roots, registries
    finally:
        for registry in registries.values():
            registry.close()


def add_pdf(registry: PdfRegistry, root: Path, name: str, data: bytes, title: str):
    path = root / name
    path.write_bytes(data)
    stat = path.stat()
    registry.upsert_file(
        root=root,
        relative_path=name,
        current_path=name,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        status="unresolved",
        metadata={"title": title},
    )
    return path


def test_combined_query_sorts_and_searches_across_libraries(libraries):
    roots, registries = libraries
    first = add_pdf(registries[str(roots[0])], roots[0], "one.pdf", b"one", "Zulu")
    second = add_pdf(registries[str(roots[1])], roots[1], "two.pdf", b"two", "Alpha")
    before = {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (first, second)
    }
    combined = CombinedLibrary(registries)

    rows = combined.query_files(order_by="title")

    assert [row["metadata"]["title"] for row in rows] == ["Alpha", "Zulu"]
    assert [row["root"] for row in rows] == [str(roots[1]), str(roots[0])]
    assert combined.query_files(search="Alpha")[0]["root"] == str(roots[1])
    assert len(combined.query_files(limit=1, offset=1)) == 1
    assert {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in before
    } == before


def test_combined_duplicates_include_matches_in_different_roots(libraries):
    roots, registries = libraries
    for root in roots:
        add_pdf(registries[str(root)], root, "same.pdf", b"same", "Same")
    add_pdf(registries[str(roots[0])], roots[0], "other.pdf", b"other", "Other")

    rows = CombinedLibrary(registries).query_files(duplicates_only=True)

    assert len(rows) == 2
    assert {row["root"] for row in rows} == {str(root) for root in roots}


def test_combined_reports_missing_roots_and_keeps_available_results(libraries):
    roots, registries = libraries
    add_pdf(registries[str(roots[0])], roots[0], "one.pdf", b"one", "One")
    roots[1].rmdir()
    combined = CombinedLibrary(registries)

    assert combined.unavailable_roots == (str(roots[1]),)
    assert combined.count_files_matching() == 1


def test_combined_filename_identifies_its_original_library(libraries):
    roots, registries = libraries
    add_pdf(registries[str(roots[0])], roots[0], "same.pdf", b"one", "One")
    row = CombinedLibrary(registries).query_files()[0]
    columns = combined_columns(BASE_COLUMNS, {str(roots[0]): "Reading"})
    filename = next(column for column in columns if column.key == "filename")

    assert filename.header == "Library / Filename"
    assert filename.getter(row) == "Reading / same.pdf"


def test_combined_details_remain_readable_without_edit_actions(qtbot, tmp_path):
    pytest.importorskip("PySide6")
    from adsfolio.gui.detail_pane import DetailPane

    pane = DetailPane()
    qtbot.addWidget(pane)
    record = {"current_path": "one.pdf", "metadata": {"title": "One"}}
    pane.set_record(record, tmp_path)
    pane.set_read_only(True)

    assert pane.isEnabled()
    assert not pane.update_button.isEnabled()
    assert not pane.manual_metadata_button.isEnabled()
    assert pane._row_widgets["title"]._editable is False
    assert pane._row_widgets["title"]._lockable is False

    pane.set_read_only(False)
    assert pane.update_button.isEnabled()
    assert pane.manual_metadata_button.isEnabled()
    assert pane._row_widgets["title"]._editable is True
