"""Check detail actions and edits during selection changes."""

from __future__ import annotations

import os
from copy import deepcopy

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QTimer
from PySide6.QtGui import QHelpEvent
from PySide6.QtWidgets import (
    QApplication,
    QInputDialog,
    QMessageBox,
    QPushButton,
    QStyle,
    QStyleOptionButton,
    QToolTip,
)

from adsfolio.gui.detail_pane import DetailPane


@pytest.fixture
def pane(qtbot):
    widget = DetailPane()
    qtbot.addWidget(widget)
    return widget


def action_buttons(pane: DetailPane) -> dict[str, QPushButton]:
    return {button.text(): button for button in pane._actions.findChildren(QPushButton)}


def record() -> dict:
    return {
        "location_id": 12,
        "attachment_id": 34,
        "current_path": "paper.pdf",
        "metadata": {"title": ["Initial title"], "note": "Original note"},
    }


def test_action_tooltips_follow_selection_and_reason(pane, tmp_path):
    buttons = action_buttons(pane)
    assert set(buttons) == {"Update by ID…", "Enter Details…", "Open", "Reveal"}
    for button in buttons.values():
        assert not button.isEnabled()
        assert "Select a PDF" in button.toolTip()

    first_root = tmp_path / "Library A"
    second_root = tmp_path / "Library B"
    pane.set_record(record(), first_root)
    assert all(button.isEnabled() for button in buttons.values())
    assert "Look up a Bibcode" in buttons["Update by ID…"].toolTip()
    assert "Enter paper metadata" in buttons["Enter Details…"].toolTip()
    for button in buttons.values():
        assert str(first_root) in button.toolTip()

    pane.set_read_only(True, reason="An ADS recheck is running.")
    for name in ("Update by ID…", "Enter Details…"):
        assert not buttons[name].isEnabled()
        assert "An ADS recheck is running." in buttons[name].toolTip()
    for name in ("Open", "Reveal"):
        assert buttons[name].isEnabled()
        assert "An ADS recheck is running." not in buttons[name].toolTip()

    pane.set_read_only(True, reason="Editing is blocked by another task.")
    assert "another task" in buttons["Update by ID…"].toolTip()
    assert "ADS recheck" not in buttons["Update by ID…"].toolTip()
    pane.set_record(record(), second_root)
    for button in buttons.values():
        assert str(second_root) in button.toolTip()
        assert str(first_root) not in button.toolTip()

    pane.set_read_only(False)
    assert all(button.isEnabled() for button in buttons.values())
    assert "another task" not in buttons["Update by ID…"].toolTip()
    assert "Look up a Bibcode" in buttons["Update by ID…"].toolTip()
    pane.set_record(None, second_root)
    assert all("Select a PDF" in button.toolTip() for button in buttons.values())
    assert all(not button.isEnabled() for button in buttons.values())


def test_read_only_without_reason_uses_generic_message(pane, tmp_path):
    pane.set_read_only(True)
    buttons = action_buttons(pane)
    assert "Select a PDF" in buttons["Update by ID…"].toolTip()
    assert "Editing is unavailable" in buttons["Update by ID…"].toolTip()
    pane.set_record(record(), tmp_path)
    for name in ("Update by ID…", "Enter Details…"):
        assert "Editing is unavailable" in buttons[name].toolTip()
        assert "combined" not in buttons[name].toolTip().lower()
    pane.set_read_only(True, reason="")
    assert "Editing is unavailable" in buttons["Update by ID…"].toolTip()


def assert_rendered_actions(pane, enabled):
    for name, button in action_buttons(pane).items():
        expected = name in enabled
        assert button.isEnabled() is expected
        option = QStyleOptionButton()
        option.initFrom(button)
        assert bool(option.state & QStyle.State_Enabled) is expected


def test_identifier_update_recheck_selection_transitions(pane, qtbot, tmp_path):
    pane.show()
    qtbot.waitExposed(pane)
    buttons = action_buttons(pane)
    update = buttons["Update by ID…"]
    stop_message = "The automatic ADS recheck will stop before this update."

    pane.set_read_only(
        True,
        reason="An ADS recheck is running.",
        allow_identifier_update=True,
    )
    assert_rendered_actions(pane, set())
    assert "Select a PDF" in update.toolTip()
    assert stop_message in update.toolTip()
    assert update.accessibleDescription() == update.toolTip()

    pane.set_record(record(), tmp_path / "first")
    assert_rendered_actions(pane, {"Update by ID…", "Open", "Reveal"})
    assert "Editing is unavailable" not in update.toolTip()
    assert stop_message in update.toolTip()
    assert update.accessibleDescription() == update.toolTip()
    assert "Editing is unavailable" in buttons["Enter Details…"].toolTip()
    for name in ("Open", "Reveal"):
        assert "unavailable" not in buttons[name].toolTip()
        assert buttons[name].accessibleDescription() == buttons[name].toolTip()

    pane.set_record(record(), tmp_path / "second")
    assert_rendered_actions(pane, {"Update by ID…", "Open", "Reveal"})
    assert str(tmp_path / "second") in update.toolTip()
    assert str(tmp_path / "first") not in update.toolTip()

    pane.set_record(None, tmp_path / "second")
    assert_rendered_actions(pane, set())
    assert "Select a PDF" in update.toolTip()
    assert update.accessibleDescription() == update.toolTip()


def test_identifier_update_changes_while_read_only(pane, qtbot, tmp_path):
    pane.show()
    qtbot.waitExposed(pane)
    pane.set_record(record(), tmp_path)
    buttons = action_buttons(pane)
    update = buttons["Update by ID…"]
    stop_message = "The automatic ADS recheck will stop before this update."
    assert_rendered_actions(pane, set(buttons))

    pane.set_read_only(True, reason="A library task is running.")
    assert_rendered_actions(pane, {"Open", "Reveal"})
    assert "A library task is running." in update.toolTip()
    assert stop_message not in update.accessibleDescription()

    pane.set_read_only(
        True,
        reason="An ADS recheck is running.",
        allow_identifier_update=True,
    )
    assert_rendered_actions(pane, {"Update by ID…", "Open", "Reveal"})
    assert stop_message in update.toolTip()
    assert update.accessibleDescription() == update.toolTip()

    pane.set_read_only(True, reason="A library task is running.")
    assert_rendered_actions(pane, {"Open", "Reveal"})
    assert "A library task is running." in update.toolTip()
    assert stop_message not in update.accessibleDescription()

    pane.set_read_only(False, allow_identifier_update=True)
    assert_rendered_actions(pane, set(buttons))
    assert stop_message not in update.accessibleDescription()
    assert pane._row_widgets["title"]._editable
    assert pane._row_widgets["title"]._lockable
    assert pane._row_widgets["title"].edit_button.isEnabled()
    assert pane._row_widgets["title"].lock_button.isEnabled()
    pane.set_read_only(True)
    assert_rendered_actions(pane, {"Open", "Reveal"})
    assert stop_message not in update.accessibleDescription()


def test_manual_details_can_preempt_recheck(pane, tmp_path):
    pane.set_record(record(), tmp_path)
    pane.set_read_only(
        True,
        reason="An ADS recheck is running.",
        allow_identifier_update=True,
        allow_manual_metadata=True,
    )
    buttons = action_buttons(pane)
    assert_rendered_actions(pane, set(buttons))
    assert "recheck will stop" in buttons["Enter Details…"].toolTip()
    assert not pane._row_widgets["title"]._editable
    assert not pane._row_widgets["title"]._lockable


def test_identifier_update_does_not_enable_other_edits(pane, tmp_path):
    selected = record()
    selected["metadata"]["bibcode"] = "2020A&A...123A...1A"
    pane.set_record(selected, tmp_path)
    pane.set_read_only(True, allow_identifier_update=True)
    updates = []
    manual_edits = []
    metadata_edits = []
    pane.ads_update_requested.connect(updates.append)
    pane.manual_metadata_requested.connect(lambda: manual_edits.append(True))
    pane.metadata_changed.connect(metadata_edits.append)

    pane.update_button.click()
    pane.manual_metadata_button.click()
    row = pane._row_widgets["title"]
    assert not row._editable
    assert not row._lockable
    assert not row.edit_button.isEnabled()
    assert not row.lock_button.isEnabled()
    assert row.edit_button.isHidden()
    assert row.lock_button.isHidden()
    menu = row.create_context_menu()
    assert not {"Edit…", "Lock", "Unlock"} & {
        action.text() for action in menu.actions()
    }
    row.edit_button.click()
    row.lock_button.click()
    row._request_edit()
    assert updates == ["2020A&A...123A...1A"]
    assert manual_edits == []
    assert metadata_edits == []


@pytest.mark.parametrize("blocked", ["no_selection", "read_only"])
def test_disabled_action_tooltips_appear_for_help_events(
    pane, qtbot, tmp_path, blocked
):
    if blocked == "read_only":
        pane.set_record(record(), tmp_path)
        pane.set_read_only(True, reason="An ADS recheck is running.")
    pane.resize(550, 450)
    pane.show()
    qtbot.waitExposed(pane)
    buttons = action_buttons(pane)
    names = (
        tuple(buttons)
        if blocked == "no_selection"
        else ("Update by ID…", "Enter Details…")
    )
    for name in names:
        button = buttons[name]
        assert not button.isEnabled()
        QToolTip.hideText()
        position = button.rect().center()
        event = QHelpEvent(QEvent.ToolTip, position, button.mapToGlobal(position))
        QApplication.sendEvent(button, event)
        qtbot.waitUntil(
            lambda button=button: (
                QToolTip.isVisible() and QToolTip.text() == button.toolTip()
            ),
            timeout=3500,
        )
    QToolTip.hideText()


def accept_during_dialog(pane, change):
    def update_and_accept():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, QInputDialog)
            change()
            dialog.setTextValue("Edited title")
        finally:
            if isinstance(dialog, QInputDialog):
                dialog.accept()

    QTimer.singleShot(0, update_and_accept)
    pane._row_widgets["title"].edit_button.click()


@pytest.mark.parametrize(
    "change",
    [
        "root",
        "location",
        "path",
        "attachment",
        "deselected",
        "field",
        "field_removed",
        "source_field_in_place",
        "source_other_in_place",
        "read_only",
        "record_restored",
        "field_restored",
        "read_only_restored",
    ],
)
def test_rejects_stale_inline_edit(pane, monkeypatch, tmp_path, change):
    root = tmp_path / "first"
    original = record()
    pane.set_record(original, root)
    emitted = []
    warnings = []
    pane.metadata_changed.connect(emitted.append)
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda parent, title, text: warnings.append((title, text)),
    )

    def change_state():
        updated = deepcopy(original)
        if change == "root":
            pane.set_record(updated, tmp_path / "second")
        elif change == "location":
            updated["location_id"] = 99
            pane.set_record(updated, root)
        elif change == "path":
            updated["current_path"] = "renamed.pdf"
            pane.set_record(updated, root)
        elif change == "attachment":
            updated["attachment_id"] = 100
            pane.set_record(updated, root)
        elif change == "deselected":
            pane.set_record(None, root)
        elif change in {"field", "field_restored"}:
            updated["metadata"]["title"] = ["Changed elsewhere"]
            pane.set_record(updated, root)
            if change == "field_restored":
                pane.set_record(original, root)
        elif change == "field_removed":
            del updated["metadata"]["title"]
            pane.set_record(updated, root)
        elif change == "source_field_in_place":
            original["metadata"]["title"] = ["Changed elsewhere"]
        elif change == "source_other_in_place":
            original["metadata"]["note"] = "Changed elsewhere"
        elif change in {"read_only", "read_only_restored"}:
            pane.set_read_only(True, reason="An ADS recheck is running.")
            if change == "read_only_restored":
                pane.set_read_only(False)
        elif change == "record_restored":
            updated["location_id"] = 99
            pane.set_record(updated, root)
            pane.set_record(original, root)

    accept_during_dialog(pane, change_state)
    assert emitted == []
    assert len(warnings) == 1
    assert "not saved" in warnings[0][1].lower()
    expected_title = (
        "Changed elsewhere" if change == "source_field_in_place" else "Initial title"
    )
    assert original["metadata"]["title"] == [expected_title]


def test_inline_edit_preserves_other_refreshed_metadata(pane, monkeypatch, tmp_path):
    root = tmp_path / "first"
    original = record()
    pane.set_record(original, root)
    emitted = []
    warnings = []
    pane.metadata_changed.connect(emitted.append)
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda parent, title, text: warnings.append((title, text)),
    )
    refreshed = deepcopy(original)
    refreshed["metadata"]["note"] = "Updated elsewhere"
    refreshed["metadata"]["other"] = {"values": [1, 2]}

    accept_during_dialog(pane, lambda: pane.set_record(refreshed, root))

    assert emitted == [
        {
            "title": ["Edited title"],
            "note": "Updated elsewhere",
            "other": {"values": [1, 2]},
        }
    ]
    assert warnings == []
    assert refreshed["metadata"]["title"] == ["Initial title"]
