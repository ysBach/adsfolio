"""Suggest field names and values while the user types a search query."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from PySide6.QtCore import QObject, QStringListModel, Qt
from PySide6.QtWidgets import QCompleter, QLineEdit

from ..pdf.search import SEARCH_FIELDS, resolve_field

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
