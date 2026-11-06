"""One window that exports library papers as BibTeX, citation text, or a list."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..bibtex.sanitize import DEFAULT_EXCLUDED_FIELDS
from ..citations import CITATION_FIELD_HELP, CitationFormatter, CitationStyle
from ..citekeys import KeyPolicy, KeyStyle
from ..config import AuthenticationError, Config
from ..references import CitationKeyError, LocalReferences
from .bibtex_export import (
    STYLE_HELP,
    export_bibtex_asking_keys,
    key_style_combo,
    save_text_file,
)
from .cv_dialog import CVDialog

FORMAT_BIBTEX = "bibtex"
FORMAT_CITATION = "citation"
FORMAT_LIST = "list"
# Format key, menu label, and the file type offered by Save As.
FORMATS: tuple[tuple[str, str, str, str], ...] = (
    (FORMAT_BIBTEX, "BibTeX (.bib)", "references.bib", "BibTeX (*.bib)"),
    (
        FORMAT_CITATION,
        "Citation text (custom style)",
        "citations.txt",
        "Text (*.txt);;All files (*)",
    ),
    (
        FORMAT_LIST,
        "Publication list (LaTeX, HTML, Markdown, or text)",
        "publications.txt",
        "All files (*)",
    ),
)
# Number of papers shown in the preview.
PREVIEW_PAPERS = 3
# Key template that matches the default Rename filenames.
FILENAME_KEY_TEMPLATE = "{authors}_{bibcode}"
# Choices for {authors} in a key template: label and author format.
AUTHOR_FORMATS = (
    ("Surname + initials (DoeJA)", "surname_initials"),
    ("Surname (Doe)", "surname"),
    ("Last, First (Doe, John A.)", "last_comma_first"),
)
KEY_TEMPLATE_FIELDS = (
    "Fields: {authors}, {first_author_surname}, {first_author_initials}, "
    "{year}, {bibcode}, {title}, {journal}, {bibstem}, {pub}, {volume}, "
    "{page}. Hover for details. Characters that keys cannot hold become _."
)


@dataclass(frozen=True)
class ExportScope:
    """One choice of papers for the export.

    `key` is a stable name, for example ``"selected"`` or ``"all"``. `label`
    is the text of the choice, without the count. `records` holds library
    table records in export order.
    """

    key: str
    label: str
    records: Sequence[Mapping[str, Any]]


@dataclass(frozen=True)
class PaperSummary:
    """Papers that an export writes, and PDFs that it skips.

    `papers` holds one record for each distinct catalog paper, in export
    order. `ads_papers` counts papers with an ADS bibcode, and `other_papers`
    counts the rest, for example papers from Crossref or DataCite. `skipped`
    holds PDFs without paper details, which do not export. `shared_pdfs`
    counts PDFs whose paper an earlier PDF already supplies.
    """

    papers: tuple[Mapping[str, Any], ...]
    ads_papers: int
    other_papers: int
    skipped: tuple[Mapping[str, Any], ...]
    shared_pdfs: int

    @property
    def document_ids(self) -> list[int]:
        """Return the catalog document IDs of the papers."""
        return [int(paper["document_id"]) for paper in self.papers]


def _count(number: int, noun: str) -> str:
    """Return ``1 paper`` or ``2 papers`` with thousands separators."""
    return f"{number:,} {noun}" if number == 1 else f"{number:,} {noun}s"


def _title(record: Mapping[str, Any]) -> str:
    metadata = record.get("metadata")
    value = metadata.get("title") if isinstance(metadata, Mapping) else None
    value = value or record.get("title")
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value or record.get("current_path") or "")


def _bibcode(record: Mapping[str, Any]) -> str:
    value = record.get("bibcode")
    if not value:
        metadata = record.get("metadata")
        value = metadata.get("bibcode") if isinstance(metadata, Mapping) else None
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    return str(value or "").strip()


def summarize_papers(records: Sequence[Mapping[str, Any]]) -> PaperSummary:
    """Split table records into exported papers and skipped PDFs.

    A record exports when it has a catalog paper (a document ID). Several PDFs
    of one paper export once, through the first of them.
    """
    papers: list[Mapping[str, Any]] = []
    seen: set[int] = set()
    skipped: list[Mapping[str, Any]] = []
    shared = 0
    for record in records:
        document_id = record.get("document_id")
        if document_id is None:
            skipped.append(record)
            continue
        if int(document_id) in seen:
            shared += 1
            continue
        seen.add(int(document_id))
        papers.append(record)
    ads = sum(1 for paper in papers if _bibcode(paper))
    return PaperSummary(
        papers=tuple(papers),
        ads_papers=ads,
        other_papers=len(papers) - ads,
        skipped=tuple(skipped),
        shared_pdfs=shared,
    )


class SkippedPdfsDialog(QDialog):
    """List the PDFs that an export skips because they have no paper details."""

    def __init__(
        self,
        parent: QWidget | None,
        records: Sequence[Mapping[str, Any]],
        show_unidentified: Callable[[], None] | None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("PDFs Without Paper Details")
        layout = QVBoxLayout(self)
        intro = QLabel(
            f"{len(records)} PDFs have no paper details, so the export skips "
            "them. Run Identify on them, or select one and use Update by ID… "
            "or Enter Details….",
            self,
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.paths = QPlainTextEdit(self)
        self.paths.setReadOnly(True)
        self.paths.setPlainText(
            "\n".join(
                str(record.get("current_path") or record.get("relative_path") or "")
                for record in records
            )
        )
        layout.addWidget(self.paths, 1)
        buttons = QHBoxLayout()
        copy = QPushButton("Copy Paths", self)
        copy.clicked.connect(
            lambda: QApplication.clipboard().setText(self.paths.toPlainText())
        )
        buttons.addWidget(copy)
        if show_unidentified is not None:
            view = QPushButton("Show Needs Identification", self)
            view.setToolTip("Close the export and show the PDFs that need Identify.")
            view.clicked.connect(lambda: (self.done(2), show_unidentified()))
            buttons.addWidget(view)
        buttons.addStretch(1)
        close = QPushButton("Close", self)
        close.clicked.connect(self.reject)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.resize(640, 420)


class AdsBibtexFetch(QThread):
    """Fetch ADS's own BibTeX for papers in a worker thread.

    The thread only talks to ADS. The window saves the result in the catalog,
    because a catalog connection belongs to the thread that opened it.
    """

    progressed = Signal(int, int)
    fetched = Signal(object, object)

    def __init__(self, bibcodes: Sequence[str], parent: Any = None) -> None:
        super().__init__(parent)
        self._bibcodes = list(bibcodes)

    def run(self) -> None:  # noqa: D102 -- QThread entry point
        from ..client import ADSClient

        try:
            client = ADSClient()
            try:
                entries = client.export.export_entries(
                    self._bibcodes,
                    cancelled=self.isInterruptionRequested,
                    progress=self.progressed.emit,
                )
            finally:
                client.close()
        except Exception as exc:  # noqa: BLE001 -- report every failure in the window
            self.fetched.emit({}, str(exc) or type(exc).__name__)
            return
        self.fetched.emit(entries, None)


# Running fetches whose window closed. They finish, then save nothing.
_ORPHAN_FETCHES: set[AdsBibtexFetch] = set()


class ExportPapersDialog(QDialog):
    """Choose papers, a format, and its settings, then copy or save the text.

    Parameters
    ----------
    parent : QWidget or None
        Parent widget.
    scopes : Sequence[ExportScope]
        Paper choices. The first scope whose key equals `initial_scope`
        starts selected.
    catalog : object
        Central `PdfCatalog` that holds the papers.
    citation_styles : Sequence[CitationStyle]
        Custom citation styles for the citation text format.
    initial_scope : str, optional
        Key of the scope to select first.
    initial_format : str, optional
        Format key to select first.
    browser_command : str or None, optional
        Browser command for links in the list preview.
    show_unidentified : Callable[[], None] or None, optional
        Show the PDFs that need identification in the main window.
    exclude_fields : Sequence[str], optional
        BibTeX fields to leave out at first, by default the abstract and the
        ADS note.
    """

    def __init__(
        self,
        parent: QWidget | None,
        scopes: Sequence[ExportScope],
        catalog: Any,
        citation_styles: Sequence[CitationStyle],
        *,
        initial_scope: str = "selected",
        initial_format: str = FORMAT_BIBTEX,
        browser_command: str | None = None,
        show_unidentified: Callable[[], None] | None = None,
        exclude_fields: Sequence[str] = DEFAULT_EXCLUDED_FIELDS,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export Papers")
        self._initial_exclude = list(exclude_fields)
        self._scopes = list(scopes)
        self._catalog = catalog
        # One service keeps its cached reference list across previews.
        self._references = LocalReferences(catalog)
        self._styles = list(citation_styles)
        self._show_unidentified = show_unidentified
        self._formatter = CitationFormatter()
        self.summary = summarize_papers(())
        self._fetch: AdsBibtexFetch | None = None
        self._fetch_requests: dict[str, str] = {}
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(200)
        self._preview_timer.timeout.connect(self._update_preview)
        layout = QVBoxLayout(self)
        layout.addWidget(self._build_scope_box(initial_scope))
        format_row = QFormLayout()
        self.format_choice = QComboBox(self)
        for key, label, _name, _filter in FORMATS:
            self.format_choice.addItem(label, key)
        self.format_choice.setToolTip("The kind of text to export")
        format_row.addRow("Format", self.format_choice)
        layout.addLayout(format_row)
        self.panels = QStackedWidget(self)
        self.panels.addWidget(self._build_bibtex_panel())
        self.panels.addWidget(self._build_citation_panel())
        self.list_dialog = CVDialog(self, records=(), browser_command=browser_command)
        self.panels.addWidget(self._build_list_panel())
        # Tall settings scroll, and the divider moves space to the preview.
        settings = QScrollArea(self)
        settings.setWidgetResizable(True)
        settings.setFrameShape(QFrame.NoFrame)
        settings.setWidget(self.panels)
        preview_box = QWidget(self)
        preview_layout = QVBoxLayout(preview_box)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.addWidget(
            QLabel(f"Preview (first {PREVIEW_PAPERS} papers)", preview_box)
        )
        self.preview = QPlainTextEdit(preview_box)
        self.preview.setReadOnly(True)
        self.preview.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.preview.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        preview_layout.addWidget(self.preview, 1)
        self.splitter = QSplitter(Qt.Vertical, self)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(settings)
        self.splitter.addWidget(preview_box)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        layout.addWidget(self.splitter, 1)
        buttons = QHBoxLayout()
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        buttons.addWidget(self.status, 1)
        cancel = QPushButton("Cancel", self)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.copy_button = QPushButton("Copy", self)
        self.copy_button.setToolTip("Copy the exported text of all chosen papers")
        self.copy_button.clicked.connect(self._copy)
        buttons.addWidget(self.copy_button)
        self.save_button = QPushButton("Save As…", self)
        self.save_button.setDefault(True)
        self.save_button.setToolTip("Write the exported text of all chosen papers")
        self.save_button.clicked.connect(self._save)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)
        index = self.format_choice.findData(initial_format)
        self.format_choice.setCurrentIndex(max(0, index))
        self.format_choice.currentIndexChanged.connect(self._format_changed)
        self._format_changed()
        self._scope_changed()
        self.resize(760, 760)

    # Layout -------------------------------------------------------------

    def _build_scope_box(self, initial_scope: str) -> QGroupBox:
        box = QGroupBox("Papers", self)
        layout = QVBoxLayout(box)
        self.scope_group = QButtonGroup(box)
        self.scope_buttons: dict[str, QRadioButton] = {}
        for index, scope in enumerate(self._scopes):
            button = QRadioButton(f"{scope.label} ({len(scope.records):,} PDFs)", box)
            button.setEnabled(bool(scope.records))
            self.scope_group.addButton(button, index)
            self.scope_buttons[scope.key] = button
            layout.addWidget(button)
        start = self.scope_buttons.get(initial_scope)
        if start is None or not start.isEnabled():
            start = next(
                (
                    button
                    for button in self.scope_buttons.values()
                    if button.isEnabled()
                ),
                None,
            )
        if start is not None:
            start.setChecked(True)
        self.scope_group.idToggled.connect(
            lambda _id, checked: checked and self._scope_changed()
        )
        self.summary_label = QLabel(box)
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)
        skipped_row = QHBoxLayout()
        self.skipped_label = QLabel(box)
        self.skipped_label.setWordWrap(True)
        skipped_row.addWidget(self.skipped_label, 1)
        self.skipped_button = QPushButton("Show…", box)
        self.skipped_button.setToolTip("List the skipped PDFs")
        self.skipped_button.clicked.connect(self._show_skipped)
        skipped_row.addWidget(self.skipped_button)
        layout.addLayout(skipped_row)
        return box

    def _build_bibtex_panel(self) -> QWidget:
        panel = QGroupBox("BibTeX", self)
        form = QFormLayout(panel)
        policy = Config.load_key_policy()
        self.ads_style = key_style_combo(panel, policy.ads, include_bibcode=True)
        self.ads_style.setToolTip("Key style for papers with an ADS bibcode")
        self.other_style = key_style_combo(panel, policy.other, include_bibcode=False)
        self.other_style.setToolTip(
            "Key style for papers without an ADS bibcode, such as papers from "
            "Crossref, DataCite, or details that you entered"
        )
        template_row = QHBoxLayout()
        self.key_template = QLineEdit(policy.template, panel)
        self.key_template.setToolTip(CITATION_FIELD_HELP)
        template_row.addWidget(self.key_template, 1)
        self.filename_style_button = QPushButton("Filename Style", panel)
        self.filename_style_button.setToolTip(
            "Make ADS keys like the default PDF filenames, for example "
            "DoeJA+RoeA_2021A&A...650A.123D"
        )
        self.filename_style_button.clicked.connect(self._use_filename_style)
        template_row.addWidget(self.filename_style_button)
        self.fields_label = QLabel(KEY_TEMPLATE_FIELDS, panel)
        self.fields_label.setWordWrap(True)
        self.fields_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.fields_label.setToolTip(CITATION_FIELD_HELP)
        authors_row = QHBoxLayout()
        self.author_format = QComboBox(panel)
        for label, value in AUTHOR_FORMATS:
            self.author_format.addItem(label, value)
        self.author_format.setCurrentIndex(
            max(0, self.author_format.findData(policy.author_format))
        )
        self.max_authors = QSpinBox(panel)
        self.max_authors.setRange(1, 99)
        self.max_authors.setValue(policy.max_authors)
        self.max_authors.setToolTip("Show all authors up to this number")
        self.authors_kept = QSpinBox(panel)
        self.authors_kept.setRange(1, 99)
        self.authors_kept.setValue(policy.authors_when_truncated)
        self.authors_kept.setToolTip("Authors kept when the list is longer")
        self.author_separator = QLineEdit(policy.author_separator, panel)
        self.author_separator.setToolTip("Text between authors")
        self.author_suffix = QLineEdit(policy.author_truncation_suffix, panel)
        self.author_suffix.setToolTip("Text after a shortened author list")
        for widget in (self.author_separator, self.author_suffix):
            widget.setMaximumWidth(48)
        for label, widget in (
            ("", self.author_format),
            ("up to", self.max_authors),
            ("else keep", self.authors_kept),
            ("join", self.author_separator),
            ("suffix", self.author_suffix),
        ):
            if label:
                authors_row.addWidget(QLabel(label, panel))
            authors_row.addWidget(widget)
        authors_row.addStretch(1)
        self.keep_key_styles = QCheckBox("Keep these key styles as default", panel)
        self.keep_key_styles.setChecked(True)
        self.keep_key_styles.setToolTip(
            "Save the key styles for later exports. A key that you typed, or "
            "that came with an imported BibTeX entry, always wins."
        )
        self.key_help = QLabel(panel)
        self.key_help.setWordWrap(True)
        self.exclude_fields = QLineEdit(", ".join(self._initial_exclude), panel)
        self.exclude_fields.setToolTip(
            "Fields to leave out of every entry, separated by commas, for "
            "example abstract, adsnote. Clear it to export every field."
        )
        self.present_fields = QLabel(panel)
        self.present_fields.setWordWrap(True)
        form.addRow("ADS papers", self.ads_style)
        form.addRow("Other papers", self.other_style)
        form.addRow("", self.key_help)
        form.addRow("Leave out fields", self.exclude_fields)
        form.addRow("", self.present_fields)
        form.addRow("Template", template_row)
        # A wrapped label in a spanning row gets its real height.
        form.addRow(self.fields_label)
        form.addRow("{authors}", authors_row)
        form.addRow("", self.keep_key_styles)
        for combo in (self.ads_style, self.other_style, self.author_format):
            combo.currentIndexChanged.connect(self._settings_changed)
        for spin in (self.max_authors, self.authors_kept):
            spin.valueChanged.connect(self._settings_changed)
        for edit in (
            self.key_template,
            self.author_separator,
            self.author_suffix,
            self.exclude_fields,
        ):
            edit.textChanged.connect(self._settings_changed)
        return panel

    def _use_filename_style(self) -> None:
        """Set the ADS key style to the default filename pattern."""
        self.key_template.setText(FILENAME_KEY_TEMPLATE)
        self.author_format.setCurrentIndex(
            max(0, self.author_format.findData("surname_initials"))
        )
        self.author_separator.setText("+")
        self.author_suffix.setText("+")
        self.ads_style.setCurrentIndex(self.ads_style.findData(KeyStyle.TEMPLATE))

    @property
    def excluded_fields(self) -> list[str]:
        """Return the field names to leave out, in lowercase."""
        return [
            name.strip().lower()
            for name in self.exclude_fields.text().split(",")
            if name.strip()
        ]

    def _build_citation_panel(self) -> QWidget:
        panel = QGroupBox("Citation text", self)
        form = QFormLayout(panel)
        self.style_choice = QComboBox(panel)
        for style in self._styles:
            self.style_choice.addItem(style.name, style.id)
        self.style_choice.setEnabled(bool(self._styles))
        self.style_choice.setToolTip(
            "Write one line for each paper with this style. Edit styles in "
            "Tools → Custom Citation Styles…."
        )
        self.style_choice.currentIndexChanged.connect(self._settings_changed)
        form.addRow("Style", self.style_choice)
        if not self._styles:
            form.addRow(
                "",
                QLabel(
                    "No custom styles exist. Create one in Tools → Custom "
                    "Citation Styles….",
                    panel,
                ),
            )
        return panel

    def _build_list_panel(self) -> QWidget:
        panel = QGroupBox("Publication list", self)
        layout = QVBoxLayout(panel)
        tabs = self.list_dialog.tabs
        # Copy and Save As in this window replace the list window's Output tab.
        for index in range(tabs.count() - 1, -1, -1):
            if tabs.tabText(index) == "Output":
                tabs.removeTab(index)
        tabs.setParent(panel)
        layout.addWidget(tabs)
        self.list_dialog.settings_changed.connect(self._settings_changed)
        return panel

    # State --------------------------------------------------------------

    @property
    def format_key(self) -> str:
        """Return the selected format key."""
        return str(self.format_choice.currentData())

    def _current_scope(self) -> ExportScope | None:
        index = self.scope_group.checkedId()
        return self._scopes[index] if 0 <= index < len(self._scopes) else None

    def _scope_changed(self) -> None:
        scope = self._current_scope()
        self.summary = summarize_papers(scope.records if scope is not None else ())
        summary = self.summary
        shared = (
            f" Each paper exports once ({_count(summary.shared_pdfs, 'duplicate PDF')}"
            " skipped)."
            if summary.shared_pdfs
            else ""
        )
        self.summary_label.setText(
            f"Exports {_count(len(summary.papers), 'paper')}: "
            f"{_count(summary.ads_papers, 'ADS paper')} and "
            f"{_count(summary.other_papers, 'other paper')}.{shared}"
        )
        skipped = len(summary.skipped)
        self.skipped_label.setText(
            f"Skips {_count(skipped, 'PDF')} without paper details."
            if skipped
            else "Every PDF has paper details."
        )
        self.skipped_button.setVisible(bool(skipped))
        # The list dialog renders only what `render` passes it. Giving it all
        # papers would make its hidden preview render the whole list.
        self._settings_changed()
        self._fetch_ads_bibtex()

    def _fetch_ads_bibtex(self) -> None:
        """Get ADS's own BibTeX once for papers that have no saved copy."""
        if self.format_key != FORMAT_BIBTEX or self._fetch is not None:
            return
        try:
            requests = self._references.ads_bibtex_requests(self.summary.document_ids)
        except (ValueError, sqlite3.Error) as exc:
            self.status.setText(f"Cannot read saved ADS BibTeX: {exc}")
            return
        if not requests:
            return
        try:
            Config.get_token(prompt=False)
        except AuthenticationError:
            self.status.setText(
                f"{_count(len(requests), 'ADS paper')} use local fields. "
                "Add an ADS token to get ADS's own BibTeX."
            )
            return
        self._fetch_requests = requests
        self._fetch = AdsBibtexFetch(list(requests), self)
        self._fetch.progressed.connect(self._fetch_progressed)
        self._fetch.fetched.connect(self._fetch_finished)
        self.status.setText(
            f"Getting ADS BibTeX for {_count(len(requests), 'paper')}. "
            "ADSfolio saves it on this computer, so this happens once."
        )
        self._update_preview()
        self._fetch.start()

    def _fetch_progressed(self, done: int, total: int) -> None:
        self.status.setText(
            f"Getting ADS BibTeX: {done:,} of {total:,} papers. "
            "ADSfolio saves it on this computer, so this happens once."
        )

    def _fetch_finished(self, entries: dict[str, str], error: str | None) -> None:
        fetch, requests = self._fetch, self._fetch_requests
        self._fetch, self._fetch_requests = None, {}
        if fetch is not None:
            fetch.wait()
            fetch.deleteLater()
        if error is not None:
            self.status.setText(
                f"Could not get ADS BibTeX: {error} "
                f"{_count(len(requests), 'paper')} use local fields for now."
            )
        else:
            try:
                saved = self._references.store_ads_bibtex(entries, requests)
            except (ValueError, sqlite3.Error) as exc:
                self.status.setText(f"Could not save ADS BibTeX: {exc}")
            else:
                missing = len(requests) - saved
                self.status.setText(
                    f"Saved ADS BibTeX for {_count(saved, 'paper')} on this computer."
                    + (
                        f" ADS has no BibTeX for {_count(missing, 'paper')}. "
                        "They use local fields."
                        if missing
                        else ""
                    )
                )
        self._update_preview()

    def _format_changed(self) -> None:
        keys = [key for key, _label, _name, _filter in FORMATS]
        index = keys.index(self.format_key)
        # Hidden panels must not claim height, so the preview keeps its space.
        for position in range(self.panels.count()):
            self.panels.widget(position).setSizePolicy(
                QSizePolicy.Preferred,
                QSizePolicy.Preferred if position == index else QSizePolicy.Ignored,
            )
        self.panels.setCurrentIndex(index)
        self.panels.adjustSize()
        self._settings_changed()
        self._fetch_ads_bibtex()

    def _settings_changed(self, *_args: object) -> None:
        self._preview_timer.start()

    def key_policy(self) -> KeyPolicy:
        """Return the chosen key styles.

        Raises
        ------
        ValueError
            If the key template is not valid.
        """
        return KeyPolicy(
            ads=self.ads_style.currentData(),
            other=self.other_style.currentData(),
            template=self.key_template.text().strip(),
            author_format=str(self.author_format.currentData()),
            max_authors=self.max_authors.value(),
            authors_when_truncated=self.authors_kept.value(),
            author_separator=self.author_separator.text(),
            author_truncation_suffix=self.author_suffix.text(),
        )

    def _citation_style(self) -> CitationStyle | None:
        style_id = self.style_choice.currentData()
        return next((style for style in self._styles if style.id == style_id), None)

    # Rendering ----------------------------------------------------------

    def _update_preview(self) -> None:
        """Show the first papers in the current format, or the reason it fails."""
        uses_template = KeyStyle.TEMPLATE in (
            self.ads_style.currentData(),
            self.other_style.currentData(),
        )
        for widget in (
            self.key_template,
            self.fields_label,
            self.author_format,
            self.max_authors,
            self.authors_kept,
            self.author_separator,
            self.author_suffix,
        ):
            widget.setEnabled(uses_template)
        self.authors_kept.setMaximum(self.max_authors.value())
        if self.format_key == FORMAT_BIBTEX:
            self._show_present_fields()
        self.key_help.setText(
            "ADS papers: "
            + STYLE_HELP[self.ads_style.currentData()]
            + "\nOther papers: "
            + STYLE_HELP[self.other_style.currentData()]
        )
        papers = self.summary.papers
        ready = bool(papers)
        try:
            text = self.render(papers[:PREVIEW_PAPERS], preview=True)
        except CitationKeyError:
            text = self._bibtex_preview_by_paper(papers[:PREVIEW_PAPERS])
        except (ValueError, sqlite3.Error) as exc:
            text = f"Cannot render the preview: {exc}"
            ready = False
        if not papers:
            text = "No papers with details to export."
        # Copy and Save wait for ADS BibTeX, so the file has ADS's entries.
        ready = ready and not (self.format_key == FORMAT_BIBTEX and self._fetch)
        self.preview.setPlainText(text)
        self.copy_button.setEnabled(ready)
        self.save_button.setEnabled(ready)

    def _show_present_fields(self) -> None:
        """List the BibTeX fields that the chosen papers hold."""
        ids = set(self.summary.document_ids)
        try:
            references = self._references._export_references()
        except (ValueError, sqlite3.Error):
            self.present_fields.setText("")
            return
        names = sorted(
            {
                name.lower()
                for reference in references
                if reference["id"] in ids
                for name in reference["fields"]
            }
        )
        self.present_fields.setText(
            "Fields in these papers: " + ", ".join(names) if names else ""
        )

    def _bibtex_preview_by_paper(self, papers: Sequence[Mapping[str, Any]]) -> str:
        """Preview each paper alone, and name the papers that need a key."""
        service = self._references
        service.policy = self.key_policy()
        parts = []
        for paper in papers:
            try:
                parts.append(
                    service.export_bibtex(
                        [int(paper["document_id"])],
                        exclude_fields=self.excluded_fields,
                    ).strip()
                )
            except CitationKeyError:
                parts.append(
                    f"% {_title(paper)}: this paper needs a citation key. "
                    "ADSfolio asks for it when you copy or save."
                )
        return "\n\n".join(parts)

    def render(
        self, papers: Sequence[Mapping[str, Any]], *, preview: bool = False
    ) -> str:
        """Return the exported text for `papers` in the current format.

        Raises
        ------
        CitationKeyError
            When BibTeX keys are missing or shared and `preview` is true.
        ValueError, sqlite3.Error
            When the settings or catalog cannot produce the text.
        """
        if self.format_key == FORMAT_BIBTEX:
            service = self._references
            service.policy = self.key_policy()
            ids = [int(paper["document_id"]) for paper in papers]
            if preview:
                return service.export_bibtex(ids, exclude_fields=self.excluded_fields)
            return (
                export_bibtex_asking_keys(self, service, ids, self.excluded_fields)
                or ""
            )
        if self.format_key == FORMAT_CITATION:
            style = self._citation_style()
            if style is None:
                raise ValueError("Create a custom citation style first.")
            return "\n".join(self._formatter.render(style, paper) for paper in papers)
        return self.list_dialog.render_list(papers)

    def _export_text(self) -> str | None:
        """Return the text of every chosen paper, or `None` when cancelled."""
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            text = self.render(self.summary.papers)
        except (ValueError, sqlite3.Error) as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Export papers", str(exc))
            return None
        QApplication.restoreOverrideCursor()
        if not text:
            return None
        if self.format_key == FORMAT_BIBTEX and self.keep_key_styles.isChecked():
            try:
                Config.save_key_policy(self.key_policy())
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Citation keys not saved", str(exc))
        if self.format_key == FORMAT_LIST:
            self.list_dialog.save_settings()
        return text

    def _copy(self) -> None:
        text = self._export_text()
        if text is not None:
            QApplication.clipboard().setText(text)
            self.status.setText(f"Copied {len(self.summary.papers):,} papers.")

    def _save(self) -> None:
        text = self._export_text()
        if text is None:
            return
        _key, _label, default_name, file_filter = next(
            item for item in FORMATS if item[0] == self.format_key
        )
        try:
            filename = save_text_file(
                self, text, "Export Papers", default_name, file_filter
            )
        except OSError as exc:
            QMessageBox.warning(self, "Export papers", str(exc))
            return
        if filename:
            self.status.setText(
                f"Saved {len(self.summary.papers):,} papers to {filename}."
            )

    def _show_skipped(self) -> None:
        dialog = SkippedPdfsDialog(self, self.summary.skipped, self._show_unidentified)
        if dialog.exec() == 2:
            self.reject()

    def done(self, result: int) -> None:
        """Keep the list settings when the window closes."""
        self.list_dialog.save_settings()
        fetch = self._fetch
        if fetch is not None:
            # Let the network request finish without the closed window.
            self._fetch = None
            fetch.fetched.disconnect(self._fetch_finished)
            fetch.progressed.disconnect(self._fetch_progressed)
            fetch.requestInterruption()
            fetch.setParent(None)
            _ORPHAN_FETCHES.add(fetch)
            fetch.finished.connect(lambda: _ORPHAN_FETCHES.discard(fetch))
        super().done(result)
