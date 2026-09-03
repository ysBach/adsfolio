"""Tests for the optional Qt table model."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontMetrics

from adsapi.citations import CitationStyle
from adsapi.config import Config
from adsapi.gui.library import BASE_COLUMNS, COLUMNS, FileQuery, build_columns
from adsapi.gui.models import PAGE_SIZE, FileTableModel, LinkDelegate
from adsapi.pdf.registry import PdfRegistry


@pytest.fixture(autouse=True)
def _use_test_catalog(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    monkeypatch.setattr(
        Config,
        "get_config_dir",
        classmethod(lambda cls: config_dir),
    )


class _StaticRegistry:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self._records = records

    def count_files_matching(self, **filters: object) -> int:
        return len(self._records)

    def query_files(self, **filters: object) -> list[dict[str, Any]]:
        return list(self._records)


def test_gui_model_tests_use_temporary_catalog_config_dir(tmp_path) -> None:
    assert Config.get_config_dir().resolve() == (tmp_path / "config").resolve()


def _add_rows(registry: PdfRegistry, root, count: int) -> None:
    for index in range(count):
        registry.upsert_file(
            root=root,
            relative_path=f"{index:04d}.pdf",
            current_path=f"{index:04d}.pdf",
            sha256=f"sha-{index}",
            size_bytes=index,
            mtime_ns=index,
            status="resolved" if index % 2 == 0 else "unresolved",
            metadata={
                "bibcode": f"2024TEST{index:04d}",
                "title": [f"Title {index}"],
                "author": [f"Author {index}"],
            },
        )


def test_model_fetches_pages(qtbot, tmp_path) -> None:
    root = tmp_path / "library"
    registry = PdfRegistry(root)
    _add_rows(registry, root, PAGE_SIZE + 3)
    model = FileTableModel(registry, FileQuery())
    assert model.rowCount() == PAGE_SIZE
    assert model.canFetchMore()
    model.fetchMore()
    assert model.rowCount() == PAGE_SIZE + 3
    assert not model.canFetchMore()
    registry.close()


def test_model_search_requeries(qtbot, tmp_path) -> None:
    root = tmp_path / "library"
    registry = PdfRegistry(root)
    _add_rows(registry, root, 3)
    model = FileTableModel(registry, FileQuery(search="Title 1"))
    assert model.total == 1
    assert model.record_at(0)["bibcode"] == "2024TEST0001"
    registry.close()


def test_model_formats_added_timestamp_and_blanks_invalid_values(qtbot) -> None:
    model = FileTableModel(
        _StaticRegistry(
            [
                {"first_seen_at": "2026-08-29T12:34:56+00:00"},
                {"first_seen_at": None},
                {"first_seen_at": "not-a-timestamp"},
            ]
        ),
        FileQuery(),
    )
    model.set_timestamp_format("%Y-%m-%d")
    added_index = next(
        index for index, column in enumerate(COLUMNS) if column.key == "first_seen_at"
    )

    assert model.index(0, added_index).data(Qt.DisplayRole) == "2026-08-29"
    assert model.index(1, added_index).data(Qt.DisplayRole) == ""
    assert model.index(2, added_index).data(Qt.DisplayRole) == ""


def test_resolution_marker_has_accessible_state(qtbot, tmp_path) -> None:
    """Resolution state remains readable when the visual marker has no text."""
    root = tmp_path / "library"
    registry = PdfRegistry(root)
    registry.upsert_file(
        root=root,
        relative_path="resolved.pdf",
        current_path="resolved.pdf",
        sha256="a",
        size_bytes=1,
        mtime_ns=1,
        status="resolved",
        metadata={"bibcode": "2024TEST...1A"},
    )
    registry.upsert_file(
        root=root,
        relative_path="needs-attention.pdf",
        current_path="needs-attention.pdf",
        sha256="b",
        size_bytes=1,
        mtime_ns=1,
        status="unresolved",
        metadata={"bibcode": "2024TEST...1B"},
    )
    model = FileTableModel(registry, FileQuery())
    status_index = next(
        index for index, column in enumerate(COLUMNS) if column.key == "status"
    )

    resolved = model.index(1, status_index)
    attention = model.index(0, status_index)
    assert resolved.data(Qt.DisplayRole) == ""
    assert resolved.data(Qt.ToolTipRole) == "Resolved"
    assert resolved.data(Qt.AccessibleTextRole) == "Resolved"
    assert attention.data(Qt.DisplayRole) == ""
    assert attention.data(Qt.ToolTipRole) == "Needs attention"
    assert attention.data(Qt.AccessibleTextRole) == "Needs attention"
    registry.close()


def test_link_delegate_switches_to_icon_when_text_does_not_fit() -> None:
    delegate = LinkDelegate("ADS")
    font = QFont()
    text_width = QFontMetrics(font).horizontalAdvance("2024A&A...1A...1B")

    assert not delegate.requires_icon("2024A&A...1A...1B", text_width + 20, font)
    assert delegate.requires_icon("2024A&A...1A...1B", 20, font)


def test_file_table_model_columns_and_set_columns(qtbot) -> None:
    model = FileTableModel(_StaticRegistry([]), FileQuery())
    assert model.columns == BASE_COLUMNS
    assert model.columnCount() == len(BASE_COLUMNS)
    assert model.query == FileQuery()

    style = CitationStyle(id="alpha", name="Alpha", template="{year}")
    new_cols = build_columns([style])

    with qtbot.waitSignal(model.modelReset):
        model.set_columns(new_cols)

    assert model.columns == new_cols
    assert model.columnCount() == len(new_cols)


def test_generated_citation_column_is_displayed_but_not_sortable(qtbot) -> None:
    style = CitationStyle(id="alpha", name="Compact", template="{year}: {title}")
    columns = build_columns([style])
    record = {
        "status": "resolved",
        "bibcode": "2024TEST...1A",
        "arxiv": "2401.12345",
        "metadata": {
            "year": ["2024"],
            "title": ["A title"],
            "bibcode": ["2024TEST...1A"],
        },
    }
    registry = _StaticRegistry([record])
    model = FileTableModel(registry, FileQuery(), columns=columns)
    citation = next(i for i, item in enumerate(columns) if item.key == "citation:alpha")

    # DisplayRole, ToolTipRole, AccessibleTextRole return complete rendered text
    assert model.index(0, citation).data(Qt.DisplayRole) == "2024: A title"
    assert model.index(0, citation).data(Qt.ToolTipRole) == "2024: A title"
    assert model.index(0, citation).data(Qt.AccessibleTextRole) == "2024: A title"
    assert model.headerData(citation, Qt.Horizontal, Qt.DisplayRole) == "Compact"

    # Base Bibcode / arXiv roles remain unchanged
    bibcode_idx = next(i for i, item in enumerate(columns) if item.key == "bibcode")
    arxiv_idx = next(i for i, item in enumerate(columns) if item.key == "arxiv")
    assert model.index(0, bibcode_idx).data(Qt.DisplayRole) == "2024TEST...1A"
    assert (
        model.index(0, bibcode_idx).data(Qt.ToolTipRole) == "Open in ADS: 2024TEST...1A"
    )
    assert model.index(0, bibcode_idx).data(Qt.AccessibleTextRole) == "2024TEST...1A"
    assert model.index(0, arxiv_idx).data(Qt.DisplayRole) == "2401.12345"
    assert model.index(0, arxiv_idx).data(Qt.ToolTipRole) == "Open in arXiv: 2401.12345"
    assert model.index(0, arxiv_idx).data(Qt.AccessibleTextRole) == "2401.12345"

    before = model.query
    model.sort(citation, Qt.DescendingOrder)
    assert model.query == before
