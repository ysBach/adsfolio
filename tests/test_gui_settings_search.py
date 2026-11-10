"""Search finds settings by their text, explanation, or choices."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt
from test_gui_preferences import preferences_window  # noqa: F401

from adsfolio.gui.preferences import PreferencesDialog


@pytest.fixture
def settings(preferences_window) -> PreferencesDialog:  # noqa: F811
    _window, dialog, _root, _pdf = preferences_window
    dialog.show()
    return dialog


def _sections(dialog) -> list[str]:
    return [
        dialog.categories.item(row).text()
        for row in range(dialog.categories.count())
        if not dialog.categories.isRowHidden(row)
    ]


def test_search_keeps_matching_sections_and_marks_the_setting(settings):
    settings.search.setText("DESCENDING")

    assert _sections(settings) == ["Sorting"]
    assert settings.categories.currentItem().text() == "Sorting"
    assert settings.sort_direction in settings._highlighter.marked_widgets
    assert settings.sort_direction.graphicsEffect() is not None


def test_search_opens_the_tab_that_holds_the_match(settings):
    settings.search.setText("library column")

    assert _sections(settings) == ["Appearance"]
    checkbox = settings.appearance.split_library_checkbox
    assert checkbox in settings._highlighter.marked_widgets
    assert settings.appearance.tabs.currentIndex() == 0


def test_search_finds_a_tab_by_its_title(settings):
    settings.search.setText("time format")

    assert "Appearance" in _sections(settings)
    settings.categories.setCurrentRow(1)
    tabs = settings.appearance.tabs
    assert tabs.tabText(tabs.currentIndex()) == "Time Format"


def test_no_match_reports_it_and_escape_clears_the_search(settings, qtbot):
    settings.search.setText("fictional-no-such-setting")

    assert _sections(settings) == []
    assert settings.search_status.text() == "No settings match."

    qtbot.keyClick(settings.search, Qt.Key_Escape)

    assert settings.search.text() == ""
    assert settings.isVisible()
    assert len(_sections(settings)) == settings.categories.count()
    assert not settings.search_status.isVisible()


def test_return_moves_to_the_first_match_and_clearing_removes_marks(settings, qtbot):
    settings.search.setText("descending")
    qtbot.keyClick(settings.search, Qt.Key_Return)

    # An offscreen window is not active, so check the window's focus widget.
    assert settings.focusWidget() is settings.sort_direction

    settings.search.clear()

    assert settings.sort_direction.graphicsEffect() is None
    assert settings._highlighter.marked_widgets == ()
