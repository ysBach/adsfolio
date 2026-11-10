"""Export Selection only opens for selected PDF rows."""

from __future__ import annotations

import sqlite3
import threading
import time

from PySide6.QtCore import QItemSelectionModel
from PySide6.QtWidgets import QMessageBox
from test_gui_folder_tree import _window

from adsfolio.config import Config
from adsfolio.gui.config import GuiConfig, RootEntry, save_config
from adsfolio.gui.export_dialog import ExportPapersDialog
from adsfolio.gui.library import LibraryView
from adsfolio.gui.main_window import MainWindow
from adsfolio.pdf.registry import PdfRegistry


def _file_index(window, path: str):
    row = next(
        row
        for row in range(window.model.rowCount())
        if window.model.record_at(row)["current_path"] == path
    )
    return window._view_table_index(window.model.index(row, 0))


def test_folder_only_disables_export_selection(monkeypatch, qtbot, tmp_path):
    window, _root = _window(monkeypatch, qtbot, tmp_path)
    folder = _file_index(window, "chapter/inner.pdf").parent()

    window.table.setCurrentIndex(folder)
    window._sync_export_action()

    assert not window._selected_records()
    assert not window.export_selected_action.isEnabled()
    assert window.export_all_action.isEnabled()


def test_mixed_folder_and_pdf_selection_enables_selected_export(
    monkeypatch, qtbot, tmp_path
):
    window, _root = _window(monkeypatch, qtbot, tmp_path)
    folder = _file_index(window, "chapter/inner.pdf").parent()
    file_index = _file_index(window, "top.pdf")
    selection = window.table.selectionModel()
    selection.select(
        folder,
        QItemSelectionModel.SelectionFlag.Select
        | QItemSelectionModel.SelectionFlag.Rows,
    )
    selection.select(
        file_index,
        QItemSelectionModel.SelectionFlag.Select
        | QItemSelectionModel.SelectionFlag.Rows,
    )

    window._sync_export_action()

    assert window.export_selected_action.isEnabled()
    assert [row["current_path"] for row in window._selected_records()] == ["top.pdf"]


def test_programmatic_selection_export_does_not_open_all_scope(
    monkeypatch, qtbot, tmp_path
):
    window, _root = _window(monkeypatch, qtbot, tmp_path)
    window.table.setCurrentIndex(_file_index(window, "chapter/inner.pdf").parent())
    opened = []
    monkeypatch.setattr(
        "adsfolio.gui.main_window.ExportPapersDialog",
        lambda *args, **kwargs: opened.append(kwargs),
    )

    window._export_papers()

    assert opened == []
    assert window.export_all_action.isEnabled()


def test_selected_action_passes_only_selected_pdf_scope(monkeypatch, qtbot, tmp_path):
    window, _root = _window(monkeypatch, qtbot, tmp_path)
    source = window._search_source()
    query_calls = []
    original_query = source.query_files
    monkeypatch.setattr(
        source,
        "query_files",
        lambda **kwargs: query_calls.append(kwargs) or original_query(**kwargs),
    )
    file_index = _file_index(window, "top.pdf")
    window.table.selectionModel().select(
        file_index,
        QItemSelectionModel.SelectionFlag.Select
        | QItemSelectionModel.SelectionFlag.Rows,
    )
    opened = []

    class Dialog:
        format_key = "bibtex"

        def __init__(self, _parent, scopes, _catalog, _styles, **kwargs):
            opened.append((scopes, kwargs))
            self.excluded_fields = []

        def exec(self):
            return 0

        def deleteLater(self):
            pass

    monkeypatch.setattr("adsfolio.gui.main_window.ExportPapersDialog", Dialog)
    window.export_selected_action.trigger()

    scopes, kwargs = opened[0]
    selected = next(scope for scope in scopes if scope.key == "selected")
    assert kwargs["initial_scope"] == "selected"
    assert [row["current_path"] for row in selected.records] == ["top.pdf"]
    all_scope = next(scope for scope in scopes if scope.key == "all")
    assert all_scope.count == 2
    assert all_scope._records is None
    assert query_calls == []


def test_all_scope_reacquires_registry_after_dialog_events(
    monkeypatch, qtbot, tmp_path
):
    window, root = _window(monkeypatch, qtbot, tmp_path)
    file_index = _file_index(window, "top.pdf")
    window.table.selectionModel().select(
        file_index,
        QItemSelectionModel.SelectionFlag.Select
        | QItemSelectionModel.SelectionFlag.Rows,
    )
    replaced_calls = []
    original_registry = window.registries[str(root)]
    replacement = PdfRegistry(
        root, initialized=True, catalog_path=original_registry.path
    )
    original_query = replacement.query_files

    def query_files(**kwargs):
        replaced_calls.append(kwargs)
        return original_query(**kwargs)

    replacement.query_files = query_files

    class Dialog:
        format_key = "bibtex"

        def __init__(self, _parent, scopes, _catalog, _styles, **kwargs):
            self.scopes = scopes
            self.excluded_fields = []

        def exec(self):
            original_registry.close()
            window.registries[str(root)] = replacement
            all_scope = next(scope for scope in self.scopes if scope.key == "all")
            assert [row["current_path"] for row in all_scope.records] == [
                "chapter/inner.pdf",
                "top.pdf",
            ]
            return 0

        def deleteLater(self):
            pass

    monkeypatch.setattr("adsfolio.gui.main_window.ExportPapersDialog", Dialog)
    window._export_papers()

    assert len(replaced_calls) == 1


def test_explicit_combined_all_loads_after_registry_replacement(
    monkeypatch, qtbot, tmp_path
):
    config_dir = tmp_path / "config"
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: config_dir))
    monkeypatch.setattr(Config, "token_source", classmethod(lambda cls: None))
    monkeypatch.setattr(MainWindow, "_queue_startup_ads_rechecks", lambda self: None)
    monkeypatch.setattr(MainWindow, "_queue_added_ads_recheck", lambda self, root: None)
    roots = [tmp_path / "one", tmp_path / "two"]
    for index, root in enumerate(roots):
        root.mkdir()
        (root / "paper.pdf").write_bytes(f"fictional {index}".encode())
    save_config(
        GuiConfig(
            roots=[
                RootEntry(str(root), label=f"Library {index}")
                for index, root in enumerate(roots)
            ],
            check_updates_automatically=False,
        )
    )
    window = MainWindow()
    qtbot.addWidget(window)
    qtbot.waitUntil(lambda: not window._startup_roots_pending)
    window.combined.set_enabled(True)
    original_registry = window.registries[str(roots[0])]
    original_calls = []
    original_query = original_registry.query_records
    original_registry.query_records = lambda **kwargs: (
        original_calls.append(kwargs) or original_query(**kwargs)
    )
    replacement_calls = []
    replacement = PdfRegistry(roots[0])
    replacement_query = replacement.query_records
    replacement.query_records = lambda **kwargs: (
        replacement_calls.append(kwargs) or replacement_query(**kwargs)
    )

    class Dialog:
        format_key = "bibtex"

        def __init__(self, _parent, scopes, _catalog, _styles, **kwargs):
            self.scopes = scopes
            self.excluded_fields = []

        def exec(self):
            window.registries[str(roots[0])] = replacement
            all_scope = next(scope for scope in self.scopes if scope.key == "all")
            assert all_scope.count == 2
            assert len(all_scope.records) == 2
            return 0

        def deleteLater(self):
            pass

    monkeypatch.setattr("adsfolio.gui.main_window.ExportPapersDialog", Dialog)
    window.export_all_action.trigger()

    assert original_calls == []
    assert len(replacement_calls) == 1


def test_export_selection_reports_exclusive_lock_and_recovers(
    monkeypatch, qtbot, tmp_path
):
    from test_gui_refresh_lock import _configure, _seed_catalog

    root = tmp_path / "library"
    _configure(monkeypatch, tmp_path, None)
    database = _seed_catalog(root)
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute("PRAGMA journal_mode = DELETE").fetchone()[0] == "delete"
        )
    window = MainWindow()
    qtbot.addWidget(window)
    registry = PdfRegistry(root, initialized=True, catalog_path=database)
    registry.catalog.set_busy_timeout(5000)
    window._register_root(root, _initialized_registry=registry)
    window._select_root(root)
    registry.catalog.set_busy_timeout(5000)
    index = _file_index(window, "paper.pdf")
    selection = window.table.selectionModel()
    selection.select(
        index,
        QItemSelectionModel.SelectionFlag.Select
        | QItemSelectionModel.SelectionFlag.Rows,
    )
    selection.setCurrentIndex(index, QItemSelectionModel.NoUpdate)
    selected_before = [row["current_path"] for row in window._selected_records()]
    current_before = window._selected_record()["current_path"]
    reports = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args: reports.append(str(args[-1])),
    )
    writer = sqlite3.connect(registry.path, timeout=0)
    writer.execute("BEGIN EXCLUSIVE")
    try:
        started = time.perf_counter()
        window.export_selected_action.trigger()
        assert time.perf_counter() - started < 1
        assert reports and "Wait for the catalog" in reports[-1]
        assert [
            row["current_path"] for row in window._selected_records()
        ] == selected_before
        assert window._selected_record()["current_path"] == current_before
        assert (
            registry.catalog._connection.execute("PRAGMA busy_timeout").fetchone()[0]
            == 5000
        )
    finally:
        writer.rollback()
        writer.close()

    opened = []

    def inspect(dialog):
        timeout = dialog._catalog._connection.execute("PRAGMA busy_timeout")
        opened.append(
            (dialog._current_scope().key, dialog._scope_error, timeout.fetchone()[0])
        )
        return 0

    monkeypatch.setattr(ExportPapersDialog, "exec", inspect)
    window.export_selected_action.trigger()
    # Saves in the window wait out short writes after the zero-wait probe.
    assert opened == [("selected", None, 5000)]


def test_export_all_fails_closed_for_pending_checked_root_and_retries(
    monkeypatch, qtbot, tmp_path
):
    window, available_root = _window(monkeypatch, qtbot, tmp_path)
    pending_root = tmp_path / "pending"
    pending_root.mkdir()
    (pending_root / "pending.pdf").write_bytes(b"fictional pending PDF")
    entry = RootEntry(str(pending_root), label="Pending library")
    pending_key = str(pending_root.resolve())
    window._unrestored_roots.append(entry)
    window.config.roots.append(entry)
    window.root_entries[pending_key] = entry
    window.combined.set_enabled(True)
    window.combined.selected.add(pending_key)
    window._select_view(LibraryView.UNRESOLVED)
    seen = {}

    def inspect(dialog):
        all_scope = next(scope for scope in dialog._scopes if scope.key == "all")
        assert dialog._scope_error is not None
        assert "still opening" in dialog.status.text()
        assert dialog.scope_buttons["all"].isEnabled()
        view_scope = next(scope for scope in dialog._scopes if scope.key == "view")
        assert {row["root"] for row in view_scope.records} == {str(available_root)}
        assert dialog._scope_error is not None

        registry = PdfRegistry(pending_root)
        registry.catalog.discover_root(pending_root)
        window._register_root(
            pending_root, _initialized_registry=registry, _startup_entry=entry
        )
        extra_root = tmp_path / "checked-after-open"
        extra_root.mkdir()
        (extra_root / "extra.pdf").write_bytes(b"fictional extra PDF")
        extra_registry = PdfRegistry(extra_root)
        extra_registry.catalog.discover_root(extra_root)
        window._register_root(extra_root, _initialized_registry=extra_registry)
        window.combined.selected.add(str(extra_root.resolve()))
        dialog.scope_buttons["all"].click()
        seen.update(
            all_count=all_scope.count,
            all_roots={row["root"] for row in all_scope.records},
            error=dialog._scope_error,
        )
        return 0

    monkeypatch.setattr(ExportPapersDialog, "exec", inspect)
    window.export_all_action.trigger()

    assert seen["all_count"] == 3
    assert seen["all_roots"] == {str(available_root), pending_key}
    assert seen["error"] is None


def test_copy_bibtex_waits_out_a_short_write_lock(monkeypatch, qtbot, tmp_path):
    window, _root = _window(monkeypatch, qtbot, tmp_path)
    saved = []

    def fetch_while_locked(service, _ids):
        # A worker holds a write lock for a moment while ADS BibTeX is saved.
        writer = sqlite3.connect(
            service.catalog.path, timeout=0, check_same_thread=False
        )
        writer.execute("BEGIN IMMEDIATE")
        release = threading.Timer(0.3, lambda: (writer.rollback(), writer.close()))
        release.start()
        try:
            service.catalog.store_ads_bibtex({"2000Fict....1A": (None, "fictional")})
        finally:
            release.join()
        saved.append(True)
        return None

    monkeypatch.setattr(
        "adsfolio.gui.main_window.fetch_ads_bibtex_now", fetch_while_locked
    )
    monkeypatch.setattr(
        "adsfolio.gui.main_window.export_bibtex_asking_keys",
        lambda _parent, _service, _ids: "@misc{fictional}",
    )

    assert window._records_bibtex([{"document_id": 1}]) == "@misc{fictional}"
    assert saved == [True]
