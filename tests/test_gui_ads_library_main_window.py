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

from PySide6.QtCore import QEvent, QItemSelectionModel, QObject, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QDialog, QMenu

from adsfolio.config import Config
from adsfolio.gui.library import FileQuery, LibraryView
from adsfolio.gui.main_window import MainWindow


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


class CapturingExportAdsLibraryDialog(QDialog):
    """Capture the remote export dialog and emulate busy close handling."""

    instances: ClassVar[list[CapturingExportAdsLibraryDialog]] = []

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.was_shown = False
        self.active = False
        self.cancel_requested = False
        self.instances.append(self)

    def show(self) -> None:
        self.was_shown = True
        super().show()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.active:
            self.cancel_requested = True
            event.ignore()
            return
        super().closeEvent(event)


class FakeCommandJob(QObject):
    """Expose the signals consumed by `MainWindow._watch_job`."""

    output = Signal(str)
    failed = Signal(str)
    finished = Signal(int)

    running = False


def _set_current_root(
    window: MainWindow,
    root: Path,
    registry: FakeRegistry | None = None,
) -> Path:
    """Install a normalized fake registry as the current library root."""
    normalized = root.resolve()
    window.current_root = root
    window._sync_welcome()
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


def test_export_ads_library_action_is_available_without_a_current_root(
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The remote export action is usable independently of local roots."""
    import adsfolio.gui.main_window as main_window_module

    CapturingExportAdsLibraryDialog.instances.clear()
    monkeypatch.setattr(
        main_window_module,
        "ExportAdsLibraryDialog",
        CapturingExportAdsLibraryDialog,
    )

    assert window.export_ads_library_action.isEnabled()
    window.export_ads_library_action.trigger()

    assert len(CapturingExportAdsLibraryDialog.instances) == 1
    assert CapturingExportAdsLibraryDialog.instances[0].was_shown

    CapturingExportAdsLibraryDialog.instances[0].close()
    QApplication.processEvents()
    assert window.export_ads_library_action.isEnabled()


def test_closing_window_defers_while_export_dialog_is_busy(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    qtbot: Any,
    tmp_path: Path,
) -> None:
    """Window teardown waits for a busy export dialog to acknowledge cancellation."""
    import adsfolio.gui.main_window as main_window_module

    monkeypatch.setattr(
        Config,
        "get_config_dir",
        classmethod(lambda cls: tmp_path / "config"),
    )
    CapturingExportAdsLibraryDialog.instances.clear()
    monkeypatch.setattr(
        main_window_module,
        "ExportAdsLibraryDialog",
        CapturingExportAdsLibraryDialog,
    )
    window = MainWindow()
    qtbot.addWidget(window)
    window._export_ads_library()
    dialog = CapturingExportAdsLibraryDialog.instances[0]
    dialog.active = True
    window.show()
    QApplication.processEvents()

    window.close()
    QApplication.processEvents()

    assert dialog.cancel_requested
    assert not window.isHidden()

    dialog.active = False
    dialog.close()
    QApplication.processEvents()
    assert window.isHidden()


def test_ads_library_action_tracks_root_selection_and_removal(
    window: MainWindow,
    tmp_path: Path,
) -> None:
    """Selecting and removing the current root refreshes action eligibility."""
    root = tmp_path / "library"
    root.mkdir()

    window._register_root(root)
    window._select_root(root)

    assert window.current_root == root.resolve()
    assert window.add_to_ads_library_action.isEnabled()

    window._remove_root(root)

    assert window.current_root is None
    assert not window.add_to_ads_library_action.isEnabled()


def test_ads_library_action_tracks_command_job_transitions(
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Command-job start and terminal signals refresh action eligibility."""
    _set_current_root(
        window,
        tmp_path / "library",
        FakeRegistry([_record(1)]),
    )
    monkeypatch.setattr(window, "refresh", lambda *_args, **_kwargs: None)

    failed_job = FakeCommandJob()
    window._watch_job(failed_job)
    assert not window.add_to_ads_library_action.isEnabled()

    failed_job.failed.emit("could not start")
    assert window.add_to_ads_library_action.isEnabled()

    finished_job = FakeCommandJob()
    window._watch_job(finished_job)
    assert not window.add_to_ads_library_action.isEnabled()

    finished_job.finished.emit(0)
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
    import adsfolio.gui.main_window as main_window_module

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
    import adsfolio.gui.main_window as main_window_module

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
    import adsfolio.gui.main_window as main_window_module

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
    import adsfolio.gui.main_window as main_window_module

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


def test_direct_add_to_ads_library_requires_eligible_window_state(
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Direct calls cannot bypass missing-root or active-work guards."""
    import adsfolio.gui.main_window as main_window_module

    CapturingAdsLibraryDialog.instances.clear()
    monkeypatch.setattr(
        main_window_module,
        "AdsLibraryDialog",
        CapturingAdsLibraryDialog,
    )

    window._add_to_ads_library()

    registry = FakeRegistry([_record(1)])
    _set_current_root(window, tmp_path / "library", registry)
    window.job = FakeCommandJob()
    window._add_to_ads_library()

    window.job = None
    active_dialog = QDialog(window)
    window._ads_library_dialog = active_dialog
    window._add_to_ads_library()

    assert registry.calls == []
    assert CapturingAdsLibraryDialog.instances == []

    window._ads_library_dialog = None


@pytest.mark.parametrize("blocker", ["command_job", "ads_dialog"])
def test_table_context_ads_action_uses_window_eligibility(
    blocker: str,
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The context action cannot bypass other active window work."""
    import adsfolio.gui.main_window as main_window_module

    CapturingAdsLibraryDialog.instances.clear()
    monkeypatch.setattr(
        main_window_module,
        "AdsLibraryDialog",
        CapturingAdsLibraryDialog,
    )
    menus: list[QMenu] = []

    class NonBlockingMenu(QMenu):
        def exec(self, *_args) -> None:
            menus.append(self)

    monkeypatch.setattr(main_window_module, "QMenu", NonBlockingMenu)
    registry = FakeRegistry([_record(1)])
    _set_current_root(window, tmp_path / "library", registry)
    window.model._rows = [_record(1)]
    window.show()
    QApplication.processEvents()
    if blocker == "command_job":
        window.job = FakeCommandJob()
    else:
        window._ads_library_dialog = QDialog(window)

    position = window.table.visualRect(window.model.index(0, 0)).center()
    window._table_menu(position)
    action = next(
        action
        for action in menus[-1].actions()
        if action.text() == "Add to ADS Library…"
    )

    assert not action.isEnabled()
    action.trigger()
    assert registry.calls == []
    assert CapturingAdsLibraryDialog.instances == []

    window.job = None
    window._ads_library_dialog = None


def test_only_one_ads_library_dialog_is_open_per_window(
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Repeated launches reuse the existing window-owned dialog."""
    import adsfolio.gui.main_window as main_window_module

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
    import adsfolio.gui.main_window as main_window_module

    CapturingAdsLibraryDialog.instances.clear()
    monkeypatch.setattr(
        main_window_module,
        "AdsLibraryDialog",
        CapturingAdsLibraryDialog,
    )
    monkeypatch.setattr(
        Config,
        "get_config_dir",
        classmethod(lambda cls: tmp_path / "config"),
    )
    window = MainWindow()
    application.processEvents()
    _set_current_root(window, tmp_path / "library", FakeRegistry([_record(1)]))

    window._add_to_ads_library()
    window.close()
    window.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _menu_for_rows(window: MainWindow, monkeypatch, tmp_path: Path, rows: list[int]):
    """Open the table context menu on the first of `rows` with all selected."""
    import adsfolio.gui.main_window as main_window_module

    menus: list[QMenu] = []

    class NonBlockingMenu(QMenu):
        def exec(self, *_args) -> None:
            menus.append(self)

    monkeypatch.setattr(main_window_module, "QMenu", NonBlockingMenu)
    _select_rows(window, rows)
    point = window.table.visualRect(window.model.index(rows[0], 0)).center()
    window._table_menu(point)
    return {action.text(): action for action in menus[-1].actions()}


def _three_records(tmp_path: Path) -> list[dict[str, Any]]:
    records = [_record(index) for index in range(3)]
    records[1]["metadata"]["bibcode"] = ""
    records[0]["metadata"]["doi"] = ["10.1/a"]
    records[2]["metadata"]["doi"] = ["10.1/c"]
    return records


def test_context_menu_actions_cover_every_selected_row(
    window: MainWindow, monkeypatch, tmp_path: Path
):
    root = _set_current_root(window, tmp_path / "library")
    window.model._rows = _three_records(tmp_path)
    window.show()
    QApplication.processEvents()
    actions = _menu_for_rows(window, monkeypatch, tmp_path, [0, 1, 2])
    assert {"Open 3 PDFs", "Reveal 3 Files", "Copy 2 Bibcodes", "Copy 2 DOIs"} <= set(
        actions
    )
    assert "Copy 3 Paths" in actions
    assert "1 selected papers have no Bibcode." in actions["Copy 2 Bibcodes"].toolTip()
    actions["Copy 2 Bibcodes"].trigger()
    assert QApplication.clipboard().text() == "B0\nB2"
    actions["Copy 3 Paths"].trigger()
    assert QApplication.clipboard().text().splitlines() == [
        str(root / f"paper-{index}.pdf") for index in range(3)
    ]
    opened = []
    monkeypatch.setattr(
        window, "_open_pdf_record", lambda record: opened.append(record)
    )
    actions["Open 3 PDFs"].trigger()
    assert [record["location_id"] for record in opened] == [0, 1, 2]


def test_single_row_menu_keeps_single_record_labels(
    window: MainWindow, monkeypatch, tmp_path: Path
):
    _set_current_root(window, tmp_path / "library")
    window.model._rows = _three_records(tmp_path)
    window.show()
    QApplication.processEvents()
    actions = _menu_for_rows(window, monkeypatch, tmp_path, [0])
    assert {"Open", "Copy Bibcode", "Copy DOI", "Export Paper as List…"} <= set(actions)
    assert window.update_selected_ads_action.text() in actions


def test_reveal_opens_each_folder_once(window: MainWindow, monkeypatch, tmp_path: Path):
    revealed = []
    monkeypatch.setattr(
        "adsfolio.gui.main_window.reveal_in_file_manager", revealed.append
    )
    paths = [
        tmp_path / "a" / "1.pdf",
        tmp_path / "a" / "2.pdf",
        tmp_path / "b" / "3.pdf",
    ]
    window._reveal_paths(paths)
    assert revealed == [paths[0], paths[2]]


def test_refreshing_several_rows_chains_one_command_per_bibcode(
    window: MainWindow, monkeypatch, tmp_path: Path
):
    _set_current_root(window, tmp_path / "library")
    window.model._rows = _three_records(tmp_path)
    window.show()
    QApplication.processEvents()
    calls = []
    monkeypatch.setattr(
        window,
        "_start_ads_refresh",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    monkeypatch.setattr(window.refresh_all_ads_action, "isEnabled", lambda: True)
    actions = _menu_for_rows(window, monkeypatch, tmp_path, [0, 1, 2])
    actions["Refresh 2 Selected ADS Records"].trigger()
    ((args, kwargs),) = calls
    assert args == (
        "--bibcode",
        "B0",
        "--relative-path",
        "paper-0.pdf",
        "--citation-metrics",
    )
    assert kwargs["follow_ups"] == (
        ("--bibcode", "B2", "--relative-path", "paper-2.pdf", "--citation-metrics"),
    )


def test_follow_up_refreshes_run_in_the_same_job(
    window: MainWindow, monkeypatch, tmp_path: Path
):
    created = []

    class Job(QObject):
        output = Signal(str)
        failed = Signal(str)
        finished = Signal(int)

        def __init__(self, argv, **kwargs) -> None:
            super().__init__(kwargs.get("parent"))
            self.argv = argv
            self.kwargs = kwargs
            self.running = False
            created.append(self)

        def start(self) -> None:
            pass

        def cancel(self) -> None:
            pass

    monkeypatch.setattr("adsfolio.gui.main_window.CommandJob", Job)
    root = tmp_path / "library"
    window._launch_ads_refresh(
        root,
        ("--bibcode", "B0"),
        selection_id=None,
        ambiguous_relative_path=None,
        follow_ups=(("--bibcode", "B2"),),
    )
    job = created[-1]
    assert job.argv[-3:] == [str(root), "--bibcode", "B0"]
    follow_up = job.kwargs["follow_up_argv"]
    assert [command[-3:] for command in follow_up] == [[str(root), "--bibcode", "B2"]]


def test_export_selected_papers_opens_the_list_window_with_all_rows(
    window: MainWindow, monkeypatch, tmp_path: Path
):
    _set_current_root(window, tmp_path / "library")
    window.model._rows = _three_records(tmp_path)
    window.show()
    QApplication.processEvents()
    opened = []

    class Dialog:
        def __init__(self, parent, **kwargs) -> None:
            opened.append(kwargs)

        def exec(self) -> int:
            return 0

    monkeypatch.setattr("adsfolio.gui.main_window.CVDialog", Dialog)
    _select_rows(window, [0, 2])
    window._sync_detail_and_selection_actions()
    assert window.export_selected_action.isEnabled()
    window.export_selected_action.trigger()
    assert [record["location_id"] for record in opened[-1]["records"]] == [0, 2]
    _select_rows(window, [])
    window._sync_detail_and_selection_actions()
    assert not window.export_selected_action.isEnabled()
