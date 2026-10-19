"""Display-only stale verification and modified table-link regression checks."""

import copy
from pathlib import Path

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem

from adsfolio.config import Config
from adsfolio.gui.config import GuiConfig
from adsfolio.gui.detail_pane import DetailPane
from adsfolio.gui.library import COLUMNS, STALE_VERIFICATION_TOOLTIP, FileQuery
from adsfolio.gui.main_window import MainWindow
from adsfolio.gui.models import FileTableModel, ResolutionDelegate
from adsfolio.pdf.readiness import (
    METADATA_SHA256_KEY,
    METADATA_STALE_KEY,
    REFERENCE_METADATA_KEY,
    metadata_ready,
)


@pytest.mark.parametrize("mode", ["single", "double"])
@pytest.mark.parametrize("column_key", ["bibcode", "arxiv", "filename"])
@pytest.mark.parametrize(
    "modifier",
    [
        Qt.NoModifier,
        Qt.ShiftModifier,
        Qt.ControlModifier,
        Qt.AltModifier,
        Qt.MetaModifier,
    ],
)
def test_table_link_modifiers_select_without_opening(
    qtbot,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mode: str,
    column_key: str,
    modifier: Qt.KeyboardModifier,
) -> None:
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr("adsfolio.gui.main_window.load_config", lambda: GuiConfig())
    monkeypatch.setattr("adsfolio.gui.main_window.save_config", lambda config: None)
    window = MainWindow()
    qtbot.addWidget(window)
    window.current_root = tmp_path
    window.config.link_activation = mode
    window.model._rows = [
        {
            "current_path": "paper.pdf",
            "metadata": {
                "bibcode": "2026TEST",
                "arxiv": "2401.00001",
            },
        }
    ]
    column = next(
        i for i, definition in enumerate(window.columns) if definition.key == column_key
    )
    index = window.model.index(0, column)
    opened = []
    monkeypatch.setattr(window, "_open_external_url", opened.append)
    monkeypatch.setattr(window, "_open_filename", opened.append)
    monkeypatch.setattr(QApplication, "keyboardModifiers", lambda: modifier)
    if mode == "single":
        window._table_clicked(index)
    else:
        window._open_index(index)
    assert len(opened) == (1 if modifier == Qt.NoModifier else 0)


@pytest.mark.parametrize(
    "provenance,expected_stale",
    [
        ({}, False),
        ({"metadata": {METADATA_SHA256_KEY: "current"}}, False),
        ({"metadata": {METADATA_SHA256_KEY: "previous"}}, True),
        ({"metadata": {"sha256": "previous"}}, True),
        ({METADATA_SHA256_KEY: "previous"}, True),
        ({"metadata": {METADATA_STALE_KEY: True}}, True),
        ({METADATA_STALE_KEY: True}, True),
        ({"sha256": None, "metadata": {METADATA_SHA256_KEY: "current"}}, True),
    ],
)
def test_verification_displays_agree_without_changing_record(
    qtbot, tmp_path: Path, provenance: dict, expected_stale: bool
) -> None:
    record = {
        "current_path": "paper.pdf",
        "status": "resolved",
        "bibcode": "2026TEST",
        "sha256": "current",
        "metadata": {"title": ["Paper"], "author": ["Author"], "year": "2026"},
    }
    record["metadata"].update(provenance.get("metadata", {}))
    record.update(
        {key: value for key, value in provenance.items() if key != "metadata"}
    )
    before = copy.deepcopy(record)
    readiness = metadata_ready(record)
    model = FileTableModel(None, FileQuery())
    model._rows = [record]
    column = next(
        i for i, definition in enumerate(COLUMNS) if definition.key == "status"
    )
    index = model.index(0, column)
    expected_tooltip = (
        STALE_VERIFICATION_TOOLTIP if expected_stale else "ADS record linked"
    )
    assert index.data(Qt.ToolTipRole) == expected_tooltip
    assert index.data(Qt.AccessibleTextRole) == expected_tooltip
    image = QImage(24, 24, QImage.Format_ARGB32)
    image.fill(Qt.white)
    painter = QPainter(image)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 24, 24)
    ResolutionDelegate().paint(painter, option, index)
    painter.end()
    green = any(
        image.pixelColor(x, y) == QColor("#2e7d32")
        for x in range(24)
        for y in range(24)
    )
    amber = any(
        image.pixelColor(x, y) == QColor("#d97706")
        for x in range(24)
        for y in range(24)
    )
    assert green is not expected_stale
    assert amber is expected_stale
    pane = DetailPane()
    qtbot.addWidget(pane)
    pane.set_record(record, tmp_path)
    row = pane._row_widgets["status"]
    assert row.value.text() == (
        "PDF changed" if expected_stale else "ADS record linked"
    )
    assert expected_tooltip in row.value.toolTip()
    assert row.value._copy_text == "resolved"
    assert record == before
    assert metadata_ready(record) == readiness


def test_fresh_manual_metadata_keeps_readiness_marker(qtbot) -> None:
    record = {
        "status": "unresolved",
        "metadata": {
            "title": ["Paper"],
            "author": ["Author"],
            "year": "2026",
            REFERENCE_METADATA_KEY: True,
        },
    }
    assert metadata_ready(record)
    model = FileTableModel(None, FileQuery())
    model._rows = [record]
    column = next(
        i for i, definition in enumerate(COLUMNS) if definition.key == "status"
    )
    index = model.index(0, column)
    assert (
        index.data(Qt.ToolTipRole)
        == "Metadata ready for renaming. No completed ADS lookup."
    )
    image = QImage(24, 24, QImage.Format_ARGB32)
    image.fill(Qt.white)
    painter = QPainter(image)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 24, 24)
    ResolutionDelegate().paint(painter, option, index)
    painter.end()
    assert any(
        image.pixelColor(x, y) == QColor("#2e7d32")
        for x in range(24)
        for y in range(24)
    )


def test_stored_match_sidebar_and_counts_explain_current_verification(
    qtbot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from adsfolio.gui.library import LibraryView

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr("adsfolio.gui.main_window.load_config", lambda: GuiConfig())
    monkeypatch.setattr("adsfolio.gui.main_window.save_config", lambda config: None)
    window = MainWindow()
    qtbot.addWidget(window)
    window.current_root = tmp_path
    filters_seen = []

    class StoredStatusRegistry:
        """Model an unchanged stored match whose PDF now needs reverification."""

        def count_files_matching(self, **filters: object) -> int:
            filters_seen.append(filters)
            return 0 if filters.get("resolved") is False else 1

        def close(self) -> None:
            pass

    registry = StoredStatusRegistry()
    window.registries[str(tmp_path)] = registry
    window._populate_views(registry)
    matched = next(
        window.views_item.child(index)
        for index in range(window.views_item.childCount())
        if window.views_item.child(index).data(0, Qt.UserRole + 1) == "resolved"
    )
    assert matched.text(0) == "ADS linked (1)"
    assert "does not prove the PDF content matches" in matched.toolTip(0)
    assert "Changed PDFs need identification again" in matched.toolTip(0)
    assert LibraryView.UNRESOLVED.label == "Needs ADS lookup"
    assert FileQuery(view=LibraryView.RESOLVED).to_filters() == {"resolved": True}
    window._update_status()
    assert "1 ADS linked" in window.status.currentMessage()
    assert "0 need ADS lookup" in window.status.currentMessage()
    assert "does not prove the PDF content matches" in window.status.toolTip()
    assert {"resolved": True} in filters_seen
    assert {"resolved": False} in filters_seen
