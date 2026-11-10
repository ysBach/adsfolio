"""Find settings by their visible text, explanation, or choices."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from PySide6.QtGui import QBrush, QColor, QTextDocumentFragment
from PySide6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QGraphicsDropShadowEffect,
    QGroupBox,
    QLabel,
    QLineEdit,
    QScrollArea,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QWidget,
)

from .form_help import FieldHelpButton

# Translucent marker color, the same as the table search highlight.
MATCH_COLOR = QColor(255, 204, 0, 160)


def normalize(text: str) -> str:
    """Return `text` without accents, mnemonic marks, or letter case."""
    decomposed = unicodedata.normalize("NFKD", text.replace("&", ""))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def matches(query: str, text: str) -> bool:
    """Return whether every word of `query` occurs in `text`."""
    words = normalize(query).split()
    folded = normalize(text)
    return bool(words) and all(word in folded for word in words)


def _plain(text: str) -> str:
    """Return the readable text of plain text or HTML."""
    if "<" in text and ">" in text:
        return QTextDocumentFragment.fromHtml(text).toPlainText()
    return text


@dataclass
class Match:
    """One setting that matches a search.

    Exactly one of `widget`, `item`, or `tab` names the matched control.
    """

    widget: QWidget | None = None
    item: QTableWidgetItem | None = None
    tab: tuple[QTabWidget, int] | None = None

    @property
    def anchor(self) -> QWidget | None:
        """Return the widget to scroll to and to show."""
        if self.widget is not None:
            return self.widget
        if self.item is not None:
            return self.item.tableWidget()
        if self.tab is not None:
            return self.tab[0].widget(self.tab[1])
        return None


def _shown_in(widget: QWidget, root: QWidget) -> bool:
    """Return whether `widget` shows when `root` and its tab pages show.

    A stack hides its other pages, so a page in a stack counts as shown.
    """
    current: QWidget | None = widget
    while current is not None and current is not root:
        parent = current.parentWidget()
        if current.isHidden() and not isinstance(parent, QStackedWidget):
            return False
        current = parent
    return current is root


def _widget_text(widget: QWidget) -> str:
    """Return the visible text, choices, and explanation of one control."""
    parts: list[str] = []
    if isinstance(widget, QLabel):
        parts.append(_plain(widget.text()))
    elif isinstance(widget, QAbstractButton):
        parts.append(widget.text())
    elif isinstance(widget, QGroupBox):
        parts.append(widget.title())
    elif isinstance(widget, QComboBox):
        parts.extend(widget.itemText(index) for index in range(widget.count()))
    elif isinstance(widget, QLineEdit):
        parts.append(widget.placeholderText())
    else:
        return ""
    parts.append(_plain(widget.toolTip()))
    return " ".join(part for part in parts if part)


def find_matches(page: QWidget, query: str) -> list[Match]:
    """Return the shown controls of `page` that match `query`, in tab order."""
    found: list[Match] = []
    if not normalize(query).split():
        return found
    for widget in page.findChildren(QWidget):
        if isinstance(widget, FieldHelpButton) or not _shown_in(widget, page):
            continue
        if isinstance(widget, QTabWidget):
            for index in range(widget.count()):
                if matches(query, widget.tabText(index)):
                    found.append(Match(tab=(widget, index)))
            continue
        if isinstance(widget, QTableWidget):
            for row in range(widget.rowCount()):
                item = widget.item(row, 0)
                if (
                    item is not None
                    and not widget.isRowHidden(row)
                    and matches(query, f"{item.text()} {item.toolTip()}")
                ):
                    found.append(Match(item=item))
            continue
        text = _widget_text(widget)
        if text and matches(query, text):
            found.append(Match(widget=widget))
    return found


class Highlighter:
    """Mark matched settings, and remove the marks again."""

    def __init__(self) -> None:
        self._widgets: list[QWidget] = []
        self._items: list[tuple[QTableWidgetItem, QBrush]] = []

    def clear(self) -> None:
        """Remove every mark."""
        for widget in self._widgets:
            try:
                widget.setGraphicsEffect(None)
            except RuntimeError:
                # Qt already deleted the widget.
                pass
        for item, brush in self._items:
            try:
                item.setBackground(brush)
            except RuntimeError:
                pass
        self._widgets.clear()
        self._items.clear()

    def mark(self, found: list[Match]) -> None:
        """Mark `found` and remove earlier marks."""
        self.clear()
        for match in found:
            if match.widget is not None and match.widget.graphicsEffect() is None:
                glow = QGraphicsDropShadowEffect(match.widget)
                glow.setColor(MATCH_COLOR)
                glow.setBlurRadius(14)
                glow.setOffset(0, 0)
                match.widget.setGraphicsEffect(glow)
                self._widgets.append(match.widget)
            elif match.item is not None:
                self._items.append((match.item, match.item.background()))
                match.item.setBackground(MATCH_COLOR)

    @property
    def marked_widgets(self) -> tuple[QWidget, ...]:
        """Return the widgets that carry a mark."""
        return tuple(self._widgets)


def reveal(match: Match) -> None:
    """Show the tab that holds `match` and scroll it into view."""
    if match.tab is not None:
        match.tab[0].setCurrentIndex(match.tab[1])
    anchor = match.anchor
    if anchor is None:
        return
    child: QWidget = anchor
    parent = anchor.parentWidget()
    while parent is not None:
        if isinstance(parent, QStackedWidget) and isinstance(
            parent.parentWidget(), QTabWidget
        ):
            parent.parentWidget().setCurrentWidget(child)
        child = parent
        parent = parent.parentWidget()
    if match.item is not None:
        match.item.tableWidget().scrollToItem(match.item)
    current = anchor.parentWidget()
    while current is not None:
        if isinstance(current, QScrollArea):
            current.ensureWidgetVisible(anchor)
            break
        current = current.parentWidget()
