"""Check presentation of a proven annotation-only watcher update."""

from dataclasses import replace
from pathlib import Path

from test_gui_main_window_watch import _event, _window_factory

from adsfolio.config import Config
from adsfolio.gui.config import GuiConfig, RootEntry
from adsfolio.gui.notifications import Notice
from adsfolio.pdf.watch_session import WatchOutcome, WatchState


def test_annotation_update_refreshes_without_notice_or_merge_prompt(
    monkeypatch, qtbot, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    root = tmp_path / "library"
    root.mkdir()
    window, controller, _ = _window_factory(
        monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(root))])
    )
    window.watch_current_action.trigger()
    event = replace(
        _event(root, controller.session_id(root)), state=WatchState.RESOLVED
    )
    refreshed = []
    prompted = []
    window.notice_bar.enqueue(Notice(window._watch_notice_key(event), "Old error"))
    window.notice_bar.enqueue(Notice("unrelated", "Another notice"))
    monkeypatch.setattr(window, "_schedule_watch_refresh", refreshed.append)
    monkeypatch.setattr(
        window, "_offer_edition_merge", lambda *args: prompted.append(args)
    )

    controller.outcome.emit(
        WatchOutcome(event, destination=event.path, annotation_update=True)
    )

    assert refreshed == [root.resolve()]
    assert window.notice_bar.queued_keys() == ("unrelated",)
    assert prompted == []
