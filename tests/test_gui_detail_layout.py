"""Check compact metadata actions, divider geometry, and saved row positions."""

from copy import deepcopy
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QApplication, QInputDialog, QMenu

from adsfolio.gui.detail_pane import DetailPane
from adsfolio.pdf.catalog import REFERENCE_FIELD_MAP, REFERENCE_METADATA_KEY
from adsfolio.pdf.registry import LOCKED_METADATA_FIELDS_KEY


@pytest.fixture
def pane(qtbot) -> DetailPane:
    widget = DetailPane()
    widget.set_compact_rows(True)
    widget.resize(400, 650)
    widget.set_record(
        {
            "current_path": "paper.pdf",
            "metadata": {
                "title": "A paper with useful metadata",
                "author": ["First, A.", "Second, B."],
                "doi": ["10.1000/one", "10.1000/two"],
                "abstract": "This sentence describes the paper. " * 30,
                LOCKED_METADATA_FIELDS_KEY: ["title"],
            },
        },
        Path("/papers"),
    )
    qtbot.addWidget(widget)
    widget.show()
    widget.activateWindow()
    QApplication.processEvents()
    return widget


def test_compact_actions_edit_lock_and_restore_focus_after_record_reset(
    pane: DetailPane, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = pane._row_widgets["title"]
    assert row.edit_button.isHidden() and row.lock_button.isHidden()
    assert row.name.locked
    assert "Locked" in row.accessibleDescription()
    assert row.name.width() < row.value.width()
    changed = []
    pane.metadata_changed.connect(changed.append)

    def refresh_record(metadata: dict) -> None:
        pane.set_record(None, Path("/papers"))
        pane.set_record(
            {"current_path": "paper.pdf", "metadata": deepcopy(metadata)},
            Path("/papers"),
        )

    pane.metadata_changed.connect(refresh_record)
    monkeypatch.setattr(
        QInputDialog, "getMultiLineText", lambda *args: ("Edited title", True)
    )

    def edit_from_popup() -> None:
        popup = QApplication.activePopupWidget()
        assert isinstance(popup, QMenu)
        next(action for action in popup.actions() if action.text() == "Edit…").trigger()
        popup.close()

    QTimer.singleShot(0, edit_from_popup)
    event = QContextMenuEvent(
        QContextMenuEvent.Mouse, QPoint(1, 1), row.value.mapToGlobal(QPoint(1, 1))
    )
    QApplication.sendEvent(row.value, event)
    assert changed[-1]["title"] == ["Edited title"]
    assert changed[-1][LOCKED_METADATA_FIELDS_KEY] == ["title"]
    assert pane.focusWidget() is pane._row_widgets["title"]
    updated = pane._row_widgets["title"]
    {a.text(): a for a in updated.create_context_menu().actions()}["Unlock"].trigger()
    assert changed[-1][LOCKED_METADATA_FIELDS_KEY] == []


def test_context_menu_reaches_values_and_preserves_per_doi_copy(
    pane: DetailPane, monkeypatch: pytest.MonkeyPatch, qtbot
) -> None:
    row = pane._row_widgets["doi"]
    menus = []

    def close_menu() -> None:
        active = QApplication.activePopupWidget()
        if isinstance(active, QMenu):
            menus.append(active)
            active.close()

    QTimer.singleShot(0, close_menu)
    label = row.doi_labels[0]
    event = QContextMenuEvent(
        QContextMenuEvent.Mouse, QPoint(1, 1), label.mapToGlobal(QPoint(1, 1))
    )
    QApplication.sendEvent(label, event)
    assert menus
    next(a for a in menus[-1].actions() if a.text() == "Copy").trigger()
    assert QApplication.clipboard().text() == "10.1000/two"
    assert pane.focusWidget() is row


def test_shared_divider_pointer_keyboard_and_reset(pane: DetailPane, qtbot) -> None:
    widths = []
    pane.label_width_changed.connect(widths.append)
    pane.set_label_width(90)
    assert {row.name.width() for row in pane._row_widgets.values()} == {90}
    row = pane._row_widgets["title"]
    row.divider.width_requested.emit(110)
    assert {item.name.width() for item in pane._row_widgets.values()} == {110}
    handle = pane.column_header.handle(1)
    handle.setFocus()
    qtbot.keyClick(handle, Qt.Key_Left)
    assert widths[-1] < 110
    qtbot.mouseDClick(row.divider, Qt.LeftButton)
    assert widths[-1] is None
    pane.resize(300, 650)
    QApplication.processEvents()
    assert row.value.width() > row.name.width()


def test_reorder_retains_hidden_absent_and_future_field_positions(
    pane: DetailPane,
) -> None:
    saved = ["future_field", "title", "year", "abstract", "author", "doi"]
    pane.set_row_order(saved)
    pane.set_hidden_rows(["abstract"])
    record = deepcopy(pane._record)
    pane.set_record(record, Path("/papers"))
    pane.move_row("doi", 0)
    order = pane.saved_row_order()
    assert all(field in order for field in saved)
    assert [key for key in order if key in {"future_field", "year", "abstract"}] == [
        "future_field",
        "year",
        "abstract",
    ]
    pane.set_hidden_rows([])
    pane.set_record(record, Path("/papers"))
    assert pane.row_keys()[0] == "doi"


def test_inline_choices_survive_compact_mode(pane: DetailPane) -> None:
    pane.set_action_buttons_visible(show_edit=True, show_lock=False)
    pane.set_compact_rows(False)
    row = pane._row_widgets["title"]
    assert not row.edit_button.isHidden()
    assert row.lock_button.isHidden()
    pane.set_compact_rows(True)
    assert row.edit_button.isHidden()
    pane.set_compact_rows(False)
    assert not row.edit_button.isHidden()


def test_reference_owned_fields_explain_where_to_edit(pane: DetailPane) -> None:
    metadata = deepcopy(pane._metadata)
    metadata[REFERENCE_METADATA_KEY] = {"source_id": 1}
    metadata[LOCKED_METADATA_FIELDS_KEY] = ["bibcode", *REFERENCE_FIELD_MAP.values()]
    pane.set_record(
        {"current_path": "paper.pdf", "metadata": metadata}, Path("/papers")
    )
    row = pane._row_widgets["title"]
    labels = [action.text() for action in row.create_context_menu().actions()]
    assert "Edit in File → Local References…" in labels
    assert "Edit…" not in labels
    assert "Unlock" not in labels
