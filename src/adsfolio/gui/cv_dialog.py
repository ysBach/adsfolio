"""Build a CV publication list in any text format from your ADS records."""

from __future__ import annotations

import logging
import tempfile
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
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..api.search import escape_query_phrase
from ..config import Config
from ..cv import render_cv
from ..cv.config import DEFAULT_EXCLUDE_BIBSTEMS, DEFAULT_ME_FMT, config_from_mapping
from ..cv.template import ENTRY_FIELDS, FIELD_HELP, TEMPLATE_PRESETS
from .jobs import CommandJob, cli_argv

logger = logging.getLogger(__name__)

__all__ = ["CVDialog", "sample_publications"]

CV_SETTINGS_FILENAME = "cv.yaml"
FORMAT_CHOICES = (
    ("LaTeX: compact", "latex", "compact"),
    ("LaTeX: detailed", "latex", "detailed"),
    ("LaTeX: bibcode first", "latex", "bibcode-first"),
    ("Markdown: built-in", "markdown", "compact"),
    ("Custom template (any text format)", "template", "compact"),
)
BUILTIN_ME_FMT = {"latex": DEFAULT_ME_FMT, "markdown": "**{}**"}
TEMPLATE_PARTS = (
    ("header", "Header", "Once, at the top."),
    ("section", "Section start", "Before each section. Uses {section} and {count}."),
    ("entry", "Entry", "Once per publication."),
    ("section_end", "Section end", "After each section, for example </ol>."),
    ("footer", "Footer", "Once, at the end."),
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


class CVDialog(QDialog):
    """Edit CV settings, preview them, and write the publication list.

    The settings live in ``cv.yaml`` in the ADSfolio configuration folder,
    the same file that ``adsfolio cv generate`` reads. **Sample papers**
    preview the format offline. **My ADS publications** and **Save CV** run
    ``adsfolio cv generate`` in the background.

    Parameters
    ----------
    parent : `QWidget`, optional
        Parent window.
    settings_path : `Path`, optional
        CV settings file. Defaults to ``cv.yaml`` in the configuration folder.
    """

    def __init__(
        self, parent: QWidget | None = None, *, settings_path: Path | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("CV Generator")
        self.resize(1180, 760)
        self.settings_path = settings_path or (
            Config.get_config_dir() / CV_SETTINGS_FILENAME
        )
        self._raw: dict[str, Any] = {}
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

    # Layout -------------------------------------------------------------

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Make a publication list from your NASA ADS records. Choose a built-in "
            "LaTeX or Markdown style, or write your own template for LaTeX, HTML, "
            "Markdown, plain text, or any other text format.",
            self,
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        splitter = QSplitter(Qt.Horizontal, self)
        tabs = QTabWidget(splitter)
        tabs.addTab(self._build_who(), "Your publications")
        tabs.addTab(self._build_format(), "Format")
        tabs.addTab(self._build_output(), "Output")
        splitter.addWidget(tabs)
        splitter.addWidget(self._build_preview())
        splitter.setSizes([560, 620])
        layout.addWidget(splitter, 1)
        buttons = QHBoxLayout()
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        buttons.addWidget(self.status, 1)
        self.close_button = QPushButton("Close", self)
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.close_button)
        self.save_button = QPushButton("Save CV", self)
        self.save_button.setDefault(True)
        self.save_button.setToolTip(
            "Look up your publications in ADS and write the output file."
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
        self.query = self._line(page, 'author:"Doe, Jane"')
        self.query.setToolTip("ADS search query that finds your publications.")
        form.addRow("ADS query", self.query)
        self.orcid = self._line(page, "0000-0002-1825-0097")
        form.addRow("ORCID iD", self.orcid)
        self.use_orcid = QCheckBox("Match publications by ORCID iD", page)
        self.use_orcid.toggled.connect(self._changed)
        form.addRow("", self.use_orcid)
        self.first_year = self._spin(page, 0, 9999, "Any")
        self.last_year = self._spin(page, 0, 9999, "Any")
        years = QHBoxLayout()
        years.addWidget(self.first_year)
        years.addWidget(QLabel("to", page))
        years.addWidget(self.last_year)
        form.addRow("Years", years)
        self.me_target = self._line(page, "Doe, J.")
        self.me_target.setToolTip(
            "Your name as ADSfolio prints it: surname, comma, initials. "
            "Matching author names are highlighted."
        )
        form.addRow("Your name in author lists", self.me_target)
        self.me_display = self._line(page, "Jane Doe")
        form.addRow("Display name ({me})", self.me_display)
        self.first_author_prefix = self._line(page, "doe, j")
        self.first_author_prefix.setToolTip(
            "Papers whose first author starts with this text (any letter case) "
            "go in the First-authored section."
        )
        form.addRow("First-author match", self.first_author_prefix)
        fill = QPushButton("Fill from My Publication Profile", page)
        fill.clicked.connect(self._fill_from_profile)
        form.addRow("", fill)
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
        self.me_fmt = self._line(page, r"\textbf{{{}}}")
        self.me_fmt.setToolTip(
            "Markup around your name. {} is your name. In LaTeX, write literal "
            "braces twice: \\textbf{{{}}}. HTML example: <b>{}</b>."
        )
        form.addRow("Highlight your name", self.me_fmt)
        self.author_sep = self._line(page, "; ")
        form.addRow("Author separator", self.author_sep)
        self.max_authors = self._spin(page, 0, 999, "All")
        form.addRow("Show up to (authors)", self.max_authors)
        self.min_authors = self._spin(page, 1, 999)
        form.addRow("Then keep the first", self.min_authors)
        self.keep_me = self._spin(page, 0, 999)
        self.keep_me.setToolTip("Keep your name when it is within this position.")
        form.addRow("Keep your name up to position", self.keep_me)
        layout.addLayout(form)

        self.template_box = QWidget(page)
        template_layout = QVBoxLayout(self.template_box)
        template_layout.setContentsMargins(0, 0, 0, 0)
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Start from", self.template_box))
        self.preset = QComboBox(self.template_box)
        for key, preset in TEMPLATE_PRESETS.items():
            self.preset.addItem(preset.label, key)
        preset_row.addWidget(self.preset)
        apply_preset = QPushButton("Use This Starting Point", self.template_box)
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
        self.escape.setToolTip(
            "How text from ADS is escaped. Your own template text is never changed."
        )
        template_form.addRow("Escape ADS text for", self.escape)
        mono = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        self.parts: dict[str, QPlainTextEdit] = {}
        for key, label, help_text in TEMPLATE_PARTS:
            edit = QPlainTextEdit(self.template_box)
            edit.setFont(mono)
            edit.setTabChangesFocus(True)
            edit.setFixedHeight(
                edit.fontMetrics().lineSpacing() * (4 if key == "entry" else 2) + 14
            )
            edit.setToolTip(help_text)
            edit.textChanged.connect(self._changed)
            self.parts[key] = edit
            template_form.addRow(label, edit)
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
        browse.clicked.connect(self._choose_output)
        row.addWidget(browse)
        form.addRow("Output file", row)
        self.journal_legend = QCheckBox(
            "Add a journal abbreviation legend (built-in formats)", page
        )
        self.journal_legend.toggled.connect(self._changed)
        form.addRow("", self.journal_legend)
        settings = QLabel(
            f"Settings file: {self.settings_path}\nThe command "
            "adsfolio cv generate uses the same file.",
            page,
        )
        settings.setWordWrap(True)
        settings.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addRow("", settings)
        settings_row = QHBoxLayout()
        export = QPushButton("Save Settings As…", page)
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
        row.addWidget(QLabel("<b>Preview</b>", page))
        self.sample_mode = QRadioButton("Sample papers", page)
        self.sample_mode.setChecked(True)
        self.sample_mode.setToolTip("Fictional papers. Updates as you type.")
        self.ads_mode = QRadioButton("My ADS publications", page)
        self.ads_mode.setToolTip("Look up your publications in ADS.")
        self.sample_mode.toggled.connect(self._preview_mode_changed)
        row.addWidget(self.sample_mode)
        row.addWidget(self.ads_mode)
        row.addStretch(1)
        self.refresh_ads = QPushButton("Look Up in ADS", page)
        self.refresh_ads.clicked.connect(self._preview_from_ads)
        row.addWidget(self.refresh_ads)
        layout.addLayout(row)
        self.preview = QPlainTextEdit(page)
        self.preview.setReadOnly(True)
        self.preview.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.preview.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        layout.addWidget(self.preview, 1)
        return page

    # Settings -----------------------------------------------------------

    def _load_settings(self) -> None:
        raw: dict[str, Any] = {}
        if self.settings_path.is_file():
            try:
                import yaml

                loaded = yaml.safe_load(self.settings_path.read_text(encoding="utf-8"))
                raw = loaded if isinstance(loaded, dict) else {}
            except (OSError, ValueError) as exc:
                self.status.setText(f"Could not read {self.settings_path}: {exc}")
        self._raw = raw
        self._loading = True
        try:
            try:
                self._show_mapping(raw)
            except (TypeError, ValueError) as exc:
                self.status.setText(f"Some saved CV settings were invalid: {exc}")
                self._show_mapping({})
            if not raw:
                self._fill_from_profile()
        finally:
            self._loading = False
        self._changed()

    def _show_mapping(self, raw: dict[str, Any]) -> None:
        author = raw.get("author") or {}
        fmt = raw.get("format") or {}
        output = raw.get("output") or {}
        template_raw = raw.get("template") or {}
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
            len(FORMAT_CHOICES) - 1,
        )
        self.format_choice.setCurrentIndex(index)
        preset = TEMPLATE_PRESETS["latex"]
        self.me_fmt.setText(str(fmt.get("me_fmt", preset.me_fmt)))
        self.author_sep.setText(str(fmt.get("author_sep", preset.author_sep)))
        self.max_authors.setValue(int(fmt.get("n_max_authors", 5)))
        self.min_authors.setValue(int(fmt.get("n_min_authors", 3)))
        self.keep_me.setValue(int(fmt.get("n_me", 6)))
        template = TEMPLATE_PRESETS.get(
            str(template_raw.get("preset", "")), preset
        ).template
        for key, edit in self.parts.items():
            edit.setPlainText(str(template_raw.get(key, getattr(template, key))))
        escape = str(template_raw.get("escape", template.escape))
        self.escape.setCurrentIndex(max(0, self.escape.findData(escape)))
        directory = Path(str(output.get("dir", ""))).expanduser()
        filename = str(output.get("filename", preset.filename))
        if not output.get("dir"):
            directory = Path.home() / "Documents"
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
        if not names and not profile.orcid:
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
        template = {key: edit.toPlainText() for key, edit in self.parts.items()}
        template["escape"] = self.escape.currentData()
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
        self._changed()

    def escape_preset_key(self) -> str:
        """Return the preset that matches the current escape choice."""
        escape = self.escape.currentData()
        return {"latex": "latex", "html": "html"}.get(escape, "markdown")

    def _apply_preset(self) -> None:
        """Replace the template parts with the chosen starting point."""
        preset = TEMPLATE_PRESETS[self.preset.currentData()]
        self._loading = True
        try:
            for key, edit in self.parts.items():
                edit.setPlainText(getattr(preset.template, key))
            self.escape.setCurrentIndex(self.escape.findData(preset.template.escape))
            self.me_fmt.setText(preset.me_fmt)
            self.author_sep.setText(preset.author_sep)
            current = Path(self.output_path.text().strip() or preset.filename)
            self.output_path.setText(str(current.with_name(preset.filename)))
        finally:
            self._loading = False
        self._changed()

    def _choose_output(self) -> None:
        selected, _ = QFileDialog.getSaveFileName(
            self, "Save CV As", self.output_path.text()
        )
        if selected:
            self.output_path.setText(selected)

    def _export_settings(self) -> None:
        selected, _ = QFileDialog.getSaveFileName(
            self, "Save CV Settings As", str(Path.home() / CV_SETTINGS_FILENAME)
        )
        if selected and self._write_settings(Path(selected)):
            self.status.setText(f"Saved settings to {selected}")

    def _preview_mode_changed(self) -> None:
        if self.sample_mode.isChecked():
            self._render_sample()
        elif self._ads_text is not None:
            self.preview.setPlainText(self._ads_text)
        else:
            self._preview_from_ads()

    def _render_sample(self) -> None:
        """Render fictional papers with the current settings, offline."""
        if not self.sample_mode.isChecked():
            return
        try:
            mapping = self.mapping()
            if not mapping["author"]["query"]:
                mapping["author"]["query"] = "*:*"
            cfg = config_from_mapping(mapping, self.settings_path)
            text = render_cv(sample_publications(cfg.me_target), cfg)
        except ValueError as exc:
            self.preview.setPlainText(f"Cannot render the preview: {exc}")
            return
        self.preview.setPlainText(text)

    # ADS generation -----------------------------------------------------

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
        self._run(Path(text).expanduser(), preview=False)

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
            self.preview.setPlainText(self._ads_text)
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
