"""Dialog for editing and managing custom citation styles."""

from __future__ import annotations

import dataclasses
import unicodedata
import uuid
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWhatsThis,
    QWidget,
)

from ..citations import (
    CITATION_FIELD_HELP,
    CitationFormatter,
    CitationStyle,
    CitationStyleError,
    new_citation_style,
)

__all__ = [
    "BUILTIN_PREVIEW_RECORD",
    "TEMPLATE_HELP_TEXT",
    "CitationStylesDialog",
]

TEMPLATE_HELP_TEXT = f"""{CITATION_FIELD_HELP}

Additional notes:
- Spaces, punctuation, and Unicode are preserved.
- ADS HTML markup is converted to plain text.
- No .pdf extension is appended.
- Unmatched braces, unknown fields, empty fields, format specifiers, conversions,
  and attribute/item access block saving.
- Empty names, empty templates, empty IDs, invalid author settings, duplicate names,
  or duplicate IDs block saving and identify the affected style."""

BUILTIN_PREVIEW_RECORD: dict[str, Any] = {
    "metadata": {
        "bibcode": "2024A&A...123..456B",
        "year": "2024",
        "title": ["Dust <SUP>2</SUP> dynamics in protoplanetary disks"],
        "author": ["Bach, Yoonsoo P.", "Doe, Alice", "Roe, Bob"],
        "bibstem": ["A&A"],
        "pub": "Astronomy &amp; Astrophysics",
        "volume": "123",
        "page": ["456"],
    }
}


def _make_unique_copy_name(base_name: str, existing_names: Iterable[str]) -> str:
    """Generate unique copy name like 'Copy of <base>' or numbered variant.

    Parameters
    ----------
    base_name : str
        Base style name.
    existing_names : Iterable[str]
        Existing names in the collection.

    Returns
    -------
    str
        Unique name starting with 'Copy of '.
    """
    norm_existing = {
        unicodedata.normalize("NFC", n.strip().casefold()) for n in existing_names
    }
    prefix = f"Copy of {base_name}"
    if unicodedata.normalize("NFC", prefix.casefold()) not in norm_existing:
        return prefix
    n = 2
    while True:
        candidate = f"{prefix} ({n})"
        if unicodedata.normalize("NFC", candidate.casefold()) not in norm_existing:
            return candidate
        n += 1


class CitationStylesDialog(QDialog):
    """Dialog for editing custom citation styles.

    Parameters
    ----------
    styles : Sequence[CitationStyle]
        Initial citation styles.
    preview_record : Mapping[str, Any] | None, default=None
        Record to use for rendering previews. If None, built-in example record is used.
    parent : QWidget | None, default=None
        Parent widget.
    """

    saved = Signal(object)

    def __init__(
        self,
        styles: Sequence[CitationStyle],
        preview_record: Mapping[str, Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Custom Citation Styles")
        self.resize(800, 580)

        self._styles_list: list[CitationStyle] = list(styles)
        self._preview_record = preview_record
        self._current_index: int = -1
        self._is_updating_ui: bool = False

        self._build_ui()
        self._populate_list(select_index=0 if self._styles_list else -1)

    @property
    def styles(self) -> tuple[CitationStyle, ...]:
        """Return the current list of CitationStyle objects.

        Returns
        -------
        tuple[CitationStyle, ...]
            Current tuple of styles.
        """
        return tuple(self._styles_list)

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)

        content_layout = QHBoxLayout()

        # Left panel: List and manipulation buttons
        left_panel = QWidget(self)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self.list_widget = QListWidget(self)
        self.list_widget.currentRowChanged.connect(self._on_current_row_changed)
        left_layout.addWidget(self.list_widget, 1)

        btn_row1 = QHBoxLayout()
        self.add_button = QPushButton("Add", self)
        self.add_button.clicked.connect(self._on_add)
        self.duplicate_button = QPushButton("Duplicate", self)
        self.duplicate_button.clicked.connect(self._on_duplicate)
        btn_row1.addWidget(self.add_button)
        btn_row1.addWidget(self.duplicate_button)
        left_layout.addLayout(btn_row1)

        btn_row2 = QHBoxLayout()
        self.move_up_button = QPushButton("Move Up", self)
        self.move_up_button.clicked.connect(self._on_move_up)
        self.move_down_button = QPushButton("Move Down", self)
        self.move_down_button.clicked.connect(self._on_move_down)
        self.delete_button = QPushButton("Delete", self)
        self.delete_button.clicked.connect(self._on_delete)
        btn_row2.addWidget(self.move_up_button)
        btn_row2.addWidget(self.move_down_button)
        btn_row2.addWidget(self.delete_button)
        left_layout.addLayout(btn_row2)

        content_layout.addWidget(left_panel, 1)

        # Right panel: Editor and Help
        right_panel = QWidget(self)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # Form fields
        form_layout = QFormLayout()

        self.name_edit = QLineEdit(self)
        form_layout.addRow("Name:", self.name_edit)

        template_row = QHBoxLayout()
        self.template_edit = QLineEdit(self)
        self.template_edit.setToolTip(TEMPLATE_HELP_TEXT)
        self.template_edit.setAccessibleDescription(TEMPLATE_HELP_TEXT)

        self.template_help_button = QToolButton(self)
        self.template_help_button.setText("?")
        self.template_help_button.setAutoRaise(True)
        self.template_help_button.setAccessibleName("Help for Template")
        self.template_help_button.setToolTip(TEMPLATE_HELP_TEXT)
        self.template_help_button.setAccessibleDescription(TEMPLATE_HELP_TEXT)
        self.template_help_button.clicked.connect(self._show_template_help)

        template_row.addWidget(self.template_edit, 1)
        template_row.addWidget(self.template_help_button)
        form_layout.addRow("Template:", template_row)

        self.author_format_combo = QComboBox(self)
        self.author_format_combo.addItem(
            "Surname + initials (e.g. BachYP)", "surname_initials"
        )
        self.author_format_combo.addItem("Surname only (e.g. Bach)", "surname")
        self.author_format_combo.addItem(
            "Last, First (e.g. Bach, Yoonsoo P.)", "last_comma_first"
        )
        form_layout.addRow("Author format:", self.author_format_combo)

        self.max_authors_spin = QSpinBox(self)
        self.max_authors_spin.setRange(1, 99)
        self.max_authors_spin.setValue(3)
        self.max_authors_spin.valueChanged.connect(self._on_max_authors_changed)
        form_layout.addRow("Max authors:", self.max_authors_spin)

        self.authors_when_truncated_spin = QSpinBox(self)
        self.authors_when_truncated_spin.setRange(1, 3)
        self.authors_when_truncated_spin.setValue(1)
        form_layout.addRow("Authors when truncated:", self.authors_when_truncated_spin)

        self.author_separator_edit = QLineEdit(self)
        self.author_separator_edit.setText(", ")
        form_layout.addRow("Author separator:", self.author_separator_edit)

        self.author_truncation_suffix_edit = QLineEdit(self)
        self.author_truncation_suffix_edit.setText(" et al.")
        form_layout.addRow("Truncation suffix:", self.author_truncation_suffix_edit)

        right_layout.addLayout(form_layout)

        # Preview section
        preview_box = QFrame(self)
        preview_box.setFrameShape(QFrame.StyledPanel)
        preview_layout = QVBoxLayout(preview_box)

        preview_header_row = QHBoxLayout()
        preview_title = QLabel("Preview:", self)
        font = preview_title.font()
        font.setBold(True)
        preview_title.setFont(font)
        preview_header_row.addWidget(preview_title)

        self.preview_note_label = QLabel("", self)
        self.preview_note_label.setStyleSheet("color: gray;")
        preview_header_row.addWidget(self.preview_note_label)
        preview_header_row.addStretch(1)

        self.preview_button = QPushButton("Update Preview", self)
        self.preview_button.clicked.connect(self._on_preview_clicked)
        preview_header_row.addWidget(self.preview_button)
        preview_layout.addLayout(preview_header_row)

        self.preview_label = QLabel("", self)
        self.preview_label.setWordWrap(True)
        self.preview_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        preview_layout.addWidget(self.preview_label)

        right_layout.addWidget(preview_box)

        # Help pane (always visible)
        help_label = QLabel("Template Fields & Rules:", self)
        help_font = help_label.font()
        help_font.setBold(True)
        help_label.setFont(help_font)
        right_layout.addWidget(help_label)

        self.help_view = QTextBrowser(self)
        self.help_view.setPlainText(TEMPLATE_HELP_TEXT)
        self.help_view.setOpenExternalLinks(False)
        self.help_view.setMaximumHeight(150)
        right_layout.addWidget(self.help_view)

        content_layout.addWidget(right_panel, 2)
        main_layout.addLayout(content_layout)

        # Bottom buttons: Save & Cancel
        self.button_box = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel,
            Qt.Horizontal,
            self,
        )
        self.button_box.accepted.connect(self._save)
        self.button_box.rejected.connect(self.reject)
        main_layout.addWidget(self.button_box)

    def _show_template_help(self) -> None:
        """Show contextual help for template field syntax."""
        QWhatsThis.showText(
            self.template_help_button.mapToGlobal(
                self.template_help_button.rect().bottomLeft()
            ),
            TEMPLATE_HELP_TEXT,
            self.template_help_button,
        )

    def _on_max_authors_changed(self, val: int) -> None:
        """Synchronize maximum for authors_when_truncated spinbox."""
        self.authors_when_truncated_spin.setMaximum(val)

    def _populate_list(self, select_index: int = 0) -> None:
        """Populate list widget from _styles_list."""
        self._is_updating_ui = True
        try:
            self.list_widget.clear()
            for s in self._styles_list:
                item = QListWidgetItem(s.name)
                self.list_widget.addItem(item)

            if 0 <= select_index < self.list_widget.count():
                self.list_widget.setCurrentRow(select_index)
                self._current_index = select_index
        finally:
            self._is_updating_ui = False

        if 0 <= select_index < self.list_widget.count():
            self._load_style_into_editor(self._styles_list[select_index])
            self._update_buttons()
        elif self.list_widget.count() == 0:
            self._set_editor_enabled(False)
            self._current_index = -1
            self._update_buttons()

    def _set_editor_enabled(self, enabled: bool) -> None:
        """Enable or disable editing controls when list is empty."""
        self.name_edit.setEnabled(enabled)
        self.template_edit.setEnabled(enabled)
        self.author_format_combo.setEnabled(enabled)
        self.max_authors_spin.setEnabled(enabled)
        self.authors_when_truncated_spin.setEnabled(enabled)
        self.author_separator_edit.setEnabled(enabled)
        self.author_truncation_suffix_edit.setEnabled(enabled)
        self.preview_button.setEnabled(enabled)
        if not enabled:
            self.name_edit.clear()
            self.template_edit.clear()
            self.preview_label.clear()
            self.preview_note_label.clear()

    def _update_buttons(self) -> None:
        """Update enabled state of list action buttons."""
        count = self.list_widget.count()
        row = self.list_widget.currentRow()
        has_sel = 0 <= row < count

        self.duplicate_button.setEnabled(has_sel)
        self.delete_button.setEnabled(has_sel)
        self.move_up_button.setEnabled(has_sel and row > 0)
        self.move_down_button.setEnabled(has_sel and row < count - 1)

    def _load_style_into_editor(self, style: CitationStyle) -> None:
        """Load style values into editor widgets."""
        self._is_updating_ui = True
        try:
            self._set_editor_enabled(True)
            self.name_edit.setText(style.name)
            self.template_edit.setText(style.template)

            idx = self.author_format_combo.findData(style.author_format)
            if idx >= 0:
                self.author_format_combo.setCurrentIndex(idx)
            else:
                self.author_format_combo.setCurrentIndex(0)

            self.max_authors_spin.setValue(style.max_authors)
            self.authors_when_truncated_spin.setMaximum(style.max_authors)
            self.authors_when_truncated_spin.setValue(style.authors_when_truncated)
            self.author_separator_edit.setText(style.author_separator)
            self.author_truncation_suffix_edit.setText(style.author_truncation_suffix)
        finally:
            self._is_updating_ui = False

        self._render_preview_draft()

    def _build_draft_style(self, original_id: str) -> CitationStyle:
        """Build and validate CitationStyle from current editor contents.

        Parameters
        ----------
        original_id : str
            Style identifier to retain.

        Returns
        -------
        CitationStyle
            Constructed CitationStyle instance.

        Raises
        ------
        CitationStyleError
            If editor contents are invalid.
        """
        name = self.name_edit.text().strip()
        if not name:
            raise CitationStyleError("Citation style name must be a non-empty string.")

        template = self.template_edit.text()

        author_format = self.author_format_combo.currentData()
        max_authors = self.max_authors_spin.value()
        authors_when_truncated = self.authors_when_truncated_spin.value()
        author_separator = self.author_separator_edit.text()
        author_truncation_suffix = self.author_truncation_suffix_edit.text()

        return CitationStyle(
            id=original_id,
            name=name,
            template=template,
            author_format=author_format,
            max_authors=max_authors,
            authors_when_truncated=authors_when_truncated,
            author_separator=author_separator,
            author_truncation_suffix=author_truncation_suffix,
        )

    def _store_current_draft(self) -> bool:
        """Store current editor draft into _styles_list.

        Returns
        -------
        bool
            True if stored or no selection, False if validation failed.
        """
        if self._current_index < 0 or self._current_index >= len(self._styles_list):
            return True
        current_style = self._styles_list[self._current_index]
        try:
            updated = self._build_draft_style(current_style.id)
        except CitationStyleError as exc:
            self._report_error(str(exc), field=self._resolve_error_field(str(exc)))
            return False

        self._styles_list[self._current_index] = updated
        item = self.list_widget.item(self._current_index)
        if item and item.text() != updated.name:
            item.setText(updated.name)
        return True

    def _on_current_row_changed(self, row: int) -> None:
        if self._is_updating_ui:
            return

        # Try to store previous draft when a current style is selected.
        if (
            0 <= self._current_index < len(self._styles_list)
            and not self._store_current_draft()
        ):
            # Block changing selection if current draft is invalid
            self._is_updating_ui = True
            try:
                self.list_widget.setCurrentRow(self._current_index)
            finally:
                self._is_updating_ui = False
            return

        self._current_index = row
        self._update_buttons()
        if 0 <= row < len(self._styles_list):
            self._load_style_into_editor(self._styles_list[row])

    def _on_add(self) -> None:
        if not self._store_current_draft():
            return
        existing_names = [s.name for s in self._styles_list]
        existing_ids = [s.id for s in self._styles_list]
        new_style = new_citation_style(existing_names, existing_ids)

        self._styles_list.append(new_style)
        self._populate_list(select_index=len(self._styles_list) - 1)

    def _on_duplicate(self) -> None:
        if not self._store_current_draft():
            return
        if self._current_index < 0 or self._current_index >= len(self._styles_list):
            return

        source = self._styles_list[self._current_index]
        existing_names = [s.name for s in self._styles_list]
        new_name = _make_unique_copy_name(source.name, existing_names)
        existing_ids = {s.id for s in self._styles_list}
        new_id = uuid.uuid4().hex
        while new_id in existing_ids:
            new_id = uuid.uuid4().hex

        copy_style = dataclasses.replace(source, id=new_id, name=new_name)
        insert_index = self._current_index + 1
        self._styles_list.insert(insert_index, copy_style)
        self._populate_list(select_index=insert_index)

    def _on_move_up(self) -> None:
        if not self._store_current_draft():
            return
        row = self.list_widget.currentRow()
        if row <= 0:
            return
        self._styles_list[row - 1], self._styles_list[row] = (
            self._styles_list[row],
            self._styles_list[row - 1],
        )
        self._populate_list(select_index=row - 1)

    def _on_move_down(self) -> None:
        if not self._store_current_draft():
            return
        row = self.list_widget.currentRow()
        if row < 0 or row >= len(self._styles_list) - 1:
            return
        self._styles_list[row], self._styles_list[row + 1] = (
            self._styles_list[row + 1],
            self._styles_list[row],
        )
        self._populate_list(select_index=row + 1)

    def _on_delete(self) -> None:
        if not self._store_current_draft():
            return
        row = self.list_widget.currentRow()
        if row < 0 or row >= len(self._styles_list):
            return
        del self._styles_list[row]
        next_row = min(row, len(self._styles_list) - 1)
        self._current_index = -1
        self._populate_list(select_index=next_row)

    def _render_preview_draft(self) -> bool:
        if self._current_index < 0 or self._current_index >= len(self._styles_list):
            self.preview_label.clear()
            self.preview_note_label.clear()
            return True

        current_id = self._styles_list[self._current_index].id
        try:
            draft = self._build_draft_style(current_id)
        except CitationStyleError as exc:
            self._report_error(str(exc), field=self._resolve_error_field(str(exc)))
            return False

        record = (
            self._preview_record
            if self._preview_record is not None
            else BUILTIN_PREVIEW_RECORD
        )
        try:
            rendered = CitationFormatter().render(draft, record)
        except CitationStyleError as exc:
            self._report_error(str(exc), field=self.template_edit)
            return False

        if self._preview_record is not None:
            self.preview_note_label.setText("(Selected table record)")
        else:
            self.preview_note_label.setText("(Built-in example record)")

        self.preview_label.setText(rendered)
        return True

    def _on_preview_clicked(self) -> None:
        self._render_preview_draft()

    def _resolve_error_field(self, message: str) -> QWidget | None:
        msg = message.lower()
        if "template" in msg or "field" in msg or "syntax" in msg or "brace" in msg:
            return self.template_edit
        if msg.startswith(("citation style name", "duplicate citation style name")):
            return self.name_edit
        if "author_format" in msg:
            return self.author_format_combo
        if (
            "author limits" in msg
            or "authors_when_truncated" in msg
            or "max_authors" in msg
        ):
            return self.authors_when_truncated_spin
        return None

    def _report_error(self, message: str, field: QWidget | None = None) -> None:
        QMessageBox.critical(self, "Invalid Citation Style", message)
        if field is not None and field.isEnabled():
            field.setFocus()
            if isinstance(field, QLineEdit):
                field.selectAll()

    def _save(self) -> None:
        # First store current draft if any
        if not self._store_current_draft():
            return

        # Atomic validation of all styles
        seen_ids: set[str] = set()
        seen_names: set[str] = set()

        for idx, style in enumerate(self._styles_list):
            try:
                # Validate individual style
                CitationStyle.from_mapping(style.to_mapping())
                # Re-check name/id uniqueness
                if style.id in seen_ids:
                    raise CitationStyleError(
                        f"Duplicate citation style ID '{style.id}'."
                    )
                seen_ids.add(style.id)

                folded = unicodedata.normalize("NFC", style.name.strip().casefold())
                if folded in seen_names:
                    raise CitationStyleError(
                        f"Duplicate citation style name '{style.name}'."
                    )
                seen_names.add(folded)
            except CitationStyleError as exc:
                # Switch selection to erroneous style
                if self._current_index != idx:
                    self._is_updating_ui = True
                    try:
                        self.list_widget.setCurrentRow(idx)
                    finally:
                        self._is_updating_ui = False
                    self._current_index = idx
                    self._load_style_into_editor(self._styles_list[idx])
                self._report_error(str(exc), field=self._resolve_error_field(str(exc)))
                return

        final_tuple = tuple(self._styles_list)
        self.saved.emit(final_tuple)
        self.accept()
