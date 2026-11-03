"""Keep the library panes stationary while notices and view help change."""

import pytest
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtWidgets import QDialog
from test_gui_main_window_watch import _window_factory

from adsfolio.config import Config
from adsfolio.gui.config import GuiConfig, RootEntry
from adsfolio.gui.library import LibraryView
from adsfolio.gui.notifications import Notice, NoticeBar


def library_window(monkeypatch, qtbot, tmp_path, width):
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    root = tmp_path / "library"
    root.mkdir()
    for index in range(40):
        (root / f"paper-{index:02d}.pdf").write_bytes(
            f"Fictional paper {index}".encode()
        )
    window, _, _ = _window_factory(
        monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(root))])
    )
    window.resize(width, 700)
    window.show()
    qtbot.waitUntil(window.isVisible)
    window._select_view(LibraryView.ALL)
    return window


def pane_rectangles(window):
    return tuple(
        (
            widget.mapTo(window, QPoint()).x(),
            widget.mapTo(window, QPoint()).y(),
            widget.width(),
            widget.height(),
        )
        for widget in (window.sidebar, window.table, window.detail)
    )


@pytest.mark.parametrize("width", [820, 1200])
def test_view_help_and_long_notifications_never_move_the_panes(
    monkeypatch, qtbot, tmp_path, width
):
    window = library_window(monkeypatch, qtbot, tmp_path, width)
    qtbot.wait(1)
    baseline = pane_rectangles(window)
    for view in (LibraryView.UNRESOLVED, LibraryView.RESOLVED, LibraryView.ALL):
        window._select_view(view)
        qtbot.wait(1)
        assert pane_rectangles(window) == baseline
        assert not window.notification_panel.isVisible()

    window.table.setCurrentIndex(window.model.index(5, 0))
    scroll = window.table.verticalScrollBar()
    scroll.setValue(scroll.maximum() // 2)
    selected = window._selected_record()["location_id"]
    position = scroll.value()
    for index in range(12):
        window.notice_bar.enqueue(
            Notice(str(index), "Short warning", severity="warning")
        )
    long_message = (
        "A detailed warning with recovery instructions. " * 500
    ) + "FINAL DETAIL"
    window.notice_bar.enqueue(Notice("0", long_message, severity="error"))
    qtbot.wait(1)
    assert pane_rectangles(window) == baseline
    assert window.notifications_button.isVisible()
    assert window.notifications_button.badge_text == "12"
    assert "12 pending" in window.notifications_button.accessibleName()
    assert not window.notification_panel.isVisible()
    assert window._selected_record()["location_id"] == selected
    assert scroll.value() == position

    window.notifications_button.click()
    panel = window.notification_panel
    assert panel.isVisible()
    qtbot.waitUntil(lambda: panel.scroll_area.verticalScrollBar().maximum() > 0)
    assert window.notice_bar.message.text() == long_message
    assert window.notice_bar.message.text().endswith("FINAL DETAIL")
    assert pane_rectangles(window) == baseline
    assert window._selected_record()["location_id"] == selected
    assert scroll.value() == position

    qtbot.keyClick(panel, Qt.Key_Escape)
    assert not panel.isVisible()
    assert len(window.notice_bar.queued_keys()) == 12
    assert pane_rectangles(window) == baseline
    for key in window.notice_bar.queued_keys():
        window.notice_bar.dismiss(key)
    qtbot.wait(1)
    assert pane_rectangles(window) == baseline
    assert window.notifications_button.isVisible()
    assert window.notifications_button.badge_text == ""
    assert window._selected_record()["location_id"] == selected
    assert scroll.value() == position


def test_view_guidance_and_actions_remain_accessible_in_the_panel(
    monkeypatch, qtbot, tmp_path
):
    window = library_window(monkeypatch, qtbot, tmp_path, 1200)
    calls = []
    monkeypatch.setattr(
        window, "_guide_identification", lambda: calls.append(window.current_root)
    )
    window._select_view(LibraryView.UNRESOLVED)
    assert not window.notification_panel.isVisible()
    assert "need identification or review" in window.notifications_button.toolTip()
    window.notifications_button.click()
    assert window.view_explanation.isVisible()
    assert "need identification or review" in window.view_explanation.text()
    assert window.check_papers_button.isVisible()
    window.check_papers_button.click()
    assert calls == [window.current_root]
    window.notification_panel.hide()
    window._select_view(LibraryView.ALL)
    window.notifications_button.click()
    assert window.notification_panel.empty.isVisible()
    assert not window.lookup_guidance.isVisible()


def test_reading_position_stays_stable_until_the_displayed_message_changes(
    monkeypatch, qtbot, tmp_path
):
    window = library_window(monkeypatch, qtbot, tmp_path, 1200)
    text = "Detailed notification. " * 1000
    window.notice_bar.enqueue(Notice("first", text))
    window.notifications_button.click()
    scroll = window.notification_panel.scroll_area.verticalScrollBar()
    qtbot.waitUntil(lambda: scroll.maximum() > 0)
    scroll.setValue(scroll.maximum() // 2)
    position = scroll.value()
    window.notice_bar.enqueue(Notice("second", text + "Second message."))
    qtbot.wait(1)
    assert scroll.value() == position
    window.notice_bar.dismiss("first")
    qtbot.wait(1)
    assert scroll.value() == 0


def test_notification_panel_stays_open_after_an_action_and_outside_click(
    monkeypatch, qtbot, tmp_path
):
    window = library_window(monkeypatch, qtbot, tmp_path, 1200)
    bar = window.notice_bar
    notice = Notice(
        "candidate",
        "Review this candidate",
        reveal_path=tmp_path / "paper.pdf",
        candidate_bibcode="2026ApJ..1009L..41B",
        candidate_sha256="a" * 64,
        candidate_fingerprint="b" * 64,
        candidate_confirmable=True,
    )
    bar.enqueue(notice)
    window.notifications_button.click()
    panel = window.notification_panel
    bar.open_requested.disconnect()

    def open_review(_path):
        dialog = QDialog(window)
        QTimer.singleShot(0, dialog.accept)
        dialog.exec()

    bar.open_requested.connect(open_review)
    qtbot.mouseClick(bar.open_button, Qt.LeftButton)
    assert panel.isVisible()
    qtbot.mouseClick(window.table.viewport(), Qt.LeftButton)
    assert panel.isVisible()
    confirmations = []
    bar.use_id_requested.connect(confirmations.append)
    qtbot.mouseClick(bar.use_id_button, Qt.LeftButton)
    assert confirmations == [notice]
    assert panel.isVisible()
    qtbot.keyClick(panel, Qt.Key_Escape)
    assert not panel.isVisible()


def test_last_notification_action_is_accessible_by_scrolling(
    monkeypatch, qtbot, tmp_path
):
    window = library_window(monkeypatch, qtbot, tmp_path, 1200)
    baseline = pane_rectangles(window)
    for index in range(25):
        window.notice_bar.enqueue(
            Notice(str(index), f"Item {index}", reveal_path=tmp_path / f"{index}.pdf")
        )
    window.notifications_button.click()
    panel = window.notification_panel
    row = next(
        bar
        for bar in window.notice_bar.findChildren(NoticeBar)
        if bar.message.text() == "Item 24"
    )
    qtbot.waitUntil(lambda: panel.scroll_area.verticalScrollBar().maximum() > 0)
    panel.scroll_area.ensureWidgetVisible(row.open_button)
    opened = []
    window.notice_bar.open_requested.disconnect()
    window.notice_bar.open_requested.connect(opened.append)
    qtbot.mouseClick(row.open_button, Qt.LeftButton)
    assert opened == [tmp_path / "24.pdf"]
    assert panel.isVisible()
    assert pane_rectangles(window) == baseline
