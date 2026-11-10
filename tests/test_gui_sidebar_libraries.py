"""Library rows keep their checkboxes and show the watch state as an icon."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QProxyStyle, QStyle, QStyleOptionViewItem
from test_gui_main_window_watch import _window_factory

from adsfolio.gui.combined_view import CHECK_LOCKED_ROLE
from adsfolio.gui.config import GuiConfig, RootEntry
from adsfolio.gui.watch import WatchFailure


def _roots(tmp_path: Path) -> list[Path]:
    roots = []
    for name in ("first", "second"):
        root = tmp_path / name
        root.mkdir()
        (root / "paper.pdf").write_bytes(f"fictional {name}".encode())
        roots.append(root.resolve())
    return roots


def _item(window, root: Path):
    return next(
        window.libraries_item.child(i)
        for i in range(window.libraries_item.childCount())
        if window.libraries_item.child(i).data(0, Qt.UserRole) == str(root)
    )


def _window(monkeypatch, qtbot, tmp_path, *, combined_roots=None, watch=()):
    roots = _roots(tmp_path)
    config = GuiConfig(
        roots=[RootEntry(str(root), watch_on_start=root in watch) for root in roots],
        combined_roots=combined_roots(roots) if combined_roots else None,
    )
    window, controller, _saves = _window_factory(monkeypatch, qtbot, config)
    return window, controller, roots


def test_combine_off_keeps_greyed_checkboxes_with_the_saved_choice(
    monkeypatch, qtbot, tmp_path
):
    window, _controller, roots = _window(
        monkeypatch, qtbot, tmp_path, combined_roots=lambda roots: [str(roots[1])]
    )
    first, second = (_item(window, root) for root in roots)

    assert not window.combined.enabled
    assert first.checkState(0) == Qt.Unchecked
    assert second.checkState(0) == Qt.Checked
    for item in (first, second):
        assert not item.flags() & Qt.ItemIsUserCheckable
        assert item.flags() & Qt.ItemIsEnabled
        assert item.flags() & Qt.ItemIsSelectable
        assert item.data(0, CHECK_LOCKED_ROLE)

    window.combined.set_enabled(True)

    assert second.checkState(0) == Qt.Checked
    assert second.flags() & Qt.ItemIsUserCheckable
    assert not second.data(0, CHECK_LOCKED_ROLE)


def test_locked_checkbox_is_drawn_disabled_with_the_row_enabled(
    monkeypatch, qtbot, tmp_path
):
    window, _controller, roots = _window(monkeypatch, qtbot, tmp_path)
    sidebar = window.sidebar
    calls = []

    class Recorder(QProxyStyle):
        def drawPrimitive(self, element, option, painter, widget=None):
            if element == QStyle.PrimitiveElement.PE_IndicatorItemViewItemCheck:
                calls.append(("indicator", option.state, option.rect))
            super().drawPrimitive(element, option, painter, widget)

        def drawControl(self, element, option, painter, widget=None):
            if element == QStyle.ControlElement.CE_ItemViewItem:
                calls.append(("row", option.state, option.rect))
            super().drawControl(element, option, painter, widget)

    style = Recorder()
    sidebar.setStyle(style)

    def draw(item):
        calls.clear()
        index = sidebar.indexFromItem(item)
        option = QStyleOptionViewItem()
        option.initFrom(sidebar)
        option.rect = sidebar.visualRect(index)
        option.decorationSize = QSize(16, 16)
        option.widget = sidebar
        pixmap = QPixmap(option.rect.size() + QSize(option.rect.x(), option.rect.y()))
        painter = QPainter(pixmap)
        sidebar.itemDelegate().paint(painter, option, index)
        painter.end()
        return list(calls)

    enabled = QStyle.StateFlag.State_Enabled
    calls_locked = draw(_item(window, roots[0]))
    row = next(state for kind, state, _rect in calls_locked if kind == "row")
    indicator = [state for kind, state, _rect in calls_locked if kind == "indicator"]
    assert row & enabled
    assert indicator and not indicator[-1] & enabled

    calls_toggle = draw(window.combined.toggle_item)
    assert all(state & enabled for _kind, state, _rect in calls_toggle)


def test_watched_library_shows_an_icon_instead_of_a_text_marker(
    monkeypatch, qtbot, tmp_path
):
    roots = _roots(tmp_path)
    window, _controller, _saves = _window_factory(
        monkeypatch,
        qtbot,
        GuiConfig(roots=[RootEntry(str(roots[0]), watch_on_start=True)]),
    )
    window._start_configured_watchers()
    item = _item(window, roots[0])

    assert "●" not in item.text(0)
    assert item.text(0) == "first (1)"
    assert not item.icon(0).isNull()
    assert "Watching now: Yes" in item.toolTip(0)


def test_blocked_watch_start_shows_a_notice_and_the_reason(
    monkeypatch, qtbot, tmp_path
):
    window, controller, roots = _window(monkeypatch, qtbot, tmp_path)
    window.job = object()
    window._job_description = "local refresh of first"
    try:
        window._set_root_watching(roots[0], True)
    finally:
        window.job = None

    assert not controller.is_watching(roots[0])
    assert f"watch-start:{roots[0]}" in window.notice_bar.queued_keys()
    tooltip = _item(window, roots[0]).toolTip(0)
    assert "Watching now: No" in tooltip
    assert "Last watch problem: Wait for the local refresh of first" in tooltip

    window._set_root_watching(roots[0], True)

    assert controller.is_watching(roots[0])
    assert "Last watch problem" not in _item(window, roots[0]).toolTip(0)


def test_library_watcher_failure_is_shown_in_the_tooltip(monkeypatch, qtbot, tmp_path):
    window, controller, roots = _window(monkeypatch, qtbot, tmp_path)

    controller.failed.emit(
        WatchFailure(
            root=roots[1],
            session_id="old",
            message="Watch root does not exist",
            event=None,
        )
    )

    tooltip = _item(window, roots[1]).toolTip(0)
    assert "Last watch problem: Watch root does not exist" in tooltip
    assert "Last watch problem" not in _item(window, roots[0]).toolTip(0)


def test_watch_icon_and_tooltip_updates_do_not_reload_the_combined_table(
    monkeypatch, qtbot, tmp_path
):
    window, controller, roots = _window(monkeypatch, qtbot, tmp_path)
    window.combined.set_enabled(True)
    resets = []
    window.model.modelReset.connect(lambda: resets.append(1))

    controller.start_root(roots[0], recursive=False)
    window._sync_watch_actions()
    window._update_root_watch_icons()

    assert not _item(window, roots[0]).icon(0).isNull()
    assert resets == []

    _item(window, roots[1]).setCheckState(0, Qt.Unchecked)

    assert resets == [1]
    assert str(roots[1]) not in window.combined.selected
