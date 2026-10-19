"""Qt models for the paged PDF library table."""

from __future__ import annotations

import sqlite3
import sys
from collections.abc import Sequence
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QEvent,
    QModelIndex,
    QObject,
    QPointF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QInputDevice,
    QPainter,
    QPalette,
    QPolygonF,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeView,
)

from ..pdf.cli_common import is_resolved_record
from ..pdf.readiness import metadata_ready
from .library import (
    BASE_COLUMNS,
    ISO8601_TIMESTAMP_FORMAT,
    STALE_VERIFICATION_TOOLTIP,
    Column,
    FileQuery,
    format_first_seen,
    format_mtime,
    verification_stale,
)

PAGE_SIZE = 500


class LibraryTableView(QTreeView):
    """Scroll horizontally in smaller steps while Shift is held."""

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._shift_wheel_remainder = 0
        for widget in (
            self.header().viewport(),
            self.horizontalScrollBar(),
            self.verticalScrollBar(),
        ):
            widget.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Apply the same Shift-wheel speed over the header and scrollbars."""
        if event.type() == QEvent.Wheel and event.modifiers() & Qt.ShiftModifier:
            self.wheelEvent(event)
            return True
        return super().eventFilter(watched, event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Use one tenth of a wheel page or trackpad pixel displacement."""
        if not event.modifiers() & Qt.ShiftModifier:
            self._shift_wheel_remainder = 0
            super().wheelEvent(event)
            return
        if event.phase() == Qt.ScrollBegin:
            self._shift_wheel_remainder = 0
        scrollbar = self.horizontalScrollBar()
        pixels = event.pixelDelta()
        angle = event.angleDelta()
        precise = (
            event.phase() != Qt.NoScrollPhase
            or event.source() == Qt.MouseEventSynthesizedBySystem
            or (
                sys.platform != "darwin"
                and event.pointingDevice().capabilities()
                & QInputDevice.Capability.PixelScroll
            )
        )
        # Cocoa also supplies estimated pixel deltas for ordinary mouse wheels.
        if not pixels.isNull() and (precise or angle.isNull()):
            delta = pixels.x() if abs(pixels.x()) > abs(pixels.y()) else pixels.y()
            distance = delta * 120
        else:
            page = scrollbar.pageStep()
            delta = angle.x() if abs(angle.x()) > abs(angle.y()) else angle.y()
            distance = max(-120 * page, min(120 * page, delta * page))
        if scrollbar.invertedControls():
            distance = -distance
        if (
            scrollbar.value() == scrollbar.minimum()
            and distance < 0
            or scrollbar.value() == scrollbar.maximum()
            and distance > 0
        ):
            self._shift_wheel_remainder = 0
            event.accept()
            return
        if distance * self._shift_wheel_remainder < 0:
            self._shift_wheel_remainder = 0
        scaled = distance + self._shift_wheel_remainder
        # 120 angle units per notch and a factor of ten keep subpixel sums exact.
        step = abs(scaled) // 1200
        if scaled < 0:
            step = -step
        self._shift_wheel_remainder = scaled - step * 1200
        before = scrollbar.value()
        scrollbar.setValue(before + step)
        if scrollbar.value() != before + step or event.phase() == Qt.ScrollEnd:
            self._shift_wheel_remainder = 0
        event.accept()


class RowHeightDelegate(QStyledItemDelegate):
    """Apply an optional fixed height to table item geometry."""

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._row_height: int | None = None

    def set_row_height(self, height: int | None) -> None:
        """Set the fixed table-row height and refresh delegate geometry."""
        self._row_height = height
        self.sizeHintChanged.emit(QModelIndex())

    def sizeHint(
        self,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> QSize:
        """Return the native size or the configured fixed row height."""
        size = super().sizeHint(option, index)
        if self._row_height is not None:
            size.setHeight(self._row_height)
        return size


class FirstAuthorDelegate(RowHeightDelegate):
    """Draw the first author's surname in bold without changing the model text."""

    @staticmethod
    def split_author(value: str) -> tuple[str, str]:
        """Split an ADS-style author name into surname and remaining text."""
        surname, separator, remainder = value.partition(",")
        return surname, f"{separator}{remainder}" if separator else ""

    def paint(
        self,
        painter: Any,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        """Paint only the surname in bold while retaining native row styling."""
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        surname, remainder = self.split_author(str(index.data(Qt.DisplayRole) or ""))
        styled.text = ""
        style = (
            styled.widget.style() if styled.widget is not None else QApplication.style()
        )
        style.drawControl(QStyle.CE_ItemViewItem, styled, painter, styled.widget)
        text_rect = style.subElementRect(
            QStyle.SE_ItemViewItemText, styled, styled.widget
        )
        painter.save()
        painter.setClipRect(text_rect)
        color_role = (
            QPalette.HighlightedText
            if styled.state & QStyle.State_Selected
            else QPalette.Text
        )
        painter.setPen(styled.palette.color(color_role))
        bold_font = QFont(styled.font)
        bold_font.setBold(True)
        painter.setFont(bold_font)
        painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, surname)
        offset = painter.fontMetrics().horizontalAdvance(surname)
        painter.setFont(styled.font)
        painter.drawText(
            text_rect.adjusted(offset, 0, 0, 0),
            Qt.AlignLeft | Qt.AlignVCenter,
            remainder,
        )
        painter.restore()


class LinkDelegate(RowHeightDelegate):
    """Render external links as text or compact branded badges."""

    def __init__(self, brand: str, parent: Any = None) -> None:
        super().__init__(parent)
        self.brand = brand

    @staticmethod
    def requires_icon(text: str, available_width: int, font: QFont) -> bool:
        """Return whether the link text needs compact rendering."""
        if not text:
            return False
        return QFontMetrics(font).horizontalAdvance(text) > max(0, available_width - 12)

    def paint(
        self,
        painter: Any,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        """Paint a full link or a compact badge according to cell width."""
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        text = str(index.data(Qt.DisplayRole) or "")
        style = (
            styled.widget.style() if styled.widget is not None else QApplication.style()
        )
        if not self.requires_icon(text, styled.rect.width(), styled.font):
            styled.font.setUnderline(bool(text))
            style.drawControl(QStyle.CE_ItemViewItem, styled, painter, styled.widget)
            return

        styled.text = ""
        style.drawControl(QStyle.CE_ItemViewItem, styled, painter, styled.widget)
        badge = styled.rect.adjusted(4, 3, -4, -3)
        if badge.width() <= 0 or badge.height() <= 0:
            return
        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#4267a8" if self.brand == "ADS" else "#9d2d2d"))
        painter.drawRoundedRect(badge, 3, 3)
        text_color = (
            styled.palette.color(QPalette.HighlightedText)
            if styled.state & QStyle.State_Selected
            else QColor("white")
        )
        painter.setPen(text_color)
        badge_font = QFont(styled.font)
        badge_font.setBold(True)
        painter.setFont(badge_font)
        painter.drawText(badge, Qt.AlignCenter, self.brand)
        painter.restore()


class ResolutionDelegate(RowHeightDelegate):
    """Draw a compact green marker for records resolved through ADS."""

    def paint(
        self,
        painter: Any,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        """Preserve native item styling, then draw the resolved-state marker."""
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        style = (
            styled.widget.style() if styled.widget is not None else QApplication.style()
        )
        style.drawControl(QStyle.CE_ItemViewItem, styled, painter, styled.widget)
        model = index.model()
        record = model.record_at(index.row()) if hasattr(model, "record_at") else None
        if not isinstance(record, dict):
            return
        marker_size = min(10, styled.rect.width() - 4, styled.rect.height() - 4)
        if marker_size <= 0:
            return
        marker = styled.rect.center()
        if verification_stale(record):
            half = marker_size / 2.0
            triangle = QPolygonF(
                [
                    QPointF(marker.x(), marker.y() - half),
                    QPointF(marker.x() + half, marker.y() + half),
                    QPointF(marker.x() - half, marker.y() + half),
                ]
            )
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#d97706"))
            painter.drawPolygon(triangle)
            painter.restore()
            return
        if not (is_resolved_record(record) or metadata_ready(record)):
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#2e7d32"))
        painter.drawEllipse(
            marker.x() - marker_size // 2,
            marker.y() - marker_size // 2,
            marker_size,
            marker_size,
        )
        painter.restore()


class FileTableModel(QAbstractTableModel):
    """Table model that pages one sorted snapshot of a ``PdfRegistry`` query."""

    query_failed = Signal(str)

    def __init__(
        self,
        registry: Any | None,
        query: FileQuery,
        columns: Sequence[Column] | Any = BASE_COLUMNS,
        parent: Any = None,
    ) -> None:
        if (
            parent is None
            and columns is not BASE_COLUMNS
            and not isinstance(columns, (list, tuple))
        ):
            parent = columns
            columns = BASE_COLUMNS
        super().__init__(parent)
        self._registry = registry
        self._query = query
        self._columns: tuple[Column, ...] = tuple(columns)
        self._snapshot: list[dict[str, Any]] = []
        self._rows: list[dict[str, Any]] = []
        self._total = 0
        self._column_fonts: dict[str, QFont] = {}
        self._column_colors: dict[str, QColor] = {}
        self._timestamp_format = ISO8601_TIMESTAMP_FORMAT
        if registry is not None:
            self._reload()

    @property
    def total(self) -> int:
        """Return the total number of rows matching the current query."""
        return self._total

    @property
    def columns(self) -> tuple[Column, ...]:
        """Return the active table column definitions."""
        return self._columns

    @property
    def query(self) -> FileQuery:
        """Return the active file query."""
        return self._query

    def set_columns(self, columns: Sequence[Column]) -> None:
        """Replace the active table columns and reload column layout.

        Parameters
        ----------
        columns : Sequence[Column]
            New sequence of column definitions.
        """
        normalized = tuple(columns)
        self.beginResetModel()
        self._columns = normalized
        self.endResetModel()

    def set_registry(
        self, registry: Any | None, query: FileQuery | None = None
    ) -> None:
        """Replace the registry, and optionally the query, with one reload.

        Parameters
        ----------
        registry : object or None
            New registry, or `None` to empty the model.
        query : FileQuery, optional
            New query. If omitted, the current query is kept.
        """
        self._registry = registry
        if query is not None:
            self._query = query
        self._reload()

    def set_query(self, query: FileQuery) -> None:
        """Replace the query and reload the model."""
        self._query = query
        self._reload()

    def record_at(self, row: int) -> dict[str, Any] | None:
        """Return the loaded record at `row`, or `None` when unavailable."""
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    def set_column_fonts(
        self,
        fonts: dict[str, QFont],
        colors: dict[str, QColor] | None = None,
    ) -> None:
        """Apply optional fixed-width fonts and text colors by column key."""
        self._column_fonts = fonts
        self._column_colors = colors or {}
        self.layoutChanged.emit()

    def set_timestamp_format(self, timestamp_format: str) -> None:
        """Apply an ISO 8601 or custom ``strftime`` timestamp format."""
        self._timestamp_format = timestamp_format
        self.layoutChanged.emit()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        """Return the number of rows currently loaded into the model."""
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        """Return the number of visible columns."""
        return 0 if parent.isValid() else len(self._columns)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        """Return display and presentation data for `index`."""
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        if not (0 <= index.column() < len(self._columns)):
            return None
        record = self._rows[index.row()]
        column = self._columns[index.column()]
        if column.key.startswith("citation:"):
            if role in (Qt.DisplayRole, Qt.ToolTipRole, Qt.AccessibleTextRole):
                return column.getter(record)
        if column.key == "status":
            if role == Qt.DisplayRole:
                return ""
            if role in (Qt.ToolTipRole, Qt.AccessibleTextRole):
                if verification_stale(record):
                    return STALE_VERIFICATION_TOOLTIP
                if is_resolved_record(record):
                    return "ADS record linked"
                if metadata_ready(record):
                    return "Metadata ready for renaming. No completed ADS lookup."
                if record.get("bibcode"):
                    return (
                        "Bibcode present. ADS lookup is pending. "
                        "Run Identify to check it in a batch. Existing metadata is retained."
                    )
                return "No completed ADS lookup. Run Identify or enter metadata."
        if role in (Qt.ToolTipRole, Qt.AccessibleTextRole) and column.key in {
            "bibcode",
            "arxiv",
        }:
            value = column.getter(record)
            if role == Qt.AccessibleTextRole:
                return value
            destination = "ADS" if column.key == "bibcode" else "arXiv"
            return f"Open in {destination}: {value}" if value else ""
        if role == Qt.DisplayRole:
            if column.key == "first_seen_at":
                return format_first_seen(record, self._timestamp_format)
            if column.key == "mtime_ns":
                return format_mtime(record, self._timestamp_format)
            return column.getter(record)
        if role == Qt.ToolTipRole and column.key == "title":
            metadata = record.get("metadata") or {}
            title = metadata.get("title") if isinstance(metadata, dict) else None
            if isinstance(title, list):
                return "\n".join(str(item) for item in title)
            return str(title or record.get("title") or "")
        if role == Qt.ForegroundRole:
            return self._column_colors.get(
                column.key,
                Qt.gray if record.get("status") == "unresolved" else None,
            )
        if role == Qt.FontRole:
            return self._column_fonts.get(column.key)
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.DisplayRole,
    ) -> Any:
        """Return column headers."""
        if role != Qt.DisplayRole or orientation != Qt.Horizontal:
            return None
        return (
            self._columns[section].header if 0 <= section < len(self._columns) else None
        )

    def canFetchMore(self, parent: QModelIndex = QModelIndex()) -> bool:
        """Return whether the snapshot holds rows not yet shown."""
        return not parent.isValid() and len(self._rows) < len(self._snapshot)

    def fetchMore(self, parent: QModelIndex = QModelIndex()) -> None:
        """Show the next ``PAGE_SIZE`` snapshot rows without querying."""
        if parent.isValid() or not self.canFetchMore():
            return
        first = len(self._rows)
        rows = self._snapshot[first : first + PAGE_SIZE]
        self.beginInsertRows(QModelIndex(), first, first + len(rows) - 1)
        self._rows.extend(rows)
        self.endInsertRows()

    def sort(self, column: int, order: Qt.SortOrder = Qt.AscendingOrder) -> None:
        """Re-query SQLite using the selected sortable column."""
        if not 0 <= column < len(self._columns):
            return
        target_column = self._columns[column]
        if not target_column.sortable:
            return
        self.set_query(
            FileQuery(
                view=self._query.view,
                search=self._query.search,
                order_by=target_column.key,
                descending=order == Qt.DescendingOrder,
                secondary_order_by=self._query.secondary_order_by,
                tertiary_order_by=self._query.tertiary_order_by,
            )
        )

    def _reload(self) -> None:
        """Run the query once and reset the model to its first page.

        The full sorted result is kept as a snapshot so ``fetchMore`` does not
        query again. A failed query leaves the model empty instead of showing
        rows from the previous registry or query.
        """
        self.beginResetModel()
        try:
            self._snapshot = []
            self._rows = []
            self._total = 0
            if self._registry is not None:
                snapshot = self._query_snapshot()
                if snapshot is not None:
                    self._snapshot = snapshot
                    self._total = len(snapshot)
                    self._rows = snapshot[:PAGE_SIZE]
        finally:
            self.endResetModel()

    def _query_snapshot(self) -> list[dict[str, Any]] | None:
        try:
            return self._registry.query_files(
                **self._query.to_filters(),
                order_by=self._query.order_by,
                descending=self._query.descending,
                secondary_order_by=self._query.secondary_order_by,
                tertiary_order_by=self._query.tertiary_order_by,
            )
        except sqlite3.OperationalError as exc:
            self._report_query_error(exc)
            return None

    def _report_query_error(self, exc: sqlite3.OperationalError) -> None:
        """Surface a transient registry lock without propagating through Qt."""
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            self.query_failed.emit("Library is busy; try again in a moment.")
            return
        raise exc
