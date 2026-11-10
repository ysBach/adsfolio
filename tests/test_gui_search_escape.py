"""Esc closes search suggestions first. A second Esc clears the search."""

from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtWidgets import QApplication, QToolBar

from adsfolio.config import Config
from adsfolio.gui.config import GuiConfig, RootEntry
from adsfolio.gui.main_window import MainWindow
from adsfolio.gui.search_completion import ESCAPE_AGAIN_HINT


@pytest.fixture
def window(qtbot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> MainWindow:
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    root = tmp_path / "library"
    root.mkdir()
    (root / "paper.pdf").write_bytes(b"%PDF-1.4\nfictional")
    # A library makes the table visible. Qt ignores shortcuts of hidden widgets.
    config = GuiConfig(roots=[RootEntry(str(root))])
    monkeypatch.setattr("adsfolio.gui.main_window.load_config", lambda: config)
    monkeypatch.setattr("adsfolio.gui.main_window.save_config", lambda saved: None)
    widget = MainWindow()
    qtbot.addWidget(widget)
    widget.show()
    qtbot.waitExposed(widget)
    assert widget.table.isVisible()
    return widget


def _escape(qtbot) -> None:
    """Press Esc where the app receives it: the open list, or the focus widget."""
    target = QApplication.activePopupWidget() or QApplication.focusWidget()
    qtbot.keyClick(target, Qt.Key_Escape)


def test_first_escape_closes_suggestions_and_keeps_the_search(window, qtbot):
    window.search.setFocus()
    qtbot.keyClicks(window.search, "f")
    popup = window.search_completer.completer.popup()
    qtbot.waitUntil(popup.isVisible)

    _escape(qtbot)

    assert not popup.isVisible()
    assert window.search.text() == "f"
    assert window.status.currentMessage() == ESCAPE_AGAIN_HINT

    _escape(qtbot)

    assert window.search.text() == ""
    assert window.table.hasFocus()
    assert window.status.currentMessage() != ESCAPE_AGAIN_HINT


def test_typing_between_escapes_cancels_the_reset(window, qtbot):
    window.search.setFocus()
    qtbot.keyClicks(window.search, "comet")
    _escape(qtbot)
    assert window.search_escape.armed

    qtbot.keyClicks(window.search, "s")
    assert not window.search_escape.armed
    _escape(qtbot)

    assert window.search.text() == "comets"
    assert window.search_escape.armed


def test_escape_in_the_table_also_needs_two_presses(window, qtbot):
    window.search.setText("comet")
    window.table.setFocus()

    _escape(qtbot)
    assert window.search.text() == "comet"

    _escape(qtbot)
    assert window.search.text() == ""


def test_header_click_cancels_first_escape_and_still_sorts(window, qtbot):
    window.search.setText("comet")
    window.table.setFocus()
    _escape(qtbot)
    assert window.search_escape.armed

    header = window.table.header()
    previous_order = header.sortIndicatorOrder()
    section = header.logicalIndex(0)
    center = QPoint(
        header.sectionViewportPosition(section) + header.sectionSize(section) // 2,
        header.viewport().height() // 2,
    )
    qtbot.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, pos=center)

    assert not window.search_escape.armed
    assert window.status.currentMessage() != ESCAPE_AGAIN_HINT
    assert header.sortIndicatorOrder() != previous_order

    _escape(qtbot)
    assert window.search.text() == "comet"
    assert window.search_escape.armed


def test_toolbar_click_cancels_first_escape_and_still_toggles_folders(window, qtbot):
    window.search.setText("paper")
    window.table.setFocus()
    _escape(qtbot)
    assert window.search_escape.armed

    toolbar = next(
        toolbar
        for toolbar in window.findChildren(QToolBar)
        if window.folders_action in toolbar.actions()
    )
    button = toolbar.widgetForAction(window.folders_action)
    previous = window.folders_action.isChecked()
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)

    assert not window.search_escape.armed
    assert window.folders_action.isChecked() != previous
    assert window.status.currentMessage() != ESCAPE_AGAIN_HINT

    window.table.setFocus()
    _escape(qtbot)
    assert window.search.text() == "paper"
    assert window.search_escape.armed


def test_shortcut_cancels_first_escape_and_still_toggles_folders(window, qtbot):
    window.search.setText("paper")
    window.table.setFocus()
    _escape(qtbot)
    assert window.search_escape.armed

    previous = window.folders_action.isChecked()
    qtbot.keyClick(
        window.table,
        Qt.Key.Key_O,
        modifier=Qt.KeyboardModifier.ControlModifier
        | Qt.KeyboardModifier.ShiftModifier,
    )

    assert not window.search_escape.armed
    assert window.folders_action.isChecked() != previous
    assert window.search.text() == "paper"


def test_escape_with_an_empty_search_goes_to_the_table_at_once(window, qtbot):
    window.search.setFocus()

    _escape(qtbot)

    assert window.table.hasFocus()
    assert not window.search_escape.armed


def test_clear_find_menu_command_clears_at_once(window):
    window.search.setText("comet")

    window.clear_search_action.trigger()

    assert window.search.text() == ""


def test_menu_command_cancels_first_escape(window, qtbot):
    window.search.setText("comet")
    window.table.setFocus()
    _escape(qtbot)
    assert window.search_escape.armed

    # A native macOS menu-bar click sends no mouse event. The action still runs.
    window.search_focus_action.trigger()

    assert not window.search_escape.armed
    assert window.status.currentMessage() != ESCAPE_AGAIN_HINT
    _escape(qtbot)
    assert window.search.text() == "comet"
    assert window.search_escape.armed


def test_window_deactivation_cancels_first_escape(window, qtbot):
    window.search.setText("comet")
    window.table.setFocus()
    _escape(qtbot)
    assert window.search_escape.armed

    QApplication.sendEvent(window, QEvent(QEvent.Type.WindowDeactivate))

    assert not window.search_escape.armed
    window.table.setFocus()
    _escape(qtbot)
    assert window.search.text() == "comet"
