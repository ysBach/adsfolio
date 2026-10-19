"""Add local references and export them without an ADS lookup."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

from PySide6.QtCore import QIODevice, QSaveFile, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..pdf.catalog import PdfCatalog
from ..references import LocalReferences, parse_bibtex

_REFERENCE_ERRORS = (ValueError, OSError, sqlite3.Error)
_EDITOR_FIELDS = (
    ("title", "Title"),
    ("author", "Authors (separate with ' and ')"),
    ("year", "Year"),
    ("journal", "Journal"),
    ("booktitle", "Book / proceedings title"),
    ("publisher", "Publisher"),
    ("volume", "Volume"),
    ("number", "Issue"),
    ("pages", "Pages / article number"),
    ("doi", "DOI"),
    ("url", "URL"),
)


class _ReferenceEditor(QDialog):
    """Review editable citation fields before saving one reference."""

    def __init__(
        self,
        parent: QWidget,
        reference: dict[str, Any] | None,
        save: Callable[..., dict[str, Any]],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit reference" if reference else "Add reference")
        self._save = save
        self._original_fields = dict((reference or {}).get("fields", {}))
        self.record: dict[str, Any] | None = None
        layout = QVBoxLayout(self)
        hint = QLabel("A PDF, DOI, and ADS bibcode are optional.", self)
        layout.addWidget(hint)
        form = QFormLayout()
        self.cite_key_edit = QLineEdit((reference or {}).get("cite_key", ""), self)
        form.addRow("Citation key", self.cite_key_edit)
        self.entry_type_combo = QComboBox(self)
        self.entry_type_combo.setEditable(True)
        self.entry_type_combo.addItems(
            [
                "misc",
                "article",
                "book",
                "inproceedings",
                "incollection",
                "phdthesis",
                "techreport",
            ]
        )
        self.entry_type_combo.setCurrentText(
            (reference or {}).get("entry_type", "misc")
        )
        form.addRow("Entry type", self.entry_type_combo)
        layout.addLayout(form)
        self.more_fields_button = QPushButton("More fields", self)
        self.more_fields_button.setCheckable(True)
        self.more_fields_widget = QWidget(self)
        extra_form = QFormLayout(self.more_fields_widget)
        self.field_edits: dict[str, QLineEdit] = {}
        for key, label in _EDITOR_FIELDS:
            editor = QLineEdit(self._original_fields.get(key, ""), self)
            self.field_edits[key] = editor
            target_form = (
                form if key in {"title", "author", "year", "journal"} else extra_form
            )
            target_form.addRow(label, editor)
        layout.addWidget(self.more_fields_button)
        layout.addWidget(self.more_fields_widget)
        self.more_fields_widget.hide()
        self.more_fields_button.toggled.connect(self.more_fields_widget.setVisible)
        self.status_label = QLabel(self)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel, parent=self
        )
        self.save_button = buttons.button(QDialogButtonBox.Save)
        buttons.accepted.connect(self._save_reference)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(560, self.sizeHint().height())

    def _save_reference(self) -> None:
        """Keep errors visible without discarding the user's draft."""
        fields = dict(self._original_fields)
        for key, editor in self.field_edits.items():
            value = editor.text().strip()
            if value:
                fields[key] = value
            else:
                fields.pop(key, None)
        try:
            self.record = self._save(
                cite_key=self.cite_key_edit.text().strip(),
                entry_type=self.entry_type_combo.currentText().strip(),
                fields=fields,
            )
        except _REFERENCE_ERRORS as error:
            self.status_label.setText(str(error))
            return
        self.accept()


class _BibtexImportDialog(QDialog):
    """Preview pasted or imported BibTeX before creating references."""

    def __init__(self, parent: QWidget, service: LocalReferences) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import BibTeX")
        self._service = service
        self.records: list[dict[str, Any]] = []
        self._preview_text: str | None = None
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel("Paste BibTeX from a citation source or open a .bib file.", self)
        )
        self.text_edit = QPlainTextEdit(self)
        self.text_edit.setPlaceholderText("@article{my-key,\n  title = {Title}\n}")
        layout.addWidget(self.text_edit)
        self.preview_list = QListWidget(self)
        layout.addWidget(self.preview_list)
        self.status_label = QLabel(self)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        buttons = QHBoxLayout()
        self.open_button = QPushButton("Open .bib…", self)
        self.preview_button = QPushButton("Preview", self)
        self.import_button = QPushButton("Add references", self)
        self.import_button.setEnabled(False)
        cancel = QPushButton("Cancel", self)
        for button in (
            self.open_button,
            self.preview_button,
            self.import_button,
            cancel,
        ):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.open_button.clicked.connect(self._open_file)
        self.preview_button.clicked.connect(self._preview)
        self.import_button.clicked.connect(self._import)
        cancel.clicked.connect(self.reject)
        self.text_edit.textChanged.connect(self._invalidate_preview)
        self.resize(680, 500)

    def _invalidate_preview(self) -> None:
        """Require another preview after the source text changes."""
        self._preview_text = None
        self.import_button.setEnabled(False)
        self.preview_list.clear()
        self.status_label.clear()

    def _open_file(self) -> None:
        """Read UTF-8 BibTeX into the editable draft."""
        filename, _ = QFileDialog.getOpenFileName(
            self, "Open BibTeX", "", "BibTeX (*.bib);;All files (*)"
        )
        if not filename:
            return
        try:
            self.text_edit.setPlainText(Path(filename).read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError) as error:
            self.status_label.setText(str(error))

    def _preview(self) -> None:
        """Display parsed entries without changing the catalog."""
        self._invalidate_preview()
        text = self.text_edit.toPlainText()
        try:
            records = parse_bibtex(text)
            if not records:
                raise ValueError(
                    "No references found. Paste at least one BibTeX entry."
                )
        except _REFERENCE_ERRORS as error:
            self.status_label.setText(str(error))
            return
        for record in records:
            fields = record["fields"]
            self.preview_list.addItem(
                f"{record['cite_key']} · {record['entry_type']} · "
                f"{fields.get('title', '(untitled)')} · {fields.get('year', '')}"
            )
        self._preview_text = text
        self.import_button.setEnabled(True)
        self.status_label.setText(f"{len(records)} reference(s) ready to add.")

    def _import(self) -> None:
        """Save the reviewed batch atomically through the reference service."""
        if self._preview_text is None:
            return
        try:
            self.records = self._service.import_bibtex(self._preview_text)
        except _REFERENCE_ERRORS as error:
            self.status_label.setText(str(error))
            return
        self.accept()


class ReferencesDialog(QDialog):
    """Manage citation metadata and export selected ADS or local references.

    Parameters
    ----------
    parent
        Parent window, if present.
    catalog
        Open catalog to reuse. When `None`, open and own the default catalog.
    """

    references_changed = Signal()

    def __init__(
        self, parent: QWidget | None = None, *, catalog: PdfCatalog | None = None
    ) -> None:
        super().__init__(parent)
        self._owns_catalog = catalog is None
        self._catalog = catalog if catalog is not None else PdfCatalog()
        self._service = LocalReferences(self._catalog)
        self._catalog_closed = False
        self.setWindowTitle("Local References")
        layout = QVBoxLayout(self)
        hint = QLabel(
            "Add references missing from ADS. Select local or ADS references to export together.",
            self,
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.reference_list = QListWidget(self)
        self.reference_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        layout.addWidget(self.reference_list)
        self.status_label = QLabel(self)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        add_buttons = QHBoxLayout()
        self.add_button = QPushButton("Add manually…", self)
        self.import_button = QPushButton("Import BibTeX…", self)
        self.edit_button = QPushButton("Edit…", self)
        self.attach_button = QPushButton("Attach PDF…", self)
        for button in (
            self.add_button,
            self.import_button,
            self.edit_button,
            self.attach_button,
        ):
            add_buttons.addWidget(button)
        layout.addLayout(add_buttons)
        export_buttons = QHBoxLayout()
        self.copy_button = QPushButton("Copy BibTeX", self)
        self.export_button = QPushButton("Export selected…", self)
        close_button = QPushButton("Close", self)
        export_buttons.addWidget(self.copy_button)
        export_buttons.addWidget(self.export_button)
        export_buttons.addStretch(1)
        export_buttons.addWidget(close_button)
        layout.addLayout(export_buttons)
        self.add_button.clicked.connect(self._add_reference)
        self.import_button.clicked.connect(self._import_bibtex)
        self.edit_button.clicked.connect(self._edit_reference)
        self.attach_button.clicked.connect(self._attach_pdf)
        self.copy_button.clicked.connect(self._copy_bibtex)
        self.export_button.clicked.connect(self._export_bibtex)
        close_button.clicked.connect(self.close)
        self.reference_list.itemSelectionChanged.connect(self._update_controls)
        self.finished.connect(self._release_catalog)
        self.resize(780, 480)
        self.refresh()

    def refresh(self, selected_ids: list[int] | None = None) -> None:
        """Reload references and preserve or set the selected record IDs."""
        selected = set(self._selected_ids() if selected_ids is None else selected_ids)
        try:
            records = self._service.list()
        except _REFERENCE_ERRORS as error:
            self.status_label.setText(str(error))
            return
        self.reference_list.clear()
        for record in records:
            fields = record["fields"]
            origin = "ADS" if record.get("bibcode") else "Local"
            item = QListWidgetItem(
                f"{record['cite_key']} · {fields.get('title', '(untitled)')} "
                f"({fields.get('year', 'no year')}) · {origin}"
            )
            item.setData(Qt.UserRole, record["id"])
            item.setToolTip(f"{len(record.get('attachments', []))} PDF attachment(s)")
            self.reference_list.addItem(item)
            item.setSelected(record["id"] in selected)
        self._update_controls()

    def _selected_ids(self) -> list[int]:
        """Return stable identities for the current selection."""
        return [item.data(Qt.UserRole) for item in self.reference_list.selectedItems()]

    def _update_controls(self) -> None:
        """Enable editing for one reference and export for any selection."""
        count = len(self._selected_ids())
        self.edit_button.setEnabled(count == 1)
        self.attach_button.setEnabled(count == 1)
        self.copy_button.setEnabled(count > 0)
        self.export_button.setEnabled(count > 0)

    def _added(self, records: list[dict[str, Any]]) -> None:
        """Select saved records and notify the surrounding library view."""
        self.refresh([record["id"] for record in records])
        self.status_label.setText(f"Saved {len(records)} reference(s).")
        self.references_changed.emit()

    def _add_reference(self) -> None:
        """Open a manual draft without requiring ADS identity or a PDF."""
        dialog = _ReferenceEditor(self, None, self._service.add)
        if dialog.exec() == QDialog.Accepted and dialog.record is not None:
            self._added([dialog.record])

    def _import_bibtex(self) -> None:
        """Open a paste/file import with a required preview."""
        dialog = _BibtexImportDialog(self, self._service)
        if dialog.exec() == QDialog.Accepted:
            self._added(dialog.records)

    def _edit_reference(self) -> None:
        """Edit one reference while preserving fields outside the form."""
        selected = self._selected_ids()
        if len(selected) != 1:
            return
        try:
            record = self._service.get(selected[0])
        except _REFERENCE_ERRORS as error:
            self.status_label.setText(str(error))
            return
        dialog = _ReferenceEditor(
            self, record, partial(self._service.update, selected[0])
        )
        if dialog.exec() == QDialog.Accepted and dialog.record is not None:
            self._added([dialog.record])

    def _attach_pdf(self) -> None:
        """Attach a chosen existing PDF to one selected reference."""
        selected = self._selected_ids()
        if len(selected) != 1:
            return
        filename, _ = QFileDialog.getOpenFileName(self, "Attach PDF", "", "PDF (*.pdf)")
        if not filename:
            return
        try:
            self._service.attach_pdf(selected[0], Path(filename))
        except _REFERENCE_ERRORS as error:
            self.status_label.setText(str(error))
            return
        self.refresh(selected)
        self.status_label.setText(f"Attached {Path(filename).name}.")
        self.references_changed.emit()

    def _copy_bibtex(self) -> None:
        """Replace the clipboard only after the full selection exports."""
        selected = self._selected_ids()
        if not selected:
            return
        try:
            text = self._service.export_bibtex(selected)
        except _REFERENCE_ERRORS as error:
            self.status_label.setText(str(error))
            return
        QApplication.clipboard().setText(text)
        self.status_label.setText(f"Copied {len(selected)} reference(s).")

    def _export_bibtex(self) -> None:
        """Save complete BibTeX atomically; preserve the destination on failure."""
        selected = self._selected_ids()
        if not selected:
            return
        try:
            text = self._service.export_bibtex(selected)
        except _REFERENCE_ERRORS as error:
            self.status_label.setText(str(error))
            return
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export selected references", "references.bib", "BibTeX (*.bib)"
        )
        if not filename:
            return
        output = QSaveFile(filename)
        payload = text.encode("utf-8")
        if not output.open(QIODevice.WriteOnly):
            self.status_label.setText(output.errorString())
            return
        if output.write(payload) != len(payload):
            self.status_label.setText(output.errorString())
            output.cancelWriting()
            return
        if not output.commit():
            self.status_label.setText(output.errorString())
            return
        self.status_label.setText(
            f"Exported {len(selected)} reference(s) to {filename}."
        )

    def _release_catalog(self) -> None:
        """Close only catalog connections created by this dialog."""
        if self._owns_catalog and not self._catalog_closed:
            self._catalog.close()
            self._catalog_closed = True
