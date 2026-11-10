"""Settings can show the library of each combined PDF in its own column."""

from __future__ import annotations

import hashlib

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt

from adsfolio.config import Config
from adsfolio.gui import preferences as preferences_module
from adsfolio.gui.config import GuiConfig, RootEntry, load_config, save_config
from adsfolio.gui.main_window import MainWindow
from adsfolio.gui.preferences import PreferencesDialog


def _config(roots, **options) -> GuiConfig:
    options.setdefault("show_folders", False)
    # Filename shows the path, so the Library / Filename column shows both.
    options.setdefault("filename_shows_path", True)
    return GuiConfig(
        roots=[
            RootEntry(str(root), label=f"Library {index}", recursive=True)
            for index, root in enumerate(roots)
        ],
        combined_view=True,
        combined_roots=[str(root) for root in roots],
        check_updates_automatically=False,
        citation_styles=[],
        **options,
    )


@pytest.fixture
def setup(qtbot, monkeypatch, tmp_path):
    config_dir = tmp_path / "config"
    monkeypatch.setattr(MainWindow, "_queue_startup_ads_rechecks", lambda self: None)
    monkeypatch.setattr(MainWindow, "_queue_added_ads_recheck", lambda self, root: None)
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: config_dir))
    monkeypatch.setattr(Config, "token_source", classmethod(lambda cls: None))
    roots = [tmp_path / "first", tmp_path / "second"]
    for index, root in enumerate(roots):
        (root / "sub").mkdir(parents=True)
        (root / "sub" / f"paper{index}.pdf").write_bytes(f"fictional {index}".encode())
    windows = []

    def open_window(**options) -> MainWindow:
        if options or not windows:
            save_config(_config(roots, **options))
        window = MainWindow()
        qtbot.addWidget(window)
        qtbot.waitUntil(lambda: not window._startup_roots_pending)
        window.resize(1400, 700)
        window.show()
        windows.append(window)
        return window

    hashes = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for root in roots
        for path in root.rglob("*.pdf")
    }
    yield open_window, roots
    for window in windows:
        window.close()
    assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in hashes} == (
        hashes
    )


def _save_option(qtbot, window, enabled: bool) -> PreferencesDialog:
    settings = PreferencesDialog(window)
    qtbot.addWidget(settings)
    settings.show_page("Appearance")
    settings.appearance.split_library_checkbox.setChecked(enabled)
    settings._save_appearance()
    return settings


def _index(window, key: str) -> int:
    return next(i for i, column in enumerate(window.columns) if column.key == key)


def _cell(window, key: str, row: int = 0) -> str:
    return window.model.index(row, _index(window, key)).data(Qt.DisplayRole)


def test_settings_checkbox_splits_library_and_filename(setup, qtbot):
    open_window, _roots = setup
    window = open_window()
    assert "library" not in [column.key for column in window.columns]
    assert window.columns[_index(window, "filename")].header == "Library / Filename"

    settings = _save_option(qtbot, window, True)

    assert settings.appearance_feedback.text() == "Appearance saved."
    assert load_config().split_library_column is True
    library = _index(window, "library")
    filename = _index(window, "filename")
    header = window.table.header()
    assert header.visualIndex(library) == header.visualIndex(filename) - 1
    assert window.columns[filename].header == "Filename"
    assert not window.table.isColumnHidden(library)
    assert window.table.columnWidth(library) > 0
    assert _cell(window, "library").startswith("Library ")
    assert _cell(window, "filename").startswith("sub/paper")

    _save_option(qtbot, window, False)

    assert "library" not in [column.key for column in window.columns]
    assert "library" not in load_config().table_column_order
    assert _cell(window, "filename").startswith("Library ")


def test_one_library_hides_the_column_and_keeps_its_width(setup, qtbot):
    open_window, roots = setup
    window = open_window(split_library_column=True)
    library = _index(window, "library")
    assert not window.table.isColumnHidden(library)
    window.table.setColumnWidth(library, 210)

    window.combined.toggle_item.setCheckState(0, Qt.Unchecked)
    window._select_root(roots[0])

    assert not window.combined.enabled
    assert window.table.isColumnHidden(_index(window, "library"))
    window.close()
    saved = load_config()
    assert saved.column_widths["library"] == 210
    assert "library" not in saved.hidden_table_columns

    restarted = open_window()
    assert restarted.table.isColumnHidden(_index(restarted, "library"))
    restarted.combined.toggle_item.setCheckState(0, Qt.Checked)

    library = _index(restarted, "library")
    assert not restarted.table.isColumnHidden(library)
    assert restarted.table.columnWidth(library) == 210


def test_library_sort_turns_into_filename_sort_when_the_option_is_off(setup, qtbot):
    open_window, _roots = setup
    window = open_window(split_library_column=True)
    assert window.apply_sort("library", True, "title")

    _save_option(qtbot, window, False)

    assert window.current_query.order_by == "filename"
    assert window.current_query.secondary_order_by == "title"
    saved = load_config()
    assert (saved.sort_column, saved.secondary_sort_column) == ("filename", "title")
    header = window.table.header()
    assert header.sortIndicatorSection() == _index(window, "filename")
    assert window.model.rowCount() == 2


def test_leftover_library_sort_works_in_one_library(setup, qtbot):
    open_window, roots = setup
    window = open_window(split_library_column=True)
    assert window.apply_sort("library", False, "title")

    window.combined.toggle_item.setCheckState(0, Qt.Unchecked)
    window._select_root(roots[1])

    assert window.model.rowCount() == 1
    assert window.model.record_at(0)["current_path"] == "sub/paper1.pdf"


def test_failed_save_restores_the_columns(setup, qtbot, monkeypatch):
    open_window, _roots = setup
    window = open_window()

    def fail(_config):
        raise OSError("fictional disk error")

    monkeypatch.setattr(preferences_module, "save_config", fail)
    settings = _save_option(qtbot, window, True)

    assert "fictional disk error" in settings.appearance_feedback.text()
    assert window.config.split_library_column is False
    assert "library" not in [column.key for column in window.columns]
    assert window.columns[_index(window, "filename")].header == "Library / Filename"


def test_header_menu_lists_library_only_in_the_combined_view(setup, qtbot):
    open_window, roots = setup
    window = open_window(split_library_column=True)
    window._set_column_visibility("library", False)
    assert window.table.isColumnHidden(_index(window, "library"))
    assert "library" in window.config.hidden_table_columns

    window.combined.toggle_item.setCheckState(0, Qt.Unchecked)
    window._select_root(roots[0])
    window._set_column_visibility("title", False)
    window._show_all_columns()

    assert window.table.isColumnHidden(_index(window, "library"))
    window.combined.toggle_item.setCheckState(0, Qt.Checked)
    assert not window.table.isColumnHidden(_index(window, "library"))


def test_folder_tree_keeps_selection_and_tree_column(setup, qtbot):
    from PySide6.QtCore import QItemSelectionModel

    open_window, _roots = setup
    window = open_window(show_folders=True)
    tree = window.combined.tree_model
    while tree is not None and tree.is_loading:
        tree._load_next_page()
    assert window.table.model() is tree
    index = next(
        window._view_table_index(window.model.index(row, 0))
        for row in range(window.model.rowCount())
        if window.model.record_at(row)["current_path"] == "sub/paper1.pdf"
    )
    selection = window.table.selectionModel()
    selection.select(
        index, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows
    )
    selection.setCurrentIndex(index, QItemSelectionModel.NoUpdate)
    window.resize(600, 700)
    horizontal = window.table.horizontalScrollBar()
    horizontal.setValue(horizontal.maximum() // 2)
    left = horizontal.value()

    _save_option(qtbot, window, True)
    tree = window.combined.tree_model
    while tree is not None and tree.is_loading:
        tree._load_next_page()

    assert window.table.model() is tree
    assert window.table.treePosition() == _index(window, "filename")
    assert [row["current_path"] for row in window._selected_records()] == [
        "sub/paper1.pdf"
    ]
    assert window._selected_record()["current_path"] == "sub/paper1.pdf"
    assert horizontal.value() == left
