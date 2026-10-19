"""Table and metadata-pane appearance preferences."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFontComboBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .config import MAX_ROW_HEIGHT, MAX_ROW_SPACING, GuiConfig
from .library import ISO8601_TIMESTAMP_FORMAT, Column, format_mtime

_DETAIL_KEY = "detail"
_DRAG_HANDLE = "≡"
_TIMESTAMP_PRESETS: tuple[tuple[str, str], ...] = (
    ("ISO 8601", ISO8601_TIMESTAMP_FORMAT),
    ("Date and time", "%Y-%m-%d %H:%M:%S"),
    ("Date only", "%Y-%m-%d"),
    ("Month name", "%d %b %Y %H:%M"),
)


class AppearanceDialog(QDialog):
    """Configure table and metadata fonts plus timestamp formatting."""

    settings_changed = Signal()

    def __init__(
        self,
        config: GuiConfig,
        table_columns: Sequence[Column],
        detail_rows: Sequence[tuple[str, str]],
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Appearance")
        self.resize(820, 520)
        self._font_settings = {
            key: dict(value) for key, value in config.column_fonts.items()
        }
        self._detail_font_settings = {
            key: dict(value) for key, value in config.detail_fonts.items()
        }
        self._rows: dict[
            str, tuple[QCheckBox, QFontComboBox, QSpinBox, QPushButton]
        ] = {}
        self._table_row_keys: list[str] = []
        self._detail_row_keys: list[str] = []
        self._table_columns = tuple(table_columns)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        self.tabs.addTab(self._build_fonts_tab(config), "Table")
        self.tabs.addTab(self._build_details_tab(config, detail_rows), "Details")
        self.tabs.addTab(self._build_time_tab(config.timestamp_format), "Time Format")
        self.tabs.addTab(self._build_applications_tab(config), "Applications")
        layout.addWidget(self.tabs)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def column_fonts(self) -> dict[str, dict[str, Any]]:
        """Return serializable font settings for table columns and detail pane."""
        self._store_all_rows()
        return self._font_settings

    @property
    def detail_fonts(self) -> dict[str, dict[str, Any]]:
        """Return serializable per-row typography for the detail pane."""
        self._store_all_rows()
        return self._detail_font_settings

    @property
    def table_column_order(self) -> list[str]:
        """Return table-column keys in their configured visual order."""
        return self._visual_key_order(self.font_table, self._table_row_keys)

    @property
    def hidden_table_columns(self) -> list[str]:
        """Return table-column keys configured as hidden."""
        return self._unchecked_keys(self.font_table, self._table_row_keys)

    @property
    def timestamp_format_value(self) -> str:
        """Return the selected timestamp format."""
        return self.timestamp_format.text().strip() or ISO8601_TIMESTAMP_FORMAT

    @property
    def expand_abstracts(self) -> bool:
        """Return whether abstracts should start expanded."""
        return self.expand_abstracts_checkbox.isChecked()

    @property
    def table_row_height(self) -> int | None:
        """Return an optional fixed main-table row height."""
        return self.table_row_height_spin.value() or None

    @property
    def detail_row_spacing(self) -> int | None:
        """Return optional pixel separation between detail rows."""
        return self.detail_row_spacing_spin.value() or None

    @property
    def detail_label_bold(self) -> bool:
        """Return whether detail-pane field labels should be bold."""
        return self.detail_label_bold_checkbox.isChecked()

    @property
    def show_edit_buttons(self) -> bool:
        """Return whether detail-row edit buttons should be shown."""
        return self.show_edit_buttons_checkbox.isChecked()

    @property
    def show_lock_buttons(self) -> bool:
        """Return whether detail-row lock buttons should be shown."""
        return self.show_lock_buttons_checkbox.isChecked()

    @property
    def filename_click_action(self) -> str:
        """Return the action used when a table filename is clicked."""
        return str(self.filename_click_action_combo.currentData() or "open")

    @property
    def pdf_viewer_command(self) -> str | None:
        """Return the optional command used to open local PDF files."""
        return self.pdf_viewer_command_edit.text().strip() or None

    @property
    def web_browser_command(self) -> str | None:
        """Return the optional command used to open external links."""
        return self.web_browser_command_edit.text().strip() or None

    @property
    def detail_row_order(self) -> list[str]:
        """Return detail row keys in their configured display order."""
        return self._visual_key_order(self.detail_table, self._detail_row_keys)

    @property
    def detail_hidden_rows(self) -> list[str]:
        """Return detail row keys configured as hidden."""
        return self._unchecked_keys(self.detail_table, self._detail_row_keys)

    def _build_fonts_tab(self, config: GuiConfig) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        labels = [
            (
                column.key,
                "Resolution marker" if column.key == "status" else column.header,
            )
            for column in self._table_columns
        ]
        rows = list(self._ordered_rows(labels, config.table_column_order))
        self._table_row_keys = [key for key, _label in rows]
        self.font_table = QTableWidget(len(rows), 5, tab)
        self.font_table.setHorizontalHeaderLabels(
            ["Column", "Custom style", "Font", "Size", "Color"]
        )
        hidden = set(config.hidden_table_columns)
        for row, (key, name) in enumerate(rows):
            self.font_table.setItem(
                row, 0, self._visibility_item(name, key not in hidden)
            )
            self._add_font_controls(
                self.font_table, row, key, self._font_settings.get(key)
            )
        self._enable_row_reordering(self.font_table)
        self.font_table.itemChanged.connect(self._emit_settings_changed)
        self.font_table.verticalHeader().sectionMoved.connect(
            self._emit_settings_changed
        )
        self.font_table.resizeColumnsToContents()
        self.font_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        layout.addWidget(
            QLabel("Drag the row handles to reorder; uncheck to hide", tab)
        )
        layout.addWidget(self.font_table)
        self._add_bulk_font_controls(layout, tab, list(self._rows))
        self.table_row_height_spin = self._add_row_height_control(
            layout, tab, "Table row height", config.table_row_height
        )
        self.table_row_height_spin.valueChanged.connect(self._emit_settings_changed)
        return tab

    @staticmethod
    def _ordered_rows(
        rows: list[tuple[str, str]], order: list[str]
    ) -> tuple[tuple[str, str], ...]:
        labels = dict(rows)
        return tuple(
            (key, labels[key])
            for key in dict.fromkeys([*order, *(key for key, _label in rows)])
            if key in labels
        )

    @staticmethod
    def _visibility_item(label: str, visible: bool) -> QTableWidgetItem:
        """Return a non-editable, checkable label cell for a reorderable row."""
        item = QTableWidgetItem(label)
        item.setFlags((item.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEditable)
        item.setCheckState(Qt.Checked if visible else Qt.Unchecked)
        return item

    @staticmethod
    def _enable_row_reordering(table: QTableWidget) -> None:
        """Let the user drag a table's vertical header to reorder its rows."""
        header = table.verticalHeader()
        header.setSectionsMovable(True)
        header.setSectionsClickable(True)
        table.setVerticalHeaderLabels([_DRAG_HANDLE] * table.rowCount())

    @staticmethod
    def _visual_key_order(table: QTableWidget, keys: list[str]) -> list[str]:
        """Return `keys` reordered to match the table's on-screen row order."""
        header = table.verticalHeader()
        return [keys[header.logicalIndex(visual)] for visual in range(len(keys))]

    @staticmethod
    def _unchecked_keys(table: QTableWidget, keys: list[str]) -> list[str]:
        """Return keys whose label cell is unchecked, in on-screen row order."""
        header = table.verticalHeader()
        return [
            keys[row]
            for row in (header.logicalIndex(visual) for visual in range(len(keys)))
            if table.item(row, 0).checkState() == Qt.Unchecked
        ]

    def _add_row_height_control(
        self,
        layout: QVBoxLayout,
        parent: QWidget,
        label: str,
        value: int | None,
    ) -> QSpinBox:
        """Add a labelled row-height spin box, where zero means automatic."""
        container = QWidget(parent)
        controls = QHBoxLayout(container)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.addWidget(QLabel(label, container))
        spin = QSpinBox(container)
        spin.setRange(0, MAX_ROW_HEIGHT)
        spin.setSpecialValueText("Automatic")
        spin.setValue(value or 0)
        controls.addWidget(spin)
        controls.addStretch(1)
        layout.addWidget(container)
        return spin

    def _add_font_controls(
        self,
        table: QTableWidget,
        row: int,
        key: str,
        settings: dict[str, Any] | None,
    ) -> None:
        enabled = QCheckBox(table)
        font = QFontComboBox(table)
        font.setCurrentFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        size = QSpinBox(table)
        size.setRange(6, 72)
        size.setValue(max(font.currentFont().pointSize(), 10))
        color = QPushButton("Default", table)
        if settings is not None:
            enabled.setChecked(True)
            selected_font = QFont(str(settings.get("family", "")))
            selected_font.setPointSize(int(settings.get("point_size", 10)))
            font.setCurrentFont(selected_font)
            size.setValue(int(settings.get("point_size", 10)))
            self._set_color_button(color, str(settings.get("color", "")))
        else:
            self._set_color_button(color, "")
        table.setCellWidget(row, 1, enabled)
        table.setCellWidget(row, 2, font)
        table.setCellWidget(row, 3, size)
        table.setCellWidget(row, 4, color)
        self._rows[key] = (enabled, font, size, color)
        enabled.toggled.connect(
            lambda _checked, row_key=key: self._font_control_changed(row_key)
        )
        font.currentFontChanged.connect(self._emit_settings_changed)
        size.valueChanged.connect(self._emit_settings_changed)
        color.clicked.connect(
            lambda _checked=False, row_key=key: self._choose_color(row_key)
        )
        self._update_font_row(key)

    def _add_bulk_font_controls(
        self, layout: QVBoxLayout, parent: QWidget, keys: list[str]
    ) -> None:
        container = QWidget(parent)
        controls = QHBoxLayout(container)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.addWidget(QLabel("Change all fonts", container))
        font = QFontComboBox(container)
        font.setCurrentFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        controls.addWidget(font)
        size = QSpinBox(container)
        size.setRange(6, 72)
        size.setValue(max(font.currentFont().pointSize(), 10))
        controls.addWidget(size)
        color = QPushButton("Default", container)
        self._set_color_button(color, "")
        color.clicked.connect(lambda: self._choose_bulk_color(color))
        controls.addWidget(color)
        apply_font = QPushButton("Apply font to all", container)
        apply_fixed = QPushButton("Apply fixed width to all", container)
        apply_font.clicked.connect(
            lambda: self._apply_font_to_rows(
                keys,
                font.currentFont(),
                size.value(),
                str(color.property("color") or ""),
            )
        )
        apply_fixed.clicked.connect(
            lambda: self._apply_font_to_rows(
                keys,
                QFontDatabase.systemFont(QFontDatabase.FixedFont),
                size.value(),
                str(color.property("color") or ""),
            )
        )
        controls.addWidget(apply_font)
        controls.addWidget(apply_fixed)
        controls.addStretch(1)
        layout.addWidget(container)

    def _choose_bulk_color(self, button: QPushButton) -> None:
        color = QColorDialog.getColor(
            QColor(str(button.property("color") or "")), self, "Choose text color"
        )
        if color.isValid():
            self._set_color_button(button, color.name())

    def _apply_font_to_rows(
        self, keys: list[str], font: QFont, size: int, color: str
    ) -> None:
        """Enable and apply one font to each named appearance row."""
        for key in keys:
            enabled, field_font, field_size, field_color = self._rows[key]
            enabled.setChecked(True)
            field_font.setCurrentFont(font)
            field_size.setValue(size)
            self._set_color_button(field_color, color)
        self._emit_settings_changed()

    def _build_details_tab(
        self,
        config: GuiConfig,
        detail_rows: list[tuple[str, str]],
    ) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        self.detail_action_controls = QWidget(tab)
        action_layout = QHBoxLayout(self.detail_action_controls)
        action_layout.setContentsMargins(0, 0, 0, 0)
        self.show_edit_buttons_checkbox = QCheckBox(
            "Show edit buttons", self.detail_action_controls
        )
        self.show_edit_buttons_checkbox.setChecked(config.show_edit_buttons)
        self.show_edit_buttons_checkbox.toggled.connect(self._emit_settings_changed)
        action_layout.addWidget(self.show_edit_buttons_checkbox)
        self.show_lock_buttons_checkbox = QCheckBox(
            "Show lock buttons", self.detail_action_controls
        )
        self.show_lock_buttons_checkbox.setChecked(config.show_lock_buttons)
        self.show_lock_buttons_checkbox.toggled.connect(self._emit_settings_changed)
        action_layout.addWidget(self.show_lock_buttons_checkbox)
        action_layout.addStretch(1)
        layout.addWidget(self.detail_action_controls)
        rows = list(self._ordered_rows(detail_rows, config.detail_row_order))
        self._detail_row_keys = [key for key, _label in rows]
        layout.addWidget(
            QLabel(
                "Metadata fields (drag the row handles to reorder; uncheck to hide)",
                tab,
            )
        )
        self.detail_table = QTableWidget(len(rows), 5, tab)
        self.detail_table.setHorizontalHeaderLabels(
            ["Field", "Custom style", "Font", "Size", "Color"]
        )
        hidden = set(config.detail_hidden_rows)
        for row, (key, label) in enumerate(rows):
            self.detail_table.setItem(
                row, 0, self._visibility_item(label, key not in hidden)
            )
            self._add_font_controls(
                self.detail_table,
                row,
                f"{_DETAIL_KEY}:{key}",
                self._detail_font_settings.get(key),
            )
        self._enable_row_reordering(self.detail_table)
        self.detail_table.itemChanged.connect(self._emit_settings_changed)
        self.detail_table.verticalHeader().sectionMoved.connect(
            self._emit_settings_changed
        )
        self.detail_table.resizeColumnsToContents()
        self.detail_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        layout.addWidget(self.detail_table)
        self._add_bulk_font_controls(
            layout,
            tab,
            [f"{_DETAIL_KEY}:{key}" for key, _label in rows],
        )
        self.expand_abstracts_checkbox = QCheckBox("Expand abstracts by default", tab)
        self.expand_abstracts_checkbox.setChecked(config.expand_abstracts)
        self.expand_abstracts_checkbox.toggled.connect(self._emit_settings_changed)
        layout.addWidget(self.expand_abstracts_checkbox)
        self.detail_label_bold_checkbox = QCheckBox("Bold field labels", tab)
        self.detail_label_bold_checkbox.setChecked(config.detail_label_bold)
        self.detail_label_bold_checkbox.toggled.connect(self._emit_settings_changed)
        layout.addWidget(self.detail_label_bold_checkbox)
        self.detail_row_spacing_spin = self._add_row_spacing_control(
            layout, tab, "Detail row separation", config.detail_row_spacing
        )
        self.detail_row_spacing_spin.valueChanged.connect(self._emit_settings_changed)
        return tab

    def _add_row_spacing_control(
        self,
        layout: QVBoxLayout,
        parent: QWidget,
        label: str,
        value: int | None,
    ) -> QSpinBox:
        """Add a control for the gap between adjacent detail rows."""
        container = QWidget(parent)
        controls = QHBoxLayout(container)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.addWidget(QLabel(label, container))
        spin = QSpinBox(container)
        spin.setRange(0, MAX_ROW_SPACING)
        spin.setSpecialValueText("Default")
        spin.setValue(value or 0)
        controls.addWidget(spin)
        controls.addStretch(1)
        layout.addWidget(container)
        return spin

    def _build_time_tab(self, timestamp_format: str) -> QWidget:
        tab = QWidget(self)
        form = QFormLayout(tab)
        self.timestamp_preset = QComboBox(tab)
        for name, value in _TIMESTAMP_PRESETS:
            self.timestamp_preset.addItem(name, value)
        self.timestamp_preset.addItem("Custom", None)
        self.timestamp_format = QLineEdit(timestamp_format, tab)
        self.timestamp_format.setPlaceholderText("strftime pattern, e.g. %Y-%m-%d")
        self.timestamp_example = QLabel(tab)
        form.addRow("Preset", self.timestamp_preset)
        form.addRow("Format", self.timestamp_format)
        form.addRow("Example", self.timestamp_example)
        self.timestamp_preset.currentIndexChanged.connect(self._select_timestamp_preset)
        self.timestamp_preset.currentIndexChanged.connect(self._emit_settings_changed)
        self.timestamp_format.textChanged.connect(self._update_timestamp_example)
        self.timestamp_format.textChanged.connect(self._emit_settings_changed)
        self._select_initial_timestamp_format(timestamp_format)
        return tab

    def _build_applications_tab(self, config: GuiConfig) -> QWidget:
        tab = QWidget(self)
        form = QFormLayout(tab)
        self.filename_click_action_combo = QComboBox(tab)
        self.filename_click_action_combo.addItem("Open file", "open")
        self.filename_click_action_combo.addItem("Reveal in file manager", "reveal")
        action_index = self.filename_click_action_combo.findData(
            config.filename_click_action
        )
        self.filename_click_action_combo.setCurrentIndex(max(0, action_index))
        form.addRow("Filename click", self.filename_click_action_combo)

        self.pdf_viewer_command_edit = QLineEdit(tab)
        self.pdf_viewer_command_edit.setText(config.pdf_viewer_command or "")
        self.pdf_viewer_command_edit.setPlaceholderText("System default")
        form.addRow("PDF viewer command", self.pdf_viewer_command_edit)

        self.web_browser_command_edit = QLineEdit(tab)
        self.web_browser_command_edit.setText(config.web_browser_command or "")
        self.web_browser_command_edit.setPlaceholderText("System default")
        form.addRow("Web browser command", self.web_browser_command_edit)

        help_label = QLabel(
            "Leave commands empty to use the operating system default. "
            "Commands may include arguments; the file or URL is appended.",
            tab,
        )
        help_label.setWordWrap(True)
        form.addRow("", help_label)
        self.filename_click_action_combo.currentIndexChanged.connect(
            self._emit_settings_changed
        )
        self.pdf_viewer_command_edit.textChanged.connect(self._emit_settings_changed)
        self.web_browser_command_edit.textChanged.connect(self._emit_settings_changed)
        return tab

    def _emit_settings_changed(self, *_args: Any) -> None:
        self.settings_changed.emit()

    def _font_control_changed(self, key: str) -> None:
        self._update_font_row(key)
        self._emit_settings_changed()

    def _update_font_row(self, key: str) -> None:
        enabled, font, size, _color = self._rows[key]
        font.setEnabled(enabled.isChecked())
        size.setEnabled(enabled.isChecked())

    def _store_all_rows(self) -> None:
        for key, (enabled, font, size, color) in self._rows.items():
            settings = (
                self._detail_font_settings
                if key.startswith(f"{_DETAIL_KEY}:")
                else self._font_settings
            )
            settings_key = key.removeprefix(f"{_DETAIL_KEY}:")
            if enabled.isChecked():
                settings[settings_key] = {
                    "family": font.currentFont().family(),
                    "point_size": size.value(),
                    "color": str(color.property("color") or ""),
                }
            else:
                settings.pop(settings_key, None)

    def _choose_color(self, key: str) -> None:
        _enabled, _font, _size, button = self._rows[key]
        current = QColor(str(button.property("color") or ""))
        color = QColorDialog.getColor(current, self, "Choose text color")
        if color.isValid():
            self._set_color_button(button, color.name())
            self._emit_settings_changed()

    @staticmethod
    def _set_color_button(button: QPushButton, color: str) -> None:
        button.setProperty("color", color)
        button.setText(color or "Default")
        button.setStyleSheet(f"color: {color};" if color else "")

    def _select_initial_timestamp_format(self, timestamp_format: str) -> None:
        for index in range(self.timestamp_preset.count() - 1):
            if self.timestamp_preset.itemData(index) == timestamp_format:
                self.timestamp_preset.setCurrentIndex(index)
                break
        else:
            self.timestamp_preset.setCurrentIndex(self.timestamp_preset.count() - 1)
        self._update_timestamp_example()

    def _select_timestamp_preset(self) -> None:
        value = self.timestamp_preset.currentData()
        if isinstance(value, str):
            self.timestamp_format.setText(value)

    def _update_timestamp_example(self) -> None:
        sample = {
            "mtime_ns": int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
        }
        self.timestamp_example.setText(
            format_mtime(sample, self.timestamp_format_value)
        )
