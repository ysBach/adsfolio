"""Regression tests for asynchronous saved-library restoration."""

from __future__ import annotations

import os
import sqlite3
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication

from adsfolio.config import Config
from adsfolio.gui import main_window as main_window_module
from adsfolio.gui.config import GuiConfig, RootEntry
from adsfolio.gui.library import LibraryView
from adsfolio.gui.main_window import MainWindow
from adsfolio.gui.startup_discovery import StartupDiscoveryThread
from adsfolio.pdf.catalog import CATALOG_FILENAME
from adsfolio.pdf.registry import PdfRegistry


@pytest.fixture(scope="module")
def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _configure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    config: GuiConfig,
) -> list[GuiConfig]:
    config_dir = tmp_path / "config"
    monkeypatch.setattr(
        Config,
        "get_config_dir",
        classmethod(lambda cls: config_dir),
    )
    saves: list[GuiConfig] = []
    monkeypatch.setattr(main_window_module, "load_config", lambda: deepcopy(config))
    monkeypatch.setattr(
        main_window_module,
        "save_config",
        lambda saved: saves.append(deepcopy(saved)),
    )
    monkeypatch.setattr(
        MainWindow,
        "_offer_ads_recheck",
        lambda self: None,
    )
    return saves


def _wait_for_startup(qtbot: Any, window: MainWindow) -> None:
    qtbot.waitUntil(
        lambda: not window._startup_roots_pending and window._startup_thread is None,
        timeout=10000,
    )


def test_real_writer_lock_does_not_block_window_or_event_loop(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    qtbot: Any,
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    (root / "paper.pdf").write_bytes(b"pdf")
    config = GuiConfig(
        roots=[RootEntry(str(root))],
        last_view=LibraryView.UNRESOLVED.value,
        combined_roots=[str(root)],
    )
    _configure(monkeypatch, tmp_path, config)
    registry = PdfRegistry(root)
    registry.catalog.discover_root(root)
    registry.close()
    database = tmp_path / "config" / CATALOG_FILENAME
    writer = sqlite3.connect(database, timeout=0)
    writer.execute("BEGIN IMMEDIATE")

    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    ticks: list[int] = []
    heartbeat = QTimer(window)
    heartbeat.setInterval(5)
    heartbeat.timeout.connect(lambda: ticks.append(len(ticks)))
    heartbeat.start()

    item = window._root_item(str(root))
    assert item is not None
    assert not item.flags() & Qt.ItemIsSelectable
    assert window.current_root is None
    assert window.splitter.isHidden() is False
    window.sidebar.setCurrentItem(item)
    assert window.current_root is None

    qtbot.wait(100)
    assert len(ticks) >= 5
    assert window._startup_thread is not None
    assert window._startup_thread.isRunning()
    assert str(root.resolve()) not in window.registries

    writer.rollback()
    writer.close()
    _wait_for_startup(qtbot, window)
    qtbot.waitUntil(
        lambda: (
            window.registries[str(root.resolve())]
            .catalog._connection.execute("PRAGMA busy_timeout")
            .fetchone()[0]
            == 5000
        )
    )

    assert window.current_root == root.resolve()
    assert window.current_view is LibraryView.UNRESOLVED
    assert window.model.rowCount() == 1
    assert window.config.combined_roots == [str(root)]
    heartbeat.stop()
    window.close()


def test_no_saved_last_root_selects_first_ready_root_and_saved_view(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    qtbot: Any,
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    (root / "paper.pdf").write_bytes(b"pdf")
    _configure(
        monkeypatch,
        tmp_path,
        GuiConfig(
            roots=[RootEntry(str(root))],
            last_root=None,
            last_view=LibraryView.UNRESOLVED.value,
        ),
    )

    window = MainWindow()
    qtbot.addWidget(window)
    _wait_for_startup(qtbot, window)

    assert window.current_root == root.resolve()
    assert window.current_view is LibraryView.UNRESOLVED
    assert window.current_query.view is LibraryView.UNRESOLVED
    window.close()


def test_offline_saved_root_keeps_cached_rows_and_retry_reloads(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    qtbot: Any,
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    offline = tmp_path / "offline-library"
    root.mkdir()
    (root / "paper.pdf").write_bytes(b"pdf")
    _configure(
        monkeypatch,
        tmp_path,
        GuiConfig(roots=[RootEntry(str(root))], last_root=str(root)),
    )
    registry = PdfRegistry(root)
    registry.catalog.discover_root(root)
    registry.close()
    root.rename(offline)
    window = MainWindow()
    qtbot.addWidget(window)
    _wait_for_startup(qtbot, window)
    key = str(root.resolve())
    saved_registry = window.registries[key]
    records = saved_registry.query_files(include_missing=True)
    assert len(records) == 1
    assert records[0]["missing_at"] is None
    assert window.current_root == root.resolve()
    assert any(
        notice.key == f"root-restore:{root.resolve()}"
        for notice in window.notice_bar.queued_notices()
    )

    offline.rename(root)
    window._retry_root_restore(window.root_entries[key])
    _wait_for_startup(qtbot, window)

    assert window.registries[key] is not saved_registry
    with pytest.raises(sqlite3.ProgrammingError):
        saved_registry.count_files()
    assert window.registries[key].count_files() == 1
    assert window.model.rowCount() == 1
    window.close()


def test_first_retry_restores_saved_combined_mode_and_offers_ads_once(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    qtbot: Any,
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    entry = RootEntry(str(root))
    _configure(
        monkeypatch,
        tmp_path,
        GuiConfig(
            roots=[entry],
            combined_view=True,
            combined_roots=[str(root)],
        ),
    )
    original_run = StartupDiscoveryThread.run
    attempts = 0

    def fail_once(thread: StartupDiscoveryThread) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            thread.failed.emit("database is locked")
        else:
            original_run(thread)

    monkeypatch.setattr(StartupDiscoveryThread, "run", fail_once)
    offers: list[bool] = []
    monkeypatch.setattr(
        MainWindow, "_offer_ads_recheck", lambda self: offers.append(True)
    )
    window = MainWindow()
    qtbot.addWidget(window)
    qtbot.waitUntil(
        lambda: (
            window._startup_finalization_done
            and window._startup_thread is None
            and not window.root_entries
        ),
        timeout=10000,
    )

    assert window.config.combined_view
    assert window._ads_recheck_startup_pending
    window._retry_root_restore(entry)
    qtbot.waitUntil(
        lambda: window.combined.enabled and window._startup_thread is None,
        timeout=10000,
    )
    qtbot.waitUntil(lambda: len(offers) == 1)
    qtbot.wait(30)

    assert window.config.combined_roots == [str(root)]
    assert offers == [True]
    window.close()


def test_retry_save_failure_is_visible(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    qtbot: Any,
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    entry = RootEntry(str(root), label="Saved library", recursive=True)
    _configure(monkeypatch, tmp_path, GuiConfig())
    monkeypatch.setattr(
        main_window_module,
        "save_config",
        lambda config: (_ for _ in ()).throw(OSError("read-only settings")),
    )
    window = MainWindow()
    qtbot.addWidget(window)
    window._unrestored_roots.append(entry)
    window._retry_root_restore(entry)
    qtbot.waitUntil(
        lambda: any(
            notice.key == f"root-save:{root.resolve()}"
            for notice in window.notice_bar.queued_notices()
        ),
        timeout=10000,
    )

    assert str(root.resolve()) in window.registries
    assert window.root_entries[str(root.resolve())].recursive
    assert "read-only settings" in window.notice_bar.message.text()
    window.close()


def test_retry_adds_selected_root_to_active_combined_table(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    qtbot: Any,
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "one.pdf").write_bytes(b"one")
    (second / "two.pdf").write_bytes(b"two")
    first_entry = RootEntry(str(first))
    second_entry = RootEntry(str(second))
    _configure(
        monkeypatch,
        tmp_path,
        GuiConfig(
            roots=[first_entry, second_entry],
            last_root=str(first),
            combined_view=True,
            combined_roots=[str(first), str(second)],
            show_folders=False,
        ),
    )
    original_run = StartupDiscoveryThread.run
    failed_second = False

    def fail_second_once(thread: StartupDiscoveryThread) -> None:
        nonlocal failed_second
        if Path(thread.root) == second and not failed_second:
            failed_second = True
            thread.failed.emit("database is locked")
        else:
            original_run(thread)

    monkeypatch.setattr(StartupDiscoveryThread, "run", fail_second_once)
    window = MainWindow()
    qtbot.addWidget(window)
    qtbot.waitUntil(
        lambda: (
            window._startup_finalization_done
            and window._startup_thread is None
            and not window._startup_roots_pending
        ),
        timeout=10000,
    )

    assert window.combined.enabled
    assert window.current_root == first.resolve()
    assert window.table.model().rowCount() == 1
    window._retry_root_restore(second_entry)
    qtbot.waitUntil(
        lambda: (
            str(second.resolve()) in window.registries
            and window._startup_thread is None
            and window.table.model().rowCount() == 2
        ),
        timeout=10000,
    )

    assert window.current_root == first.resolve()
    assert second.resolve().as_posix() in window.combined.selected
    window.close()
