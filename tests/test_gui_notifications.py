"""Tests for the compact non-modal GUI notice queue."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from adsfolio.gui import notifications
from adsfolio.gui.notifications import Notice, NoticeAction, NoticeBar


def test_notice_bar_shows_one_item_and_pending_count(qtbot):
    bar = NoticeBar()
    qtbot.addWidget(bar)

    bar.enqueue(Notice("one", "One", action_label="Identify & Rename"))
    bar.enqueue(Notice("two", "Two", action_label="Identify & Rename"))

    assert bar.message.text() == "One"
    assert bar.pending_count.text() == "1 more"


def test_notice_bar_exposes_paths_for_current_and_queued_files(qtbot, tmp_path) -> None:
    bar = NoticeBar()
    qtbot.addWidget(bar)
    first = tmp_path / "one" / "paper.pdf"
    second = tmp_path / "two" / "paper.pdf"
    message = "paper.pdf: No ADS record identified"
    bar.enqueue(Notice("one", message, details=str(first)))
    bar.enqueue(Notice("two", message, details=str(second)))

    assert bar.message.toolTip() == str(first)
    assert str(first) in bar.pending_count.toolTip()
    assert str(second) in bar.pending_count.toolTip()

    bar.dismiss("one")

    assert bar.message.toolTip() == str(second)
    assert bar.pending_count.toolTip() == ""

    bar.dismiss("two")

    assert bar.message.toolTip() == ""
    assert bar.pending_count.toolTip() == ""


@pytest.mark.parametrize(
    ("platform", "label"),
    [("darwin", "Reveal in Finder"), ("linux", "Reveal in File Manager")],
)
def test_notice_reveal_uses_platform_label(monkeypatch, qtbot, platform, label) -> None:
    """Use the native file manager's name in the reveal button."""
    monkeypatch.setattr(notifications, "sys", SimpleNamespace(platform=platform))
    bar = NoticeBar()
    qtbot.addWidget(bar)

    assert bar.reveal_button.text() == label
    assert bar.reveal_button.accessibleName() == label


@pytest.mark.parametrize("action", ["reveal", "open"])
def test_notice_reveal_tracks_displayed_path_without_dismissal(
    qtbot, tmp_path, action
) -> None:
    """Reveal follows replacements and the queue without running other actions."""
    bar = NoticeBar()
    qtbot.addWidget(bar)
    revealed, actions, dismissals = [], [], []
    getattr(bar, f"{action}_requested").connect(revealed.append)
    button = getattr(bar, f"{action}_button")
    bar.action_requested.connect(actions.append)
    bar.dismiss_requested.connect(dismissals.append)
    first = tmp_path / "first.pdf"
    moved = tmp_path / "unresolved" / "first.pdf"
    second = tmp_path / "second.pdf"
    bar.enqueue(Notice("first", "New PDF", reveal_path=first))
    bar.enqueue(Notice("second", "Another PDF", reveal_path=second))
    assert not button.isHidden()
    assert button.toolTip() == str(first)
    button.click()
    assert bar.queued_keys() == ("first", "second")

    bar.enqueue(Notice("first", "No match", reveal_path=moved))
    assert button.toolTip() == str(moved)
    button.click()
    bar.dismiss("first")
    assert button.toolTip() == str(second)
    button.click()
    bar.dismiss("second")
    assert button.isHidden()
    button.click()

    bar.enqueue(Notice("general", "General notification"))
    assert button.isHidden()
    button.click()
    assert revealed == [first, moved, second]
    assert actions == []
    assert dismissals == []


def test_failed_notice_offers_identifier_lookup_without_retry_or_manual_entry(
    qtbot,
) -> None:
    bar = NoticeBar()
    qtbot.addWidget(bar)
    updates, retries, edits = [], [], []
    payload = object()
    bar.update_by_id_requested.connect(updates.append)
    bar.action_requested.connect(retries.append)
    bar.manual_entry_requested.connect(edits.append)
    bar.enqueue(
        Notice(
            "failed",
            "Lookup failed",
            action=NoticeAction.RETRY,
            payload=payload,
            manual_entry=True,
            update_by_id=True,
        )
    )
    bar.enqueue(Notice("next", "Another notice"))
    assert bar.update_by_id_button.text() == "Update by ID…"
    assert not bar.update_by_id_button.isHidden()
    bar.update_by_id_button.click()
    assert updates == [payload]
    assert retries == edits == []
    assert bar.queued_keys() == ("failed", "next")
    bar.dismiss("failed")
    assert bar.update_by_id_button.isHidden()
    bar.update_by_id_button.click()
    assert updates == [payload]


def test_failed_notice_offers_manual_entry_beside_retry(qtbot) -> None:
    """Manual entry uses the current warning's PDF without triggering Retry."""
    bar = NoticeBar()
    qtbot.addWidget(bar)
    edits, retries = [], []
    payload = object()
    bar.manual_entry_requested.connect(edits.append)
    bar.action_requested.connect(retries.append)
    bar.enqueue(
        Notice(
            "failed",
            "Lookup failed",
            action=NoticeAction.RETRY,
            payload=payload,
            manual_entry=True,
        )
    )
    bar.enqueue(Notice("next", "Another notice"))
    assert bar.action_button.text() == "Retry"
    assert not bar.manual_entry_button.isHidden()

    bar.manual_entry_button.click()

    assert edits == [payload]
    assert retries == []
    assert bar.queued_keys() == ("failed", "next")
    bar.dismiss("failed")
    assert bar.manual_entry_button.isHidden()
    bar.manual_entry_button.click()
    assert edits == [payload]


def test_notice_replacement_does_not_duplicate_identity(qtbot):
    bar = NoticeBar()
    qtbot.addWidget(bar)
    bar.enqueue(Notice("one", "Detected"))

    bar.enqueue(Notice("one", "Resolved"))

    assert bar.queued_keys() == ("one",)
    assert bar.message.text() == "Resolved"


def test_notice_bar_dismisses_in_fifo_order(qtbot):
    bar = NoticeBar()
    qtbot.addWidget(bar)
    bar.enqueue(Notice("one", "One"))
    bar.enqueue(Notice("two", "Two"))

    bar.dismiss("one")

    assert bar.queued_keys() == ("two",)
    assert bar.message.text() == "Two"
    assert bar.pending_count.text() == ""


def test_notice_bar_emits_opaque_payloads_instead_of_callbacks(qtbot):
    bar = NoticeBar()
    qtbot.addWidget(bar)
    payload = object()
    actions = []
    dismissals = []
    bar.action_requested.connect(actions.append)
    bar.dismiss_requested.connect(dismissals.append)
    bar.enqueue(
        Notice(
            "one",
            "One",
            action_label="Identify & Rename",
            payload=payload,
        )
    )

    bar.action_button.click()
    bar.dismiss_button.click()

    assert actions == [payload]
    assert dismissals == [payload]
    assert bar.queued_keys() == ()


def test_notice_bar_clears_only_matching_root(qtbot, tmp_path):
    bar = NoticeBar()
    qtbot.addWidget(bar)
    one = tmp_path / "one"
    two = tmp_path / "two"
    bar.enqueue(Notice("one", "One", root=one))
    bar.enqueue(Notice("two", "Two", root=two))

    bar.clear_root(Path(one))

    assert bar.queued_keys() == ("two",)


def test_notice_bar_does_not_offer_identify_all_for_retry_notices(qtbot):
    bar = NoticeBar()
    qtbot.addWidget(bar)
    bar.enqueue(Notice("one", "Failed one", action_label="Retry", payload="one"))
    bar.enqueue(Notice("two", "Failed two", action_label="Retry", payload="two"))

    assert not bar.action_all_button.isVisible()


def test_notice_bar_emits_bulk_payloads_and_clears_on_dismiss_all(qtbot):
    bar = NoticeBar()
    qtbot.addWidget(bar)
    action_payloads = []
    dismissal_payloads = []
    bar.action_all_requested.connect(action_payloads.append)
    bar.dismiss_all_requested.connect(dismissal_payloads.append)
    bar.enqueue(
        Notice(
            "one",
            "One",
            action_label="Identify & Rename",
            payload="payload-one",
        )
    )
    bar.enqueue(
        Notice(
            "two",
            "Two",
            action_label="Identify & Rename",
            payload="payload-two",
        )
    )

    bar.action_all_button.click()
    bar.dismiss_all_button.click()

    assert action_payloads == [("payload-one", "payload-two")]
    assert dismissal_payloads == [("payload-one", "payload-two")]
    assert bar.queued_keys() == ()


def test_identify_all_excludes_retry_payloads_from_mixed_queue(qtbot) -> None:
    bar = NoticeBar()
    qtbot.addWidget(bar)
    emitted = []
    bar.action_all_requested.connect(emitted.append)
    bar.enqueue(
        Notice(
            "manual-one",
            "One",
            action=NoticeAction.IDENTIFY_RENAME,
            payload="manual-one",
        )
    )
    bar.enqueue(Notice("retry", "Retry", action=NoticeAction.RETRY, payload="retry"))
    bar.enqueue(
        Notice(
            "manual-two",
            "Two",
            action=NoticeAction.IDENTIFY_RENAME,
            payload="manual-two",
        )
    )

    bar.action_all_button.click()

    assert emitted == [("manual-one", "manual-two")]


def test_notice_bar_escapes_ampersand_in_button_labels(qtbot) -> None:
    bar = NoticeBar()
    qtbot.addWidget(bar)
    bar.enqueue(
        Notice(
            "one",
            "One",
            action=NoticeAction.IDENTIFY_RENAME,
            payload="one",
        )
    )
    bar.enqueue(
        Notice(
            "two",
            "Two",
            action=NoticeAction.IDENTIFY_RENAME,
            payload="two",
        )
    )

    assert NoticeAction.IDENTIFY_RENAME.value == "Identify & Rename"
    assert bar.action_button.text() == "Identify && Rename"
    assert bar.action_all_button.text() == "Identify && Rename All"


def test_success_acknowledgement_keeps_queue_and_payload_behavior(qtbot):
    bar = NoticeBar()
    qtbot.addWidget(bar)
    dismissed = []
    bar.dismiss_requested.connect(dismissed.append)
    bar.enqueue(
        Notice("renamed", "Renamed paper.pdf", severity="success", payload="renamed")
    )
    bar.enqueue(
        Notice(
            "identified",
            "Identified paper.pdf",
            severity="success",
            payload="identified",
        )
    )
    assert bar.dismiss_button.text() == "OK"
    assert bar.dismiss_button.accessibleName() == "OK"
    assert bar.dismiss_all_button.text() == "OK All"
    bar.enqueue(Notice("warning", "Needs review", severity="warning"))
    assert bar.dismiss_all_button.text() == "Dismiss All"
    bar.dismiss_button.click()
    assert dismissed == ["renamed"]
    assert bar.queued_keys() == ("identified", "warning")
    bar.dismiss_button.click()
    assert bar.dismiss_button.text() == "Dismiss"
    assert bar.dismiss_button.accessibleName() == "Dismiss"
