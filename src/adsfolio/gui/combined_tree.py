"""Group a combined file table into library and directory nodes.

The proxy does not inspect the filesystem. It loads the source model's existing
snapshot one page at a time on the Qt event loop. A source reset also resets
this proxy. Use `key_for_index` before the reset and `index_for_key` after
`loading_finished` to restore expanded folders and selected files.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import PurePath, PureWindowsPath
from typing import Any

from PySide6.QtCore import (
    QAbstractProxyModel,
    QModelIndex,
    QObject,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QStyle

from .models import FileTableModel

_LOG = logging.getLogger(__name__)
_ROOT_INDEX = QModelIndex()
_FILE_FLAGS = Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemNeverHasChildren
_FOLDER_FLAGS = Qt.ItemIsEnabled | Qt.ItemIsSelectable

# Keys contain only immutable values. A file key needs a location identifier.
TreeKey = tuple[Any, ...]


@dataclass
class _Node:
    kind: str
    key: TreeKey | None
    name: str
    parent: _Node | None = None
    row: int = 0
    source_row: int = -1
    record: dict[str, Any] | None = None
    signature: tuple[Any, ...] = ()
    children: list[_Node] = field(default_factory=list)


def _path_text(value: Any) -> str | None:
    """Return a nonempty path string, or `None` for an invalid value."""
    try:
        result = os.fspath(value)
    except TypeError:
        return None
    if not isinstance(result, str) or not result or "\x00" in result:
        return None
    return result


def _root(record: dict[str, Any]) -> str | None:
    return _path_text(record.get("root"))


def _directories(record: dict[str, Any]) -> tuple[str, ...]:
    """Return safe, lexical parent components without accessing the disk."""
    text = _path_text(record.get("current_path"))
    if text is None:
        return ()
    path = PurePath(text)
    windows_path = PureWindowsPath(text)
    if path.is_absolute() or windows_path.drive or windows_path.root:
        return ()
    if not path.parts or ".." in path.parts:
        return ()
    return path.parts[:-1]


def _file_key(record: dict[str, Any], root: str | None) -> TreeKey | None:
    location_id = record.get("location_id")
    if isinstance(location_id, bool) or not isinstance(location_id, (str, int)):
        return None
    return ("file", root, location_id)


def _signature(record: dict[str, Any]) -> tuple[Any, ...]:
    root = _root(record)
    return (root, _directories(record), _file_key(record, root))


class CombinedTreeProxyModel(QAbstractProxyModel):
    """Present a `FileTableModel` as library, directory, and file nodes.

    File roles and flags come from the source, except for filename presentation.
    The filename column shows basenames and provides full paths in tooltips
    and accessibility text. Folder labels also appear in that column.
    A folder has no source index or file record.

    Groups follow the first occurrence in source order. Files within each
    folder retain their source order. The source model handles sorting.
    Missing or unsafe paths stay as file nodes directly under their root.
    Records with an invalid root share an unknown-library node.

    `loading_finished` fires after all snapshot pages load. `loading_failed`
    fires if the source cannot advance or raises during pagination. Loading a
    page emits row insertions, not a model reset. A source reload resets both
    models, so callers must restore selection and expansion using stable keys.
    Duplicate file keys return an invalid lookup rather than selecting a file.
    Call `load_remaining` to retry after a pagination failure.
    """

    loading_finished = Signal()
    loading_failed = Signal(str)

    def __init__(
        self,
        source_model: FileTableModel | None = None,
        parent: QObject | None = None,
        *,
        root_labels: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(parent)
        self._root_labels = dict(root_labels or {})
        # Icon for file rows in the tree column, or `None` for no icon.
        self.file_icon: QIcon | None = None
        self._virtual_root = _Node("virtual", None, "")
        self._groups: dict[TreeKey, _Node] = {}
        self._files_by_row: dict[int, _Node] = {}
        self._keys: dict[TreeKey, list[_Node]] = {}
        self._filename_column = -1
        self._folder_column = -1
        self._resetting = False
        self._structural_reset = False
        self._generation = 0
        self._loading = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._load_next_page)
        if source_model is not None:
            self.setSourceModel(source_model)

    @property
    def filename_column(self) -> int:
        """Return the filename column, or -1 when the source has none."""
        return self._filename_column

    def set_folder_column(self, column: int) -> None:
        """Show folder labels in a visible table column."""
        if column == self._folder_column or not 0 <= column < self.columnCount():
            return
        previous = self._folder_column
        self._folder_column = column
        for node in self._groups.values():
            for logical in (previous, column):
                index = self._node_index(node, logical)
                if index.isValid():
                    self.dataChanged.emit(index, index, [])

    @property
    def is_loading(self) -> bool:
        """Return whether page loading is pending or active."""
        return self._loading

    def set_root_labels(self, labels: Mapping[str, str]) -> None:
        """Update library labels without resetting the model."""
        updated = dict(labels)
        if updated == self._root_labels:
            return
        self._root_labels = updated
        for node in self._virtual_root.children:
            node.name = self._root_label(node.key[1])
            index = self._node_index(node, self._folder_column)
            if index.isValid():
                self.dataChanged.emit(
                    index, index, [Qt.DisplayRole, Qt.AccessibleTextRole]
                )
        for node in self._files_by_row.values():
            index = self._node_index(node, self._filename_column)
            if index.isValid():
                self.dataChanged.emit(
                    index, index, [Qt.ToolTipRole, Qt.AccessibleTextRole]
                )

    def _root_label(self, root: str | None) -> str:
        return self._root_labels.get(root) or root or "Unknown library"

    def _file_description(self, record: dict[str, Any]) -> str:
        root = _root(record)
        label = self._root_label(root)
        if root and label != root and not label.endswith(f" ({root})"):
            library = f"{label} ({root})"
        else:
            library = label
        path = _path_text(record.get("current_path"))
        return f"{library} / {path}" if path else f"{library} / [path unavailable]"

    def setSourceModel(self, source_model: FileTableModel | None) -> None:
        """Replace the source and schedule loading of its snapshot pages."""
        old_source = self.sourceModel()
        if source_model is old_source:
            return
        self._timer.stop()
        self._loading = False
        self._generation += 1
        self.beginResetModel()
        if old_source is not None:
            self._disconnect_source(old_source)
        super().setSourceModel(source_model)
        if source_model is not None:
            self._connect_source(source_model)
        self._rebuild()
        self.endResetModel()
        if source_model is not None:
            self._schedule_loading()

    def _connect_source(self, source: FileTableModel) -> None:
        source.modelAboutToBeReset.connect(self._source_about_to_reset)
        source.modelReset.connect(self._source_reset)
        source.rowsAboutToBeInserted.connect(self._source_about_to_insert)
        source.rowsInserted.connect(self._source_inserted)
        source.rowsAboutToBeRemoved.connect(self._source_about_to_remove)
        source.rowsRemoved.connect(self._source_removed)
        source.dataChanged.connect(self._source_data_changed)
        source.layoutChanged.connect(self._source_layout_changed)
        source.headerDataChanged.connect(self._source_header_changed)

    def _disconnect_source(self, source: FileTableModel) -> None:
        source.modelAboutToBeReset.disconnect(self._source_about_to_reset)
        source.modelReset.disconnect(self._source_reset)
        source.rowsAboutToBeInserted.disconnect(self._source_about_to_insert)
        source.rowsInserted.disconnect(self._source_inserted)
        source.rowsAboutToBeRemoved.disconnect(self._source_about_to_remove)
        source.rowsRemoved.disconnect(self._source_removed)
        source.dataChanged.disconnect(self._source_data_changed)
        source.layoutChanged.disconnect(self._source_layout_changed)
        source.headerDataChanged.disconnect(self._source_header_changed)

    def _rebuild(self) -> None:
        self._virtual_root = _Node("virtual", None, "")
        self._groups.clear()
        self._files_by_row.clear()
        self._keys.clear()
        source = self.sourceModel()
        self._filename_column = (
            next(
                (
                    i
                    for i, column in enumerate(source.columns)
                    if column.key == "filename"
                ),
                -1,
            )
            if source is not None
            else -1
        )
        self._folder_column = self._filename_column
        if source is not None:
            for row in range(source.rowCount()):
                self._append_source_row(row, notify=False)

    def _node_index(self, node: _Node | None, column: int = 0) -> QModelIndex:
        if node is None or node is self._virtual_root:
            return QModelIndex()
        if not 0 <= column < self.columnCount():
            return QModelIndex()
        return self.createIndex(node.row, column, node)

    def _node(self, index: QModelIndex) -> _Node | None:
        if not index.isValid() or index.model() is not self:
            return None
        return index.internalPointer()

    def _append_node(self, parent: _Node, node: _Node, notify: bool) -> None:
        node.parent = parent
        node.row = len(parent.children)
        if notify:
            self.beginInsertRows(self._node_index(parent), node.row, node.row)
        parent.children.append(node)
        if node.key is not None:
            self._keys.setdefault(node.key, []).append(node)
        if node.kind == "file":
            self._files_by_row[node.source_row] = node
        else:
            self._groups[node.key] = node
        if notify:
            self.endInsertRows()

    def _ensure_parent(
        self, root: str | None, directories: tuple[str, ...], notify: bool
    ) -> _Node:
        root_key = ("root", root)
        parent = self._groups.get(root_key)
        if parent is None:
            parent = _Node("root", root_key, self._root_label(root))
            self._append_node(self._virtual_root, parent, notify)
        for depth, name in enumerate(directories, 1):
            key = ("directory", root, directories[:depth])
            child = self._groups.get(key)
            if child is None:
                child = _Node("directory", key, name)
                self._append_node(parent, child, notify)
            parent = child
        return parent

    def _append_source_row(self, row: int, notify: bool) -> None:
        source = self.sourceModel()
        record = source.record_at(row) if source is not None else None
        if record is None:
            return
        root, directories, file_key = _signature(record)
        parent = self._ensure_parent(root, directories, notify)
        self._append_node(
            parent,
            _Node(
                "file",
                file_key,
                "",
                source_row=row,
                record=record,
                signature=(root, directories, file_key),
            ),
            notify,
        )

    def _append_source_rows(self, first: int, last: int) -> None:
        """Insert consecutive files in one notification per folder run."""
        source = self.sourceModel()
        if source is None:
            return
        row = first
        while row <= last:
            record = source.record_at(row)
            if record is None:
                row += 1
                continue
            root, directories, _ = _signature(record)
            parent = self._ensure_parent(root, directories, notify=True)
            nodes = []
            while row <= last and record is not None:
                signature = _signature(record)
                if signature[:2] != (root, directories):
                    break
                nodes.append(
                    _Node(
                        "file",
                        signature[2],
                        "",
                        source_row=row,
                        record=record,
                        signature=signature,
                    )
                )
                row += 1
                record = source.record_at(row) if row <= last else None
            start = len(parent.children)
            self.beginInsertRows(
                self._node_index(parent), start, start + len(nodes) - 1
            )
            for node in nodes:
                self._append_node(parent, node, notify=False)
            self.endInsertRows()

    def _schedule_loading(self) -> None:
        self._loading = True
        self._timer.start(0)

    def load_remaining(self) -> None:
        """Schedule remaining pages, including a retry after a failure."""
        if (
            self.sourceModel() is not None
            and not self._resetting
            and not self._structural_reset
            and not self._loading
        ):
            self._schedule_loading()

    def _load_next_page(self) -> None:
        source = self.sourceModel()
        if source is None or self._resetting or self._structural_reset:
            self._loading = False
            return
        generation = self._generation
        try:
            if source.canFetchMore(QModelIndex()):
                before = source.rowCount()
                source.fetchMore(QModelIndex())
                if generation != self._generation or source is not self.sourceModel():
                    return
                if source.rowCount() <= before:
                    raise RuntimeError(
                        "The source did not load the next snapshot page."
                    )
            if source.canFetchMore(QModelIndex()):
                self._timer.start(0)
                return
        # A Qt timer must report source failures instead of losing the callback.
        except Exception as exc:  # noqa: BLE001
            self._loading = False
            _LOG.exception("Combined tree snapshot loading failed")
            self.loading_failed.emit(str(exc))
            return
        self._loading = False
        self.loading_finished.emit()

    def _source_about_to_reset(self) -> None:
        self._timer.stop()
        self._loading = False
        self._generation += 1
        self._resetting = True
        self.beginResetModel()

    def _source_reset(self) -> None:
        self._rebuild()
        self._resetting = False
        self.endResetModel()
        self._schedule_loading()

    def _source_about_to_insert(
        self, parent: QModelIndex, first: int, last: int
    ) -> None:
        if parent.isValid() or first != len(self._files_by_row):
            self._generation += 1
            self._structural_reset = True
            self.beginResetModel()

    def _source_inserted(self, parent: QModelIndex, first: int, last: int) -> None:
        if self._structural_reset:
            self._rebuild()
            self._structural_reset = False
            self.endResetModel()
            self._schedule_loading()
            return
        self._append_source_rows(first, last)

    def _source_about_to_remove(
        self, parent: QModelIndex, first: int, last: int
    ) -> None:
        self._generation += 1
        self._structural_reset = True
        self.beginResetModel()

    def _source_removed(self, parent: QModelIndex, first: int, last: int) -> None:
        self._rebuild()
        self._structural_reset = False
        self.endResetModel()
        self._schedule_loading()

    def _reset_from_changed_source(self) -> None:
        self._generation += 1
        self.beginResetModel()
        self._rebuild()
        self.endResetModel()
        self._schedule_loading()

    def _source_data_changed(
        self, top_left: QModelIndex, bottom_right: QModelIndex, roles: list[int]
    ) -> None:
        source = self.sourceModel()
        if source is None or top_left.parent().isValid():
            return
        for row in range(top_left.row(), bottom_right.row() + 1):
            node = self._files_by_row.get(row)
            record = source.record_at(row)
            if node is None or record is None or _signature(record) != node.signature:
                self._reset_from_changed_source()
                return
        for row in range(top_left.row(), bottom_right.row() + 1):
            node = self._files_by_row[row]
            node.record = source.record_at(row)
            self.dataChanged.emit(
                self._node_index(node, top_left.column()),
                self._node_index(node, bottom_right.column()),
                roles,
            )

    def _source_layout_changed(self, *args: Any) -> None:
        source = self.sourceModel()
        if source is None:
            return
        if source.rowCount() != len(self._files_by_row) or any(
            source.record_at(row) is not node.record
            or _signature(source.record_at(row)) != node.signature
            for row, node in self._files_by_row.items()
        ):
            self._reset_from_changed_source()
            return
        if self.columnCount():
            for node in self._files_by_row.values():
                self.dataChanged.emit(
                    self._node_index(node),
                    self._node_index(node, self.columnCount() - 1),
                    [],
                )

    def _source_header_changed(
        self, orientation: Qt.Orientation, first: int, last: int
    ) -> None:
        if orientation == Qt.Horizontal:
            self.headerDataChanged.emit(orientation, first, last)

    def index(
        self, row: int, column: int, parent: QModelIndex = _ROOT_INDEX
    ) -> QModelIndex:
        if parent.isValid() and (parent.model() is not self or parent.column() != 0):
            return QModelIndex()
        parent_node = self._node(parent) if parent.isValid() else self._virtual_root
        if parent_node is None or not 0 <= row < len(parent_node.children):
            return QModelIndex()
        return self._node_index(parent_node.children[row], column)

    def parent(self, index: QModelIndex) -> QModelIndex:
        node = self._node(index)
        return self._node_index(node.parent if node is not None else None)

    def rowCount(self, parent: QModelIndex = _ROOT_INDEX) -> int:
        if parent.isValid() and (parent.model() is not self or parent.column() != 0):
            return 0
        node = self._node(parent) if parent.isValid() else self._virtual_root
        return len(node.children) if node is not None else 0

    def columnCount(self, parent: QModelIndex = _ROOT_INDEX) -> int:
        if parent.isValid() and parent.model() is not self:
            return 0
        source = self.sourceModel()
        return source.columnCount() if source is not None else 0

    def mapToSource(self, proxy_index: QModelIndex) -> QModelIndex:
        node = self._node(proxy_index)
        source = self.sourceModel()
        if node is None or node.kind != "file" or source is None:
            return QModelIndex()
        return source.index(node.source_row, proxy_index.column())

    def mapFromSource(self, source_index: QModelIndex) -> QModelIndex:
        source = self.sourceModel()
        if (
            source is None
            or not source_index.isValid()
            or source_index.model() is not source
            or source_index.parent().isValid()
        ):
            return QModelIndex()
        return self._node_index(
            self._files_by_row.get(source_index.row()), source_index.column()
        )

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        node = self._node(index)
        if node is None:
            return None
        if node.kind == "file":
            source = self.sourceModel()
            if (
                role == Qt.DecorationRole
                and index.column() == self._folder_column
                and self.file_icon is not None
            ):
                return self.file_icon
            if index.column() == self._filename_column and node.record is not None:
                if role == Qt.DisplayRole:
                    path = _path_text(node.record.get("current_path"))
                    if not path:
                        return ""
                    windows_path = PureWindowsPath(path)
                    if windows_path.drive or windows_path.root:
                        return windows_path.name
                    return PurePath(path).name
                if role in (Qt.ToolTipRole, Qt.AccessibleTextRole):
                    return self._file_description(node.record)
            return (
                source.data(self.mapToSource(index), role)
                if source is not None
                else None
            )
        if index.column() == self._folder_column and role == Qt.DecorationRole:
            return QApplication.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        if index.column() == self._folder_column and role == Qt.ToolTipRole:
            key = node.key
            if key is not None and key[1]:
                root = PurePath(key[1])
                return (
                    str(root.joinpath(*key[2]))
                    if node.kind == "directory"
                    else str(root)
                )
            return "Library path is unavailable."
        if index.column() == self._folder_column and role in (
            Qt.DisplayRole,
            Qt.AccessibleTextRole,
        ):
            return node.name
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        node = self._node(index)
        if node is None:
            return Qt.NoItemFlags
        if node.kind == "file":
            # `FileTableModel` keeps the default table flags for every cell.
            # Qt asks for flags once per cell during selection work, so skip
            # the round trip through the source model.
            return _FILE_FLAGS if self.sourceModel() is not None else Qt.NoItemFlags
        return _FOLDER_FLAGS

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole
    ) -> Any:
        source = self.sourceModel()
        return (
            source.headerData(section, orientation, role)
            if source is not None
            else None
        )

    def sort(self, column: int, order: Qt.SortOrder = Qt.AscendingOrder) -> None:
        """Delegate sorting to the source model."""
        source = self.sourceModel()
        if source is not None:
            source.sort(column, order)

    def canFetchMore(self, parent: QModelIndex = _ROOT_INDEX) -> bool:
        """Keep page loading under the proxy's event-loop scheduler."""
        return False

    def fetchMore(self, parent: QModelIndex = _ROOT_INDEX) -> None:
        """Leave page loading to the proxy's event-loop scheduler."""

    def node_kind(self, index: QModelIndex) -> str | None:
        """Return ``root``, ``directory``, or ``file`` for a proxy index."""
        node = self._node(index)
        return node.kind if node is not None else None

    def is_folder_index(self, index: QModelIndex) -> bool:
        """Return whether the index identifies a root or directory."""
        return self.node_kind(index) in ("root", "directory")

    def record_for_index(self, index: QModelIndex) -> dict[str, Any] | None:
        """Return the original source record for a file, or `None`."""
        source_index = self.mapToSource(index)
        source = self.sourceModel()
        if source is None or not source_index.isValid():
            return None
        return source.record_at(source_index.row())

    def key_for_index(self, index: QModelIndex) -> TreeKey | None:
        """Return a stable node key, or `None` for a file without an ID."""
        node = self._node(index)
        return node.key if node is not None else None

    def index_for_key(self, key: TreeKey | None, column: int = 0) -> QModelIndex:
        """Find a unique node by key, or return an invalid index."""
        if key is None:
            return QModelIndex()
        try:
            nodes = self._keys.get(key, [])
        except TypeError:
            return QModelIndex()
        return self._node_index(nodes[0], column) if len(nodes) == 1 else QModelIndex()
