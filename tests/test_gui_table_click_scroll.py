"""Clicking a row in the real window keeps the table's horizontal position."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication
from test_gui_main_window_watch import _window_factory

from adsfolio.gui.config import GuiConfig, RootEntry


def _library_window(monkeypatch, qtbot, tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    for index in range(30):
        (root / f"paper{index:02d}.pdf").write_bytes(b"%PDF-1.4\n" + bytes([index]))
    window, _controller, _saves = _window_factory(
        monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(root))])
    )
    registry = window.registries[str(root.resolve())]
    for index in range(30):
        registry.replace_file_metadata(
            root.resolve(),
            f"paper{index:02d}.pdf",
            {"title": [f"A long fictional title number {index} " * 4]},
        )
    window.refresh()
    window.resize(700, 500)
    window.show()
    qtbot.waitExposed(window)
    QApplication.processEvents()
    return window


def _column(window, key: str) -> int:
    return next(i for i, c in enumerate(window.columns) if c.key == key)


@pytest.mark.parametrize("key", ["title", "filename"])
@pytest.mark.parametrize("width", [300, 1200])
def test_click_keeps_horizontal_position(monkeypatch, qtbot, tmp_path, key, width):
    window = _library_window(monkeypatch, qtbot, tmp_path)
    table = window.table
    column = _column(window, key)
    if table.isColumnHidden(column):
        table.setColumnHidden(column, False)
    table.setColumnWidth(column, width)
    QApplication.processEvents()
    scrollbar = table.horizontalScrollBar()
    header = table.header()
    start = header.sectionViewportPosition(column) + scrollbar.value()
    # Hide the left part of the column, then click its visible part.
    scrollbar.setValue(start + 60)
    QApplication.processEvents()
    offset = scrollbar.value()
    assert offset > 0
    x = header.sectionViewportPosition(column) + header.sectionSize(column) // 2
    x = max(5, min(x, table.viewport().width() - 5))
    target = table.indexAt(QPoint(x, 30))
    assert target.column() == column, (target.column(), column)
    qtbot.mouseClick(table.viewport(), Qt.LeftButton, pos=QPoint(x, 30))
    # Qt scrolls to a clicked cell after the double-click interval plus 100 ms.
    qtbot.wait(QApplication.doubleClickInterval() + 400)
    assert table.currentIndex().row() == target.row()
    assert scrollbar.value() == offset


def _scrolled_right(window) -> int:
    """Select a row, then scroll until logical column 0 is fully hidden.

    Qt scrolls to the current cell, which is in column 0, after a reload.
    """
    table = window.table
    header = table.header()
    table.setColumnWidth(0, 150)
    wide = next(
        column
        for column in range(header.count())
        if column != 0 and not table.isColumnHidden(column)
    )
    table.setColumnWidth(wide, 3000)
    table.setCurrentIndex(window._view_table_index(window.model.index(3, 0)))
    QApplication.processEvents()
    scrollbar = table.horizontalScrollBar()
    target = header.sectionPosition(0) + header.sectionSize(0) + 200
    if header.sectionPosition(0) > header.sectionPosition(wide):
        # Column 0 is right of the wide column: hide it on the right side.
        target = max(0, header.sectionPosition(0) - table.viewport().width() - 200)
    scrollbar.setValue(target)
    QApplication.processEvents()
    left = header.sectionViewportPosition(0)
    assert left + header.sectionSize(0) <= 0 or left >= table.viewport().width()
    return scrollbar.value()


def test_reload_with_a_selected_pdf_keeps_horizontal_position(
    monkeypatch, qtbot, tmp_path
):
    window = _library_window(monkeypatch, qtbot, tmp_path)
    offset = _scrolled_right(window)

    window.refresh()
    qtbot.wait(50)

    assert window.table.currentIndex().isValid()
    assert window.table.horizontalScrollBar().value() == offset


def test_library_click_in_combined_view_keeps_horizontal_position(
    monkeypatch, qtbot, tmp_path
):
    window = _library_window(monkeypatch, qtbot, tmp_path)
    window.combined.set_enabled(True)
    # Start from a view, so the library click below changes the selection.
    window.sidebar.setCurrentItem(window.views_item.child(0))
    QApplication.processEvents()
    offset = _scrolled_right(window)
    root_item = next(
        window.libraries_item.child(i)
        for i in range(window.libraries_item.childCount())
        if window.libraries_item.child(i).data(0, Qt.UserRole + 1) == "root"
    )
    resets = []
    window.model.modelReset.connect(lambda: resets.append(1))

    window.sidebar.setCurrentItem(root_item)
    qtbot.wait(50)

    assert resets, "the library click must reload the table"

    assert window.table.currentIndex().isValid()
    assert window.table.horizontalScrollBar().value() == offset
