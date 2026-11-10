"""Recover library access after a temporary catalog lock."""

from __future__ import annotations

import sqlite3
from copy import deepcopy

import pytest
from PySide6.QtGui import QKeySequence

from adsfolio.config import Config
from adsfolio.gui.config import GuiConfig, RootEntry
from adsfolio.gui.main_window import MainWindow
from adsfolio.gui.startup_discovery import StartupDiscoveryThread


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr("adsfolio.gui.main_window.load_config", lambda: GuiConfig())
    monkeypatch.setattr("adsfolio.gui.main_window.save_config", lambda config: None)
    widget = MainWindow()
    qtbot.addWidget(widget)
    widget.show()
    return widget


def test_notifications_remain_accessible_when_find_toolbar_is_hidden(window):
    assert window.notifications_action.text() == "Show Notifications"
    assert window.notifications_action.shortcut() == QKeySequence("Ctrl+Shift+N")

    window.search_toolbar.hide()
    window.notifications_action.trigger()

    assert window.notification_panel.isVisible()


def test_notifications_use_status_bar_when_native_menu_bar_is_hidden(window):
    window.search_toolbar.hide()
    window.menuBar().hide()

    window._toggle_notifications()

    assert window.notification_panel.isVisible()


def test_retry_restores_root_and_preserves_its_saved_options(
    window, qtbot, monkeypatch, tmp_path
):
    root = tmp_path / "saved-library"
    root.mkdir()
    entry = RootEntry(
        str(root),
        label="My library",
        recursive=True,
        watch_on_start=True,
        auto_process_new_pdfs=True,
    )
    watch_calls = []
    monkeypatch.setattr(window, "_start_root_watch", watch_calls.append)
    saves = []
    monkeypatch.setattr(
        "adsfolio.gui.main_window.save_config",
        lambda config: saves.append(deepcopy(config)),
    )
    window._unrestored_roots.append(entry)
    window._show_root_retry(entry, sqlite3.OperationalError("database is locked"))
    window._retry_root_restore(entry)
    qtbot.waitUntil(
        lambda: (
            str(root.resolve()) in window.registries
            and window._startup_thread is None
            and not window._startup_roots_pending
        ),
        timeout=10000,
    )

    assert str(root.resolve()) in window.registries
    restored = window.root_entries[str(root.resolve())]
    assert restored.label == "My library"
    assert restored.recursive
    assert restored.watch_on_start
    assert restored.auto_process_new_pdfs
    assert entry not in window._unrestored_roots
    assert watch_calls == [root]
    assert saves and saves[-1].roots[0].watch_on_start
    assert saves[-1].roots[0].auto_process_new_pdfs
    assert not any(
        notice.payload is entry for notice in window.notice_bar.queued_notices()
    )


def test_startup_worker_failure_retries_asynchronously(
    window, qtbot, monkeypatch, tmp_path
):
    root = tmp_path / "startup-library"
    root.mkdir()
    entry = RootEntry(str(root), label="Startup library", recursive=True)
    original_run = StartupDiscoveryThread.run
    calls = 0

    def fail_once(thread):
        nonlocal calls
        calls += 1
        if calls == 1:
            thread.failed.emit("database is locked")
            return
        original_run(thread)

    monkeypatch.setattr(StartupDiscoveryThread, "run", fail_once)
    monkeypatch.setattr(
        "adsfolio.gui.main_window.load_config",
        lambda: GuiConfig(roots=[entry]),
    )
    startup = MainWindow()
    qtbot.addWidget(startup)
    qtbot.waitUntil(
        lambda: (
            startup._startup_finalization_done
            and startup._startup_thread is None
            and any(
                notice.payload is entry
                for notice in startup.notice_bar.queued_notices()
            )
        )
    )
    notice = next(
        notice
        for notice in startup.notice_bar.queued_notices()
        if notice.payload is entry
    )
    assert notice.action_label == "Retry"
    startup._handle_notice_action(notice.payload)
    qtbot.waitUntil(
        lambda: (
            str(root.resolve()) in startup.registries
            and startup._startup_thread is None
            and not startup._startup_roots_pending
        ),
        timeout=10000,
    )

    assert str(root.resolve()) in startup.registries
    assert startup.root_entries[str(root.resolve())].recursive
    assert entry not in startup._unrestored_roots
    # A retry after startup also restores the normal lock wait for later writes.
    qtbot.waitUntil(
        lambda: (
            startup.registries[str(root.resolve())]
            .catalog._connection.execute("PRAGMA busy_timeout")
            .fetchone()[0]
            == 5000
        )
    )


def test_busy_detection_rejects_unrelated_locked_word_and_accepts_extended_code(
    window,
):
    unrelated = sqlite3.OperationalError("no such table: locked")
    assert not window._is_database_busy(unrelated)

    locked = sqlite3.OperationalError("operation failed")
    locked.sqlite_errorname = "SQLITE_BUSY_SNAPSHOT"
    assert window._is_database_busy(locked)
    assert window._is_database_busy(sqlite3.OperationalError("database is locked"))
