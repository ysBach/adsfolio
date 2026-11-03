"""A single window for the desktop application's settings."""

from __future__ import annotations

import sqlite3
from copy import deepcopy
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import Config, write_private_text
from ..cv.config import config_from_mapping
from ..pdf.identify_settings import IdentifySettings
from ..pdf.registry import CentralPdfRegistryState
from ..pdf.rename_settings import RenameSettings
from .appearance import AppearanceDialog
from .config import save_config
from .dialogs import IdentifyDialog, RenameDialog
from .watch_sounds import WatchSoundChoice


class PreferencesDialog(QDialog):
    """Edit desktop settings in one window without running library jobs."""

    def __init__(self, window: Any) -> None:
        super().__init__(window)
        self.window = window
        self.setWindowTitle("Settings")
        self.resize(960, 700)
        self.setMinimumSize(700, 480)
        self.setObjectName("preferences_dialog")
        self._appearance_dirty = False
        self._builders = (
            self._general_page,
            self._appearance_page,
            self._sorting_page,
            self._libraries_page,
            self._defaults_page,
            self._citations_page,
            self._account_page,
            self._exports_page,
        )
        self._built: set[int] = set()
        outer = QVBoxLayout(self)
        outer.addWidget(
            self._message(
                "Save changes in each section. Library watching controls apply immediately.",
                self,
            )
        )
        content = QHBoxLayout()
        self.categories = QListWidget(self)
        self.categories.setAccessibleName("Settings categories")
        self.categories.addItems(
            [
                "General",
                "Appearance",
                "Sorting",
                "Libraries and Watching",
                "Identify and Rename",
                "Citations",
                "Account",
                "CV and Export",
            ]
        )
        self.categories.setMaximumWidth(190)
        self.pages = QStackedWidget(self)
        for _ in self._builders:
            self.pages.addWidget(QWidget(self.pages))
        content.addWidget(self.categories)
        content.addWidget(self.pages, 1)
        outer.addLayout(content, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.categories.currentRowChanged.connect(self._select_page)
        self.categories.setCurrentRow(0)

    def _select_page(self, index: int) -> None:
        if not 0 <= index < len(self._builders):
            return
        if index not in self._built:
            page = self._builders[index]()
            old = self.pages.widget(index)
            self.pages.removeWidget(old)
            old.deleteLater()
            self.pages.insertWidget(index, page)
            self._built.add(index)
        self.pages.setCurrentIndex(index)

    @staticmethod
    def _message(text: str, parent: QWidget) -> QLabel:
        label = QLabel(text, parent)
        label.setWordWrap(True)
        return label

    @staticmethod
    def _scroll(widget: QWidget, parent: QWidget) -> QScrollArea:
        widget.setWindowFlags(Qt.Widget)
        scroll = QScrollArea(parent)
        scroll.setWidgetResizable(True)
        scroll.setWidget(widget)
        return scroll

    @staticmethod
    def _feedback(layout: QVBoxLayout, parent: QWidget) -> QLabel:
        feedback = QLabel(parent)
        feedback.setWordWrap(True)
        layout.addWidget(feedback)
        return feedback

    def _general_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.addWidget(
            self._message("Choose settings used by the desktop app.", page)
        )
        form = QFormLayout()
        self.sound = WatchSoundChoice(
            page,
            sound_id=self.window.config.watch_sound,
            player=self.window.watch_sound_player,
        )
        form.addRow("New PDF sound", self.sound)
        self.automatic_updates = QCheckBox("Check for updates automatically", page)
        self.automatic_updates.setChecked(
            self.window.config.check_updates_automatically
        )
        form.addRow(self.automatic_updates)
        self.show_libraries = QCheckBox("Show Libraries sidebar", page)
        self.show_libraries.setChecked(self.window.config.sidebar_visible)
        form.addRow(self.show_libraries)
        self.show_details = QCheckBox("Show detail pane", page)
        self.show_details.setChecked(self.window.config.detail_visible)
        form.addRow(self.show_details)
        layout.addLayout(form)
        self.general_feedback = self._feedback(layout, page)
        self.sound.preview_failed.connect(
            lambda: self.general_feedback.setText(
                "Could not play the sound. Check the audio output."
            )
        )
        self.sound.sound_changed.connect(lambda _sound: self.general_feedback.clear())
        save = QPushButton("Save General Settings", page)
        save.clicked.connect(self._save_general)
        layout.addWidget(save, alignment=Qt.AlignLeft)
        layout.addStretch(1)
        return page

    def _save_general(self) -> None:
        window = self.window
        before = deepcopy(window.config)
        window._sync_config_roots()
        window.config.watch_sound = self.sound.sound_id()
        window.config.check_updates_automatically = self.automatic_updates.isChecked()
        window.config.sidebar_visible = self.show_libraries.isChecked()
        window.config.detail_visible = self.show_details.isChecked()
        try:
            save_config(window.config)
        except OSError as exc:
            window.config = before
            self.general_feedback.setText(f"Could not save settings: {exc}")
            return
        for action, checked in (
            (window.automatic_updates_action, self.automatic_updates.isChecked()),
            (window.sidebar_action, self.show_libraries.isChecked()),
            (window.detail_action, self.show_details.isChecked()),
        ):
            blocker = QSignalBlocker(action)
            action.setChecked(checked)
            del blocker
        window._toggle_sidebar(self.show_libraries.isChecked())
        window._toggle_detail(self.show_details.isChecked())
        self.general_feedback.setText("General settings saved.")

    def _appearance_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.addWidget(
            self._message("Select Save Appearance to apply these changes.", page)
        )
        self.appearance = AppearanceDialog(
            self.window.config,
            self.window.columns,
            self.window.detail.available_rows(),
            page,
        )
        buttons = self.appearance.findChild(QDialogButtonBox)
        if buttons is not None:
            buttons.hide()
        layout.addWidget(self._scroll(self.appearance, page), 1)
        self.appearance_feedback = self._feedback(layout, page)
        self.appearance.settings_changed.connect(self._mark_appearance_dirty)
        save = QPushButton("Save Appearance", page)
        save.clicked.connect(self._save_appearance)
        layout.addWidget(save, alignment=Qt.AlignLeft)
        return page

    def _mark_appearance_dirty(self) -> None:
        self._appearance_dirty = True
        self.appearance_feedback.clear()

    def _save_appearance(self) -> None:
        window = self.window
        before = deepcopy(window.config)
        try:
            window._apply_appearance_dialog(self.appearance)
            window._sync_config_roots()
            save_config(window.config)
        except (OSError, ValueError) as exc:
            window.config = before
            window._apply_appearance_config()
            self.appearance_feedback.setText(f"Could not save appearance: {exc}")
            return
        self._appearance_dirty = False
        self.appearance_feedback.setText("Appearance saved.")

    def _sorting_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.addWidget(
            self._message(
                "Choose the primary column and optional tie-break columns. "
                "The direction applies to every sort column.",
                page,
            )
        )
        form = QFormLayout()
        columns = [column for column in self.window.columns if column.sortable]
        self.sort_primary = QComboBox(page)
        self.sort_secondary = QComboBox(page)
        self.sort_tertiary = QComboBox(page)
        for combo in (self.sort_secondary, self.sort_tertiary):
            combo.addItem("None", None)
        for column in columns:
            label = column.header or "Resolution marker"
            for combo in (self.sort_primary, self.sort_secondary, self.sort_tertiary):
                combo.addItem(label, column.key)
        for combo, current in (
            (self.sort_primary, self.window.current_query.order_by),
            (self.sort_secondary, self.window.current_query.secondary_order_by),
            (self.sort_tertiary, self.window.current_query.tertiary_order_by),
        ):
            index = combo.findData(current)
            if index >= 0:
                combo.setCurrentIndex(index)
        self.sort_direction = QComboBox(page)
        self.sort_direction.addItem("Ascending", False)
        self.sort_direction.addItem("Descending", True)
        self.sort_direction.setCurrentIndex(int(self.window.current_query.descending))
        form.addRow("Primary column", self.sort_primary)
        form.addRow("Secondary column", self.sort_secondary)
        form.addRow("Tertiary column", self.sort_tertiary)
        form.addRow("Direction", self.sort_direction)
        layout.addLayout(form)
        self.sort_feedback = self._feedback(layout, page)
        save = QPushButton("Save Sorting", page)
        save.clicked.connect(self._save_sorting)
        layout.addWidget(save, alignment=Qt.AlignLeft)
        layout.addStretch(1)
        return page

    def _save_sorting(self) -> None:
        primary = self.sort_primary.currentData()
        if not isinstance(primary, str):
            self.sort_feedback.setText("Choose a primary column.")
            return
        secondary = self.sort_secondary.currentData()
        tertiary = self.sort_tertiary.currentData()
        selected = [key for key in (primary, secondary, tertiary) if key is not None]
        if len(selected) != len(set(selected)):
            self.sort_feedback.setText("Choose a different column for each sort level.")
            return
        saved = self.window.apply_sort(
            primary,
            bool(self.sort_direction.currentData()),
            secondary,
            tertiary,
        )
        self.sort_feedback.setText(
            "Sorting saved."
            if saved
            else "Could not save sorting. Check Notifications."
        )

    def _root_combo(self, parent: QWidget) -> QComboBox:
        combo = QComboBox(parent)
        combo.addItem("Choose a library", None)
        for key, entry in self.window.root_entries.items():
            combo.addItem(entry.label or Path(key).name or key, key)
            combo.setItemData(combo.count() - 1, key, Qt.ToolTipRole)
        if self.window.current_root is not None and not self.window.combined.enabled:
            index = combo.findData(str(self.window.current_root))
            if index >= 0:
                combo.setCurrentIndex(index)
        return combo

    def _libraries_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        form = QFormLayout()
        self.library_target = self._root_combo(page)
        form.addRow("Library", self.library_target)
        self.library_path = self._message("", page)
        form.addRow("Folder", self.library_path)
        self.watch_now = QCheckBox("Watch this library now", page)
        self.watch_on_start = QCheckBox("Start watching when ADSfolio opens", page)
        self.watch_auto = QCheckBox("Automatically identify and rename new PDFs", page)
        self.watch_auto.setToolTip(
            "Future watched arrivals can be identified and renamed using saved settings."
        )
        self.watch_recursive = QCheckBox("Include subdirectories", page)
        for control in (
            self.watch_now,
            self.watch_on_start,
            self.watch_auto,
            self.watch_recursive,
        ):
            form.addRow(control)
        layout.addLayout(form)
        layout.addWidget(
            self._message(
                "Stop watching before changing the subdirectory setting. "
                "Refresh the library afterward to scan the new scope.",
                page,
            )
        )
        self.libraries_feedback = self._feedback(layout, page)
        layout.addStretch(1)
        self.library_target.currentIndexChanged.connect(self._load_library_controls)
        self.watch_now.toggled.connect(self._change_watch_now)
        self.watch_on_start.toggled.connect(
            lambda checked: self._change_watch_policy("watch_on_start", checked)
        )
        self.watch_auto.toggled.connect(
            lambda checked: self._change_watch_policy("auto_process_new_pdfs", checked)
        )
        self.watch_recursive.toggled.connect(self._change_recursive)
        self.window.watch_controller.started.connect(self._load_library_controls)
        self.window.watch_controller.stopped.connect(self._load_library_controls)
        self.window.watch_controller.failed.connect(self._watch_failed)
        self._load_library_controls()
        return page

    def _library_root(self, selector: QComboBox) -> Path | None:
        value = selector.currentData()
        if not isinstance(value, str) or value not in self.window.root_entries:
            return None
        return Path(value)

    def _load_library_controls(self, *_args: Any) -> None:
        root = self._library_root(self.library_target)
        entry = self.window.root_entries.get(str(root)) if root is not None else None
        watching = root is not None and self.window.watch_controller.is_watching(root)
        self.library_path.setText(
            str(root) if root is not None else "Choose a library."
        )
        for control, checked in (
            (self.watch_now, watching),
            (self.watch_on_start, bool(entry and entry.watch_on_start)),
            (self.watch_auto, bool(entry and entry.auto_process_new_pdfs)),
            (self.watch_recursive, bool(entry and entry.recursive)),
        ):
            blocker = QSignalBlocker(control)
            control.setChecked(checked)
            control.setEnabled(entry is not None)
            del blocker
        self.watch_recursive.setEnabled(entry is not None and not watching)

    def _change_watch_now(self, checked: bool) -> None:
        root = self._library_root(self.library_target)
        if root is not None:
            self.window._set_root_watching(root, checked)
        self._load_library_controls()
        if self.watch_now.isChecked() != checked:
            self.libraries_feedback.setText(
                "Could not change watching. Check Notifications."
            )
        else:
            self.libraries_feedback.clear()

    def _change_watch_policy(self, field: str, checked: bool) -> None:
        root = self._library_root(self.library_target)
        if root is not None:
            self.window._set_root_watch_policy(root, field, checked)
        self._load_library_controls()
        control = self.watch_on_start if field == "watch_on_start" else self.watch_auto
        if control.isChecked() != checked:
            self.libraries_feedback.setText(
                "Could not save the setting. Check Notifications."
            )
        else:
            self.libraries_feedback.clear()

    def _change_recursive(self, checked: bool) -> None:
        root = self._library_root(self.library_target)
        if root is not None:
            self.window._set_root_recursive(root, checked)
        self._load_library_controls()
        if self.watch_recursive.isChecked() != checked:
            self.libraries_feedback.setText(
                "Could not save the setting. Check Notifications."
            )
        else:
            self.libraries_feedback.clear()

    def _watch_failed(self, failure: Any) -> None:
        if self._library_root(self.library_target) == failure.root:
            self.libraries_feedback.setText("Watching failed. Check Notifications.")
            self._load_library_controls()

    def _defaults_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        self.defaults_target = self._root_combo(page)
        row = QFormLayout()
        row.addRow("Library for library-only defaults", self.defaults_target)
        layout.addLayout(row)
        layout.addWidget(
            self._message(
                "Save defaults before choosing another library or scope. "
                "Stop that library's watcher before saving library-only defaults.",
                page,
            )
        )
        self.defaults_tabs = QTabWidget(page)
        self.defaults_tabs.addTab(QWidget(), "Identify")
        self.defaults_tabs.addTab(QWidget(), "Rename")
        layout.addWidget(self.defaults_tabs, 1)
        self.defaults_feedback = self._feedback(layout, page)
        self.defaults_target.currentIndexChanged.connect(self._rebuild_defaults)
        self._defaults_target_index = self.defaults_target.currentIndex()
        self._rebuild_defaults()
        return page

    def _rebuild_defaults(self, *_args: Any) -> None:
        if hasattr(self, "identify") and self._defaults_changed():
            answer = QMessageBox.question(
                self,
                "Unsaved defaults",
                "Discard the unsaved Identify or Rename changes for this library?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                blocker = QSignalBlocker(self.defaults_target)
                self.defaults_target.setCurrentIndex(self._defaults_target_index)
                del blocker
                return
        root = self._library_root(self.defaults_target)
        registry = self.window.registries.get(str(root)) if root is not None else None
        try:
            state = CentralPdfRegistryState(registry) if registry is not None else None
            identify_library = state.identify_settings if state is not None else None
            rename_library = state.rename_settings if state is not None else None
            identify_global = Config.load_identify_settings()
            rename_global = Config.load_rename_settings()
        except (OSError, ValueError, sqlite3.Error) as exc:
            blocker = QSignalBlocker(self.defaults_target)
            self.defaults_target.setCurrentIndex(self._defaults_target_index)
            del blocker
            self.defaults_feedback.setText(f"Could not load library settings: {exc}")
            return
        self._defaults_target_index = self.defaults_target.currentIndex()
        self.defaults_feedback.clear()
        root_for_dialog = root or Path.home()
        identify = IdentifyDialog(
            root_for_dialog,
            identify_global,
            identify_library,
            initial_scope=(
                self.window.config.identify_scopes.get(str(root), "global")
                if root is not None
                else "global"
            ),
            save_settings=lambda scope, settings: self._save_identify(
                root, scope, settings
            ),
            parent=self.defaults_tabs,
        )
        identify.run_button.hide()
        identify.cancel_button.hide()
        rename = RenameDialog(
            root_for_dialog,
            rename_global,
            rename_library,
            initial_scope=(
                self.window.config.rename_scopes.get(str(root), "global")
                if root is not None
                else "global"
            ),
            save_settings=lambda scope, settings: self._save_rename(
                root, scope, settings
            ),
            parent=self.defaults_tabs,
        )
        rename.preview_button.hide()
        rename.apply_button.hide()
        rename.summary.hide()
        rename.table.hide()
        buttons = rename.findChild(QDialogButtonBox)
        if buttons is not None:
            cancel = buttons.button(QDialogButtonBox.Cancel)
            if cancel is not None:
                cancel.hide()
        rename.save_defaults_button.clicked.disconnect(rename.accept)
        rename.save_defaults_button.clicked.connect(rename._save)
        for dialog in (identify, rename):
            option = dialog.scope.model().item(dialog.scope.findData("library"))
            if option is not None:
                option.setEnabled(root is not None)
        self.identify = identify
        self.rename = rename
        self._identify_baseline = identify.selected_settings()
        self._rename_baseline = rename.selected_settings()
        self._scope_indices = {
            "identify": identify.scope.currentIndex(),
            "rename": rename.scope.currentIndex(),
        }
        identify.scope.currentIndexChanged.disconnect(identify._load_selected_scope)
        rename.scope.currentIndexChanged.disconnect(rename._load_selected_scope)
        identify.scope.currentIndexChanged.connect(
            lambda _index: self._change_defaults_scope(identify, "identify")
        )
        rename.scope.currentIndexChanged.connect(
            lambda _index: self._change_defaults_scope(rename, "rename")
        )
        for index, (name, dialog) in enumerate(
            (("Identify", identify), ("Rename", rename))
        ):
            old = self.defaults_tabs.widget(index)
            self.defaults_tabs.removeTab(index)
            old.deleteLater()
            self.defaults_tabs.insertTab(
                index, self._scroll(dialog, self.defaults_tabs), name
            )

    def _change_defaults_scope(self, dialog: Any, name: str) -> None:
        baseline = f"_{name}_baseline"
        try:
            changed = dialog.selected_settings() != getattr(self, baseline)
        except ValueError:
            changed = True
        if changed:
            answer = QMessageBox.question(
                self,
                "Unsaved defaults",
                f"Discard the unsaved {name.title()} changes for this scope?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                blocker = QSignalBlocker(dialog.scope)
                dialog.scope.setCurrentIndex(self._scope_indices[name])
                del blocker
                return
        dialog._load_selected_scope()
        setattr(self, baseline, dialog.selected_settings())
        self._scope_indices[name] = dialog.scope.currentIndex()

    def _defaults_changed(self) -> bool:
        try:
            return (
                self.identify.selected_settings() != self._identify_baseline
                or self.rename.selected_settings() != self._rename_baseline
            )
        except ValueError:
            return True

    def _check_library_save(self, root: Path | None, scope: str) -> None:
        if scope != "library":
            return
        if root is None or str(root) not in self.window.registries:
            raise ValueError("Choose a library first.")
        if self.window.watch_controller.is_watching(root):
            raise ValueError("Stop watching this library before saving its defaults.")
        if (
            self.window.job is not None
            or self.window._pending_watch_dialog is not None
            or self.window._pending_ads_refresh is not None
            or self.window._foreground_paused_root is not None
            or self.window._local_refresh_context is not None
        ):
            raise ValueError("Wait for the current library operation to finish.")

    def _save_identify(
        self, root: Path | None, scope: str, settings: IdentifySettings
    ) -> None:
        self._check_library_save(root, scope)
        try:
            self.window._persist_identify_defaults(root, scope, settings)
        except sqlite3.Error as exc:
            raise OSError(str(exc)) from exc
        self._identify_baseline = settings
        self.defaults_feedback.setText("Identify defaults saved.")

    def _save_rename(
        self, root: Path | None, scope: str, settings: RenameSettings
    ) -> None:
        self._check_library_save(root, scope)
        try:
            self.window._persist_rename_defaults(root, scope, settings)
        except sqlite3.Error as exc:
            raise OSError(str(exc)) from exc
        if scope == "library":
            self.rename._library_settings = settings
        else:
            self.rename._global_settings = settings
        self._rename_baseline = settings
        self.defaults_feedback.setText("Rename defaults saved.")

    def _citations_page(self) -> QWidget:
        from .preferences_citations import CitationPreferences

        return CitationPreferences(
            Config.load_key_policy(),
            self.window.config.citation_styles,
            save_policy=Config.save_key_policy,
            save_styles=self._save_citation_styles,
            parent=self,
        )

    def _save_citation_styles(self, styles: Any) -> None:
        if self._appearance_dirty:
            raise ValueError("Save Appearance before changing citation styles.")
        self.window._save_citation_preferences(styles)
        appearance_index = 1
        if appearance_index in self._built:
            old = self.pages.widget(appearance_index)
            self.pages.removeWidget(old)
            old.deleteLater()
            self.pages.insertWidget(appearance_index, QWidget(self.pages))
            self._built.remove(appearance_index)

    def _account_page(self) -> QWidget:
        from .preferences_account import AccountPreferences

        page = AccountPreferences(parent=self)
        page.token_saved.connect(self.window._sync_welcome)
        page.profile_saved.connect(self.window._sync_welcome)
        return page

    def _exports_page(self) -> QWidget:
        import yaml

        from .cv_dialog import CVDialog

        owner = self

        class _CVSettings(CVDialog):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                self._settings_load_error: str | None = None
                self._initial_settings_bytes: bytes | None = None
                super().__init__(*args, **kwargs)

            def _changed(self, *_args: object) -> None:
                """Keep settings edits from starting the generator preview."""

            def _read_yaml(self, path: Path) -> dict[str, Any]:
                try:
                    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
                    if loaded is not None and not isinstance(loaded, dict):
                        raise ValueError("Settings must contain a mapping.")
                    return loaded or {}
                except (OSError, ValueError, yaml.YAMLError) as exc:
                    self._settings_load_error = str(exc)
                    raise ValueError(str(exc)) from exc

            def _show_mapping(self, mapping: dict[str, Any]) -> None:
                try:
                    super()._show_mapping(mapping)
                except (TypeError, ValueError) as exc:
                    if self._loading:
                        self._settings_load_error = str(exc)
                    raise

            def _load_settings(self) -> None:
                try:
                    before = PreferencesDialog._read_optional_file(self.settings_path)
                except OSError as exc:
                    before = None
                    self._settings_load_error = str(exc)
                try:
                    super()._load_settings()
                except (OSError, TypeError, ValueError) as exc:
                    self._settings_load_error = str(exc)
                    self._show_mapping({})
                try:
                    after = PreferencesDialog._read_optional_file(self.settings_path)
                except OSError as exc:
                    after = None
                    self._settings_load_error = str(exc)
                if before != after:
                    self._settings_load_error = "Settings changed while loading."
                self._initial_settings_bytes = before
                if self._settings_load_error is not None:
                    self.status.setText(
                        f"Could not load settings: {self._settings_load_error}. "
                        "Reopen Settings to retry."
                    )

            def reject(self) -> None:
                owner.reject()

            def done(self, result: int) -> None:
                self._preview_timer.stop()
                QDialog.done(self, result)

        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.addWidget(
            self._message(
                "These settings do not generate or export a publication list. "
                "Save each tab separately.",
                page,
            )
        )
        tabs = QTabWidget(page)
        self.cv_settings = _CVSettings(page)
        self.export_settings = _CVSettings(page, records=[])
        self._cv_snapshots: dict[Path, bytes | None] = {}
        for label, dialog in (
            ("CV Generator", self.cv_settings),
            ("Selected Papers", self.export_settings),
        ):
            dialog.close_button.hide()
            dialog.save_button.hide()
            if dialog._settings_load_error is None:
                self._cv_snapshots[dialog.settings_path] = (
                    dialog._initial_settings_bytes
                )
            splitter = dialog.tabs.parentWidget()
            if splitter is not None and hasattr(splitter, "widget"):
                preview = splitter.widget(1)
                if preview is not None:
                    preview.hide()
            save = QPushButton("Save Settings", dialog)
            save.clicked.connect(
                lambda _checked=False, target=dialog: self._save_cv(target)
            )
            dialog.layout().addWidget(save)
            tabs.addTab(self._scroll(dialog, tabs), label)
        layout.addWidget(tabs, 1)
        return page

    @staticmethod
    def _read_optional_file(path: Path) -> bytes | None:
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None

    def _save_cv(self, dialog: Any) -> None:
        import yaml

        path = dialog.settings_path
        if path not in self._cv_snapshots:
            dialog.status.setText("Could not read settings. Reopen Settings to retry.")
            return
        try:
            if self._read_optional_file(path) != self._cv_snapshots[path]:
                dialog.status.setText(
                    "Settings changed on disk. Reopen Settings to edit them."
                )
                return
            mapping = dialog.mapping()
            config_from_mapping(mapping, path)
            text = yaml.safe_dump(mapping, sort_keys=False, allow_unicode=True)
            write_private_text(path, text)
            self._cv_snapshots[path] = text.encode("utf-8")
            dialog._raw = mapping
        except (OSError, ValueError) as exc:
            dialog.status.setText(f"Could not save settings: {exc}")
            return
        dialog.status.setText("Settings saved.")
