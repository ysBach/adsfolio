"""Tests for the compact non-modal GUI notice queue."""

from pathlib import Path

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
