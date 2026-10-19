"""Check panel navigation and Find without changing the selected library."""

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
from PySide6.QtGui import QKeySequence

from adsfolio.config import Config
from adsfolio.gui.config import GuiConfig, load_config, save_config
from adsfolio.gui.main_window import MainWindow


@pytest.fixture
def window(qtbot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> MainWindow:
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr("adsfolio.gui.main_window.load_config", lambda: GuiConfig())
    widget = MainWindow()
    qtbot.addWidget(widget)
    widget.show()
    return widget


def test_find_reveals_hidden_bar_and_selects_existing_query(window: MainWindow) -> None:
    window.search.setText("polarization")
    window.search_toolbar.hide()
    window.search_focus_action.trigger()
    assert not window.search_toolbar.isHidden()
    assert window.search.selectedText() == "polarization"
    assert window.search.hasFocus()
    window.clear_search_action.trigger()
    assert window.search.text() == ""
    assert window.table.hasFocus()


def test_panel_actions_preserve_query_and_remember_visibility(
    window: MainWindow,
) -> None:
    query = window.current_query
    window.sidebar_action.trigger()
    window.detail_action.trigger()
    assert window.sidebar.isHidden()
    assert window.detail.isHidden()
    assert not window.config.sidebar_visible
    assert not window.config.detail_visible
    assert window.current_query == query
    window.sidebar_action.trigger()
    window.detail_action.trigger()
    assert not window.sidebar.isHidden()
    assert not window.detail.isHidden()
    assert window.current_query == query


def test_layout_preferences_roundtrip_without_discarding_inline_choices(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    expected = GuiConfig(
        detail_label_width=88,
        detail_compact_rows=True,
        show_edit_buttons=True,
        show_lock_buttons=False,
        sidebar_visible=False,
        detail_visible=False,
    )
    save_config(expected)
    assert load_config() == expected


def test_column_header_offers_the_shared_appearance_action(window: MainWindow) -> None:
    assert window._create_header_context_menu().actions()[0] is window.appearance_action
    assert window.refresh_action.shortcuts()[0] == QKeySequence("F5")
    assert window.search_focus_action.shortcut() == QKeySequence(
        QKeySequence.StandardKey.Find
    )
    assert window.identify_action.shortcut() == QKeySequence("Ctrl+Shift+I")


@pytest.mark.parametrize("width", [True, False, 0, -1, "wide", None])
def test_invalid_label_width_uses_automatic_layout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, width: object
) -> None:
    import json

    from adsfolio.gui.config import config_path

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    config_path().write_text(json.dumps({"detail_label_width": width}))
    assert load_config().detail_label_width is None


def test_hidden_panels_reopen_with_saved_widths(
    window: MainWindow, qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    from copy import deepcopy

    window.splitter.setSizes([180, 650, 370])
    window.sidebar_action.trigger()
    window.detail_action.trigger()
    window.close()
    saved = deepcopy(window.config)
    assert all(width > 0 for width in saved.splitter_sizes)
    monkeypatch.setattr("adsfolio.gui.main_window.load_config", lambda: saved)
    reopened = MainWindow()
    qtbot.addWidget(reopened)
    reopened.show()
    reopened.sidebar_action.trigger()
    reopened.detail_action.trigger()
    assert reopened.splitter.sizes()[0] > 0
    assert reopened.splitter.sizes()[2] > 0


def test_detail_link_keeps_enter_and_escape_does_not_clear_find(
    window: MainWindow, qtbot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from PySide6.QtCore import Qt

    window.detail.set_record(
        {"current_path": "paper.pdf", "metadata": {"bibcode": "2026TEST"}}, tmp_path
    )
    opened = []
    monkeypatch.setattr(
        "adsfolio.gui.detail_pane.open_url", lambda url, command: opened.append(url)
    )
    window.search.setText("keep this query")
    link = window.detail._row_widgets["links"].link_label
    link.setFocus()
    qtbot.keyClick(link, Qt.Key_Tab)
    qtbot.keyClick(link, Qt.Key_Return)
    assert opened
    qtbot.keyClick(link, Qt.Key_Escape)
    assert window.search.text() == "keep this query"


@pytest.mark.parametrize("delta", [120, -120])
@pytest.mark.parametrize("axis", ["horizontal", "vertical"])
def test_shift_mouse_wheel_moves_one_tenth_of_native_page(
    qtbot, delta: int, axis: str
) -> None:
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QStandardItem, QStandardItemModel, QWheelEvent
    from PySide6.QtWidgets import QApplication, QTreeView

    from adsfolio.gui.models import LibraryTableView

    movements = []
    for view_type in (QTreeView, LibraryTableView):
        view = view_type()
        qtbot.addWidget(view)
        model = QStandardItemModel(view)
        model.appendRow([QStandardItem("Column") for _ in range(30)])
        view.setModel(model)
        view.resize(500, 300)
        view.show()
        QApplication.processEvents()
        scrollbar = view.horizontalScrollBar()
        scrollbar.setValue(1000)
        before = scrollbar.value()
        angle = QPoint(delta, 0) if axis == "horizontal" else QPoint(0, delta)
        event = QWheelEvent(
            QPointF(20, 20),
            QPointF(20, 20),
            QPoint(),
            angle,
            Qt.NoButton,
            Qt.ShiftModifier,
            Qt.NoScrollPhase,
            False,
        )
        QApplication.sendEvent(view.viewport(), event)
        movements.append(abs(scrollbar.value() - before))
    if axis == "horizontal":
        assert movements[1] == pytest.approx(movements[0] / 10, abs=1)
    else:
        assert movements[1] == pytest.approx(49, abs=1)


def test_regular_wheel_and_trackpad_keep_native_behavior(qtbot) -> None:
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QStandardItem, QStandardItemModel, QWheelEvent
    from PySide6.QtWidgets import QApplication, QTreeView

    from adsfolio.gui.models import LibraryTableView

    for pixels, angle, modifiers in (
        (QPoint(), QPoint(120, 0), Qt.NoModifier),
        (QPoint(20, 0), QPoint(20, 0), Qt.ShiftModifier),
    ):
        values = []
        for view_type in (QTreeView, LibraryTableView):
            view = view_type()
            qtbot.addWidget(view)
            model = QStandardItemModel(view)
            model.appendRow([QStandardItem("Column") for _ in range(30)])
            view.setModel(model)
            view.resize(500, 300)
            view.show()
            QApplication.processEvents()
            scrollbar = view.horizontalScrollBar()
            scrollbar.setValue(1000)
            event = QWheelEvent(
                QPointF(20, 20),
                QPointF(20, 20),
                pixels,
                angle,
                Qt.NoButton,
                modifiers,
                Qt.NoScrollPhase,
                False,
            )
            QApplication.sendEvent(view.viewport(), event)
            values.append(scrollbar.value())
        assert values[0] == values[1]
