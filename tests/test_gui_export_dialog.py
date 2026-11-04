"""Export Papers writes BibTeX, citation text, or a list, and names skipped PDFs."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtWidgets import QApplication, QFileDialog, QPushButton

from adsfolio.citations import CitationStyle
from adsfolio.citekeys import KeyPolicy, KeyStyle
from adsfolio.config import Config
from adsfolio.gui.export_dialog import (
    FORMAT_CITATION,
    FORMAT_LIST,
    ExportPapersDialog,
    ExportScope,
    SkippedPdfsDialog,
    summarize_papers,
)
from adsfolio.pdf.registry import PdfRegistry

STYLE = CitationStyle(
    id="short", name="Short", template="{first_author_surname} ({year}) {title}"
)


@pytest.fixture
def records(monkeypatch, tmp_path: Path):
    """Catalog one ADS paper, one verified-outside-ADS paper, and a bare PDF."""
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    root = tmp_path / "library"
    root.mkdir()
    registry = PdfRegistry(root)

    def add(name: str, status: str, metadata: dict) -> None:
        path = root / name
        path.write_bytes(f"fictional {name}".encode())
        registry.upsert_file(
            root=root,
            relative_path=name,
            current_path=name,
            sha256=name,
            size_bytes=path.stat().st_size,
            mtime_ns=path.stat().st_mtime_ns,
            status=status,
            metadata=metadata,
        )

    add(
        "ads.pdf",
        "resolved",
        {
            "bibcode": "2020ApJ...900....1D",
            "title": ["Dust of Small Stars"],
            "author": ["Doe, John A.", "Roe, Alice"],
            "year": "2020",
            "pub": "The Astrophysical Journal",
            "abstract": "A fictional abstract.",
        },
    )
    add("copy.pdf", "resolved", {"bibcode": "2020ApJ...900....1D"})
    add("blank.pdf", "unresolved", {})
    yield registry, registry.query_files(order_by="relative_path")
    registry.close()


def _dialog(qtbot, registry, rows, **kwargs) -> ExportPapersDialog:
    dialog = ExportPapersDialog(
        None,
        [
            ExportScope("selected", "Selected rows", rows[:1]),
            ExportScope("all", "All PDFs in this library", rows),
        ],
        registry.catalog,
        [STYLE],
        initial_scope="all",
        **kwargs,
    )
    qtbot.addWidget(dialog)
    dialog._update_preview()
    return dialog


def test_summary_exports_each_paper_once_and_lists_bare_pdfs(records):
    _registry, rows = records
    summary = summarize_papers(rows)
    assert [paper["current_path"] for paper in summary.papers] == ["ads.pdf"]
    assert (summary.ads_papers, summary.other_papers) == (1, 0)
    assert summary.shared_pdfs == 1
    assert [record["current_path"] for record in summary.skipped] == ["blank.pdf"]


def test_each_format_previews_the_chosen_papers(qtbot, records):
    registry, rows = records
    dialog = _dialog(qtbot, registry, rows)
    assert dialog.summary_label.text() == (
        "Exports 1 paper: 1 ADS paper and 0 other papers. "
        "Each paper exports once (1 duplicate PDF skipped)."
    )
    assert dialog.skipped_label.text() == "Skips 1 PDF without paper details."
    assert dialog.preview.toPlainText().startswith("@article{2020ApJ...900....1D,")
    dialog.format_choice.setCurrentIndex(dialog.format_choice.findData(FORMAT_CITATION))
    dialog._update_preview()
    assert dialog.preview.toPlainText() == "Doe (2020) Dust of Small Stars"
    dialog.format_choice.setCurrentIndex(dialog.format_choice.findData(FORMAT_LIST))
    dialog._update_preview()
    assert "Dust of Small Stars" in dialog.preview.toPlainText()
    # The list settings sit in this window, without the list window's Output tab.
    tabs = dialog.list_dialog.tabs
    assert [tabs.tabText(i) for i in range(tabs.count())] == ["Your name", "Format"]


def test_save_writes_the_format_and_keeps_the_key_styles(
    qtbot, records, monkeypatch, tmp_path
):
    registry, rows = records
    dialog = _dialog(qtbot, registry, rows)
    dialog.ads_style.setCurrentIndex(dialog.ads_style.findData(KeyStyle.SCHOLAR))
    target = tmp_path / "out.bib"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *args: (str(target), "BibTeX (*.bib)")
    )
    dialog._save()
    assert target.read_text().startswith("@article{doe2020dust,")
    assert Config.load_key_policy().ads is KeyStyle.SCHOLAR


def test_unchecked_default_box_leaves_saved_key_styles_unchanged(
    qtbot, records, monkeypatch
):
    registry, rows = records
    Config.save_key_policy(KeyPolicy())
    dialog = _dialog(qtbot, registry, rows)
    dialog.ads_style.setCurrentIndex(dialog.ads_style.findData(KeyStyle.SCHOLAR))
    dialog.keep_key_styles.setChecked(False)
    copied = []
    monkeypatch.setattr(QApplication.clipboard(), "setText", copied.append)
    dialog._copy()
    assert copied and copied[0].startswith("@article{doe2020dust,")
    assert Config.load_key_policy() == KeyPolicy()


def test_scope_without_papers_disables_copy_and_save(qtbot, records):
    registry, rows = records
    blank = [row for row in rows if row["current_path"] == "blank.pdf"]
    dialog = ExportPapersDialog(
        None,
        [ExportScope("selected", "Selected rows", blank)],
        registry.catalog,
        [],
    )
    qtbot.addWidget(dialog)
    dialog._update_preview()
    assert dialog.preview.toPlainText() == "No papers with details to export."
    assert not dialog.copy_button.isEnabled()
    assert not dialog.save_button.isEnabled()


def test_skipped_list_names_the_pdfs_and_can_show_needs_identification(qtbot, records):
    _registry, rows = records
    shown = []
    dialog = SkippedPdfsDialog(
        None, summarize_papers(rows).skipped, lambda: shown.append(1)
    )
    qtbot.addWidget(dialog)
    assert dialog.paths.toPlainText() == "blank.pdf"
    view = next(
        button
        for button in dialog.findChildren(QPushButton)
        if button.text() == "Show Needs Identification"
    )
    view.click()
    assert shown == [1]


def test_preview_names_a_paper_that_needs_a_typed_key(qtbot, records):
    registry, rows = records
    dialog = _dialog(qtbot, registry, rows)
    dialog.ads_style.setCurrentIndex(dialog.ads_style.findData(KeyStyle.MANUAL))
    dialog._update_preview()
    assert dialog.preview.toPlainText() == (
        "% Dust of Small Stars: this paper needs a citation key. "
        "ADSfolio asks for it when you copy or save."
    )
    assert dialog.save_button.isEnabled()


def test_opening_the_window_does_not_render_the_whole_list(qtbot, records, monkeypatch):
    registry, rows = records
    rendered = []
    original = ExportPapersDialog.render

    def counted(self, papers, *, preview=False):
        rendered.append(len(papers))
        return original(self, papers, preview=preview)

    monkeypatch.setattr(ExportPapersDialog, "render", counted)
    dialog = _dialog(qtbot, registry, rows)
    # The hidden list window has no papers of its own to render.
    assert dialog.list_dialog.local_docs == []
    assert all(count <= 3 for count in rendered)


def test_filename_style_keys_and_left_out_fields(qtbot, records):
    registry, rows = records
    dialog = _dialog(qtbot, registry, rows)
    assert dialog.excluded_fields == ["abstract", "adsnote"]
    assert "abstract" in dialog.present_fields.text()
    dialog.filename_style_button.click()
    dialog._update_preview()
    text = dialog.preview.toPlainText()
    assert text.startswith("@article{DoeJA+RoeA_2020ApJ...900....1D,")
    assert "abstract" not in text
    dialog.exclude_fields.setText("")
    dialog._update_preview()
    assert "abstract = {A fictional abstract.}" in dialog.preview.toPlainText()
