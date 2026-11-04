"""Controls for a table of PDFs from selected libraries."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QFileInfo,
    QItemSelection,
    QItemSelectionModel,
    QModelIndex,
    QObject,
    QSignalBlocker,
    Qt,
    QTimer,
)
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFileIconProvider,
    QMenu,
    QStyle,
    QTreeWidgetItem,
)

from .combined_library import CombinedLibrary, combined_columns
from .combined_tree import CombinedTreeProxyModel
from .library import build_columns
from .notifications import Notice

# The system PDF icon, found once from an existing PDF.
_PDF_ICON: QIcon | None = None


class CombinedViewController(QObject):
    """Keep combined-view selection separate from the underlying libraries."""

    def __init__(self, window: Any) -> None:
        super().__init__(window)
        self.window = window
        self.enabled = False
        self.restoring = True
        self.tree_model: CombinedTreeProxyModel | None = None
        self._tree_expanded: set[tuple[Any, ...]] = set()
        self._tree_seen_roots: set[tuple[Any, ...]] = set()
        self._tree_pending_selection: set[tuple[Any, ...]] = set()
        self._tree_pending_current: tuple[Any, ...] | None = None
        self._tree_resetting = False
        self._restoring_tree = False
        self._tree_signals_connected = False
        self._tree_restore_timer = QTimer(self)
        self._tree_restore_timer.setSingleShot(True)
        self._tree_restore_timer.timeout.connect(self._restore_tree_state)
        self.selected = {
            window._config_root_key(path) for path in window.config.combined_roots or []
        }
        self._known_roots = {
            window._config_root_key(entry.path) for entry in window.config.roots
        }
        self.toggle_item = QTreeWidgetItem(window.libraries_item, ["Combine libraries"])
        self.toggle_item.setData(0, Qt.UserRole + 1, "combined-toggle")
        self.toggle_item.setToolTip(
            0,
            "Show PDFs from checked libraries together. Select a PDF to edit it in its library.",
        )
        self.toggle_item.setFlags(self.toggle_item.flags() | Qt.ItemIsUserCheckable)
        self.toggle_item.setCheckState(0, Qt.Unchecked)
        window.sidebar.itemChanged.connect(self._item_changed)

    def register_root(self, item: QTreeWidgetItem, key: str) -> None:
        """Add a new library to the combined selection by default."""
        if self.window.config.combined_roots is None or (
            not self.restoring and key not in self._known_roots
        ):
            self.selected.add(key)
        self._known_roots.add(key)
        self._set_root_checkbox(item, key)
        if not self.restoring:
            self._save_selection()

    def finish_restoring(self) -> None:
        """Restore the saved combined-view state after registering roots."""
        self.restoring = False
        unrestored = self._unrestored_root_keys()
        if self.window.config.combined_roots is None:
            self.selected.update(unrestored)
        self.selected.intersection_update((*self.window.registries, *unrestored))
        saved_view = self.window.config.combined_view
        self.set_enabled(bool(self.window.registries) and saved_view)
        if not self.window.registries and unrestored:
            self.window.config.combined_view = saved_view

    def remove_root(self, key: str) -> None:
        """Forget a removed library without changing any files."""
        self.selected.discard(key)
        self._known_roots.discard(key)
        self._save_selection()

    def _unrestored_root_keys(self) -> list[str]:
        return [
            self.window._config_root_key(entry.path)
            for entry in self.window._unrestored_roots
        ]

    def _save_selection(self) -> None:
        keys = dict.fromkeys((*self.window.registries, *self._unrestored_root_keys()))
        self.window.config.combined_roots = [
            key for key in keys if key in self.selected
        ]

    def _set_root_checkbox(self, item: QTreeWidgetItem, key: str) -> None:
        blocker = QSignalBlocker(self.window.sidebar)
        if self.enabled:
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Checked if key in self.selected else Qt.Unchecked)
        else:
            item.setData(0, Qt.CheckStateRole, None)
            item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)
        del blocker

    def _item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if column != 0:
            return
        kind = item.data(0, Qt.UserRole + 1)
        if kind == "combined-toggle":
            self.set_enabled(item.checkState(0) == Qt.Checked)
        elif self.enabled and kind == "root":
            key = str(item.data(0, Qt.UserRole))
            if item.checkState(0) == Qt.Checked:
                self.selected.add(key)
            else:
                self.selected.discard(key)
            self._save_selection()
            self.refresh()

    def set_enabled(self, enabled: bool) -> None:
        """Switch between the combined table and the selected library."""
        window = self.window
        if (
            enabled
            and not self.enabled
            and (
                window.job is not None
                or window._pending_watch_dialog is not None
                or window._pending_ads_refresh is not None
                or window._foreground_paused_root is not None
                or window._local_refresh_context is not None
                or window._guided_identify_context is not None
                or window._manual_metadata_target is not None
                or window._edition_merge_target is not None
            )
        ):
            blocker = QSignalBlocker(window.sidebar)
            self.toggle_item.setCheckState(0, Qt.Unchecked)
            del blocker
            window.status.showMessage(
                "Combine libraries is unavailable now. "
                + window._operation_wait_message()
            )
            return
        self.enabled = bool(enabled)
        window.config.combined_view = self.enabled
        if self.enabled:
            self._save_selection()
        blocker = QSignalBlocker(window.sidebar)
        self.toggle_item.setCheckState(0, Qt.Checked if self.enabled else Qt.Unchecked)
        del blocker
        for index in range(window.libraries_item.childCount()):
            item = window.libraries_item.child(index)
            if item.data(0, Qt.UserRole + 1) == "root":
                self._set_root_checkbox(item, str(item.data(0, Qt.UserRole)))
        if not self.enabled:
            # Remove the tree first. A column reset under the tree would
            # otherwise capture and restore its whole selection.
            self._set_tree_enabled(False)
        self.update_columns()
        self._set_tree_enabled(self.enabled)
        if self.enabled:
            self.refresh()
        else:
            window.notice_bar.dismiss("combined-unavailable")
        if not self.enabled and window.current_root is not None:
            registry = window.registries.get(str(window.current_root))
            window.model.set_registry(registry, window.current_query)
            if registry is not None:
                window._populate_views(registry)
        window._update_status()
        window._sync_ads_library_action()

    def _set_tree_enabled(self, enabled: bool) -> None:
        """Switch the table between flat and grouped library models."""
        window = self.window
        if enabled and self.tree_model is None:
            tree = CombinedTreeProxyModel(window.model, self, root_labels=self.labels())
            tree.file_icon = self._pdf_icon()
            self.tree_model = tree
            tree.modelAboutToBeReset.connect(self._capture_tree_selection)
            window._set_table_model(tree)
            window.table.setRootIsDecorated(True)
            # Half the style's indentation keeps the folding arrows compact.
            window.table.setIndentation(
                window.table.style().pixelMetric(
                    QStyle.PM_TreeViewIndentation, None, window.table
                )
                // 2
            )
            if not self._tree_signals_connected:
                window.table.expanded.connect(self._tree_expansion_changed)
                window.table.collapsed.connect(self._tree_expansion_changed)
                self._tree_signals_connected = True
            tree.modelReset.connect(self._restore_tree_state)
            tree.rowsInserted.connect(self._queue_tree_restore)
            tree.loading_finished.connect(self._finish_tree_loading)
            tree.loading_failed.connect(window.status.showMessage)
            self._restore_tree_state()
        elif not enabled and self.tree_model is not None:
            tree = self.tree_model
            self.tree_model = None
            window._set_table_model(window.model)
            window.table.setRootIsDecorated(False)
            window.table.resetIndentation()
            tree.setSourceModel(None)
            tree.deleteLater()
            self._tree_restore_timer.stop()
            self._tree_pending_selection.clear()
            self._tree_pending_current = None

    def _pdf_icon(self) -> QIcon:
        """Return the system icon for PDF files.

        The system gives its PDF icon only for a file that exists, so the
        first existing PDF of the table supplies it. Without one, the generic
        file icon is used. The icon is kept for the session.
        """
        global _PDF_ICON
        if _PDF_ICON is not None:
            return _PDF_ICON
        model = self.window.model
        for row in range(model.rowCount()):
            record = model.record_at(row)
            path = self.path_for(record) if record is not None else None
            if path is not None and path.is_file():
                _PDF_ICON = QFileIconProvider().icon(QFileInfo(str(path)))
                return _PDF_ICON
        return QApplication.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)

    def _capture_tree_selection(self) -> None:
        """Retain tree selection across a source-model reset."""
        tree = self.tree_model
        if tree is None or self.window.table.model() is not tree:
            return
        self._tree_resetting = True
        self._tree_pending_selection = {
            key
            for index in self.window._selected_view_rows()
            if (key := tree.key_for_index(index)) is not None
        }
        self._tree_pending_current = tree.key_for_index(
            self.window.table.currentIndex()
        )

    def _tree_expansion_changed(self, index: QModelIndex) -> None:
        tree = self.tree_model
        if tree is None or self._tree_resetting or self._restoring_tree:
            return
        key = tree.key_for_index(index)
        if key is None or not tree.is_folder_index(index):
            return
        if self.window.table.isExpanded(index):
            self._tree_expanded.add(key)
        else:
            self._tree_expanded.discard(key)

    def _restore_tree_state(self, *_args: Any) -> None:
        """Restore expanded groups and selected nodes as pages become available."""
        tree = self.tree_model
        if tree is None or self.window.table.model() is not tree:
            return
        self._tree_resetting = False
        self._restoring_tree = True
        changed = False
        try:
            self.sync_tree_column()
            for row in range(tree.rowCount()):
                index = tree.index(row, 0)
                key = tree.key_for_index(index)
                if key is not None and key not in self._tree_seen_roots:
                    self._tree_seen_roots.add(key)
                    self._tree_expanded.add(key)
            for key in self._tree_expanded:
                index = tree.index_for_key(key)
                if index.isValid():
                    self.window.table.setExpanded(index, True)
            selection = self.window.table.selectionModel()
            restored = QItemSelection()
            for key in tuple(self._tree_pending_selection):
                index = tree.index_for_key(key)
                if index.isValid():
                    restored.select(index, index)
                    self._tree_pending_selection.discard(key)
            changed = not restored.isEmpty()
            if changed:
                # One select call emits one selectionChanged signal.
                selection.select(
                    restored, QItemSelectionModel.Select | QItemSelectionModel.Rows
                )
            if self._tree_pending_current is not None:
                index = tree.index_for_key(self._tree_pending_current)
                if index.isValid():
                    selection.setCurrentIndex(index, QItemSelectionModel.NoUpdate)
                    self._tree_pending_current = None
                    changed = True
        finally:
            self._restoring_tree = False
        if changed:
            self.window._sync_detail_and_selection_actions()

    @property
    def restoring_tree(self) -> bool:
        """Return whether a tree selection restore is in progress."""
        return self._restoring_tree

    def _queue_tree_restore(self, *_args: Any) -> None:
        """Combine row insertions into one tree-state update."""
        if not self._tree_restore_timer.isActive():
            self._tree_restore_timer.start(0)

    def sync_tree_column(self) -> None:
        """Keep folder labels and expand controls in a visible column."""
        tree = self.tree_model
        if tree is None or self.window.table.model() is not tree:
            return
        table = self.window.table
        visible = [
            column
            for column in range(tree.columnCount())
            if not table.isColumnHidden(column)
        ]
        if not visible:
            return
        column = (
            tree.filename_column
            if tree.filename_column in visible
            else min(visible, key=table.header().visualIndex)
        )
        tree.set_folder_column(column)
        table.setTreePosition(column)

    def _finish_tree_loading(self) -> None:
        self._restore_tree_state()
        self._tree_pending_selection.clear()
        self._tree_pending_current = None

    def selection_changed(self) -> None:
        """Do not restore an older selection after the user chooses another row."""
        if not self._tree_resetting and not self._restoring_tree:
            self._tree_pending_selection.clear()
            self._tree_pending_current = None

    def update_columns(self) -> None:
        """Show the owning library alongside each combined filename."""
        window = self.window
        if self.tree_model is not None:
            self.tree_model.set_root_labels(self.labels())
        columns = build_columns(window.config.citation_styles)
        if self.enabled:
            columns = combined_columns(columns, self.labels())
        window.columns = columns
        window._applying_table_layout = True
        try:
            window.model.set_columns(columns)
        finally:
            window._applying_table_layout = False

    def registry(self) -> CombinedLibrary:
        """Return a fresh adapter for the currently selected registries."""
        return CombinedLibrary(
            {
                key: registry
                for key, registry in self.window.registries.items()
                if key in self.selected
            },
            labels=self.labels(),
        )

    def labels(self) -> dict[str, str]:
        """Return library labels, adding paths for repeated labels."""
        labels = {
            key: entry.label or Path(key).name or key
            for key, entry in self.window.root_entries.items()
        }
        counts = Counter(labels.values())
        return {
            key: f"{label} ({key})" if counts[label] > 1 else label
            for key, label in labels.items()
        }

    def refresh(self) -> None:
        """Reload selected libraries and report unavailable folders."""
        if not self.enabled:
            return
        window = self.window
        selected = {
            (str(record.get("root") or ""), record.get("location_id"))
            for record in window._selected_record_refs()
            if record.get("location_id") is not None
        }
        registry = self.registry()
        window.model.set_registry(registry, window.current_query)
        selected = {identity for identity in selected if identity[0] in self.selected}
        if selected:
            selection = window.table.selectionModel()
            seen = 0
            while selected:
                for row in range(seen, window.model.rowCount()):
                    record = window.model.record_at(row)
                    identity = (
                        str(record.get("root") or ""),
                        record.get("location_id"),
                    )
                    if identity in selected:
                        index = window._view_table_index(window.model.index(row, 0))
                        if not index.isValid():
                            continue
                        selection.select(
                            index, QItemSelectionModel.Select | QItemSelectionModel.Rows
                        )
                        selection.setCurrentIndex(index, QItemSelectionModel.NoUpdate)
                        selected.discard(identity)
                seen = window.model.rowCount()
                if not selected or not window.model.canFetchMore(QModelIndex()):
                    break
                window.model.fetchMore(QModelIndex())
        window._populate_views(registry)
        if registry.unavailable_roots:
            paths = "\n".join(registry.unavailable_roots)
            window.notice_bar.enqueue(
                Notice(
                    "combined-unavailable",
                    f"These libraries are unavailable:\n{paths}\n"
                    "Reconnect them and refresh the view.",
                    severity="warning",
                )
            )
        else:
            window.notice_bar.dismiss("combined-unavailable")
        window._update_status()

    def root_for(self, record: dict[str, Any]) -> Path | None:
        """Return a selected, registered root for one combined record."""
        key = str(record.get("root") or "")
        if key not in self.selected or key not in self.window.registries:
            return None
        return Path(key)

    def path_for(self, record: dict[str, Any]) -> Path | None:
        """Return a record's path only when it stays inside its own root."""
        root = self.root_for(record)
        relative = str(record.get("current_path") or "")
        if root is None or not relative or Path(relative).is_absolute():
            return None
        try:
            path = (root / relative).resolve()
        except (OSError, RuntimeError):
            return None
        return path if path.is_relative_to(root) else None

    def show_in_library(self, record: dict[str, Any]) -> None:
        """Leave the combined view and select one PDF in its library."""
        root = self.root_for(record)
        location_id = record.get("location_id")
        if root is None or location_id is None:
            return
        self.set_enabled(False)
        self.window._select_root(root)
        model = self.window.model
        seen = 0
        while True:
            for row in range(seen, model.rowCount()):
                candidate = model.record_at(row)
                if (
                    candidate is not None
                    and candidate.get("location_id") == location_id
                ):
                    self.window.table.setCurrentIndex(model.index(row, 0))
                    return
            if not model.canFetchMore():
                self.window.status.showMessage("PDF not found. Refresh its library.")
                return
            seen = model.rowCount()
            model.fetchMore()

    def _run_single_selection(
        self, identity: tuple[str, Any], action: Any, *, allow_recheck: bool = False
    ) -> None:
        """Run a menu action only for the PDF whose menu was opened."""
        target = self.window._selected_edit_target()
        if target is None or (str(target[0]), target[1].get("location_id")) != identity:
            self.window.status.showMessage(
                "The selection changed. Select the PDF and try again."
            )
            return
        reason = self.window._detail_edit_block_reason()
        if reason and not (allow_recheck and self.window._can_preempt_ads_recheck()):
            self.window.status.showMessage(reason)
            return
        action()

    def _run_library_action(self, root: Path, action: Any) -> None:
        """Run a folder-menu action only while its library remains available."""
        if (
            not self.enabled
            or str(root) not in self.selected
            or str(root) not in self.window.registries
            or not root.is_dir()
        ):
            self.window.status.showMessage("The selected library is unavailable.")
            return
        reason = self.window._detail_edit_block_reason()
        if reason:
            self.window.status.showMessage(reason)
            return
        action(root)

    def folder_menu(self, index: QModelIndex, position: Any) -> None:
        """Offer whole-library actions on a library folder."""
        tree = self.tree_model
        if tree is None or tree.node_kind(index) != "root":
            return
        key = tree.key_for_index(index)
        if key is None or not key[1]:
            return
        root = Path(str(key[1]))
        menu = QMenu(self.window)
        menu.setToolTipsVisible(True)
        reason = self.window._detail_edit_block_reason()
        if not reason and (str(root) not in self.selected or not root.is_dir()):
            reason = "The selected library is unavailable."
        identify = menu.addAction(
            "Identify Library…",
            lambda: self._run_library_action(
                root, lambda owner: self.window._identify(root=owner)
            ),
        )
        identify.setEnabled(not reason)
        identify.setToolTip(reason or f"Identify PDFs in {root}.")
        rename = menu.addAction(
            "Rename Library…",
            lambda: self._run_library_action(root, self.window._rename_library),
        )
        rename.setEnabled(not reason)
        rename.setToolTip(reason or f"Preview Rename settings for PDFs in {root}.")
        menu.exec(self.window.table.viewport().mapToGlobal(position))

    def table_menu(self, records: Sequence[dict[str, Any]], position: Any) -> None:
        """Offer only root-aware actions for a combined selection."""
        paths = [path for record in records if (path := self.path_for(record))]
        menu = QMenu(self.window)
        menu.setToolTipsVisible(True)
        open_action = menu.addAction(
            "Open PDFs" if len(records) != 1 else "Open PDF",
            lambda: [self.window._open_pdf_record(record) for record in records],
        )
        open_action.setEnabled(len(paths) == len(records))
        reveal = menu.addAction(
            "Reveal in File Manager", lambda: self.window._reveal_paths(paths)
        )
        reveal.setEnabled(bool(paths))
        copy = menu.addAction(
            "Copy Paths", lambda: self.window._copy_text("\n".join(map(str, paths)))
        )
        copy.setEnabled(bool(paths))
        roots = {self.root_for(record) for record in records}
        one_root = len(roots) == 1 and None not in roots and len(paths) == len(records)
        reason = self.window._detail_edit_block_reason()
        selection_reason = reason or (
            "" if one_root else "Select PDFs from one available library."
        )
        menu.addSeparator()
        identify = menu.addAction(
            "Identify Selected PDFs…",
            lambda: self.window._identify_combined_selection(records),
        )
        identify.setEnabled(not selection_reason)
        identify.setToolTip(
            selection_reason or "Identify these PDFs using their library's settings."
        )
        identify_rename = menu.addAction(
            "Identify && Rename Selected PDFs",
            lambda: self.window._identify_combined_selection(records, rename=True),
        )
        identify_rename.setEnabled(not selection_reason)
        identify_rename.setToolTip(
            selection_reason
            or "Identify these PDFs and apply their library's Rename settings."
        )
        menu.addSeparator()
        add_to_library = menu.addAction(
            "Add to ADS Library…", self.window._add_to_ads_library
        )
        add_to_library.setEnabled(self.window._can_add_to_ads_library())
        add_to_library.setToolTip(
            self.window._add_to_ads_library_block_reason()
            or "Add these papers to an ADS library."
        )
        menu.addAction(
            "Export Selection As…", lambda: self.window._export_papers(records)
        )
        if len(records) == 1:
            menu.addSeparator()
            identity = (
                str(records[0].get("root") or ""),
                records[0].get("location_id"),
            )
            update = menu.addAction(
                "Update by ID…",
                lambda: self._run_single_selection(
                    identity,
                    lambda: self.window._update_from_identifier(""),
                    allow_recheck=True,
                ),
            )
            update.setEnabled(self.window.update_selected_ads_action.isEnabled())
            update.setToolTip(self.window.update_selected_ads_action.toolTip())
            refresh = menu.addAction(
                "Refresh and Rename Selected from ADS",
                lambda: self._run_single_selection(
                    identity, self.window._refresh_selected_ads
                ),
            )
            refresh.setEnabled(self.window.refresh_selected_ads_action.isEnabled())
            refresh.setToolTip(self.window.refresh_selected_ads_action.toolTip())
            manual = menu.addAction(
                "Enter Details…",
                lambda: self._run_single_selection(
                    identity, self.window._enter_manual_metadata, allow_recheck=True
                ),
            )
            manual.setEnabled(self.window.detail.manual_metadata_button.isEnabled())
            manual.setToolTip(self.window.detail.manual_metadata_button.toolTip())
            menu.addSeparator()
            menu.addAction("Show in Library", lambda: self.show_in_library(records[0]))
        menu.exec(self.window.table.viewport().mapToGlobal(position))
