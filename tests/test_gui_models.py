"""Check the Qt file table model: paging, queries, cell roles, and lock errors."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontMetrics

from adsfolio.citations import CitationStyle
from adsfolio.config import Config
from adsfolio.gui.library import BASE_COLUMNS, COLUMNS, FileQuery, build_columns
from adsfolio.gui.models import PAGE_SIZE, FileTableModel, LinkDelegate
from adsfolio.pdf.catalog import PdfCatalog
from adsfolio.pdf.registry import PdfRegistry


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


def test_catalog_page_bounds_hydration_and_records_are_complete(
    qtbot, tmp_path, monkeypatch
) -> None:
    root = tmp_path / "library"
    registry = PdfRegistry(root)
    _add_rows(registry, root, PAGE_SIZE + 7)
    hydrated = 0
    original = PdfCatalog._record_from_row

    def counted(row):
        nonlocal hydrated
        hydrated += 1
        return original(row)

    monkeypatch.setattr(PdfCatalog, "_record_from_row", staticmethod(counted))
    model = FileTableModel(
        registry, FileQuery(order_by="relative_path", descending=False)
    )
    assert hydrated == PAGE_SIZE
    assert model.rowCount() == PAGE_SIZE
    assert len(model.records) == PAGE_SIZE + 7
    assert hydrated == PAGE_SIZE + 7
    assert model.records[-1]["relative_path"] == f"{PAGE_SIZE + 6:04d}.pdf"
    registry.close()


def test_closed_catalog_page_cannot_return_partial_records(qtbot, tmp_path) -> None:
    root = tmp_path / "library"
    registry = PdfRegistry(root)
    _add_rows(registry, root, PAGE_SIZE + 2)
    model = FileTableModel(
        registry, FileQuery(order_by="relative_path", descending=False)
    )
    snapshot = model._page_snapshot
    assert snapshot is not None
    assert not snapshot._connection.in_transaction
    model.close()
    model.close()
    assert snapshot._connection is None
    with pytest.raises(RuntimeError, match="closed before all records"):
        _ = model.records
    registry.close()


def test_first_catalog_page_must_match_frozen_total(qtbot, tmp_path, monkeypatch):
    class ShortSnapshot:
        total = PAGE_SIZE + 1

        def __init__(self):
            self.closed = False

        def fetch(self, _offset, _limit):
            return [{}]

        def close(self):
            self.closed = True

    root = tmp_path / "library"
    registry = PdfRegistry(root)
    snapshot = ShortSnapshot()
    monkeypatch.setattr(registry, "open_page_snapshot", lambda **_kwargs: snapshot)
    model = FileTableModel(None, FileQuery(order_by="relative_path", descending=False))
    with qtbot.waitSignal(model.query_failed) as emitted:
        model.set_registry(registry)
    assert "before all rows loaded" in emitted.args[0]
    assert snapshot.closed
    assert model.rowCount() == 0
    assert not model.canFetchMore()
    with pytest.raises(RuntimeError, match="closed before all records"):
        _ = model.records
    registry.close()


def test_temp_table_creation_falls_back_but_lock_does_not_retry(
    qtbot, tmp_path, monkeypatch
):
    from adsfolio.pdf.catalog_page import TempSnapshotUnavailable

    root = tmp_path / "library"
    registry = PdfRegistry(root)
    _add_rows(registry, root, 3)
    expected = registry.query_files(order_by="relative_path", descending=False)
    monkeypatch.setattr(
        registry,
        "open_page_snapshot",
        lambda **_kwargs: (_ for _ in ()).throw(TempSnapshotUnavailable("TEMP full")),
    )
    model = FileTableModel(
        registry, FileQuery(order_by="relative_path", descending=False)
    )
    assert list(model.records) == expected

    calls = 0
    original_query = registry.query_records

    def counted_query(**filters):
        nonlocal calls
        calls += 1
        return original_query(**filters)

    monkeypatch.setattr(registry, "query_records", counted_query)
    monkeypatch.setattr(
        registry,
        "open_page_snapshot",
        lambda **_kwargs: (_ for _ in ()).throw(
            sqlite3.OperationalError("database is locked")
        ),
    )
    with qtbot.waitSignal(model.query_failed):
        model.set_query(FileQuery(order_by="mtime_ns"))
    assert calls == 0
    assert not model.canFetchMore()
    registry.close()


def test_page_read_sqlite_error_stops_all_fetch_loops(qtbot, tmp_path):
    root = tmp_path / "library"
    registry = PdfRegistry(root)
    _add_rows(registry, root, PAGE_SIZE + 2)
    model = FileTableModel(
        registry, FileQuery(order_by="relative_path", descending=False)
    )
    snapshot = model._page_snapshot
    assert snapshot is not None

    def fail_read(_offset, _limit):
        raise sqlite3.DatabaseError("synthetic read failure")

    snapshot.fetch = fail_read
    with qtbot.waitSignal(model.query_failed) as emitted:
        model.fetchMore()
    assert "synthetic read failure" in emitted.args[0]
    assert not model.canFetchMore()
    attempts = 0
    while model.canFetchMore():
        attempts += 1
        model.fetchMore()
    assert attempts == 0
    with pytest.raises(RuntimeError, match="closed before all records"):
        _ = model.records
    registry.close()


@pytest.mark.parametrize("short_count", [0, 1])
def test_short_catalog_page_stops_fetch_and_rejects_incomplete_records(
    qtbot, tmp_path, short_count
) -> None:
    root = tmp_path / "library"
    registry = PdfRegistry(root)
    _add_rows(registry, root, PAGE_SIZE + 2)
    model = FileTableModel(
        registry, FileQuery(order_by="relative_path", descending=False)
    )
    snapshot = model._page_snapshot
    assert snapshot is not None
    snapshot.fetch = lambda _offset, _limit: [{} for _ in range(short_count)]
    with qtbot.waitSignal(model.query_failed) as emitted:
        model.fetchMore()
    assert "before all rows loaded" in emitted.args[0]
    assert model.rowCount() == PAGE_SIZE
    assert not model.canFetchMore()
    with pytest.raises(RuntimeError, match="closed before all records"):
        _ = model.records
    registry.close()


def test_catalog_page_is_frozen_across_external_write_and_reloads_cleanly(
    qtbot, tmp_path
) -> None:
    root = tmp_path / "library"
    registry = PdfRegistry(root)
    _add_rows(registry, root, PAGE_SIZE + 3)
    model = FileTableModel(
        registry, FileQuery(order_by="relative_path", descending=False)
    )
    snapshot = model._page_snapshot
    assert snapshot is not None
    assert model._snapshot[0]["metadata"]["title"] == ["Title 0"]

    writer = sqlite3.connect(registry.path)
    try:
        writer.execute(
            "UPDATE attachments SET metadata_json = ? WHERE id = ?",
            (json.dumps({"title": ["Changed"]}), model._snapshot[0]["attachment_id"]),
        )
        writer.commit()
    finally:
        writer.close()

    model.fetchMore()
    assert model.record_at(0)["metadata"]["title"] == ["Title 0"]
    with qtbot.waitSignal(model.modelReset):
        model.set_query(FileQuery(order_by="relative_path", descending=False))
    assert snapshot._connection is None
    assert model._page_snapshot is not None
    assert model.record_at(0)["metadata"]["title"] == ["Changed"]
    model._close_page_snapshot()
    registry.close()


@pytest.mark.parametrize(
    "query",
    [
        FileQuery(order_by="first_seen_at", descending=True),
        FileQuery(
            order_by="size_bytes", descending=True, secondary_order_by="mtime_ns"
        ),
        FileQuery(order_by="mtime_ns", secondary_order_by="relative_path"),
    ],
)
def test_catalog_page_sort_matches_registry_tie_breaks(qtbot, tmp_path, query) -> None:
    root = tmp_path / "library"
    registry = PdfRegistry(root)
    _add_rows(registry, root, 24)
    expected = registry.query_files(
        **query.to_filters(),
        order_by=query.order_by,
        descending=query.descending,
        secondary_order_by=query.secondary_order_by,
        tertiary_order_by=query.tertiary_order_by,
    )
    model = FileTableModel(registry, query)
    assert list(model.records) == expected
    model._close_page_snapshot()
    registry.close()


def test_catalog_page_preserves_identity_values_and_legacy_sort_fallback(
    qtbot, tmp_path
) -> None:
    root = tmp_path / "library"
    registry = PdfRegistry(root)
    _add_rows(registry, root, 8)
    legacy_values = [True, False, 0, [], {}, ["2025-01-01"], [None], ""]
    rows = registry.catalog._connection.execute(
        "SELECT id, attachment_id FROM file_locations ORDER BY relative_path"
    ).fetchall()
    for index, row in enumerate(rows):
        registry.catalog._connection.execute(
            "UPDATE file_locations SET device = ?, inode = ?, first_seen_at = '' "
            "WHERE id = ?",
            (
                7 if index == 0 else 0x123456789 + index,
                11 if index == 0 else 0x23456789A + index,
                row[0],
            ),
        )
        registry.catalog._connection.execute(
            "UPDATE attachments SET metadata_json = ? WHERE id = ?",
            (
                json.dumps(
                    {
                        "title": [f"Title {index}"],
                        "first_seen_at": legacy_values[index],
                    }
                ),
                row[1],
            ),
        )
    registry.catalog._connection.commit()
    query = FileQuery(order_by="first_seen_at", descending=False)
    expected = registry.query_files(
        **query.to_filters(), order_by=query.order_by, descending=query.descending
    )
    model = FileTableModel(registry, query)
    actual = list(model.records)
    assert actual == expected
    assert [(record["device"], record["inode"]) for record in actual] == [
        (record["device"], record["inode"]) for record in expected
    ]
    first_path = next(
        record for record in actual if record["relative_path"] == "0000.pdf"
    )
    assert first_path["device"] == 7
    assert first_path["inode"] == 11
    assert max(record["device"] for record in actual) > 2**32
    model.close()
    registry.close()


def test_records_sqlite_error_closes_snapshot_and_reraises(qtbot, tmp_path) -> None:
    root = tmp_path / "library"
    registry = PdfRegistry(root)
    _add_rows(registry, root, PAGE_SIZE + 2)
    model = FileTableModel(
        registry, FileQuery(order_by="relative_path", descending=False)
    )
    snapshot = model._page_snapshot
    assert snapshot is not None
    snapshot.fetch = lambda _offset, _limit: (_ for _ in ()).throw(
        sqlite3.DatabaseError("synthetic records read failure")
    )
    messages: list[str] = []
    model.query_failed.connect(messages.append)
    with pytest.raises(sqlite3.DatabaseError, match="synthetic records read failure"):
        _ = model.records
    assert messages and "synthetic records read failure" in messages[0]
    assert snapshot._connection is None
    assert not model.canFetchMore()
    with pytest.raises(RuntimeError, match="closed before all records"):
        _ = model.records
    registry.close()


def test_catalog_page_uses_registry_connection_with_pending_write(
    qtbot, tmp_path
) -> None:
    root = tmp_path / "library"
    registry = PdfRegistry(root)
    _add_rows(registry, root, 2)
    registry.catalog._connection.execute(
        "UPDATE attachments SET metadata_json = ? WHERE id = 1",
        (json.dumps({"title": ["Uncommitted"]}),),
    )
    assert registry.catalog._connection.in_transaction
    model = FileTableModel(
        registry, FileQuery(order_by="relative_path", descending=False)
    )
    assert model._page_snapshot is None
    assert any(
        record["metadata"].get("title") == ["Uncommitted"] for record in model.records
    )
    registry.catalog._connection.rollback()
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


class _CountingRegistry:
    """Static registry that records every query and can raise a sqlite error."""

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records
        self.calls: list[dict[str, Any]] = []
        self.error: sqlite3.OperationalError | None = None

    def count_files_matching(self, **filters: object) -> int:
        raise AssertionError("the model must not run a separate count query")

    def query_files(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return list(self.records)


def _numbered(count: int) -> list[dict[str, Any]]:
    return [
        {"relative_path": f"{i:04d}.pdf", "status": "resolved"} for i in range(count)
    ]


def test_one_query_per_reset_and_none_for_fetch_more(qtbot) -> None:
    registry = _CountingRegistry(_numbered(2 * PAGE_SIZE + 5))
    model = FileTableModel(registry, FileQuery())
    assert len(registry.calls) == 1
    assert model.total == 2 * PAGE_SIZE + 5
    assert model.rowCount() == PAGE_SIZE

    inserted: list[tuple[int, int]] = []
    model.rowsInserted.connect(lambda _p, first, last: inserted.append((first, last)))
    while model.canFetchMore():
        model.fetchMore()

    assert len(registry.calls) == 1
    assert inserted == [
        (PAGE_SIZE, 2 * PAGE_SIZE - 1),
        (2 * PAGE_SIZE, 2 * PAGE_SIZE + 4),
    ]
    assert model.rowCount() == model.total
    assert (
        model.record_at(2 * PAGE_SIZE + 4)["relative_path"]
        == f"{2 * PAGE_SIZE + 4:04d}.pdf"
    )

    model.fetchMore()  # nothing left: no signal and no query
    assert len(inserted) == 2
    assert len(registry.calls) == 1


def test_reset_sees_new_data_and_discards_loaded_pages(qtbot) -> None:
    registry = _CountingRegistry(_numbered(PAGE_SIZE + 1))
    model = FileTableModel(registry, FileQuery())
    model.fetchMore()
    assert model.rowCount() == PAGE_SIZE + 1

    registry.records = _numbered(3)
    model.set_query(FileQuery(search="x"))
    assert len(registry.calls) == 2
    assert (model.total, model.rowCount()) == (3, 3)

    registry.records = _numbered(4)
    model.set_registry(registry)
    assert len(registry.calls) == 3
    assert (model.total, model.rowCount()) == (4, 4)

    registry.records = _numbered(5)
    model._reload()
    assert len(registry.calls) == 4
    assert (model.total, model.rowCount()) == (5, 5)


def test_locked_query_clears_previous_rows_and_reports(qtbot) -> None:
    registry = _CountingRegistry(_numbered(3))
    model = FileTableModel(registry, FileQuery())
    messages: list[str] = []
    model.query_failed.connect(messages.append)

    registry.error = sqlite3.OperationalError("database is locked")
    with qtbot.waitSignal(model.modelReset):
        model.set_query(FileQuery(search="x"))
    assert messages == ["Library is busy; try again in a moment."]
    assert (model.total, model.rowCount()) == (0, 0)
    assert not model.canFetchMore()

    registry.error = None
    model.set_query(FileQuery())
    assert model.rowCount() == 3
    registry.error = sqlite3.OperationalError("database is locked")
    other = _CountingRegistry(_numbered(7))
    other.error = registry.error
    model.set_registry(other)
    assert (model.total, model.rowCount()) == (0, 0)
    assert model.record_at(0) is None


def test_other_sqlite_errors_propagate_and_reset_is_closed(qtbot) -> None:
    registry = _CountingRegistry(_numbered(3))
    model = FileTableModel(registry, FileQuery())
    registry.error = sqlite3.OperationalError("no such table: files")
    resets: list[str] = []
    model.modelReset.connect(lambda: resets.append("end"))
    with pytest.raises(sqlite3.OperationalError):
        model.set_query(FileQuery(search="x"))
    assert resets == ["end"]
    assert model.rowCount() == 0


def test_set_registry_with_query_resets_once(qtbot) -> None:
    first = _CountingRegistry(_numbered(2))
    second = _CountingRegistry(_numbered(5))
    model = FileTableModel(first, FileQuery())
    resets: list[int] = []
    model.modelReset.connect(lambda: resets.append(1))

    model.set_registry(second, FileQuery(search="x"))

    assert resets == [1]
    assert len(first.calls) == 1 and len(second.calls) == 1
    assert model.query == FileQuery(search="x")
    assert model.total == 5

    model.set_registry(first)  # omitted query keeps the current one
    assert model.query == FileQuery(search="x")


def test_status_explains_changed_pdf_without_losing_citation(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    registry = PdfRegistry(root)
    registry.upsert_file(
        root=root,
        relative_path="paper.pdf",
        current_path="paper.pdf",
        sha256="current",
        size_bytes=1,
        mtime_ns=1,
        status="unresolved",
        metadata={
            "bibcode": "2017MNRAS.469S.443B",
            "title": ["A known paper"],
            "author": ["Author, A."],
            "year": "2017",
            "_adsfolio_metadata_sha256": "previous",
        },
    )
    model = FileTableModel(registry, FileQuery())
    column = next(i for i, c in enumerate(COLUMNS) if c.key == "status")
    assert model.index(0, column).data(Qt.ToolTipRole) == (
        "PDF changed since identification. Run Identify to verify it again."
    )
    assert model.record_at(0)["title"] == ["A known paper"]
    registry.close()
