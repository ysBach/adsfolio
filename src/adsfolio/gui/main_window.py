"""Main three-pane desktop window for the adsfolio PDF library."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Generic, TypeVar

from PySide6.QtCore import (
    QItemSelectionModel,
    QModelIndex,
    QSignalBlocker,
    Qt,
    QTimer,
)
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QToolBar,
    QToolButton,
    QTreeView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..citations import CitationStyle, citation_preference_key
from ..config import AuthenticationError, Config
from ..pdf.annotation_merge import AnnotationMergeError, MissingDependencyError
from ..pdf.doi_providers import DOI_PROVIDER_LABELS, DoiProvider
from ..pdf.identify_settings import IdentifySettings
from ..pdf.readiness import metadata_ready, stored_bibcode_matches_file
from ..pdf.registry import CentralPdfRegistryState, LibraryRefresh, PdfRegistry
from ..pdf.rename_settings import RenameSettings
from ..pdf.watch_session import WatchEvent, WatchOutcome, WatchState
from ..pdf.watcher import WatchIdentifyPolicy
from .about import AboutDialog
from .ads_library_dialog import AdsLibraryDialog
from .ads_library_export_dialog import ExportAdsLibraryDialog
from .appearance import AppearanceDialog
from .citation_styles import CitationStylesDialog
from .config import RootEntry, load_config, save_config
from .cv_dialog import CVDialog
from .detail_pane import DetailPane
from .dialogs import IdentifyDialog, RenameDialog, UserProfileDialog
from .icons import RoundIconButton
from .jobs import CommandJob, cli_argv
from .library import (
    Column,
    FileQuery,
    LibraryView,
    ads_url,
    arxiv_url,
    build_columns,
    verification_stale,
)
from .models import (
    FileTableModel,
    FirstAuthorDelegate,
    LibraryTableView,
    LinkDelegate,
    ResolutionDelegate,
    RowHeightDelegate,
)
from .notifications import Notice, NoticeAction, NoticeBar
from .osint import open_path, open_url, reveal_in_file_manager
from .references_dialog import ReferencesDialog
from .refresh_job import LibraryRefreshJob
from .token_dialog import TokenDialog
from .update_dialog import UpdateButton, UpdateDialog, quiet_progress_bar
from .updates import (
    REPOSITORY_URL,
    Release,
    UpdateChecker,
    UpdateDownloader,
    installation_kind,
)
from .watch import LibraryWatchController, WatchFailure
from .welcome import WelcomePanel

logger = logging.getLogger(__name__)

_SettingsT = TypeVar("_SettingsT")
DEFAULT_WINDOW_SIZE = (1280, 780)
DEFAULT_SPLITTER_SIZES = [220, 700, 360]


class _SettingsPersistenceError(OSError):
    """Report a settings transaction or compensating rollback failure."""


class _SettingsPersistenceCoordinator(Generic[_SettingsT]):
    """Coordinate domain settings and GUI scope as a compensated transaction."""

    def __init__(
        self,
        *,
        scopes: dict[str, str],
        root_key: str,
        load_global: Callable[[], _SettingsT],
        save_global: Callable[[_SettingsT], object],
        load_library: Callable[[], _SettingsT | None],
        save_library: Callable[[_SettingsT | None], object],
        save_gui: Callable[[], object],
    ) -> None:
        self._scopes = scopes
        self._root_key = root_key
        self._load_global = load_global
        self._save_global = save_global
        self._load_library = load_library
        self._save_library = save_library
        self._save_gui = save_gui

    def persist(self, scope: str, settings: _SettingsT) -> None:
        """Persist settings and scope, compensating every attempted store.

        Parameters
        ----------
        scope
            `global` or `library` destination selected by the dialog.
        settings
            Normalized settings value to persist.

        Raises
        ------
        OSError
            If a store or any compensating rollback fails.
        ValueError
            If `scope` is unsupported.
        """
        if scope not in {"global", "library"}:
            raise ValueError(f"Unsupported settings scope: {scope!r}")
        had_scope = self._root_key in self._scopes
        prior_scope = self._scopes.get(self._root_key)
        try:
            prior_global = self._load_global()
            prior_library = self._load_library()
        except (OSError, ValueError, sqlite3.Error) as exc:
            raise _SettingsPersistenceError(
                f"Could not snapshot prior settings: {exc}"
            ) from exc

        domain_attempted = False
        gui_attempted = False
        try:
            domain_attempted = True
            if scope == "global":
                self._save_global(settings)
            else:
                self._save_library(settings)
            self._scopes[self._root_key] = scope
            gui_attempted = True
            self._save_gui()
        except (OSError, ValueError, sqlite3.Error) as exc:
            if had_scope and prior_scope is not None:
                self._scopes[self._root_key] = prior_scope
            else:
                self._scopes.pop(self._root_key, None)
            rollback_failures: list[str] = []
            if domain_attempted:
                try:
                    if scope == "global":
                        self._save_global(prior_global)
                    else:
                        self._save_library(prior_library)
                except (OSError, ValueError, sqlite3.Error) as rollback_exc:
                    rollback_failures.append(f"domain settings: {rollback_exc}")
            if gui_attempted:
                try:
                    self._save_gui()
                except (OSError, ValueError, sqlite3.Error) as rollback_exc:
                    rollback_failures.append(f"GUI settings: {rollback_exc}")
            if rollback_failures:
                raise _SettingsPersistenceError(
                    f"{exc}; rollback failed: {'; '.join(rollback_failures)}"
                ) from exc
            raise _SettingsPersistenceError(str(exc)) from exc


@dataclass(frozen=True)
class _LibraryViewContext:
    """Retain the originating view while a library job runs."""

    root: Path
    selected: tuple[dict[str, Any], ...]
    current: dict[str, Any] | None
    query: FileQuery
    loaded: int
    scroll: int


class MainWindow(QMainWindow):
    """Browse registered PDF roots with a paged table and metadata pane."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        watch_controller: LibraryWatchController | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("ADSfolio")
        self.config = load_config()
        self.columns = build_columns(self.config.citation_styles)
        self.registries: dict[str, PdfRegistry] = {}
        self.root_entries: dict[str, RootEntry] = {}
        self.current_root: Path | None = None
        self.current_view = (
            LibraryView(self.config.last_view)
            if self.config.last_view in {view.value for view in LibraryView}
            else LibraryView.ALL
        )
        sortable_columns = {
            column.key for column in self.columns if column.sortable is True
        }
        sort_column_is_valid = self.config.sort_column in sortable_columns
        sort_column = (
            self.config.sort_column if sort_column_is_valid else "first_seen_at"
        )
        self.current_query = FileQuery(
            view=self.current_view,
            order_by=sort_column,
            descending=(self.config.sort_descending if sort_column_is_valid else True),
            secondary_order_by=(
                self.config.secondary_sort_column
                if self.config.secondary_sort_column in sortable_columns
                else None
            ),
            tertiary_order_by=(
                self.config.tertiary_sort_column
                if self.config.tertiary_sort_column in sortable_columns
                else None
            ),
        )
        self.update_checker = UpdateChecker(self)
        self.update_checker.finished.connect(self._update_check_finished)
        self.update_downloader = UpdateDownloader(self)
        self._about_dialog: AboutDialog | None = None
        self._update_dialog: UpdateDialog | None = None
        self.job: Any | None = None
        self._ads_library_dialog: AdsLibraryDialog | None = None
        self._ads_library_export_dialog: ExportAdsLibraryDialog | None = None
        self._close_requested = False
        self._refresh_selection_id: int | None = None
        self._ads_match_candidates: list[dict[str, str]] = []
        self._ambiguous_ads_match_context: tuple[Path, str, int | None] | None = None
        self._pending_watch_refresh_roots: set[Path] = set()
        self._watch_refresh_selection_id: int | None = None
        self._pending_watch_dialog: tuple[str, Path] | None = None
        self._pending_ads_refresh: (
            tuple[Path, tuple[str, ...], int | None, str | None] | None
        ) = None
        # Extra refresh-ads argument lists run after the pending refresh.
        self._pending_ads_follow_ups: tuple[tuple[str, ...], ...] = ()
        self._ads_refresh_root: Path | None = None
        self._local_refresh_context: _LibraryViewContext | None = None
        self._local_refresh_result: LibraryRefresh | None = None
        self._guided_identify_context: _LibraryViewContext | None = None
        self._guided_identify_commands: list[list[str]] = []
        self._foreground_paused_root: Path | None = None
        self._watch_refresh_timer = QTimer(self)
        self._watch_refresh_timer.setSingleShot(True)
        self._watch_refresh_timer.setInterval(0)
        self._watch_refresh_timer.timeout.connect(self._run_watch_refresh)
        self.watch_controller = watch_controller or LibraryWatchController(self)
        self._build_ui()
        self._connect_watch_controller()
        self._restore_config()
        self._sync_welcome()
        QTimer.singleShot(0, self._start_configured_watchers)

    def _build_ui(self) -> None:
        self.sidebar = QTreeWidget(self)
        self.sidebar.setHeaderHidden(True)
        self.sidebar.setIndentation(self.sidebar.fontMetrics().horizontalAdvance("  "))
        self.sidebar.setMinimumWidth(0)
        self.sidebar.setTextElideMode(Qt.ElideRight)
        self.sidebar.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.sidebar.itemSelectionChanged.connect(self._sidebar_changed)
        self.sidebar.setContextMenuPolicy(Qt.CustomContextMenu)
        self.sidebar.customContextMenuRequested.connect(self._sidebar_menu)
        self.libraries_item = QTreeWidgetItem(["LIBRARIES"])
        self.libraries_item.setData(0, Qt.UserRole + 1, "libraries")
        self.views_item = QTreeWidgetItem(["VIEWS"])
        self.views_item.setData(0, Qt.UserRole + 1, "views")
        self.views_item.setToolTip(
            0,
            "ADS groups use saved lookup status. Needs ADS lookup can include bibcodes and complete non-ADS metadata.",
        )
        self.sidebar.addTopLevelItem(self.libraries_item)
        self.sidebar.addTopLevelItem(self.views_item)
        self.model = FileTableModel(
            None,
            self.current_query,
            columns=self.columns,
            parent=self,
        )
        self.model.rowsInserted.connect(lambda *_args: self._update_status())
        self.model.query_failed.connect(self.statusBar().showMessage)
        self.table = LibraryTableView(self)
        self.table.setModel(self.model)
        self.table.setRootIsDecorated(False)
        self.table.setUniformRowHeights(True)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QTreeView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTreeView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        header = self.table.header()
        header.setSectionsMovable(True)
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(self._show_header_context_menu)
        self._rebuild_table_delegates()
        self.model.modelReset.connect(self._sync_detail_and_selection_actions)
        self.model.modelReset.connect(self._reset_link_activation)
        self.table.doubleClicked.connect(self._open_index)
        self.table.clicked.connect(self._table_clicked)
        self.table.selectionModel().currentChanged.connect(self._current_changed)
        self.table.selectionModel().selectionChanged.connect(
            lambda *_args: self._sync_detail_and_selection_actions()
        )
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)
        self.detail = DetailPane(self)
        self.detail.set_compact_rows(self.config.detail_compact_rows)
        self.detail.set_label_width(self.config.detail_label_width)
        self.detail.label_width_changed.connect(self._set_detail_label_width)
        self.detail.set_citation_styles(self.config.citation_styles)
        self.detail.set_row_order(self.config.detail_row_order)
        self.detail.set_hidden_rows(self.config.detail_hidden_rows)
        self.detail.set_expand_abstracts(self.config.expand_abstracts)
        self.detail.set_row_spacing(self.config.detail_row_spacing)
        self.detail.set_label_bold(self.config.detail_label_bold)
        self.detail.set_link_activation(self.config.link_activation)
        self._set_table_row_height(self.config.table_row_height)
        self.detail.copy_feedback.connect(
            lambda field: self.status.showMessage(f"Copied: {field}", 1500)
        )
        self.detail.metadata_changed.connect(self._save_detail_metadata)
        self.detail.ads_update_requested.connect(self._update_selected_from_ads)
        self.detail.row_order_changed.connect(self._set_detail_row_order)
        splitter = QSplitter(Qt.Horizontal, self)
        splitter.addWidget(self.sidebar)
        splitter.addWidget(self.table)
        splitter.addWidget(self.detail)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        self.notice_bar = NoticeBar(self)
        central = QWidget(self)
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self.notice_bar)
        self.lookup_guidance = QWidget(central)
        guidance_layout = QHBoxLayout(self.lookup_guidance)
        guidance_layout.setContentsMargins(8, 4, 8, 4)
        self.view_explanation = QLabel(self.lookup_guidance)
        self.view_explanation.setWordWrap(True)
        guidance_layout.addWidget(self.view_explanation, 1)
        self.check_papers_button = QPushButton("Identify papers…", self.lookup_guidance)
        self.check_papers_button.clicked.connect(lambda: self._guide_identification())
        guidance_layout.addWidget(self.check_papers_button)
        self.lookup_guidance.hide()
        central_layout.addWidget(self.lookup_guidance)
        self.welcome = WelcomePanel(central)
        self.welcome.add_folder_requested.connect(self._add_root)
        self.welcome.token_requested.connect(self._edit_token)
        self.welcome.profile_requested.connect(self._edit_user_profile)
        self.start_library_button = self.welcome.add_folder_button
        central_layout.addWidget(self.welcome, 1)
        central_layout.addWidget(splitter, 1)
        self.setCentralWidget(central)
        self.splitter = splitter
        splitter.splitterMoved.connect(self._remember_splitter_sizes)
        self.search = QLineEdit(self)
        self.search.setClearButtonEnabled(True)
        self.search.setPlaceholderText("Search title, author, bibcode, DOI, filename")
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(150)
        self.search.textChanged.connect(self.search_timer.start)
        self.search_timer.timeout.connect(self._search_changed)
        toolbar = QToolBar("Find", self)
        self.search_toolbar = toolbar
        toolbar.visibilityChanged.connect(self._find_bar_visibility_changed)
        toolbar.setObjectName("search_toolbar")
        toolbar.addWidget(QLabel("Find: "))
        toolbar.addWidget(self.search)
        self.addToolBar(toolbar)
        self.log_dock = QDockWidget("Job log", self)
        self.log_dock.setObjectName("job_log_dock")
        self.log = QPlainTextEdit(self.log_dock)
        self.log.setReadOnly(True)
        self.log_dock.setWidget(self.log)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.log_dock)
        self.log_dock.hide()
        self._last_link_activation: tuple[str, float] | None = None
        self.status = self.statusBar()
        self.job_progress = quiet_progress_bar(self)
        self.job_progress.setRange(0, 0)
        self.job_progress.setTextVisible(False)
        self.job_progress.setFixedWidth(96)
        self.job_progress.hide()
        self.status.addPermanentWidget(self.job_progress)
        self._build_actions()
        toolbar.addSeparator()
        toolbar.addAction(self.refresh_action)
        toolbar.addAction(self.identify_action)
        toolbar.addAction(self.rename_action)
        self.ads_refresh_button = QToolButton(self)
        self.ads_refresh_button.setText("ADS Refresh")
        self.ads_refresh_button.setPopupMode(QToolButton.InstantPopup)
        self.ads_refresh_button.setToolTip(
            "Look up stored bibcodes, update ADS metadata, or apply saved rename rules"
        )
        ads_menu = QMenu(self.ads_refresh_button)
        ads_menu.addAction(self.refresh_selected_ads_action)
        ads_menu.addAction(self.update_selected_ads_action)
        ads_menu.addSeparator()
        ads_menu.addAction(self.link_bibcodes_action)
        ads_menu.addAction(self.refresh_all_ads_action)
        ads_menu.addAction(self.refresh_all_citation_metrics_action)
        self.ads_refresh_button.setMenu(ads_menu)
        toolbar.addWidget(self.ads_refresh_button)
        # Round icon buttons sit together at the toolbar's end with a small gap.
        round_buttons = QWidget(self)
        round_layout = QHBoxLayout(round_buttons)
        round_layout.setContentsMargins(6, 0, 4, 0)
        round_layout.setSpacing(6)
        # "App Update" avoids confusion with the ADS metadata update actions.
        self.update_button = UpdateButton(round_buttons)
        self.update_button.clicked.connect(lambda: self._show_update_dialog())
        round_layout.addWidget(self.update_button)
        self.github_button = RoundIconButton(
            "github", "Open ADSfolio on GitHub", round_buttons
        )
        self.github_button.clicked.connect(self._open_github)
        round_layout.addWidget(self.github_button)
        toolbar.addWidget(round_buttons)
        self.update_checker.changed.connect(self._sync_update_button)
        self._sync_update_button()
        self._sync_refresh_actions()
        self._apply_table_appearance()

    def _build_actions(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        self.add_action = QAction("Add Library Root…", self)
        self.local_references_action = QAction("Local References…", self)
        self.merge_annotations_action = QAction("Merge Annotations…", self)
        self.merge_annotations_action.triggered.connect(self._merge_annotations)
        self.local_references_action.triggered.connect(self._open_local_references)
        self.remove_action = QAction("Remove Root", self)
        self.refresh_action = QAction("Refresh", self)
        refresh_shortcuts = [QKeySequence("F5")]
        for shortcut in QKeySequence.keyBindings(QKeySequence.StandardKey.Refresh):
            if shortcut not in refresh_shortcuts:
                refresh_shortcuts.append(shortcut)
        self.refresh_action.setShortcuts(refresh_shortcuts)
        self.refresh_action.setToolTip(
            "Scan local PDFs and reconcile added, changed, moved, and missing files"
        )
        self.reconcile_file_facts_action = QAction("Reconcile File Facts…", self)
        self.load_more_action = QAction("Load Next Page", self)
        self.remove_missing_action = QAction("Remove Missing Registry Records…", self)
        self.quit_action = QAction("Quit", self)
        self.add_action.triggered.connect(self._add_root)
        self.remove_action.triggered.connect(self._remove_root)
        self.refresh_action.triggered.connect(lambda: self._refresh_local_files())
        self.reconcile_file_facts_action.triggered.connect(
            lambda: self._refresh_local_files()
        )
        self.load_more_action.triggered.connect(self._load_next_page)
        self.remove_missing_action.triggered.connect(self._remove_missing_records)
        self.quit_action.triggered.connect(self.close)
        for action in (
            self.add_action,
            self.local_references_action,
            self.remove_action,
            self.refresh_action,
            self.reconcile_file_facts_action,
            self.load_more_action,
            self.remove_missing_action,
            self.quit_action,
        ):
            file_menu.addAction(action)
        tools = self.menuBar().addMenu("Tools")
        self.token_action = QAction("NASA ADS Token…", self)
        self.token_action.triggered.connect(self._edit_token)
        tools.addAction(self.token_action)
        self.profile_action = QAction("My Publication Profile…", self)
        self.cv_action = QAction("CV Generator…", self)
        self.cv_action.setToolTip(
            "Make a publication list for your CV in LaTeX, HTML, Markdown, or text"
        )
        self.cv_action.triggered.connect(self._open_cv_generator)
        self.export_selected_action = QAction("Export Selected Papers…", self)
        self.export_selected_action.setToolTip(
            "Write the selected papers as a list in LaTeX, HTML, Markdown, or text, "
            "from their stored details, without ADS"
        )
        self.export_selected_action.triggered.connect(
            lambda: self._export_selected_papers()
        )
        self.citation_styles_action = QAction("Custom Citation Styles…", self)
        self.identify_action = QAction("Identify…", self)
        self.identify_action.setShortcut("Ctrl+Shift+I")
        self.rename_action = QAction("Rename…", self)
        self.rename_action.setShortcut("Ctrl+Shift+R")
        self.cancel_job_action = QAction("Cancel Current Job", self)
        self.refresh_selected_ads_action = QAction(
            "Refresh and Rename Selected from ADS", self
        )
        self.update_selected_ads_action = QAction(
            "Update and Rename Selected by Identifier…", self
        )
        self.update_selected_ads_action.triggered.connect(
            lambda: self._update_selected_from_ads("")
        )
        self.link_bibcodes_action = QAction("Look Up Stored Bibcodes…", self)
        self.link_bibcodes_action.triggered.connect(self._link_stored_bibcodes)
        self.refresh_all_ads_action = QAction(
            "Refresh and Rename All ADS Records…", self
        )
        self.refresh_all_citation_metrics_action = QAction(
            "Refresh and Rename All Citation Splits…", self
        )
        self.add_to_ads_library_action = QAction("Add to ADS Library…", self)
        self.export_ads_library_action = QAction("Export ADS Library…", self)
        self.watch_current_action = QAction("Watch Current Library", self)
        self.watch_on_start_action = QAction("Autowatch at Start", self)
        self.auto_process_action = QAction("Automatically Identify && Rename", self)
        for action in (
            self.watch_current_action,
            self.watch_on_start_action,
            self.auto_process_action,
        ):
            action.setCheckable(True)
        self.cancel_job_action.setEnabled(False)
        self.profile_action.triggered.connect(self._edit_user_profile)
        self.citation_styles_action.triggered.connect(self._edit_citation_styles)
        self.identify_action.triggered.connect(lambda: self._guide_identification())
        self.rename_action.triggered.connect(self._rename)
        self.cancel_job_action.triggered.connect(self._cancel_job)
        self.refresh_selected_ads_action.triggered.connect(self._refresh_selected_ads)
        self.refresh_all_ads_action.triggered.connect(self._refresh_all_ads)
        self.refresh_all_citation_metrics_action.triggered.connect(
            self._refresh_all_citation_metrics
        )
        self.add_to_ads_library_action.triggered.connect(self._add_to_ads_library)
        self.export_ads_library_action.triggered.connect(self._export_ads_library)
        self.watch_current_action.triggered.connect(self._toggle_current_watch)
        self.watch_on_start_action.triggered.connect(
            lambda checked: self._set_current_watch_policy("watch_on_start", checked)
        )
        self.auto_process_action.triggered.connect(
            lambda checked: self._set_current_watch_policy(
                "auto_process_new_pdfs", checked
            )
        )
        tools.addAction(self.profile_action)
        tools.addAction(self.cv_action)
        tools.addAction(self.export_selected_action)
        tools.addAction(self.citation_styles_action)
        tools.addSeparator()
        tools.addAction(self.merge_annotations_action)
        tools.addAction(self.identify_action)
        tools.addAction(self.rename_action)
        tools.addAction(self.cancel_job_action)
        tools.addSeparator()
        tools.addAction(self.refresh_selected_ads_action)
        tools.addAction(self.link_bibcodes_action)
        tools.addAction(self.refresh_all_ads_action)
        tools.addAction(self.refresh_all_citation_metrics_action)
        tools.addAction(self.add_to_ads_library_action)
        tools.addAction(self.export_ads_library_action)
        tools.addSeparator()
        tools.addAction(self.watch_current_action)
        tools.addAction(self.watch_on_start_action)
        tools.addAction(self.auto_process_action)
        view = self.menuBar().addMenu("View")
        self.log_dock.toggleViewAction().setShortcut("Ctrl+Shift+J")
        view.addAction(self.log_dock.toggleViewAction())
        self.sidebar_action = QAction("Show Libraries", self)
        self.sidebar_action.setCheckable(True)
        self.sidebar_action.setChecked(self.config.sidebar_visible)
        self.sidebar_action.setShortcut("Ctrl+Shift+L")
        self.sidebar_action.triggered.connect(self._toggle_sidebar)
        view.addAction(self.sidebar_action)
        self.search_toolbar.toggleViewAction().setText("Show Find Bar")
        self.search_toolbar.toggleViewAction().setShortcut("Ctrl+Shift+F")
        view.addAction(self.search_toolbar.toggleViewAction())
        self.detail_action = QAction("Show Detail Pane", self)
        self.detail_action.setCheckable(True)
        self.detail_action.setChecked(self.config.detail_visible)
        self.detail_action.setShortcut("Ctrl+Shift+D")
        self.detail_action.triggered.connect(self._toggle_detail)
        view.addAction(self.detail_action)
        self.appearance_action = QAction("Appearance…", self)
        self.appearance_action.triggered.connect(self._edit_appearance)
        self.sort_priorities_action = QAction("Sort Priorities…", self)
        self.sort_priorities_action.triggered.connect(self._edit_sort_priorities)
        view.addAction(self.appearance_action)
        view.addAction(self.sort_priorities_action)
        self.open_selected_action = QAction(self.table)
        self.open_selected_action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        self.table.addAction(self.open_selected_action)
        self.open_selected_action.setShortcuts(["Return", "Enter"])
        self.open_selected_action.triggered.connect(self._open_current)
        self.search_focus_action = QAction("Find…", self)
        self.search_focus_action.setShortcut(QKeySequence.StandardKey.Find)
        self.search_focus_action.triggered.connect(self._focus_search)
        self.clear_search_action = QAction("Clear Find", self)
        self._clear_search_shortcuts = []
        for widget in (self.search, self.table):
            shortcut = QShortcut(QKeySequence("Escape"), widget)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(self._clear_search)
            self._clear_search_shortcuts.append(shortcut)
        self.clear_search_action.triggered.connect(self._clear_search)
        view.addSeparator()
        view.addAction(self.search_focus_action)
        view.addAction(self.clear_search_action)
        self.addActions(
            [
                self.search_focus_action,
                self.clear_search_action,
            ]
        )
        help_menu = self.menuBar().addMenu("Help")
        self.about_action = QAction("About ADSfolio", self)
        self.about_action.setMenuRole(QAction.AboutRole)
        self.about_action.triggered.connect(self._show_about)
        self.check_updates_action = QAction("Check for Updates…", self)
        self.check_updates_action.triggered.connect(self._check_for_updates)
        self.automatic_updates_action = QAction("Automatically Check for Updates", self)
        self.automatic_updates_action.setCheckable(True)
        self.automatic_updates_action.setChecked(
            self.config.check_updates_automatically
        )
        self.automatic_updates_action.toggled.connect(self._set_automatic_updates)
        help_menu.addAction(self.about_action)
        help_menu.addAction(self.check_updates_action)
        help_menu.addAction(self.automatic_updates_action)
        self._sync_ads_library_action()

    def check_updates_on_start(self) -> None:
        """Check for updates when startup preferences allow it."""
        if self.config.check_updates_automatically and not self._close_requested:
            self.update_checker.check()

    def _show_about(self) -> None:
        if self._about_dialog is None:
            self._about_dialog = AboutDialog(
                self.update_checker,
                self.automatic_updates_action,
                self,
                browser_command=self.config.web_browser_command,
            )
        self._about_dialog.browser_command = self.config.web_browser_command
        self._about_dialog.show()
        self._about_dialog.raise_()
        self._about_dialog.activateWindow()

    def _check_for_updates(self) -> None:
        self._show_update_dialog(check=True)

    def _open_github(self) -> None:
        """Open the project repository in the configured browser."""
        if not open_url(REPOSITORY_URL, application=self.config.web_browser_command):
            self.status.showMessage(
                f"Could not open the browser. Open this address: {REPOSITORY_URL}"
            )

    def _update_is_new(self) -> bool:
        """Return whether an available update has not been skipped."""
        release = self.update_checker.latest
        return (
            release is not None
            and self.update_checker.update_available
            and str(release.version) != self.config.skipped_update_version
        )

    def _sync_update_button(self) -> None:
        release = self.update_checker.latest
        dot = self._update_is_new()
        self.update_button.set_dot_visible(dot)
        if dot and release is not None:
            tip = f"Update available: ADSfolio {release.version}"
        elif self.update_checker.busy:
            tip = "Checking for updates…"
        else:
            tip = "Check for ADSfolio updates"
        self.update_button.setToolTip(tip)
        self.update_button.setAccessibleName(tip)

    def _show_update_dialog(self, *, check: bool = False) -> None:
        """Open the update window, checking first when no result exists."""
        if self._update_dialog is None:
            self._update_dialog = UpdateDialog(
                self.update_checker,
                self.update_downloader,
                installation_kind(),
                self,
                browser_command=self.config.web_browser_command,
            )
            self._update_dialog.skip_requested.connect(self._skip_update)
        dialog = self._update_dialog
        dialog.browser_command = self.config.web_browser_command
        dialog.skipped_version = self.config.skipped_update_version
        if check or (
            self.update_checker.latest is None and not self.update_checker.busy
        ):
            self.update_checker.check()
        dialog.sync_state()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _skip_update(self, version: str) -> None:
        self.config.skipped_update_version = version
        self.notice_bar.dismiss("application-update")
        self._sync_update_button()
        try:
            save_config(self.config)
        except OSError as exc:
            self.notice_bar.enqueue(
                Notice(
                    "update-preference",
                    f"Could not save the skipped version: {exc}",
                    severity="error",
                )
            )

    def _set_automatic_updates(self, enabled: bool) -> None:
        self.config.check_updates_automatically = enabled
        try:
            save_config(self.config)
        except OSError as exc:
            self.notice_bar.enqueue(
                Notice(
                    "update-preference",
                    f"Could not save the update preference: {exc}",
                    severity="error",
                )
            )

    def _update_check_finished(self) -> None:
        release = self.update_checker.latest
        if release is not None:
            self.notice_bar.dismiss("application-update")
        if release is not None and self._update_is_new():
            self.notice_bar.enqueue(
                Notice(
                    "application-update",
                    f"ADSfolio {release.version} is available.",
                    action=NoticeAction.VIEW_RELEASE,
                    payload=release,
                )
            )

    def _restore_config(self) -> None:
        if self.config.window_geometry:
            from PySide6.QtCore import QByteArray

            self.restoreGeometry(
                QByteArray.fromBase64(self.config.window_geometry.encode())
            )
        else:
            self.resize(*DEFAULT_WINDOW_SIZE)
        if self.config.splitter_sizes:
            self.splitter.setSizes(self.config.splitter_sizes)
        else:
            self.splitter.setSizes(list(DEFAULT_SPLITTER_SIZES))
        for entry in self.config.roots:
            try:
                self._register_root(
                    Path(entry.path),
                    entry.label,
                    entry.recursive,
                    watch_on_start=entry.watch_on_start,
                    auto_process_new_pdfs=entry.auto_process_new_pdfs,
                )
            except (OSError, ValueError) as exc:
                self.notice_bar.enqueue(
                    Notice(
                        f"root-restore:{entry.path}",
                        f"Could not restore library root {entry.path}: {exc}",
                        severity="error",
                    )
                )
        if self.config.window_state:
            from PySide6.QtCore import QByteArray

            self.restoreState(QByteArray.fromBase64(self.config.window_state.encode()))
        for index, column in enumerate(self.columns):
            width = self.config.column_widths.get(column.key)
            if width is not None:
                self.table.setColumnWidth(index, width)
        self._apply_table_layout()
        last_view = self.current_view
        if self.config.last_root:
            try:
                self._select_root(Path(self.config.last_root))
            except (OSError, ValueError) as exc:
                self.notice_bar.enqueue(
                    Notice(
                        "last-root-restore",
                        f"Could not restore the last selected root: {exc}",
                        severity="error",
                    )
                )
        if self.current_root is None and self.root_entries:
            self._select_root(Path(next(iter(self.root_entries))))
        if self.current_root is not None and last_view is not LibraryView.ALL:
            self._select_view(last_view)
        self.sidebar.setVisible(self.config.sidebar_visible)
        self.detail.setVisible(self.config.detail_visible)
        self._sync_watch_actions()

    def _set_detail_label_width(self, width: int | None) -> None:
        """Retain the user's shared label-column width."""
        self.config.detail_label_width = width

    def _remember_splitter_sizes(self) -> None:
        """Retain panel widths when Qt reports zero for a hidden panel."""
        previous = self.config.splitter_sizes or list(DEFAULT_SPLITTER_SIZES)
        self.config.splitter_sizes = [
            width
            or (
                previous[index]
                if index < len(previous)
                else DEFAULT_SPLITTER_SIZES[index]
            )
            for index, width in enumerate(self.splitter.sizes())
        ]

    def _toggle_sidebar(self, visible: bool) -> None:
        """Show or hide libraries and retain the preference."""
        self._remember_splitter_sizes()
        self.config.sidebar_visible = visible
        self.sidebar.setVisible(visible)
        if visible and self.splitter.sizes()[0] == 0:
            self.splitter.setSizes(self.config.splitter_sizes)

    def _toggle_detail(self, visible: bool) -> None:
        """Show or hide metadata and retain the preference."""
        self._remember_splitter_sizes()
        self.config.detail_visible = visible
        self.detail.setVisible(visible)
        if visible and self.splitter.sizes()[2] == 0:
            self.splitter.setSizes(self.config.splitter_sizes)

    def _find_bar_visibility_changed(self, visible: bool) -> None:
        """Keep keyboard focus usable after hiding Find."""
        if not visible and self.search.hasFocus():
            self.table.setFocus()

    def _focus_search(self) -> None:
        """Reveal Find and select its current query for replacement."""
        self.search_toolbar.show()
        self.search.setFocus()
        self.search.selectAll()

    def _register_root(
        self,
        path: Path,
        label: str = "",
        recursive: bool = False,
        *,
        watch_on_start: bool = False,
        auto_process_new_pdfs: bool = False,
    ) -> None:
        if "\0" in str(path):
            raise ValueError("Library root contains a null character")
        path = path.expanduser().resolve()
        key = str(path)
        if key not in self.registries:
            registry = PdfRegistry(path)
            if path.is_dir():
                registry.catalog.discover_root(path, recursive=recursive)
            self.registries[key] = registry
            self.root_entries[key] = RootEntry(
                path=key,
                label=label,
                recursive=recursive,
                watch_on_start=watch_on_start,
                auto_process_new_pdfs=auto_process_new_pdfs,
            )
        else:
            return
        count = self._safe_count(self.registries[key])
        count_text = str(count) if count is not None else "?"
        root_item = QTreeWidgetItem(
            [f"{label or path.name or str(path)} ({count_text})"]
        )
        root_item.setToolTip(0, key)
        root_item.setData(0, Qt.UserRole, key)
        root_item.setData(0, Qt.UserRole + 1, "root")
        self.libraries_item.addChild(root_item)
        self.libraries_item.setExpanded(True)

    def _connect_watch_controller(self) -> None:
        """Connect controller events after the notice surface exists."""
        self.watch_controller.started.connect(self._handle_watch_started)
        self.watch_controller.stopped.connect(self._handle_watch_stopped)
        self.watch_controller.detected.connect(self._handle_watch_detection)
        self.watch_controller.outcome.connect(self._handle_watch_outcome)
        self.watch_controller.failed.connect(self._handle_watch_failure)
        self.watch_controller.paused.connect(self._handle_watch_paused)
        self.notice_bar.action_requested.connect(self._handle_notice_action)
        self.notice_bar.dismiss_requested.connect(self._handle_notice_dismiss)
        self.notice_bar.action_all_requested.connect(self._handle_notice_actions)
        self.notice_bar.dismiss_all_requested.connect(self._handle_notice_dismissals)

    def _sync_config_roots(self) -> None:
        self.config.roots = [
            self.root_entries[key]
            for key in self.registries
            if key in self.root_entries
        ]

    def _sync_watch_actions(self) -> None:
        root = self.current_root.resolve() if self.current_root is not None else None
        entry = self.root_entries.get(str(root)) if root is not None else None
        actions = (
            self.watch_current_action,
            self.watch_on_start_action,
            self.auto_process_action,
        )
        blockers = [QSignalBlocker(action) for action in actions]
        try:
            for action in actions:
                action.setEnabled(entry is not None)
            self.watch_current_action.setChecked(
                root is not None and self.watch_controller.is_watching(root)
            )
            self.watch_on_start_action.setChecked(
                entry.watch_on_start if entry is not None else False
            )
            self.auto_process_action.setChecked(
                entry.auto_process_new_pdfs if entry is not None else False
            )
            if (
                self._local_refresh_context is not None
                and self._local_refresh_context.root == root
            ):
                self.watch_current_action.setEnabled(False)
        finally:
            del blockers

    def _sync_ads_library_action(self) -> None:
        """Synchronize ADS library actions with current window activity."""
        if hasattr(self, "add_to_ads_library_action"):
            self.add_to_ads_library_action.setEnabled(self._can_add_to_ads_library())
        if hasattr(self, "export_ads_library_action"):
            self.export_ads_library_action.setEnabled(self._can_export_ads_library())
        self._sync_refresh_actions()

    def _sync_refresh_actions(self) -> None:
        """Enable refresh controls only when their root and selection are usable."""
        if not hasattr(self, "refresh_selected_ads_action"):
            return
        available = (
            self.current_root is not None
            and self.job is None
            and self._pending_watch_dialog is None
            and self._pending_ads_refresh is None
            and self._local_refresh_context is None
            and not self._close_requested
        )
        record = self._selected_record()
        bibcode = (
            (record.get("metadata") or {}).get("bibcode") or record.get("bibcode")
            if record
            else None
        )
        self.check_papers_button.setEnabled(available)
        self._sync_export_action()
        self.identify_action.setEnabled(available)
        self.rename_action.setEnabled(available)
        self.remove_action.setEnabled(self.current_root is not None)
        self.refresh_action.setEnabled(available)
        self.reconcile_file_facts_action.setEnabled(available)
        self.remove_missing_action.setEnabled(available)
        self.refresh_selected_ads_action.setEnabled(
            available
            and bool(bibcode)
            and not bool((record.get("metadata") or {}).get("_adsfolio_metadata_stale"))
        )
        self.update_selected_ads_action.setEnabled(available and record is not None)
        self.link_bibcodes_action.setEnabled(available)
        self.refresh_all_ads_action.setEnabled(available)
        self.refresh_all_citation_metrics_action.setEnabled(available)
        if hasattr(self, "ads_refresh_button"):
            self.ads_refresh_button.setEnabled(available)
        self._sync_watch_actions()

    def _can_add_to_ads_library(self) -> bool:
        """Return whether this window can open an ADS library dialog."""
        return (
            self.current_root is not None
            and self.job is None
            and self._ads_library_dialog is None
            and self._ads_library_export_dialog is None
        )

    def _can_export_ads_library(self) -> bool:
        """Return whether this window can open the remote export dialog."""
        return (
            not self._close_requested
            and self.job is None
            and self._ads_library_dialog is None
            and self._ads_library_export_dialog is None
        )

    def _selected_records(self) -> tuple[dict[str, Any], ...]:
        """Return deep-copied selected records in visual table-row order."""
        rows = sorted(
            {index.row() for index in self.table.selectionModel().selectedRows()}
        )
        return tuple(
            deepcopy(record)
            for row in rows
            if (record := self.model.record_at(row)) is not None
        )

    def _ads_library_dialog_closed(self, dialog: AdsLibraryDialog) -> None:
        """Release the window's ADS library dialog after it closes."""
        if self._ads_library_dialog is dialog:
            self._ads_library_dialog = None
            self._sync_ads_library_action()

    def _ads_library_export_dialog_closed(self, dialog: ExportAdsLibraryDialog) -> None:
        """Release the window's ADS export dialog after it closes."""
        if self._ads_library_export_dialog is dialog:
            self._ads_library_export_dialog = None
            self._sync_ads_library_action()
            if self._close_requested and self.job is None:
                QTimer.singleShot(0, self.close)

    def _add_to_ads_library(self) -> None:
        """Open an ADS library dialog with immutable local record snapshots."""
        if not self._can_add_to_ads_library():
            self._sync_ads_library_action()
            return
        assert self.current_root is not None
        root = self.current_root.expanduser().resolve()
        registry = self.registries[str(root)]
        selected_records = self._selected_records()
        try:
            all_records = tuple(
                deepcopy(record) for record in registry.query_files(resolved=True)
            )
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            self.status.showMessage("Library is busy; try again in a moment.")
            return

        dialog = AdsLibraryDialog(
            root,
            selected_records,
            all_records,
            parent=self,
        )
        self._ads_library_dialog = dialog
        dialog.finished.connect(lambda _result: self._ads_library_dialog_closed(dialog))
        self._sync_ads_library_action()
        dialog.show()

    def _open_local_references(self) -> None:
        """Open local bibliography intake and export without requiring a PDF root."""
        try:
            dialog = ReferencesDialog(parent=self)
        except (OSError, ValueError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "Local references", str(exc))
            return
        dialog.references_changed.connect(self.refresh)
        dialog.exec()
        dialog.deleteLater()

    def _export_ads_library(self) -> None:
        """Open the remote ADS library export dialog."""
        if not self._can_export_ads_library():
            self._sync_ads_library_action()
            return
        dialog = ExportAdsLibraryDialog(parent=self)
        self._ads_library_export_dialog = dialog
        dialog.finished.connect(
            lambda _result: self._ads_library_export_dialog_closed(dialog)
        )
        self._sync_ads_library_action()
        dialog.show()

    def _start_root_watch(self, root: Path) -> bool:
        normalized = root.expanduser().resolve()
        entry = self.root_entries.get(str(normalized))
        if entry is None:
            return False
        if not normalized.is_dir():
            self.notice_bar.enqueue(
                Notice(
                    f"watch-missing:{normalized}",
                    f"Watch root does not exist: {normalized}",
                    severity="error",
                    root=normalized,
                )
            )
            return False
        try:
            self.watch_controller.start_root(normalized, recursive=entry.recursive)
        except (OSError, RuntimeError, ValueError) as exc:
            self.notice_bar.enqueue(
                Notice(
                    f"watch-start:{normalized}",
                    f"Could not start watching {normalized}: {exc}",
                    severity="error",
                    root=normalized,
                )
            )
            return False
        return self.watch_controller.is_watching(normalized)

    def _start_configured_watchers(self) -> None:
        for entry in tuple(self.root_entries.values()):
            if entry.watch_on_start:
                self._start_root_watch(Path(entry.path))
        self._sync_watch_actions()

    def _toggle_current_watch(self, checked: bool) -> None:
        if self.current_root is None:
            self._sync_watch_actions()
            return
        root = self.current_root.resolve()
        if (
            self._local_refresh_context is not None
            and self._local_refresh_context.root == root
        ):
            self._sync_watch_actions()
            return
        if checked:
            self._start_root_watch(root)
        elif not self.watch_controller.stop_root(root):
            self.notice_bar.enqueue(
                Notice(
                    f"watch-stop:{root}",
                    f"Could not stop watching {root}",
                    severity="error",
                    root=root,
                )
            )
        else:
            self.notice_bar.clear_root(root)
        self._sync_watch_actions()

    def _set_current_watch_policy(self, field: str, checked: bool) -> None:
        if self.current_root is None:
            self._sync_watch_actions()
            return
        root = self.current_root.resolve()
        key = str(root)
        previous = self.root_entries[key]
        self.root_entries[key] = replace(previous, **{field: checked})
        self._sync_config_roots()
        try:
            save_config(self.config)
        except OSError as exc:
            self.root_entries[key] = previous
            self._sync_config_roots()
            self.notice_bar.enqueue(
                Notice(
                    f"watch-save:{root}:{field}",
                    f"Could not save watch setting: {exc}",
                    severity="error",
                    root=root,
                )
            )
        self._sync_watch_actions()

    def _set_root_recursive(self, root: Path, recursive: bool) -> bool:
        normalized = root.expanduser().resolve()
        key = str(normalized)
        entry = self.root_entries.get(key)
        if entry is None:
            return False
        if self.watch_controller.is_watching(normalized):
            self.notice_bar.enqueue(
                Notice(
                    f"watch-recursive:{normalized}",
                    "Stop watching this library before changing recursive scope.",
                    severity="warning",
                    root=normalized,
                )
            )
            return False
        previous = entry
        self.root_entries[key] = replace(entry, recursive=recursive)
        self._sync_config_roots()
        try:
            save_config(self.config)
        except OSError as exc:
            self.root_entries[key] = previous
            self._sync_config_roots()
            self.notice_bar.enqueue(
                Notice(
                    f"recursive-save:{normalized}",
                    f"Could not save recursive setting: {exc}",
                    severity="error",
                    root=normalized,
                )
            )
            return False
        return True

    def _handle_watch_started(self, _session_id: str) -> None:
        self._update_root_watch_labels()
        self._sync_watch_actions()

    def _handle_watch_stopped(self, _session_id: str) -> None:
        self._update_root_watch_labels()
        self._sync_watch_actions()

    def _handle_watch_paused(self, session_id: str) -> None:
        pending = self._pending_watch_dialog
        if pending is None:
            return
        kind, root = pending
        if self.watch_controller.session_id(root) != session_id:
            return
        self._pending_watch_dialog = None
        self._foreground_paused_root = root
        if kind == "ads_refresh":
            self._start_pending_ads_refresh(root)
        elif kind == "local_refresh":
            self._launch_local_refresh(root)
        elif kind == "identify":
            self._open_identify_dialog(root)
        elif kind == "guided_identify":
            self._launch_guided_identification(root)
        else:
            self._open_rename_dialog(root)

    def _handle_watch_failure(self, failure: WatchFailure) -> None:
        event = failure.event
        current = event is not None and self._is_current_watch_event(event)
        self.notice_bar.enqueue(
            Notice(
                self._watch_notice_key(event)
                if event is not None
                else f"watch-failure:{failure.root}:{failure.session_id}",
                failure.message,
                severity="error",
                action=NoticeAction.RETRY if current else None,
                payload=event if current else failure,
                root=failure.root,
            )
        )
        self._sync_watch_actions()

    def _is_current_watch_event(self, event: WatchEvent) -> bool:
        root = event.root.expanduser().resolve()
        return self.watch_controller.session_id(root) == event.session_id

    @staticmethod
    def _watch_notice_key(event: WatchEvent) -> str:
        identity = event.identity
        return (
            f"watch:{event.root.resolve()}:{event.session_id}:"
            f"{identity.device}:{identity.inode}:{identity.birth_time_ns}:"
            f"{identity.generation}"
        )

    def _effective_identify_settings(self, root: Path) -> IdentifySettings:
        normalized = root.expanduser().resolve()
        global_settings = Config.load_identify_settings()
        if self.config.identify_scopes.get(str(normalized), "global") != "library":
            return global_settings
        registry = self.registries.get(str(normalized))
        if registry is None:
            return global_settings
        return CentralPdfRegistryState(registry).identify_settings or global_settings

    def _effective_rename_settings(self, root: Path) -> RenameSettings:
        normalized = root.expanduser().resolve()
        global_settings = Config.load_rename_settings()
        if self.config.rename_scopes.get(str(normalized), "global") != "library":
            return global_settings
        registry = self.registries.get(str(normalized))
        if registry is None:
            return global_settings
        return CentralPdfRegistryState(registry).rename_settings or global_settings

    def _handle_watch_detection(self, event: WatchEvent) -> None:
        if not self._is_current_watch_event(event):
            return
        entry = self.root_entries.get(str(event.root.resolve()))
        if entry is None:
            return
        identify_settings = self._effective_identify_settings(event.root)
        try:
            excluded = WatchIdentifyPolicy.from_settings(
                event.root, identify_settings
            ).excludes(event.path)
        except ValueError:
            excluded = False
        if excluded:
            self.watch_controller.dismiss(event)
            return
        if entry.auto_process_new_pdfs:
            self._process_watch_event(event)
            return
        self.notice_bar.enqueue(
            Notice(
                self._watch_notice_key(event),
                f"New PDF found: {event.path.name}",
                action=NoticeAction.IDENTIFY_RENAME,
                payload=event,
                root=event.root,
            )
        )

    def _process_watch_event(self, event: WatchEvent) -> None:
        if not self._is_current_watch_event(event):
            return
        self.watch_controller.process(
            event,
            self._effective_identify_settings(event.root),
            self._effective_rename_settings(event.root),
        )

    def _handle_watch_outcome(self, outcome: WatchOutcome) -> None:
        event = outcome.event
        if not self._is_current_watch_event(event):
            return
        state = event.state
        destination = outcome.destination or event.path
        message = (
            f"{destination.name}: {outcome.message}"
            if outcome.message
            else f"{state.value.title()}: {destination.name}"
        )
        self.notice_bar.enqueue(
            Notice(
                self._watch_notice_key(event),
                message,
                severity=(
                    "error"
                    if state is WatchState.FAILED
                    else (
                        "success"
                        if state in {WatchState.RESOLVED, WatchState.RENAMED}
                        else "warning"
                    )
                ),
                action=(NoticeAction.RETRY if state is WatchState.FAILED else None),
                payload=event,
                root=event.root,
                details=str(destination),
            )
        )
        if state in {
            WatchState.RESOLVED,
            WatchState.RENAMED,
            WatchState.UNRESOLVED,
        }:
            self._schedule_watch_refresh(event.root)

    def _handle_notice_action(self, payload: object) -> None:
        if isinstance(payload, Release):
            self._show_update_dialog()
            return
        if not isinstance(payload, WatchEvent) or not self._is_current_watch_event(
            payload
        ):
            return
        if payload.state is WatchState.FAILED:
            self.watch_controller.retry(payload)
        elif payload.state is WatchState.DETECTED:
            self._process_watch_event(payload)

    def _handle_notice_dismiss(self, payload: object) -> None:
        if (
            isinstance(payload, WatchEvent)
            and payload.state is WatchState.DETECTED
            and self._is_current_watch_event(payload)
        ):
            self.watch_controller.dismiss(payload)

    def _handle_notice_actions(self, payloads: tuple[object, ...]) -> None:
        for payload in payloads:
            self._handle_notice_action(payload)

    def _handle_notice_dismissals(self, payloads: tuple[object, ...]) -> None:
        for payload in payloads:
            self._handle_notice_dismiss(payload)

    def _schedule_watch_refresh(self, root: Path) -> None:
        normalized = root.expanduser().resolve()
        if not self._pending_watch_refresh_roots:
            record = self._selected_record()
            self._watch_refresh_selection_id = (
                int(record["location_id"])
                if record is not None and record.get("location_id") is not None
                else None
            )
        self._pending_watch_refresh_roots.add(normalized)
        if not self._watch_refresh_timer.isActive():
            self._watch_refresh_timer.start()

    def _run_watch_refresh(self) -> None:
        roots = tuple(self._pending_watch_refresh_roots)
        self._pending_watch_refresh_roots.clear()
        selection_id = self._watch_refresh_selection_id
        self._watch_refresh_selection_id = None
        for root in roots:
            if str(root) in self.registries:
                self.refresh(root)
        if (
            selection_id is not None
            and self.current_root is not None
            and self.current_root.resolve() in roots
        ):
            self._select_file_id(selection_id)

    def _update_root_watch_labels(self) -> None:
        for index in range(self.libraries_item.childCount()):
            item = self.libraries_item.child(index)
            key = str(item.data(0, Qt.UserRole))
            entry = self.root_entries.get(key)
            registry = self.registries.get(key)
            if entry is None or registry is None:
                continue
            count = self._safe_count(registry)
            count_text = str(count) if count is not None else "?"
            marker = "● " if self.watch_controller.is_watching(Path(key)) else ""
            item.setText(
                0,
                f"{marker}{entry.label or Path(key).name or key} ({count_text})",
            )

    def _populate_views(self, registry: PdfRegistry) -> None:
        """Show virtual-view counts for the selected library root."""
        self.views_item.takeChildren()
        for view in LibraryView:
            count = self._safe_count(registry, **FileQuery(view=view).to_filters())
            item = QTreeWidgetItem(
                [f"{view.label} ({count if count is not None else '?'})"]
            )
            item.setData(0, Qt.UserRole, str(self.current_root))
            item.setData(0, Qt.UserRole + 1, view.value)
            item.setToolTip(0, view.description)
            self.views_item.addChild(item)
        self.views_item.setExpanded(True)

    def _safe_count(self, registry: PdfRegistry, **filters: Any) -> int | None:
        """Return a registry count, reporting an active SQLite writer safely."""
        try:
            return registry.count_files_matching(**filters)
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            self.status.showMessage("Library is busy; try again in a moment.")
            return None

    def _select_root(self, path: Path) -> None:
        key = str(path.expanduser().resolve())
        for index in range(self.libraries_item.childCount()):
            item = self.libraries_item.child(index)
            if item.data(0, Qt.UserRole) == key:
                self.sidebar.setCurrentItem(item)
                return

    def _select_view(self, view: LibraryView) -> None:
        """Select `view` for the current root after populating the sidebar."""
        for index in range(self.views_item.childCount()):
            item = self.views_item.child(index)
            if item.data(0, Qt.UserRole + 1) == view.value:
                self.sidebar.setCurrentItem(item)
                return

    def _sidebar_changed(self) -> None:
        item = self.sidebar.currentItem()
        if item is None:
            return
        kind = item.data(0, Qt.UserRole + 1)
        if kind == "root":
            key = item.data(0, Qt.UserRole)
            view = LibraryView.ALL
        elif kind in {view.value for view in LibraryView}:
            key = item.data(0, Qt.UserRole)
            view = LibraryView(str(kind))
        else:
            return
        self.current_root = Path(str(key))
        self.current_view = view
        self.current_query = FileQuery(
            view=self.current_view,
            search=self.search.text(),
            order_by=self.current_query.order_by,
            descending=self.current_query.descending,
            secondary_order_by=self.current_query.secondary_order_by,
            tertiary_order_by=self.current_query.tertiary_order_by,
        )
        registry = self.registries[str(self.current_root)]
        self.model.set_registry(registry, self.current_query)
        if kind == "root":
            self._populate_views(registry)
        self._update_status()
        self._sync_watch_actions()
        self._sync_ads_library_action()

    def _search_changed(self) -> None:
        self.current_query = FileQuery(
            view=self.current_view,
            search=self.search.text(),
            order_by=self.current_query.order_by,
            descending=self.current_query.descending,
            secondary_order_by=self.current_query.secondary_order_by,
            tertiary_order_by=self.current_query.tertiary_order_by,
        )
        self.model.set_query(self.current_query)
        self._update_status()

    def _current_changed(self, current: QModelIndex, previous: QModelIndex) -> None:
        del previous
        selected = self.table.selectionModel().selectedRows()
        record = (
            self.model.record_at(current.row())
            if current.isValid() and len(selected) <= 1
            else None
        )
        cached_document = None
        if record is not None and self.current_root is not None:
            metadata = record.get("metadata")
            bibcode = (
                metadata.get("bibcode")
                if isinstance(metadata, dict)
                else record.get("bibcode")
            )
            if bibcode:
                try:
                    cached_document = self.registries[
                        str(self.current_root)
                    ].find_ads_document_by_bibcode(str(bibcode))
                except sqlite3.OperationalError as exc:
                    if (
                        "locked" not in str(exc).lower()
                        and "busy" not in str(exc).lower()
                    ):
                        raise
                    self.status.showMessage("Library is busy; try again in a moment.")
        self.detail.set_record(
            record,
            self.current_root or Path(),
            cached_document,
        )
        self._sync_ads_library_action()

    def _sync_detail_and_selection_actions(self) -> None:
        """Synchronize the detail pane and actions after selection or model changes."""
        self._current_changed(self.table.currentIndex(), QModelIndex())
        self._sync_export_action()

    def _sync_export_action(self) -> None:
        """Enable Export Selected Papers only while rows are selected."""
        if hasattr(self, "export_selected_action"):
            self.export_selected_action.setEnabled(
                self.current_root is not None
                and bool(self.table.selectionModel().selectedRows())
            )

    def _open_index(self, index: QModelIndex) -> None:
        if QApplication.keyboardModifiers() != Qt.NoModifier:
            return
        record = self.model.record_at(index.row())
        if record is None or self.current_root is None:
            return
        columns = self._columns_for_table_model()
        if columns is not None and index.column() < len(columns):
            column_key = columns[index.column()].key
            if column_key in {"bibcode", "arxiv", "filename"}:
                if self.config.link_activation == "double":
                    self._activate_link_cell(index, record, column_key)
                return
        self._open_pdf_record(record)

    def _merge_annotations(self) -> None:
        """Choose a canonical target and a source PDF, then open the merge dialog."""
        start = str(self.current_root) if self.current_root else ""
        target, _ = QFileDialog.getOpenFileName(
            self, "Choose the canonical (target) PDF", start, "PDF (*.pdf)"
        )
        if not target:
            return
        source, _ = QFileDialog.getOpenFileName(
            self,
            "Choose the PDF whose annotations to copy (source)",
            str(Path(target).parent),
            "PDF (*.pdf)",
        )
        if not source:
            return
        try:
            from .annotation_merge import AnnotationMergeDialog

            dialog = AnnotationMergeDialog(Path(target), Path(source), self)
        except (MissingDependencyError, AnnotationMergeError, RuntimeError) as exc:
            QMessageBox.warning(self, "Cannot merge annotations", str(exc))
            return
        dialog.exec()

    def _reset_link_activation(self) -> None:
        """Forget the last activated target when the table contents are replaced."""
        self._last_link_activation = None

    def _columns_for_table_model(self) -> tuple[Column, ...] | None:
        """Return column definitions matching the model installed on the table."""
        table_model = self.table.model()
        if table_model is None:
            return None
        model_columns = getattr(table_model, "columns", None)
        if model_columns is not None:
            return tuple(model_columns)
        base_columns = build_columns()
        if table_model.columnCount() == len(base_columns):
            return base_columns
        if table_model.columnCount() == len(self.columns):
            return self.columns
        return None

    def _table_clicked(self, index: QModelIndex) -> None:
        """Copy generated cells and, if configured, activate links on single click."""
        if not index.isValid():
            return
        record = self.model.record_at(index.row())
        if record is None or self.current_root is None:
            return
        columns = self._columns_for_table_model()
        if columns is None or index.column() >= len(columns):
            return
        column = columns[index.column()]
        if column.key.startswith("citation:"):
            display_text = index.data(Qt.DisplayRole)
            if display_text is not None and str(display_text) != "":
                self._copy_text(str(display_text))
                self.status.showMessage("Copied", 1500)
            return
        if self.config.link_activation != "single":
            return
        if QApplication.keyboardModifiers() != Qt.NoModifier:
            return
        self._activate_link_cell(index, record, column.key)

    def _activate_link_cell(
        self, index: QModelIndex, record: dict[str, Any], column_key: str
    ) -> None:
        """Open the ADS, arXiv, or filename target of a table cell."""
        if column_key == "filename":
            if self.current_root is None:
                return
            target = str(self.current_root / str(record.get("current_path") or ""))
        elif column_key in {"bibcode", "arxiv"}:
            columns = self._columns_for_table_model()
            column = next((c for c in columns or () if c.key == column_key), None)
            value = column.getter(record) if column is not None else None
            if not value:
                return
            target = ads_url(value) if column_key == "bibcode" else arxiv_url(value)
        else:
            return
        # Qt delivers the second click of a double-click after "doubleClicked";
        # ignore a repeat of the same target within the system interval.
        now = time.monotonic()
        previous = self._last_link_activation
        interval = QApplication.doubleClickInterval() / 1000
        if (
            previous is not None
            and previous[0] == target
            and now - previous[1] < interval
        ):
            return
        self._last_link_activation = (target, now)
        if column_key == "filename":
            self._open_filename(record)
        else:
            self._open_external_url(target)

    def _open_external_url(self, url: str) -> None:
        if open_url(url, self.config.web_browser_command):
            return
        QMessageBox.warning(self, "Cannot open link", f"Could not open:\n{url}")

    def _open_filename(self, record: dict[str, Any]) -> None:
        if self.current_root is None:
            return
        path = self.current_root / str(record.get("current_path") or "")
        if self.config.filename_click_action == "reveal":
            if reveal_in_file_manager(path):
                return
            QMessageBox.warning(
                self,
                "Cannot reveal file",
                f"File is missing or cannot be revealed:\n{path}",
            )
            return
        self._open_pdf_record(record)

    def _open_pdf_record(self, record: dict[str, Any]) -> None:
        if self.current_root is None:
            return
        path = self.current_root / str(record.get("current_path") or "")
        if not open_path(path, self.config.pdf_viewer_command):
            QMessageBox.warning(
                self, "Cannot open PDF", f"File is missing or cannot be opened:\n{path}"
            )

    def _open_current(self) -> None:
        """Open the current table selection using the standard PDF action."""
        focused = QApplication.focusWidget()
        if focused is not None and self.detail.isAncestorOf(focused):
            return
        index = self.table.currentIndex()
        record = self.model.record_at(index.row()) if index.isValid() else None
        if record is not None:
            self._open_pdf_record(record)

    def _clear_search(self) -> None:
        """Clear search text and return keyboard navigation to the table."""
        self.search.clear()
        self.table.setFocus()

    def _sidebar_menu(self, position: Any) -> None:
        item = self.sidebar.itemAt(position)
        if item is None or item.data(0, Qt.UserRole + 1) != "root":
            return
        root = Path(str(item.data(0, Qt.UserRole)))
        menu = QMenu(self)
        watch_action = menu.addAction("Watch Current Library")
        watch_action.setCheckable(True)
        watch_action.setChecked(self.watch_controller.is_watching(root))
        watch_action.triggered.connect(
            lambda checked: self._set_root_watching(root, checked)
        )
        watch_on_start = menu.addAction("Autowatch at Start")
        watch_on_start.setCheckable(True)
        watch_on_start.setChecked(self.root_entries[str(root)].watch_on_start)
        watch_on_start.triggered.connect(
            lambda checked: self._set_root_watch_policy(root, "watch_on_start", checked)
        )
        auto_process = menu.addAction("Automatically Identify && Rename")
        auto_process.setCheckable(True)
        auto_process.setChecked(self.root_entries[str(root)].auto_process_new_pdfs)
        auto_process.triggered.connect(
            lambda checked: self._set_root_watch_policy(
                root, "auto_process_new_pdfs", checked
            )
        )
        recursive_action = menu.addAction("Include Subdirectories")
        recursive_action.setCheckable(True)
        recursive_action.setChecked(self.root_entries[str(root)].recursive)
        recursive_action.setEnabled(not self.watch_controller.is_watching(root))
        recursive_action.triggered.connect(
            lambda checked: self._set_root_recursive(root, checked)
        )
        menu.addSeparator()
        refresh_action = menu.addAction(
            "Refresh", lambda: self._refresh_local_files(root)
        )
        refresh_action.setEnabled(
            self.job is None and self._pending_watch_dialog is None
        )
        menu.addAction("Remove from list", lambda: self._remove_root(root))
        menu.addAction(
            "Reveal in File Manager",
            lambda: reveal_in_file_manager(root),
        )
        menu.exec(self.sidebar.viewport().mapToGlobal(position))

    def _set_root_watching(self, root: Path, checked: bool) -> None:
        previous = self.current_root
        self.current_root = root.resolve()
        try:
            self._toggle_current_watch(checked)
        finally:
            self.current_root = previous
            self._sync_watch_actions()

    def _set_root_watch_policy(self, root: Path, field: str, checked: bool) -> None:
        previous = self.current_root
        self.current_root = root.resolve()
        try:
            self._set_current_watch_policy(field, checked)
        finally:
            self.current_root = previous
            self._sync_watch_actions()

    def _table_menu(self, position: Any) -> None:
        index = self.table.indexAt(position)
        record = self.model.record_at(index.row()) if index.isValid() else None
        if record is None or self.current_root is None:
            return
        selection = self.table.selectionModel()
        if not selection.isRowSelected(index.row(), QModelIndex()):
            selection.clearSelection()
            selection.select(
                index,
                QItemSelectionModel.SelectionFlag.Select
                | QItemSelectionModel.SelectionFlag.Rows,
            )
        records = self._selected_records() or (deepcopy(record),)
        count = len(records)
        root = self.current_root
        paths = [root / str(item.get("current_path") or "") for item in records]
        menu = QMenu(self)
        menu.addAction(
            "Open" if count == 1 else f"Open {count} PDFs",
            lambda: [self._open_pdf_record(item) for item in records],
        )
        menu.addAction(
            "Reveal in File Manager" if count == 1 else f"Reveal {count} Files",
            lambda: self._reveal_paths(paths),
        )
        for label, values in (
            ("Bibcode", [self._record_value(item, "bibcode") for item in records]),
            ("DOI", [self._record_value(item, "doi") for item in records]),
            ("Path", [str(path) for path in paths]),
        ):
            present = [value for value in values if value]
            plural = "DOIs" if label == "DOI" else f"{label}s"
            text = (
                f"Copy {label}"
                if count == 1
                else f"Copy {len(present)} {plural}"
                if present
                else f"Copy {plural}"
            )
            action = menu.addAction(
                text, lambda present=present: self._copy_text("\n".join(present))
            )
            action.setEnabled(bool(present))
            if count > 1 and not present:
                action.setToolTip(f"None of the selected papers has a {label}.")
            elif count > 1 and len(present) < count:
                action.setToolTip(
                    f"{count - len(present)} selected papers have no {label}."
                )
        menu.addSeparator()
        if count == 1:
            menu.addAction(self.refresh_selected_ads_action)
            menu.addAction(self.update_selected_ads_action)
        else:
            refreshable = [
                item for item in records if self._record_value(item, "bibcode")
            ]
            refresh = menu.addAction(
                f"Refresh {len(refreshable)} Selected ADS Records",
                lambda: self._refresh_records_ads(refreshable),
            )
            refresh.setEnabled(
                bool(refreshable) and self.refresh_all_ads_action.isEnabled()
            )
        menu.addSeparator()
        add_to_ads_library_action = menu.addAction(
            "Add to ADS Library…", self._add_to_ads_library
        )
        add_to_ads_library_action.setEnabled(self._can_add_to_ads_library())
        menu.addAction(
            "Export Selected Papers…" if count > 1 else "Export Paper as List…",
            lambda: self._export_selected_papers(records),
        )
        menu.addSeparator()
        menu.addAction(self.appearance_action)
        menu.exec(self.table.viewport().mapToGlobal(position))

    def _show_header_context_menu(self, position: Any) -> None:
        """Show column visibility and layout actions for the table header."""
        menu = self._create_header_context_menu()
        menu.exec(self.table.header().mapToGlobal(position))

    def _create_header_context_menu(self) -> QMenu:
        """Build the context menu for the table's column header."""
        menu = QMenu(self)
        menu.addAction(self.appearance_action)
        menu.addSeparator()
        header = self.table.header()
        visible_count = sum(
            not self.table.isColumnHidden(header.logicalIndex(visual))
            for visual in range(header.count())
        )
        for visual in range(header.count()):
            logical = header.logicalIndex(visual)
            column = self.columns[logical]
            label = column.header or "Resolution marker"
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(not self.table.isColumnHidden(logical))
            action.setEnabled(action.isChecked() is False or visible_count > 1)
            action.triggered.connect(
                lambda checked, column_key=column.key: self._set_column_visibility(
                    column_key, checked
                )
            )

        menu.addSeparator()
        show_all = menu.addAction("Show all columns")
        show_all.triggered.connect(self._show_all_columns)
        resize = menu.addAction("Resize columns to contents")
        resize.triggered.connect(
            lambda: header.resizeSections(QHeaderView.ResizeToContents)
        )
        restore = menu.addAction("Restore default column order")
        restore.triggered.connect(self._restore_default_column_order)
        return menu

    def _set_column_visibility(self, key: str, visible: bool) -> None:
        """Set one column's visibility while keeping one column visible."""
        logical = next(
            (index for index, column in enumerate(self.columns) if column.key == key),
            None,
        )
        if logical is None:
            return
        if not visible:
            visible_count = sum(
                not self.table.isColumnHidden(index)
                for index in range(len(self.columns))
            )
            if visible_count <= 1:
                return
        self.table.setColumnHidden(logical, not visible)
        self.config.hidden_table_columns = [
            column.key
            for index, column in enumerate(self.columns)
            if self.table.isColumnHidden(index)
        ]

    def _show_all_columns(self) -> None:
        """Make every table column visible and update persisted preferences."""
        for index in range(len(self.columns)):
            self.table.setColumnHidden(index, False)
        self.config.hidden_table_columns = []

    def _restore_default_column_order(self) -> None:
        """Restore the declaration order of table columns."""
        self.config.table_column_order = [column.key for column in self.columns]
        self._apply_table_layout()

    def _copy_text(self, text: str) -> None:
        QApplication.clipboard().setText(text)

    def _record_value(self, record: dict[str, Any], key: str) -> str:
        """Return one scalar field from a record or its metadata."""
        value = record.get(key)
        if value in (None, "", []):
            metadata = record.get("metadata")
            value = metadata.get(key) if isinstance(metadata, dict) else None
        return self._scalar_metadata_value(value) if value is not None else ""

    def _reveal_paths(self, paths: Sequence[Path]) -> None:
        """Reveal each distinct folder once, selecting its first file."""
        seen: set[Path] = set()
        for path in paths:
            if path.parent not in seen:
                seen.add(path.parent)
                reveal_in_file_manager(path)

    def _export_selected_papers(
        self, records: Sequence[dict[str, Any]] | None = None
    ) -> None:
        """Export papers with a CV template from their stored metadata."""
        selected = list(records if records is not None else self._selected_records())
        if not selected:
            self.status.showMessage("Select papers to export first.", 5000)
            return
        CVDialog(
            self,
            records=selected,
            browser_command=self.config.web_browser_command,
        ).exec()

    def _refresh_records_ads(self, records: Sequence[dict[str, Any]]) -> None:
        """Refresh several records from ADS, one CLI run per record, in order."""
        commands = [
            (
                "--bibcode",
                self._record_value(record, "bibcode"),
                "--relative-path",
                str(record["relative_path"]),
                "--citation-metrics",
            )
            for record in records
            if self._record_value(record, "bibcode")
        ]
        if not commands:
            return
        self._start_ads_refresh(
            *commands[0],
            selection_id=int(records[0]["location_id"]),
            follow_ups=tuple(commands[1:]),
        )

    def _load_next_page(self) -> None:
        """Load another page without materializing the whole registry."""
        self.model.fetchMore()
        self._update_status()

    def _remove_missing_records(self) -> None:
        """Remove stale registry entries after explicit user confirmation."""
        if self.current_root is None:
            return
        registry = self.registries[str(self.current_root)]
        self.status.showMessage("Checking registry paths for missing files…")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            missing_ids = [
                int(record["location_id"])
                for record in registry.iter_files(include_missing=True)
                if not (self.current_root / str(record["current_path"])).is_file()
            ]
        finally:
            QApplication.restoreOverrideCursor()
        if not missing_ids:
            QMessageBox.information(self, "Registry cleanup", "No missing files found.")
            self._update_status()
            return
        answer = QMessageBox.question(
            self,
            "Remove missing registry records",
            (
                f"Remove {len(missing_ids)} record(s) whose files are absent from "
                "this library root? This does not delete any files."
            ),
        )
        if answer != QMessageBox.Yes:
            self._update_status()
            return
        removed = registry.delete_files_by_ids(missing_ids)
        self.log.appendPlainText(f"Removed {removed} missing registry record(s).")
        self.refresh()

    def _refresh_local_files(self, root: Path | None = None) -> None:
        """Queue a filesystem scan after any watcher write has finished."""
        root = root or self.current_root
        if (
            root is None
            or self.job is not None
            or self._pending_watch_dialog is not None
            or self._pending_ads_refresh is not None
            or self._local_refresh_context is not None
        ):
            return
        root = root.expanduser().resolve()
        if str(root) not in self.registries:
            return
        self._local_refresh_context = _LibraryViewContext(
            root=root,
            selected=self._selected_records() if root == self.current_root else (),
            current=deepcopy(self._selected_record())
            if root == self.current_root
            else None,
            query=self.current_query,
            loaded=self.model.rowCount(),
            scroll=self.table.verticalScrollBar().value(),
        )
        self._request_foreground_dialog("local_refresh", root)
        self._sync_refresh_actions()

    def _launch_local_refresh(self, root: Path) -> None:
        """Start a thread-owned scan of the originally requested root."""
        if self._close_requested or str(root) not in self.root_entries:
            self._local_refresh_context = None
            self._resume_foreground_watch()
            return
        self._local_refresh_result = None
        job = LibraryRefreshJob(root, self.root_entries[str(root)].recursive, self)
        job.refreshed.connect(self._capture_local_refresh)
        self._watch_job(job)
        self.status.showMessage(f"Refreshing local files: {root}")
        job.start()

    def _capture_local_refresh(self, result: LibraryRefresh) -> None:
        """Retain the scan summary until the normal job completion reload."""
        self._local_refresh_result = result

    def _restore_library_selection(self, context: _LibraryViewContext) -> None:
        """Restore selected locations, or their unique moved attachments."""
        if self.current_root != context.root or self.current_query != context.query:
            return
        records = self.registries[str(context.root)].query_files(
            **self.current_query.to_filters()
        )
        by_id = {record["location_id"]: record for record in records}
        by_attachment: dict[int, list[int]] = {}
        for record in records:
            by_attachment.setdefault(record["attachment_id"], []).append(
                record["location_id"]
            )
        targets: dict[int, int] = {}
        for record in (
            *context.selected,
            *((context.current,) if context.current else ()),
        ):
            old_id = record["location_id"]
            if old_id in by_id:
                targets[old_id] = old_id
            else:
                matches = by_attachment.get(record["attachment_id"], [])
                if len(matches) == 1:
                    targets[old_id] = matches[0]
        needed = set(targets.values())
        while self.model.canFetchMore():
            loaded = {
                self.model.record_at(row)["location_id"]
                for row in range(self.model.rowCount())
            }
            if self.model.rowCount() >= context.loaded and needed <= loaded:
                break
            previous = self.model.rowCount()
            self.model.fetchMore()
            if self.model.rowCount() == previous:
                break
        selection = self.table.selectionModel()
        selection.clearSelection()
        selected_ids = {
            targets[record["location_id"]]
            for record in context.selected
            if record["location_id"] in targets
        }
        current_id = (
            targets.get(context.current["location_id"]) if context.current else None
        )
        current_index = QModelIndex()
        for row in range(self.model.rowCount()):
            location_id = self.model.record_at(row)["location_id"]
            index = self.model.index(row, 0)
            if location_id in selected_ids:
                selection.select(
                    index, QItemSelectionModel.Select | QItemSelectionModel.Rows
                )
            if location_id == current_id:
                current_index = index
        selection.setCurrentIndex(current_index, QItemSelectionModel.NoUpdate)
        self._sync_detail_and_selection_actions()
        self.table.verticalScrollBar().setValue(context.scroll)

    def _edit_appearance(self) -> None:
        """Configure table and detail-pane appearance preferences."""
        original_config = deepcopy(self.config)
        dialog = AppearanceDialog(
            self.config,
            self.columns,
            self.detail.available_rows(),
            self,
        )
        dialog.settings_changed.connect(lambda: self._apply_appearance_dialog(dialog))
        result = dialog.exec()
        if result == QDialog.DialogCode.Accepted:
            self._apply_appearance_dialog(dialog)
            return
        self.config = original_config
        self._apply_appearance_config()

    def _edit_citation_styles(self) -> None:
        """Edit global citation styles and apply accepted definitions live."""
        selected_rows = self.table.selectionModel().selectedRows()
        preview_record = (
            self.model.record_at(selected_rows[0].row())
            if len(selected_rows) == 1
            else None
        )
        dialog = CitationStylesDialog(
            self.config.citation_styles,
            preview_record=preview_record,
            parent=self,
        )
        dialog.saved.connect(self._apply_citation_styles)
        dialog.exec()

    @staticmethod
    def _merge_citation_order(
        order: Sequence[str],
        deleted_keys: set[str],
        citation_keys: Sequence[str],
        added_keys: Sequence[str],
    ) -> list[str]:
        """Reorder active citation keys and place new ones after Bibcode."""
        citations = tuple(dict.fromkeys(citation_keys))
        citation_set = set(citations)
        added_set = set(added_keys)
        retained = [
            key for key in order if key not in deleted_keys and key not in added_set
        ]
        present = {key for key in retained if key in citation_set}
        citation_slots = [
            index for index, key in enumerate(retained) if key in citation_set
        ]
        existing = [key for key in citations if key in present]
        for index, key in zip(citation_slots, existing):
            retained[index] = key

        missing = [key for key in citations if key not in present]
        if not missing or "bibcode" not in retained:
            return retained
        insertion = retained.index("bibcode") + 1
        return [*retained[:insertion], *missing, *retained[insertion:]]

    def _apply_citation_styles(self, styles: Sequence[CitationStyle]) -> None:
        """Apply accepted citation styles and update all dependent UI state."""
        prior_widths = {
            column.key: self.table.columnWidth(index)
            for index, column in enumerate(self.columns)
        }
        current_index = self.table.currentIndex()
        selected_row = current_index.row() if current_index.isValid() else None
        selected_record = (
            self.model.record_at(selected_row) if selected_row is not None else None
        )
        selected_location_id = (
            selected_record.get("location_id") if selected_record is not None else None
        )

        old_keys = {
            citation_preference_key(style.id) for style in self.config.citation_styles
        }
        normalized_styles = tuple(styles)
        new_keys = [citation_preference_key(style.id) for style in normalized_styles]
        new_key_set = set(new_keys)
        deleted_keys = old_keys - new_key_set
        added_keys = [key for key in new_keys if key not in old_keys]
        added_key_set = set(added_keys)

        for preferences in (
            self.config.column_widths,
            self.config.column_fonts,
            self.config.detail_fonts,
        ):
            for key in deleted_keys:
                preferences.pop(key, None)
        self.config.table_column_order = self._merge_citation_order(
            self.config.table_column_order,
            deleted_keys,
            new_keys,
            added_keys,
        )
        self.config.detail_row_order = self._merge_citation_order(
            self.config.detail_row_order,
            deleted_keys,
            new_keys,
            added_keys,
        )
        self.config.hidden_table_columns = [
            key
            for key in self.config.hidden_table_columns
            if key not in deleted_keys and key not in added_key_set
        ]
        self.config.detail_hidden_rows = [
            key
            for key in self.config.detail_hidden_rows
            if key not in deleted_keys and key not in added_key_set
        ]
        self.config.citation_styles = list(normalized_styles)
        save_config(self.config)

        self.columns = build_columns(self.config.citation_styles)
        self.model.set_columns(self.columns)
        self.detail.set_row_order(self.config.detail_row_order)
        self.detail.set_hidden_rows(self.config.detail_hidden_rows)
        self.detail.set_citation_styles(self.config.citation_styles)
        self._rebuild_table_delegates(prior_widths)
        self._apply_table_appearance()
        if selected_record is not None:
            restored_row = selected_row
            if selected_location_id is not None:
                restored_row = next(
                    (
                        row
                        for row in range(self.model.rowCount())
                        if (
                            self.model.record_at(row) is not None
                            and self.model.record_at(row).get("location_id")
                            == selected_location_id
                        )
                    ),
                    None,
                )
            if restored_row is not None and restored_row < self.model.rowCount():
                restored_index = self.model.index(restored_row, 0)
                self.table.setCurrentIndex(restored_index)
                self._current_changed(restored_index, QModelIndex())

    def _apply_appearance_dialog(self, dialog: AppearanceDialog) -> None:
        """Apply the current Appearance dialog values to the running window."""
        self.config.column_fonts = {
            key: dict(value) for key, value in dialog.column_fonts.items()
        }
        self.config.detail_fonts = {
            key: dict(value) for key, value in dialog.detail_fonts.items()
        }
        self.config.timestamp_format = dialog.timestamp_format_value
        self.config.table_column_order = list(dialog.table_column_order)
        self.config.hidden_table_columns = list(dialog.hidden_table_columns)
        self.config.expand_abstracts = dialog.expand_abstracts
        self.config.table_row_height = dialog.table_row_height
        self.config.detail_row_spacing = dialog.detail_row_spacing
        self.config.detail_label_bold = dialog.detail_label_bold
        self.config.detail_compact_rows = dialog.detail_compact_rows
        self.config.show_edit_buttons = dialog.show_edit_buttons
        self.config.show_lock_buttons = dialog.show_lock_buttons
        self.config.filename_click_action = dialog.filename_click_action
        self.config.link_activation = dialog.link_activation
        self.config.pdf_viewer_command = dialog.pdf_viewer_command
        self.config.web_browser_command = dialog.web_browser_command
        self.config.detail_row_order = list(dialog.detail_row_order)
        self.config.detail_hidden_rows = list(dialog.detail_hidden_rows)
        self._apply_appearance_config()

    def _apply_appearance_config(self) -> None:
        """Apply all table and detail appearance settings to the live UI."""
        self.detail.set_row_order(self.config.detail_row_order)
        self.detail.set_hidden_rows(self.config.detail_hidden_rows)
        self.detail.set_compact_rows(self.config.detail_compact_rows)
        self.detail.set_label_width(self.config.detail_label_width)
        self.detail.set_link_activation(self.config.link_activation)
        self._apply_table_appearance()
        self._current_changed(self.table.currentIndex(), QModelIndex())

    def _edit_sort_priorities(self) -> None:
        """Configure persisted primary, secondary, and tertiary sort keys."""
        choices = [
            "(none)",
            *(column.key for column in self.columns if column.sortable is True),
        ]
        dialog = QDialog(self)
        dialog.setWindowTitle("Sort Priorities")
        layout = QVBoxLayout(dialog)
        selectors: list[QComboBox] = []
        for label, current in (
            ("Primary sort", self.current_query.order_by),
            ("Secondary sort", self.current_query.secondary_order_by),
            ("Tertiary sort", self.current_query.tertiary_order_by),
        ):
            selector = QComboBox(dialog)
            selector.addItems(choices)
            selector.setCurrentText(current if current in choices else "(none)")
            layout.addWidget(QLabel(label, dialog))
            layout.addWidget(selector)
            selectors.append(selector)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dialog
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected = [
            None if selector.currentText() == "(none)" else selector.currentText()
            for selector in selectors
        ]
        primary = selected[0] or "relative_path"
        secondary = selected[1] if selected[1] != primary else None
        tertiary = selected[2] if selected[2] not in {primary, secondary} else None
        self.current_query = FileQuery(
            view=self.current_view,
            search=self.search.text(),
            order_by=primary,
            descending=self.current_query.descending,
            secondary_order_by=secondary,
            tertiary_order_by=tertiary,
        )
        self.model.set_query(self.current_query)
        self._update_status()

    def _apply_table_appearance(self) -> None:
        """Apply persisted typography and timestamp display to the table."""
        fonts: dict[str, QFont] = {}
        colors: dict[str, QColor] = {}
        for key, settings in self.config.column_fonts.items():
            family = settings.get("family")
            point_size = settings.get("point_size")
            if not isinstance(family, str) or not isinstance(point_size, int):
                continue
            font = QFont(family)
            font.setPointSize(point_size)
            fonts[key] = font
            color = QColor(str(settings.get("color", "")))
            if color.isValid():
                colors[key] = color
        self.model.set_column_fonts(fonts, colors)
        detail_fonts: dict[str, QFont] = {}
        detail_colors: dict[str, QColor] = {}
        for key, settings in self.config.detail_fonts.items():
            family = settings.get("family")
            point_size = settings.get("point_size")
            if not isinstance(family, str) or not isinstance(point_size, int):
                continue
            font = QFont(family)
            font.setPointSize(point_size)
            detail_fonts[key] = font
            color = QColor(str(settings.get("color", "")))
            if color.isValid():
                detail_colors[key] = color
        self.model.set_timestamp_format(self.config.timestamp_format)
        self.detail.set_timestamp_format(self.config.timestamp_format)
        self.detail.set_content_font(fonts.get("detail"), colors.get("detail"))
        self.detail.set_row_styles(detail_fonts, detail_colors)
        self.detail.set_expand_abstracts(self.config.expand_abstracts)
        self.detail.set_row_spacing(self.config.detail_row_spacing)
        self.detail.set_label_bold(self.config.detail_label_bold)
        self.detail.set_action_buttons_visible(
            show_edit=self.config.show_edit_buttons,
            show_lock=self.config.show_lock_buttons,
        )
        self.detail.set_application_commands(
            pdf_viewer_command=self.config.pdf_viewer_command,
            web_browser_command=self.config.web_browser_command,
        )
        self._set_table_row_height(self.config.table_row_height)
        self._apply_table_layout()

    def _set_table_row_height(self, height: int | None) -> None:
        """Apply table-row height to every active item delegate."""
        for delegate in self._table_row_delegates:
            delegate.set_row_height(height)
        self.table.doItemsLayout()
        self.table.viewport().update()

    def _rebuild_table_delegates(
        self,
        preserved_widths: dict[str, int] | None = None,
    ) -> None:
        """Recreate delegates and widths for the active runtime columns."""
        self._table_row_delegates = []
        default_delegate = RowHeightDelegate(self.table)
        self.table.setItemDelegate(default_delegate)
        self._table_row_delegates.append(default_delegate)
        widths = preserved_widths or {}
        for index, column in enumerate(self.columns):
            width = widths.get(
                column.key,
                self.config.column_widths.get(column.key, column.width),
            )
            self.table.setColumnWidth(index, width)
            self.table.setItemDelegateForColumn(index, default_delegate)
            delegate: RowHeightDelegate | None = None
            if column.key == "author":
                delegate = FirstAuthorDelegate(self.table)
            elif column.key == "status":
                delegate = ResolutionDelegate(self.table)
            elif column.key in {"bibcode", "arxiv"}:
                delegate = LinkDelegate(
                    "ADS" if column.key == "bibcode" else "arXiv",
                    self.table,
                )
            if delegate is not None:
                self.table.setItemDelegateForColumn(index, delegate)
                self._table_row_delegates.append(delegate)

    def _apply_table_layout(self) -> None:
        """Apply persisted column order and visibility to the library table."""
        positions = {column.key: index for index, column in enumerate(self.columns)}
        order = list(
            dict.fromkeys(
                [
                    *(
                        key
                        for key in self.config.table_column_order
                        if key in positions
                    ),
                    *(column.key for column in self.columns),
                ]
            )
        )
        header = self.table.header()
        for visual_index, key in enumerate(order):
            logical_index = positions[key]
            header.moveSection(header.visualIndex(logical_index), visual_index)
            self.table.setColumnHidden(
                logical_index, key in self.config.hidden_table_columns
            )

    def _add_root(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Add Library Root")
        if not selected:
            return
        path = Path(selected)
        recursive = False
        if str(path.expanduser().resolve()) not in self.registries:
            choice = self._ask_include_subfolders(path)
            if choice is None:
                return
            recursive = choice
        self._register_root(path, recursive=recursive)
        self._select_root(path)
        if self.model.total:
            self._guide_identification()

    def _ask_include_subfolders(self, path: Path) -> bool | None:
        """Ask whether a new library includes subfolders.

        Returns
        -------
        bool or `None`
            `True` to include subfolders, `False` for this folder only, or
            `None` when the user cancels. A folder without visible subfolders
            returns `False` without asking.
        """
        try:
            has_subfolders = any(
                entry.is_dir() and not entry.name.startswith(".")
                for entry in path.iterdir()
            )
        except OSError:
            has_subfolders = False
        if not has_subfolders:
            return False
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Include Subfolders?")
        box.setText(f"Include PDFs in the subfolders of {path.name or path}?")
        box.setInformativeText(
            "You can change this later with Include Subdirectories on the library."
        )
        include = box.addButton("Include Subfolders", QMessageBox.YesRole)
        only = box.addButton("This Folder Only", QMessageBox.NoRole)
        box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(only)
        box.exec()
        clicked = box.clickedButton()
        if clicked is include:
            return True
        if clicked is only:
            return False
        return None

    def _remove_root(self, root: Path | None = None) -> None:
        root = root or self.current_root
        if root is None:
            return
        root = root.expanduser().resolve()
        key = str(root)
        if any(
            context is not None and context.root == root
            for context in (self._local_refresh_context, self._guided_identify_context)
        ):
            self.status.showMessage(
                "Wait for the library operation to finish before removing this library."
            )
            return
        if (
            self._pending_ads_refresh is not None
            and self._pending_ads_refresh[0] == root
        ):
            self._pending_ads_refresh = None
            self._pending_watch_dialog = None
            self.watch_controller.resume_writes(root)
        if self.watch_controller.is_watching(
            root
        ) and not self.watch_controller.stop_root(root):
            self.notice_bar.enqueue(
                Notice(
                    f"remove-watch:{root}",
                    f"Could not stop watching {root}; the library was not removed.",
                    severity="error",
                    root=root,
                )
            )
            return
        registry = self.registries.pop(key, None)
        if registry is not None:
            registry.close()
        self.root_entries.pop(key, None)
        self.notice_bar.clear_root(root)
        for index in range(self.libraries_item.childCount()):
            item = self.libraries_item.child(index)
            if item.data(0, Qt.UserRole) == key:
                self.libraries_item.takeChild(index)
                break
        if self.current_root == root:
            self.current_root = None
            self.model.set_registry(None)
            self.views_item.takeChildren()
        self._sync_config_roots()
        self._sync_watch_actions()
        self._sync_ads_library_action()

    def refresh(self, root: Path | None = None) -> None:
        """Reload the current registry and sidebar counts."""
        root = root or self.current_root
        if root is None:
            return
        key = str(root)
        self.registries[key].close()
        self.registries[key] = PdfRegistry(root)
        entry = self.root_entries[key]
        for index in range(self.libraries_item.childCount()):
            item = self.libraries_item.child(index)
            if item.data(0, Qt.UserRole) == key:
                count = self._safe_count(self.registries[key])
                marker = "● " if self.watch_controller.is_watching(root) else ""
                item.setText(
                    0,
                    f"{marker}{entry.label or root.name or key} ({count if count is not None else '?'})",
                )
                break
        if self.current_root == root:
            self.model.set_registry(self.registries[key], self.current_query)
            self._populate_views(self.registries[key])
            self._update_status()

    def _update_status(self) -> None:
        self._sync_welcome()
        explains_ads_group = self.current_root is not None and self.current_view in {
            LibraryView.RESOLVED,
            LibraryView.UNRESOLVED,
        }
        self.lookup_guidance.setVisible(explains_ads_group)
        self.view_explanation.setVisible(explains_ads_group)
        self.check_papers_button.setVisible(self.current_view is LibraryView.UNRESOLVED)
        if explains_ads_group:
            self.view_explanation.setText(
                "ADS linked: these papers have saved ADS records. Changed PDFs need identification again."
                if self.current_view is LibraryView.RESOLVED
                else "These papers have no saved ADS link. A bibcode or usable non-ADS metadata may already be present. Select Identify papers… to check them."
            )
        if self.current_root is None:
            self.status.setToolTip("")
            self.status.showMessage("No library selected")
            return
        total = self.model.total
        loaded = self.model.rowCount()
        registry = self.registries[str(self.current_root)]
        filters = self.current_query.to_filters()
        resolved_filters = {**filters, "resolved": True}
        unresolved_filters = {**filters, "resolved": False}
        resolved = self._safe_count(registry, **resolved_filters)
        unresolved = self._safe_count(registry, **unresolved_filters)
        if resolved is None or unresolved is None:
            return
        self.status.showMessage(
            f"{loaded} loaded of {total} · {resolved} ADS linked · "
            f"{unresolved} need ADS lookup"
        )
        self.load_more_action.setEnabled(self.model.canFetchMore())

        self.status.setToolTip(LibraryView.RESOLVED.description)

    def _guide_identification(self) -> None:
        """Explain identification and start it with one confirmation."""
        root = self.current_root
        if (
            root is None
            or self.job is not None
            or self._pending_watch_dialog is not None
            or self._pending_ads_refresh is not None
            or self._close_requested
        ):
            return
        settings = self._effective_identify_settings(root)
        providers = " → ".join(DOI_PROVIDER_LABELS[p] for p in settings.providers)
        skipped_folder = (
            " The configured unresolved folder is skipped."
            if settings.skip_unresolved_directory
            else ""
        )
        prompt = QMessageBox(self)
        prompt.setWindowTitle("Identify papers")
        prompt.setTextFormat(Qt.PlainText)
        prompt.setIcon(QMessageBox.Information)
        prompt.setText(f"Find publication details for PDFs in {root.name}?")
        cache_step = (
            "1. Link stored bibcodes from the cache or ADS, including tracked subfolders.\n"
            if DoiProvider.ADS in settings.providers
            else "1. Use the selected metadata providers. ADS lookup is disabled.\n"
        )
        scope = (
            "this folder and its subfolders"
            if self.root_entries[str(root)].recursive
            else "this folder only"
        )
        prompt.setInformativeText(
            cache_step
            + f"2. Identify papers that still need metadata in {scope}.{skipped_folder}\n\n"
            f"DOI provider order: {providers}. Search and category filters do not limit the check.\n\n"
            "Locked fields and history stay intact. Unlocked publication fields can be updated. PDFs keep their names and locations.\n\n"
            "Select OK to start. If ADS needs a token, setup opens first."
        )
        prompt.setToolTip(str(root))
        prompt.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
        prompt.setDefaultButton(QMessageBox.Ok)
        prompt.setEscapeButton(QMessageBox.Cancel)
        advanced = prompt.addButton("Advanced options…", QMessageBox.ActionRole)
        prompt.exec()
        if prompt.clickedButton() is advanced:
            self._identify()
            return
        if prompt.standardButton(prompt.clickedButton()) != QMessageBox.Ok:
            return
        if DoiProvider.ADS in settings.providers:
            try:
                Config.get_token(prompt=False)
            except AuthenticationError:
                registry = self.registries[str(root)]
                records = list(registry.iter_files())
                codes = {
                    self._scalar_metadata_value(
                        record.get("bibcode") or record["metadata"].get("bibcode")
                    )
                    for record in records
                    if record.get("status") != "resolved"
                    and stored_bibcode_matches_file(record)
                }
                cached = registry.find_ads_documents_by_bibcodes(list(codes))
                needs_token = (
                    settings.providers == (DoiProvider.ADS,)
                    or any(code.lower() not in cached for code in codes)
                    or any(
                        not stored_bibcode_matches_file(record)
                        and (verification_stale(record) or not metadata_ready(record))
                        for record in records
                    )
                )
                if needs_token and TokenDialog(self).exec() != QDialog.Accepted:
                    return
            except OSError as exc:
                QMessageBox.warning(self, "Could not read ADS token", str(exc))
                return
        self._guided_identify_context = _LibraryViewContext(
            root=root,
            selected=self._selected_records(),
            current=deepcopy(self._selected_record()),
            query=self.current_query,
            loaded=self.model.rowCount(),
            scroll=self.table.verticalScrollBar().value(),
        )
        flags = [
            "--settings-scope",
            self.config.identify_scopes.get(str(root), "global"),
            "--recursive"
            if self.root_entries[str(root)].recursive
            else "--no-recursive",
        ]
        for strategy in settings.strategies:
            flags.extend(("--strategy", strategy.value))
        for provider in settings.providers:
            flags.extend(("--provider", provider.value))
        if settings.skip_unresolved_directory and settings.output_unresolved:
            flags.extend(("--output-unresolved", settings.output_unresolved))
        else:
            flags.append("--clear-output-unresolved")
        flags.extend(
            (
                "--skip-unresolved-directory"
                if settings.skip_unresolved_directory
                else "--include-unresolved-directory",
                "--allow-ads",
            )
        )
        commands = []
        if DoiProvider.ADS in settings.providers:
            commands.append(
                cli_argv(
                    "pdf",
                    "refresh-ads",
                    str(root),
                    "--all",
                    "--no-rename",
                    "--pending-only",
                )
            )
        commands.append(cli_argv("pdf", "identify", str(root), *flags))
        self._guided_identify_commands = commands
        self._request_foreground_dialog("guided_identify", root)
        self._sync_refresh_actions()

    def _launch_guided_identification(self, root: Path) -> None:
        """Run the approved lookup steps after watcher writes pause."""
        if self._close_requested or str(root) not in self.registries:
            self._guided_identify_context = None
            self._guided_identify_commands.clear()
            self._resume_foreground_watch()
            return
        commands = self._guided_identify_commands
        self._guided_identify_commands = []
        job = CommandJob(
            commands[0],
            follow_up_argv=commands[1:],
            parent=self,
            working_directory=root,
        )
        self._refresh_selection_id = None
        self._watch_job(job)
        self._sync_refresh_actions()
        self.status.showMessage(f"Identifying papers: {root.name}")
        job.start()

    def _identify(self) -> None:
        if self.current_root is None:
            return
        self._refresh_selection_id = None
        root = self.current_root.resolve()
        self._request_foreground_dialog("identify", root)

    def _request_foreground_dialog(
        self,
        kind: str,
        root: Path,
        *,
        ads_refresh: (
            tuple[Path, tuple[str, ...], int | None, str | None] | None
        ) = None,
    ) -> None:
        if (
            self.job is not None
            or self._pending_watch_dialog is not None
            or self._pending_ads_refresh is not None
        ):
            return
        if kind == "ads_refresh":
            if ads_refresh is None:
                return
            self._pending_ads_refresh = ads_refresh
        if self.watch_controller.is_watching(root):
            self._pending_watch_dialog = (kind, root)
            self._sync_refresh_actions()
            self.watch_controller.pause_writes(root)
            return
        if kind == "ads_refresh":
            self._start_pending_ads_refresh(root)
        elif kind == "local_refresh":
            self._launch_local_refresh(root)
        elif kind == "identify":
            self._open_identify_dialog(root)
        elif kind == "guided_identify":
            self._launch_guided_identification(root)
        else:
            self._open_rename_dialog(root)

    def _open_identify_dialog(self, root: Path) -> None:
        registry = self.registries[str(root)]
        state = CentralPdfRegistryState(registry)
        persistence = _SettingsPersistenceCoordinator[IdentifySettings](
            scopes=self.config.identify_scopes,
            root_key=str(root),
            load_global=Config.load_identify_settings,
            save_global=Config.save_identify_settings,
            load_library=lambda: state.identify_settings,
            save_library=state.set_identify_settings,
            save_gui=lambda: save_config(self.config),
        )

        def save_identify_settings(
            scope: str,
            settings: IdentifySettings,
        ) -> None:
            saved = settings
            if scope == "library" and settings.output_unresolved:
                output = Path(settings.output_unresolved).expanduser()
                if output.is_absolute():
                    try:
                        output = output.resolve().relative_to(root)
                    except ValueError:
                        pass
                saved = settings.with_overrides(output_unresolved=str(output))
            WatchIdentifyPolicy.from_settings(root, saved)
            persistence.persist(scope, saved)

        library_settings = state.identify_settings
        dialog = IdentifyDialog(
            root,
            Config.load_identify_settings(),
            library_settings,
            initial_scope=self.config.identify_scopes.get(str(root), "global"),
            save_settings=save_identify_settings,
            parent=self,
        )
        dialog.job_started.connect(self._watch_job)
        dialog.exec()
        if self.job is None:
            self._resume_foreground_watch()

    def _edit_user_profile(self) -> None:
        """Edit the global identity used for publication prefetching."""
        dialog = UserProfileDialog(
            Config.load_user_profile(),
            save_profile=Config.save_user_profile,
            parent=self,
        )
        if dialog.exec() == QDialog.Accepted:
            self.status.showMessage("Publication profile saved", 5000)
        self._sync_welcome()

    def _open_cv_generator(self) -> None:
        """Open the CV Generator, which saves its settings in cv.yaml."""
        CVDialog(self).exec()

    def _edit_token(self) -> None:
        """Open the ADS token dialog and refresh the welcome status."""
        TokenDialog(self).exec()
        self._sync_welcome()

    def _sync_welcome(self) -> None:
        """Show the welcome guide instead of empty panes when no library exists."""
        empty = self.current_root is None
        self.welcome.setVisible(empty)
        self.splitter.setVisible(not empty)
        if not empty:
            return
        try:
            profile = Config.load_user_profile()
            profile_saved = bool(profile.orcid or profile.author_names)
        except (OSError, ValueError):
            profile_saved = False
        try:
            source = Config.token_source()
        except OSError:
            source = None
        self.welcome.set_status(token_source=source, profile_saved=profile_saved)

    def _refresh_selected_ads(self) -> None:
        """Refresh and rename one selected record from ADS metadata."""
        if self.current_root is None:
            return
        record = self._selected_record()
        metadata = record.get("metadata") if isinstance(record, dict) else {}
        bibcode = metadata.get("bibcode") if isinstance(metadata, dict) else None
        if bibcode is None and isinstance(record, dict):
            bibcode = record.get("bibcode")
        if not bibcode:
            QMessageBox.information(
                self,
                "Refresh ADS record",
                "Select a record with a bibcode first.",
            )
            return
        self._start_ads_refresh(
            "--bibcode",
            self._scalar_metadata_value(bibcode),
            "--relative-path",
            str(record["relative_path"]),
            "--citation-metrics",
            selection_id=int(record["location_id"]),
        )

    def _update_selected_from_ads(self, initial_bibcode: str) -> None:
        """Ask for an identifier, then refresh and rename the selected file."""
        record = self._selected_record()
        if record is None or self.current_root is None:
            return
        bibcode, accepted = QInputDialog.getText(
            self,
            "Update by identifier",
            "Bibcode, DOI, arXiv ID, or journal-volume-page.\n"
            "ADSfolio tries Crossref and DataCite when ADS has no DOI or arXiv ID:",
            text=initial_bibcode,
        )
        normalized = bibcode.strip()
        if not accepted or not normalized:
            return
        self._start_ads_refresh(
            "--identifier",
            normalized,
            "--relative-path",
            str(record["relative_path"]),
            selection_id=int(record["location_id"]),
            ambiguous_relative_path=str(record["relative_path"]),
        )

    def _save_detail_metadata(self, metadata: dict[str, Any]) -> None:
        """Persist one selected file's edited metadata and retain its selection."""
        record = self._selected_record()
        if record is None or self.current_root is None:
            return
        location_id = int(record["location_id"])
        registry = self.registries[str(self.current_root)]
        latest = registry.find_location_by_id(location_id)
        if latest is None:
            QMessageBox.warning(
                self,
                "Metadata update failed",
                "The selected file is no longer present in the registry.",
            )
            return
        relative_path = str(latest["relative_path"])
        try:
            saved = registry.replace_file_metadata(
                self.current_root, relative_path, metadata
            )
        except (ValueError, OSError, sqlite3.Error) as exc:
            QMessageBox.warning(self, "Metadata update failed", str(exc))
            self.refresh()
            self._select_file_id(location_id)
            return
        if not saved:
            QMessageBox.warning(
                self,
                "Metadata update failed",
                "The selected file is no longer present in the registry.",
            )
            return
        self.refresh()
        self._select_file_id(location_id)

    def _set_detail_row_order(self, order: list[str]) -> None:
        """Retain the user-selected detail row order for the next launch."""
        self.config.detail_row_order = list(order)

    def _selected_record(self) -> dict[str, Any] | None:
        """Return the single current table record, if one is selected."""
        index = self.table.currentIndex()
        return self.model.record_at(index.row()) if index.isValid() else None

    @staticmethod
    def _scalar_metadata_value(value: Any) -> str:
        """Return a single metadata value from a scalar or ADS-style list."""
        if isinstance(value, (list, tuple)):
            return str(value[0]).strip() if value else ""
        return str(value).strip()

    def _select_file_id(self, location_id: int) -> None:
        """Restore selection after a registry reload by location id."""
        for row in range(self.model.rowCount()):
            record = self.model.record_at(row)
            if record is not None and record.get("location_id") == location_id:
                self.table.setCurrentIndex(self.model.index(row, 0))
                return

    def _link_stored_bibcodes(self) -> None:
        """Look up all known bibcodes without renaming or moving PDFs."""
        if self.current_root is None:
            return
        records = [
            record
            for record in self.registries[str(self.current_root)].iter_files()
            if record.get("status") != "resolved" and record.get("bibcode")
        ]
        count = sum(stored_bibcode_matches_file(record) for record in records)
        skipped = len(records) - count
        explanation = (
            f" {skipped} other records need Identify because their saved metadata has no matching PDF digest."
            if skipped
            else ""
        )
        if (
            QMessageBox.question(
                self,
                "Look Up Stored Bibcodes",
                f"Look up stored bibcodes for {count} pending records? Cached ADS records are used first. "
                "Existing metadata, locked fields, and history are retained. "
                "PDFs are not renamed or moved." + explanation,
            )
            != QMessageBox.Yes
        ):
            return
        self._start_ads_refresh("--all", "--no-rename", "--pending-only")

    def _refresh_all_ads(self) -> None:
        """Confirm a batched ADS document refresh without per-paper metrics."""
        if self.current_root is None:
            return
        count = sum(
            bool(record.get("bibcode"))
            for record in self.registries[str(self.current_root)].iter_files()
        )
        if (
            QMessageBox.question(
                self,
                "Refresh all ADS records",
                (
                    f"Refresh ADS metadata for up to {count} records with bibcodes (including pending ADS lookups) and "
                    "rename their PDFs using saved settings?"
                ),
            )
            != QMessageBox.Yes
        ):
            return
        self._start_ads_refresh("--all")

    def _refresh_all_citation_metrics(self) -> None:
        """Confirm explicit per-paper requests for citation splits."""
        if self.current_root is None:
            return
        count = sum(
            bool(record.get("bibcode"))
            for record in self.registries[str(self.current_root)].iter_files()
        )
        if (
            QMessageBox.question(
                self,
                "Refresh all citation splits",
                (
                    f"Refresh ADS metadata, rename up to {count} records, and fetch "
                    "their refereed/non-refereed citation splits? ADS Metrics supplies "
                    f"these only per paper, so this sends up to {count} requests."
                ),
            )
            != QMessageBox.Yes
        ):
            return
        self._start_ads_refresh("--all", "--citation-metrics")

    def _start_ads_refresh(
        self,
        *args: str,
        selection_id: int | None = None,
        ambiguous_relative_path: str | None = None,
        root: Path | None = None,
        follow_ups: tuple[tuple[str, ...], ...] = (),
    ) -> None:
        """Start the CLI-backed ADS refresh and rename for the selected root.

        Each argument list in `follow_ups` runs as another ``refresh-ads``
        command after the first one succeeds.
        """
        refresh_root = (
            root.expanduser().resolve()
            if root is not None
            else (
                self.current_root.expanduser().resolve() if self.current_root else None
            )
        )
        if (
            self._close_requested
            or refresh_root is None
            or str(refresh_root) not in self.registries
        ):
            return
        if (
            self.job is not None
            or self._pending_watch_dialog is not None
            or self._pending_ads_refresh is not None
        ):
            return
        rename_scope = self.config.rename_scopes.get(str(refresh_root), "global")
        output_unresolved = self._effective_unresolved_directory(refresh_root)
        common = (
            "--rename-scope",
            rename_scope,
            "--output-unresolved",
            str(output_unresolved),
        )
        refresh_args = (*common, *args)
        self._pending_ads_follow_ups = tuple((*common, *extra) for extra in follow_ups)
        self._request_foreground_dialog(
            "ads_refresh",
            refresh_root,
            ads_refresh=(
                refresh_root,
                refresh_args,
                selection_id,
                ambiguous_relative_path,
            ),
        )

    def _effective_unresolved_directory(self, root: Path) -> Path:
        """Resolve the Identify destination used by an ADS refresh."""
        settings = self._effective_identify_settings(root)
        directory = (
            Path(settings.output_unresolved).expanduser()
            if settings.output_unresolved
            else root / "unresolved"
        )
        if not directory.is_absolute():
            directory = root / directory
        return directory.resolve()

    def _start_pending_ads_refresh(self, root: Path) -> None:
        """Launch a queued ADS refresh after its watcher pause is acknowledged."""
        pending = self._pending_ads_refresh
        self._pending_ads_refresh = None
        if pending is None:
            self._resume_foreground_watch()
            return
        pending_root, args, selection_id, ambiguous_relative_path = pending
        if (
            pending_root != root
            or str(root) not in self.registries
            or self._close_requested
        ):
            self._resume_foreground_watch()
            return
        follow_ups = self._pending_ads_follow_ups
        self._pending_ads_follow_ups = ()
        self._launch_ads_refresh(
            pending_root,
            args,
            selection_id=selection_id,
            ambiguous_relative_path=ambiguous_relative_path,
            follow_ups=follow_ups,
        )

    def _launch_ads_refresh(
        self,
        root: Path,
        args: tuple[str, ...],
        *,
        selection_id: int | None,
        ambiguous_relative_path: str | None,
        follow_ups: tuple[tuple[str, ...], ...] = (),
    ) -> None:
        """Launch one ADS refresh with its snapshotted root and arguments."""
        self._refresh_selection_id = selection_id
        self._ads_refresh_root = root
        self._ads_match_candidates = []
        self._ambiguous_ads_match_context = (
            (root, ambiguous_relative_path, selection_id)
            if ambiguous_relative_path is not None
            else None
        )
        job = CommandJob(
            cli_argv("pdf", "refresh-ads", str(root), *args),
            parent=self,
            working_directory=root,
            follow_up_argv=[
                cli_argv("pdf", "refresh-ads", str(root), *extra)
                for extra in follow_ups
            ],
        )
        job.output.connect(self._capture_ads_match_candidates)
        self._watch_job(job)
        job.start()

    def _capture_ads_match_candidates(self, line: str) -> None:
        """Capture structured ADS ambiguity candidates emitted by the CLI."""
        prefix = "ADS_MATCH_CANDIDATES_JSON:"
        if not line.startswith(prefix):
            return
        try:
            payload = json.loads(line.removeprefix(prefix))
        except json.JSONDecodeError:
            logger.warning("Could not parse ADS ambiguity candidates: %s", line)
            return
        if not isinstance(payload, list):
            return
        candidates: list[dict[str, str]] = []
        for value in payload:
            if not isinstance(value, dict):
                continue
            bibcode = value.get("bibcode")
            if not isinstance(bibcode, str) or not bibcode:
                continue
            candidates.append(
                {
                    "bibcode": bibcode,
                    "first_author": str(value.get("first_author") or ""),
                    "title": str(value.get("title") or ""),
                }
            )
        self._ads_match_candidates = candidates

    def _choose_ambiguous_ads_match(
        self,
        candidates: list[dict[str, str]],
        relative_path: str,
        selection_id: int | None,
        *,
        root: Path | None = None,
    ) -> None:
        """Let the user choose one coordinate-match candidate by Bibcode."""
        labels = [
            "{bibcode} — {first_author} — {title}".format(
                bibcode=candidate["bibcode"],
                first_author=candidate["first_author"] or "Unknown author",
                title=candidate["title"] or "Untitled",
            )
            for candidate in candidates
        ]
        choice, accepted = QInputDialog.getItem(
            self,
            "Multiple ADS matches",
            "Choose the correct record:",
            labels,
            editable=False,
        )
        if not accepted:
            return
        bibcode = candidates[labels.index(choice)]["bibcode"]
        self._start_ads_refresh(
            "--bibcode",
            bibcode,
            "--relative-path",
            relative_path,
            selection_id=selection_id,
            root=root,
        )

    def _rename(self) -> None:
        if self.current_root is None:
            return
        self._refresh_selection_id = None
        self._request_foreground_dialog("rename", self.current_root.resolve())

    def _open_rename_dialog(self, root: Path) -> None:
        registry = self.registries[str(root)]
        state = CentralPdfRegistryState(registry)
        persistence = _SettingsPersistenceCoordinator[RenameSettings](
            scopes=self.config.rename_scopes,
            root_key=str(root),
            load_global=Config.load_rename_settings,
            save_global=Config.save_rename_settings,
            load_library=lambda: state.rename_settings,
            save_library=state.set_rename_settings,
            save_gui=lambda: save_config(self.config),
        )

        def save_rename_settings(scope: str, settings: RenameSettings) -> None:
            persistence.persist(scope, settings)

        dialog = RenameDialog(
            root,
            Config.load_rename_settings(),
            state.rename_settings,
            initial_scope=self.config.rename_scopes.get(str(root), "global"),
            identify_settings=self._effective_identify_settings(root),
            save_settings=save_rename_settings,
            parent=self,
        )
        dialog.job_started.connect(self._watch_job)
        dialog.exec()
        if self.job is None:
            self._resume_foreground_watch()

    def _resume_foreground_watch(self) -> None:
        root = self._foreground_paused_root
        if root is None:
            return
        self._foreground_paused_root = None
        self.watch_controller.resume_writes(root)
        self._sync_refresh_actions()

    def _watch_job(self, job: Any) -> None:
        job.setParent(self)
        self.job = job
        self._sync_ads_library_action()
        self.job_progress.show()
        self.log_dock.show()
        self.identify_action.setEnabled(False)
        self.rename_action.setEnabled(False)
        self.refresh_selected_ads_action.setEnabled(False)
        self.link_bibcodes_action.setEnabled(False)
        self.refresh_all_ads_action.setEnabled(False)
        self.refresh_all_citation_metrics_action.setEnabled(False)
        self.cancel_job_action.setEnabled(True)
        job.output.connect(self.log.appendPlainText)
        job.failed.connect(
            lambda message: self.log.appendPlainText(f"Failed to start: {message}")
        )
        job.failed.connect(self._job_failed)
        job.finished.connect(lambda code: self._job_finished(code))

    def _cancel_job(self) -> None:
        """Request cancellation of the active identify or rename process."""
        if self.job is not None:
            self.job.cancel()
            self.cancel_job_action.setEnabled(False)

    def _job_failed(self, message: str) -> None:
        """Restore controls when a subprocess cannot be started."""
        self.identify_action.setEnabled(True)
        self.rename_action.setEnabled(True)
        self.refresh_selected_ads_action.setEnabled(True)
        self.link_bibcodes_action.setEnabled(True)
        self.refresh_all_ads_action.setEnabled(True)
        self.refresh_all_citation_metrics_action.setEnabled(True)
        self.cancel_job_action.setEnabled(False)
        self.job_progress.hide()
        self.job = None
        self._local_refresh_context = None
        self._local_refresh_result = None
        if self._guided_identify_context is not None:
            root = self._guided_identify_context.root
            self.notice_bar.enqueue(
                Notice(
                    f"identify:{root}",
                    f"Identification could not start: {message}",
                    severity="error",
                    root=root,
                )
            )
        self._guided_identify_context = None
        self._guided_identify_commands.clear()
        self._sync_ads_library_action()
        self._refresh_selection_id = None
        self._ads_refresh_root = None
        self._ads_match_candidates = []
        self._ambiguous_ads_match_context = None
        self._resume_foreground_watch()
        if self._close_requested:
            self.close()

    def _job_finished(self, code: int) -> None:
        identify_context = self._guided_identify_context
        self._guided_identify_context = None
        self._guided_identify_commands.clear()
        local_context = self._local_refresh_context
        local_result = self._local_refresh_result
        self._local_refresh_context = None
        self._local_refresh_result = None
        candidates = self._ads_match_candidates
        context = self._ambiguous_ads_match_context
        refresh_root = (
            identify_context.root
            if identify_context
            else local_context.root
            if local_context
            else self._ads_refresh_root
        )
        self._ads_refresh_root = None
        self._ads_match_candidates = []
        self._ambiguous_ads_match_context = None
        if code != 0:
            self.log.appendPlainText(f"Command exited with status {code}")
        self.identify_action.setEnabled(True)
        self.rename_action.setEnabled(True)
        self.refresh_selected_ads_action.setEnabled(True)
        self.link_bibcodes_action.setEnabled(True)
        self.refresh_all_ads_action.setEnabled(True)
        self.refresh_all_citation_metrics_action.setEnabled(True)
        self.cancel_job_action.setEnabled(False)
        self.job_progress.hide()
        self.job = None
        self._sync_ads_library_action()
        self._resume_foreground_watch()
        if refresh_root is None or str(refresh_root) in self.registries:
            self.refresh(refresh_root)
        if local_context is not None and str(local_context.root) in self.registries:
            self._restore_library_selection(local_context)
            if local_result is not None:
                summary = (
                    f"Refreshed {local_context.root.name}: {local_result.added} added, "
                    f"{local_result.updated} modified, {local_result.moved} moved, "
                    f"{local_result.restored} restored, {local_result.missing} missing "
                    "(history retained)."
                )
                self.status.showMessage(summary)
                self.log.appendPlainText(summary)
            elif code != 0:
                self.status.showMessage(
                    "Local refresh failed; see the job log for details."
                )
        if (
            identify_context is not None
            and str(identify_context.root) in self.registries
        ):
            self._restore_library_selection(identify_context)
            if code == 0:
                registry = self.registries[str(identify_context.root)]
                linked = self._safe_count(registry, resolved=True)
                pending = self._safe_count(registry, resolved=False)
                summary = (
                    f"Identification finished in {identify_context.root.name}: "
                    f"{linked if linked is not None else 'unknown'} ADS linked, {pending if pending is not None else 'unknown'} without ADS links. "
                    "Non-ADS papers can still have usable metadata. "
                    "Review the results, then use Rename… if needed."
                )
            else:
                summary = "Identification stopped. See the job log for details."
            self.status.showMessage(summary)
            self.notice_bar.enqueue(
                Notice(
                    f"identify:{identify_context.root}",
                    summary,
                    severity="info" if code == 0 else "warning",
                    root=identify_context.root,
                )
            )
        self._sync_refresh_actions()
        if self._refresh_selection_id is not None and (
            refresh_root is None or refresh_root == self.current_root
        ):
            self._select_file_id(self._refresh_selection_id)
        self._refresh_selection_id = None
        if (
            candidates
            and context is not None
            and code == 0
            and not self._close_requested
        ):
            root, relative_path, selection_id = context
            if str(root) in self.registries:
                self._choose_ambiguous_ads_match(
                    candidates, relative_path, selection_id, root=root
                )
        if self._close_requested:
            self.close()

    def closeEvent(self, event: Any) -> None:
        """Persist window state and close registry connections."""
        if self.job is not None and self.job.running:
            self._close_requested = True
            self._cancel_job()
            event.ignore()
            return
        if self._ads_library_export_dialog is not None:
            self._close_requested = True
            self._ads_library_export_dialog.close()
            if self._ads_library_export_dialog is not None:
                event.ignore()
                return
        if self._pending_ads_refresh is not None:
            pending_root = self._pending_ads_refresh[0]
            self._pending_ads_refresh = None
            self._pending_watch_dialog = None
            self.watch_controller.resume_writes(pending_root)
        if not self.watch_controller.stop_all():
            self._close_requested = False
            self.notice_bar.enqueue(
                Notice(
                    "watch-shutdown",
                    "The window could not stop all library watchers; close was cancelled.",
                    severity="error",
                )
            )
            event.ignore()
            return
        self._close_requested = True
        self.update_checker.cancel()
        self.update_downloader.cancel()
        if self._about_dialog is not None:
            self._about_dialog.close()
        if self._update_dialog is not None:
            self._update_dialog.close()
        self.search_timer.stop()
        self._watch_refresh_timer.stop()
        self._pending_watch_refresh_roots.clear()
        self._watch_refresh_selection_id = None
        self._pending_watch_dialog = None
        self._local_refresh_context = None
        self._guided_identify_context = None
        self._guided_identify_commands.clear()
        self._sync_config_roots()
        self.config.last_root = str(self.current_root) if self.current_root else None
        self.config.last_view = self.current_view.value
        self._remember_splitter_sizes()
        self.config.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
        self.config.window_state = bytes(self.saveState().toBase64()).decode()
        self.config.column_widths = {
            column.key: self.table.columnWidth(index)
            for index, column in enumerate(self.columns)
        }
        self.config.table_column_order = [
            self.columns[self.table.header().logicalIndex(index)].key
            for index in range(self.table.header().count())
        ]
        self.config.hidden_table_columns = [
            column.key
            for index, column in enumerate(self.columns)
            if self.table.isColumnHidden(index)
        ]
        self.config.sort_column = self.current_query.order_by
        self.config.sort_descending = self.current_query.descending
        self.config.secondary_sort_column = self.current_query.secondary_order_by
        self.config.tertiary_sort_column = self.current_query.tertiary_order_by
        self.config.detail_row_order = self.detail.saved_row_order()
        try:
            save_config(self.config)
        except OSError as exc:
            logger.warning("Could not save GUI configuration: %s", exc)
        for registry in self.registries.values():
            registry.close()
        super().closeEvent(event)
