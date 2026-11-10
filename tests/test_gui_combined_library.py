"""Combined-library queries sort, search, and find duplicates across library roots.

The queries do not change PDF files, and combined details stay read-only.
"""

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


def test_split_library_column_shows_library_and_path_apart(libraries):
    roots, registries = libraries
    add_pdf(registries[str(roots[0])], roots[0], "same.pdf", b"one", "One")
    row = CombinedLibrary(registries).query_files()[0]
    columns = combined_columns(
        BASE_COLUMNS, {str(roots[0]): "Reading"}, split_library=True
    )
    keys = [column.key for column in columns]
    library = columns[keys.index("library")]
    filename = columns[keys.index("filename")]

    assert keys.index("library") == keys.index("filename") - 1
    assert library.header == "Library"
    assert library.getter(row) == "Reading"
    assert filename.header == "Filename"
    assert filename.getter(row) == "same.pdf"


def test_split_library_sorts_filename_by_path_and_library_by_label(libraries):
    roots, registries = libraries
    add_pdf(registries[str(roots[0])], roots[0], "a.pdf", b"one", "One")
    add_pdf(registries[str(roots[1])], roots[1], "b.pdf", b"two", "Two")
    labels = {str(roots[0]): "Zeta", str(roots[1]): "Alpha"}

    def names(combined, key):
        return [row["current_path"] for row in combined.query_files(order_by=key)]

    joined = CombinedLibrary(registries, labels)
    split = CombinedLibrary(registries, labels, split_library=True)

    # One column sorts "Alpha / b.pdf" before "Zeta / a.pdf".
    assert names(joined, "filename") == ["b.pdf", "a.pdf"]
    assert names(split, "filename") == ["a.pdf", "b.pdf"]
    assert names(split, "library") == ["b.pdf", "a.pdf"]


def test_library_sort_in_one_library_keeps_the_next_sort_key(libraries):
    roots, registries = libraries
    registry = registries[str(roots[0])]
    add_pdf(registry, roots[0], "one.pdf", b"one", "Zulu")
    add_pdf(registry, roots[0], "two.pdf", b"two", "Alpha")

    rows = registry.query_files(order_by="library", secondary_order_by="title")

    assert [row["current_path"] for row in rows] == ["two.pdf", "one.pdf"]
