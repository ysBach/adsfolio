"""Build a CV publication list in any text format from your ADS records.

The same window also exports papers selected in the library table. That mode
uses the metadata stored in the library and makes no ADS request.
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..api.search import escape_query_phrase
from ..config import Config
from ..cv import render_cv
from ..cv.config import DEFAULT_EXCLUDE_BIBSTEMS, DEFAULT_ME_FMT, config_from_mapping
from ..cv.template import ENTRY_FIELDS, FIELD_HELP, TEMPLATE_PRESETS, CVTemplate
from .jobs import CommandJob, cli_argv
from .osint import open_url

logger = logging.getLogger(__name__)

__all__ = ["CVDialog", "docs_from_records", "sample_publications"]

CV_SETTINGS_FILENAME = "cv.yaml"
SELECTION_SETTINGS_FILENAME = "cv-selection.yaml"
FORMAT_CHOICES = (
    ("LaTeX: compact", "latex", "compact"),
    ("LaTeX: detailed", "latex", "detailed"),
    ("LaTeX: bibcode first", "latex", "bibcode-first"),
    ("Markdown: built-in", "markdown", "compact"),
    ("Custom template (any text format)", "template", "compact"),
)
TEMPLATE_INDEX = len(FORMAT_CHOICES) - 1
BUILTIN_ME_FMT = {"latex": DEFAULT_ME_FMT, "markdown": "**{}**"}
TEMPLATE_PARTS = (
    (
        "header",
        "Header",
        (
            "Text written once at the top, for example a title. "
            "Placeholders: {me}, {date}, {total}, {first_count}, {co_count}."
        ),
    ),
    (
        "section",
        "Section start",
        (
            "Text written before each section, for example a heading and <ol>. "
            "Placeholders: {section} and {count}."
        ),
    ),
    (
        "entry",
        "Entry",
        (
            "Text written once for each paper. Hover over the placeholder list "
            "below to see what each placeholder contains."
        ),
    ),
    (
        "section_end",
        "Section end",
        "Text written after each section, for example </ol> or \\end{enumerate}.",
    ),
    (
        "footer",
        "Footer",
        "Text written once at the end, for example a legend or </body>.",
    ),
)
RENDER_MODES = (
    ("source", "Source"),
    ("markdown", "Markdown"),
    ("html", "HTML"),
)


def _short_name(name: str) -> str:
    """Return ``Surname, I.`` for ``Surname, Given Names``."""
    if "," not in name:
        return name.strip()
    surname, given = name.split(",", 1)
    initial = given.strip()[:1]
    return f"{surname.strip()}, {initial}." if initial else surname.strip()


def _display_name(name: str) -> str:
    """Return ``Given Surname`` for ``Surname, Given``."""
    if "," not in name:
        return name.strip()
    surname, given = name.split(",", 1)
    return f"{given.strip()} {surname.strip()}".strip()


def sample_publications(me_target: str) -> list[dict[str, Any]]:
    """Return fictional ADS records that include `me_target` as an author.

    The bibcodes use the journal code ``DEMO``, which is not an ADS journal,
    so no record is real.
    """
    me = me_target.strip() or "Doe, J."
    return [
        {
            "bibcode": "2025DEMO....3....1X",
            "title": ["Dust in comet atmospheres"],
            "author": [me, "Rivera, Ana", "Chen, Mei"],
            "first_author": [me],
            "year": "2025",
            "author_count": 3,
            "citation_count": 12,
            "bibstem": ["ApJ"],
            "volume": "980",
            "page": ["42"],
            "doi": ["10.0000/demo.1"],
            "property": ["REFEREED"],
        },
        {
            "bibcode": "2024DEMO....2....7X",
            "title": ["Optical observations of small bodies"],
            "author": ["Morgan, Lee", "Patel, Ravi", me, "Kim, Sun", "Lopez, Eva"],
            "first_author": ["Morgan, Lee"],
            "year": "2024",
            "author_count": 5,
            "citation_count": 5,
            "bibstem": ["A&A"],
            "volume": "684",
            "page": ["A80"],
            "doi": ["10.0000/demo.2"],
            "property": ["REFEREED"],
        },
        {
            "bibcode": "2023DEMO....1....3X",
            "title": ["Light scattering from planetary surfaces"],
            "author": ["Taylor, Jo", me],
            "first_author": ["Taylor, Jo"],
            "year": "2023",
            "author_count": 2,
            "citation_count": 0,
            "bibstem": ["arXiv"],
            "volume": "",
            "page": ["arXiv:2301.00001"],
            "doi": [],
            "property": ["NOT REFEREED"],
        },
    ]


def docs_from_records(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Convert library table records into CV documents, keeping their order.

    Parameters
    ----------
    records : sequence of mapping
        Records from the library table. ADS-style fields are read from the
        record's ``metadata`` mapping, then from the record itself.

    Returns
    -------
    list of dict
        Documents with the fields that the CV renderers read.
    """
    keys = (
        "bibcode",
        "title",
        "author",
        "year",
        "pub",
        "bibstem",
        "volume",
        "page",
        "doi",
        "citation_count",
        "author_count",
        "property",
    )
    docs: list[dict[str, Any]] = []
    for record in records:
        metadata = record.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        doc: dict[str, Any] = {}
        for key in keys:
            value = metadata.get(key)
            if value in (None, "", []):
                value = record.get(key)
            if value not in (None, "", []):
                doc[key] = value
        authors = doc.get("author")
        if isinstance(authors, str):
            doc["author"] = [authors]
        if doc.get("author"):
            doc["first_author"] = [doc["author"][0]]
        docs.append(doc)
    return docs


def _tip(widget: QWidget, text: str, form: QFormLayout | None = None) -> None:
    """Give `widget`, and its label in `form`, the same tooltip."""
    widget.setToolTip(text)
    if form is not None:
        label = form.labelForField(widget)
        if label is not None:
            label.setToolTip(text)


class CVDialog(QDialog):
    """Edit CV settings, preview them, and write the publication list.

    In the default mode, the settings live in ``cv.yaml`` in the ADSfolio
    configuration folder, the same file that ``adsfolio cv generate`` reads.
    **Sample papers** preview the format offline. **My ADS publications** and
    **Save CV** run ``adsfolio cv generate`` in the background.

    With `records`, the window exports those papers instead. It renders them
    from their stored metadata with a custom template, makes no ADS request,
    and keeps its settings in ``cv-selection.yaml``.

    Parameters
    ----------
    parent : `QWidget`, optional
        Parent window.
    settings_path : `Path`, optional
        Settings file. Defaults to ``cv.yaml``, or ``cv-selection.yaml`` with
        `records`, in the configuration folder.
    records : sequence of mapping, optional
        Library table records to export.
    browser_command : `str`, optional
        Configured browser for links in the rendered preview.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        settings_path: Path | None = None,
        records: Sequence[Mapping[str, Any]] | None = None,
        browser_command: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.local_docs = docs_from_records(records) if records is not None else None
        self.browser_command = browser_command
        self.setWindowTitle(
            "Export Selected Papers" if self.local_mode else "CV Generator"
        )
        self.resize(1180, 760)
        filename = (
            SELECTION_SETTINGS_FILENAME if self.local_mode else CV_SETTINGS_FILENAME
        )
        self.settings_path = settings_path or (Config.get_config_dir() / filename)
        self._raw: dict[str, Any] = {}
        # The starting point (preset) that the template began from, if any.
        self._preset_key: str | None = None
        self._job: CommandJob | None = None
        self._job_output: list[str] = []
        self._job_target: Path | None = None
        self._job_preview = False
        self._temp_dir: tempfile.TemporaryDirectory[str] | None = None
        self._ads_text: str | None = None
        self._loading = False
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(250)
        self._preview_timer.timeout.connect(self._render_sample)
        self._build()
        self._load_settings()

    @property
    def local_mode(self) -> bool:
        """Return whether the window exports selected library papers."""
        return self.local_docs is not None

    # Layout -------------------------------------------------------------

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        if self.local_mode:
            text = (
                f"Export the {len(self.local_docs)} selected papers as a list in "
                "LaTeX, HTML, Markdown, plain text, or your own format. ADSfolio "
                "uses the details stored in this library and makes no ADS request."
            )
        else:
            text = (
                "Make a publication list from your NASA ADS records. Choose a "
                "built-in LaTeX or Markdown style, or write your own template for "
                "LaTeX, HTML, Markdown, plain text, or any other text format. "
                "Hover over any item for an explanation."
            )
        intro = QLabel(text, self)
        intro.setWordWrap(True)
        layout.addWidget(intro)
        splitter = QSplitter(Qt.Horizontal, self)
        self.tabs = QTabWidget(splitter)
        who = self.tabs.addTab(
            self._build_who(), "Your name" if self.local_mode else "Your publications"
        )
        self.tabs.setTabToolTip(
            who,
            "Your name, so ADSfolio can highlight it in author lists."
            if self.local_mode
            else "Which ADS records are yours, and how your name appears.",
        )
        fmt = self.tabs.addTab(self._build_format(), "Format")
        self.tabs.setTabToolTip(fmt, "How each paper and the whole list look.")
        out = self.tabs.addTab(self._build_output(), "Output")
        self.tabs.setTabToolTip(out, "Where the file is saved.")
        splitter.addWidget(self.tabs)
        splitter.addWidget(self._build_preview())
        if self.local_mode:
            # Selected papers have no ADS fields that the built-in styles need.
            self.format_choice.setCurrentIndex(TEMPLATE_INDEX)
            self.format_choice.setEnabled(False)
        splitter.setSizes([560, 620])
        layout.addWidget(splitter, 1)
        buttons = QHBoxLayout()
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        buttons.addWidget(self.status, 1)
        self.close_button = QPushButton("Close", self)
        self.close_button.setToolTip("Close the window. Your settings are kept.")
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.close_button)
        self.save_button = QPushButton(
            "Save List" if self.local_mode else "Save CV", self
        )
        self.save_button.setDefault(True)
        self.save_button.setToolTip(
            "Write the selected papers to the output file."
            if self.local_mode
            else "Look up your publications in ADS and write the output file."
        )
        self.save_button.clicked.connect(self._save_cv)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)

    def _line(self, parent: QWidget, placeholder: str = "") -> QLineEdit:
        edit = QLineEdit(parent)
        edit.setPlaceholderText(placeholder)
        edit.textChanged.connect(self._changed)
        return edit

    def _spin(
        self, parent: QWidget, low: int, high: int, special: str = ""
    ) -> QSpinBox:
        spin = QSpinBox(parent)
        spin.setRange(low, high)
        if special:
            spin.setSpecialValueText(special)
        spin.valueChanged.connect(self._changed)
        return spin

    def _build_who(self) -> QWidget:
        page = QWidget(self)
        form = QFormLayout(page)
        self.who_form = form
        self.query = self._line(page, 'author:"Doe, Jane"')
        form.addRow("ADS query", self.query)
        _tip(
            self.query,
            "The NASA ADS search that finds your papers, for example "
            'author:"Doe, Jane". Use *:* with ORCID matching to select by ORCID '
            "only.",
            form,
        )
        self.orcid = self._line(page, "0000-0002-1825-0097")
        form.addRow("ORCID iD", self.orcid)
        _tip(self.orcid, "Your ORCID iD, used when ORCID matching is on.", form)
        self.use_orcid = QCheckBox("Match publications by ORCID iD", page)
        self.use_orcid.toggled.connect(self._changed)
        form.addRow("", self.use_orcid)
        _tip(
            self.use_orcid,
            "Keep only papers that ADS links to your ORCID iD. Turn this off if "
            "some of your papers are not claimed in ORCID.",
        )
        self.first_year = self._spin(page, 0, 9999, "Any")
        self.last_year = self._spin(page, 0, 9999, "Any")
        years = QHBoxLayout()
        years.addWidget(self.first_year)
        years.addWidget(QLabel("to", page))
        years.addWidget(self.last_year)
        form.addRow("Years", years)
        years_tip = "Only papers published in these years. Any means no limit."
        _tip(self.first_year, years_tip)
        _tip(self.last_year, years_tip)
        form.labelForField(years).setToolTip(years_tip)
        self.years_row = years
        self.me_target = self._line(page, "Doe, J.")
        form.addRow("Your name in author lists", self.me_target)
        _tip(
            self.me_target,
            "Your name as the list prints it: surname, comma, and the first "
            "initial, for example Doe, J. Author names that start with this "
            "text are highlighted.",
            form,
        )
        self.me_display = self._line(page, "Jane Doe")
        form.addRow("Display name ({me})", self.me_display)
        _tip(
            self.me_display,
            "Your full name. The {me} placeholder in a header or footer shows it.",
            form,
        )
        self.first_author_prefix = self._line(page, "doe, j")
        form.addRow("First-author match", self.first_author_prefix)
        _tip(
            self.first_author_prefix,
            "Papers whose first author starts with this text, in any letter case, "
            "go in the First-authored section. Other papers go in Co-authored.",
            form,
        )
        self.fill_button = QPushButton("Fill from My Publication Profile", page)
        self.fill_button.setToolTip(
            "Copy your name, ORCID iD, and years from Tools → My Publication Profile…."
        )
        self.fill_button.clicked.connect(self._fill_from_profile)
        form.addRow("", self.fill_button)
        if self.local_mode:
            for widget in (self.query, self.orcid, self.use_orcid, years):
                form.setRowVisible(widget, False)
        return page

    def _build_format(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        form = QFormLayout()
        self.format_choice = QComboBox(page)
        for label, output, style in FORMAT_CHOICES:
            self.format_choice.addItem(label, (output, style))
        self.format_choice.currentIndexChanged.connect(self._format_changed)
        form.addRow("Format", self.format_choice)
        _tip(
            self.format_choice,
            "Built-in styles write ready-made LaTeX or Markdown. Custom template "
            "lets you write the text of every part yourself, in any format."
            + (
                " Selected papers always use a custom template."
                if self.local_mode
                else ""
            ),
            form,
        )
        self.me_fmt = self._line(page, r"\textbf{{{}}}")
        form.addRow("Highlight your name", self.me_fmt)
        _tip(
            self.me_fmt,
            "The markup around your name in author lists. {} is your name. "
            "Examples: <b>{}</b> for HTML, **{}** for Markdown, \\textbf{{{}}} for "
            "LaTeX (write literal braces twice).",
            form,
        )
        self.author_sep = self._line(page, "; ")
        form.addRow("Author separator", self.author_sep)
        _tip(
            self.author_sep,
            "Text between two author names, for example a semicolon and a space. "
            'The last two names are joined with "and".',
            form,
        )
        self.max_authors = self._spin(page, 0, 999, "All")
        form.addRow("Show up to (authors)", self.max_authors)
        _tip(
            self.max_authors,
            "Longer author lists are shortened with et al. All shows every author.",
            form,
        )
        self.min_authors = self._spin(page, 1, 999)
        form.addRow("Then keep the first", self.min_authors)
        _tip(
            self.min_authors,
            "How many leading authors a shortened list keeps before et al.",
            form,
        )
        self.keep_me = self._spin(page, 0, 999)
        form.addRow("Keep your name up to position", self.keep_me)
        _tip(
            self.keep_me,
            "If you are at this position or earlier, a shortened list still shows "
            "your name.",
            form,
        )
        layout.addLayout(form)

        self.template_box = QWidget(page)
        template_layout = QVBoxLayout(self.template_box)
        template_layout.setContentsMargins(0, 0, 0, 0)
        preset_row = QHBoxLayout()
        start_label = QLabel("Start from", self.template_box)
        preset_row.addWidget(start_label)
        self.preset = QComboBox(self.template_box)
        for key, preset in TEMPLATE_PRESETS.items():
            self.preset.addItem(preset.label, key)
        preset_tip = (
            "A complete starting point for one format. Use This Starting Point "
            "replaces the template text, escaping, name highlight, separator, and "
            "file extension."
        )
        _tip(self.preset, preset_tip)
        start_label.setToolTip(preset_tip)
        preset_row.addWidget(self.preset)
        apply_preset = QPushButton("Use This Starting Point", self.template_box)
        apply_preset.setToolTip(preset_tip)
        apply_preset.clicked.connect(self._apply_preset)
        preset_row.addWidget(apply_preset)
        preset_row.addStretch(1)
        template_layout.addLayout(preset_row)
        template_form = QFormLayout()
        self.escape = QComboBox(self.template_box)
        for key, label in (
            ("latex", "LaTeX"),
            ("html", "HTML"),
            ("none", "None (Markdown, plain text)"),
        ):
            self.escape.addItem(label, key)
        self.escape.currentIndexChanged.connect(self._changed)
        template_form.addRow("Escape paper text for", self.escape)
        _tip(
            self.escape,
            "How titles, journals, and other paper details are made safe for the "
            "format, for example & becomes \\& in LaTeX and &amp; in HTML. Text "
            "that you type in the template is never changed.",
            template_form,
        )
        self.group = QCheckBox(
            "Split into First-authored and Co-authored sections", self.template_box
        )
        self.group.toggled.connect(self._changed)
        template_form.addRow("", self.group)
        _tip(
            self.group,
            "On: two sections, by whether you are the first author. Off: one "
            "section named Publications, in the original order.",
        )
        mono = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        self.parts: dict[str, QPlainTextEdit] = {}
        for key, label, help_text in TEMPLATE_PARTS:
            edit = QPlainTextEdit(self.template_box)
            edit.setFont(mono)
            edit.setTabChangesFocus(True)
            lines = 4 if key == "entry" else 2
            edit.setFixedHeight(edit.fontMetrics().lineSpacing() * lines + 14)
            edit.textChanged.connect(self._changed)
            self.parts[key] = edit
            template_form.addRow(label, edit)
            _tip(edit, help_text, template_form)
        template_layout.addLayout(template_form)
        names = ", ".join(f"{{{name}}}" for name in ENTRY_FIELDS)
        fields_help = QLabel(
            f"Entry placeholders: {names}. Header and footer: {{me}}, {{date}}, "
            "{total}, {first_count}, {co_count}. Other braces stay as written.",
            self.template_box,
        )
        fields_help.setWordWrap(True)
        fields_help.setToolTip(
            "\n".join(f"{{{name}}}: {text}" for name, text in FIELD_HELP.items())
        )
        template_layout.addWidget(fields_help)
        layout.addWidget(self.template_box, 1)
        return page

    def _build_output(self) -> QWidget:
        page = QWidget(self)
        form = QFormLayout(page)
        row = QHBoxLayout()
        self.output_path = self._line(page)
        row.addWidget(self.output_path, 1)
        browse = QPushButton("Choose…", page)
        browse.setToolTip("Choose the file name and folder.")
        browse.clicked.connect(self._choose_output)
        row.addWidget(browse)
        form.addRow("Output file", row)
        output_tip = "The file that Save writes. An existing file is replaced."
        _tip(self.output_path, output_tip)
        form.labelForField(row).setToolTip(output_tip)
        self.journal_legend = QCheckBox(
            "Add a journal abbreviation legend (built-in formats)", page
        )
        self.journal_legend.toggled.connect(self._changed)
        form.addRow("", self.journal_legend)
        _tip(
            self.journal_legend,
            "Built-in formats can end with a list that expands each journal "
            "abbreviation. Custom templates do not add it.",
        )
        command = (
            ""
            if self.local_mode
            else "\nThe command adsfolio cv generate uses the same file."
        )
        settings = QLabel(f"Settings file: {self.settings_path}{command}", page)
        settings.setWordWrap(True)
        settings.setTextInteractionFlags(Qt.TextSelectableByMouse)
        settings.setToolTip("ADSfolio saves this window's settings here on close.")
        form.addRow("", settings)
        settings_row = QHBoxLayout()
        export = QPushButton("Save Settings As…", page)
        export.setToolTip("Copy these settings to another YAML file, for a project.")
        export.clicked.connect(self._export_settings)
        settings_row.addWidget(export)
        settings_row.addStretch(1)
        form.addRow("", settings_row)
        return page

    def _build_preview(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        title = QLabel("<b>Preview</b>", page)
        title.setToolTip("What the saved file will contain.")
        row.addWidget(title)
        self.sample_mode = QRadioButton(
            f"Selected papers ({len(self.local_docs)})"
            if self.local_mode
            else "Sample papers",
            page,
        )
        self.sample_mode.setChecked(True)
        self.sample_mode.setToolTip(
            "The selected papers. Updates as you type."
            if self.local_mode
            else "Fictional papers that include your name. Updates as you type and "
            "needs no network."
        )
        self.ads_mode = QRadioButton("My ADS publications", page)
        self.ads_mode.setToolTip(
            "Your real publications from NASA ADS. Choose Look Up in ADS to update."
        )
        self.sample_mode.toggled.connect(self._preview_mode_changed)
        row.addWidget(self.sample_mode)
        row.addWidget(self.ads_mode)
        row.addStretch(1)
        show_label = QLabel("Show as", page)
        row.addWidget(show_label)
        self.render_mode = QComboBox(page)
        for key, label in RENDER_MODES:
            self.render_mode.addItem(label, key)
        show_tip = (
            "Source shows the exact file text. Markdown and HTML show the text as "
            "a reader would see it. LaTeX shows as source."
        )
        self.render_mode.setToolTip(show_tip)
        show_label.setToolTip(show_tip)
        self.render_mode.currentIndexChanged.connect(self._show_preview)
        row.addWidget(self.render_mode)
        self.refresh_ads = QPushButton("Look Up in ADS", page)
        self.refresh_ads.setToolTip(
            "Run the ADS search now and show your real list. Needs an ADS token."
        )
        self.refresh_ads.clicked.connect(self._preview_from_ads)
        row.addWidget(self.refresh_ads)
        if self.local_mode:
            self.ads_mode.hide()
            self.refresh_ads.hide()
        layout.addLayout(row)
        mono = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        self.preview_stack = QStackedWidget(page)
        self.preview = QPlainTextEdit(page)
        self.preview.setReadOnly(True)
        self.preview.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.preview.setFont(mono)
        self.rendered = QTextBrowser(page)
        self.rendered.setOpenLinks(False)
        self.rendered.anchorClicked.connect(
            lambda url: open_url(url.toString(), application=self.browser_command)
        )
        self.preview_stack.addWidget(self.preview)
        self.preview_stack.addWidget(self.rendered)
        layout.addWidget(self.preview_stack, 1)
        return page

    # Settings -----------------------------------------------------------

    def _read_yaml(self, path: Path) -> dict[str, Any]:
        import yaml

        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}

    def _load_settings(self) -> None:
        raw: dict[str, Any] = {}
        source = self.settings_path
        if self.local_mode and not source.is_file():
            # Start a selection export from the CV style, as one list.
            source = Config.get_config_dir() / CV_SETTINGS_FILENAME
        if source.is_file():
            try:
                raw = self._read_yaml(source)
            except (OSError, ValueError) as exc:
                self.status.setText(f"Could not read {source}: {exc}")
        if self.local_mode and source != self.settings_path:
            raw = self._selection_start(raw)
        self._raw = raw
        self._loading = True
        try:
            try:
                self._show_mapping(raw)
            except (TypeError, ValueError) as exc:
                self.status.setText(f"Some saved settings were invalid: {exc}")
                self._show_mapping({})
            if not raw.get("author"):
                self._fill_from_profile()
        finally:
            self._loading = False
        self._auto_render_mode()
        self._changed()

    @staticmethod
    def _selection_start(raw: dict[str, Any]) -> dict[str, Any]:
        """Return first-use settings for exporting selected papers.

        The export keeps the CV template when there is one, otherwise it starts
        from the Markdown preset. It is one list without a header or footer,
        because a CV header describes an ADS publication list.
        """
        part_names = {name for name, _label, _help in TEMPLATE_PARTS}
        fmt = dict(raw.get("format") or {})
        template = dict(raw.get("template") or {})
        if fmt.get("output") != "template" or not (
            part_names & set(template) or template.get("preset")
        ):
            template = {"preset": "markdown"}
            fmt.pop("me_fmt", None)
            fmt.pop("author_sep", None)
        fmt["output"] = "template"
        template.update({"group": False, "header": "", "footer": ""})
        start = {**raw, "format": fmt, "template": template}
        start.pop("output", None)
        return start

    def _show_mapping(self, raw: dict[str, Any]) -> None:
        author = raw.get("author") or {}
        fmt = raw.get("format") or {}
        output = raw.get("output") or {}
        template_raw = raw.get("template") or {}
        preset_key = str(template_raw.get("preset", ""))
        self._preset_key = preset_key if preset_key in TEMPLATE_PRESETS else None
        self.query.setText(str(author.get("query", "")))
        self.orcid.setText(str(author.get("orcid", "")))
        self.use_orcid.setChecked(bool(author.get("use_orcid", False)))
        self.first_year.setValue(int(author.get("debut_year") or 0))
        self.last_year.setValue(int(author.get("retire_year") or 0))
        self.me_target.setText(str(author.get("me_target", "")))
        self.me_display.setText(str(author.get("me_display", "")))
        self.first_author_prefix.setText(str(author.get("first_author_prefix", "")))
        output_format = str(fmt.get("output", "template"))
        style = str(fmt.get("style", "compact"))
        index = next(
            (
                i
                for i, (_, out, sty) in enumerate(FORMAT_CHOICES)
                if out == output_format and (out == "template" or sty == style)
            ),
            TEMPLATE_INDEX,
        )
        self.format_choice.setCurrentIndex(TEMPLATE_INDEX if self.local_mode else index)
        preset = TEMPLATE_PRESETS.get(
            str(template_raw.get("preset", "")), TEMPLATE_PRESETS["latex"]
        )
        self.me_fmt.setText(str(fmt.get("me_fmt", preset.me_fmt)))
        self.author_sep.setText(str(fmt.get("author_sep", preset.author_sep)))
        self.max_authors.setValue(int(fmt.get("n_max_authors", 5)))
        self.min_authors.setValue(int(fmt.get("n_min_authors", 3)))
        self.keep_me.setValue(int(fmt.get("n_me", 6)))
        part_names = {name for name, _label, _help in TEMPLATE_PARTS}
        if str(template_raw.get("preset", "")) in TEMPLATE_PRESETS:
            template = TEMPLATE_PRESETS[str(template_raw["preset"])].template
        elif part_names & set(template_raw):
            # Same rule as cv.yaml loading: without a preset, missing parts
            # are empty.
            template = CVTemplate()
        else:
            template = preset.template
        for key, edit in self.parts.items():
            edit.setPlainText(str(template_raw.get(key, getattr(template, key))))
        escape = str(template_raw.get("escape", template.escape))
        self.escape.setCurrentIndex(max(0, self.escape.findData(escape)))
        self.group.setChecked(bool(template_raw.get("group", not self.local_mode)))
        filename = str(output.get("filename", preset.filename))
        if self.local_mode and not output.get("filename"):
            filename = "selected-papers" + Path(preset.filename).suffix
        directory = (
            Path(str(output["dir"])).expanduser()
            if output.get("dir")
            else Path.home() / "Documents"
        )
        self.output_path.setText(str(directory / filename))
        self.journal_legend.setChecked(bool(output.get("journal_legend", False)))
        self._sync_format_controls()

    def _fill_from_profile(self) -> None:
        """Copy name, ORCID, and years from the publication profile."""
        try:
            profile = Config.load_user_profile()
        except (OSError, ValueError) as exc:
            self.status.setText(f"Could not read the publication profile: {exc}")
            return
        names = list(profile.author_names)
        # Same rule as ``adsfolio cv init``: an ORCID iD alone selects the
        # publications; otherwise the profile's name forms do.
        self.orcid.setText(profile.orcid)
        self.use_orcid.setChecked(bool(profile.orcid))
        if profile.orcid:
            self.query.setText("*:*")
        elif names:
            clauses = [f'author:"{escape_query_phrase(name)}"' for name in names]
            query = " OR ".join(clauses)
            self.query.setText(f"({query})" if len(clauses) > 1 else query)
        if names:
            self.me_target.setText(_short_name(names[0]))
            self.me_display.setText(_display_name(names[0]))
            self.first_author_prefix.setText(_short_name(names[0]).rstrip(".").lower())
        self.first_year.setValue(profile.first_publication_year or 0)
        self.last_year.setValue(profile.last_publication_year or 0)
        if not names and not profile.orcid and not self.local_mode:
            self.status.setText(
                "Your publication profile is empty. Enter your ADS query and name, "
                "or set Tools → My Publication Profile… first."
            )

    def mapping(self) -> dict[str, Any]:
        """Return the settings as a ``cv.yaml`` mapping.

        Sections that this window does not edit, such as ``markers`` and
        ``status``, keep their loaded values.
        """
        output_format, style = self.format_choice.currentData()
        raw = dict(self._raw)
        author = dict(raw.get("author") or {})
        author.update(
            {
                "query": self.query.text().strip(),
                "orcid": self.orcid.text().strip(),
                "use_orcid": self.use_orcid.isChecked(),
                "debut_year": self.first_year.value() or None,
                "retire_year": self.last_year.value() or None,
                "first_author_prefix": self.first_author_prefix.text().strip(),
                "me_target": self.me_target.text().strip(),
                "me_display": self.me_display.text().strip(),
            }
        )
        if self.local_mode and not author["query"]:
            # Selected papers need no ADS search; the field must still be set.
            author["query"] = "*:*"
        author.setdefault("exclude_bibstems", list(DEFAULT_EXCLUDE_BIBSTEMS))
        fmt = dict(raw.get("format") or {})
        fmt.update(
            {
                "output": output_format,
                "style": style,
                "n_max_authors": self.max_authors.value(),
                "n_min_authors": self.min_authors.value(),
                "n_me": self.keep_me.value(),
                "author_sep": self.author_sep.text(),
                "me_fmt": self.me_fmt.text(),
            }
        )
        fmt.setdefault("author_format", "cv_default")
        template: dict[str, Any] = {
            key: edit.toPlainText() for key, edit in self.parts.items()
        }
        template["escape"] = self.escape.currentData()
        template["group"] = self.group.isChecked()
        if self._preset_key is not None:
            # Records the starting point; the parts above override its text.
            template["preset"] = self._preset_key
        target = Path(self.output_path.text().strip()).expanduser()
        output = dict(raw.get("output") or {})
        output.update(
            {
                "dir": str(target.parent),
                "filename": target.name,
                "journal_legend": self.journal_legend.isChecked(),
            }
        )
        raw.update(
            {"author": author, "format": fmt, "template": template, "output": output}
        )
        return raw

    def _write_settings(self, path: Path) -> bool:
        import yaml

        try:
            config_from_mapping(self.mapping(), path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                yaml.safe_dump(self.mapping(), sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
        except (OSError, ValueError) as exc:
            self.status.setText(f"Could not save settings: {exc}")
            return False
        self._raw = self.mapping()
        return True

    # Interaction --------------------------------------------------------

    def _changed(self, *_args: object) -> None:
        if not self._loading:
            self._preview_timer.start()

    def _sync_format_controls(self) -> None:
        output_format, _style = self.format_choice.currentData()
        self.template_box.setEnabled(output_format == "template")
        self.journal_legend.setEnabled(output_format != "template")

    def _auto_render_mode(self) -> None:
        """Pick the preview view that suits the current format."""
        output_format, _style = self.format_choice.currentData()
        if output_format == "markdown":
            mode = "markdown"
        elif output_format == "template":
            mode = {"html": "html"}.get(self.escape.currentData(), "source")
            if mode == "source" and self._preset_key == "markdown":
                mode = "markdown"
        else:
            mode = "source"
        self.render_mode.setCurrentIndex(self.render_mode.findData(mode))

    def _format_changed(self) -> None:
        self._sync_format_controls()
        output_format, _style = self.format_choice.currentData()
        known = {preset.me_fmt for preset in TEMPLATE_PRESETS.values()}
        known.update(BUILTIN_ME_FMT.values())
        if not self._loading and self.me_fmt.text() in known:
            if output_format in BUILTIN_ME_FMT:
                self.me_fmt.setText(BUILTIN_ME_FMT[output_format])
            else:
                preset = TEMPLATE_PRESETS.get(self.escape_preset_key())
                if preset is not None:
                    self.me_fmt.setText(preset.me_fmt)
        if not self._loading:
            self._auto_render_mode()
        self._changed()

    def escape_preset_key(self) -> str:
        """Return the preset that matches the current escape choice."""
        escape = self.escape.currentData()
        return {"latex": "latex", "html": "html"}.get(escape, "markdown")

    def _apply_preset(self) -> None:
        """Replace the template parts with the chosen starting point."""
        key = self.preset.currentData()
        preset = TEMPLATE_PRESETS[key]
        self._preset_key = key
        self._loading = True
        try:
            for name, edit in self.parts.items():
                text = getattr(preset.template, name)
                if self.local_mode and name in {"header", "footer"}:
                    text = ""  # Preset headers describe an ADS CV.
                edit.setPlainText(text)
            self.escape.setCurrentIndex(self.escape.findData(preset.template.escape))
            self.me_fmt.setText(preset.me_fmt)
            self.author_sep.setText(preset.author_sep)
            current = Path(self.output_path.text().strip() or preset.filename)
            name = (
                current.stem + Path(preset.filename).suffix
                if self.local_mode
                else preset.filename
            )
            self.output_path.setText(str(current.with_name(name)))
        finally:
            self._loading = False
        mode = {"html": "html", "markdown": "markdown"}.get(key, "source")
        self.render_mode.setCurrentIndex(self.render_mode.findData(mode))
        self._changed()

    def _choose_output(self) -> None:
        selected, _ = QFileDialog.getSaveFileName(
            self, "Save As", self.output_path.text()
        )
        if selected:
            self.output_path.setText(selected)

    def _export_settings(self) -> None:
        selected, _ = QFileDialog.getSaveFileName(
            self, "Save Settings As", str(Path.home() / CV_SETTINGS_FILENAME)
        )
        if selected and self._write_settings(Path(selected)):
            self.status.setText(f"Saved settings to {selected}")

    def _preview_mode_changed(self) -> None:
        if self.sample_mode.isChecked():
            self._render_sample()
        elif self._ads_text is not None:
            self._set_preview(self._ads_text)
        else:
            self._preview_from_ads()

    def _set_preview(self, text: str) -> None:
        self.preview.setPlainText(text)
        self._show_preview()

    def _show_preview(self) -> None:
        """Show the source text, or the same text rendered as Markdown or HTML."""
        mode = self.render_mode.currentData()
        text = self.preview.toPlainText()
        if mode == "markdown":
            self.rendered.setMarkdown(text)
        elif mode == "html":
            self.rendered.setHtml(text)
        self.preview_stack.setCurrentWidget(
            self.preview if mode == "source" else self.rendered
        )

    def _current_docs(self, cfg: Any) -> list[dict[str, Any]]:
        if self.local_mode:
            return list(self.local_docs)
        return sample_publications(cfg.me_target)

    def _config(self) -> Any:
        mapping = self.mapping()
        if not mapping["author"]["query"]:
            mapping["author"]["query"] = "*:*"
        return config_from_mapping(mapping, self.settings_path)

    def _render_sample(self) -> None:
        """Render sample or selected papers with the current settings, offline."""
        if not self.sample_mode.isChecked():
            return
        try:
            cfg = self._config()
            text = render_cv(self._current_docs(cfg), cfg)
        except ValueError as exc:
            self._set_preview(f"Cannot render the preview: {exc}")
            return
        self._set_preview(text)

    # Saving -------------------------------------------------------------

    def _run(self, target: Path, *, preview: bool) -> None:
        if self._job is not None:
            return
        if not self.query.text().strip():
            self.status.setText("Enter an ADS query on the Your publications tab.")
            return
        if Config.token_source() is None:
            self.status.setText(
                "ADS lookups need a token. Add it in Tools → NASA ADS Token…."
            )
            return
        if not self._write_settings(self.settings_path):
            return
        self._job_output = []
        self._job_target = target
        self._job_preview = preview
        argv = cli_argv(
            "cv",
            "generate",
            "--config",
            str(self.settings_path),
            "--output",
            str(target),
        )
        self._job = CommandJob(argv, parent=self)
        self._job.output.connect(self._job_output.append)
        self._job.failed.connect(self._job_failed)
        self._job.finished.connect(self._job_finished)
        self._set_busy(True)
        self.status.setText("Looking up your publications in ADS…")
        self._job.start()

    def _set_busy(self, busy: bool) -> None:
        self.save_button.setEnabled(not busy)
        self.refresh_ads.setEnabled(not busy)

    def _preview_from_ads(self) -> None:
        self._temp_dir = self._temp_dir or tempfile.TemporaryDirectory(
            prefix="adsfolio-cv-"
        )
        self._run(Path(self._temp_dir.name) / "preview.txt", preview=True)

    def _save_cv(self) -> None:
        text = self.output_path.text().strip()
        if not text:
            self.status.setText("Choose an output file on the Output tab.")
            return
        target = Path(text).expanduser()
        if self.local_mode:
            self._save_local(target)
            return
        self._run(target, preview=False)

    def _save_local(self, target: Path) -> None:
        """Write the selected papers with the current settings, offline."""
        try:
            cfg = self._config()
            text = render_cv(self._current_docs(cfg), cfg)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        except (OSError, ValueError) as exc:
            self.status.setText(f"Could not save the list: {exc}")
            return
        self._write_settings(self.settings_path)
        self.status.setText(f"Saved {len(self.local_docs)} papers to {target}")

    def _job_failed(self, message: str) -> None:
        self._job = None
        self._set_busy(False)
        self.status.setText(f"Could not start the CV command: {message}")

    def _job_finished(self, code: int) -> None:
        self._job = None
        self._set_busy(False)
        target = self._job_target
        if code != 0 or target is None:
            detail = next(
                (line for line in reversed(self._job_output) if line.strip()), ""
            )
            self.status.setText(f"CV generation failed. {detail}".strip())
            return
        if self._job_preview:
            try:
                self._ads_text = target.read_text(encoding="utf-8")
            except OSError as exc:
                self.status.setText(f"Could not read the preview: {exc}")
                return
            self.ads_mode.setChecked(True)
            self._set_preview(self._ads_text)
            self.status.setText("Preview shows your ADS publications.")
        else:
            self.status.setText(f"Saved your CV to {target}")

    def done(self, result: int) -> None:
        """Save the settings and stop a running lookup when the window closes."""
        if self._job is not None:
            self._job.cancel()
        self._write_settings(self.settings_path)
        if self._temp_dir is not None:
            self._temp_dir.cleanup()
            self._temp_dir = None
        super().done(result)
