"""Qt models for the paged PDF library table."""

from __future__ import annotations

import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QEvent,
    QModelIndex,
    QObject,
    QPoint,
    QPointF,
    QSize,
    Qt,
    QTimerEvent,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QInputDevice,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPalette,
    QPolygonF,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeView,
)

from ..pdf.classification import current_identity_proof
from ..pdf.search import parse_query, search_spans
from .library import (
    BASE_COLUMNS,
    ISO8601_TIMESTAMP_FORMAT,
    SAVED_AUTHOR_COUNT_TOOLTIP,
    SAVED_CITATION_COUNT_TOOLTIP,
    STALE_VERIFICATION_TOOLTIP,
    Column,
    FileQuery,
    ResolutionState,
    format_first_seen,
    format_mtime,
    resolution_state,
)

PAGE_SIZE = 500
# Green marks ADS links. Purple marks external identity or usable local metadata.
# Amber marks records that need identification or review.
RESOLUTION_COLORS = {
    ResolutionState.CHANGED: "#d97706",
    ResolutionState.ADS_LINKED: "#2e7d32",
    ResolutionState.IDENTIFIED_EXTERNAL: "#7b1fa2",
    ResolutionState.LOCAL_METADATA: "#7b1fa2",
    ResolutionState.UNRESOLVED: "#d97706",
}
COUNT_COLUMN_TOOLTIPS = {
    "author_count": SAVED_AUTHOR_COUNT_TOOLTIP,
    "citation_count": SAVED_CITATION_COUNT_TOOLTIP,
}
COLUMN_ALIGNMENT_FLAGS = {
    "left": Qt.AlignLeft,
    "center": Qt.AlignHCenter,
    "right": Qt.AlignRight,
}


def resolution_tooltip(record: dict[str, Any]) -> str:
    """Return the status-marker description for one record."""
    state = resolution_state(record)
    if state is ResolutionState.CHANGED:
        return STALE_VERIFICATION_TOOLTIP
    if state is ResolutionState.ADS_LINKED:
        return "ADS record linked"
    if state is ResolutionState.IDENTIFIED_EXTERNAL:
        proof = current_identity_proof(record)
        if proof is not None and proof.get("status") == "manual_confirmed":
            return "Manually identified. No ADS record linked."
        return "Identified by a verified PDF identifier. No ADS record linked."
    if state is ResolutionState.LOCAL_METADATA:
        return "Metadata available. Confirm the PDF identity. No ADS record linked."
    if record.get("bibcode"):
        return (
            "Bibcode present. ADS lookup is pending. "
            "Run Identify to check it in a batch. Existing metadata is retained."
        )
    return "Needs identification. Run Identify or use Update by ID."


class LibraryTableView(QTreeView):
    """Scroll horizontally in smaller steps for discrete wheels and while Shift is held."""

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._shift_wheel_remainder = 0
        # True while a click or key press handles a row change.
        self._user_row_change = False
        for widget in (
            self.header().viewport(),
            self.horizontalScrollBar(),
            self.verticalScrollBar(),
        ):
            widget.installEventFilter(self)

    @staticmethod
    def _is_precise(event: QWheelEvent) -> bool:
        """Return whether wheel input comes from a precise trackpad or touch device."""
        return (
            event.phase() != Qt.NoScrollPhase
            or event.source() == Qt.MouseEventSynthesizedBySystem
            or (
                sys.platform != "darwin"
                and bool(
                    event.pointingDevice().capabilities()
                    & QInputDevice.Capability.PixelScroll
                )
            )
        )

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Apply the same discrete horizontal speed over the header and scrollbars."""
        if event.type() == QEvent.Wheel:
            is_shift = bool(event.modifiers() & Qt.ShiftModifier)
            angle = event.angleDelta()
            is_discrete_horizontal = angle.x() != 0 and not self._is_precise(event)
            if is_shift or is_discrete_horizontal:
                self.wheelEvent(event)
                return True
        return super().eventFilter(watched, event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Use one tenth of a wheel page for discrete horizontal or Shift scrolling."""
        is_shift = bool(event.modifiers() & Qt.ShiftModifier)
        angle = event.angleDelta()
        precise = self._is_precise(event)
        is_discrete_horizontal = angle.x() != 0 and not precise
        if not is_shift and not is_discrete_horizontal:
            self._shift_wheel_remainder = 0
            super().wheelEvent(event)
            return
        if event.phase() == Qt.ScrollBegin:
            self._shift_wheel_remainder = 0
        pixels = event.pixelDelta()
        has_vertical = pixels.y() != 0 or angle.y() != 0
        if not is_shift and has_vertical:
            vertical_event = QWheelEvent(
                event.position(),
                event.globalPosition(),
                QPoint(0, pixels.y()),
                QPoint(0, angle.y()),
                event.buttons(),
                event.modifiers(),
                event.phase(),
                event.inverted(),
                event.source(),
                event.pointingDevice(),
            )
            super().wheelEvent(vertical_event)
        scrollbar = self.horizontalScrollBar()
        pixels = event.pixelDelta()
        angle = event.angleDelta()
        if is_shift:
            # Cocoa also supplies estimated pixel deltas for ordinary mouse wheels.
            if not pixels.isNull() and (precise or angle.isNull()):
                delta = pixels.x() if abs(pixels.x()) > abs(pixels.y()) else pixels.y()
                distance = delta * 120
            else:
                page = scrollbar.pageStep()
                delta = angle.x() if abs(angle.x()) > abs(angle.y()) else angle.y()
                distance = max(-120 * page, min(120 * page, delta * page))
        else:
            page = scrollbar.pageStep()
            distance = max(-120 * page, min(120 * page, angle.x() * page))
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

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Select the clicked row without scrolling sideways."""
        self._user_row_change = True
        try:
            super().mousePressEvent(event)
        finally:
            self._user_row_change = False

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """Move between rows without scrolling sideways."""
        self._user_row_change = True
        try:
            super().keyPressEvent(event)
        finally:
            self._user_row_change = False

    def timerEvent(self, event: QTimerEvent) -> None:  # noqa: N802
        """Keep the horizontal position when Qt's delayed click scroll runs.

        After a mouse press, Qt waits for the double-click interval plus 100 ms
        and then scrolls to the clicked cell from this timer.
        """
        self._user_row_change = True
        try:
            super().timerEvent(event)
        finally:
            self._user_row_change = False

    def scrollTo(  # noqa: N802 - Qt override
        self,
        index: QModelIndex,
        hint: QAbstractItemView.ScrollHint = QAbstractItemView.EnsureVisible,
    ) -> None:
        """Scroll to an index, and keep the horizontal position for user row moves.

        The view selects whole rows. After a click or key press, Qt's default
        also scrolls sideways to show the current cell, so a click on a partly
        hidden column moved the table. A direct call still scrolls to the column.
        """
        if not self._user_row_change:
            super().scrollTo(index, hint)
            return
        scrollbar = self.horizontalScrollBar()
        horizontal = scrollbar.value()
        super().scrollTo(index, hint)
        scrollbar.setValue(horizontal)


# Search fields that each table column displays. A field term highlights
# only in these columns.
COLUMN_SEARCH_FIELDS: dict[str, frozenset[str]] = {
    "title": frozenset({"title"}),
    "author": frozenset({"author", "first_author"}),
    "year": frozenset({"year"}),
    "pub": frozenset({"journal"}),
    "bibcode": frozenset({"bibcode"}),
    "arxiv": frozenset({"identifier"}),
    "filename": frozenset({"filename"}),
}
# Translucent marker color. The cell text stays readable below it.
HIGHLIGHT_COLOR = QColor(255, 204, 0, 120)


class SearchHighlight:
    """Hold the active search for highlighting matched text in table cells.

    A term with a field name highlights in the columns that display that
    field. A plain term highlights in the columns of the default fields, or
    in every text column when all fields are searched.
    """

    def __init__(self) -> None:
        self.enabled = True
        self.query = ""
        self.exact = False
        self.wildcards = False
        self.default_fields: tuple[str, ...] = ()
        self.column_keys: tuple[str, ...] = ()

    def column_key(self, column: int) -> str:
        """Return the key of a model column, or ``""`` when it is unknown."""
        return self.column_keys[column] if 0 <= column < len(self.column_keys) else ""

    def spans(self, column_key: str, text: str) -> list[tuple[int, int]]:
        """Return sorted, merged ``(start, end)`` spans to mark in `text`."""
        if not self.enabled or not text or not self.query.strip():
            return []
        shown = COLUMN_SEARCH_FIELDS.get(column_key)
        text_column = shown is not None or column_key.startswith("citation:")
        found: list[tuple[int, int]] = []
        for term in parse_query(self.query):
            if not term.text:
                continue
            if term.field is not None:
                applies = shown is not None and term.field in shown
            elif self.default_fields:
                applies = shown is not None and bool(shown & set(self.default_fields))
            else:
                applies = text_column
            if applies:
                found.extend(
                    search_spans(
                        text, term.text, exact=self.exact, wildcards=self.wildcards
                    )
                )
        merged: list[tuple[int, int]] = []
        for start, end in sorted(found):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        return merged


def paint_highlights(
    painter: QPainter,
    rect: Any,
    spans: Sequence[tuple[int, int]],
    advance: Any,
    visible: int,
    height: int,
) -> None:
    """Draw translucent marks behind the visible part of matched text.

    Parameters
    ----------
    painter : QPainter
        Painter of the cell.
    rect : QRect
        Rectangle of the cell text. Marks are clipped to it.
    spans : Sequence[tuple[int, int]]
        Character spans to mark.
    advance : Callable[[int], int]
        Return the x offset of a character index from the text start.
    visible : int
        Number of characters shown before an elision mark.
    height : int
        Font line height.
    """
    if not spans:
        return
    painter.save()
    painter.setClipRect(rect)
    painter.setPen(Qt.NoPen)
    painter.setBrush(HIGHLIGHT_COLOR)
    top = rect.center().y() - height // 2
    for start, end in spans:
        end = min(end, visible)
        if start >= end:
            continue
        left = advance(start)
        painter.drawRect(left, top, max(1, advance(end) - left), height)
    painter.restore()


class RowHeightDelegate(QStyledItemDelegate):
    """Apply an optional fixed height to table item geometry.

    When `highlight` is set, the delegate also marks matched search text.
    """

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._row_height: int | None = None
        self.highlight: SearchHighlight | None = None

    def initStyleOption(self, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        """Move a file row in a folder tree left, under its folder icon.

        A file row has no folding arrow, so its own indentation step is empty.
        The cell content starts there instead, so the file icon lines up with
        the folder icon above it. Folder rows keep their indentation.
        """
        super().initStyleOption(option, index)
        view = option.widget
        model = index.model()
        if (
            isinstance(view, QTreeView)
            and view.rootIsDecorated()
            and index.column() == view.treePosition()
            and index.parent().isValid()
            and getattr(model, "node_kind", None) is not None
            and model.node_kind(index) == "file"
        ):
            option.rect = option.rect.adjusted(-view.indentation(), 0, 0, 0)

    def _text_spans(self, index: QModelIndex, text: str) -> list[tuple[int, int]]:
        """Return highlight spans for the displayed text of `index`."""
        if self.highlight is None:
            return []
        return self.highlight.spans(self.highlight.column_key(index.column()), text)

    def paint(
        self,
        painter: Any,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        """Paint the native item, then mark matched search text."""
        super().paint(painter, option, index)
        self._paint_native_highlights(painter, option, index)

    def _paint_native_highlights(
        self,
        painter: Any,
        option: QStyleOptionViewItem,
        index: QModelIndex,
        font: QFont | None = None,
    ) -> None:
        """Mark matches in text that the style drew in the standard way."""
        if self.highlight is None or not self.highlight.enabled:
            return
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        text = styled.text
        spans = self._text_spans(index, text)
        if not spans:
            return
        widget = styled.widget
        style = widget.style() if widget is not None else QApplication.style()
        text_rect = style.subElementRect(QStyle.SE_ItemViewItemText, styled, widget)
        # QCommonStyle draws item text inside this horizontal margin.
        margin = style.pixelMetric(QStyle.PM_FocusFrameHMargin, None, widget) + 1
        text_rect = text_rect.adjusted(margin, 0, -margin, 0)
        metrics = QFontMetrics(font if font is not None else styled.font)
        shown = metrics.elidedText(text, styled.textElideMode, text_rect.width())
        visible = len(text) if shown == text else max(0, len(shown) - 1)
        width = metrics.horizontalAdvance(shown)
        alignment = styled.displayAlignment
        if alignment & Qt.AlignRight:
            origin = text_rect.right() + 1 - width
        elif alignment & Qt.AlignHCenter:
            origin = text_rect.left() + (text_rect.width() - width) // 2
        else:
            origin = text_rect.left()
        paint_highlights(
            painter,
            text_rect,
            spans,
            lambda position: origin + metrics.horizontalAdvance(text[:position]),
            visible,
            metrics.height(),
        )

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
        offset = painter.fontMetrics().horizontalAdvance(surname)
        shift = 0
        alignment = index.data(Qt.TextAlignmentRole)
        if alignment is not None:
            flags = Qt.AlignmentFlag(alignment)
            text_width = offset + QFontMetrics(styled.font).horizontalAdvance(remainder)
            if flags & Qt.AlignRight:
                shift = text_rect.width() - text_width
            elif flags & Qt.AlignHCenter:
                shift = (text_rect.width() - text_width) // 2
        painter.drawText(
            text_rect.adjusted(shift, 0, 0, 0),
            Qt.AlignLeft | Qt.AlignVCenter,
            surname,
        )
        painter.setFont(styled.font)
        painter.drawText(
            text_rect.adjusted(shift + offset, 0, 0, 0),
            Qt.AlignLeft | Qt.AlignVCenter,
            remainder,
        )
        painter.restore()
        text = surname + remainder
        spans = self._text_spans(index, text)
        if spans:
            bold_metrics = QFontMetrics(bold_font)
            metrics = QFontMetrics(styled.font)
            origin = text_rect.left() + shift

            def advance(position: int) -> int:
                if position <= len(surname):
                    return origin + bold_metrics.horizontalAdvance(surname[:position])
                return (
                    origin
                    + offset
                    + metrics.horizontalAdvance(remainder[: position - len(surname)])
                )

            paint_highlights(
                painter, text_rect, spans, advance, len(text), metrics.height()
            )


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
            self._paint_native_highlights(painter, option, index, styled.font)
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
        alignment = index.data(Qt.TextAlignmentRole)
        flags = Qt.AlignmentFlag(alignment) if alignment is not None else Qt.AlignCenter
        painter.drawText(badge, flags, self.brand)
        painter.restore()


class ResolutionDelegate(RowHeightDelegate):
    """Draw a compact marker for a record's ADS link or local-metadata state."""

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
        if hasattr(model, "record_for_index"):
            record = model.record_for_index(index)
        else:
            record = (
                model.record_at(index.row()) if hasattr(model, "record_at") else None
            )
        if not isinstance(record, dict):
            return
        marker_size = min(10, styled.rect.width() - 4, styled.rect.height() - 4)
        if marker_size <= 0:
            return
        marker = styled.rect.center()
        alignment = index.data(Qt.TextAlignmentRole)
        if alignment is not None:
            flags = Qt.AlignmentFlag(alignment)
            inner = styled.rect.adjusted(2, 2, -2, -2)
            if flags & Qt.AlignLeft:
                marker.setX(inner.left() + marker_size // 2)
            elif flags & Qt.AlignRight:
                marker.setX(inner.right() - (marker_size - 1 - marker_size // 2))
        state = resolution_state(record)
        if state is ResolutionState.CHANGED:
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
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(RESOLUTION_COLORS[state]))
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
        # Records before search and sort, reused while the catalog is unchanged.
        self._natural: list[dict[str, Any]] = []
        self._natural_key: tuple[Any, ...] | None = None
        self._rows: list[dict[str, Any]] = []
        self._total = 0
        # Cell values by record identity, column, and role. Records do not
        # change while the model holds them, so the values stay valid until
        # a reload or a presentation change.
        self._cell_cache: dict[tuple[int, int, int], Any] = {}
        self._column_fonts: dict[str, QFont] = {}
        self._column_colors: dict[str, QColor] = {}
        self._column_alignments: dict[str, str] = {}
        self._timestamp_format = ISO8601_TIMESTAMP_FORMAT
        if registry is not None:
            self._reload()

    @property
    def total(self) -> int:
        """Return the total number of rows matching the current query."""
        return self._total

    @property
    def records(self) -> tuple[dict[str, Any], ...]:
        """Return every record of the current query, loaded or not, in order."""
        return tuple(self._snapshot)

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
        self._cell_cache.clear()
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
        self._cell_cache.clear()
        self.layoutChanged.emit()

    def set_timestamp_format(self, timestamp_format: str) -> None:
        """Apply an ISO 8601 or custom ``strftime`` timestamp format."""
        self._timestamp_format = timestamp_format
        self._cell_cache.clear()
        self.layoutChanged.emit()

    def set_column_alignments(self, alignments: dict[str, str]) -> None:
        """Apply horizontal alignment overrides by column key."""
        self._column_alignments = dict(alignments)
        self._cell_cache.clear()
        if self.columnCount():
            self.headerDataChanged.emit(Qt.Horizontal, 0, self.columnCount() - 1)
            if self.rowCount():
                self.dataChanged.emit(
                    self.index(0, 0),
                    self.index(self.rowCount() - 1, self.columnCount() - 1),
                    [Qt.TextAlignmentRole],
                )

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
        key = (id(record), index.column(), int(role))
        try:
            return self._cell_cache[key]
        except KeyError:
            pass
        value = self._cell_data(record, self._columns[index.column()], role)
        self._cell_cache[key] = value
        return value

    def _cell_data(self, record: dict[str, Any], column: Column, role: int) -> Any:
        """Compute one cell value for `data`."""
        if role == Qt.TextAlignmentRole:
            alignment = COLUMN_ALIGNMENT_FLAGS.get(
                self._column_alignments.get(column.key)
            )
            if alignment is not None:
                return int(alignment | Qt.AlignVCenter)
        if column.key.startswith("citation:"):
            if role in (Qt.DisplayRole, Qt.ToolTipRole, Qt.AccessibleTextRole):
                return column.getter(record)
        if column.key == "status":
            if role == Qt.DisplayRole:
                return ""
            if role in (Qt.ToolTipRole, Qt.AccessibleTextRole):
                return resolution_tooltip(record)
        if column.key in COUNT_COLUMN_TOOLTIPS:
            if role == Qt.ToolTipRole:
                if column.getter(record):
                    return COUNT_COLUMN_TOOLTIPS[column.key]
                return "No count is saved for this record."
            if role == Qt.TextAlignmentRole:
                return int(Qt.AlignRight | Qt.AlignVCenter)
            if role == Qt.AccessibleTextRole:
                return f"{column.header}: {column.getter(record) or 'not saved'}"
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
                Qt.gray
                if resolution_state(record) is ResolutionState.UNRESOLVED
                else None,
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
        if orientation != Qt.Horizontal or not 0 <= section < len(self._columns):
            return None
        if role == Qt.TextAlignmentRole:
            alignment = COLUMN_ALIGNMENT_FLAGS.get(
                self._column_alignments.get(self._columns[section].key)
            )
            return int(alignment | Qt.AlignVCenter) if alignment is not None else None
        if role == Qt.ToolTipRole:
            return COUNT_COLUMN_TOOLTIPS.get(self._columns[section].key)
        if role != Qt.DisplayRole:
            return None
        return self._columns[section].header

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
            replace(
                self._query,
                order_by=target_column.key,
                descending=order == Qt.DescendingOrder,
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
            self._cell_cache.clear()
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
            snapshot = self._cached_snapshot()
            if snapshot is not None:
                return snapshot
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

    def _cached_snapshot(self) -> list[dict[str, Any]] | None:
        """Search and sort reused records while the catalog is unchanged.

        The result equals ``query_files`` for the same query. Records keep
        catalog order before the sort, so equal sort values keep the same
        order as a new query. Returns `None` when the registry cannot supply
        a change token, natural order, search keys, and sorting.
        """
        registry = self._registry
        if not all(
            callable(getattr(registry, name, None))
            for name in (
                "change_token",
                "query_records",
                "search_location_keys",
                "sort_records",
            )
        ):
            return None
        filters = self._query.to_filters()
        search = filters.pop("search", None)
        exact = bool(filters.pop("search_exact", False))
        wildcards = bool(filters.pop("search_wildcards", False))
        fields = tuple(filters.pop("search_fields", ()))
        if search and filters.get("duplicates_only"):
            # Duplicates are counted among the search results, not before.
            return None
        token = registry.change_token()
        key = (token, tuple(sorted(filters.items())))
        if key != self._natural_key:
            self._natural_key = None
            self._natural = registry.query_records(**filters)
            # The token from before the query makes a concurrent write miss.
            self._natural_key = key
        records = list(self._natural)
        if search:
            keys = registry.search_location_keys(
                search, exact=exact, wildcards=wildcards, fields=fields
            )
            records = [
                record
                for record in records
                if (str(record.get("root") or ""), record.get("location_id")) in keys
            ]
        registry.sort_records(
            records,
            order_by=self._query.order_by,
            descending=self._query.descending,
            secondary_order_by=self._query.secondary_order_by,
            tertiary_order_by=self._query.tertiary_order_by,
        )
        return records

    def _report_query_error(self, exc: sqlite3.OperationalError) -> None:
        """Surface a transient registry lock without propagating through Qt."""
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            self.query_failed.emit("Library is busy; try again in a moment.")
            return
        raise exc
