"""Exercise ADS-first views with isolated catalogs and unchanged PDFs."""

from __future__ import annotations

import copy

import pytest
from PySide6.QtCore import Qt

from adsfolio.config import Config
from adsfolio.gui.combined_library import CombinedLibrary
from adsfolio.gui.config import GuiConfig
from adsfolio.gui.detail_pane import DetailPane
from adsfolio.gui.library import COLUMNS, FileQuery, LibraryView
from adsfolio.gui.main_window import MainWindow
from adsfolio.gui.models import FileTableModel
from adsfolio.pdf.identity import IDENTITY_KEY, target_identity
from adsfolio.pdf.readiness import EXTERNAL_METADATA_KEY, METADATA_SHA256_KEY
from adsfolio.pdf.registry import PdfRegistry


def _add_record(registry, root, name, *, external=False, ads=False):
    metadata = {
        "title": [name],
        "author": ["Roe, A."],
        "year": "2024",
    }
    if ads:
        metadata["bibcode"] = "2024Example"
    if external:
        metadata["doi"] = ["10.5555/external"]
        metadata[EXTERNAL_METADATA_KEY] = True
        metadata[METADATA_SHA256_KEY] = name
        metadata[IDENTITY_KEY] = {
            "status": "verified",
            "authority": "provider_document",
            "sha256": name,
            "identity": target_identity(metadata).fingerprint,
        }
    registry.upsert_file(
        root=root,
        relative_path=name,
        current_path=name,
        sha256=name,
        size_bytes=1,
        mtime_ns=1,
        status="resolved" if ads else "unresolved",
        metadata=metadata,
    )


def test_sidebar_badge_table_and_detail_agree_for_external_identity(
    qtbot, monkeypatch, tmp_path
):
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    monkeypatch.setattr("adsfolio.gui.main_window.load_config", lambda: GuiConfig())
    monkeypatch.setattr("adsfolio.gui.main_window.save_config", lambda config: None)
    root = tmp_path / "library"
    root.mkdir()
    registry = PdfRegistry(root)
    _add_record(registry, root, "ads.pdf", ads=True)
    _add_record(registry, root, "external.pdf", external=True)
    _add_record(registry, root, "unknown.pdf")
    before = copy.deepcopy(registry.query_files())
    window = MainWindow()
    qtbot.addWidget(window)
    window.registries[str(root)] = registry
    window.current_root = root
    window.model.set_registry(registry)
    window._populate_views(registry)
    window._update_status()
    labels = [
        window.views_item.child(i).text(0)
        for i in range(window.views_item.childCount())
    ]
    assert labels.index("ADS linked (1)") < labels.index("Identified without ADS (1)")
    assert "Needs identification (1)" in labels
    assert window.identify_button.badge_text == "1"
    assert (
        "1 ADS linked · 1 identified without ADS · 1 need identification"
        in window.status.currentMessage()
    )
    for view, expected in (
        (LibraryView.RESOLVED, "ads.pdf"),
        (LibraryView.IDENTIFIED_WITHOUT_ADS, "external.pdf"),
        (LibraryView.UNRESOLVED, "unknown.pdf"),
    ):
        window._select_view(view)
        assert window.model.total == 1
        assert window.model.record_at(0)["current_path"] == expected
        assert window.identify_button.badge_text == "1"
        assert window.status.toolTip() == view.description
    external = registry.query_files(identification="identified_without_ads")[0]
    model = FileTableModel(None, FileQuery())
    model._rows = [external]
    status = next(i for i, column in enumerate(COLUMNS) if column.key == "status")
    title = next(i for i, column in enumerate(COLUMNS) if column.key == "title")
    assert "verified PDF identifier" in model.index(0, status).data(
        Qt.AccessibleTextRole
    )
    assert model.index(0, title).data(Qt.ForegroundRole) is None
    pane = DetailPane()
    qtbot.addWidget(pane)
    pane.set_record(external, root)
    assert pane._row_widgets["status"].value.text() == "Identified (no ADS link)"
    assert registry.query_files() == before
    window.close()


def test_combined_groups_keep_root_ownership_search_and_counts(tmp_path):
    registries = {}
    for label in ("first", "second"):
        root = tmp_path / label
        root.mkdir()
        registry = PdfRegistry(root)
        _add_record(registry, root, "external.pdf", external=True)
        _add_record(registry, root, "unknown.pdf")
        registries[str(root)] = registry
    try:
        combined = CombinedLibrary(registries)
        rows = combined.query_files(
            identification="identified_without_ads", limit=1, offset=1
        )
        assert len(rows) == 1
        assert rows[0]["root"] in registries
        assert (
            combined.count_files_matching(identification="identified_without_ads") == 2
        )
        assert combined.count_files_matching(identification="needs_identification") == 2
        assert (
            combined.count_files_matching(
                identification="identified_without_ads", search="unknown"
            )
            == 0
        )
    finally:
        for registry in registries.values():
            registry.close()


@pytest.mark.parametrize("manual", [False, True])
def test_changed_external_pdf_leaves_success_group(qtbot, manual):
    from test_pdf_classification import external_record

    record = external_record("manual_confirmed" if manual else "verified")
    record["sha256"] = "changed"
    model = FileTableModel(None, FileQuery())
    model._rows = [record]
    status = next(i for i, column in enumerate(COLUMNS) if column.key == "status")
    title = next(i for i, column in enumerate(COLUMNS) if column.key == "title")
    assert "PDF changed" in model.index(0, status).data(Qt.ToolTipRole)
    assert model.index(0, title).data(Qt.ForegroundRole) is None
