"""Open PDF recovery controls by clicking the table's status marker."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QInputDialog, QPushButton
from pytestqt.qtbot import QtBot
from test_gui_main_window_watch import _window_factory

from adsfolio.gui.config import GuiConfig, RootEntry
from adsfolio.pdf.readiness import METADATA_STALE_KEY


@pytest.mark.parametrize("state", ["unresolved", "changed"])
@pytest.mark.parametrize("pane_state", ["hidden", "collapsed"])
@pytest.mark.parametrize("link_mode", ["single", "double"])
@pytest.mark.parametrize("selection_state", ["other", "current", "multiple"])
def test_status_click_reveals_selected_pdf_recovery_controls(
    monkeypatch: pytest.MonkeyPatch,
    qtbot: QtBot,
    tmp_path: Path,
    state: str,
    pane_state: str,
    link_mode: str,
    selection_state: str,
) -> None:
    """A real marker click reveals the PDF even when details are hidden."""
    root = tmp_path / "library"
    root.mkdir()
    for name in ("first.pdf", "warning.pdf"):
        (root / name).write_bytes(b"%PDF-1.4\n" + name.encode())
    window, _controller, _saves = _window_factory(
        monkeypatch,
        qtbot,
        GuiConfig(roots=[RootEntry(str(root))], link_activation=link_mode),
    )
    registry = window.registries[str(root)]
    metadata = {"title": ["Unidentified PDF"]}
    if state == "changed":
        metadata.update({"bibcode": "2026TEST", METADATA_STALE_KEY: True})
    registry.replace_file_metadata(root, "warning.pdf", metadata)
    registry.update_file_status(
        root, "warning.pdf", "resolved" if state == "changed" else "unresolved"
    )
    window.refresh()
    registry = window.registries[str(root)]
    window.show()
    qtbot.waitUntil(window.isVisible)
    row = next(
        row
        for row in range(window.model.rowCount())
        if window.model.record_at(row)["current_path"] == "warning.pdf"
    )
    column = next(
        column
        for column, definition in enumerate(window.columns)
        if definition.key == "status"
    )
    index = window.model.index(row, column)
    other_row = next(other for other in range(window.model.rowCount()) if other != row)
    window.table.setCurrentIndex(
        index if selection_state == "current" else window.model.index(other_row, column)
    )
    if selection_state == "multiple":
        window.table.selectionModel().select(
            index, QItemSelectionModel.Select | QItemSelectionModel.Rows
        )
        assert len(window.table.selectionModel().selectedRows()) == 2
    before = copy.deepcopy(registry.query_files())
    if pane_state == "hidden":
        window.detail_action.setChecked(False)
        window._toggle_detail(False)
    else:
        window.splitter.setSizes([200, 800, 0])
        window.config.splitter_sizes = list(window.splitter.sizes())
    qtbot.waitUntil(lambda: window.splitter.sizes()[2] == 0)

    QTest.mouseClick(
        window.table.viewport(),
        Qt.LeftButton,
        pos=window.table.visualRect(index).center(),
    )

    assert window.detail.isVisible()
    assert window.splitter.sizes()[2] > 0
    assert window.detail_action.isChecked()
    assert window.config.detail_visible
    assert [item.row() for item in window.table.selectionModel().selectedRows()] == [
        row
    ]
    assert window.detail._record["current_path"] == "warning.pdf"
    assert window.detail._row_widgets["status"].value.text() == (
        "PDF changed" if state == "changed" else "No ADS link"
    )
    update_button = next(
        button
        for button in window.detail.findChildren(QPushButton)
        if button.text() == "Update by ID…"
    )
    assert update_button.isVisible()
    assert update_button.isEnabled()
    assert window.detail.manual_metadata_button.isVisible()
    assert window.detail.manual_metadata_button.isEnabled()
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("", False))
    with qtbot.waitSignal(window.detail.ads_update_requested) as emitted:
        update_button.click()
    assert emitted.args == ["2026TEST" if state == "changed" else ""]
    assert registry.query_files() == before


@pytest.mark.parametrize("link_mode", ["single", "double"])
@pytest.mark.parametrize(
    "key,modifier",
    [
        (Qt.Key_Shift, Qt.ShiftModifier),
        (Qt.Key_Control, Qt.ControlModifier),
        (Qt.Key_Alt, Qt.AltModifier),
        (Qt.Key_Meta, Qt.MetaModifier),
    ],
)
def test_modified_status_click_keeps_detail_pane_hidden(
    monkeypatch: pytest.MonkeyPatch,
    qtbot: QtBot,
    tmp_path: Path,
    link_mode: str,
    key: Qt.Key,
    modifier: Qt.KeyboardModifier,
) -> None:
    """Modified marker clicks retain native selection without opening details."""
    root = tmp_path / "library"
    root.mkdir()
    for name in ("first.pdf", "warning.pdf"):
        (root / name).write_bytes(b"%PDF-1.4\n" + name.encode())
    window, _controller, _saves = _window_factory(
        monkeypatch,
        qtbot,
        GuiConfig(roots=[RootEntry(str(root))], link_activation=link_mode),
    )
    window.show()
    qtbot.waitUntil(window.isVisible)
    column = next(
        column
        for column, definition in enumerate(window.columns)
        if definition.key == "status"
    )
    window.table.setCurrentIndex(window.model.index(0, column))
    window.detail_action.setChecked(False)
    window._toggle_detail(False)
    qtbot.waitUntil(lambda: window.splitter.sizes()[2] == 0)

    QTest.keyPress(window.table, key)
    try:
        QTest.mouseClick(
            window.table.viewport(),
            Qt.LeftButton,
            modifier,
            pos=window.table.visualRect(window.model.index(1, column)).center(),
        )

        assert not window.detail.isVisible()
        assert window.splitter.sizes()[2] == 0
        assert not window.detail_action.isChecked()
        assert not window.config.detail_visible
    finally:
        QTest.keyRelease(window.table, key)
