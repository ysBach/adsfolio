"""The combined-library tree proxy groups records into library, folder, and file nodes.

The tests check source-row mapping and that keys, selection, and expansion
survive paging, sorting, and source resets.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QItemSelectionModel, QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtTest import QAbstractItemModelTester, QSignalSpy
from PySide6.QtWidgets import QTreeView

from adsfolio.gui import models
from adsfolio.gui.combined_tree import CombinedTreeProxyModel
from adsfolio.gui.library import BASE_COLUMNS, FileQuery
from adsfolio.gui.models import PAGE_SIZE, FileTableModel

_ROOT_INDEX = QModelIndex()


class _Registry:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records
        self.queries: list[dict[str, Any]] = []

    def query_files(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.queries.append(kwargs)
        records = list(self.records)
        if kwargs.get("order_by") == "filename":
            records.sort(
                key=lambda record: str(record.get("current_path") or ""),
                reverse=kwargs.get("descending", False),
            )
        return records


def _record(root: Any, path: Any, location_id: Any, title: str = "") -> dict[str, Any]:
    return {
        "root": root,
        "current_path": path,
        "location_id": location_id,
        "status": "resolved",
        "metadata": {"title": [title]},
    }


def _walk(model: CombinedTreeProxyModel, parent: QModelIndex = _ROOT_INDEX):
    for row in range(model.rowCount(parent)):
        index = model.index(row, 0, parent)
        yield index
        yield from _walk(model, index)


def _files(model: CombinedTreeProxyModel) -> list[QModelIndex]:
    return [index for index in _walk(model) if model.node_kind(index) == "file"]


def _child(
    model: CombinedTreeProxyModel, parent: QModelIndex, name: str
) -> QModelIndex:
    for row in range(model.rowCount(parent)):
        index = model.index(row, model.filename_column, parent)
        if model.data(index) == name:
            return model.index(row, 0, parent)
    raise AssertionError(f"Missing child {name!r}")


def _finish_loading(qtbot, model: CombinedTreeProxyModel) -> None:
    if model.is_loading:
        with qtbot.waitSignal(model.loading_finished, timeout=5000):
            pass


def test_tree_maps_folders_files_and_roles(qtbot) -> None:
    records = [
        _record("/fiction/a/library", "papers/2026/alpha.pdf", 1, "First"),
        _record("/fiction/a/library", "papers/2025/alpha.pdf", 2, "Second"),
        _record("/fiction/b/library", "papers/2026/alpha.pdf", 1, "Third"),
        _record("/fiction/a/library", "top.pdf", 3, "Fourth"),
    ]
    source = FileTableModel(_Registry(records), FileQuery())
    model = CombinedTreeProxyModel(source)
    _finish_loading(qtbot, model)

    root_a = _child(model, QModelIndex(), "/fiction/a/library")
    root_b = _child(model, QModelIndex(), "/fiction/b/library")
    papers_a = _child(model, root_a, "papers")
    year_a = _child(model, papers_a, "2026")
    leaf_a = model.index(0, 0, year_a)
    papers_b = _child(model, root_b, "papers")
    year_b = _child(model, papers_b, "2026")
    leaf_b = model.index(0, 0, year_b)

    assert model.node_kind(root_a) == "root"
    assert model.node_kind(papers_a) == "directory"
    assert model.node_kind(leaf_a) == "file"
    assert model.is_folder_index(root_a) and model.is_folder_index(papers_a)
    assert not model.is_folder_index(leaf_a)
    assert model.parent(leaf_a) == year_a
    assert model.parent(year_a) == papers_a
    assert model.parent(papers_a) == root_a
    assert not model.parent(root_a).isValid()
    assert model.record_for_index(root_a) is None
    assert model.record_for_index(papers_a) is None
    assert not model.mapToSource(root_a).isValid()
    assert not model.mapToSource(papers_a).isValid()
    assert model.record_for_index(leaf_a) is records[0]
    assert model.record_for_index(leaf_b) is records[2]
    assert model.key_for_index(leaf_a) != model.key_for_index(leaf_b)
    assert model.key_for_index(year_a) != model.key_for_index(year_b)
    assert len(_files(model)) == len(records)

    for row, record in enumerate(records):
        for column in range(source.columnCount()):
            source_index = source.index(row, column)
            proxy_index = model.mapFromSource(source_index)
            assert proxy_index.isValid()
            assert model.mapToSource(proxy_index) == source_index
            assert model.record_for_index(proxy_index) is record
            for role in (
                Qt.DisplayRole,
                Qt.ToolTipRole,
                Qt.ForegroundRole,
                Qt.FontRole,
            ):
                if column == model.filename_column and role in (
                    Qt.DisplayRole,
                    Qt.ToolTipRole,
                ):
                    continue
                assert model.data(proxy_index, role) == source.data(source_index, role)
            assert model.flags(proxy_index) == source.flags(source_index)
    assert model.flags(root_a) == Qt.ItemIsEnabled | Qt.ItemIsSelectable
    assert model.data(root_a) is None
    assert model.data(model.index(root_a.row(), model.filename_column)) == (
        "/fiction/a/library"
    )
    assert model.headerData(model.filename_column, Qt.Horizontal) == source.headerData(
        model.filename_column, Qt.Horizontal
    )
    assert model.rowCount(model.index(root_a.row(), 1)) == 0


def test_root_labels_and_filename_details_update_without_reset(qtbot) -> None:
    records = [
        _record("/fiction/a/library", "papers/2026/same.pdf", 1),
        _record("/fiction/b/library", "other/same.pdf", 1),
    ]
    columns = tuple(
        replace(
            column,
            getter=lambda record: f"source label / {record['current_path']}",
        )
        if column.key == "filename"
        else column
        for column in BASE_COLUMNS
    )
    source = FileTableModel(_Registry(records), FileQuery(), columns)
    model = CombinedTreeProxyModel(
        source,
        root_labels={
            "/fiction/a/library": "PublOCR",
            "/fiction/b/library": "PublOCR (/fiction/b/library)",
        },
    )
    view = QTreeView()
    qtbot.addWidget(view)
    view.setModel(model)
    _finish_loading(qtbot, model)
    root = _child(model, QModelIndex(), "PublOCR")
    other_root = _child(model, QModelIndex(), "PublOCR (/fiction/b/library)")
    index = model.mapFromSource(source.index(0, model.filename_column))
    other_index = model.mapFromSource(source.index(1, model.filename_column))
    assert index.data() == other_index.data() == "same.pdf"
    assert source.index(0, model.filename_column).data() == (
        "source label / papers/2026/same.pdf"
    )
    description = "PublOCR (/fiction/a/library) / papers/2026/same.pdf"
    assert index.data(Qt.ToolTipRole) == description
    assert index.data(Qt.AccessibleTextRole) == description
    assert other_index.data(Qt.ToolTipRole) == (
        "PublOCR (/fiction/b/library) / other/same.pdf"
    )
    view.expand(root)
    view.setCurrentIndex(index)
    persistent = QPersistentModelIndex(index)
    resets = QSignalSpy(model.modelReset)
    model.set_root_labels({"/fiction/a/library": "Reading"})
    assert resets.count() == 0
    assert persistent.isValid()
    assert view.currentIndex() == QModelIndex(persistent)
    assert view.isExpanded(root)
    assert model.index(root.row(), model.filename_column).data() == "Reading"
    assert (
        model.index(other_root.row(), model.filename_column).data()
        == "/fiction/b/library"
    )
    assert (
        index.data(Qt.ToolTipRole)
        == "Reading (/fiction/a/library) / papers/2026/same.pdf"
    )
    assert model.mapToSource(index) == source.index(0, model.filename_column)


def test_offscreen_view_expands_nested_folders_and_selects_file(qtbot, qtlog) -> None:
    source = FileTableModel(
        _Registry([_record("/fiction/library", "a/b/paper.pdf", 4)]), FileQuery()
    )
    model = CombinedTreeProxyModel(source)
    _tester = QAbstractItemModelTester(
        model, QAbstractItemModelTester.FailureReportingMode.Warning
    )
    view = QTreeView()
    qtbot.addWidget(view)
    view.setModel(model)
    view.setRootIsDecorated(True)
    view.setTreePosition(model.filename_column)
    view.show()
    _finish_loading(qtbot, model)

    root = model.index(0, 0)
    folder = model.index(0, 0, root)
    subfolder = model.index(0, 0, folder)
    leaf = model.index(0, 0, subfolder)
    for index in (root, folder, subfolder):
        view.expand(index)
        assert view.isExpanded(index)
    view.selectionModel().setCurrentIndex(
        leaf, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows
    )
    assert model.record_for_index(view.currentIndex())["location_id"] == 4
    view.collapse(folder)
    assert not view.isExpanded(folder)
    assert view.isExpanded(root)
    assert not [record for record in qtlog.records if "FAIL!" in record.message]


@pytest.mark.parametrize(
    "path",
    [
        None,
        "",
        "../outside.pdf",
        "a/../outside.pdf",
        "/outside.pdf",
        r"C:\outside.pdf",
        "bad\x00name.pdf",
        42,
    ],
)
def test_invalid_paths_remain_files_under_their_root(qtbot, path: Any) -> None:
    record = _record("/fiction/library", path, 12)
    source = FileTableModel(_Registry([record]), FileQuery())
    model = CombinedTreeProxyModel(source)
    _finish_loading(qtbot, model)
    root = model.index(0, 0)
    leaf = model.index(0, 0, root)
    assert model.rowCount(root) == 1
    assert model.node_kind(leaf) == "file"
    assert model.record_for_index(leaf) is record
    assert model.mapToSource(leaf) == source.index(0, 0)


def test_unknown_roots_and_duplicate_ids_never_lose_records(qtbot) -> None:
    records = [
        _record(None, "a/same.pdf", 1),
        _record({}, "a/same.pdf", 1),
        _record("/fiction/library", None, None),
        _record("/fiction/library", None, None),
        _record(Path("/fiction/library"), "a/same.pdf", 1),
    ]
    source = FileTableModel(_Registry(records), FileQuery())
    model = CombinedTreeProxyModel(source)
    _finish_loading(qtbot, model)
    files = _files(model)
    assert len(files) == len(records)
    assert [
        model.record_for_index(model.mapFromSource(source.index(i, 0)))
        for i in range(5)
    ] == records
    unknown = _child(model, QModelIndex(), "Unknown library")
    assert model.node_kind(unknown) == "root"
    duplicate = model.key_for_index(model.mapFromSource(source.index(0, 0)))
    assert duplicate == model.key_for_index(model.mapFromSource(source.index(1, 0)))
    assert not model.index_for_key(duplicate).isValid()
    assert model.key_for_index(model.mapFromSource(source.index(2, 0))) is None
    assert model.index_for_key(
        model.key_for_index(model.mapFromSource(source.index(4, 0)))
    ).isValid()


def test_pages_preserve_selection_and_expansion_without_a_reset(qtbot, qtlog) -> None:
    records = [
        _record("/fiction/library", f"papers/{i:04d}.pdf", i, f"Title {i}")
        for i in range(2 * PAGE_SIZE + 3)
    ]
    registry = _Registry(records)
    source = FileTableModel(registry, FileQuery())
    model = CombinedTreeProxyModel(source)
    _tester = QAbstractItemModelTester(
        model, QAbstractItemModelTester.FailureReportingMode.Warning
    )
    view = QTreeView()
    qtbot.addWidget(view)
    view.setModel(model)
    view.setRootIsDecorated(True)
    view.setTreePosition(model.filename_column)
    view.show()
    root = model.index(0, 0)
    folder = model.index(0, 0, root)
    chosen = model.mapFromSource(source.index(5, 0))
    persistent = QPersistentModelIndex(chosen)
    view.expand(root)
    view.expand(folder)
    view.selectionModel().setCurrentIndex(
        chosen, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows
    )
    resets = QSignalSpy(model.modelReset)
    inserts = QSignalSpy(model.rowsInserted)
    assert source.rowCount() == PAGE_SIZE
    _finish_loading(qtbot, model)

    assert source.rowCount() == len(records)
    assert not source.canFetchMore()
    assert len(registry.queries) == 1
    assert resets.count() == 0
    assert inserts.count() >= 2
    assert persistent.isValid()
    assert view.currentIndex() == QModelIndex(persistent)
    assert view.isExpanded(root) and view.isExpanded(folder)
    assert model.record_for_index(view.currentIndex()) is records[5]
    assert (
        model.record_for_index(model.mapFromSource(source.index(len(records) - 1, 0)))
        is records[-1]
    )
    assert len(_files(model)) == len(records)
    assert not [record for record in qtlog.records if "FAIL!" in record.message]


def test_page_boundaries_merge_folders_and_keep_source_order(
    qtbot, qtlog, monkeypatch
) -> None:
    monkeypatch.setattr(models, "PAGE_SIZE", 2)
    records = [
        _record("/fiction/one", "a/one.pdf", 1),
        _record("/fiction/one", "b/two.pdf", 2),
        _record("/fiction/one", "a/three.pdf", 3),
        _record("/fiction/two", "a/four.pdf", 1),
        _record("/fiction/one", "b/five.pdf", 5),
        _record("/fiction/one", "a/nested/six.pdf", 6),
    ]
    source = FileTableModel(_Registry(records), FileQuery())
    model = CombinedTreeProxyModel(source)
    _tester = QAbstractItemModelTester(
        model, QAbstractItemModelTester.FailureReportingMode.Warning
    )
    view = QTreeView()
    qtbot.addWidget(view)
    view.setModel(model)
    root = _child(model, QModelIndex(), "/fiction/one")
    folder = _child(model, root, "a")
    view.expand(root)
    view.expand(folder)
    chosen = model.mapFromSource(source.index(0, 0))
    view.setCurrentIndex(chosen)
    _finish_loading(qtbot, model)

    assert source.rowCount() == len(records)
    assert view.currentIndex() == chosen
    assert view.isExpanded(root) and view.isExpanded(folder)
    assert [model.node_kind(model.index(row, 0, folder)) for row in range(3)] == [
        "file",
        "file",
        "directory",
    ]
    assert [
        model.record_for_index(model.index(row, 0, folder))["location_id"]
        for row in range(2)
    ] == [1, 3]
    assert all(
        model.mapToSource(model.mapFromSource(source.index(i, 0))) == source.index(i, 0)
        for i in range(len(records))
    )
    assert not [record for record in qtlog.records if "FAIL!" in record.message]


def test_sort_and_reload_restore_folder_and_late_page_file_keys(qtbot) -> None:
    records = [
        _record("/fiction/library", f"papers/{i:04d}.pdf", i)
        for i in range(PAGE_SIZE + 5)
    ]
    registry = _Registry(records)
    source = FileTableModel(registry, FileQuery())
    model = CombinedTreeProxyModel(source)
    _finish_loading(qtbot, model)
    root = model.index(0, 0)
    folder = model.index(0, 0, root)
    folder_key = model.key_for_index(folder)
    file_key = model.key_for_index(model.mapFromSource(source.index(2, 0)))
    resets = QSignalSpy(model.modelReset)
    model.sort(model.filename_column, Qt.DescendingOrder)
    assert source.query.order_by == "filename"
    assert source.query.descending
    assert resets.count() == 1
    assert not model.index_for_key(file_key).isValid()
    _finish_loading(qtbot, model)
    restored_folder = model.index_for_key(folder_key)
    restored_file = model.index_for_key(file_key)
    assert restored_folder.isValid() and restored_file.isValid()
    assert model.parent(restored_file) == restored_folder
    assert model.record_for_index(restored_file) is records[2]
    assert [
        model.record_for_index(index)["location_id"] for index in _files(model)
    ] == list(reversed(range(len(records))))
    assert len(registry.queries) == 2

    registry.records = [records[2], records[0]]
    source.set_query(FileQuery())
    _finish_loading(qtbot, model)
    assert model.record_for_index(model.index_for_key(file_key)) is records[2]
    assert len(_files(model)) == 2


def test_view_restores_expansion_and_late_selection_after_source_reset(qtbot) -> None:
    records = [
        _record("/fiction/library", f"a/b/{i:04d}.pdf", i) for i in range(PAGE_SIZE + 1)
    ]
    source = FileTableModel(_Registry(records), FileQuery())
    model = CombinedTreeProxyModel(source)
    view = QTreeView()
    qtbot.addWidget(view)
    view.setModel(model)
    view.setRootIsDecorated(True)
    view.setTreePosition(model.filename_column)
    _finish_loading(qtbot, model)
    folders = [model.index(0, 0)]
    folders.append(model.index(0, 0, folders[-1]))
    folders.append(model.index(0, 0, folders[-1]))
    for folder in folders:
        view.expand(folder)
    view.setCurrentIndex(model.mapFromSource(source.index(0, 0)))
    saved: dict[str, Any] = {}

    def save_state() -> None:
        saved["folders"] = [
            model.key_for_index(index)
            for index in _walk(model)
            if model.is_folder_index(index) and view.isExpanded(index)
        ]
        saved["file"] = model.key_for_index(view.currentIndex())

    def restore_state() -> None:
        for key in saved["folders"]:
            index = model.index_for_key(key)
            if index.isValid():
                view.expand(index)
        index = model.index_for_key(saved["file"])
        if index.isValid():
            view.setCurrentIndex(index)

    model.modelAboutToBeReset.connect(save_state)
    model.loading_finished.connect(restore_state)
    model.sort(model.filename_column, Qt.DescendingOrder)
    assert not model.index_for_key(saved["file"]).isValid()
    _finish_loading(qtbot, model)
    assert model.record_for_index(view.currentIndex()) is records[0]
    assert all(view.isExpanded(model.index_for_key(key)) for key in saved["folders"])


def test_source_presentation_updates_keep_selection_and_forward_signals(qtbot) -> None:
    record = _record("/fiction/library", "a/file.pdf", 1, "Before")
    source = FileTableModel(_Registry([record]), FileQuery())
    model = CombinedTreeProxyModel(source)
    _finish_loading(qtbot, model)
    leaf = model.mapFromSource(source.index(0, 0))
    key = model.key_for_index(leaf)
    changes = QSignalSpy(model.dataChanged)
    resets = QSignalSpy(model.modelReset)
    record["metadata"]["title"] = ["After"]
    source.dataChanged.emit(source.index(0, 0), source.index(0, 0), [Qt.DisplayRole])
    assert model.index_for_key(key).data() == "After"
    assert changes.count() == 1
    source.set_timestamp_format("%Y")
    assert changes.count() >= 2
    assert resets.count() == 0

    record["current_path"] = "b/file.pdf"
    source.dataChanged.emit(source.index(0, 0), source.index(0, 0), [Qt.DisplayRole])
    assert resets.count() == 1
    _finish_loading(qtbot, model)
    moved = model.index_for_key(key)
    assert (
        model.data(
            model.index(
                model.parent(moved).row(),
                model.filename_column,
                model.parent(model.parent(moved)),
            )
        )
        == "b"
    )
    assert model.record_for_index(moved) is record


def test_column_reset_and_source_replacement(qtbot) -> None:
    first = FileTableModel(_Registry([_record("/first", "a.pdf", 1)]), FileQuery())
    second = FileTableModel(_Registry([_record("/second", "b.pdf", 2)]), FileQuery())
    model = CombinedTreeProxyModel(first)
    _finish_loading(qtbot, model)
    file_key = model.key_for_index(model.mapFromSource(first.index(0, 0)))
    first.set_columns(tuple(reversed(BASE_COLUMNS)))
    _finish_loading(qtbot, model)
    assert model.filename_column == next(
        index for index, column in enumerate(first.columns) if column.key == "filename"
    )
    assert model.index_for_key(file_key).isValid()

    model.setSourceModel(second)
    _finish_loading(qtbot, model)
    assert not model.index_for_key(file_key).isValid()
    assert (
        model.record_for_index(model.mapFromSource(second.index(0, 0)))["location_id"]
        == 2
    )
    first.set_query(FileQuery(search="old source"))
    assert len(_files(model)) == 1
    assert not model.mapFromSource(first.index(0, 0)).isValid()
    model.setSourceModel(None)
    assert model.rowCount() == 0
    assert model.columnCount() == 0


def test_stalled_source_reports_failure_without_a_loop(qtbot) -> None:
    class _StalledModel(FileTableModel):
        stalled = True

        def canFetchMore(self, parent: QModelIndex = _ROOT_INDEX) -> bool:
            return (
                not parent.isValid() if self.stalled else super().canFetchMore(parent)
            )

        def fetchMore(self, parent: QModelIndex = _ROOT_INDEX) -> None:
            if not self.stalled:
                super().fetchMore(parent)

    records = [_record("/fiction", f"{i}.pdf", i) for i in range(PAGE_SIZE + 1)]
    source = _StalledModel(_Registry(records), FileQuery())
    model = CombinedTreeProxyModel(source)
    resets = QSignalSpy(model.modelReset)
    with qtbot.waitSignal(model.loading_failed, timeout=1000) as signal:
        pass
    assert signal.args == ["The source did not load the next snapshot page."]
    assert not model.is_loading
    assert len(_files(model)) == PAGE_SIZE
    source.stalled = False
    model.load_remaining()
    _finish_loading(qtbot, model)
    assert len(_files(model)) == PAGE_SIZE + 1
    assert resets.count() == 0
