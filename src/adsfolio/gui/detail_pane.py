"""Editable, reorderable metadata detail pane for a selected PDF."""

from __future__ import annotations

import copy
import html
from collections.abc import Sequence
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import quote

from PySide6.QtCore import QEvent, QMimeData, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QDrag,
    QFont,
    QFontMetrics,
    QIcon,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QSplitterHandle,
    QStyle,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from ..citations import CitationFormatter, CitationStyle, citation_preference_key
from ..formatting import html_to_text
from ..pdf.catalog import REFERENCE_FIELD_MAP, REFERENCE_METADATA_KEY
from ..pdf.registry import LOCKED_METADATA_FIELDS_KEY
from .icons import ICON_SIZE, vector_icon
from .library import (
    ISO8601_TIMESTAMP_FORMAT,
    STALE_VERIFICATION_TOOLTIP,
    format_mtime,
    verification_stale,
)
from .osint import open_path, open_url, reveal_in_file_manager

DEFAULT_ROW_ORDER = (
    "modified",
    "title",
    "author",
    "author_count",
    "year",
    "pub",
    "bibcode",
    "doi",
    "abstract",
    "status",
    "identified_at",
    "links",
    "filename",
    "full_path",
    "size",
    "sha256",
    "bibstem",
    "volume",
    "page",
)
FIELD_LABELS = {
    "abstract": "Abstract",
    "author": "Authors",
    "author_count": "Author count",
    "bibcode": "Bibcode",
    "bibstem": "Bibstem",
    "citation_count": "Citations (all)",
    "doi": "DOI",
    "filename": "Filename",
    "full_path": "Full path",
    "identified_at": "Identified",
    "modified": "Modified",
    "page": "Page",
    "pub": "Journal",
    "sha256": "SHA-256",
    "size": "Size",
    "status": "Status",
    "title": "Title",
    "upgrade_status": "Upgrade",
    "upgraded_at": "Upgraded at",
    "annotations_ported": "Annotations ported",
    "annotations_failed": "Annotations failed",
    "unported_annotations_file": "Unported annotations file",
    "volume": "Volume",
    "year": "Year",
}
LIST_FIELDS = {"author", "bibstem", "doi", "page", "title"}
ADS_FIELDS = {
    "abstract",
    "author",
    "author_count",
    "bibcode",
    "bibstem",
    "citation_count",
    "doi",
    "page",
    "pub",
    "title",
    "volume",
    "year",
}
ROW_DRAG_MIME_TYPE = "application/x-adsfolio-detail-row"
WRAP_OPPORTUNITY_AFTER = ("/", "_", "-", ".")


def _wrap_opportunities(text: str) -> str:
    """Insert a zero-width space after each character that may end a line."""
    for character in WRAP_OPPORTUNITY_AFTER:
        text = text.replace(character, f"{character}\u200b")
    return text


class _MarkupWrapper(HTMLParser):
    """Add wrap opportunities to text while re-emitting ADS markup verbatim.

    ADS abstracts and titles carry display markup such as ``<SUP>1</SUP>``.
    Inserting wrap opportunities blindly corrupts the tags themselves, so tag
    text is passed through untouched and only character data is rewritten.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def result(self) -> str:
        """Return the rewritten markup."""
        return "".join(self._parts)

    def handle_data(self, data: str) -> None:
        """Rewrite literal text, escaping it to survive rich-text rendering."""
        self._parts.append(_wrap_opportunities(html.escape(data, quote=False)))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Re-emit a start tag exactly as it was written."""
        self._parts.append(self.get_starttag_text() or f"<{tag}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Re-emit a self-closing tag exactly as it was written."""
        self._parts.append(self.get_starttag_text() or f"<{tag}/>")

    def handle_endtag(self, tag: str) -> None:
        """Re-emit a closing tag."""
        self._parts.append(f"</{tag}>")


class DragHandle(QLabel):
    """Drag a field label to reorder its metadata row."""

    def __init__(self, key: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._key = key
        self._press_position = None
        self.locked = False
        self._lock_icon: QIcon | None = None
        self._lock_icon_key: tuple[int, float] | None = None
        self.setAccessibleName("Drag to reorder")
        self.setToolTip("Drag to reorder")
        self.setCursor(Qt.OpenHandCursor)
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)

    def paintEvent(self, event: Any) -> None:
        """Elide the label and keep a compact lock visible inside its column."""
        painter = QPainter(self)
        try:
            rect = self.contentsRect()
            if self.locked:
                size = self.fontMetrics().height()
                badge = rect.adjusted(0, 0, 0, 0)
                badge.setWidth(size)
                badge.setHeight(size)
                icon_key = (self.palette().cacheKey(), self.devicePixelRatioF())
                if self._lock_icon is None or icon_key != self._lock_icon_key:
                    self._lock_icon = vector_icon("lock", self)
                    self._lock_icon_key = icon_key
                self._lock_icon.paint(painter, badge)
                rect.setLeft(
                    rect.left() + size + self.fontMetrics().horizontalAdvance(" ")
                )
            painter.setPen(self.palette().color(self.foregroundRole()))
            painter.drawText(
                rect,
                Qt.AlignLeft | Qt.AlignTop,
                self.fontMetrics().elidedText(
                    self.text(), Qt.ElideRight, max(0, rect.width())
                ),
            )
        finally:
            painter.end()

    def mousePressEvent(self, event: Any) -> None:
        """Record the starting point for an explicit row drag."""
        if event.button() == Qt.LeftButton:
            self._press_position = event.position()
            self.setCursor(Qt.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: Any) -> None:
        """Start a row drag after the pointer travels beyond Qt's threshold."""
        if (
            self._press_position is not None
            and event.buttons() & Qt.LeftButton
            and (event.position() - self._press_position).manhattanLength()
            >= QApplication.startDragDistance()
        ):
            mime = QMimeData()
            mime.setData(ROW_DRAG_MIME_TYPE, self._key.encode())
            drag = QDrag(self)
            drag.setMimeData(mime)
            drag.exec(Qt.MoveAction)
            self._press_position = None
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: Any) -> None:
        """Restore the normal drag-handle cursor."""
        self._press_position = None
        self.setCursor(Qt.OpenHandCursor)
        super().mouseReleaseEvent(event)


class _LabelColumnHandle(QSplitterHandle):
    """Resize the shared label column by pointer or keyboard."""

    def __init__(self, orientation: Qt.Orientation, parent: QSplitter) -> None:
        super().__init__(orientation, parent)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("Field column width")
        self.setToolTip(
            "Drag to resize field labels. Double-click to reset. Left/Right keys resize."
        )

    def mouseDoubleClickEvent(self, event: Any) -> None:
        if event.button() == Qt.LeftButton:
            self.splitter().reset_requested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event: Any) -> None:
        if event.key() in (Qt.Key_Left, Qt.Key_Right):
            splitter = self.splitter()
            step = self.fontMetrics().averageCharWidth()
            width = splitter.sizes()[0] + (
                step if event.key() == Qt.Key_Right else -step
            )
            splitter.width_requested.emit(width)
            event.accept()
            return
        super().keyPressEvent(event)


class _LabelColumnSplitter(QSplitter):
    """Expose one shared boundary for every metadata row."""

    width_requested = Signal(int)
    reset_requested = Signal()

    def createHandle(self) -> QSplitterHandle:
        return _LabelColumnHandle(self.orientation(), self)


class _RowColumnDivider(QFrame):
    """Resize the shared field column from its line in any metadata row."""

    width_requested = Signal(int)
    reset_requested = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.VLine)
        self.setFixedWidth(self.style().pixelMetric(QStyle.PM_SplitterWidth))
        self.setCursor(Qt.SplitHCursor)
        self.setToolTip("Drag to resize field labels. Double-click to reset.")
        self._press_x: float | None = None
        self._press_width = 0

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.LeftButton:
            self._press_x = event.globalPosition().x()
            self._press_width = self.parentWidget().name.width()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: Any) -> None:
        if self._press_x is not None and event.buttons() & Qt.LeftButton:
            self.width_requested.emit(
                self._press_width + round(event.globalPosition().x() - self._press_x)
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: Any) -> None:
        self._press_x = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: Any) -> None:
        if event.button() == Qt.LeftButton:
            self._press_x = None
            self.reset_requested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class MetadataRowsContainer(QWidget):
    """Own draggable metadata rows without Qt item-widget reparenting."""

    row_dropped = Signal(str, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.rows_layout = QVBoxLayout(self)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(0)
        self.rows_layout.setAlignment(Qt.AlignTop)

    def dragEnterEvent(self, event: Any) -> None:
        """Accept drags produced by a detail-row handle."""
        if event.mimeData().hasFormat(ROW_DRAG_MIME_TYPE):
            event.acceptProposedAction()

    def dragMoveEvent(self, event: Any) -> None:
        """Keep accepting valid detail-row drags across the container."""
        if event.mimeData().hasFormat(ROW_DRAG_MIME_TYPE):
            event.acceptProposedAction()

    def dropEvent(self, event: Any) -> None:
        """Emit the source row and its destination index."""
        if not event.mimeData().hasFormat(ROW_DRAG_MIME_TYPE):
            return
        source = bytes(event.mimeData().data(ROW_DRAG_MIME_TYPE)).decode()
        target = self.rows_layout.count()
        for index in range(self.rows_layout.count()):
            widget = self.rows_layout.itemAt(index).widget()
            if (
                widget is not None
                and event.position().y() < widget.geometry().center().y()
            ):
                target = index
                break
        self.row_dropped.emit(source, target)
        event.acceptProposedAction()

    def minimumSizeHint(self) -> QSize:
        """Fit within the scroll viewport width while honoring wrapped row height."""
        hint = super().minimumSizeHint()
        if self.hasHeightForWidth() and self.width() > 0:
            return QSize(0, max(hint.height(), self.heightForWidth(self.width())))
        return QSize(0, hint.height())

    def sizeHint(self) -> QSize:
        """Report height-for-width when laid out inside the scroll viewport."""
        hint = super().sizeHint()
        if self.hasHeightForWidth() and self.width() > 0:
            return QSize(
                hint.width(),
                max(hint.height(), self.heightForWidth(self.width())),
            )
        return hint

    def resizeEvent(self, event: Any) -> None:
        """Recompute vertical size hints when the scroll viewport width changes."""
        super().resizeEvent(event)
        if event.oldSize().width() != event.size().width():
            for index in range(self.rows_layout.count()):
                widget = self.rows_layout.itemAt(index).widget()
                if isinstance(widget, MetadataRow):
                    widget._invalidate_row_geometry()
        self.rows_layout.invalidate()
        self.rows_layout.activate()
        self.updateGeometry()


class CopyableLabel(QLabel):
    """Selectable metadata text that copies its complete value when clicked."""

    copied = Signal()

    def __init__(
        self,
        text: str,
        parent: QWidget | None = None,
        copy_text: str | None = None,
    ) -> None:
        super().__init__(text, parent)
        self._copy_text = copy_text if copy_text is not None else text
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Click to copy")
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)

    def minimumSizeHint(self) -> QSize:
        """Allow narrow value columns to compute height-for-width at actual width."""
        return QSize(0, super().minimumSizeHint().height())

    def mouseReleaseEvent(self, event: Any) -> None:
        """Copy the label text after a primary-button click."""
        if event.button() == Qt.LeftButton and self.text():
            QApplication.clipboard().setText(self._copy_text)
            QToolTip.showText(event.globalPosition().toPoint(), "Copied", self)
            self.copied.emit()
        super().mouseReleaseEvent(event)

    def set_copy_text(self, value: str) -> None:
        """Set the original text copied by a primary-button click."""
        self._copy_text = value


class DetailLinkLabel(QLabel):
    """Require the configured pointer gesture while retaining keyboard links."""

    activated = Signal(str)

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.link_activation = "double"
        self._double_click = False
        self._allow_activation = True
        self._pointer_modifiers = Qt.NoModifier
        self.setTextFormat(Qt.RichText)
        self.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setOpenExternalLinks(False)
        self.linkActivated.connect(self._activate)
        self._sync_activation_feedback()

    def _sync_activation_feedback(self) -> None:
        cue = (
            "Double-click to open in viewer or browser."
            if self.link_activation == "double"
            else "Click to open in viewer or browser."
        )
        self.setToolTip(cue)
        self.setAccessibleDescription(cue)

    def _activate(self, url: str) -> None:
        """Forward explicit keyboard activation or an allowed pointer gesture."""
        if self._allow_activation:
            self.activated.emit(url)

    def mousePressEvent(self, event: Any) -> None:
        """Remember modified gestures even if modifiers are released first."""
        self._double_click = False
        self._pointer_modifiers = event.modifiers()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: Any) -> None:
        """Open once per gesture, leaving modified clicks for text selection."""
        self._double_click = event.button() == Qt.LeftButton
        self._pointer_modifiers = event.modifiers()
        if self.link_activation == "single" or event.modifiers() != Qt.NoModifier:
            super().mouseDoubleClickEvent(event)
            return
        # QLabel selects text on a double click, cancelling its pending anchor.
        # In double mode, treat the second press as an anchor press instead.
        press = QMouseEvent(
            QEvent.MouseButtonPress,
            event.position(),
            event.globalPosition(),
            event.button(),
            event.buttons(),
            event.modifiers(),
        )
        super().mousePressEvent(press)

    def mouseReleaseEvent(self, event: Any) -> None:
        """Allow only unmodified primary gestures matching the preference."""
        self._allow_activation = (
            event.button() == Qt.LeftButton
            and event.modifiers() == Qt.NoModifier
            and self._pointer_modifiers == Qt.NoModifier
            and (
                (self.link_activation == "single" and not self._double_click)
                or (self.link_activation == "double" and self._double_click)
            )
        )
        try:
            super().mouseReleaseEvent(event)
        finally:
            self._allow_activation = True
            self._double_click = False
            self._pointer_modifiers = Qt.NoModifier


class MetadataRow(QWidget):
    """One draggable metadata row with copy, edit, and lock controls."""

    copied = Signal(str)
    edit_requested = Signal(str)
    lock_changed = Signal(str, bool)
    link_requested = Signal(str)
    size_changed = Signal()
    move_requested = Signal(str, int)
    menu_closed = Signal(str)
    label_width_requested = Signal(int)
    label_width_reset = Signal()

    def __init__(
        self,
        key: str,
        label: str,
        value: Any,
        *,
        editable: bool,
        lockable: bool,
        locked: bool,
        label_width: int,
        expand_abstract: bool = False,
        link_html: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.key = key
        self._editable = editable
        self._lockable = lockable
        self._locked = locked
        self._copy_value = self._copy_text(value)
        self.restriction = ""
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(label)
        self._authors = self._as_list(value) if key == "author" else []
        self._show_all_authors = False
        self._abstract = self._raw_text(value) if key == "abstract" else ""
        self._show_full_abstract = expand_abstract
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 5, 8, 5)
        layout.setSpacing(0)
        self.name = DragHandle(key, self)
        self.name.setText(label)
        self.name.setContentsMargins(0, 0, 4, 0)
        self.name.setFixedWidth(label_width)
        self.name.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.name.setToolTip(
            f"{label}. Drag to reorder. Right-click for field actions."
        )
        layout.addWidget(self.name, 0, Qt.AlignTop)
        self.divider = _RowColumnDivider(self)
        self.divider.width_requested.connect(self.label_width_requested.emit)
        self.divider.reset_requested.connect(self.label_width_reset.emit)
        layout.addWidget(self.divider)
        value_box = QWidget(self)
        value_layout = QVBoxLayout(value_box)
        value_layout.setContentsMargins(4, 0, 0, 0)
        value_layout.setSpacing(2)
        self._value_layout = value_layout
        self.doi_labels: list[CopyableLabel] = []
        self.link_label: QLabel | None = None
        if link_html is None:
            self.value: QLabel = CopyableLabel(
                "", value_box, copy_text=self._copy_text(value)
            )
            self.value.copied.connect(lambda: self.copied.emit(self.key))
        else:
            self.value = DetailLinkLabel(link_html, value_box)
            self.value.activated.connect(self.link_requested.emit)
            self.link_label = self.value
        self.value.setWordWrap(True)
        self.value.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.value.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self._unbounded_value_height = self.value.maximumHeight()
        value_layout.addWidget(self.value)
        self.author_toggle = QToolButton(value_box)
        self.author_toggle.setMinimumWidth(0)
        self.author_toggle.clicked.connect(self._toggle_authors)
        value_layout.addWidget(self.author_toggle, 0, Qt.AlignLeft)
        self.author_toggle.hide()
        self.abstract_toggle = QToolButton(value_box)
        self.abstract_toggle.setMinimumWidth(0)
        self.abstract_toggle.clicked.connect(self._toggle_abstract)
        value_layout.addWidget(self.abstract_toggle, 0, Qt.AlignLeft)
        self.abstract_toggle.hide()
        layout.addWidget(value_box, 1)
        if link_html is None:
            self._set_value(value)
        self.edit_button = QToolButton(self)
        self.edit_button.setIcon(vector_icon("edit", self.edit_button))
        self.edit_button.setIconSize(ICON_SIZE)
        self.edit_button.setAccessibleName(f"Edit {label}")
        self.edit_button.setToolTip(f"Edit {label}")
        self.edit_button.setFixedWidth(26)
        self.edit_button.setVisible(self._editable)
        self.edit_button.clicked.connect(lambda: self.edit_requested.emit(self.key))
        layout.addWidget(self.edit_button, 0, Qt.AlignTop)
        self.lock_button = QToolButton(self)
        self.lock_button.setFixedWidth(26)
        self.lock_button.setIconSize(ICON_SIZE)
        self.lock_button.setCheckable(True)
        self.lock_button.clicked.connect(self._toggle_lock)
        self.lock_button.setVisible(self._lockable)
        layout.addWidget(self.lock_button, 0, Qt.AlignTop)
        self._render_lock()
        for child in (self, self.name, self.value):
            self._install_field_menu(child)
        self._edit_shortcut = QShortcut(QKeySequence("F2"), self)
        self._edit_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._edit_shortcut.activated.connect(self._request_edit)
        self._menu_shortcut = QShortcut(QKeySequence("Shift+F10"), self)
        self._menu_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._menu_shortcut.activated.connect(self._show_keyboard_menu)

    def _install_field_menu(self, widget: QWidget) -> None:
        widget.setContextMenuPolicy(Qt.CustomContextMenu)
        widget.customContextMenuRequested.connect(
            lambda position: self._show_field_menu(widget, position)
        )

    def _show_field_menu(self, widget: QWidget, position: Any) -> None:
        menu = self.create_context_menu(widget)
        menu.exec(widget.mapToGlobal(position))
        if not self.isHidden():
            self.setFocus(Qt.PopupFocusReason)
        self.menu_closed.emit(self.key)
        menu.deleteLater()

    def _request_edit(self) -> None:
        if self._editable:
            self.edit_requested.emit(self.key)

    def _show_keyboard_menu(self) -> None:
        self._show_field_menu(self, self.rect().center())

    def create_context_menu(self, origin: QWidget | None = None) -> QMenu:
        """Offer copy, edit, lock, and reorder actions for a field.

        Parameters
        ----------
        origin : PySide6.QtWidgets.QWidget, optional
            Value label that opened the menu. A DOI label copies that DOI.
            Other origins copy the full field value.

        Returns
        -------
        PySide6.QtWidgets.QMenu
            Actions available for this field.
        """
        self.setFocus()
        menu = QMenu(self)
        copy_action = menu.addAction("Copy bibcode" if self.key == "links" else "Copy")
        copy_action.triggered.connect(lambda: self._copy_from_menu(origin))
        if self.restriction:
            notice = menu.addAction(self.restriction)
            notice.setEnabled(False)
        if self._editable:
            menu.addAction("Edit…", self._request_edit)
        if self._lockable:
            menu.addAction("Unlock" if self._locked else "Lock", self._toggle_lock)
        menu.addSeparator()
        move_up = menu.addAction(
            "Move up", lambda: self.move_requested.emit(self.key, -1)
        )
        move_down = menu.addAction(
            "Move down", lambda: self.move_requested.emit(self.key, 1)
        )
        container = self.parentWidget()
        if isinstance(container, MetadataRowsContainer):
            position = container.rows_layout.indexOf(self)
            move_up.setEnabled(position > 0)
            move_down.setEnabled(position < container.rows_layout.count() - 1)
        return menu

    def _copy_from_menu(self, origin: QWidget | None) -> None:
        value = (
            origin._copy_text if isinstance(origin, CopyableLabel) else self._copy_value
        )
        QApplication.clipboard().setText(value)
        self.setFocus()
        self.copied.emit(self.key)

    def keyPressEvent(self, event: Any) -> None:
        if event.key() == Qt.Key_Menu:
            self._show_keyboard_menu()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event: Any) -> None:
        super().paintEvent(event)
        if self.hasFocus():
            painter = QPainter(self)
            try:
                painter.setPen(self.palette().highlight().color())
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
            finally:
                painter.end()

    def minimumSizeHint(self) -> QSize:
        """Allow horizontal shrinking to the viewport while preserving wrapped height."""
        hint = super().minimumSizeHint()
        if self.hasHeightForWidth() and self.width() > 0:
            return QSize(0, self.heightForWidth(self.width()))
        return QSize(0, hint.height())

    @staticmethod
    def _as_list(value: Any) -> list[str]:
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value if str(item).strip()]
        return [str(value)] if value not in (None, "") else []

    def _raw_text(self, value: Any) -> str:
        """Return the value as one string, keeping any ADS display markup."""
        return (
            "; ".join(self._as_list(value))
            if isinstance(value, (list, tuple))
            else str(value or "")
        )

    def _copy_text(self, value: Any) -> str:
        """Return the plain text a copy click should place on the clipboard."""
        return html_to_text(self._raw_text(value))

    def _set_value(self, value: Any) -> None:
        if self.key == "author":
            self._render_authors()
            return
        if self.key == "abstract":
            self._render_abstract()
            return
        if self.key == "doi":
            self._render_dois(value)
            return
        display_value = (
            {"resolved": "ADS record linked", "unresolved": "No ADS link"}.get(
                value, value
            )
            if self.key == "status" and isinstance(value, str)
            else value
        )
        self.value.setText(DetailPane._display_text(DetailPane._join(display_value)))
        self.author_toggle.hide()
        self.abstract_toggle.hide()

    def _render_dois(self, value: Any) -> None:
        """Render each DOI as an independently copyable line."""
        for label in self.doi_labels:
            self._value_layout.removeWidget(label)
            label.deleteLater()
        self.doi_labels = []
        values = self._as_list(value)
        first = values[0] if values else ""
        self.value.setText(DetailPane._display_text(first))
        if isinstance(self.value, CopyableLabel):
            self.value.set_copy_text(first)
        for index, doi in enumerate(values[1:], start=1):
            label = CopyableLabel(
                DetailPane._display_text(doi),
                self.value.parentWidget(),
                copy_text=doi,
            )
            label.setWordWrap(True)
            self._install_field_menu(label)
            label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
            label.copied.connect(lambda: self.copied.emit(self.key))
            self._value_layout.insertWidget(index, label)
            self.doi_labels.append(label)
        self.author_toggle.hide()
        self.abstract_toggle.hide()

    def _render_authors(self) -> None:
        shown = self._authors if self._show_all_authors else self._authors[:5]
        lines = []
        for author in shown:
            surname, separator, remainder = author.partition(",")
            lines.append(
                f"<b>{html.escape(surname)}</b>{html.escape(separator + remainder)}"
            )
        self.value.setTextFormat(Qt.RichText)
        self.value.setText("<br>".join(lines))
        remaining = len(self._authors) - 5
        self.author_toggle.setVisible(remaining > 0)
        if remaining > 0:
            self.author_toggle.setText(
                "Show less" if self._show_all_authors else f"Show {remaining} more"
            )
        self._invalidate_row_geometry()
        self.size_changed.emit()

    def _toggle_authors(self) -> None:
        self._show_all_authors = not self._show_all_authors
        self._render_authors()

    def _render_abstract(self) -> None:
        """Render a bounded abstract preview or its complete text."""
        text = DetailPane._display_text(self._abstract)
        self.value.setText(text)
        if not text.strip():
            self.value.setMinimumHeight(0)
            self.value.setMaximumHeight(self._unbounded_value_height)
            self.abstract_toggle.hide()
            self._invalidate_row_geometry()
            return
        collapsed_height = 3 * self.value.fontMetrics().lineSpacing()
        self.value.setMinimumHeight(0)
        self.value.setMaximumHeight(
            self._unbounded_value_height
            if self._show_full_abstract
            else collapsed_height
        )
        self.abstract_toggle.setText(
            "Show less" if self._show_full_abstract else "Show more"
        )
        self.abstract_toggle.setToolTip(
            "Collapse abstract" if self._show_full_abstract else "Expand abstract"
        )
        self.abstract_toggle.setAccessibleName(self.abstract_toggle.toolTip())
        self.abstract_toggle.show()
        self._invalidate_row_geometry()
        self.size_changed.emit()

    def _toggle_abstract(self) -> None:
        """Toggle the abstract between preview and full text."""
        self._show_full_abstract = not self._show_full_abstract
        self._render_abstract()

    def _invalidate_row_geometry(self) -> None:
        """Clear cached height-for-width state on the value container and row."""
        self.value.updateGeometry()
        self._value_layout.invalidate()
        value_box = self.value.parentWidget()
        if value_box is not None:
            value_box.updateGeometry()
            if QApplication.instance() is not None:
                QApplication.sendEvent(value_box, QEvent(QEvent.LayoutRequest))
        if self.layout() is not None:
            self.layout().invalidate()
        self.updateGeometry()
        if QApplication.instance() is not None:
            QApplication.sendEvent(self, QEvent(QEvent.LayoutRequest))

    def _render_lock(self) -> None:
        self.name.locked = self._locked
        self.name.setAccessibleDescription(
            "Locked: ADS cannot update this field."
            if self._locked
            else "Unlocked field."
        )
        self.setAccessibleDescription(self.name.accessibleDescription())
        self.name.setToolTip(
            f"{self.name.text()}. "
            + ("Locked: ADS cannot update this field. " if self._locked else "")
            + "Drag to reorder. Right-click for actions."
        )
        self.name.update()
        self.lock_button.setIcon(
            vector_icon("lock" if self._locked else "unlock", self.lock_button)
        )
        self.lock_button.setChecked(self._locked)
        self.lock_button.setAccessibleName(
            f"Unlock {self.name.text()}" if self._locked else f"Lock {self.name.text()}"
        )
        self.lock_button.setToolTip(
            "Locked: ADS cannot update this field. Click to unlock."
            if self._locked
            else "Unlocked: ADS may update this field. Click to lock."
        )

    def _toggle_lock(self) -> None:
        self._locked = not self._locked
        self._render_lock()
        self.lock_changed.emit(self.key, self._locked)

    def set_action_button_visibility(self, *, show_edit: bool, show_lock: bool) -> None:
        """Set visibility for this row's applicable edit and lock buttons."""
        self.edit_button.setVisible(self._editable and show_edit)
        self.lock_button.setVisible(self._lockable and show_lock)
        self._invalidate_row_geometry()

    def set_content_style(self, font: QFont | None, color: QColor | None) -> None:
        """Apply the configured font and color to this row's value."""
        for label in [self.value, *self.doi_labels]:
            if font is not None:
                label.setFont(font)
            if color is not None:
                label.setStyleSheet(f"color: {color.name()};")

    def set_label_bold(self, enabled: bool) -> None:
        """Set whether this row's left-column label uses bold text."""
        font = self.name.font()
        font.setBold(enabled)
        self.name.setFont(font)


class DetailPane(QWidget):
    """Display editable metadata and file actions for one selected record."""

    copy_feedback = Signal(str)
    metadata_changed = Signal(dict)
    ads_update_requested = Signal(str)
    manual_metadata_requested = Signal()
    row_order_changed = Signal(list)
    label_width_changed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._link_activation = "double"
        self._record: dict[str, Any] | None = None
        self._cached_document: dict[str, Any] | None = None
        self._metadata: dict[str, Any] = {}
        self._root = Path()
        self._path = Path()
        self._timestamp_format = ISO8601_TIMESTAMP_FORMAT
        self._content_font: QFont | None = None
        self._content_color: QColor | None = None
        self._row_fonts: dict[str, QFont] = {}
        self._row_colors: dict[str, QColor] = {}
        self._expand_abstracts = False
        self._row_spacing: int | None = None
        self._label_bold = True
        self._compact_rows = False
        self._label_width: int | None = None
        self._pending_field_focus: tuple[Path, str, str] | None = None
        self._show_edit_buttons = True
        self._show_lock_buttons = True
        self._pdf_viewer_command: str | None = None
        self._web_browser_command: str | None = None
        self._row_order: list[str] = []
        self._hidden_rows: set[str] = set()
        self._labels: dict[str, QLabel] = {}
        self._row_widgets: dict[str, MetadataRow] = {}
        self._row_labels: dict[str, str] = {}
        self._citation_styles: tuple[CitationStyle, ...] = ()
        self._rows = MetadataRowsContainer(self)
        self._rows.row_dropped.connect(self._move_dropped_row)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(self._rows)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.column_header = _LabelColumnSplitter(Qt.Horizontal, self)
        self.column_header.setChildrenCollapsible(False)
        self.column_header.addWidget(QLabel("Field", self.column_header))
        self.column_header.addWidget(QLabel("Value", self.column_header))
        self.column_header.setStretchFactor(0, 0)
        self.column_header.setStretchFactor(1, 1)
        self.column_header.splitterMoved.connect(
            lambda position, index: self._resize_label_column(position)
        )
        self.column_header.width_requested.connect(self._resize_label_column)
        self.column_header.reset_requested.connect(self.reset_label_width)
        header_box = QWidget(self)
        header_layout = QHBoxLayout(header_box)
        header_layout.setContentsMargins(8, 0, 8, 0)
        header_layout.addWidget(self.column_header)
        layout.addWidget(header_box)
        layout.addWidget(scroll)
        self._actions = QWidget(self)
        # Two columns keep the pane's minimum width below its default splitter
        # width when the platform style draws wider buttons.
        buttons = QGridLayout(self._actions)
        buttons.setContentsMargins(4, 4, 4, 4)
        buttons.setSpacing(4)
        update_button = QPushButton("Update by ID…", self._actions)
        self.manual_metadata_button = QPushButton("Enter Details…", self._actions)
        open_button = QPushButton("Open", self._actions)
        reveal_button = QPushButton("Reveal", self._actions)
        for button, icon_name, description in (
            (
                update_button,
                "refresh",
                "Look up a Bibcode, DOI, arXiv ID, or reference.\n"
                "Reference formats: {authorLastName}-{year}-{bibstem} or {bibstem}-{volume}-{page}.\n"
                "{bibstem} means the ADS journal abbreviation. You can use the full journal name instead.\n"
                "These reference formats query ADS only; other sites are not searched.\n"
                "Click for field definitions and examples.",
            ),
            (
                self.manual_metadata_button,
                "edit",
                "Enter paper metadata without an online lookup",
            ),
            (open_button, "open", "Open PDF"),
            (reveal_button, "folder", "Reveal PDF in file manager"),
        ):
            button.setIcon(vector_icon(icon_name, button))
            button.setIconSize(ICON_SIZE)
            button.setToolTip(description)
            button.setAccessibleName(description)
        buttons.addWidget(update_button, 0, 0)
        buttons.addWidget(open_button, 0, 1)
        buttons.addWidget(self.manual_metadata_button, 1, 0)
        buttons.addWidget(reveal_button, 1, 1)
        layout.addWidget(self._actions)
        update_button.clicked.connect(self._request_ads_update)
        self.manual_metadata_button.clicked.connect(self.manual_metadata_requested)
        open_button.clicked.connect(self._open)
        reveal_button.clicked.connect(self._reveal)
        self._actions.setEnabled(False)

    def set_compact_rows(self, enabled: bool) -> None:
        """Use context menus while retaining the saved inline-button choices."""
        self._compact_rows = bool(enabled)
        self.set_action_buttons_visible(
            show_edit=self._show_edit_buttons, show_lock=self._show_lock_buttons
        )

    def set_label_width(self, width: int | None) -> None:
        """Set the shared label width, or use the width of a common field name."""
        self._label_width = width
        self._apply_label_width()

    def _apply_label_width(self) -> None:
        available = max(
            0, self.column_header.width() - self.column_header.handleWidth()
        )
        font = QFont(self.font())
        font.setBold(self._label_bold)
        metrics = QFontMetrics(font)
        requested = self._label_width or (
            metrics.horizontalAdvance("Abstract")
            + metrics.height()
            + metrics.horizontalAdvance(" ")
            + 4
        )
        value_minimum = self.column_header.widget(1).minimumSizeHint().width()
        width = max(0, min(requested, available - value_minimum))
        self.column_header.setSizes([width, max(0, available - width)])
        width = self.column_header.sizes()[0]
        changed = False
        for row in self._row_widgets.values():
            if row.name.width() != width:
                row.name.setFixedWidth(width)
                changed = True
        if changed:
            self._refresh_rows_geometry()

    def reset_label_width(self) -> None:
        """Restore the default field width and retain automatic font sizing."""
        self.set_label_width(None)
        self.label_width_changed.emit(None)

    def _resize_label_column(self, width: int) -> None:
        self.set_label_width(max(1, width))
        self._label_width = self.column_header.sizes()[0]
        self.label_width_changed.emit(self._label_width)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._apply_label_width()

    def set_link_activation(self, mode: str) -> None:
        """Set pointer link activation to `double` (default) or `single`.

        Keyboard link activation remains available in either mode. Existing
        rows update immediately, and new rows inherit the setting.
        """
        if mode not in {"double", "single"}:
            raise ValueError("Link activation must be 'double' or 'single'")
        self._link_activation = mode
        for row in self._row_widgets.values():
            if isinstance(row.link_label, DetailLinkLabel):
                row.link_label.link_activation = mode
                row.link_label._double_click = False
                row.link_label._sync_activation_feedback()

    def set_citation_styles(self, styles: Sequence[CitationStyle]) -> None:
        """Configure runtime custom citation styles and rerender the current record."""
        self._citation_styles = tuple(styles)
        if self._record is not None:
            self.set_record(self._record, self._root, self._cached_document)

    def _default_row_order(self) -> list[str]:
        """Return the default row order with citation rows immediately after bibcode."""
        citation_keys = [citation_preference_key(s.id) for s in self._citation_styles]
        order: list[str] = []
        for key in DEFAULT_ROW_ORDER:
            order.append(key)
            if key == "bibcode":
                order.extend(citation_keys)
        return order

    def set_row_order(self, order: list[str]) -> None:
        """Set persisted row order, retaining unknown future field keys."""
        self._row_order = list(dict.fromkeys(str(key) for key in order if str(key)))

    def set_hidden_rows(self, rows: list[str]) -> None:
        """Hide configured row keys without discarding their metadata."""
        self._hidden_rows = {str(key) for key in rows if str(key)}

    def set_expand_abstracts(self, enabled: bool) -> None:
        """Set whether newly rendered abstracts start expanded."""
        self._expand_abstracts = enabled

    def set_action_buttons_visible(self, *, show_edit: bool, show_lock: bool) -> None:
        """Set visibility for edit and lock buttons across detail rows."""
        self._show_edit_buttons = bool(show_edit)
        self._show_lock_buttons = bool(show_lock)
        for row in self._row_widgets.values():
            row.set_action_button_visibility(
                show_edit=self._show_edit_buttons and not self._compact_rows,
                show_lock=self._show_lock_buttons and not self._compact_rows,
            )
        self._refresh_rows_geometry()

    def set_application_commands(
        self,
        *,
        pdf_viewer_command: str | None,
        web_browser_command: str | None,
    ) -> None:
        """Set optional applications used by local and external links."""
        self._pdf_viewer_command = pdf_viewer_command
        self._web_browser_command = web_browser_command

    def set_row_spacing(self, spacing: int | None) -> None:
        """Set the pixel separation between adjacent metadata rows."""
        self._row_spacing = spacing if spacing is None else max(0, int(spacing))
        self._rows.rows_layout.setSpacing(self._row_spacing or 0)
        self._refresh_rows_geometry()

    def set_label_bold(self, enabled: bool) -> None:
        """Set whether the left-column metadata labels use bold text."""
        self._label_bold = bool(enabled)
        for row in self._row_widgets.values():
            row.set_label_bold(self._label_bold)
        self._apply_label_width()

    def _refresh_rows_geometry(self) -> None:
        """Activate row layouts and update scroll container geometry."""
        for row in self._row_widgets.values():
            row._invalidate_row_geometry()
        if QApplication.instance() is not None:
            QApplication.sendEvent(self._rows, QEvent(QEvent.LayoutRequest))
        self._rows.rows_layout.invalidate()
        self._rows.rows_layout.activate()
        self._rows.updateGeometry()

    def available_rows(self) -> list[tuple[str, str]]:
        """Return selectable detail rows known for the current record."""
        keys = dict.fromkeys(
            [
                *self._default_row_order(),
                "upgrade_status",
                "upgraded_at",
                "annotations_ported",
                "annotations_failed",
                "unported_annotations_file",
                "original_bibcode",
                *self._row_order,
                *self._row_labels,
            ]
        )
        return [(key, self.row_label(key)) for key in keys]

    def row_keys(self) -> list[str]:
        """Return the current metadata-row order."""
        return [
            self._rows.rows_layout.itemAt(index).widget().key
            for index in range(self._rows.rows_layout.count())
            if self._rows.rows_layout.itemAt(index).widget() is not None
        ]

    def move_row(self, key: str, destination: int) -> None:
        """Move `key` to a final row index and persist the resulting order."""
        keys = self.row_keys()
        if key not in keys:
            return
        source = keys.index(key)
        destination = max(0, min(destination, len(keys) - 1))
        if source == destination:
            return
        widget = self._rows.rows_layout.itemAt(source).widget()
        if widget is None:
            return
        self._rows.rows_layout.removeWidget(widget)
        self._rows.rows_layout.insertWidget(destination, widget)
        full_order = list(
            dict.fromkeys([*self._row_order, *self._default_row_order(), *keys])
        )
        full_order.remove(key)
        visible = self.row_keys()
        next_key = visible[destination + 1] if destination + 1 < len(visible) else None
        insertion = (
            full_order.index(next_key)
            if next_key is not None
            else full_order.index(visible[destination - 1]) + 1
        )
        full_order.insert(insertion, key)
        self._row_order = full_order
        self._refresh_rows_geometry()
        self.row_order_changed.emit(self._row_order)

    def _move_dropped_row(self, key: str, destination: int) -> None:
        """Translate a pre-removal drop index into the final row position."""
        keys = self.row_keys()
        if key not in keys:
            return
        source = keys.index(key)
        if source < destination:
            destination -= 1
        self.move_row(key, destination)

    def saved_row_order(self) -> list[str]:
        """Return the persisted row order even while no record is selected."""
        return list(self._row_order or self.row_keys())

    def row_label(self, key: str) -> str:
        """Return the visible label for one metadata-row key."""
        if key in self._row_labels:
            return self._row_labels[key]
        if key.startswith("citation:"):
            style_id = key.removeprefix("citation:")
            for style in self._citation_styles:
                if style.id == style_id:
                    return style.name
        return FIELD_LABELS.get(key, key)

    def set_record(
        self,
        record: dict[str, Any] | None,
        root: Path,
        cached_document: dict[str, Any] | None = None,
    ) -> None:
        """Render `record`, or clear the pane when it is `None`."""
        focused = QApplication.focusWidget()
        focused_key = next(
            (
                key
                for key, row in self._row_widgets.items()
                if row is focused or (focused is not None and row.isAncestorOf(focused))
            ),
            None,
        )
        same_record = (
            self._record is not None
            and record is not None
            and self._record.get("current_path") == record.get("current_path")
        )
        self._record = record
        self._cached_document = cached_document if record is not None else None
        self._root = Path(root)
        self._path = Path()
        self._metadata = {}
        while self._rows.rows_layout.count():
            item = self._rows.rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self._labels = {}
        self._row_widgets = {}
        self._row_labels = {}
        self._actions.setEnabled(record is not None)
        if record is None:
            self._refresh_rows_geometry()
            return
        raw_metadata = record.get("metadata")
        self._metadata = (
            copy.deepcopy(raw_metadata) if isinstance(raw_metadata, dict) else {}
        )
        self._fill_missing_cached_metadata(cached_document)
        self._path = self._root / str(record.get("current_path") or "")
        locked = {
            str(key)
            for key in self._metadata.get(LOCKED_METADATA_FIELDS_KEY, [])
            if str(key).strip()
        }
        rows = self._build_rows(record, cached_document, locked)
        by_key = {key: row for key, row in rows}
        default_order = self._default_row_order()
        order = [
            key for key in (*self._row_order, *default_order, *by_key) if key in by_key
        ]
        for key in dict.fromkeys(order):
            if key in self._hidden_rows:
                continue
            label, value, editable, lockable, display_text = by_key[key]
            if REFERENCE_METADATA_KEY in self._metadata and key in {
                "bibcode",
                *REFERENCE_FIELD_MAP.values(),
            }:
                editable, lockable = False, False
            self._add_row(
                key,
                label,
                value,
                editable=editable,
                lockable=lockable,
                locked=key in locked,
                display_text=display_text,
            )
        self._apply_label_width()
        pending = self._pending_field_focus
        if pending is not None and pending[:2] == (
            self._root,
            str(record.get("current_path") or ""),
        ):
            focused_key = pending[2]
            same_record = True
        self._pending_field_focus = None
        if same_record and focused_key in self._row_widgets:
            self._row_widgets[focused_key].setFocus()

    def _fill_missing_cached_metadata(
        self, cached_document: dict[str, Any] | None
    ) -> None:
        for key, value in (cached_document or {}).items():
            if key in ADS_FIELDS and self._metadata.get(key) in (None, "", []):
                self._metadata[key] = copy.deepcopy(value)

    def _build_rows(
        self,
        record: dict[str, Any],
        cached_document: dict[str, Any] | None,
        locked: set[str],
    ) -> list[tuple[str, tuple[str, Any, bool, bool, str | None]]]:
        metrics = (cached_document or {}).get("metrics", {})
        rows: list[tuple[str, tuple[str, Any, bool, bool, str | None]]] = [
            (
                "modified",
                (
                    "Modified",
                    self._format_mtime(record.get("mtime_ns")),
                    False,
                    False,
                    None,
                ),
            ),
        ]
        known_metadata_keys = [
            "title",
            "author",
            "author_count",
            "year",
            "pub",
            "bibstem",
            "volume",
            "page",
            "bibcode",
            "doi",
            "abstract",
            "status",
            "identified_at",
            "upgrade_status",
            "original_bibcode",
        ]
        read_only_metadata_keys = {
            "identified_at",
            "original_bibcode",
            "status",
            "upgrade_status",
        }
        for key in known_metadata_keys:
            value = self._metadata.get(key, record.get(key))
            rows.append(
                (
                    key,
                    (
                        FIELD_LABELS.get(key, self._label_for_key(key)),
                        value,
                        key not in read_only_metadata_keys,
                        key in ADS_FIELDS,
                        None,
                    ),
                )
            )
        if isinstance(metrics, dict):
            for metric_key, label in (
                ("all_citations", "Citations (all)"),
                ("refereed_citations", "Citations (refereed)"),
                ("non_refereed_citations", "Citations (non-refereed)"),
            ):
                if metrics.get(metric_key) is not None:
                    rows.append(
                        (
                            f"metric_{metric_key}",
                            (label, metrics[metric_key], False, False, None),
                        )
                    )
        formatter = CitationFormatter()
        render_record = {**record, "metadata": self._metadata}
        for style in self._citation_styles:
            rendered = formatter.render(style, render_record)
            rows.append(
                (
                    citation_preference_key(style.id),
                    (
                        style.name,
                        rendered,
                        False,
                        False,
                        None,
                    ),
                )
            )
        bibcode = str(self._metadata.get("bibcode") or record.get("bibcode") or "")
        if bibcode:
            rows.append(
                ("links", ("Links", bibcode, False, False, self._ads_links(bibcode)))
            )
        rows.extend(
            [
                (
                    "filename",
                    (
                        "Filename",
                        Path(str(record.get("current_path") or "")).name,
                        False,
                        False,
                        None,
                    ),
                ),
                ("full_path", ("Full path", str(self._path), False, False, None)),
                (
                    "size",
                    (
                        "Size",
                        self._human_size(record.get("size_bytes")),
                        False,
                        False,
                        None,
                    ),
                ),
                (
                    "sha256",
                    ("SHA-256", str(record.get("sha256") or ""), False, False, None),
                ),
            ]
        )
        for key, value in self._metadata.items():
            if (
                key in {"current_filename", "source"}
                or key.startswith("_adsfolio_")
                or key in known_metadata_keys
            ):
                continue
            rows.append(
                (key, (self._label_for_key(key), value, True, key in ADS_FIELDS, None))
            )
        if not self._path.exists():
            rows.append(
                (
                    "warning",
                    ("", "Warning: file is missing on disk", False, False, None),
                )
            )
        return rows

    def _add_row(
        self,
        key: str,
        label: str,
        value: Any,
        *,
        editable: bool,
        lockable: bool,
        locked: bool,
        display_text: str | None,
    ) -> None:
        label_width = self.column_header.sizes()[0]
        row = MetadataRow(
            key,
            label,
            value,
            editable=editable,
            lockable=lockable,
            locked=locked,
            label_width=label_width,
            expand_abstract=self._expand_abstracts,
            link_html=display_text,
            parent=self._rows,
        )
        if REFERENCE_METADATA_KEY in self._metadata and key in {
            "bibcode",
            *REFERENCE_FIELD_MAP.values(),
        }:
            row.restriction = "Edit in File → Local References…"
            row.name.setToolTip(row.name.toolTip() + " " + row.restriction)
        row.menu_closed.connect(self._focus_metadata_row)
        row.label_width_requested.connect(self._resize_label_column)
        row.label_width_reset.connect(self.reset_label_width)
        if isinstance(row.link_label, DetailLinkLabel):
            row.link_label.link_activation = self._link_activation
            row.link_label._sync_activation_feedback()
        row.set_label_bold(self._label_bold)
        row.set_action_button_visibility(
            show_edit=self._show_edit_buttons and not self._compact_rows,
            show_lock=self._show_lock_buttons and not self._compact_rows,
        )
        if key == "status" and verification_stale(self._record or {}):
            row.value.setText("PDF changed")
            row.value.setToolTip(
                f"{STALE_VERIFICATION_TOOLTIP} Click to copy the stored status."
            )
            row.value.setAccessibleDescription(STALE_VERIFICATION_TOOLTIP)
        elif key == "status" and value in ("resolved", "unresolved"):
            explanation = (
                "ADS record linked. This does not verify the PDF contents."
                if value == "resolved"
                else (
                    "Metadata is available. ADS lookup is pending. "
                    "Existing metadata is retained."
                    if self._metadata
                    else "ADS lookup is pending. Run Identify to check known identifiers in batches."
                )
            )
            row.value.setToolTip(f"{explanation} Click to copy the stored status.")
            row.value.setAccessibleDescription(explanation)
        if key == "sha256" and isinstance(row.value, CopyableLabel):
            row.value.setText(self._display_digest(str(value)))
            row.value.set_copy_text(str(value))
        row.set_content_style(
            self._row_fonts.get(key, self._content_font),
            self._row_colors.get(key, self._content_color),
        )
        row.copied.connect(
            lambda copied_key: self.copy_feedback.emit(self.row_label(copied_key))
        )
        row.move_requested.connect(
            lambda field, delta: self.move_row(
                field, self.row_keys().index(field) + delta
            )
        )
        row.edit_requested.connect(self._edit_metadata)
        row.lock_changed.connect(self._set_lock)
        row.link_requested.connect(self._open_external_url)
        row.size_changed.connect(self._refresh_rows_geometry)
        self._row_widgets[key] = row
        self._row_labels[key] = label
        self._labels[key] = row.value
        self._rows.rows_layout.addWidget(row)
        if self._rows.isVisible():
            row.show()

    def _focus_metadata_row(self, key: str) -> None:
        """Restore field focus after a popup or metadata refresh."""
        row = self._row_widgets.get(key)
        if row is not None:
            row.setFocus(Qt.PopupFocusReason)

    def _edit_metadata(self, key: str) -> None:
        original = self._metadata.get(key, "")
        initial = (
            "\n".join(self._as_list(original)) if key in LIST_FIELDS else str(original)
        )
        prompt = "One value per line" if key in LIST_FIELDS else "Value"
        value, accepted = QInputDialog.getMultiLineText(
            self,
            f"Edit {self.row_label(key)}",
            prompt,
            initial,
        )
        if not accepted:
            return
        self._metadata[key] = (
            [line for line in value.splitlines() if line.strip()]
            if key in LIST_FIELDS
            else value
        )
        self._pending_field_focus = (
            self._root,
            str((self._record or {}).get("current_path") or ""),
            key,
        )
        self.metadata_changed.emit(copy.deepcopy(self._metadata))
        self._pending_field_focus = None

    def _set_lock(self, key: str, locked: bool) -> None:
        current = {
            str(value)
            for value in self._metadata.get(LOCKED_METADATA_FIELDS_KEY, [])
            if str(value).strip()
        }
        if locked:
            current.add(key)
        else:
            current.discard(key)
        self._metadata[LOCKED_METADATA_FIELDS_KEY] = sorted(current)
        self._pending_field_focus = (
            self._root,
            str((self._record or {}).get("current_path") or ""),
            key,
        )
        self.metadata_changed.emit(copy.deepcopy(self._metadata))
        self._pending_field_focus = None

    def _request_ads_update(self) -> None:
        self.ads_update_requested.emit(str(self._metadata.get("bibcode") or ""))

    def _open_external_url(self, url: str) -> None:
        open_url(url, self._web_browser_command)

    def set_timestamp_format(self, timestamp_format: str) -> None:
        """Set the timestamp format used for subsequently rendered records."""
        self._timestamp_format = timestamp_format

    def set_content_font(self, font: QFont | None, color: QColor | None) -> None:
        """Set the appearance applied to detail values on future renders."""
        self._content_font = font
        self._content_color = color

    def set_row_styles(
        self, fonts: dict[str, QFont], colors: dict[str, QColor]
    ) -> None:
        """Set field-specific appearance overrides for future detail renders."""
        self._row_fonts = dict(fonts)
        self._row_colors = dict(colors)

    @staticmethod
    def _label_for_key(key: str) -> str:
        return FIELD_LABELS.get(key, key.replace("_", " ").capitalize())

    @staticmethod
    def _as_list(value: Any) -> list[str]:
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value if str(item).strip()]
        return [str(value)] if value not in (None, "") else []

    @staticmethod
    def _display_text(value: str) -> str:
        """Insert invisible wrap opportunities without changing copied text."""
        if "<" not in value:
            return _wrap_opportunities(value)
        wrapper = _MarkupWrapper()
        wrapper.feed(value)
        wrapper.close()
        return wrapper.result()

    @staticmethod
    def _display_digest(value: str) -> str:
        """Group a SHA-256 hexadecimal digest into readable wrap-safe blocks."""
        return "\u200b".join(
            value[index : index + 8] for index in range(0, len(value), 8)
        )

    @staticmethod
    def _join(value: Any, separator: str = ", ") -> str:
        if isinstance(value, (list, tuple)):
            return separator.join(html_to_text(str(item)) for item in value)
        return "" if value is None else html_to_text(str(value))

    @staticmethod
    def _human_size(value: Any) -> str:
        try:
            size = float(value or 0)
        except (TypeError, ValueError):
            return ""
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.1f} {unit}"
            size /= 1024
        return ""

    def _format_mtime(self, value: Any) -> str:
        return format_mtime({"mtime_ns": value}, self._timestamp_format)

    @staticmethod
    def _ads_links(bibcode: str) -> str:
        encoded = quote(bibcode, safe="")
        return (
            f'<a href="https://adsabs.harvard.edu/abs/{encoded}">Classic ADS</a>'
            " · "
            f'<a href="https://scixplorer.org/abs/{encoded}/abstract">SciX</a>'
        )

    def _open(self) -> None:
        open_path(self._path, self._pdf_viewer_command)

    def _reveal(self) -> None:
        reveal_in_file_manager(self._path)
