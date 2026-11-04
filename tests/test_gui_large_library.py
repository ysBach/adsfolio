"""Keep large-library views fast without changing their results.

The tests also check search matching, highlights, completion, and Find bar options.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QItemSelectionModel, Qt

import adsfolio.gui.main_window as main_window_module
from adsfolio.config import Config
from adsfolio.gui.combined_library import CombinedLibrary
from adsfolio.gui.config import GuiConfig, RootEntry, save_config
from adsfolio.gui.library import FileQuery, LibraryView, count_matching
from adsfolio.gui.main_window import MainWindow
from adsfolio.gui.models import FileTableModel
from adsfolio.pdf.catalog import PdfCatalog
from adsfolio.pdf.registry import PdfRegistry

FILES_PER_ROOT = 150


def _fill_root(root, prefix: str) -> None:
    """Catalog fictional PDFs with mixed groups, sources, and duplicate digests."""
    registry = PdfRegistry(root)
    for index in range(FILES_PER_ROOT):
        name = f"{prefix}-{index:03d}.pdf"
        path = root / name
        path.write_bytes(f"fictional {prefix} {index}".encode())
        resolved = index % 3 == 0
        metadata = {
            "title": [f"Fictional Paper {index % 7}"],
            "year": str(2000 + index % 5),
            "author": [f"Author{index % 4}, A."],
        }
        if resolved:
            metadata["bibcode"] = f"2000Fict..{index:03d}A"
        if index % 5 == 0:
            metadata["source"] = "arxiv"
        registry.upsert_file(
            root=root,
            relative_path=name,
            current_path=name,
            sha256=f"digest-{index % 40}",
            size_bytes=path.stat().st_size,
            mtime_ns=path.stat().st_mtime_ns,
            status="resolved" if resolved else "unresolved",
            metadata=metadata,
        )
    registry.close()


@pytest.fixture
def large_window(qtbot, monkeypatch, tmp_path):
    config_dir = tmp_path / "config"
    monkeypatch.setattr(MainWindow, "_queue_startup_ads_rechecks", lambda self: None)
    monkeypatch.setattr(MainWindow, "_queue_added_ads_recheck", lambda self, root: None)
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: config_dir))
    monkeypatch.setattr(Config, "token_source", classmethod(lambda cls: None))
    roots = [tmp_path / "first", tmp_path / "second"]
    for index, root in enumerate(roots):
        root.mkdir()
        _fill_root(root, f"lib{index}")
    save_config(
        GuiConfig(
            roots=[
                RootEntry(str(root), label=f"Library {index}")
                for index, root in enumerate(roots)
            ],
            check_updates_automatically=False,
            citation_styles=[],
        )
    )
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    yield window, [root.resolve() for root in roots]
    window.close()


def _count_parses(monkeypatch):
    """Count full catalog loads, which parse every record's metadata JSON."""
    calls = []
    original = PdfCatalog.query_files

    def counted(self, *args, **kwargs):
        calls.append(kwargs.get("root"))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(PdfCatalog, "query_files", counted)
    return calls


def test_view_counts_match_registry_counts(large_window):
    window, roots = large_window
    registries = [window.registries[str(root)] for root in roots]
    combined = CombinedLibrary({str(r.directory): r for r in registries})
    for source in (*registries, combined):
        facts = source.record_facts()
        for search in (None, "Paper 3", "AUTHOR1", "lib1-01"):
            keys = source.search_location_keys(search) if search else None
            for view in LibraryView:
                filters = FileQuery(view=view, search=search or "").to_filters()
                assert count_matching(facts, view, search_keys=keys) == (
                    source.count_files_matching(**filters)
                )
                for group in (
                    "ads_linked",
                    "identified_without_ads",
                    "needs_identification",
                ):
                    expected = source.count_files_matching(
                        **{**filters, "identification": group}
                    )
                    effective = (
                        view
                        if view in {LibraryView.ARXIV_ONLY, LibraryView.DUPLICATES}
                        else LibraryView.ALL
                    )
                    assert (
                        count_matching(facts, effective, group=group, search_keys=keys)
                        == expected
                    )


def test_status_and_view_counts_reuse_one_catalog_load(large_window, monkeypatch):
    window, _roots = large_window
    calls = _count_parses(monkeypatch)
    registry = window.registries[str(window.current_root)]
    window._populate_views(registry)
    for _ in range(5):
        window._update_status()
    assert calls == []
    window.search.setText("Paper 2")
    window._search_changed()
    window.table.sortByColumn(0, Qt.DescendingOrder)
    assert calls == []
    assert "need identification" in window.status.currentMessage()


def test_cached_model_snapshot_equals_a_new_query(large_window):
    window, roots = large_window
    registry = window.registries[str(roots[0])]
    combined = CombinedLibrary(
        {str(root): window.registries[str(root)] for root in roots},
        labels={str(roots[0]): "A", str(roots[1]): "B"},
    )
    for source in (registry, combined):
        model = FileTableModel(source, FileQuery())
        for view in LibraryView:
            for search in ("", "paper 1", "%Paper_3%", "AUTHOR2"):
                for order_by, secondary in (
                    ("year", "title"),
                    ("title", None),
                    ("filename", "year"),
                    ("author", "year"),
                ):
                    for descending in (False, True):
                        query = FileQuery(
                            view=view,
                            search=search,
                            order_by=order_by,
                            descending=descending,
                            secondary_order_by=secondary,
                        )
                        model.set_query(query)
                        expected = source.query_files(
                            **query.to_filters(),
                            order_by=order_by,
                            descending=descending,
                            secondary_order_by=secondary,
                        )
                        assert model._snapshot == expected


def test_search_ignores_accents_and_case_unless_exact(tmp_path, monkeypatch):
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    root = tmp_path / "library"
    root.mkdir()
    registry = PdfRegistry(root)
    titles = ["Dorémont-Roe comet", "DOREMONT survey", "plain ascii", "100% sure"]
    for index, title in enumerate(titles):
        path = root / f"p_{index}.pdf"
        path.write_bytes(f"fictional {index}".encode())
        registry.upsert_file(
            root=root,
            relative_path=path.name,
            current_path=path.name,
            sha256=f"d{index}",
            size_bytes=path.stat().st_size,
            mtime_ns=path.stat().st_mtime_ns,
            status="unresolved",
            metadata={"title": [title]},
        )
    ids = {
        record["metadata"]["title"][0]: record["location_id"]
        for record in registry.query_files()
    }
    # Stored JSON can escape non-ASCII text. Search reads the decoded value.
    registry.catalog._connection.execute(
        "UPDATE attachments SET metadata_json = ? WHERE metadata_json LIKE ?",
        ('{"title": ["Bockel\\u00e9e-Morvan comet"]}', "%Morvan%"),
    )
    registry.catalog._connection.commit()

    def found(search, **options):
        sql = registry.catalog.query_location_ids(
            root=root,
            search=search,
            search_exact=options.get("exact", False),
            search_wildcards=options.get("wildcards", False),
        )
        memory = {key[1] for key in registry.search_location_keys(search, **options)}
        assert sql == memory
        return {title for title, location in ids.items() if location in memory}

    both = {"Dorémont-Roe comet", "DOREMONT survey"}
    assert found("doremont") == both
    assert found("DORÉMONT") == both
    assert found("Dorémont", exact=True) == {"Dorémont-Roe comet"}
    assert found("doremont", exact=True) == set()
    assert found("100%") == {"100% sure"}
    assert found("p_1") == {"DOREMONT survey"}
    assert found("p%1", wildcards=True) == {"DOREMONT survey"}
    assert found("p%1") == set()
    # A key name alone does not match; only values are searched.
    assert found("title") == set()
    assert len(found("  ")) == len(titles)
    registry.close()


def test_leaving_combined_view_with_everything_selected_is_bounded(
    large_window, monkeypatch
):
    window, _roots = large_window
    window.combined.set_enabled(True)
    tree = window.combined.tree_model
    while tree.is_loading:
        window.combined.tree_model._load_next_page()
    window.table.selectAll()
    selected = len(window._selected_view_rows())
    assert selected >= 2 * FILES_PER_ROOT
    syncs = []
    original = MainWindow._sync_detail_and_selection_actions

    def counted(self):
        syncs.append(1)
        return original(self)

    monkeypatch.setattr(MainWindow, "_sync_detail_and_selection_actions", counted)
    window.combined.set_enabled(False)
    assert not window.combined.enabled
    assert len(syncs) <= 3


def test_one_click_builds_the_detail_rows_once(large_window, monkeypatch):
    window, _roots = large_window
    builds = []
    original = type(window.detail).set_record

    def counted(self, *args, **kwargs):
        builds.append(args[0])
        return original(self, *args, **kwargs)

    monkeypatch.setattr(type(window.detail), "set_record", counted)
    index = window.table.model().index(4, 0)
    window.table.selectionModel().setCurrentIndex(
        index, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows
    )
    assert len([record for record in builds if record is not None]) == 1


def test_combined_selection_from_one_library_can_go_to_an_ads_library(
    large_window, monkeypatch
):
    window, roots = large_window
    opened = []

    class FakeDialog:
        def __init__(self, root, selected, all_records, parent=None):
            opened.append((root, selected, all_records))
            self.finished = type("S", (), {"connect": lambda self, slot: None})()

        def show(self):
            pass

    monkeypatch.setattr(main_window_module, "AdsLibraryDialog", FakeDialog)
    window.combined.set_enabled(True)
    tree = window.combined.tree_model
    while tree.is_loading:
        tree._load_next_page()
    selection = window.table.selectionModel()

    def select(root, count):
        selection.clearSelection()
        rows = [
            row
            for row in range(window.model.rowCount())
            if window.model.record_at(row)["root"] == str(root)
        ][:count]
        for row in rows:
            selection.select(
                window._view_table_index(window.model.index(row, 0)),
                QItemSelectionModel.Select | QItemSelectionModel.Rows,
            )

    select(roots[1], 3)
    assert window.add_to_ads_library_action.isEnabled()
    window._add_to_ads_library()
    root, selected, all_records = opened[-1]
    assert root == roots[1]
    assert len(selected) == 3
    assert {record["root"] for record in selected} == {str(roots[1])}
    assert all_records
    window._ads_library_dialog = None

    select(roots[0], 2)
    rows_in_second = [
        window._view_table_index(window.model.index(row, 0))
        for row in range(window.model.rowCount())
        if window.model.record_at(row)["root"] == str(roots[1])
    ][:1]
    selection.select(
        rows_in_second[0], QItemSelectionModel.Select | QItemSelectionModel.Rows
    )
    window._sync_ads_library_action()
    assert not window.add_to_ads_library_action.isEnabled()
    assert "one available library" in window.add_to_ads_library_action.toolTip()
    assert window.export_selected_action.isEnabled()


def test_find_bar_options_change_matching_and_window_settings(large_window):
    window, _roots = large_window
    window.search.setText("fictional paper 3")
    window._search_changed()
    assert window.model.total > 0
    window.search_exact_action.setChecked(True)
    assert window.config.search_exact
    assert window.model.total == 0
    window.search.setText("Fictional Paper 3")
    window._search_changed()
    assert window.model.total > 0
    window.search_exact_action.setChecked(False)
    window.search.setText("lib0-00_")
    window._search_changed()
    assert window.model.total == 0
    window.search_wildcards_action.setChecked(True)
    assert window.config.search_wildcards
    assert window.model.total == 10


def test_field_terms_all_match_and_plain_terms_use_default_fields(large_window):
    window, roots = large_window
    registry = window.registries[str(roots[0])]

    def found(query, fields=()):
        sql = registry.catalog.query_location_ids(
            root=roots[0], search=query, search_fields=fields
        )
        memory = {key[1] for key in registry.search_location_keys(query, fields=fields)}
        assert sql == memory
        return memory

    authors1 = found("author:author1")
    year2001 = found("year:2001")
    assert authors1 and year2001
    assert found("author:author1 year:2001") == authors1 & year2001
    # Plain terms search all fields by default, so the filename matches.
    assert found("lib0-001") == found("filename:lib0-001")
    assert found("lib0-001", fields=("title",)) == set()
    assert found("paper", fields=("title",)) == found("title:paper")
    # A quoted phrase must appear as one piece.
    assert found('"Fictional Paper 3"') == found('title:"fictional paper 3"')
    assert found("first_author:Author1") == authors1


def test_highlight_marks_terms_only_in_matching_columns():
    from adsfolio.gui.models import SearchHighlight

    highlight = SearchHighlight()
    highlight.query = 'author:doremont "comet nucleus"'
    assert highlight.spans("author", "Dorémont-Roe, D.") == [(0, 8)]
    assert highlight.spans("title", "Dorémont comet nucleus") == [(9, 22)]
    assert highlight.spans("filename", "comet nucleus.pdf") == [(0, 13)]
    assert highlight.spans("year", "2019") == []
    highlight.default_fields = ("title",)
    assert highlight.spans("filename", "comet nucleus.pdf") == []
    highlight.enabled = False
    assert highlight.spans("title", "comet nucleus") == []


def test_completer_suggests_field_names_and_values(qtbot):
    from PySide6.QtWidgets import QLineEdit

    from adsfolio.gui.search_completion import SearchCompleter

    line = QLineEdit()
    qtbot.addWidget(line)
    completer = SearchCompleter(
        line,
        lambda field: ["Doe, J.", "Dorémont-Roe, D."] if field == "author" else [],
    )
    line.setText("comet f")
    line.textEdited.emit(line.text())
    model = completer.completer.completionModel()
    suggestions = {model.index(row, 0).data() for row in range(model.rowCount())}
    assert suggestions == {"first_author:", "filename:"}
    line.setText("comet author:d")
    line.textEdited.emit(line.text())
    model = completer.completer.completionModel()
    assert model.rowCount() == 2
    completer._insert("Doe, J.")
    assert line.text() == 'comet author:"Doe, J." '


def test_find_bar_uses_icon_toggles_and_a_fields_menu(large_window):
    window, _roots = large_window
    for action in (
        window.search_highlight_action,
        window.search_exact_action,
        window.search_wildcards_action,
    ):
        assert action.isCheckable()
        assert not action.icon().isNull()
        assert action.toolTip()
    assert window.search_highlight_action.isChecked()
    window.search_highlight_action.setChecked(False)
    assert window.config.search_highlight is False
    assert window.search_highlight.enabled is False
    window.search_field_actions["title"].setChecked(True)
    window._search_fields_chosen("title")
    assert window.config.search_fields == ["title"]
    assert window.current_query.search_fields == ("title",)
    assert not window.search_all_fields_action.isChecked()
    window._search_fields_chosen(None)
    assert window.config.search_fields == []
    assert window.search_all_fields_action.isChecked()
