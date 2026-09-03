"""Main-window integration tests for adding local records to ADS libraries."""

from __future__ import annotations

import os
import sqlite3
from copy import deepcopy
from pathlib import Path
from typing import Any, ClassVar

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QItemSelectionModel
from PySide6.QtWidgets import QApplication, QDialog, QMenu

from adsapi.config import Config
from adsapi.gui.library import FileQuery, LibraryView
from adsapi.gui.main_window import MainWindow


@pytest.fixture(scope="module")
def application() -> QApplication:
    """Provide one Qt application for main-window integration tests."""
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    qtbot: Any,
    tmp_path: Path,
) -> MainWindow:
    """Create a main window with an isolated configuration directory."""
    monkeypatch.setattr(
        Config,
        "get_config_dir",
        classmethod(lambda cls: tmp_path / "config"),
    )
    created = MainWindow()
    qtbot.addWidget(created)
    return created


class FakeRegistry:
    """Return complete resolved records without opening SQLite."""

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records
        self.calls: list[dict[str, Any]] = []

    def query_files(self, **filters: Any) -> list[dict[str, Any]]:
        self.calls.append(filters)
        return deepcopy(self.records)

    def close(self) -> None:
        pass


class CapturingAdsLibraryDialog(QDialog):
    """Capture dialog constructor values while retaining QDialog behavior."""

    instances: ClassVar[list[CapturingAdsLibraryDialog]] = []

    def __init__(
        self,
        root: Path,
        selected_records,
        all_records,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.root = root
        self.selected_records = selected_records
        self.all_records = all_records
        self.was_shown = False
        self.instances.append(self)

    def show(self) -> None:
        self.was_shown = True
        super().show()


def _set_current_root(
    window: MainWindow,
    root: Path,
    registry: FakeRegistry | None = None,
) -> Path:
    """Install a normalized fake registry as the current library root."""
    normalized = root.resolve()
    window.current_root = root
    if registry is not None:
        window.registries[str(normalized)] = registry
    window._sync_ads_library_action()
    return normalized


def _record(index: int) -> dict[str, Any]:
    """Build a mutable record with nested metadata for snapshot checks."""
    return {
        "location_id": index,
        "relative_path": f"paper-{index}.pdf",
        "current_path": f"paper-{index}.pdf",
        "status": "resolved",
        "metadata": {"bibcode": f"B{index}", "title": [f"Title {index}"]},
    }


def _select_rows(window: MainWindow, rows: list[int]) -> None:
    """Select complete table rows using the native selection model."""
    selection = window.table.selectionModel()
    selection.clearSelection()
    for row in rows:
        selection.select(
            window.model.index(row, 0),
            QItemSelectionModel.SelectionFlag.Select
            | QItemSelectionModel.SelectionFlag.Rows,
        )


def test_ads_library_action_requires_a_current_root(window: MainWindow, tmp_path: Path):
    """The Tools action is available only for a selected local root."""
    assert window.add_to_ads_library_action.text() == "Add to ADS Library…"
    assert not window.add_to_ads_library_action.isEnabled()

    _set_current_root(window, tmp_path / "library")

    assert window.add_to_ads_library_action.isEnabled()


def test_table_context_selection_preserves_selected_rows_and_selects_new_row(
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Context-clicking a selected row preserves the selection set."""
    _set_current_root(window, tmp_path / "library")
    window.model._rows = [_record(index) for index in range(3)]
    window.show()
    QApplication.processEvents()
    menus: list[QMenu] = []
    import adsapi.gui.main_window as main_window_module

    class NonBlockingMenu(QMenu):
        def exec(self, *_args) -> None:
            menus.append(self)

    monkeypatch.setattr(main_window_module, "QMenu", NonBlockingMenu)

    _select_rows(window, [0, 1])
    row_one_position = window.table.visualRect(window.model.index(1, 0)).center()
    window._table_menu(row_one_position)

    assert [index.row() for index in window.table.selectionModel().selectedRows()] == [
        0,
        1,
    ]
    assert "Add to ADS Library…" in {
        action.text() for action in menus[-1].actions() if not action.isSeparator()
    }

    row_two_position = window.table.visualRect(window.model.index(2, 0)).center()
    window._table_menu(row_two_position)

    assert [index.row() for index in window.table.selectionModel().selectedRows()] == [
        2
    ]


def test_add_to_ads_library_passes_visual_selected_and_complete_resolved_snapshots(
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Dialog inputs ignore table paging/search and cannot alias live records."""
    import adsapi.gui.main_window as main_window_module

    CapturingAdsLibraryDialog.instances.clear()
    monkeypatch.setattr(
        main_window_module,
        "AdsLibraryDialog",
        CapturingAdsLibraryDialog,
    )
    selected_source = [_record(index) for index in range(2)]
    all_source = [_record(index) for index in range(4)]
    registry = FakeRegistry(all_source)
    normalized_root = _set_current_root(
        window,
        tmp_path / "other" / ".." / "library",
        registry,
    )
    window.current_view = LibraryView.RESOLVED
    window.current_query = FileQuery(
        view=LibraryView.RESOLVED,
        search="only-visible-search-result",
    )
    window.model._rows = selected_source
    _select_rows(window, [1, 0])

    window._add_to_ads_library()

    assert len(CapturingAdsLibraryDialog.instances) == 1
    dialog = CapturingAdsLibraryDialog.instances[0]
    assert dialog.root == normalized_root
    assert dialog.parent() is window
    assert dialog.was_shown
    assert dialog.selected_records == tuple(selected_source)
    assert dialog.all_records == tuple(all_source)
    assert isinstance(dialog.selected_records, tuple)
    assert isinstance(dialog.all_records, tuple)
    assert registry.calls == [{"resolved": True}]
    assert len(dialog.all_records) == 4
    assert all(record not in (registry,) for record in dialog.all_records)

    selected_source[0]["metadata"]["title"][0] = "mutated live row"
    all_source[0]["metadata"]["title"][0] = "mutated registry source"
    assert dialog.selected_records[0]["metadata"]["title"] == ["Title 0"]
    assert dialog.all_records[0]["metadata"]["title"] == ["Title 0"]


def test_add_to_ads_library_reports_busy_registry_without_opening_dialog(
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A transient SQLite lock becomes a busy status message."""
    import adsapi.gui.main_window as main_window_module

    opened: list[object] = []
    monkeypatch.setattr(
        main_window_module,
        "AdsLibraryDialog",
        lambda *args, **kwargs: opened.append((args, kwargs)),
    )

    class BusyRegistry:
        def query_files(self, **_filters: Any) -> list[dict[str, Any]]:
            raise sqlite3.OperationalError("database is locked")

        def close(self) -> None:
            pass

    _set_current_root(window, tmp_path / "library", BusyRegistry())

    window._add_to_ads_library()

    assert opened == []
    assert window.status.currentMessage() == "Library is busy; try again in a moment."


def test_add_to_ads_library_propagates_non_busy_sqlite_errors(
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Only transient busy/locked errors are converted into GUI state."""
    import adsapi.gui.main_window as main_window_module

    monkeypatch.setattr(
        main_window_module,
        "AdsLibraryDialog",
        lambda *args, **kwargs: pytest.fail("dialog must not open"),
    )

    class BrokenRegistry:
        def query_files(self, **_filters: Any) -> list[dict[str, Any]]:
            raise sqlite3.OperationalError("malformed query")

        def close(self) -> None:
            pass

    _set_current_root(window, tmp_path / "library", BrokenRegistry())

    with pytest.raises(sqlite3.OperationalError, match="malformed query"):
        window._add_to_ads_library()


def test_only_one_ads_library_dialog_is_open_per_window(
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Repeated launches reuse the existing window-owned dialog."""
    import adsapi.gui.main_window as main_window_module

    CapturingAdsLibraryDialog.instances.clear()
    monkeypatch.setattr(
        main_window_module,
        "AdsLibraryDialog",
        CapturingAdsLibraryDialog,
    )
    registry = FakeRegistry([_record(1)])
    _set_current_root(window, tmp_path / "library", registry)

    window._add_to_ads_library()
    window._add_to_ads_library()

    assert len(CapturingAdsLibraryDialog.instances) == 1
    assert not window.add_to_ads_library_action.isEnabled()

    CapturingAdsLibraryDialog.instances[0].close()
    QApplication.processEvents()

    assert window.add_to_ads_library_action.isEnabled()


def test_ads_library_dialog_parent_teardown_does_not_touch_deleted_action(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Destroying the parent does not update already-destroyed child UI."""
    import adsapi.gui.main_window as main_window_module

    CapturingAdsLibraryDialog.instances.clear()
    monkeypatch.setattr(
        main_window_module,
        "AdsLibraryDialog",
        CapturingAdsLibraryDialog,
    )
    window = MainWindow()
    application.processEvents()
    _set_current_root(window, tmp_path / "library", FakeRegistry([_record(1)]))

    window._add_to_ads_library()
    window.close()
    window.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
