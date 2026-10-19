"""Tests for the unified GUI appearance dialog."""

from __future__ import annotations

import os
from copy import deepcopy

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QLabel

from adsfolio.citations import CitationStyle
from adsfolio.config import Config
from adsfolio.gui.appearance import AppearanceDialog
from adsfolio.gui.config import GuiConfig
from adsfolio.gui.library import COLUMNS, build_columns
from adsfolio.gui.main_window import MainWindow


@pytest.fixture(scope="module")
def application() -> QApplication:
    """Provide one Qt application for dialog tests."""
    return QApplication.instance() or QApplication([])


def test_appearance_dialog_reorders_and_hides_detail_rows(
    application: QApplication,
) -> None:
    """One dialog owns table, detail, and timestamp preferences."""
    dialog = AppearanceDialog(
        GuiConfig(
            table_column_order=["bibcode", "title"],
            hidden_table_columns=["filename"],
            detail_row_order=["abstract", "title"],
            detail_hidden_rows=["abstract"],
        ),
        COLUMNS,
        [("title", "Title"), ("abstract", "Abstract"), ("doi", "DOI")],
    )

    assert dialog.windowTitle() == "Appearance"
    assert [dialog.tabs.tabText(index) for index in range(dialog.tabs.count())] == [
        "Table",
        "Details",
        "Time Format",
        "Applications",
    ]
    assert dialog.table_column_order[:2] == ["bibcode", "title"]
    assert dialog.hidden_table_columns == ["filename"]
    assert dialog.detail_row_order == ["abstract", "title", "doi"]
    assert dialog.detail_hidden_rows == ["abstract"]
    assert dialog.font_table.columnCount() == 5
    assert dialog.font_table.rowCount() == len(COLUMNS)
    assert dialog.detail_table.columnCount() == 5
    status_row = dialog.table_column_order.index("status")
    assert dialog.font_table.item(status_row, 0).text() == "Resolution marker"
    assert dialog.font_table.item(status_row, 0).checkState() == Qt.Checked
    filename_row = dialog.table_column_order.index("filename")
    assert dialog.font_table.item(filename_row, 0).checkState() == Qt.Unchecked
    dialog.close()


def test_appearance_dialog_exposes_file_and_browser_application_settings(
    application: QApplication,
) -> None:
    """Application settings support defaults plus explicit commands."""
    dialog = AppearanceDialog(
        GuiConfig(
            filename_click_action="reveal",
            pdf_viewer_command="pdf-viewer",
            web_browser_command="web-browser",
        ),
        COLUMNS,
        [("title", "Title")],
    )

    assert dialog.filename_click_action == "reveal"
    assert dialog.pdf_viewer_command == "pdf-viewer"
    assert dialog.web_browser_command == "web-browser"
    dialog.close()


def test_appearance_dialog_reorders_via_table_header(
    application: QApplication,
) -> None:
    """Dragging a table row header changes the stored column order."""
    dialog = AppearanceDialog(GuiConfig(), COLUMNS, [("title", "Title")])

    original = dialog.table_column_order
    dialog.font_table.verticalHeader().moveSection(0, 2)

    assert dialog.table_column_order == [
        original[1],
        original[2],
        original[0],
        *original[3:],
    ]
    dialog.close()


def test_appearance_dialog_places_row_controls_on_their_own_tabs(
    application: QApplication,
) -> None:
    """Row-height controls live beside the widget they resize."""
    dialog = AppearanceDialog(
        GuiConfig(table_row_height=24, detail_row_spacing=18),
        COLUMNS,
        [("title", "Title")],
    )

    table_tab = dialog.tabs.widget(0)
    details_tab = dialog.tabs.widget(1)
    time_tab = dialog.tabs.widget(2)

    assert dialog.table_row_height == 24
    assert dialog.detail_row_spacing == 18
    assert dialog.table_row_height_spin.parent().parent() is table_tab
    assert dialog.detail_row_spacing_spin.parent().parent() is details_tab
    detail_labels = [label.text() for label in details_tab.findChildren(QLabel)]
    assert "Detail row separation" in detail_labels
    assert "Detail row height" not in detail_labels
    assert dialog.expand_abstracts_checkbox.parent() is details_tab
    assert dialog.timestamp_format.parent() is time_tab
    dialog.close()


def test_appearance_dialog_places_detail_action_visibility_at_top(
    application: QApplication,
) -> None:
    """Details exposes global edit and lock button visibility controls first."""
    dialog = AppearanceDialog(
        GuiConfig(show_edit_buttons=False, show_lock_buttons=False),
        COLUMNS,
        [("title", "Title")],
    )
    details_tab = dialog.tabs.widget(1)

    assert details_tab.layout().indexOf(dialog.detail_action_controls) == 0
    assert not dialog.show_edit_buttons
    assert not dialog.show_lock_buttons

    dialog.show_edit_buttons_checkbox.setChecked(True)
    dialog.show_lock_buttons_checkbox.setChecked(True)

    assert dialog.show_edit_buttons
    assert dialog.show_lock_buttons
    dialog.close()


def test_appearance_dialog_exposes_detail_label_boldness(
    application: QApplication,
) -> None:
    """Detail label boldness is editable and returned with appearance settings."""
    dialog = AppearanceDialog(
        GuiConfig(detail_label_bold=False),
        COLUMNS,
        [("title", "Title")],
    )

    assert not dialog.detail_label_bold
    assert not dialog.detail_label_bold_checkbox.isChecked()

    dialog.detail_label_bold_checkbox.setChecked(True)
    assert dialog.detail_label_bold
    dialog.close()


def test_appearance_dialog_emits_for_detail_row_spacing(
    application: QApplication,
) -> None:
    """Changing a row-height control notifies the live-preview owner."""
    dialog = AppearanceDialog(GuiConfig(), COLUMNS, [("title", "Title")])
    spy = QSignalSpy(dialog.settings_changed)

    dialog.detail_row_spacing_spin.setValue(24)

    assert spy.count() == 1
    dialog.close()


def test_appearance_dialog_emits_for_visibility(
    application: QApplication,
) -> None:
    """Changing a table visibility checkbox notifies the live-preview owner."""
    dialog = AppearanceDialog(GuiConfig(), COLUMNS, [("title", "Title")])
    spy = QSignalSpy(dialog.settings_changed)

    dialog.font_table.item(0, 0).setCheckState(Qt.Unchecked)

    assert spy.count() == 1
    dialog.close()


def test_appearance_dialog_uses_runtime_columns_and_returns_generated_preferences(
    application: QApplication,
) -> None:
    """Runtime citation columns and detail rows participate in all appearance settings."""
    style = CitationStyle(id="alpha", name="Compact", template="{bibcode}")
    key = "citation:alpha"
    columns = build_columns([style])
    dialog = AppearanceDialog(
        GuiConfig(
            table_column_order=["title", "bibcode", key],
            hidden_table_columns=[key],
            detail_row_order=["bibcode", key, "title"],
            detail_hidden_rows=[key],
        ),
        columns,
        [("title", "Title"), ("bibcode", "Bibcode"), (key, "Compact")],
    )

    assert dialog.font_table.rowCount() == len(columns)
    assert dialog.table_column_order.index(key) == (
        dialog.table_column_order.index("bibcode") + 1
    )
    assert dialog.font_table.item(dialog.table_column_order.index(key), 0).text() == (
        "Compact"
    )
    assert key in dialog.hidden_table_columns
    assert dialog.detail_row_order[:2] == ["bibcode", key]
    assert key in dialog.detail_hidden_rows

    _enabled, table_font, table_size, table_color = dialog._rows[key]
    _detail_enabled, detail_font, detail_size, detail_color = dialog._rows[
        f"detail:{key}"
    ]
    _enabled.setChecked(True)
    table_size.setValue(14)
    dialog._set_color_button(table_color, "#ff0000")
    _detail_enabled.setChecked(True)
    detail_size.setValue(15)
    dialog._set_color_button(detail_color, "#00ff00")

    assert dialog.column_fonts[key] == {
        "family": table_font.currentFont().family(),
        "point_size": 14,
        "color": "#ff0000",
    }
    assert dialog.detail_fonts[key] == {
        "family": detail_font.currentFont().family(),
        "point_size": 15,
        "color": "#00ff00",
    }
    dialog.close()


def test_appearance_cancel_restores_after_live_edit(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Cancel restores settings changed while the dialog was open."""
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    window = MainWindow()
    original = deepcopy(window.config)
    observed: dict[str, object] = {}

    def edit_and_cancel() -> None:
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, AppearanceDialog)
        dialog.table_row_height_spin.setValue(80)
        observed["live_height"] = window.config.table_row_height
        dialog.reject()

    QTimer.singleShot(0, edit_and_cancel)
    window._edit_appearance()

    assert observed["live_height"] == 80
    assert window.config == original
    window.close()


def test_appearance_accept_keeps_live_edit(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Accepting Appearance keeps the setting already applied live."""
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    window = MainWindow()
    observed: dict[str, object] = {}

    def edit_and_accept() -> None:
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, AppearanceDialog)
        dialog.table_row_height_spin.setValue(80)
        observed["live_height"] = window.config.table_row_height
        dialog.accept()

    QTimer.singleShot(0, edit_and_accept)
    window._edit_appearance()

    assert observed["live_height"] == 80
    assert window.config.table_row_height == 80
    window.close()


def test_table_row_height_resets_to_automatic(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Switching table height back to automatic restores the default size."""
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    window = MainWindow()
    model = QStandardItemModel(1, 1)
    model.setItem(0, 0, QStandardItem("row"))
    window.table.setModel(model)
    window.table.show()
    application.processEvents()
    index = model.index(0, 0)
    default_height = window.table.rowHeight(index)

    window.config.table_row_height = 80
    window._apply_table_appearance()
    application.processEvents()
    assert window.table.rowHeight(index) == 80

    model.appendRow(QStandardItem("new row"))
    application.processEvents()
    assert window.table.rowHeight(model.index(1, 0)) == 80

    window.config.table_row_height = None
    window._apply_table_appearance()
    application.processEvents()
    assert window.table.rowHeight(index) == default_height
    window.close()
