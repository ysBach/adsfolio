"""BibTeX export that asks the user for missing or shared citation keys."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from PySide6.QtCore import QIODevice, QSaveFile, Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..references import CitationKeyError, LocalReferences

_KEY_COLUMN = 3
_FORBIDDEN_KEY_CHARACTERS = set(' \t,{}()=\\#%"')


class CitationKeyDialog(QDialog):
    """Ask for a citation key for each paper that has none or shares one.

    Parameters
    ----------
    parent : `QWidget` or `None`
        Parent widget.
    error : `CitationKeyError`
        The export problem, with the selected references.
    """

    def __init__(self, parent: QWidget | None, error: CitationKeyError) -> None:
        super().__init__(parent)
        self.setWindowTitle("Citation keys needed")
        assignment = error.assignment
        shared = {
            document_id: key
            for key, ids in assignment.clashes.items()
            for document_id in ids
        }
        self._ids = [*assignment.missing, *shared]
        # Keys of the other selected papers stay fixed and must stay unique.
        self._fixed_keys = {
            key
            for document_id, key in assignment.keys.items()
            if document_id not in shared
        }
        layout = QVBoxLayout(self)
        message = QLabel(
            "Each paper needs its own citation key before export. "
            "Type a key for each paper below. ADSfolio saves the keys, "
            "and later exports use them.",
            self,
        )
        message.setWordWrap(True)
        layout.addWidget(message)
        self.table = QTableWidget(len(self._ids), 4, self)
        self.table.setHorizontalHeaderLabels(["Problem", "Year", "Title", "Key"])
        self.table.horizontalHeader().setStretchLastSection(True)
        for row, document_id in enumerate(self._ids):
            fields = error.references[document_id]["fields"]
            problem = (
                f"Shares {shared[document_id]}" if document_id in shared else "No key"
            )
            values = (problem, fields.get("year", ""), fields.get("title", ""))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setToolTip(value)
                self.table.setItem(row, column, item)
            key_item = QTableWidgetItem(assignment.keys.get(document_id, ""))
            key_item.setToolTip(
                "Type a key without spaces, commas, braces, quotes, #, %, \\, or =."
            )
            self.table.setItem(row, _KEY_COLUMN, key_item)
        self.table.resizeColumnsToContents()
        layout.addWidget(self.table)
        self.problem_label = QLabel(self)
        self.problem_label.setWordWrap(True)
        layout.addWidget(self.problem_label)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setToolTip(
            "Save these keys and export"
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.table.itemChanged.connect(lambda _item: self._check())
        self.resize(720, 360)
        self._check()

    def keys(self) -> dict[int, str]:
        """Return the typed key of each listed paper."""
        return {
            document_id: self.table.item(row, _KEY_COLUMN).text().strip()
            for row, document_id in enumerate(self._ids)
        }

    def _check(self) -> None:
        """Enable Save only when every key is present, valid, and unique."""
        keys = list(self.keys().values())
        problem = ""
        if any(not key for key in keys):
            problem = "Type a key for every paper."
        elif any(_FORBIDDEN_KEY_CHARACTERS & set(key) for key in keys):
            problem = "A key holds a character that BibTeX rejects."
        elif len(set(keys)) != len(keys) or set(keys) & self._fixed_keys:
            problem = "Each key must be different from the other selected papers."
        self.problem_label.setText(problem)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(
            not problem
        )


def export_bibtex_asking_keys(
    parent: QWidget | None,
    service: LocalReferences,
    document_ids: Sequence[int],
) -> str | None:
    """Return BibTeX for `document_ids`, asking for keys when needed.

    Returns
    -------
    str or None
        The BibTeX text, or `None` when the user cancels.

    Raises
    ------
    ValueError, sqlite3.Error
        For export problems other than citation keys.
    """
    while True:
        try:
            return service.export_bibtex(document_ids)
        except CitationKeyError as error:
            dialog = CitationKeyDialog(parent, error)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return None
            _save_keys(parent, service, dialog.keys())


def _save_keys(
    parent: QWidget | None, service: LocalReferences, keys: Mapping[int, str]
) -> None:
    for document_id, key in keys.items():
        try:
            service.set_cite_key(document_id, key)
        except (ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(parent, "Citation key not saved", str(exc))
            return


def save_bibtex_file(parent: QWidget | None, text: str, title: str) -> str | None:
    """Ask for a `.bib` path and write `text` atomically.

    Returns
    -------
    str or None
        The saved path, or `None` when the user cancels.

    Raises
    ------
    OSError
        When the file cannot be written. An existing file stays unchanged.
    """
    filename, _ = QFileDialog.getSaveFileName(
        parent, title, "references.bib", "BibTeX (*.bib)"
    )
    if not filename:
        return None
    output = QSaveFile(filename)
    payload = text.encode("utf-8")
    if not output.open(QIODevice.WriteOnly):
        raise OSError(output.errorString())
    if output.write(payload) != len(payload):
        output.cancelWriting()
        raise OSError(output.errorString())
    if not output.commit():
        raise OSError(output.errorString())
    return filename


def document_ids_of(records: Sequence[Mapping[str, Any]]) -> list[int]:
    """Return the distinct catalog document IDs of table records, in order."""
    ids: list[int] = []
    for record in records:
        document_id = record.get("document_id")
        if document_id is not None and int(document_id) not in ids:
            ids.append(int(document_id))
    return ids
