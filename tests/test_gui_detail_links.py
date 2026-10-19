"""Pointer and keyboard activation for detail-pane publication links."""

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from adsfolio.gui.detail_pane import DetailLinkLabel, DetailPane


@pytest.fixture
def label(qtbot):
    widget = DetailLinkLabel('<a href="https://example.com/paper">Publication</a>')
    widget.resize(260, 40)
    qtbot.addWidget(widget)
    widget.show()
    return widget


def test_pointer_defaults_to_double_click(label: DetailLinkLabel) -> None:
    activated = []
    label.activated.connect(activated.append)
    point = QPoint(10, label.height() // 2)
    QTest.mouseClick(label, Qt.LeftButton, pos=point)
    assert activated == []
    QTest.mouseDClick(label, Qt.LeftButton, pos=point)
    QTest.mouseRelease(label, Qt.LeftButton, pos=point)
    assert activated == ["https://example.com/paper"]


def test_single_click_opt_in(label: DetailLinkLabel) -> None:
    label.link_activation = "single"
    activated = []
    label.activated.connect(activated.append)
    QTest.mouseClick(label, Qt.LeftButton, pos=QPoint(10, label.height() // 2))
    assert activated == ["https://example.com/paper"]


@pytest.mark.parametrize("mode", ["double", "single"])
def test_keyboard_activation_is_explicit(label: DetailLinkLabel, mode: str) -> None:
    label.link_activation = mode
    activated = []
    label.activated.connect(activated.append)
    label.setFocus()
    QTest.keyClick(label, Qt.Key_Tab)
    assert activated == []
    QTest.keyClick(label, Qt.Key_Return)
    assert activated == ["https://example.com/paper"]


def test_pane_setting_updates_existing_and_new_rows(qtbot, tmp_path: Path) -> None:
    pane = DetailPane()
    qtbot.addWidget(pane)
    record = {"current_path": "paper.pdf", "metadata": {"bibcode": "2026TEST"}}
    pane.set_record(record, tmp_path)
    assert pane._row_widgets["links"].link_label.link_activation == "double"
    pane.set_link_activation("single")
    assert pane._row_widgets["links"].link_label.link_activation == "single"
    pane.set_record(record, tmp_path)
    assert pane._row_widgets["links"].link_label.link_activation == "single"
    pane.set_link_activation("double")
    assert pane._row_widgets["links"].link_label.link_activation == "double"
    assert not pane._row_widgets["links"].link_label.openExternalLinks()
    with pytest.raises(ValueError):
        pane.set_link_activation("invalid")


@pytest.mark.parametrize("mode", ["double", "single"])
def test_non_primary_and_blank_area_clicks_do_not_activate(
    label: DetailLinkLabel, mode: str
) -> None:
    label.link_activation = mode
    activated = []
    label.activated.connect(activated.append)
    QTest.mouseClick(label, Qt.RightButton, pos=QPoint(10, label.height() // 2))
    QTest.mouseDClick(label, Qt.LeftButton, pos=QPoint(240, label.height() // 2))
    QTest.mouseRelease(label, Qt.LeftButton, pos=QPoint(240, label.height() // 2))
    assert activated == []


def test_pane_opens_configured_browser_only_after_double_click(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtWidgets import QApplication

    opened = []
    monkeypatch.setattr(
        "adsfolio.gui.detail_pane.open_url",
        lambda url, command: opened.append((url, command)),
    )
    pane = DetailPane()
    qtbot.addWidget(pane)
    pane.set_application_commands(
        pdf_viewer_command=None, web_browser_command="custom-browser"
    )
    pane.set_record(
        {"current_path": "paper.pdf", "metadata": {"bibcode": "2026TEST"}}, tmp_path
    )
    pane.resize(520, 900)
    pane.show()
    QApplication.processEvents()
    link = pane._row_widgets["links"].link_label
    point = QPoint(
        link.contentsRect().left()
        + link.fontMetrics().horizontalAdvance("Classic ADS") // 2,
        link.contentsRect().top() + link.fontMetrics().height() // 2,
    )
    QTest.mouseClick(link, Qt.LeftButton, pos=point)
    assert opened == []
    QTest.mouseDClick(link, Qt.LeftButton, pos=point)
    QTest.mouseRelease(link, Qt.LeftButton, pos=point)
    assert opened == [("https://adsabs.harvard.edu/abs/2026TEST", "custom-browser")]


@pytest.mark.parametrize("mode", ["double", "single"])
def test_complete_qt_double_click_sequence_opens_once(
    label: DetailLinkLabel, mode: str
) -> None:
    """Send press/release/double-click/release, as Qt delivers a real double click."""
    label.link_activation = mode
    activated = []
    label.activated.connect(activated.append)
    point = QPoint(10, label.height() // 2)
    QTest.mousePress(label, Qt.LeftButton, pos=point)
    QTest.mouseRelease(label, Qt.LeftButton, pos=point)
    assert len(activated) == (1 if mode == "single" else 0)
    QTest.mouseDClick(label, Qt.LeftButton, pos=point)
    QTest.mouseRelease(label, Qt.LeftButton, pos=point)
    assert activated == ["https://example.com/paper"]


@pytest.mark.parametrize("mode", ["double", "single"])
@pytest.mark.parametrize(
    "modifier", [Qt.ShiftModifier, Qt.ControlModifier, Qt.AltModifier, Qt.MetaModifier]
)
def test_modified_pointer_gestures_do_not_open(
    label: DetailLinkLabel, mode: str, modifier: Qt.KeyboardModifier
) -> None:
    label.link_activation = mode
    activated = []
    label.activated.connect(activated.append)
    point = QPoint(10, label.height() // 2)
    QTest.mouseClick(label, Qt.LeftButton, modifier, pos=point)
    QTest.mouseDClick(label, Qt.LeftButton, modifier, pos=point)
    QTest.mouseRelease(label, Qt.LeftButton, modifier, pos=point)
    assert activated == []
    # A modifier released before the pointer must not turn selection into opening.
    QTest.mousePress(label, Qt.LeftButton, modifier, pos=point)
    QTest.mouseRelease(label, Qt.LeftButton, Qt.NoModifier, pos=point)
    assert activated == []


def test_detail_link_label_tooltip_and_accessible_description(
    label: DetailLinkLabel,
) -> None:
    assert label.toolTip() == "Double-click to open in viewer or browser."
    assert label.accessibleDescription() == "Double-click to open in viewer or browser."
    label.link_activation = "single"
    label._sync_activation_feedback()
    assert label.toolTip() == "Click to open in viewer or browser."
    assert label.accessibleDescription() == "Click to open in viewer or browser."
