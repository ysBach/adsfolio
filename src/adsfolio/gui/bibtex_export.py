"""BibTeX export that asks the user for missing or shared citation keys."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from PySide6.QtCore import QIODevice, QSaveFile, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..citations import CITATION_FIELD_HELP
from ..citekeys import KeyPolicy, KeyStyle, citation_key
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


_EXAMPLE_PAPERS: tuple[Mapping[str, Any], ...] = (
    {
        "bibcode": "2020ApJ...900....1K",
        "doi": ["10.3847/1538-4357/abcdef"],
        "title": ["The Dust of Small Stars"],
        "author": ["Kim, Sun"],
        "year": "2020",
    },
    {
        "doi": ["10.5555/example.2021"],
        "title": ["On the Theory of Dust"],
        "author": ["Roe, Alice"],
        "year": "2021",
    },
)

_STYLE_HELP = {
    KeyStyle.BIBCODE: "The ADS bibcode, for example 2020ApJ...900....1K.",
    KeyStyle.DOI: "The DOI, for example 10.5555/example.2021.",
    KeyStyle.TEMPLATE: "Your template below, for example {first_author_initials}{year}.",
    KeyStyle.SCHOLAR: (
        "Surname, year, and the first title word that is not a short word "
        "such as 'on' or 'the', for example roe2021theory."
    ),
    KeyStyle.MANUAL: "You type a key for each paper when you export it.",
}


class CitationKeySettingsDialog(QDialog):
    """Choose the citation key style for ADS papers and for other papers.

    A key that the user typed, or that came with an imported BibTeX entry,
    always wins over these styles.

    Parameters
    ----------
    parent : `QWidget` or `None`
        Parent widget.
    policy : `KeyPolicy`
        The current styles.
    samples : sequence of mapping, optional
        Paper metadata for the preview. The default is two example papers.
    """

    def __init__(
        self,
        parent: QWidget | None,
        policy: KeyPolicy,
        samples: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Citation Keys")
        self._samples = list(samples) or list(_EXAMPLE_PAPERS)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Choose how BibTeX export makes citation keys. A key that you "
            "typed, or that came with an imported BibTeX entry, always wins.",
            self,
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self.ads_style = self._style_combo(policy.ads, include_bibcode=True)
        self.ads_style.setToolTip("Key style for papers with an ADS bibcode")
        self.other_style = self._style_combo(policy.other, include_bibcode=False)
        self.other_style.setToolTip(
            "Key style for papers without an ADS bibcode, such as papers that "
            "ADSfolio found through Crossref or DataCite"
        )
        self.template = QLineEdit(policy.template, self)
        self.template.setToolTip(CITATION_FIELD_HELP)
        form.addRow("ADS papers:", self.ads_style)
        form.addRow("Papers outside ADS:", self.other_style)
        form.addRow("My template:", self.template)
        layout.addLayout(form)
        self.help_label = QLabel(self)
        self.help_label.setWordWrap(True)
        layout.addWidget(self.help_label)
        self.preview = QLabel(self)
        self.preview.setWordWrap(True)
        self.preview.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.preview.setToolTip("Keys for the selected papers, or for example papers")
        layout.addWidget(self.preview)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        for combo in (self.ads_style, self.other_style):
            combo.currentIndexChanged.connect(lambda _index: self._update())
        self.template.textChanged.connect(lambda _text: self._update())
        self.setMinimumWidth(560)
        self._update()

    def _style_combo(self, current: KeyStyle, *, include_bibcode: bool) -> QComboBox:
        combo = QComboBox(self)
        for style in KeyStyle:
            if style is KeyStyle.BIBCODE and not include_bibcode:
                continue
            combo.addItem(style.label, style)
            combo.setItemData(
                combo.count() - 1, _STYLE_HELP[style], Qt.ItemDataRole.ToolTipRole
            )
        combo.setCurrentIndex(max(0, combo.findData(current)))
        return combo

    def policy(self) -> KeyPolicy:
        """Return the chosen styles.

        Raises
        ------
        ValueError
            If the template is not valid.
        """
        return KeyPolicy(
            ads=self.ads_style.currentData(),
            other=self.other_style.currentData(),
            template=self.template.text().strip(),
        )

    def _update(self) -> None:
        uses_template = KeyStyle.TEMPLATE in (
            self.ads_style.currentData(),
            self.other_style.currentData(),
        )
        self.template.setEnabled(uses_template)
        self.help_label.setText(
            "ADS papers: "
            + _STYLE_HELP[self.ads_style.currentData()]
            + "\nPapers outside ADS: "
            + _STYLE_HELP[self.other_style.currentData()]
        )
        save = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        try:
            policy = self.policy()
        except ValueError as exc:
            self.preview.setText(str(exc))
            save.setEnabled(False)
            return
        save.setEnabled(True)
        lines = []
        for sample in self._samples[:5]:
            key = citation_key(sample, policy) or "(you type it at export)"
            title = sample.get("title")
            title = title[0] if isinstance(title, list) and title else title or ""
            lines.append(f"{key}  —  {title}")
        self.preview.setText("Preview:\n" + "\n".join(lines))
        self._fit_height()

    def _fit_height(self) -> None:
        """Grow the dialog so wrapped help and preview text stay visible."""
        layout = self.layout()
        layout.activate()
        width = max(self.width(), self.minimumWidth())
        needed = layout.totalHeightForWidth(width) if layout.hasHeightForWidth() else 0
        needed = max(needed, layout.sizeHint().height())
        if self.height() < needed:
            self.resize(width, needed)

    def showEvent(self, event: Any) -> None:  # noqa: N802 -- Qt override
        """Fit the height once the dialog has its real width."""
        super().showEvent(event)
        self._fit_height()


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
