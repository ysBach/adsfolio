"""Offscreen vector rendering, palette changes, and accessible icon controls."""

from pathlib import Path

import pytest
from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPalette
from PySide6.QtWidgets import QPushButton, QWidget

from adsfolio.gui.detail_pane import DetailPane, DragHandle, MetadataRow
from adsfolio.gui.icons import _PATHS, ICON_SIZE, vector_icon
from adsfolio.gui.notifications import Notice, NoticeBar


def render_icon(icon: QIcon, size: int, mode: QIcon.Mode = QIcon.Normal) -> QImage:
    """Render an icon on a transparent surface at the requested pixel size."""
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    icon.paint(painter, QRect(0, 0, size, size), Qt.AlignCenter, mode)
    painter.end()
    return image


@pytest.mark.parametrize("name", list(_PATHS))
@pytest.mark.parametrize("size", [16, 32, 48])
def test_vectors_render_at_display_scales(qtbot, name: str, size: int) -> None:
    widget = QWidget()
    qtbot.addWidget(widget)
    icon = vector_icon(name, widget)
    image = render_icon(icon, size)
    assert any(image.pixelColor(x, y).alpha() for x in range(size) for y in range(size))
    assert image.pixelColor(0, 0).alpha() == 0
    # Exercise QIcon's cloning and pixmap paths as well as direct vector painting.
    copied = QIcon(icon)
    copied.addPixmap(icon.pixmap(QSize(16, 16)))
    assert not copied.pixmap(QSize(size, size)).isNull()


@pytest.mark.parametrize(
    "foreground,background", [("#202020", "#ffffff"), ("#eeeeee", "#202020")]
)
def test_icon_tracks_palette_and_disabled_colors(
    qtbot, foreground: str, background: str
) -> None:
    widget = QPushButton("Test")
    qtbot.addWidget(widget)
    icon = vector_icon("close", widget)
    widget.setIcon(icon)
    before = render_icon(icon, 32)
    palette = widget.palette()
    palette.setColor(QPalette.Active, QPalette.ButtonText, QColor(foreground))
    palette.setColor(QPalette.Active, QPalette.Button, QColor(background))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#808080"))
    widget.setPalette(palette)
    active = render_icon(widget.icon(), 32)
    disabled = render_icon(widget.icon(), 32, QIcon.Disabled)
    assert active != before
    assert active != disabled
    assert any(
        active.pixelColor(x, y) == QColor(foreground)
        for x in range(32)
        for y in range(32)
    )
    assert any(
        disabled.pixelColor(x, y) == QColor("#808080")
        for x in range(32)
        for y in range(32)
    )


def test_metadata_icons_keep_edit_lock_and_drag_behavior(qtbot) -> None:
    row = MetadataRow(
        "title",
        "Title",
        "Paper",
        editable=True,
        lockable=True,
        locked=False,
        label_width=80,
    )
    qtbot.addWidget(row)
    edits = []
    locks = []
    row.edit_requested.connect(edits.append)
    row.lock_changed.connect(lambda key, locked: locks.append((key, locked)))
    assert row.edit_button.accessibleName() == "Edit Title"
    assert row.edit_button.toolTip() == "Edit Title"
    assert row.edit_button.iconSize() == ICON_SIZE
    assert row.lock_button.iconSize() == ICON_SIZE
    assert row.lock_button.accessibleName() == "Lock Title"
    row.edit_button.click()
    row.lock_button.click()
    assert edits == ["title"]
    assert locks == [("title", True)]
    assert row.lock_button.isChecked()
    assert row.lock_button.accessibleName() == "Unlock Title"
    assert "ADS cannot update" in row.lock_button.toolTip()
    row.lock_button.click()
    assert locks[-1] == ("title", False)
    assert not row.lock_button.isChecked()
    grip = row.findChild(DragHandle)
    assert grip.accessibleName() == "Drag to reorder"
    assert grip.cursor().shape() == Qt.OpenHandCursor


@pytest.mark.parametrize("severity", ["info", "warning", "error", "success", "unknown"])
def test_notice_icons_preserve_text_and_accessibility(qtbot, severity: str) -> None:
    bar = NoticeBar()
    qtbot.addWidget(bar)
    bar.enqueue(Notice("one", "Notice text", severity=severity, action_label="Retry"))
    assert bar.message.text() == "Notice text"
    assert bar.severity_icon.accessibleName() == (
        severity.capitalize() if severity != "unknown" else "Info"
    )
    expected = "Dismiss"
    assert bar.dismiss_button.text() == expected
    assert bar.dismiss_button.toolTip() == expected
    assert bar.action_button.accessibleName() == "Retry"
    assert not bar.action_button.icon().isNull()
    bar.show()
    assert not bar.grab().isNull()


def test_detail_pane_renders_with_icons_in_both_palettes(qtbot, tmp_path: Path) -> None:
    pane = DetailPane()
    qtbot.addWidget(pane)
    pane.set_record(
        {
            "current_path": "paper.pdf",
            "metadata": {
                "title": "A paper",
                "author": ["Author, A."],
                "bibcode": "2026TEST",
            },
        },
        tmp_path,
    )
    pane.resize(460, 640)
    pane.show()
    for text, background in (("#202020", "#ffffff"), ("#eeeeee", "#202020")):
        palette = pane.palette()
        for role in (QPalette.WindowText, QPalette.ButtonText, QPalette.Text):
            palette.setColor(role, QColor(text))
        for role in (QPalette.Window, QPalette.Button, QPalette.Base):
            palette.setColor(role, QColor(background))
        pane.setPalette(palette)
        qtbot.wait(10)
        assert not pane.grab().isNull()


def test_icon_label_reuses_rendering_until_palette_or_symbol_changes(
    qtbot, monkeypatch
) -> None:
    from adsfolio.gui import icons

    calls = []
    original = icons.vector_icon

    def counted_icon(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(icons, "vector_icon", counted_icon)
    label = icons.VectorIconLabel("info", "Information")
    qtbot.addWidget(label)
    first = label.grab().toImage()
    assert calls == ["info"]
    assert label.grab().toImage() == first
    assert calls == ["info"]
    palette = label.palette()
    palette.setColor(QPalette.WindowText, QColor("#c04020"))
    label.setPalette(palette)
    assert label.grab().toImage() != first
    assert calls == ["info", "info"]
    label.set_icon("warning", "Warning")
    label.grab()
    assert calls == ["info", "info", "warning"]
