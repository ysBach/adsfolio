"""Closing the window stops page loads that would read closed catalogs."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from adsfolio.config import Config
from adsfolio.gui.config import GuiConfig, RootEntry, save_config
from adsfolio.gui.main_window import MainWindow
from adsfolio.gui.models import PAGE_SIZE


def test_close_right_after_a_sort_reads_no_closed_catalog(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    monkeypatch.setattr(MainWindow, "_queue_startup_ads_rechecks", lambda self: None)
    monkeypatch.setattr(MainWindow, "_offer_ads_recheck", lambda self: None)
    root = tmp_path / "library"
    root.mkdir()
    for number in range(PAGE_SIZE * 3):
        (root / f"paper{number:05d}.pdf").write_bytes(b"fictional %d" % number)
    save_config(
        GuiConfig(
            roots=[RootEntry(str(root))],
            last_root=str(root),
            check_updates_automatically=False,
            show_folders=False,
        )
    )
    window = MainWindow()
    qtbot.addWidget(window)
    qtbot.waitUntil(lambda: not window._startup_roots_pending)
    window.resize(1200, 3000)
    window.show()
    qtbot.waitExposed(window)
    # A title sort keeps every row in memory, so the view can load pages.
    assert window.apply_sort("title", False)
    assert window.model.canFetchMore()

    window.close()
    # pytest-qt fails the test if a Qt callback raises here.
    qtbot.wait(300)

    assert not window.model.canFetchMore()
