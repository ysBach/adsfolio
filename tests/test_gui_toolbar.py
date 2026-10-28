"""Compact icon-only toolbar, Identify badge, and action-icon palette tracking."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QKeySequence, QPainter, QPalette
from PySide6.QtWidgets import QToolButton, QWidget

from adsfolio.config import Config
from adsfolio.gui.config import GuiConfig
from adsfolio.gui.icons import ActionIconBinder, BadgeToolButton
from adsfolio.gui.library import LibraryView
from adsfolio.gui.main_window import MainWindow
from adsfolio.pdf.registry import PdfRegistry


def _icon_image(icon: QIcon, mode: QIcon.Mode = QIcon.Normal) -> QImage:
    image = QImage(48, 48, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    icon.paint(painter, QRect(0, 0, 48, 48), Qt.AlignCenter, mode)
    painter.end()
    return image


def _has_color(image: QImage, color: str) -> bool:
    return any(
        image.pixelColor(x, y) == QColor(color)
        for x in range(image.width())
        for y in range(image.height())
    )


def _add_file(registry: PdfRegistry, root: Path, name: str, status: str) -> None:
    path = root / name
    path.write_bytes(b"%PDF")
    registry.upsert_file(
        root=root,
        relative_path=name,
        current_path=name,
        sha256=name,
        size_bytes=path.stat().st_size,
        mtime_ns=path.stat().st_mtime_ns,
        status=status,
        metadata={"bibcode": name} if status == "resolved" else {},
    )


@pytest.fixture
def window(qtbot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> MainWindow:
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr("adsfolio.gui.main_window.load_config", lambda: GuiConfig())
    monkeypatch.setattr("adsfolio.gui.main_window.save_config", lambda config: None)
    widget = MainWindow()
    qtbot.addWidget(widget)
    widget.show()
    return widget


def _open_library(window: MainWindow, tmp_path: Path, statuses: list[str]) -> Path:
    root = tmp_path / "library"
    root.mkdir()
    registry = PdfRegistry(root)
    for index, status in enumerate(statuses):
        _add_file(registry, root, f"{index:02d}.pdf", status)
    registry.close()
    window._register_root(root, "", False)
    window._select_root(root)
    return root


def _toolbar_button(window: MainWindow, action) -> QToolButton:
    button = window.search_toolbar.widgetForAction(action)
    assert isinstance(button, QToolButton)
    return button


def test_toolbar_is_icon_only_with_labels_shortcuts_and_scoped_tooltips(
    window: MainWindow,
) -> None:
    refresh = _toolbar_button(window, window.refresh_action)
    rename = _toolbar_button(window, window.rename_action)
    for button in (refresh, rename, window.identify_button, window.ads_refresh_button):
        assert button.toolButtonStyle() == Qt.ToolButtonIconOnly
        assert not button.icon().isNull()
    assert window.identify_button.defaultAction() is window.identify_action
    # Menu labels, shortcuts, and wiring stay on the actions.
    assert window.refresh_action.text() == "Refresh"
    assert window.identify_action.text() == "Identify…"
    assert window.rename_action.text() == "Rename…"
    assert window.identify_action.shortcut() == QKeySequence("Ctrl+Shift+I")
    assert window.rename_action.shortcut() == QKeySequence("Ctrl+Shift+R")
    assert window.refresh_action.shortcuts()[0] == QKeySequence("F5")
    assert "current library" in window.refresh_action.toolTip()
    assert "current library" in window.rename_action.toolTip()
    assert "current library" in window.identify_action.toolTip()
    assert QKeySequence("Ctrl+Shift+R").toString(QKeySequence.NativeText) in (
        window.rename_action.toolTip()
    )
    assert window.ads_refresh_button.accessibleName() == "ADS Refresh"
    assert "ADS Refresh" in window.ads_refresh_button.toolTip()
    labels = [action.text() for action in window.ads_refresh_button.menu().actions()]
    assert "Look Up Stored Bibcodes…" in labels


def test_ads_refresh_icon_differs_from_local_refresh_icon(window: MainWindow) -> None:
    local = _icon_image(window.refresh_action.icon())
    ads = _icon_image(window.ads_refresh_button.icon())
    assert local != ads


def test_identify_badge_counts_whole_library_ignoring_view_search_and_paging(
    window: MainWindow, tmp_path: Path
) -> None:
    statuses = ["unresolved"] * 4 + ["resolved"] * 3
    _open_library(window, tmp_path, statuses)
    # Only part of the library is loaded into the table.
    window.model._rows = window.model._rows[:2]
    window._update_status()
    assert window.model.rowCount() < window.model.total
    assert window.identify_button.badge_text == "4"
    tooltip = window.identify_action.toolTip()
    assert "4 catalogued PDFs need ADS lookup" in tooltip
    assert "skip PDFs that already have usable metadata" in tooltip
    assert window.identify_button.accessibleDescription() == tooltip

    window.current_view = LibraryView.RESOLVED
    window.search.setText("01")
    window._search_changed()
    assert window.model.total == 0
    assert window.identify_button.badge_text == "4"

    window.current_view = LibraryView.ARXIV_ONLY
    window.search.setText("")
    window._search_changed()
    assert window.identify_button.badge_text == "4"

    # Selection does not change the whole-library count.
    window.current_view = LibraryView.ALL
    window._search_changed()
    window.table.setCurrentIndex(window.model.index(0, 0))
    assert window.identify_button.badge_text == "4"


def test_identify_badge_is_exact_for_large_counts_and_hidden_for_zero(
    window: MainWindow, qtbot
) -> None:
    window.identify_button.set_badge(123456)
    assert window.identify_button.badge_text == "123456"
    window.identify_button.set_badge(0)
    assert window.identify_button.badge_text == ""
    window.identify_button.set_badge(None)
    assert window.identify_button.badge_text == ""


def test_identify_badge_follows_new_records(window: MainWindow, tmp_path: Path) -> None:
    root = _open_library(window, tmp_path, ["resolved"])
    assert window.identify_button.badge_text == ""
    assert "0 catalogued PDFs need ADS lookup" in window.identify_action.toolTip()
    registry = window.registries[str(root)]
    _add_file(registry, root, "new.pdf", "unresolved")
    window.refresh(root)
    assert window.identify_button.badge_text == "1"
    assert "1 catalogued PDF needs ADS lookup" in window.identify_action.toolTip()


def test_identify_badge_clears_without_a_library(
    window: MainWindow, tmp_path: Path
) -> None:
    assert window.identify_button.badge_text == ""
    _open_library(window, tmp_path, ["unresolved"])
    assert window.identify_button.badge_text == "1"
    window._remove_root()
    assert window.current_root is None
    assert window.identify_button.badge_text == ""
    assert "need ADS lookup" not in window.identify_action.toolTip()
    assert window.status.currentMessage() == "No library selected"


def test_identify_badge_does_not_keep_a_stale_count_when_the_count_is_unavailable(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _open_library(window, tmp_path, ["unresolved", "unresolved"])
    assert window.identify_button.badge_text == "2"
    registry = window.registries[str(root)]

    def locked(**filters: object) -> int:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(registry, "count_files_matching", locked)
    window._update_status()
    assert window.identify_button.badge_text == ""
    assert "unavailable" in window.identify_action.toolTip()
    assert "2 catalogued" not in window.identify_action.toolTip()


def test_badge_button_grows_from_font_metrics_only_when_needed(qtbot) -> None:
    button = BadgeToolButton()
    qtbot.addWidget(button)
    button.setText("Identify")
    button.setToolButtonStyle(Qt.ToolButtonIconOnly)
    plain = button.sizeHint()
    button.set_badge(7)
    assert button.sizeHint().width() >= plain.width()
    button.set_badge(12345678901234)
    wide = button.sizeHint()
    assert wide.width() > plain.width()
    assert wide.height() == plain.height()
    badge = button._badge_size()
    assert wide.width() >= 2 * badge.width()
    button.resize(wide)
    image = button.grab().toImage()
    assert not image.isNull()
    button.set_badge(None)
    assert button.sizeHint() == plain


def test_action_icons_follow_palette_and_disabled_state(qtbot) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    from PySide6.QtGui import QAction

    action = QAction("Identify…", host)
    binder = ActionIconBinder(host)
    binder.bind(action, "identify")
    before = _icon_image(action.icon())
    palette = host.palette()
    palette.setColor(QPalette.Active, QPalette.ButtonText, QColor("#112233"))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#808080"))
    host.setPalette(palette)
    active = _icon_image(action.icon())
    disabled = _icon_image(action.icon(), QIcon.Disabled)
    assert active != before
    assert _has_color(active, "#112233")
    assert _has_color(disabled, "#808080")
    assert not _has_color(disabled, "#112233")
    assert QSize(16, 16) == action.icon().actualSize(QSize(16, 16))
