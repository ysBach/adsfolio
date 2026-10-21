"""Dialogs for identify and rename operations."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWhatsThis,
    QWidget,
)

from ..config import Config
from ..pdf.doi_providers import (
    DEFAULT_DOI_PROVIDERS,
    DOI_PROVIDER_LABELS,
    DoiProvider,
)
from ..pdf.identify_settings import (
    IDENTIFY_STRATEGY_LABELS,
    RECOMMENDED_IDENTIFY_STRATEGIES,
    IdentifySettings,
    IdentifyStrategy,
)
from ..pdf.registry import open_registry_state
from ..pdf.rename_settings import RenameSettings
from ..pdf.renamer import PdfRenamer
from ..user_profile import UserProfile, normalize_orcid
from .jobs import CommandJob, cli_argv


class OptionalYearSpinBox(QSpinBox):
    """Spin box that displays its zero sentinel as an empty year."""

    def textFromValue(self, value: int) -> str:
        """Render the minimum sentinel as blank."""
        return "" if value == self.minimum() else str(value)


class ExplainedFormLabel(QWidget):
    """Form label with visible, keyboard-accessible contextual help."""

    def __init__(
        self,
        text: str,
        explanation: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.explanation = explanation
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.label = QLabel(text, self)
        self.help_button = QToolButton(self)
        self.help_button.setText("?")
        self.help_button.setAutoRaise(True)
        self.help_button.setAccessibleName(f"Help for {text}")
        self.help_button.setToolTip(explanation)
        self.help_button.setAccessibleDescription(explanation)
        self.help_button.clicked.connect(self._show_help)
        layout.addWidget(self.label)
        layout.addWidget(self.help_button)
        layout.addStretch(1)

    def _show_help(self) -> None:
        """Show the full explanation without opening a modal dialog."""
        QWhatsThis.showText(
            self.help_button.mapToGlobal(self.help_button.rect().bottomLeft()),
            self.explanation,
            self.help_button,
        )


def _add_explained_row(
    form: QFormLayout,
    label: str,
    field: QWidget | QHBoxLayout,
    explanation: str,
    parent: QWidget,
) -> ExplainedFormLabel:
    """Add one consistently explained and accessible form row."""
    explained_label = ExplainedFormLabel(label, explanation, parent)
    form.addRow(explained_label, field)
    if isinstance(field, QWidget):
        field.setToolTip(explanation)
        field.setAccessibleDescription(explanation)
    return explained_label


class UserProfileDialog(QDialog):
    """Edit the global publication profile used for ADS prefetching."""

    def __init__(
        self,
        profile: UserProfile,
        *,
        save_profile: Callable[[UserProfile], None] | None = None,
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self._save_profile = save_profile
        self.setWindowTitle("My Publication Profile")
        form = QFormLayout(self)

        self.orcid = QLineEdit(profile.orcid, self)
        self.orcid.setPlaceholderText("0000-0000-0000-0000")
        self.author_names = QPlainTextEdit(self)
        self.author_names.setPlainText("\n".join(profile.author_names))
        self.author_names.setPlaceholderText("One ADS author name per line")
        self.author_names.setMaximumHeight(96)
        self.first_year = OptionalYearSpinBox(self)
        self.first_year.setRange(0, 9999)
        self.first_year.setSpecialValueText("")
        self.first_year.setValue(profile.first_publication_year or 0)
        self.last_year = OptionalYearSpinBox(self)
        self.last_year.setRange(0, 9999)
        self.last_year.setSpecialValueText("")
        self.last_year.setValue(profile.last_publication_year or 0)
        self.library_ids = QPlainTextEdit(self)
        self.library_ids.setPlainText("\n".join(profile.ads_library_ids))
        self.library_ids.setPlaceholderText("One ADS library ID per line")
        self.library_ids.setMaximumHeight(72)

        explanations = {
            "ORCID": (
                "Your ORCID identifier. It is the preferred way to prefetch your "
                "publications because author names can be ambiguous."
            ),
            "ADS author names": (
                "Names ADS may use for you, one per line. Include alternative name "
                "forms only when ORCID does not find all of your publications."
            ),
            "First publication year": (
                "Your earliest publication year. This limits the publications fetched "
                "for profile matching; it does not reject PDFs by publication year."
            ),
            "Last publication year": (
                "Your latest publication year, if applicable. Leave blank for no upper "
                "bound. This affects profile prefetching, not PDF eligibility."
            ),
            "ADS library IDs": (
                "ADS library identifiers to include in profile prefetching, one per "
                "line. These are IDs from ADS library URLs, not library display names."
            ),
        }
        self.help_labels = {
            "orcid": _add_explained_row(
                form, "ORCID", self.orcid, explanations["ORCID"], self
            ),
            "author_names": _add_explained_row(
                form,
                "ADS author names",
                self.author_names,
                explanations["ADS author names"],
                self,
            ),
            "first_year": _add_explained_row(
                form,
                "First publication year",
                self.first_year,
                explanations["First publication year"],
                self,
            ),
            "last_year": _add_explained_row(
                form,
                "Last publication year",
                self.last_year,
                explanations["Last publication year"],
                self,
            ),
            "library_ids": _add_explained_row(
                form,
                "ADS library IDs",
                self.library_ids,
                explanations["ADS library IDs"],
                self,
            ),
        }

        from PySide6.QtWidgets import QDialogButtonBox

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    @staticmethod
    def _unique_lines(text: str) -> tuple[str, ...]:
        """Return trimmed, non-empty, stable-unique lines."""
        values: list[str] = []
        for line in text.splitlines():
            value = line.strip()
            if value and value not in values:
                values.append(value)
        return tuple(values)

    def selected_profile(self) -> UserProfile:
        """Return the normalized profile shown in the dialog."""
        return UserProfile(
            orcid=normalize_orcid(self.orcid.text()),
            author_names=self._unique_lines(self.author_names.toPlainText()),
            first_publication_year=self.first_year.value() or None,
            last_publication_year=self.last_year.value() or None,
            ads_library_ids=self._unique_lines(self.library_ids.toPlainText()),
        )

    def accept(self) -> None:
        """Persist the profile before accepting the dialog."""
        try:
            profile = self.selected_profile()
            if self._save_profile is not None:
                self._save_profile(profile)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not save publication profile", str(exc))
            return
        super().accept()


class IdentifyDialog(QDialog):
    """Collect identify flags and run the CLI as a subprocess."""

    job_started = Signal(object)

    def __init__(
        self,
        root: Path,
        global_settings: IdentifySettings | None = None,
        library_settings: IdentifySettings | None = None,
        *,
        initial_scope: str = "global",
        save_settings: Callable[[str, IdentifySettings], None] | None = None,
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self.root = Path(root)
        self._global_settings = global_settings or IdentifySettings()
        self._library_settings = library_settings
        self._save_settings = save_settings
        self.setWindowTitle("Identify PDFs")
        form = QFormLayout(self)
        self.scope = QComboBox(self)
        self.scope.addItem("All libraries", "global")
        self.scope.addItem("This library only", "library")
        self.recursive = QCheckBox(self)
        self.refresh_cache = QCheckBox(self)
        self.allow_ads = QCheckBox(self)
        self.strategies = QListWidget(self)
        self.strategies.setDragDropMode(QAbstractItemView.InternalMove)
        self.strategies.setDefaultDropAction(Qt.MoveAction)
        self.strategies.setAccessibleName("Identification priority")
        self.move_strategy_up = QPushButton("Up", self)
        self.move_strategy_down = QPushButton("Down", self)
        self.reset_strategies = QPushButton("Reset recommended", self)
        strategy_buttons = QHBoxLayout()
        strategy_buttons.setContentsMargins(0, 0, 0, 0)
        strategy_buttons.addWidget(self.move_strategy_up)
        strategy_buttons.addWidget(self.move_strategy_down)
        strategy_buttons.addWidget(self.reset_strategies)
        strategy_container = QWidget(self)
        strategy_layout = QVBoxLayout(strategy_container)
        strategy_layout.setContentsMargins(0, 0, 0, 0)
        strategy_layout.addWidget(self.strategies)
        strategy_layout.addLayout(strategy_buttons)
        self.providers = QListWidget(self)
        self.providers.setDragDropMode(QAbstractItemView.InternalMove)
        self.providers.setDefaultDropAction(Qt.MoveAction)
        self.providers.setAccessibleName("Metadata provider priority")
        self.move_provider_up = QPushButton("Up", self)
        self.move_provider_down = QPushButton("Down", self)
        self.reset_providers = QPushButton("Reset recommended", self)
        provider_buttons = QHBoxLayout()
        provider_buttons.setContentsMargins(0, 0, 0, 0)
        provider_buttons.addWidget(self.move_provider_up)
        provider_buttons.addWidget(self.move_provider_down)
        provider_buttons.addWidget(self.reset_providers)
        provider_container = QWidget(self)
        provider_layout = QVBoxLayout(provider_container)
        provider_layout.setContentsMargins(0, 0, 0, 0)
        provider_layout.addWidget(self.providers)
        provider_layout.addLayout(provider_buttons)
        self.output = QLineEdit(self)
        self.choose_output = QPushButton("Choose…", self)
        output_layout = QHBoxLayout()
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.addWidget(self.output)
        output_layout.addWidget(self.choose_output)
        self.skip_unresolved = QCheckBox(self)
        self.apply_moves = QCheckBox(self)
        self.verbose = QCheckBox(self)
        self.verbose_parser = QCheckBox(self)
        self.ai_csv = QLineEdit(self)
        self.choose_ai_csv = QPushButton("Choose…", self)
        ai_csv_layout = QHBoxLayout()
        ai_csv_layout.setContentsMargins(0, 0, 0, 0)
        ai_csv_layout.addWidget(self.ai_csv)
        ai_csv_layout.addWidget(self.choose_ai_csv)
        help_text = {
            "scope": "Choose whether reusable Identify behavior applies globally or only to this library.",
            "recursive": "Scan PDFs in subdirectories as well as the library root.",
            "refresh_cache": "Rebuild your publication-profile cache for this run. This performs ADS network requests.",
            "allow_ads": "Allow fallback ADS requests without a separate interactive confirmation.",
            "strategies": "Check the evidence sources to use and drag them, or use Up and Down, to set priority. The first unique validated ADS match wins.",
            "providers": "Check the metadata providers to query and drag them, or use Up and Down, to set priority. Defaults to NASA/ADS, Crossref, DataCite.",
            "output": "Destination for unresolved PDFs. Relative paths use the library root; moves occur only when Apply is enabled.",
            "skip_unresolved": "Exclude the unresolved destination and all of its descendants before hashing or ADS work. If no destination is set, this means <library>/unresolved.",
            "apply_moves": "Move unresolved PDFs to the destination and identified PDFs from that subtree back to the library root. When disabled, moves are previewed only.",
            "verbose": "Show per-file unresolved move or dry-run details in the job log.",
            "verbose_parser": "Show low-level PDF parser warnings for this run only.",
            "ai_csv": "Optional AI-enriched unresolved CSV used for this run only.",
        }
        self.help_labels = {
            "scope": _add_explained_row(
                form, "Save defaults for", self.scope, help_text["scope"], self
            ),
            "recursive": _add_explained_row(
                form, "Recursive scan", self.recursive, help_text["recursive"], self
            ),
            "refresh_cache": _add_explained_row(
                form,
                "Refresh publication cache",
                self.refresh_cache,
                help_text["refresh_cache"],
                self,
            ),
            "allow_ads": _add_explained_row(
                form,
                "Allow ADS fallback",
                self.allow_ads,
                help_text["allow_ads"],
                self,
            ),
            "strategies": _add_explained_row(
                form,
                "Identification priority",
                strategy_container,
                help_text["strategies"],
                self,
            ),
            "providers": _add_explained_row(
                form,
                "Metadata providers",
                provider_container,
                help_text["providers"],
                self,
            ),
            "output": _add_explained_row(
                form,
                "Move unresolved to",
                output_layout,
                help_text["output"],
                self,
            ),
            "skip_unresolved": _add_explained_row(
                form,
                "Skip unresolved directory",
                self.skip_unresolved,
                help_text["skip_unresolved"],
                self,
            ),
            "apply_moves": _add_explained_row(
                form,
                "Apply file moves",
                self.apply_moves,
                help_text["apply_moves"],
                self,
            ),
            "verbose": _add_explained_row(
                form, "Verbose log", self.verbose, help_text["verbose"], self
            ),
            "verbose_parser": _add_explained_row(
                form,
                "Parser warnings (this run)",
                self.verbose_parser,
                help_text["verbose_parser"],
                self,
            ),
            "ai_csv": _add_explained_row(
                form,
                "AI CSV (this run)",
                ai_csv_layout,
                help_text["ai_csv"],
                self,
            ),
        }
        warning = QLabel(
            "ADS may block you for ~24 h if you exceed ~1000 requests/24 h", self
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #b00020;")
        form.addRow(warning)
        self._add_buttons(form)
        self.choose_output.clicked.connect(self._choose_output_directory)
        self.choose_ai_csv.clicked.connect(self._choose_ai_csv)
        self.move_strategy_up.clicked.connect(lambda: self._move_strategy(-1))
        self.move_strategy_down.clicked.connect(lambda: self._move_strategy(1))
        self.reset_strategies.clicked.connect(self._reset_strategies)
        self.move_provider_up.clicked.connect(lambda: self._move_provider(-1))
        self.move_provider_down.clicked.connect(lambda: self._move_provider(1))
        self.reset_providers.clicked.connect(self._reset_providers)
        self.scope.currentIndexChanged.connect(self._load_selected_scope)
        self.output.setToolTip(help_text["output"])
        self.output.setAccessibleDescription(help_text["output"])
        self.choose_output.setToolTip(help_text["output"])
        self.choose_output.setAccessibleDescription(help_text["output"])
        for control in (
            self.strategies,
            self.move_strategy_up,
            self.move_strategy_down,
            self.reset_strategies,
        ):
            control.setToolTip(help_text["strategies"])
            control.setAccessibleDescription(help_text["strategies"])
        for control in (
            self.providers,
            self.move_provider_up,
            self.move_provider_down,
            self.reset_providers,
        ):
            control.setToolTip(help_text["providers"])
            control.setAccessibleDescription(help_text["providers"])
        self.ai_csv.setToolTip(help_text["ai_csv"])
        self.ai_csv.setAccessibleDescription(help_text["ai_csv"])
        self.choose_ai_csv.setToolTip(help_text["ai_csv"])
        self.choose_ai_csv.setAccessibleDescription(help_text["ai_csv"])
        index = self.scope.findData(
            initial_scope if initial_scope in {"global", "library"} else "global"
        )
        self.scope.setCurrentIndex(index)
        self._load_selected_scope()

    def _load_selected_scope(self, *_args: Any) -> None:
        """Populate reusable controls from the selected scope."""
        settings = (
            self._library_settings or self._global_settings
            if self.scope.currentData() == "library"
            else self._global_settings
        )
        self.recursive.setChecked(settings.recursive)
        self._populate_strategies(settings.strategies)
        self._populate_providers(settings.providers)
        self.output.setText(settings.output_unresolved or "")
        self.skip_unresolved.setChecked(settings.skip_unresolved_directory)
        self.verbose.setChecked(settings.verbose)

    def selected_settings(self) -> IdentifySettings:
        """Return normalized reusable values shown in the dialog."""
        return IdentifySettings(
            strategies=self.selected_strategies(),
            providers=self.selected_providers(),
            recursive=self.recursive.isChecked(),
            output_unresolved=self.output.text().strip() or None,
            skip_unresolved_directory=self.skip_unresolved.isChecked(),
            verbose=self.verbose.isChecked(),
        )

    def save_defaults(self) -> None:
        """Persist reusable settings without running or closing the dialog."""
        try:
            settings = self.selected_settings()
            scope = str(self.scope.currentData())
            if self._save_settings is not None:
                self._save_settings(scope, settings)
            if scope == "library":
                self._library_settings = settings
            else:
                self._global_settings = settings
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not save Identify settings", str(exc))
            return

    def run_identify(self) -> None:
        """Start Identify with the displayed controls without saving defaults."""
        try:
            self.selected_settings()
            self.start_job()
        except ValueError as exc:
            QMessageBox.critical(self, "Could not run Identify", str(exc))
            return
        super().accept()

    def _add_buttons(self, form: QFormLayout) -> None:
        buttons = QHBoxLayout()
        self.save_defaults_button = QPushButton("Save Defaults", self)
        self.run_button = QPushButton("Run Identify", self)
        self.cancel_button = QPushButton("Cancel", self)
        self.save_defaults_button.clicked.connect(self.save_defaults)
        self.run_button.clicked.connect(self.run_identify)
        self.cancel_button.clicked.connect(self.reject)
        buttons.addStretch(1)
        buttons.addWidget(self.save_defaults_button)
        buttons.addWidget(self.run_button)
        buttons.addWidget(self.cancel_button)
        form.addRow(buttons)

    def _populate_strategies(
        self, enabled_strategies: tuple[IdentifyStrategy, ...]
    ) -> None:
        """Show enabled strategies first, followed by disabled catalog entries."""
        self.strategies.clear()
        enabled = set(enabled_strategies)
        for strategy in (*enabled_strategies, *IdentifyStrategy):
            if self._find_strategy_row(strategy) >= 0:
                continue
            item = QListWidgetItem(IDENTIFY_STRATEGY_LABELS[strategy])
            item.setData(Qt.UserRole, strategy.value)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
            item.setCheckState(Qt.Checked if strategy in enabled else Qt.Unchecked)
            self.strategies.addItem(item)

    def _find_strategy_row(self, strategy: IdentifyStrategy) -> int:
        for row in range(self.strategies.count()):
            if self.strategies.item(row).data(Qt.UserRole) == strategy.value:
                return row
        return -1

    def selected_strategies(self) -> tuple[IdentifyStrategy, ...]:
        """Return enabled strategies in the displayed priority order."""
        return tuple(
            IdentifyStrategy(item.data(Qt.UserRole))
            for row in range(self.strategies.count())
            if (item := self.strategies.item(row)).checkState() == Qt.Checked
        )

    def _move_strategy(self, offset: int) -> None:
        row = self.strategies.currentRow()
        destination = row + offset
        if row < 0 or destination < 0 or destination >= self.strategies.count():
            return
        item = self.strategies.takeItem(row)
        self.strategies.insertItem(destination, item)
        self.strategies.setCurrentRow(destination)

    def _reset_strategies(self) -> None:
        self._populate_strategies(RECOMMENDED_IDENTIFY_STRATEGIES)

    def _populate_providers(self, enabled_providers: tuple[DoiProvider, ...]) -> None:
        self.providers.clear()
        enabled = set(enabled_providers)
        for provider in (*enabled_providers, *DoiProvider):
            if self._find_provider_row(provider) >= 0:
                continue
            item = QListWidgetItem(DOI_PROVIDER_LABELS.get(provider, provider.value))
            item.setData(Qt.UserRole, provider.value)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
            item.setCheckState(Qt.Checked if provider in enabled else Qt.Unchecked)
            self.providers.addItem(item)

    def _find_provider_row(self, provider: DoiProvider) -> int:
        for row in range(self.providers.count()):
            if self.providers.item(row).data(Qt.UserRole) == provider.value:
                return row
        return -1

    def selected_providers(self) -> tuple[DoiProvider, ...]:
        return tuple(
            DoiProvider(item.data(Qt.UserRole))
            for row in range(self.providers.count())
            if (item := self.providers.item(row)).checkState() == Qt.Checked
        )

    def _move_provider(self, offset: int) -> None:
        row = self.providers.currentRow()
        destination = row + offset
        if row < 0 or destination < 0 or destination >= self.providers.count():
            return
        item = self.providers.takeItem(row)
        self.providers.insertItem(destination, item)
        self.providers.setCurrentRow(destination)

    def _reset_providers(self) -> None:
        self._populate_providers(DEFAULT_DOI_PROVIDERS)

    def build_argv(self) -> list[str]:
        """Return the CLI argv represented by the current controls."""
        settings = self.selected_settings()
        flags = [
            "--settings-scope",
            str(self.scope.currentData()),
            "--recursive" if settings.recursive else "--no-recursive",
        ]
        for strategy in settings.strategies:
            flags.extend(("--strategy", strategy.value))
        for provider in settings.providers:
            flags.extend(("--provider", provider.value))
        if settings.output_unresolved is None:
            flags.append("--clear-output-unresolved")
        else:
            flags.extend(("--output-unresolved", settings.output_unresolved))
        flags.append(
            "--skip-unresolved-directory"
            if settings.skip_unresolved_directory
            else "--include-unresolved-directory"
        )
        flags.append("--verbose" if settings.verbose else "--quiet")
        for checkbox, flag in (
            (self.refresh_cache, "--refresh-cache"),
            (self.allow_ads, "--allow-ads"),
        ):
            if checkbox.isChecked():
                flags.append(flag)
        if self.apply_moves.isChecked():
            flags.append("--apply")
        if self.verbose_parser.isChecked():
            flags.append("--verbose-parser")
        if ai_csv := self.ai_csv.text().strip():
            flags.extend(("--ai-csv", ai_csv))
        return cli_argv("pdf", "identify", str(self.root), *flags)

    def _choose_output_directory(self) -> None:
        """Choose the destination for unresolved files."""
        directory = QFileDialog.getExistingDirectory(
            self,
            "Move unresolved PDFs to",
            self.output.text().strip() or str(self.root),
        )
        if directory:
            self.output.setText(directory)

    def _choose_ai_csv(self) -> None:
        """Choose an AI-enriched unresolved CSV for this run."""
        filename, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Choose AI-enriched unresolved CSV",
            self.ai_csv.text().strip() or str(self.root),
            "CSV files (*.csv);;All files (*)",
        )
        if filename:
            self.ai_csv.setText(filename)

    def start_job(self) -> CommandJob:
        """Create and start the identify subprocess."""
        job = CommandJob(self.build_argv(), parent=self, working_directory=self.root)
        job.start()
        self.job_started.emit(job)
        return job


class RenameDialog(QDialog):
    """Preview and apply registry-backed PDF renames."""

    job_started = Signal(object)

    def __init__(
        self,
        root: Path,
        global_settings: RenameSettings | None = None,
        library_settings: RenameSettings | None = None,
        *,
        initial_scope: str = "global",
        identify_settings: IdentifySettings | None = None,
        save_settings: Callable[[str, RenameSettings], None] | None = None,
        parent: Any = None,
    ) -> None:
        if (
            parent is None
            and not isinstance(global_settings, RenameSettings)
            and global_settings is not None
        ):
            parent = global_settings
            global_settings = None
        super().__init__(parent)
        self.root = Path(root)
        self._global_settings = global_settings or RenameSettings()
        self._library_settings = library_settings
        self._identify_settings = (
            identify_settings
            if identify_settings is not None
            else Config.load_identify_settings()
        )
        self._save_settings = save_settings
        self.setWindowTitle("Rename PDFs")
        self.scope = QComboBox(self)
        self.scope.addItem("All libraries", "global")
        self.scope.addItem("This library only", "library")
        self.template = QLineEdit(self)
        self.author_format = QComboBox(self)
        self.author_format.addItems(
            ["surname", "surname_initials", "last_comma_first", "ads_custom:"]
        )
        self.author_format.setEditable(True)
        self.max_authors = QSpinBox(self)
        self.max_authors.setRange(1, 99)
        self.authors_when_truncated = QSpinBox(self)
        self.authors_when_truncated.setRange(1, self.max_authors.value())
        self.max_authors.valueChanged.connect(self.authors_when_truncated.setMaximum)
        self.author_separator = QLineEdit(self)
        self.author_truncation_suffix = QLineEdit(self)
        self.char_policy = QComboBox(self)
        self.char_policy.addItems(["keep", "ascii"])
        self.table = QTableWidget(0, 2, self)
        self.table.setHorizontalHeaderLabels(["Current location", "Target location"])
        self.summary = QLabel("No preview generated", self)
        form = QFormLayout()
        scope_help = "Choose whether reusable Rename behavior applies globally or only to this library."
        template_help = (
            "Builds each filename with Python-style fields: {bibcode}, {year}, "
            "{title}, {first_author}, {first_author_full}, {authors}, {authors_N}, "
            "{journal}, {volume}, and {page}. {authors} adds the configured suffix "
            "when total authors exceed Max authors; {authors_N} omits that "
            "suffix. Missing metadata becomes empty, unknown fields stop the rename, "
            "unsafe characters are sanitized, .pdf is appended automatically, and "
            "results are limited to 240 UTF-8 bytes. A rename is skipped if its "
            "target filename already exists. Use {{ and }} for literal braces."
        )
        author_help = (
            "Controls author placeholders: surname; surname_initials; "
            "last_comma_first; or ads_custom:<ADS export format>. The custom form "
            "requires ADS access and is not available in the in-process GUI preview."
        )
        max_authors_help = (
            "Inclusive threshold for author truncation: author lists at or below "
            "this number are shown in full; longer lists retain only Authors when "
            "truncated. {authors} marks truncation with the configured suffix."
        )
        authors_when_truncated_help = (
            "Number of leading authors retained when the total exceeds Max authors. "
            "This must be between 1 and Max authors."
        )
        author_separator_help = (
            "Text placed between rendered authors. Use '+' for names such as "
            "SmithJ+DoeA. An empty value joins names directly. Unsafe filename "
            "characters are sanitized."
        )
        author_truncation_suffix_help = (
            "Text appended to {authors} only when the author list exceeds Max "
            "authors. Use '+' for SmithJ+DoeA+. {authors_N} always omits it."
        )
        char_policy_help = (
            "keep preserves Unicode; ascii transliterates Unicode when possible. Both "
            "choices replace characters that are unsafe in filenames."
        )
        self.help_labels = {
            "scope": _add_explained_row(
                form, "Save defaults for", self.scope, scope_help, self
            ),
            "template": _add_explained_row(
                form, "Filename template", self.template, template_help, self
            ),
            "author_format": _add_explained_row(
                form, "Author format", self.author_format, author_help, self
            ),
            "max_authors": _add_explained_row(
                form,
                "Max authors",
                self.max_authors,
                max_authors_help,
                self,
            ),
            "authors_when_truncated": _add_explained_row(
                form,
                "Authors when truncated",
                self.authors_when_truncated,
                authors_when_truncated_help,
                self,
            ),
            "author_separator": _add_explained_row(
                form,
                "Author separator",
                self.author_separator,
                author_separator_help,
                self,
            ),
            "author_truncation_suffix": _add_explained_row(
                form,
                "Truncation suffix",
                self.author_truncation_suffix,
                author_truncation_suffix_help,
                self,
            ),
            "char_policy": _add_explained_row(
                form,
                "Character policy",
                self.char_policy,
                char_policy_help,
                self,
            ),
        }
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.summary)
        layout.addWidget(self.table)
        from PySide6.QtWidgets import QDialogButtonBox

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel, self)
        self.preview_button = QPushButton("Preview", self)
        self.save_defaults_button = QPushButton("Save Defaults", self)
        self.apply_button = QPushButton("Apply", self)
        self.preview_button.setToolTip(
            "Generate proposed filenames without modifying any files."
        )
        self.preview_button.setAccessibleDescription(self.preview_button.toolTip())
        self.save_defaults_button.setToolTip(
            "Save these Rename defaults without renaming existing PDFs."
        )
        self.save_defaults_button.setAccessibleDescription(
            self.save_defaults_button.toolTip()
        )
        self.apply_button.setToolTip(
            "Run the rename command and change filenames on disk."
        )
        self.apply_button.setAccessibleDescription(self.apply_button.toolTip())
        buttons.addButton(self.preview_button, QDialogButtonBox.ActionRole)
        buttons.addButton(self.save_defaults_button, QDialogButtonBox.ActionRole)
        buttons.addButton(self.apply_button, QDialogButtonBox.AcceptRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.preview_button.clicked.connect(self.preview)
        self.save_defaults_button.clicked.connect(self.accept)
        self.apply_button.clicked.connect(self.apply)
        self.scope.currentIndexChanged.connect(self._load_selected_scope)
        index = self.scope.findData(
            initial_scope if initial_scope in {"global", "library"} else "global"
        )
        self.scope.setCurrentIndex(index)
        self._load_selected_scope()

    def _load_selected_scope(self, *_args: Any) -> None:
        """Populate reusable controls from the selected scope."""
        settings = (
            self._library_settings or self._global_settings
            if self.scope.currentData() == "library"
            else self._global_settings
        )
        self.template.setText(settings.template)
        self.author_format.setCurrentText(settings.author_format)
        self.max_authors.setValue(settings.max_authors)
        self.authors_when_truncated.setMaximum(settings.max_authors)
        self.authors_when_truncated.setValue(settings.authors_when_truncated)
        self.author_separator.setText(settings.author_separator)
        self.author_truncation_suffix.setText(settings.author_truncation_suffix)
        self.char_policy.setCurrentText(settings.char_policy)

    def selected_settings(self) -> RenameSettings:
        """Return normalized reusable values shown in the dialog."""
        return RenameSettings(
            template=self.template.text(),
            author_format=self._format(),
            max_authors=self.max_authors.value(),
            authors_when_truncated=self.authors_when_truncated.value(),
            char_policy=str(self.char_policy.currentText()),
            author_separator=self.author_separator.text(),
            author_truncation_suffix=self.author_truncation_suffix.text(),
        )

    def _save(self) -> bool:
        """Persist the selected reusable settings, returning True on success."""
        try:
            settings = self.selected_settings()
            if self._save_settings is not None:
                self._save_settings(str(self.scope.currentData()), settings)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Could not save Rename settings", str(exc))
            return False
        return True

    def accept(self) -> None:
        """Save reusable settings before accepting the dialog."""
        if not self._save():
            return
        super().accept()

    def _format(self) -> str:
        """Return the selected author format."""
        return self.author_format.currentText().strip()

    def _unresolved_directory(self) -> Path:
        """Return the absolute unresolved directory used by Identify and Rename."""
        configured = self._identify_settings.output_unresolved
        directory = (
            Path(configured).expanduser() if configured else self.root / "unresolved"
        )
        if not directory.is_absolute():
            directory = self.root / directory
        return directory.resolve()

    def _target_display_path(
        self,
        current_name: str,
        new_name: str,
        *,
        state_directory: Path,
        unresolved_directory: Path,
    ) -> str:
        """Render a preview target relative to the library root.

        ``PdfRenamer.batch_rename`` returns only a filename for the rendered
        target.  Reconstructing its destination here makes nested renames and
        moves out of the unresolved subtree visible before the subprocess runs.
        """
        source_path = state_directory / current_name
        target_directory = source_path.parent
        if (
            state_directory in unresolved_directory.parents
            and unresolved_directory in source_path.parents
        ):
            target_directory = state_directory
        target_path = target_directory / new_name
        try:
            return target_path.relative_to(state_directory).as_posix()
        except ValueError:
            return str(target_path)

    def preview(self) -> None:
        """Build a one-shot in-process preview from the registry state."""
        state = open_registry_state(self.root)
        state_directory = Path(getattr(state, "directory", self.root)).resolve()
        unresolved_directory = self._unresolved_directory()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            plans = PdfRenamer().batch_rename(
                state,
                template=self.template.text(),
                dry_run=True,
                author_format=self._format(),
                max_authors=self.max_authors.value(),
                authors_when_truncated=self.authors_when_truncated.value(),
                char_policy=self.char_policy.currentText(),
                author_separator=self.author_separator.text(),
                author_truncation_suffix=self.author_truncation_suffix.text(),
                move_resolved_from=(
                    unresolved_directory
                    if state_directory in unresolved_directory.parents
                    else None
                ),
                move_resolved_to=state_directory,
            )
        except ValueError as exc:
            self.summary.setText(str(exc))
            self.table.setRowCount(0)
            return
        finally:
            state.close()
            QApplication.restoreOverrideCursor()
        self.table.setRowCount(len(plans))
        for row, (old, new) in enumerate(plans):
            self.table.setItem(row, 0, QTableWidgetItem(old))
            self.table.setItem(
                row,
                1,
                QTableWidgetItem(
                    self._target_display_path(
                        old,
                        new,
                        state_directory=state_directory,
                        unresolved_directory=unresolved_directory,
                    )
                ),
            )
        self.summary.setText(f"{len(plans)} change(s)" if plans else "No changes")

    def build_argv(self) -> list[str]:
        """Return the CLI argv represented by the dialog."""
        flags = [
            "--scheme",
            self.template.text(),
            "--author-format",
            self._format(),
            "--max-authors",
            str(self.max_authors.value()),
            "--authors-when-truncated",
            str(self.authors_when_truncated.value()),
            "--char-policy",
            self.char_policy.currentText(),
            "--author-separator",
            self.author_separator.text(),
            "--author-truncation-suffix",
            self.author_truncation_suffix.text(),
            "--output-unresolved",
            str(self._unresolved_directory()),
        ]
        return cli_argv("pdf", "rename", str(self.root), *flags, "--apply")

    def apply(self) -> None:
        """Start the confirmed rename subprocess."""
        if (
            not self.table.rowCount()
            and QMessageBox.question(
                self, "No changes", "No preview changes exist. Apply anyway?"
            )
            != QMessageBox.Yes
        ):
            return
        if not self._save():
            return
        job = CommandJob(self.build_argv(), parent=self, working_directory=self.root)
        job.start()
        self.job_started.emit(job)
        super().accept()
