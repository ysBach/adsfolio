"""Check library sorting with isolated catalogs and fictional PDFs."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtWidgets import QApplication, QComboBox, QDialog

from adsfolio.citations import CitationStyle
from adsfolio.config import Config
from adsfolio.gui.config import GuiConfig, RootEntry, load_config, save_config
from adsfolio.gui.library import LibraryView
from adsfolio.gui.main_window import MainWindow
from adsfolio.pdf.registry import PdfRegistry


@pytest.fixture
def fictional_libraries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    roots = [tmp_path / "first", tmp_path / "second"]
    records = (
        (0, "a.pdf", "Shared", "2024", "Baker"),
        (0, "b.pdf", "Other", "2024", "Dunn"),
        (0, "c.pdf", "Shared", "2024", "Adams"),
        (1, "d.pdf", "Shared", "2023", "Clark"),
        (1, "e.pdf", "Shared", "2024", "Evans"),
        (1, "f.pdf", "Third", "2025", "Ford"),
    )
    for root in roots:
        root.mkdir()
    for root_index, name, title, year, author in records:
        root = roots[root_index]
        data = f"%PDF-1.4\nfictional {name}\n%%EOF\n".encode()
        path = root / name
        path.write_bytes(data)
        stat = path.stat()
        registry = PdfRegistry(root)
        registry.upsert_file(
            root=root,
            relative_path=name,
            current_path=name,
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            status="unresolved",
            metadata={"title": title, "year": year, "author": [author]},
        )
        registry.close()
    files = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for root in roots
        for path in root.iterdir()
    }
    save_config(
        GuiConfig(
            roots=[RootEntry(str(root)) for root in roots],
            last_root=str(roots[0]),
            sort_column="year",
            sort_descending=False,
            secondary_sort_column="title",
            tertiary_sort_column="author",
            citation_styles=[
                CitationStyle(id="fictional", name="Fictional", template="{bibcode}")
            ],
            check_updates_automatically=False,
        )
    )
    yield roots
    assert {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in files
    } == files


def _column(window: MainWindow, key: str) -> int:
    return next(i for i, column in enumerate(window.columns) if column.key == key)


def _state(window: MainWindow) -> tuple[str, bool, str | None, str | None]:
    query = window.current_query
    return (
        query.order_by,
        query.descending,
        query.secondary_order_by,
        query.tertiary_order_by,
    )


def _assert_sort(
    window: MainWindow, expected: tuple[str, bool, str | None, str | None]
) -> None:
    assert _state(window) == expected
    query = window.model.query
    assert (
        query.order_by,
        query.descending,
        query.secondary_order_by,
        query.tertiary_order_by,
    ) == expected
    header = window.table.header()
    assert header.sortIndicatorSection() == _column(window, expected[0])
    assert (header.sortIndicatorOrder() == Qt.DescendingOrder) == expected[1]


def _saved_sort() -> tuple[str, bool, str | None, str | None]:
    config = load_config()
    return (
        config.sort_column,
        config.sort_descending,
        config.secondary_sort_column,
        config.tertiary_sort_column,
    )


def _filenames(window: MainWindow) -> list[str]:
    return [
        window.model.record_at(i)["current_path"]
        for i in range(window.model.rowCount())
    ]


def _click_header(qtbot, window: MainWindow, key: str) -> None:
    header = window.table.header()
    index = _column(window, key)
    window.table.scrollTo(window.model.index(0, index))
    position = QPoint(header.sectionViewportPosition(index) + 10, header.height() // 2)
    qtbot.mouseClick(header.viewport(), Qt.LeftButton, pos=position)


def _window(qtbot) -> MainWindow:
    window = MainWindow()
    qtbot.addWidget(window)
    window.resize(1800, 800)
    window.show()
    for index in range(len(window.columns)):
        window.table.setColumnWidth(index, 95)
    return window


@pytest.mark.parametrize(
    "descending, expected",
    [(False, ["b.pdf", "c.pdf", "a.pdf"]), (True, ["a.pdf", "c.pdf", "b.pdf"])],
)
def test_saved_sort_restores_header_and_tie_breaks(
    qtbot, fictional_libraries, descending, expected
):
    config = load_config()
    config.sort_descending = descending
    save_config(config)
    window = _window(qtbot)

    _assert_sort(window, ("year", descending, "title", "author"))
    assert _filenames(window) == expected
    assert _saved_sort() == ("year", descending, "title", "author")
    window.close()


def test_header_sort_survives_navigation_and_restart(qtbot, fictional_libraries):
    roots = fictional_libraries
    window = _window(qtbot)
    directions = []
    for _ in range(2):
        _click_header(qtbot, window, "title")
        expected = _state(window)
        directions.append(expected[1])
        assert expected[0] == "title"
        _assert_sort(window, expected)
        assert _saved_sort() == expected
        assert _filenames(window) == (
            ["a.pdf", "c.pdf", "b.pdf"] if expected[1] else ["b.pdf", "c.pdf", "a.pdf"]
        )
    assert directions[0] != directions[1]

    window.search.setText("Shared")
    window.search_timer.stop()
    window._search_changed()
    _assert_sort(window, expected)
    assert _filenames(window) == (
        ["a.pdf", "c.pdf"] if expected[1] else ["c.pdf", "a.pdf"]
    )
    window._select_view(LibraryView.UNRESOLVED)
    _assert_sort(window, expected)
    window.refresh(roots[0])
    _assert_sort(window, expected)
    window._select_root(roots[1])
    _assert_sort(window, expected)
    assert _filenames(window) == (
        ["e.pdf", "d.pdf"] if expected[1] else ["d.pdf", "e.pdf"]
    )
    window.combined.set_enabled(True)
    _assert_sort(window, expected)
    assert _filenames(window) == (
        ["e.pdf", "d.pdf", "a.pdf", "c.pdf"]
        if expected[1]
        else ["c.pdf", "a.pdf", "d.pdf", "e.pdf"]
    )
    window.refresh(roots[0])
    _assert_sort(window, expected)
    window.combined.set_enabled(False)
    _assert_sort(window, expected)
    window.close()

    reopened = _window(qtbot)
    _assert_sort(reopened, expected)
    assert _saved_sort() == expected
    reopened.close()


def test_unsortable_header_keeps_selection_and_indicator(qtbot, fictional_libraries):
    window = _window(qtbot)
    expected = ("year", False, "title", "author")
    before = _filenames(window)
    _click_header(qtbot, window, "citation:fictional")

    _assert_sort(window, expected)
    assert _filenames(window) == before
    assert _saved_sort() == expected
    _click_header(qtbot, window, "year")
    _assert_sort(window, ("year", True, "title", "author"))
    assert _filenames(window) == ["a.pdf", "c.pdf", "b.pdf"]
    window.close()


def test_sort_priorities_update_header_and_save_immediately(qtbot, fictional_libraries):
    window = _window(qtbot)

    def accept_dialog():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QDialog)
        selectors = dialog.findChildren(QComboBox)
        assert selectors[0].findText("(none)") == -1
        for selector, value in zip(selectors, ("title", "year", "author")):
            selector.setCurrentText(value)
        dialog.accept()

    QTimer.singleShot(0, accept_dialog)
    window._edit_sort_priorities()
    expected = ("title", False, "year", "author")
    _assert_sort(window, expected)
    assert _filenames(window) == ["b.pdf", "c.pdf", "a.pdf"]
    assert _saved_sort() == expected
    window.combined.set_enabled(True)
    _assert_sort(window, expected)
    assert _filenames(window) == ["b.pdf", "d.pdf", "c.pdf", "a.pdf", "e.pdf", "f.pdf"]
    window.close()

    reopened = _window(qtbot)
    _assert_sort(reopened, expected)
    assert _filenames(reopened) == [
        "b.pdf",
        "d.pdf",
        "c.pdf",
        "a.pdf",
        "e.pdf",
        "f.pdf",
    ]
    reopened.close()


def test_column_rebuild_keeps_sort_by_key(qtbot, fictional_libraries):
    window = _window(qtbot)
    window.table.sortByColumn(_column(window, "mtime_ns"), Qt.AscendingOrder)
    expected = ("mtime_ns", False, "title", "author")
    _assert_sort(window, expected)
    window._apply_citation_styles([])
    _assert_sort(window, expected)
    window.combined.set_enabled(True)
    _assert_sort(window, expected)
    window.combined.set_enabled(False)
    _assert_sort(window, expected)
    window.close()


def test_failed_sort_save_reports_failure_and_preserves_session_sort(
    qtbot, monkeypatch, fictional_libraries
):
    window = _window(qtbot)
    original = _saved_sort()

    def fail_save(_config):
        raise OSError("fictional disk failure")

    with monkeypatch.context() as patch:
        patch.setattr("adsfolio.gui.main_window.save_config", fail_save)
        _click_header(qtbot, window, "title")
        expected = _state(window)
        _assert_sort(window, expected)
        assert expected[0] == "title"
        assert _saved_sort() == original
        assert any(
            "fictional disk failure" in notice.message
            for notice in window.notice_bar.queued_notices()
        )
        window.refresh(fictional_libraries[0])
        _assert_sort(window, expected)

    def accept_unchanged_dialog():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QDialog)
        dialog.accept()

    QTimer.singleShot(0, accept_unchanged_dialog)
    window._edit_sort_priorities()
    assert _saved_sort() == expected
    assert not any(
        notice.key == "sort-preference" for notice in window.notice_bar.queued_notices()
    )
    window.close()


def test_unchanged_sort_priorities_do_not_write_config(
    qtbot, monkeypatch, fictional_libraries
):
    window = _window(qtbot)
    saves = []
    monkeypatch.setattr(
        "adsfolio.gui.main_window.save_config", lambda config: saves.append(config)
    )

    def accept_dialog():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QDialog)
        dialog.accept()

    QTimer.singleShot(0, accept_dialog)
    window._edit_sort_priorities()
    assert saves == []
    window.close()


def test_sort_save_keeps_root_that_failed_to_restore(
    qtbot, monkeypatch, fictional_libraries, tmp_path
):
    unavailable = tmp_path / "unavailable"
    config = load_config()
    config.roots.append(RootEntry(str(unavailable)))
    save_config(config)
    original_register = MainWindow._register_root

    def register_root(window, path, *args, **kwargs):
        if path == unavailable:
            raise OSError("fictional inaccessible root")
        return original_register(window, path, *args, **kwargs)

    monkeypatch.setattr(MainWindow, "_register_root", register_root)
    window = _window(qtbot)
    _click_header(qtbot, window, "title")
    assert str(unavailable) in {root.path for root in load_config().roots}
    window.close()
    assert str(unavailable) in {root.path for root in load_config().roots}

    unavailable.mkdir()
    monkeypatch.setattr(MainWindow, "_register_root", original_register)
    reopened = _window(qtbot)
    assert str(unavailable) in reopened.registries
    reopened._remove_root(unavailable)
    reopened.close()
    assert str(unavailable) not in {root.path for root in load_config().roots}


def test_apply_sort_updates_all_priorities_and_reports_save_failure(
    qtbot, monkeypatch, fictional_libraries
):
    window = _window(qtbot)
    calls = []
    original_update = window._update_status

    def update_status():
        calls.append(True)
        original_update()

    monkeypatch.setattr(window, "_update_status", update_status)
    assert window.apply_sort("author", True, "year", "title") is True
    expected = ("author", True, "year", "title")
    _assert_sort(window, expected)
    assert _saved_sort() == expected
    assert _filenames(window) == ["b.pdf", "a.pdf", "c.pdf"]
    assert calls

    for keys in (
        ("citation:fictional", False, None, None),
        ("title", False, "citation:fictional", None),
        ("title", False, None, "missing"),
    ):
        assert window.apply_sort(*keys) is False
        _assert_sort(window, expected)
        assert _saved_sort() == expected

    def fail_save(_config):
        raise OSError("fictional preference failure")

    with monkeypatch.context() as patch:
        patch.setattr("adsfolio.gui.main_window.save_config", fail_save)
        assert window.apply_sort("year", False, "title", "author") is False
        _assert_sort(window, ("year", False, "title", "author"))
        assert _saved_sort() == expected
        assert any(
            "fictional preference failure" in notice.message
            for notice in window.notice_bar.queued_notices()
        )
    window.close()


def test_apply_sort_saves_values_already_set_in_memory(qtbot, fictional_libraries):
    window = _window(qtbot)
    window.config.sort_column = "author"
    window.config.sort_descending = True
    window.config.secondary_sort_column = "year"
    window.config.tertiary_sort_column = "title"

    assert window.apply_sort("author", True, "year", "title") is True
    assert _saved_sort() == ("author", True, "year", "title")
    window.close()


@pytest.mark.parametrize("change_sort", [False, True])
def test_sort_save_includes_newly_registered_roots(
    qtbot, fictional_libraries, tmp_path, change_sort
):
    window = _window(qtbot)
    new_root = tmp_path / "new-library"
    new_root.mkdir()
    window._register_root(new_root)
    assert str(new_root) not in {root.path for root in load_config().roots}

    keys = (
        ("author", False, "year", "title")
        if change_sort
        else ("year", False, "title", "author")
    )
    assert window.apply_sort(*keys) is True
    assert {root.path for root in load_config().roots} == {
        *(str(root) for root in fictional_libraries),
        str(new_root),
    }
    window.close()
    reopened = _window(qtbot)
    assert str(new_root) in reopened.registries
    _assert_sort(reopened, keys)
    reopened.close()


@pytest.mark.parametrize("combined", [False, True])
def test_startup_sort_signal_does_not_save_partial_roots(
    qtbot, monkeypatch, fictional_libraries, combined
):
    config = load_config()
    config.combined_view = combined
    config.combined_roots = [str(root) for root in fictional_libraries]
    save_config(config)
    original_register = MainWindow._register_root
    original_save = save_config
    saves = []

    def save(config):
        saves.append([entry.path for entry in config.roots])
        return original_save(config)

    def register_root(window, path, *args, **kwargs):
        if not window.registries:
            window.table.sortByColumn(_column(window, "title"), Qt.DescendingOrder)
        return original_register(window, path, *args, **kwargs)

    monkeypatch.setattr("adsfolio.gui.main_window.save_config", save)
    monkeypatch.setattr(MainWindow, "_register_root", register_root)
    window = _window(qtbot)
    assert saves == []
    assert window.combined.enabled is combined
    _assert_sort(window, ("year", False, "title", "author"))
    assert {root.path for root in load_config().roots} == {
        str(root) for root in fictional_libraries
    }
    window.close()


def test_visible_header_sorts_after_columns_move(qtbot, fictional_libraries):
    window = _window(qtbot)
    header = window.table.header()
    header.moveSection(header.visualIndex(_column(window, "author")), 0)
    assert window.splitter.isVisible()

    _click_header(qtbot, window, "title")
    expected = _state(window)
    assert expected[0] == "title"
    _assert_sort(window, expected)
    assert _filenames(window) == (
        ["a.pdf", "c.pdf", "b.pdf"] if expected[1] else ["b.pdf", "c.pdf", "a.pdf"]
    )
    assert _saved_sort() == expected
    window.close()
