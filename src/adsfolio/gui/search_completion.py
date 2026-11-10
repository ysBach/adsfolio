"""Suggest field names and values while the user types a search query."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from PySide6.QtCore import QEvent, QObject, QStringListModel, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCompleter,
    QLineEdit,
    QStatusBar,
    QWidget,
)

from ..pdf.search import SEARCH_FIELDS, resolve_field

# Status-bar text after the first Esc of a search reset.
ESCAPE_AGAIN_HINT = "Press Esc again to clear the search."
# Fields whose stored values are useful suggestions.
VALUE_FIELDS = frozenset({"author", "first_author", "journal", "year"})
# The token before the cursor: an optional field name, then the value so far.
_TOKEN = re.compile(r'(?:(?P<field>[A-Za-z_]+):)?(?P<quote>")?(?P<value>[^"]*)$')


class SearchCompleter(QObject):
    """Show a popup of completions for the term under the cursor.

    A word without a colon gets field-name suggestions: ``f`` suggests
    ``first_author:`` and ``filename:``. After ``author:``, ``first_author:``,
    ``journal:``, or ``year:``, `values` supplies the suggestions. Choosing
    a suggestion replaces only that term.

    Parameters
    ----------
    line_edit : QLineEdit
        Search field.
    values : Callable[[str], Sequence[str]]
        Return the stored values of one field key, for example author names.
    """

    def __init__(
        self, line_edit: QLineEdit, values: Callable[[str], Sequence[str]]
    ) -> None:
        super().__init__(line_edit)
        self._line_edit = line_edit
        self._values = values
        self._model = QStringListModel(self)
        self.completer = QCompleter(self._model, self)
        self.completer.setWidget(line_edit)
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.setCompletionMode(QCompleter.PopupCompletion)
        self.completer.setFilterMode(Qt.MatchStartsWith)
        self.completer.setMaxVisibleItems(12)
        self.completer.activated.connect(self._insert)
        line_edit.textEdited.connect(self._update)
        self._token_start = 0
        self._field: str | None = None

    def _current_token(self) -> tuple[int, str]:
        """Return the start index and text of the term before the cursor.

        A space ends a term, except inside double quotes.
        """
        text = self._line_edit.text()[: self._line_edit.cursorPosition()]
        start = 0
        in_quote = False
        for index, char in enumerate(text):
            if char == '"':
                in_quote = not in_quote
            elif char.isspace() and not in_quote:
                start = index + 1
        return start, text[start:]

    def _update(self, _text: str = "") -> None:
        """Offer completions for the term before the cursor."""
        start, token = self._current_token()
        match = _TOKEN.match(token)
        if not token or match is None:
            self.completer.popup().hide()
            return
        self._token_start = start
        field_name = match.group("field")
        if field_name is None:
            self._field = None
            prefix = token
            suggestions = [f"{name}:" for name in SEARCH_FIELDS]
        else:
            field = resolve_field(field_name)
            if field not in VALUE_FIELDS:
                self.completer.popup().hide()
                return
            self._field = field
            prefix = match.group("value")
            suggestions = list(self._values(field))
        if not prefix and field_name is None:
            self.completer.popup().hide()
            return
        self._model.setStringList(suggestions)
        self.completer.setCompletionPrefix(prefix)
        if self.completer.completionCount() == 0 or (
            self.completer.completionCount() == 1
            and self.completer.currentCompletion().casefold() == prefix.casefold()
        ):
            self.completer.popup().hide()
            return
        self.completer.complete()

    def _insert(self, completion: str) -> None:
        """Replace the term before the cursor with `completion`."""
        text = self._line_edit.text()
        cursor = self._line_edit.cursorPosition()
        if self._field is None:
            replacement = completion
        else:
            value = f'"{completion}"' if re.search(r'[\s"]', completion) else completion
            replacement = f"{self._field}:{value} "
        updated = text[: self._token_start] + replacement + text[cursor:]
        self._line_edit.setText(updated)
        self._line_edit.setCursorPosition(self._token_start + len(replacement))


class SearchEscape(QObject):
    """Clear the search on the second Esc in a row.

    The first Esc closes the suggestion list and shows `ESCAPE_AGAIN_HINT`.
    A second Esc runs `clear`. Another key, a mouse click, a text change, or
    a deactivated window in between cancels the reset. Header clicks cancel
    the reset and still sort the table. With an empty search, one Esc runs
    `clear`. Connect menu-bar actions to `cancel`, because a click in the
    native macOS menu bar sends no mouse event to the application.

    Parameters
    ----------
    line_edit : QLineEdit
        Search field.
    popup : QWidget
        Suggestion list of the search field.
    widgets : sequence of QWidget
        Widgets in which Esc resets the search.
    clear : Callable[[], None]
        Clear the search.
    status : QStatusBar
        Status bar for the hint.
    """

    def __init__(
        self,
        line_edit: QLineEdit,
        popup: QWidget,
        widgets: Sequence[QWidget],
        clear: Callable[[], None],
        status: QStatusBar,
    ) -> None:
        super().__init__(line_edit)
        self._line_edit = line_edit
        self._popup = popup
        self._clear = clear
        self._status = status
        self._application = QApplication.instance()
        self.armed = False
        self.shortcuts: list[QShortcut] = []
        for widget in widgets:
            shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), widget)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(self.press)
            self.shortcuts.append(shortcut)
        line_edit.textChanged.connect(self.cancel)

    def press(self) -> None:
        """Handle one Esc: close suggestions, then reset on the second Esc."""
        self._popup.hide()
        if self.armed or not self._line_edit.text():
            self.cancel()
            self._clear()
            return
        self.armed = True
        if self._application is not None:
            self._application.installEventFilter(self)
        self._status.showMessage(ESCAPE_AGAIN_HINT)

    def cancel(self, *_args: object) -> None:
        """Forget a first Esc."""
        if self.armed and self._status.currentMessage() == ESCAPE_AGAIN_HINT:
            self._status.clearMessage()
        if self.armed and self._application is not None:
            self._application.removeEventFilter(self)
        self.armed = False

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Cancel a first Esc after another key, a mouse click, or a window change."""
        kind = event.type()
        if kind in {QEvent.Type.KeyPress, QEvent.Type.ShortcutOverride}:
            if event.key() != Qt.Key.Key_Escape:
                self.cancel()
        elif kind == QEvent.Type.MouseButtonPress or (
            kind in {QEvent.Type.Close, QEvent.Type.WindowDeactivate}
            and watched is self._line_edit.window()
        ):
            self.cancel()
        return False
