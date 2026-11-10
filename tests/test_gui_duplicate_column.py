"""Only the Duplicates view shows the Duplicate column, at a usable width."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from adsfolio.config import Config
from adsfolio.gui.config import GuiConfig, RootEntry, load_config, save_config
from adsfolio.gui.library import DUPLICATE_COLUMN, LibraryView
from adsfolio.gui.main_window import MainWindow
from adsfolio.gui.preferences import PreferencesDialog


@pytest.fixture
def open_window(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    monkeypatch.setattr(MainWindow, "_queue_startup_ads_rechecks", lambda self: None)
    monkeypatch.setattr(MainWindow, "_offer_ads_recheck", lambda self: None)
    root = tmp_path / "library"
    root.mkdir()
    (root / "paper.pdf").write_bytes(b"%PDF-1.4 fictional")
    windows = []

    def factory(**options) -> MainWindow:
        if options or not windows:
            save_config(
                GuiConfig(
                    roots=[RootEntry(str(root))],
                    last_root=str(root),
                    check_updates_automatically=False,
                    show_folders=False,
                    **options,
                )
            )
        window = MainWindow()
        qtbot.addWidget(window)
        qtbot.waitUntil(lambda: not window._startup_roots_pending)
        window.show()
        windows.append(window)
        return window

    yield factory
    for window in windows:
        window.close()


def _duplicate(window) -> int:
    return next(
        index
        for index, column in enumerate(window.columns)
        if column.key == DUPLICATE_COLUMN
    )


def _assert_shown(window) -> None:
    index = _duplicate(window)
    assert not window.table.isColumnHidden(index)
    assert window.table.columnWidth(index) > 0


def test_close_outside_duplicates_view_keeps_the_column(open_window, qtbot):
    window = open_window()
    window._select_view(LibraryView.ALL)
    window.close()
    saved = load_config()
    assert DUPLICATE_COLUMN not in saved.hidden_table_columns
    assert saved.column_widths[DUPLICATE_COLUMN] > 0

    restarted = open_window()
    restarted._select_view(LibraryView.DUPLICATES)
    _assert_shown(restarted)


def test_old_saved_hidden_state_does_not_hide_the_column(open_window, qtbot):
    # Settings that 0.10.0 saved after a close in another view.
    window = open_window(
        hidden_table_columns=[DUPLICATE_COLUMN],
        column_widths={DUPLICATE_COLUMN: 0},
    )
    window._select_view(LibraryView.DUPLICATES)
    _assert_shown(window)

    window._restore_default_column_order()
    _assert_shown(window)
    settings = PreferencesDialog(window)
    qtbot.addWidget(settings)
    settings.show_page("Appearance")
    settings._save_appearance()
    _assert_shown(window)
    window.folders_action.trigger()
    window.folders_action.trigger()
    _assert_shown(window)
    assert DUPLICATE_COLUMN not in window.config.hidden_table_columns


def test_show_all_columns_leaves_the_column_to_its_view(open_window):
    window = open_window()
    window._select_view(LibraryView.ALL)
    window._show_all_columns()

    assert window.table.isColumnHidden(_duplicate(window))


def test_hidden_column_returns_at_its_saved_width(open_window):
    window = open_window(column_widths={"pub": 230})
    index = next(i for i, c in enumerate(window.columns) if c.key == "pub")
    assert window.table.columnWidth(index) == 230
    window._set_column_visibility("pub", False)
    window.close()
    assert load_config().column_widths["pub"] == 230

    restarted = open_window()
    restarted._set_column_visibility("pub", True)
    assert restarted.table.columnWidth(index) == 230
