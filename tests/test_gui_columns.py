"""Table column order, visibility, and alignment preferences.

The model and cell delegates apply the alignment, and all three settings
persist across a restart.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt, QTimer
from PySide6.QtGui import QMouseEvent, QPainter, QPicture
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QMenu, QStyleOptionViewItem

from adsfolio.citations import CitationStyle
from adsfolio.config import Config
from adsfolio.gui.appearance import AppearanceDialog
from adsfolio.gui.config import GuiConfig, config_path, load_config, save_config
from adsfolio.gui.library import FileQuery, build_columns
from adsfolio.gui.main_window import MainWindow
from adsfolio.gui.models import (
    FileTableModel,
    FirstAuthorDelegate,
    LinkDelegate,
    ResolutionDelegate,
)


@pytest.fixture(autouse=True)
def isolated_gui_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    save_config(GuiConfig(check_updates_automatically=False, citation_styles=[]))


def _column_index(window: MainWindow, key: str) -> int:
    return next(i for i, column in enumerate(window.columns) if column.key == key)


def _visual_order(window: MainWindow) -> list[str]:
    header = window.table.header()
    return [
        window.columns[header.logicalIndex(visual)].key
        for visual in range(header.count())
    ]


def _drag_header(qtbot, window: MainWindow, source: int, target: int) -> None:
    header = window.table.header()
    viewport = header.viewport()
    start = QPoint(
        header.sectionViewportPosition(source) + header.sectionSize(source) // 2,
        header.height() // 2,
    )
    end = QPoint(
        header.sectionViewportPosition(target) + header.sectionSize(target) - 10,
        start.y(),
    )
    qtbot.mousePress(viewport, Qt.LeftButton, pos=start)
    for position in (start + QPoint(20, 0), end):
        event = QMouseEvent(
            QEvent.MouseMove,
            QPointF(position),
            QPointF(viewport.mapToGlobal(position)),
            Qt.NoButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )
        QApplication.sendEvent(viewport, event)
    qtbot.mouseRelease(viewport, Qt.LeftButton, pos=end)


def test_column_first_header_drag_survives_appearance_and_restart(qtbot, tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    pdf = library / "example.pdf"
    pdf.write_bytes(b"%PDF-1.4\nfictional content\n%%EOF\n")
    before = hashlib.sha256(pdf.read_bytes()).hexdigest()
    window = MainWindow()
    qtbot.addWidget(window)
    window._register_root(library)
    window._select_root(library)
    assert window.model.rowCount() == 1
    window.resize(1800, 800)
    window.show()
    window.table.setColumnWidth(0, 160)
    window.table.setColumnWidth(1, 160)
    qtbot.wait(20)
    initial = _visual_order(window)
    initial_sort = window.current_query

    _drag_header(qtbot, window, 0, 1)

    reordered = _visual_order(window)
    assert reordered[:2] == [initial[1], initial[0]]
    assert window.config.table_column_order == reordered
    assert load_config().table_column_order == reordered
    assert window.current_query == initial_sort
    observed = {}

    def accept_appearance() -> None:
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, AppearanceDialog)
        observed["order"] = dialog.table_column_order
        dialog.accept()

    QTimer.singleShot(0, accept_appearance)
    window._edit_appearance()
    assert observed["order"] == reordered
    assert _visual_order(window) == reordered
    window._set_column_alignment("title", "center")
    assert hashlib.sha256(pdf.read_bytes()).hexdigest() == before
    window.close()

    restored = MainWindow()
    qtbot.addWidget(restored)
    assert _visual_order(restored) == reordered
    assert restored.config.column_alignments == {"title": "center"}
    assert restored.model.rowCount() == 1
    assert hashlib.sha256(pdf.read_bytes()).hexdigest() == before
    restored.close()


def test_column_header_menu_targets_the_clicked_column_after_reordering(
    qtbot, tmp_path
):
    window = MainWindow()
    qtbot.addWidget(window)
    root = tmp_path / "library"
    root.mkdir()
    window._register_root(root)
    window._select_root(root)
    window.resize(1800, 800)
    window.show()
    header = window.table.header()
    title = _column_index(window, "title")
    author = _column_index(window, "author")
    header.moveSection(header.visualIndex(author), 0)
    qtbot.wait(20)
    reordered = _visual_order(window)

    position = QPoint(header.sectionViewportPosition(title) + 10, header.height() // 2)
    qtbot.mouseClick(header.viewport(), Qt.LeftButton, pos=position)
    assert window.model.query.order_by == "title"
    assert _visual_order(window) == reordered

    observed = []

    def select_right():
        menu = QApplication.activePopupWidget()
        try:
            if isinstance(menu, QMenu):
                alignment = next(
                    action.menu()
                    for action in menu.actions()
                    if action.text() == "Align Title"
                )
                next(
                    action for action in alignment.actions() if action.text() == "Right"
                ).trigger()
                observed.append(True)
        finally:
            if menu is not None:
                menu.close()

    QTimer.singleShot(0, select_right)
    window._show_header_context_menu(position)

    assert observed == [True]
    assert window.config.column_alignments == {"title": "right"}
    assert window.model.headerData(title, Qt.Horizontal, Qt.TextAlignmentRole) == int(
        Qt.AlignRight | Qt.AlignVCenter
    )
    window.close()


def test_column_hidden_order_and_alignment_survive_reordering_and_restart(qtbot):
    save_config(
        GuiConfig(
            check_updates_automatically=False,
            citation_styles=[],
            table_column_order=["year", "title", "author"],
            hidden_table_columns=["author"],
            column_alignments={"author": "right"},
        )
    )
    window = MainWindow()
    qtbot.addWidget(window)
    author = _column_index(window, "author")
    title = _column_index(window, "title")
    assert window.table.isColumnHidden(author)
    header = window.table.header()
    header.moveSection(header.visualIndex(title), 3)
    reordered = _visual_order(window)
    assert window.config.table_column_order == reordered
    window._apply_table_layout()
    assert _visual_order(window) == reordered
    assert window.table.isColumnHidden(author)
    window.close()

    restored = MainWindow()
    qtbot.addWidget(restored)
    assert _visual_order(restored) == reordered
    assert restored.table.isColumnHidden(author)
    restored._set_column_visibility("author", True)
    assert not restored.table.isColumnHidden(author)
    assert restored.model.headerData(
        author, Qt.Horizontal, Qt.TextAlignmentRole
    ) == int(Qt.AlignRight | Qt.AlignVCenter)
    restored.close()


def test_column_header_alignment_menu_updates_model_and_persists(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    title = _column_index(window, "title")
    menu = window._create_header_context_menu(title)
    alignment = next(
        action.menu() for action in menu.actions() if action.text() == "Align Title"
    )
    actions = {action.text(): action for action in alignment.actions()}
    assert actions["Default"].isChecked()
    actions["Center"].trigger()

    assert window.config.column_alignments == {"title": "center"}
    assert load_config().column_alignments == {"title": "center"}
    assert window.model.headerData(title, Qt.Horizontal, Qt.TextAlignmentRole) == int(
        Qt.AlignHCenter | Qt.AlignVCenter
    )
    window.close()
    assert load_config().column_alignments == {"title": "center"}

    restored = MainWindow()
    qtbot.addWidget(restored)
    menu = restored._create_header_context_menu(_column_index(restored, "title"))
    alignment = next(
        action.menu() for action in menu.actions() if action.text() == "Align Title"
    )
    actions = {action.text(): action for action in alignment.actions()}
    assert actions["Center"].isChecked()
    actions["Default"].trigger()
    assert restored.config.column_alignments == {}
    assert restored.model.headerData(title, Qt.Horizontal, Qt.TextAlignmentRole) is None
    restored.close()


def test_failed_column_save_keeps_live_choice_and_reports_error(qtbot, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)

    def fail_save(_config):
        raise OSError("fictional disk failure")

    with monkeypatch.context() as patch:
        patch.setattr("adsfolio.gui.main_window.save_config", fail_save)
        window._set_column_alignment("title", "right")
        assert window.config.column_alignments == {"title": "right"}
        assert load_config().column_alignments == {}
        assert any(
            notice.key == "column-preference"
            and "fictional disk failure" in notice.message
            for notice in window.notice_bar.queued_notices()
        )
    window.close()


class _StaticRegistry:
    def query_files(self, **_filters):
        return [
            {
                "status": "unresolved",
                "title": "Fictional title",
                "author": "Bach, Yoonsoo",
                "bibcode": "2026TestLongBibcode",
                "author_count": 3,
            }
        ]


def test_column_model_alignment_overrides_restore_native_defaults(qtbot):
    style = CitationStyle(id="alpha", name="Compact", template="{bibcode}")
    columns = build_columns([style])
    model = FileTableModel(_StaticRegistry(), FileQuery(), columns=columns)
    indices = {column.key: index for index, column in enumerate(columns)}
    data_signal = QSignalSpy(model.dataChanged)
    header_signal = QSignalSpy(model.headerDataChanged)
    default_count = model.index(0, indices["author_count"]).data(Qt.TextAlignmentRole)
    assert default_count == int(Qt.AlignRight | Qt.AlignVCenter)
    assert model.index(0, indices["author"]).data(Qt.TextAlignmentRole) is None

    choices = {
        "title": ("left", Qt.AlignLeft),
        "author": ("center", Qt.AlignHCenter),
        "author_count": ("center", Qt.AlignHCenter),
        "status": ("right", Qt.AlignRight),
        "bibcode": ("left", Qt.AlignLeft),
        "citation:alpha": ("right", Qt.AlignRight),
    }
    model.set_column_alignments({key: value for key, (value, _flag) in choices.items()})
    for key, (_value, flag) in choices.items():
        expected = int(flag | Qt.AlignVCenter)
        assert model.index(0, indices[key]).data(Qt.TextAlignmentRole) == expected
        assert (
            model.headerData(indices[key], Qt.Horizontal, Qt.TextAlignmentRole)
            == expected
        )
    assert data_signal.count() == 1
    assert header_signal.count() == 1

    model.set_column_alignments({})
    assert (
        model.index(0, indices["author_count"]).data(Qt.TextAlignmentRole)
        == default_count
    )
    assert model.index(0, indices["author"]).data(Qt.TextAlignmentRole) is None
    assert (
        model.headerData(indices["author"], Qt.Horizontal, Qt.TextAlignmentRole) is None
    )


class _RecordingPainter(QPainter):
    def __init__(self):
        self.picture = QPicture()
        super().__init__(self.picture)
        self.text_calls = []
        self.ellipse_calls = []

    def drawText(self, *args):
        self.text_calls.append(args)
        return super().drawText(*args)

    def drawEllipse(self, *args):
        self.ellipse_calls.append(args)
        return super().drawEllipse(*args)


def test_column_custom_delegates_honor_alignment(qtbot):
    model = FileTableModel(_StaticRegistry(), FileQuery())
    indices = {column.key: index for index, column in enumerate(model.columns)}
    option = QStyleOptionViewItem()
    option.font = QApplication.font()
    option.rect = QRect(0, 0, 180, 28)
    author_positions = []
    marker_positions = []
    badge_flags = []

    for value in ("left", "center", "right"):
        model.set_column_alignments(
            {key: value for key in ("author", "status", "bibcode")}
        )
        painter = _RecordingPainter()
        FirstAuthorDelegate().paint(painter, option, model.index(0, indices["author"]))
        author_positions.append(painter.text_calls[0][0].x())
        painter.end()

        painter = _RecordingPainter()
        ResolutionDelegate().paint(painter, option, model.index(0, indices["status"]))
        marker_positions.append(painter.ellipse_calls[0][0])
        painter.end()

        compact = QStyleOptionViewItem(option)
        compact.rect = QRect(0, 0, 45, 28)
        painter = _RecordingPainter()
        LinkDelegate("ADS").paint(painter, compact, model.index(0, indices["bibcode"]))
        badge_flags.append(painter.text_calls[0][1])
        painter.end()

    assert author_positions[0] < author_positions[1] < author_positions[2]
    assert marker_positions[0] < marker_positions[1] < marker_positions[2]
    assert badge_flags == [
        Qt.AlignLeft | Qt.AlignVCenter,
        Qt.AlignHCenter | Qt.AlignVCenter,
        Qt.AlignRight | Qt.AlignVCenter,
    ]


def test_column_config_ignores_invalid_alignment_values():
    config_path().write_text(
        json.dumps(
            {
                "column_alignments": {
                    "title": "left",
                    "citation:alpha": "right",
                    "author": "center",
                    "source": "left",
                    "bad": ["left"],
                    "wrong": "diagonal",
                    "": "right",
                }
            }
        ),
        encoding="utf-8",
    )

    assert load_config().column_alignments == {
        "title": "left",
        "author": "center",
        "citation:alpha": "right",
    }


def test_column_citation_changes_preserve_layout_and_drop_removed_alignment(qtbot):
    style = CitationStyle(id="alpha", name="Compact", template="{bibcode}")
    save_config(
        GuiConfig(
            check_updates_automatically=False,
            citation_styles=[style],
            column_alignments={"title": "center", "citation:alpha": "right"},
        )
    )
    window = MainWindow()
    qtbot.addWidget(window)
    header = window.table.header()
    header.moveSection(header.visualIndex(_column_index(window, "title")), 2)
    expected = [key for key in _visual_order(window) if key != "citation:alpha"]

    window._apply_citation_styles([])

    assert _visual_order(window) == expected
    assert window.config.column_alignments == {"title": "center"}
    window.close()
