"""Exercise sound preferences and watcher-to-window delivery."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QLabel

from adsfolio.config import Config
from adsfolio.gui.config import GuiConfig, RootEntry, load_config, save_config
from adsfolio.gui.main_window import MainWindow
from adsfolio.gui.watch import _LibraryWatchWorker
from adsfolio.gui.watch_sounds import WatchSoundChoice
from adsfolio.pdf.identify_settings import IdentifySettings
from adsfolio.pdf.watch_session import FileSnapshot, WatchSession, WatchSource


class Controller(QObject):
    started = Signal(object)
    stopped = Signal(object)
    detected = Signal(object)
    outcome = Signal(object)
    failed = Signal(object)
    paused = Signal(object)
    reconciled = Signal(object)

    def __init__(self, root: Path):
        super().__init__()
        self.root = root

    def is_watching(self, root):
        return Path(root) == self.root

    def session_id(self, root):
        return "active" if Path(root) == self.root else None

    def dismiss(self, event):
        pass

    def stop_all(self, *, wait_ms: int = 5000):
        return True


@pytest.fixture
def sound_window(qtbot, monkeypatch, tmp_path):
    config_dir = tmp_path / "config"
    monkeypatch.setattr(MainWindow, "_queue_startup_ads_rechecks", lambda self: None)
    monkeypatch.setattr(MainWindow, "_queue_added_ads_recheck", lambda self, root: None)
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: config_dir))
    root = tmp_path / "library"
    root.mkdir()
    existing = root / "existing.pdf"
    existing.write_bytes(b"original")
    save_config(
        GuiConfig(
            roots=[RootEntry(str(root))],
            watch_sound="chime",
            check_updates_automatically=False,
            citation_styles=[],
        )
    )
    controller = Controller(root)
    window = MainWindow(watch_controller=controller)
    qtbot.addWidget(window)
    yield window, controller, root, existing
    window.close()


def test_watcher_new_file_plays_selected_sound_once(sound_window, monkeypatch):
    window, controller, root, existing = sound_window
    played = []
    monkeypatch.setattr(
        window.watch_sound_player, "play", lambda sound: played.append(sound) or True
    )
    worker = _LibraryWatchWorker(root, "active")
    worker._session = WatchSession(root, "active", [FileSnapshot.from_path(existing)])
    worker._baseline_ready = True
    worker.detected.connect(controller.detected.emit)

    existing.write_bytes(b"modified existing")
    worker._pending[(existing, WatchSource.MODIFIED)] = None
    worker._drain()
    assert played == []

    added = root / "new.pdf"
    added.write_bytes(b"new")
    worker._pending[(added, WatchSource.CREATED)] = None
    worker._drain()
    assert played == ["chime"]
    assert any(
        "new.pdf" in notice.message for notice in window.notice_bar.queued_notices()
    )

    worker._pending[(added, WatchSource.MODIFIED)] = None
    worker._drain()
    assert played == ["chime"]

    window.config.watch_sound = "silent"
    another = root / "silent.pdf"
    another.write_bytes(b"another")
    worker._pending[(another, WatchSource.CREATED)] = None
    worker._drain()
    assert played == ["chime"]


def test_sound_dialog_previews_failure_and_saves_selection(sound_window, monkeypatch):
    window, _controller, _root, _existing = sound_window
    monkeypatch.setattr(window.watch_sound_player, "play", lambda sound: False)
    observed = []

    def choose_sound():
        dialog = QApplication.activeModalWidget()
        choice = dialog.findChild(WatchSoundChoice)
        choice.set_sound("ripple")
        choice.preview_button.click()
        observed.extend(label.text() for label in dialog.findChildren(QLabel))
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Save).click()

    QTimer.singleShot(0, choose_sound)
    window._choose_watch_sound()
    assert window.config.watch_sound == "ripple"
    assert load_config().watch_sound == "ripple"
    assert any("Check the audio output" in text for text in observed)


def test_failed_watcher_sound_reports_audio_problem(sound_window, monkeypatch):
    window, controller, root, _existing = sound_window
    monkeypatch.setattr(window.watch_sound_player, "play", lambda sound: False)
    worker = _LibraryWatchWorker(root, "active")
    worker._session = WatchSession(root, "active")
    worker._baseline_ready = True
    worker.detected.connect(controller.detected.emit)
    added = root / "new.pdf"
    added.write_bytes(b"new")
    worker._pending[(added, WatchSource.CREATED)] = None
    worker._drain()

    assert any(
        notice.key == "watch-sound-unavailable"
        for notice in window.notice_bar.queued_notices()
    )


def test_sound_includes_new_pdf_skipped_by_identify(sound_window, monkeypatch):
    window, controller, root, _existing = sound_window
    played = []
    dismissed = []
    monkeypatch.setattr(
        window.watch_sound_player, "play", lambda sound: played.append(sound) or True
    )
    monkeypatch.setattr(controller, "dismiss", dismissed.append)
    monkeypatch.setattr(
        window,
        "_effective_identify_settings",
        lambda root: IdentifySettings(skip_unresolved_directory=True),
    )
    directory = root / "unresolved"
    directory.mkdir()
    added = directory / "new.pdf"
    added.write_bytes(b"new")
    worker = _LibraryWatchWorker(root, "active")
    worker._session = WatchSession(root, "active")
    worker._baseline_ready = True
    worker.detected.connect(controller.detected.emit)
    worker._pending[(added, WatchSource.CREATED)] = None
    worker._drain()

    assert played == ["chime"]
    assert len(dismissed) == 1
    assert window.notice_bar.queued_notices() == ()
