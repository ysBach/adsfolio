"""Check panel navigation and Find without changing the selected library."""

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
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


@pytest.fixture
def scrolling_table(qtbot):
    from PySide6.QtGui import QStandardItem, QStandardItemModel
    from PySide6.QtWidgets import QApplication

    from adsfolio.gui.models import LibraryTableView

    view = LibraryTableView()
    qtbot.addWidget(view)
    model = QStandardItemModel(view)
    for _ in range(30):
        model.appendRow([QStandardItem("Column") for _ in range(30)])
    view.setModel(model)
    view.resize(500, 300)
    view.show()
    QApplication.processEvents()
    view.horizontalScrollBar().setValue(1000)
    view.verticalScrollBar().setValue(10)
    return view


def send_wheel(
    view,
    *,
    angle=(0, 0),
    pixels=(0, 0),
    target="viewport",
    phase=None,
    inverted=False,
    modifiers=None,
    device=None,
):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication

    receivers = {
        "viewport": view.viewport(),
        "header": view.header().viewport(),
        "horizontal_bar": view.horizontalScrollBar(),
        "vertical_bar": view.verticalScrollBar(),
    }
    from PySide6.QtGui import QPointingDevice

    event = QWheelEvent(
        QPointF(20, 20),
        QPointF(20, 20),
        QPoint(*pixels),
        QPoint(*angle),
        Qt.NoButton,
        Qt.ShiftModifier if modifiers is None else modifiers,
        Qt.NoScrollPhase if phase is None else phase,
        inverted,
        Qt.MouseEventNotSynthesized,
        device or QPointingDevice.primaryPointingDevice(),
    )
    QApplication.sendEvent(receivers[target], event)
    return event


@pytest.mark.parametrize("delta", [120, -120, 240, -240])
@pytest.mark.parametrize("axis", ["horizontal", "vertical"])
@pytest.mark.parametrize(
    "target", ["viewport", "header", "horizontal_bar", "vertical_bar"]
)
def test_shift_mouse_wheel_moves_one_tenth_page(scrolling_table, delta, axis, target):
    view = scrolling_table
    horizontal, vertical = view.horizontalScrollBar(), view.verticalScrollBar()
    before = horizontal.value()
    vertical_before = vertical.value()
    angle = (delta, 0) if axis == "horizontal" else (0, delta)
    # Cocoa includes estimated pixels even for a discrete mouse wheel.
    pixels = (2, 0) if axis == "horizontal" else (0, 2)
    send_wheel(view, angle=angle, pixels=pixels, target=target)
    direction = (1 if delta > 0 else -1) * (-1 if horizontal.invertedControls() else 1)
    assert horizontal.value() - before == direction * (horizontal.pageStep() // 10)
    assert vertical.value() == vertical_before


@pytest.mark.parametrize(
    "pixels",
    [(20, 0), (-20, 0), (0, 20), (0, -20), (20, 1), (1, 20), (-20, 1), (1, -20)],
)
@pytest.mark.parametrize("inverted", [True, False])
def test_shift_trackpad_uses_one_tenth_of_pixels(scrolling_table, pixels, inverted):
    from PySide6.QtCore import Qt

    view = scrolling_table
    scrollbar = view.horizontalScrollBar()
    before = scrollbar.value()
    vertical_before = view.verticalScrollBar().value()
    send_wheel(
        view, pixels=pixels, angle=(120, 0), phase=Qt.ScrollUpdate, inverted=inverted
    )
    delta = pixels[0] if abs(pixels[0]) > abs(pixels[1]) else pixels[1]
    expected = delta // 10
    if scrollbar.invertedControls():
        expected = -expected
    assert scrollbar.value() - before == expected
    assert view.verticalScrollBar().value() == vertical_before


def test_small_trackpad_deltas_accumulate_and_reverse_without_delay(scrolling_table):
    from PySide6.QtCore import Qt

    scrollbar = scrolling_table.horizontalScrollBar()
    before = scrollbar.value()
    for _ in range(10):
        send_wheel(scrolling_table, pixels=(1, 0), phase=Qt.ScrollUpdate)
    assert abs(scrollbar.value() - before) == 1
    for _ in range(9):
        send_wheel(scrolling_table, pixels=(1, 0), phase=Qt.ScrollUpdate)
    before_reverse = scrollbar.value()
    for _ in range(10):
        send_wheel(scrolling_table, pixels=(-1, 0), phase=Qt.ScrollMomentum)
    assert abs(scrollbar.value() - before_reverse) == 1
    assert scrollbar.value() == before


def test_trackpad_end_and_scrollbar_edges_discard_partial_steps(scrolling_table):
    from PySide6.QtCore import Qt

    scrollbar = scrolling_table.horizontalScrollBar()
    for _ in range(9):
        send_wheel(scrolling_table, pixels=(1, 0), phase=Qt.ScrollUpdate)
    send_wheel(scrolling_table, phase=Qt.ScrollEnd)
    before = scrollbar.value()
    send_wheel(scrolling_table, pixels=(1, 0), phase=Qt.ScrollBegin)
    assert scrollbar.value() == before
    scrollbar.setValue(scrollbar.minimum())
    for _ in range(10):
        send_wheel(scrolling_table, pixels=(1, 0), phase=Qt.ScrollUpdate)
    for _ in range(10):
        send_wheel(scrolling_table, pixels=(-1, 0), phase=Qt.ScrollUpdate)
    assert scrollbar.value() == scrollbar.minimum() + 1


def test_unmodified_vertical_wheel_keeps_native_behavior(qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QStandardItem, QStandardItemModel
    from PySide6.QtWidgets import QApplication, QTreeView

    from adsfolio.gui.models import LibraryTableView

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
        view.horizontalScrollBar().setValue(1000)
        view.verticalScrollBar().setValue(10)
        send_wheel(view, angle=(0, 120), pixels=(0, 0), modifiers=Qt.NoModifier)
        values.append(
            (view.horizontalScrollBar().value(), view.verticalScrollBar().value())
        )
    assert values[0] == values[1]


@pytest.mark.parametrize(
    "phase", [Qt.ScrollBegin, Qt.ScrollUpdate, Qt.ScrollMomentum, Qt.ScrollEnd]
)
@pytest.mark.parametrize(
    "angle,pixels",
    [((40, 0), (20, 0)), ((0, 40), (0, 20)), ((40, -40), (20, -20))],
)
def test_unmodified_precise_trackpad_keeps_native_behavior(qtbot, phase, angle, pixels):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QStandardItem, QStandardItemModel
    from PySide6.QtWidgets import QApplication, QTreeView

    from adsfolio.gui.models import LibraryTableView

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
        view.horizontalScrollBar().setValue(1000)
        view.verticalScrollBar().setValue(10)
        send_wheel(
            view,
            angle=angle,
            pixels=pixels,
            phase=phase,
            modifiers=Qt.NoModifier,
        )
        values.append(
            (view.horizontalScrollBar().value(), view.verticalScrollBar().value())
        )
    assert values[0] == values[1]


def test_unmodified_pixel_only_wheel_keeps_native_behavior(qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QStandardItem, QStandardItemModel
    from PySide6.QtWidgets import QApplication, QTreeView

    from adsfolio.gui.models import LibraryTableView

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
        view.horizontalScrollBar().setValue(1000)
        view.verticalScrollBar().setValue(10)
        send_wheel(view, angle=(0, 0), pixels=(20, 0), modifiers=Qt.NoModifier)
        values.append(
            (view.horizontalScrollBar().value(), view.verticalScrollBar().value())
        )
    assert values[0] == values[1]


@pytest.mark.parametrize("delta", [120, -120, 240, -240])
@pytest.mark.parametrize(
    "target", ["viewport", "header", "horizontal_bar", "vertical_bar"]
)
def test_unmodified_horizontal_wheel_moves_one_tenth_page(
    scrolling_table, delta, target
):
    from PySide6.QtCore import Qt

    view = scrolling_table
    horizontal, vertical = view.horizontalScrollBar(), view.verticalScrollBar()
    before = horizontal.value()
    vertical_before = vertical.value()
    angle = (delta, 0)
    pixels = (2, 0)
    send_wheel(
        view,
        angle=angle,
        pixels=pixels,
        target=target,
        modifiers=Qt.NoModifier,
    )
    direction = (1 if delta > 0 else -1) * (-1 if horizontal.invertedControls() else 1)
    assert horizontal.value() - before == direction * (horizontal.pageStep() // 10)
    assert vertical.value() == vertical_before


def test_unmodified_diagonal_wheel_preserves_both_movements(scrolling_table):
    from PySide6.QtCore import Qt

    view = scrolling_table
    horizontal, vertical = view.horizontalScrollBar(), view.verticalScrollBar()
    horizontal.setValue(1000)
    vertical.setValue((vertical.minimum() + vertical.maximum()) // 2)
    before_h = horizontal.value()
    before_v = vertical.value()
    assert vertical.minimum() < before_v < vertical.maximum()
    send_wheel(
        view,
        angle=(120, -120),
        pixels=(2, 2),
        target="viewport",
        modifiers=Qt.NoModifier,
    )
    direction_h = -1 if horizontal.invertedControls() else 1
    assert horizontal.value() - before_h == direction_h * (horizontal.pageStep() // 10)
    assert vertical.value() != before_v


def test_cocoa_mouse_wheel_keeps_notch_step_with_shared_pixel_device(
    scrolling_table, monkeypatch
):
    from PySide6.QtGui import QInputDevice, QPointingDevice

    from adsfolio.gui import models

    monkeypatch.setattr(models.sys, "platform", "darwin")
    device = QPointingDevice(
        "Shared pointer",
        1,
        QInputDevice.DeviceType.Mouse,
        QPointingDevice.PointerType.Generic,
        QInputDevice.Capability.PixelScroll,
        1,
        3,
    )
    scrollbar = scrolling_table.horizontalScrollBar()
    before = scrollbar.value()
    send_wheel(scrolling_table, angle=(0, 120), pixels=(0, 2), device=device)
    assert abs(scrollbar.value() - before) == scrollbar.pageStep() // 10


def test_shift_wheel_keeps_native_horizontal_direction(qtbot, scrolling_table):
    from PySide6.QtWidgets import QTreeView

    native = QTreeView()
    qtbot.addWidget(native)
    native.setModel(scrolling_table.model())
    native.resize(scrolling_table.size())
    native.show()
    from PySide6.QtWidgets import QApplication

    QApplication.processEvents()
    native.horizontalScrollBar().setValue(1000)
    before_native = native.horizontalScrollBar().value()
    before_scaled = scrolling_table.horizontalScrollBar().value()
    send_wheel(native, angle=(120, 0))
    send_wheel(scrolling_table, angle=(120, 0))
    native_delta = native.horizontalScrollBar().value() - before_native
    scaled_delta = scrolling_table.horizontalScrollBar().value() - before_scaled
    assert scaled_delta == pytest.approx(native_delta / 10, abs=1)
