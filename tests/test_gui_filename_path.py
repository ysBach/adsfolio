"""The Filename column shows names only, or paths after a click on its icon."""

from __future__ import annotations

import hashlib

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QItemSelectionModel, QPoint, Qt

from adsfolio.config import Config
from adsfolio.gui.config import GuiConfig, RootEntry, load_config, save_config
from adsfolio.gui.main_window import MainWindow
from adsfolio.gui.preferences import PreferencesDialog


@pytest.fixture
def setup(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    monkeypatch.setattr(MainWindow, "_queue_startup_ads_rechecks", lambda self: None)
    monkeypatch.setattr(MainWindow, "_offer_ads_recheck", lambda self: None)
    roots = [tmp_path / "first", tmp_path / "second"]
    (roots[0] / "zeta").mkdir(parents=True)
    (roots[0] / "zeta" / "alpha.pdf").write_bytes(b"%PDF-1.4 fictional one")
    (roots[0] / "beta.pdf").write_bytes(b"%PDF-1.4 fictional two")
    roots[1].mkdir()
    (roots[1] / "gamma.pdf").write_bytes(b"%PDF-1.4 fictional three")
    hashes = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for root in roots
        for path in root.rglob("*.pdf")
    }
    windows = []

    def open_window(**options) -> MainWindow:
        if options or not windows:
            options.setdefault("show_folders", False)
            save_config(
                GuiConfig(
                    roots=[
                        RootEntry(str(root), label=f"Library {i}", recursive=True)
                        for i, root in enumerate(roots)
                    ],
                    last_root=str(roots[0]),
                    check_updates_automatically=False,
                    citation_styles=[],
                    **options,
                )
            )
        window = MainWindow()
        qtbot.addWidget(window)
        qtbot.waitUntil(lambda: not window._startup_roots_pending)
        window.resize(1400, 700)
        window.show()
        qtbot.waitExposed(window)
        windows.append(window)
        return window

    yield open_window, roots
    for window in windows:
        window.close()
    assert {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in hashes
    } == hashes


def _filename(window) -> int:
    return next(i for i, c in enumerate(window.columns) if c.key == "filename")


def _shown(window) -> list[str]:
    column = _filename(window)
    return [
        window.model.index(row, column).data(Qt.DisplayRole)
        for row in range(window.model.rowCount())
    ]


def _click_icon(qtbot, window) -> None:
    rect = window.table.header_icon_rect(_filename(window))
    assert not rect.isEmpty()
    qtbot.mouseClick(window.table.header().viewport(), Qt.LeftButton, pos=rect.center())


def _click_header_text(qtbot, window) -> None:
    header = window.table.header()
    logical = _filename(window)
    position = QPoint(
        header.sectionViewportPosition(logical) + header.sectionSize(logical) - 30,
        header.height() // 2,
    )
    qtbot.mouseClick(header.viewport(), Qt.LeftButton, pos=position)


def test_names_by_default_and_icon_shows_paths(setup, qtbot):
    open_window, _roots = setup
    window = open_window()
    assert sorted(_shown(window)) == ["alpha.pdf", "beta.pdf"]
    column = _filename(window)
    assert window.model.headerData(column, Qt.Horizontal, Qt.DecorationRole)
    assert "folder icon" in window.model.headerData(
        column, Qt.Horizontal, Qt.ToolTipRole
    )
    index = window._view_table_index(
        window.model.index(_shown(window).index("alpha.pdf"), 0)
    )
    window.table.selectionModel().select(
        index, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows
    )
    window.table.selectionModel().setCurrentIndex(index, QItemSelectionModel.NoUpdate)
    query = window.current_query

    _click_icon(qtbot, window)

    assert sorted(_shown(window)) == ["beta.pdf", "zeta/alpha.pdf"]
    assert window.current_query == query
    assert window._selected_record()["current_path"] == "zeta/alpha.pdf"
    assert window.filename_path_action.isChecked()
    assert load_config().filename_shows_path is True

    _click_icon(qtbot, window)

    assert sorted(_shown(window)) == ["alpha.pdf", "beta.pdf"]
    assert load_config().filename_shows_path is False


def test_header_click_sorts_by_what_the_column_shows(setup, qtbot):
    open_window, _roots = setup
    window = open_window(sort_column="title", sort_descending=False)

    _click_header_text(qtbot, window)

    assert window.current_query.order_by == "file_name"
    assert _shown(window) == ["alpha.pdf", "beta.pdf"]
    assert window.table.header().sortIndicatorSection() == _filename(window)

    window.filename_path_action.trigger()
    _click_header_text(qtbot, window)
    _click_header_text(qtbot, window)

    assert window.current_query.order_by == "filename"
    assert not window.current_query.descending
    assert _shown(window) == ["beta.pdf", "zeta/alpha.pdf"]


def test_sorting_settings_offer_path_and_name(setup, qtbot):
    open_window, _roots = setup
    window = open_window()
    settings = PreferencesDialog(window)
    qtbot.addWidget(settings)
    settings.show_page("Sorting")
    labels = {
        settings.sort_primary.itemData(i): settings.sort_primary.itemText(i)
        for i in range(settings.sort_primary.count())
    }

    assert labels["filename"] == "Filename: path"
    assert labels["file_name"] == "Filename: name only"
    settings.sort_primary.setCurrentIndex(settings.sort_primary.findData("filename"))
    settings.sort_direction.setCurrentIndex(settings.sort_direction.findData(False))
    settings._save_sorting()

    # Names show, and rows still sort by path.
    assert window.current_query.order_by == "filename"
    assert _shown(window) == ["beta.pdf", "alpha.pdf"]


def test_combined_view_shows_names_and_restart_keeps_paths(setup, qtbot):
    open_window, roots = setup
    window = open_window(
        combined_view=True, combined_roots=[str(root) for root in roots]
    )
    assert window.combined.enabled
    assert window.columns[_filename(window)].header == "Filename"
    assert sorted(_shown(window)) == ["alpha.pdf", "beta.pdf", "gamma.pdf"]

    window.filename_path_action.trigger()
    assert window.columns[_filename(window)].header == "Library / Filename"
    assert sorted(_shown(window)) == [
        "Library 0 / beta.pdf",
        "Library 0 / zeta/alpha.pdf",
        "Library 1 / gamma.pdf",
    ]
    window.close()

    restarted = open_window()
    assert restarted.config.filename_shows_path
    assert "Library 1 / gamma.pdf" in _shown(restarted)


def test_folder_tree_hides_the_icon_and_disables_the_toggle(setup, qtbot):
    open_window, _roots = setup
    window = open_window(show_folders=True)
    tree = window.combined.tree_model
    assert window.table.model() is tree
    column = _filename(window)

    assert tree.headerData(column, Qt.Horizontal, Qt.DecorationRole) is None
    assert window.table.header_icon_rect(column).isEmpty()
    assert not window.filename_path_action.isEnabled()

    window.folders_action.trigger()

    assert window.table.model() is window.model
    assert window.filename_path_action.isEnabled()
    assert not window.table.header_icon_rect(column).isEmpty()
