"""Refresh connection safety under real SQLite locks."""

from __future__ import annotations

import os
import sqlite3
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtWidgets import QApplication

from adsfolio.config import Config
from adsfolio.gui import main_window as main_window_module
from adsfolio.gui.combined_view import CombinedRefreshRetry
from adsfolio.gui.config import GuiConfig, RootEntry
from adsfolio.gui.main_window import MainWindow
from adsfolio.pdf.registry import PdfRegistry


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _configure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, root: Path | None
) -> None:
    config = GuiConfig(
        roots=[RootEntry(str(root))] if root is not None else [],
        last_root=str(root) if root is not None else None,
    )
    monkeypatch.setattr(
        Config,
        "get_config_dir",
        classmethod(lambda cls: tmp_path / "config"),
    )
    monkeypatch.setattr(main_window_module, "load_config", lambda: deepcopy(config))
    monkeypatch.setattr(main_window_module, "save_config", lambda _config: None)
    monkeypatch.setattr(MainWindow, "_offer_ads_recheck", lambda _self: None)


def _seed_catalog(root: Path) -> Path:
    root.mkdir()
    pdf = root / "paper.pdf"
    pdf.write_bytes(b"fictional test PDF")
    registry = PdfRegistry(root)
    registry.upsert_file(
        root=root,
        relative_path="paper.pdf",
        current_path="paper.pdf",
        sha256="fictional-sha256",
        size_bytes=pdf.stat().st_size,
        mtime_ns=pdf.stat().st_mtime_ns,
        status="unresolved",
        metadata={"title": ["Fictional test paper"]},
    )
    database = registry.catalog.path
    registry.close()
    return database


def _open_window(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    qtbot: Any,
    tmp_path: Path,
    root: Path,
) -> MainWindow:
    _configure(monkeypatch, tmp_path, root)
    window = MainWindow()
    qtbot.addWidget(window)
    qtbot.waitUntil(
        lambda: not window._startup_roots_pending and window._startup_thread is None,
        timeout=10000,
    )
    assert window.current_root == root.resolve()
    return window


def test_refresh_keeps_current_registry_when_exclusive_lock_blocks_open(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    qtbot: Any,
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    _configure(monkeypatch, tmp_path, None)
    database = _seed_catalog(root)
    connection = sqlite3.connect(database)
    assert connection.execute("PRAGMA journal_mode = DELETE").fetchone()[0] == "delete"
    connection.close()

    window = MainWindow()
    qtbot.addWidget(window)
    registry = PdfRegistry(root, initialized=True, catalog_path=database)
    window._register_root(root, _initialized_registry=registry)
    window._select_root(root)
    assert window.current_root == root.resolve()
    old_registry = window.registries[str(root.resolve())]
    assert old_registry.path == database
    assert window.model.rowCount() == 1
    view_index = window._view_table_index(window.model.index(0, 0))
    assert view_index.isValid()
    window.table.selectionModel().select(
        view_index,
        QItemSelectionModel.Select | QItemSelectionModel.Rows,
    )
    selected_before = window.table.selectionModel().selectedRows()
    assert selected_before

    writer = sqlite3.connect(database, timeout=0)
    writer.execute("BEGIN EXCLUSIVE")
    try:
        probe = sqlite3.connect(database, timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                probe.execute("SELECT id FROM library_roots").fetchall()
        finally:
            probe.close()
        assert window.refresh(root) is False
        assert window.registries[str(root.resolve())] is old_registry
        assert window.model.rowCount() == 1
        assert window.table.selectionModel().selectedRows() == selected_before
        assert "Select Refresh to try again" in window.status.currentMessage()
        assert any(
            notice.key == f"refresh:{root.resolve()}"
            for notice in window.notice_bar.queued_notices()
        )
        window._pending_watch_refresh_roots.add(root.resolve())
        window._watch_refresh_selection_id = int(
            window.model.record_at(0)["location_id"]
        )
        window._run_watch_refresh()
        assert window.registries[str(root.resolve())] is old_registry
    finally:
        writer.rollback()
        writer.close()

    assert old_registry.count_files() == 1
    assert window.registries[str(root.resolve())] is old_registry
    window.notice_bar.action_button.click()
    replacement = window.registries[str(root.resolve())]
    assert replacement is not old_registry
    assert replacement.count_files() == 1
    assert (
        replacement.catalog._connection.execute("PRAGMA busy_timeout").fetchone()[0]
        == 5000
    )
    assert not any(
        notice.key == f"refresh:{root.resolve()}"
        for notice in window.notice_bar.queued_notices()
    )
    window.close()


def test_refresh_replaces_registry_under_wal_writer_lock(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    qtbot: Any,
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    _configure(monkeypatch, tmp_path, root)
    database = _seed_catalog(root)
    window = _open_window(application, monkeypatch, qtbot, tmp_path, root)
    key = str(root.resolve())
    old_registry = window.registries[key]
    assert old_registry.path == database
    writer = sqlite3.connect(database, timeout=0)
    writer.execute("BEGIN IMMEDIATE")
    try:
        window.refresh(root)
        replacement = window.registries[key]
        assert replacement is not old_registry
        assert (
            replacement.catalog._connection.execute("PRAGMA busy_timeout").fetchone()[0]
            == 5000
        )
        assert replacement.count_files() == 1
        assert window.model.rowCount() == 1
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            old_registry.count_files()
    finally:
        writer.rollback()
        writer.close()
        window.close()


def test_refresh_keeps_live_model_when_candidate_snapshot_hits_real_lock(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    qtbot: Any,
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    _configure(monkeypatch, tmp_path, None)
    database = _seed_catalog(root)
    connection = sqlite3.connect(database)
    assert connection.execute("PRAGMA journal_mode = DELETE").fetchone()[0] == "delete"
    connection.close()

    window = MainWindow()
    qtbot.addWidget(window)
    old_registry = PdfRegistry(root, initialized=True, catalog_path=database)
    window._register_root(root, _initialized_registry=old_registry)
    window._select_root(root)
    assert window.model.rowCount() == 1
    window.table.selectionModel().select(
        window.model.index(0, 0),
        QItemSelectionModel.Select | QItemSelectionModel.Rows,
    )
    selected_before = window.table.selectionModel().selectedRows()
    assert selected_before

    writer = sqlite3.connect(database, timeout=0)
    original = PdfRegistry.open_page_snapshot
    lock_started = False

    def lock_before_snapshot(self: PdfRegistry, **kwargs: Any) -> Any:
        nonlocal lock_started
        if not lock_started:
            writer.execute("BEGIN EXCLUSIVE")
            lock_started = True
            probe = sqlite3.connect(database, timeout=0)
            try:
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    probe.execute("SELECT id FROM library_roots").fetchall()
            finally:
                probe.close()
        return original(self, **kwargs)

    monkeypatch.setattr(PdfRegistry, "open_page_snapshot", lock_before_snapshot)
    try:
        assert window.refresh(root) is False
        assert lock_started
        assert window.registries[str(root.resolve())] is old_registry
        assert window.model.rowCount() == 1
        assert window.table.selectionModel().selectedRows() == selected_before
        assert any(
            notice.key == f"refresh:{root.resolve()}"
            for notice in window.notice_bar.queued_notices()
        )
    finally:
        writer.rollback()
        writer.close()

    window.notice_bar.action_button.click()
    replacement = window.registries[str(root.resolve())]
    assert replacement is not old_registry
    assert window.model.rowCount() == 1
    assert (
        replacement.catalog._connection.execute("PRAGMA busy_timeout").fetchone()[0]
        == 5000
    )
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        old_registry.count_files()
    window.close()


def test_refresh_keeps_combined_selection_when_candidate_query_hits_real_lock(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    qtbot: Any,
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    _configure(monkeypatch, tmp_path, None)
    database = _seed_catalog(root)
    connection = sqlite3.connect(database)
    assert connection.execute("PRAGMA journal_mode = DELETE").fetchone()[0] == "delete"
    connection.close()

    window = MainWindow()
    qtbot.addWidget(window)
    old_registry = PdfRegistry(root, initialized=True, catalog_path=database)
    window._register_root(root, _initialized_registry=old_registry)
    window._select_root(root)
    window.combined.set_enabled(True)
    assert str(root.resolve()) in window.combined.selected
    view_index = window._view_table_index(window.model.index(0, 0))
    assert view_index.isValid()
    window.table.selectionModel().select(
        view_index,
        QItemSelectionModel.Select | QItemSelectionModel.Rows,
    )
    selected_before = tuple(
        (record.get("root"), record.get("location_id"))
        for record in window._selected_record_refs()
    )
    assert selected_before

    writer = sqlite3.connect(database, timeout=0)
    original = PdfRegistry.query_records
    lock_started = False

    def lock_before_query(self: PdfRegistry, **kwargs: Any) -> Any:
        nonlocal lock_started
        if not lock_started:
            writer.execute("BEGIN EXCLUSIVE")
            lock_started = True
            probe = sqlite3.connect(database, timeout=0)
            try:
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    probe.execute("SELECT id FROM library_roots").fetchall()
            finally:
                probe.close()
        return original(self, **kwargs)

    monkeypatch.setattr(PdfRegistry, "query_records", lock_before_query)
    try:
        assert window.refresh(root) is False
        assert lock_started
        assert window.registries[str(root.resolve())] is old_registry
        assert window.model.rowCount() == 1
        assert (
            tuple(
                (record.get("root"), record.get("location_id"))
                for record in window._selected_record_refs()
            )
            == selected_before
        )
    finally:
        writer.rollback()
        writer.close()

    window.notice_bar.action_button.click()
    replacement = window.registries[str(root.resolve())]
    assert replacement is not old_registry
    assert (
        tuple(
            (record.get("root"), record.get("location_id"))
            for record in window._selected_record_refs()
        )
        == selected_before
    )
    assert (
        replacement.catalog._connection.execute("PRAGMA busy_timeout").fetchone()[0]
        == 5000
    )
    window.close()


def test_combined_toolbar_refresh_is_nonblocking_and_preserves_current_pdf(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    qtbot: Any,
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    sibling_root = tmp_path / "sibling"
    _configure(monkeypatch, tmp_path, None)
    database = _seed_catalog(root)
    sibling_database = _seed_catalog(sibling_root)
    second_pdf = root / "second.pdf"
    second_pdf.write_bytes(b"second fictional test PDF")
    seed = PdfRegistry(root, initialized=True, catalog_path=database)
    seed.upsert_file(
        root=root,
        relative_path=second_pdf.name,
        current_path=second_pdf.name,
        sha256="second-fictional-sha256",
        size_bytes=second_pdf.stat().st_size,
        mtime_ns=second_pdf.stat().st_mtime_ns,
        status="unresolved",
        metadata={"title": ["Second fictional test paper"]},
    )
    seed.close()
    connection = sqlite3.connect(database)
    assert connection.execute("PRAGMA journal_mode = DELETE").fetchone()[0] == "delete"
    connection.close()

    window = MainWindow()
    qtbot.addWidget(window)
    sibling_registry = PdfRegistry(
        sibling_root, initialized=True, catalog_path=sibling_database
    )
    sibling_registry.catalog.set_busy_timeout(1700)
    window._register_root(sibling_root, _initialized_registry=sibling_registry)
    live_registry = PdfRegistry(root, initialized=True, catalog_path=database)
    live_registry.catalog.set_busy_timeout(5000)
    window._register_root(root, _initialized_registry=live_registry)
    window._select_root(root)
    window.combined.set_enabled(True)
    assert window.model.rowCount() == 3
    assert window.apply_sort("title", True)

    target_rows = [
        row
        for row in range(window.model.rowCount())
        if window.model.record_at(row)["root"] == str(root.resolve())
    ]
    assert len(target_rows) == 2
    first = window._view_table_index(window.model.index(target_rows[0], 0))
    second = window._view_table_index(window.model.index(target_rows[1], 0))
    assert first.isValid() and second.isValid()
    window.table.selectionModel().select(
        first, QItemSelectionModel.Select | QItemSelectionModel.Rows
    )
    window.table.selectionModel().setCurrentIndex(second, QItemSelectionModel.NoUpdate)
    selected_before = tuple(
        (str(record["root"]), int(record["location_id"]))
        for record in window._selected_record_refs()
    )
    current_record = window._selected_record()
    assert current_record is not None
    current_before = (str(current_record["root"]), int(current_record["location_id"]))
    assert current_before not in selected_before
    query_before = window.model.query
    checked_before = tuple(sorted(window.combined.selected))

    writer = sqlite3.connect(database, timeout=0)
    writer.execute("BEGIN EXCLUSIVE")
    try:
        probe = sqlite3.connect(database, timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                probe.execute("SELECT id FROM library_roots").fetchall()
        finally:
            probe.close()

        started = time.perf_counter()
        window.refresh_action.trigger()
        elapsed = time.perf_counter() - started

        assert elapsed < 1.0
        assert window.registries[str(root.resolve())] is live_registry
        assert window.registries[str(sibling_root.resolve())] is sibling_registry
        assert window.model.rowCount() == 3
        assert window.model.query == query_before
        assert tuple(sorted(window.combined.selected)) == checked_before
        assert (
            tuple(
                (str(record["root"]), int(record["location_id"]))
                for record in window._selected_record_refs()
            )
            == selected_before
        )
        current_record = window._selected_record()
        assert current_record is not None
        assert (str(current_record["root"]), int(current_record["location_id"])) == (
            current_before
        )
        retry_notice = next(
            notice
            for notice in window.notice_bar.queued_notices()
            if notice.key == "combined-refresh"
        )
        assert isinstance(retry_notice.payload, CombinedRefreshRetry)
        assert retry_notice.payload.roots == checked_before
    finally:
        writer.rollback()
        writer.close()

    request = retry_notice.payload
    assert isinstance(request, CombinedRefreshRetry)
    window.combined.selected.clear()
    window._handle_notice_action(request)
    assert window.registries[str(root.resolve())] is live_registry
    assert window.registries[str(sibling_root.resolve())] is sibling_registry
    assert window.model.rowCount() == 3
    window.combined.selected.update(checked_before)
    window.notice_bar.action_button.click()

    assert window.registries[str(root.resolve())] is live_registry
    assert window.registries[str(sibling_root.resolve())] is sibling_registry
    assert window.model.rowCount() == 3
    assert window.model.query == query_before
    assert tuple(sorted(window.combined.selected)) == checked_before
    assert (
        tuple(
            (str(record["root"]), int(record["location_id"]))
            for record in window._selected_record_refs()
        )
        == selected_before
    )
    current_record = window._selected_record()
    assert current_record is not None
    assert (str(current_record["root"]), int(current_record["location_id"])) == (
        current_before
    )
    assert (
        live_registry.catalog._connection.execute("PRAGMA busy_timeout").fetchone()[0]
        == 5000
    )
    assert (
        sibling_registry.catalog._connection.execute("PRAGMA busy_timeout").fetchone()[
            0
        ]
        == 1700
    )
    assert not any(
        notice.key == "combined-refresh"
        for notice in window.notice_bar.queued_notices()
    )
    window.close()


def test_combined_scope_change_lock_clears_rows_from_unchecked_root(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    qtbot: Any,
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    sibling_root = tmp_path / "sibling"
    _configure(monkeypatch, tmp_path, None)
    database = _seed_catalog(root)
    sibling_database = _seed_catalog(sibling_root)
    connection = sqlite3.connect(database)
    assert connection.execute("PRAGMA journal_mode = DELETE").fetchone()[0] == "delete"
    connection.close()

    window = MainWindow()
    qtbot.addWidget(window)
    sibling_registry = PdfRegistry(
        sibling_root, initialized=True, catalog_path=sibling_database
    )
    window._register_root(sibling_root, _initialized_registry=sibling_registry)
    live_registry = PdfRegistry(root, initialized=True, catalog_path=database)
    window._register_root(root, _initialized_registry=live_registry)
    window._select_root(root)
    window.combined.set_enabled(True)
    assert window.model.rowCount() == 2
    assert str(root.resolve()) in window.combined.selected
    assert str(sibling_root.resolve()) in window.combined.selected

    writer = sqlite3.connect(database, timeout=0)
    writer.execute("BEGIN EXCLUSIVE")
    try:
        sibling_item = window._root_item(str(sibling_root.resolve()))
        assert sibling_item is not None
        sibling_item.setCheckState(0, Qt.Unchecked)

        assert tuple(window.combined.selected) == (str(root.resolve()),)
        assert window.model.rowCount() == 0
        assert window._selected_record_refs() == ()
        assert window._selected_record() is None
        retry_notice = next(
            notice
            for notice in window.notice_bar.queued_notices()
            if notice.key == "combined-refresh"
        )
        assert "checked view is unavailable" in retry_notice.message.lower()
        assert ".." not in window.status.currentMessage()
    finally:
        writer.rollback()
        writer.close()

    window.refresh_action.trigger()
    assert window.model.rowCount() == 1
    assert tuple(window.combined.selected) == (str(root.resolve()),)
    assert window.registries[str(root.resolve())] is live_registry
    assert window.registries[str(sibling_root.resolve())] is sibling_registry
    window.close()


def test_combined_refresh_keeps_registered_view_with_pending_saved_root(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    qtbot: Any,
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    pending_root = tmp_path / "pending-library"
    _configure(monkeypatch, tmp_path, None)
    database = _seed_catalog(root)
    connection = sqlite3.connect(database)
    assert connection.execute("PRAGMA journal_mode = DELETE").fetchone()[0] == "delete"
    connection.close()

    window = MainWindow()
    qtbot.addWidget(window)
    live_registry = PdfRegistry(root, initialized=True, catalog_path=database)
    live_registry.catalog.set_busy_timeout(5000)
    window._register_root(root, _initialized_registry=live_registry)
    window._select_root(root)
    window.combined.set_enabled(True)
    window.combined.selected.add(str(pending_root.resolve()))
    checked_scope = tuple(sorted(window.combined.selected))
    pending_entry = RootEntry(str(pending_root))
    window._unrestored_roots.append(pending_entry)
    assert window.model.rowCount() == 1
    original_index = window._view_table_index(window.model.index(0, 0))
    selection_model = window.table.selectionModel()
    selection_model.select(
        original_index,
        QItemSelectionModel.Select | QItemSelectionModel.Rows,
    )
    selection_model.setCurrentIndex(original_index, QItemSelectionModel.NoUpdate)

    writer = sqlite3.connect(database, timeout=0)
    writer.execute("BEGIN EXCLUSIVE")
    try:
        window.refresh_action.trigger()
        assert window.registries[str(root.resolve())] is live_registry
        assert window.model.rowCount() == 1
        assert len(window._selected_record_refs()) == 1
        assert window._selected_record() is not None
        notice = next(
            notice
            for notice in window.notice_bar.queued_notices()
            if notice.key == "combined-refresh"
        )
        assert isinstance(notice.payload, CombinedRefreshRetry)
        assert notice.payload.roots == tuple(sorted(window.combined.selected))
        assert "current view stays open" in notice.message.lower()
    finally:
        writer.rollback()
        writer.close()

    window.notice_bar.action_button.click()

    assert window.model.rowCount() == 1
    assert len(window._selected_record_refs()) == 1
    assert window._selected_record() is not None
    assert window._selected_record()["current_path"] == "paper.pdf"
    assert tuple(sorted(window.combined.selected)) == checked_scope
    assert str(pending_root.resolve()) not in window.registries
    assert pending_entry in window._unrestored_roots
    assert all(
        notice.key != "combined-refresh"
        for notice in window.notice_bar.queued_notices()
    )
    window.close()


def test_failed_job_refresh_skips_selection_followups(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    qtbot: Any,
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    _configure(monkeypatch, tmp_path, root)
    database = _seed_catalog(root)
    window = _open_window(application, monkeypatch, qtbot, tmp_path, root)
    restored: list[object] = []
    selected: list[int] = []
    monkeypatch.setattr(window, "refresh", lambda _root=None: False)
    monkeypatch.setattr(
        window, "_restore_library_selection", lambda context: restored.append(context)
    )
    monkeypatch.setattr(window, "_select_file_id", selected.append)
    window._guided_identify_context = SimpleNamespace(root=root.resolve())
    window._refresh_selection_id = 17

    window._job_finished(0)

    assert restored == []
    assert selected == []
    assert window._refresh_selection_id is None
    assert window.registries[str(root.resolve())].path == database
    window.close()
