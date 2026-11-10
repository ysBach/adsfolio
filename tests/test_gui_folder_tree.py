"""One library shows its folders, and View > Show Folders turns them off."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QCoreApplication,
    QEvent,
    QItemSelectionModel,
    QModelIndex,
    QObject,
    Qt,
)
from PySide6.QtWidgets import QApplication
from test_gui_main_window_watch import _window_factory

from adsfolio.gui.config import GuiConfig, RootEntry
from adsfolio.gui.library import LibraryView


def _library(tmp_path: Path) -> Path:
    root = tmp_path / "library"
    (root / "chapter").mkdir(parents=True)
    (root / "top.pdf").write_bytes(b"%PDF-1.4\ntop")
    (root / "chapter" / "inner.pdf").write_bytes(b"%PDF-1.4\ninner")
    return root


def _window(monkeypatch, qtbot, tmp_path, **config):
    root = _library(tmp_path)
    window, _controller, _saves = _window_factory(
        monkeypatch,
        qtbot,
        GuiConfig(roots=[RootEntry(str(root), recursive=True)], **config),
    )
    tree = window.combined.tree_model
    while tree is not None and tree.is_loading:
        tree._load_next_page()
    QApplication.processEvents()
    return window, root.resolve()


def _top_level(window) -> dict[str, str]:
    model = window.table.model()
    column = window.table.treePosition()
    kinds = {}
    for row in range(model.rowCount()):
        index = model.index(row, column)
        kinds[str(index.data())] = model.node_kind(index)
    return kinds


def test_one_library_shows_folders_without_a_library_node(monkeypatch, qtbot, tmp_path):
    window, _root = _window(monkeypatch, qtbot, tmp_path)

    assert not window.combined.enabled
    assert window.folders_action.isChecked()
    assert window.table.model() is window.combined.tree_model
    assert _top_level(window) == {"chapter": "directory", "top.pdf": "file"}


def test_show_folders_off_gives_a_flat_list_and_is_remembered(
    monkeypatch, qtbot, tmp_path
):
    window, _root = _window(monkeypatch, qtbot, tmp_path)

    window.folders_action.trigger()

    assert not window.config.show_folders
    assert window.table.model() is window.model
    assert window.model.rowCount() == 2

    window.folders_action.trigger()

    assert window.config.show_folders
    assert window.table.model() is window.combined.tree_model


def test_saved_show_folders_off_starts_flat(monkeypatch, qtbot, tmp_path):
    window, _root = _window(monkeypatch, qtbot, tmp_path, show_folders=False)

    assert not window.folders_action.isChecked()
    assert window.table.model() is window.model


def test_duplicates_view_stays_flat(monkeypatch, qtbot, tmp_path):
    window, _root = _window(monkeypatch, qtbot, tmp_path)

    window._select_view(LibraryView.DUPLICATES)

    assert window.table.model() is window.model


def test_selection_in_a_subfolder_shows_its_record(monkeypatch, qtbot, tmp_path):
    window, root = _window(monkeypatch, qtbot, tmp_path)
    row = next(
        row
        for row in range(window.model.rowCount())
        if window.model.record_at(row)["current_path"] == "chapter/inner.pdf"
    )
    index = window._view_table_index(window.model.index(row, 0))

    assert index.isValid()
    assert index.parent().isValid()
    window.table.setCurrentIndex(index)

    assert window._selected_record()["current_path"] == "chapter/inner.pdf"
    assert window.detail._root == root


def test_show_folders_round_trip_preserves_selected_current_and_detail(
    monkeypatch, qtbot, tmp_path
):
    window, root = _window(monkeypatch, qtbot, tmp_path)
    row = next(
        row
        for row in range(window.model.rowCount())
        if window.model.record_at(row)["current_path"] == "chapter/inner.pdf"
    )
    index = window._view_table_index(window.model.index(row, 0))
    window.table.selectionModel().select(
        index, QItemSelectionModel.Select | QItemSelectionModel.Rows
    )
    window.table.selectionModel().setCurrentIndex(index, QItemSelectionModel.NoUpdate)

    window._toggle_folders(False)
    assert _selected_paths(window) == {"chapter/inner.pdf"}
    assert window._selected_record()["current_path"] == "chapter/inner.pdf"
    assert window.detail._root == root

    window._toggle_folders(True)
    assert _selected_paths(window) == {"chapter/inner.pdf"}
    assert window._selected_record()["current_path"] == "chapter/inner.pdf"
    assert window.detail._root == root


def test_show_folders_disposes_displaced_owned_selection_models(
    monkeypatch, qtbot, tmp_path
):
    window, _root = _window(monkeypatch, qtbot, tmp_path)
    destroyed = []
    # Startup can replace the table model. Delete those selection models first.
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()
    owned_models = [
        selection
        for selection in window.table.findChildren(QItemSelectionModel)
        if selection.parent() == window.table
    ]
    baseline = len(owned_models)
    assert baseline == 1
    for _ in range(20):
        old_selection = window.table.selectionModel()
        old_selection.destroyed.connect(lambda *_: destroyed.append(True))
        window.folders_action.trigger()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QApplication.processEvents()
        owned_models = [
            selection
            for selection in window.table.findChildren(QItemSelectionModel)
            if selection.parent() == window.table
        ]
        assert len(owned_models) == baseline

    assert len(destroyed) == 20
    index = window._view_table_index(window.model.index(0, 0))
    window.table.selectionModel().select(
        index, QItemSelectionModel.Select | QItemSelectionModel.Rows
    )
    assert _selected_paths(window) == {window.model.record_at(0)["current_path"]}


def test_model_switch_preserves_shared_selection_model_ownership(
    monkeypatch, qtbot, tmp_path
):
    window, _root = _window(monkeypatch, qtbot, tmp_path)
    owner = QObject(window)
    shared = QItemSelectionModel(window.model, owner)
    destroyed = []
    shared.destroyed.connect(lambda *_: destroyed.append(True))
    window.table.setSelectionModel(shared)

    window._set_table_model(window.combined.tree_model)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()

    assert not destroyed
    assert shared.parent() == owner
    index = window._view_table_index(window.model.index(0, 0))
    window.table.selectionModel().select(
        index, QItemSelectionModel.Select | QItemSelectionModel.Rows
    )
    assert _selected_paths(window) == {window.model.record_at(0)["current_path"]}


def test_show_folders_preserves_multiselect_in_closed_folders(
    monkeypatch, qtbot, tmp_path
):
    window, _root = _window(monkeypatch, qtbot, tmp_path)
    window.table.collapseAll()
    indices = [
        window._view_table_index(window.model.index(row, 0))
        for row in range(window.model.rowCount())
    ]
    for index in indices:
        window.table.selectionModel().select(
            index, QItemSelectionModel.Select | QItemSelectionModel.Rows
        )
    window.table.selectionModel().setCurrentIndex(
        indices[-1], QItemSelectionModel.NoUpdate
    )

    window._toggle_folders(False)
    assert _selected_paths(window) == {"top.pdf", "chapter/inner.pdf"}
    assert len(window.table.selectionModel().selection()) == 1
    window._toggle_folders(True)
    assert _selected_paths(window) == {"top.pdf", "chapter/inner.pdf"}
    folder = next(
        window.table.model().index(row, 0)
        for row in range(window.table.model().rowCount())
        if window.table.model().node_kind(window.table.model().index(row, 0))
        == "directory"
    )
    assert not window.table.isExpanded(folder)


def test_show_folders_keeps_unselected_current_with_nondefault_sort(
    monkeypatch, qtbot, tmp_path
):
    window, _root = _window(monkeypatch, qtbot, tmp_path)
    window.model.sort(0, Qt.DescendingOrder)
    QApplication.processEvents()
    selected_row = next(
        row
        for row in range(window.model.rowCount())
        if window.model.record_at(row)["current_path"] == "top.pdf"
    )
    current_row = next(
        row
        for row in range(window.model.rowCount())
        if window.model.record_at(row)["current_path"] == "chapter/inner.pdf"
    )
    selected = window._view_table_index(window.model.index(selected_row, 0))
    current = window._view_table_index(
        window.model.index(current_row, min(1, window.model.columnCount() - 1))
    )
    window.table.selectionModel().select(
        selected, QItemSelectionModel.Select | QItemSelectionModel.Rows
    )
    window.table.selectionModel().setCurrentIndex(current, QItemSelectionModel.NoUpdate)
    assert window.table.currentIndex().column() == 1

    window._toggle_folders(False)
    assert _selected_paths(window) == {"top.pdf"}
    assert window._selected_record()["current_path"] == "chapter/inner.pdf"
    assert window.table.currentIndex().isValid()

    window._toggle_folders(True)
    assert _selected_paths(window) == {"top.pdf"}
    assert window._selected_record()["current_path"] == "chapter/inner.pdf"
    assert window.table.currentIndex().isValid()


def test_folder_contents_keep_the_standard_indentation(monkeypatch, qtbot, tmp_path):
    """Top-level files line up with folders, and folder contents step right."""
    from PySide6.QtWidgets import QStyleOptionViewItem

    window, _root = _window(monkeypatch, qtbot, tmp_path)
    window.table.expandAll()
    column = window.table.treePosition()
    row = next(
        row
        for row in range(window.model.rowCount())
        if window.model.record_at(row)["current_path"] == "chapter/inner.pdf"
    )
    index = window._view_table_index(window.model.index(row, column))
    option = QStyleOptionViewItem()
    option.initFrom(window.table)
    option.widget = window.table
    option.rect = window.table.visualRect(index)
    expected = option.rect

    window.table.itemDelegateForIndex(index).initStyleOption(option, index)

    assert option.rect == expected


def test_show_folders_has_a_shortcut_and_a_toolbar_button(monkeypatch, qtbot, tmp_path):
    from PySide6.QtGui import QKeySequence

    window, _root = _window(monkeypatch, qtbot, tmp_path)

    assert window.folders_action.shortcut() == QKeySequence("Ctrl+Shift+O")
    assert window.folders_action in window.search_toolbar.actions()
    assert "Show Folders" in window.folders_action.toolTip()


def _selected_paths(window) -> set[str]:
    return {record["current_path"] for record in window._selected_record_refs()}


def test_select_all_includes_pdfs_in_closed_folders(monkeypatch, qtbot, tmp_path):
    window, _root = _window(monkeypatch, qtbot, tmp_path)
    window.table.collapseAll()

    window.table.selectAll()

    assert _selected_paths(window) == {"top.pdf", "chapter/inner.pdf"}


def test_select_all_includes_rows_not_loaded_yet(monkeypatch, qtbot, tmp_path):
    window, root = _window(monkeypatch, qtbot, tmp_path, show_folders=False)
    for index in range(60):
        (root / f"more{index:02d}.pdf").write_bytes(f"%PDF-1.4\n{index}".encode())
    window.registries[str(root)].catalog.discover_root(root, recursive=True)
    window.refresh()
    window.resize(700, 300)
    monkeypatch.setattr("adsfolio.gui.models.PAGE_SIZE", 5)
    window.model.set_query(window.current_query)
    QApplication.processEvents()
    # More PDFs exist than the table has loaded to fill the screen.
    assert window.model.rowCount() < 62

    window.table.selectAll()

    assert len(_selected_paths(window)) == 62


def test_show_folders_keeps_late_selection_without_resorting_source(
    monkeypatch, qtbot, tmp_path
):
    window, root = _window(monkeypatch, qtbot, tmp_path, show_folders=False)
    monkeypatch.setattr("adsfolio.gui.models.PAGE_SIZE", 5)
    for number in range(60):
        (root / f"more{number:02d}.pdf").write_bytes(f"%PDF-1.4\n{number}".encode())
    window.registries[str(root)].catalog.discover_root(root, recursive=True)
    window.refresh()

    filename_column = next(
        column for column, item in enumerate(window.columns) if item.key == "filename"
    )
    order = Qt.AscendingOrder if window.model.query.descending else Qt.DescendingOrder
    window.model.sort(filename_column, order)
    while window.model.canFetchMore(QModelIndex()):
        window.model.fetchMore(QModelIndex())
    assert window.model.rowCount() == 62
    assert window.current_query != window.model.query

    resets = []
    window.model.modelReset.connect(lambda: resets.append(True))
    row = window.model.rowCount() - 2
    record = window.model.record_at(row)
    selected_path = record["current_path"]
    index = window._view_table_index(window.model.index(row, 0))
    window.table.selectionModel().select(
        index, QItemSelectionModel.Select | QItemSelectionModel.Rows
    )
    window.table.selectionModel().setCurrentIndex(index, QItemSelectionModel.NoUpdate)
    expected_query = window.model.query

    window._toggle_folders(True)
    assert window.model.query == expected_query
    assert not resets
    qtbot.waitUntil(lambda: _selected_paths(window) == {selected_path}, timeout=10000)

    window._toggle_folders(False)
    assert window.model.query == expected_query
    assert not resets
    assert _selected_paths(window) == {selected_path}
