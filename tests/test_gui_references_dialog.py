"""User workflows for adding and exporting local citation records."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from pypdf import PdfWriter
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog

from adsfolio.gui.references_dialog import (
    ReferencesDialog,
    _BibtexImportDialog,
    _ReferenceEditor,
)
from adsfolio.pdf.catalog import PdfCatalog
from adsfolio.references import LocalReferences


@pytest.fixture
def catalog(tmp_path: Path):
    """Keep GUI mutations in an isolated catalog."""
    instance = PdfCatalog(tmp_path / "catalog.sqlite")
    yield instance
    instance.close()


@pytest.fixture
def dialog(qtbot, catalog):
    """Provide a real local-reference dialog without network requests."""
    widget = ReferencesDialog(catalog=catalog)
    qtbot.addWidget(widget)
    return widget


def test_import_requires_preview_and_keeps_draft_out_of_catalog(qtbot, dialog, catalog):
    """Preview shows citation metadata but saves only after explicit Add."""
    service = LocalReferences(catalog)
    importer = _BibtexImportDialog(dialog, service)
    qtbot.addWidget(importer)
    importer.text_edit.setPlainText(
        "@article{scholar-key, title={A {NASA} study}, author={Doe, Jane}, year={2025}}"
    )
    assert not importer.import_button.isEnabled()

    qtbot.mouseClick(importer.preview_button, Qt.LeftButton)
    assert importer.preview_list.count() == 1
    assert "scholar-key" in importer.preview_list.item(0).text()
    assert service.list() == []
    assert importer.import_button.isEnabled()

    importer.text_edit.appendPlainText("\n@misc{second, title={Second}}")
    assert not importer.import_button.isEnabled()
    assert importer.preview_list.count() == 0
    qtbot.mouseClick(importer.preview_button, Qt.LeftButton)
    qtbot.mouseClick(importer.import_button, Qt.LeftButton)
    assert importer.result() == QDialog.Accepted
    assert {record["cite_key"] for record in service.list()} == {
        "scholar-key",
        "second",
    }


def test_open_bib_file_is_previewed_before_import(
    qtbot, dialog, catalog, tmp_path, monkeypatch
):
    """Opening an external .bib file remains a reviewable draft."""
    source = tmp_path / "scholar.bib"
    source.write_text(
        "@book{book, title={A book}, publisher={Press}}", encoding="utf-8-sig"
    )
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(source), ""))
    importer = _BibtexImportDialog(dialog, LocalReferences(catalog))
    qtbot.addWidget(importer)
    qtbot.mouseClick(importer.open_button, Qt.LeftButton)
    assert importer.text_edit.toPlainText().startswith("@book")
    assert not importer.import_button.isEnabled()
    assert LocalReferences(catalog).list() == []


def test_invalid_import_keeps_dialog_and_catalog_unchanged(qtbot, dialog, catalog):
    """Invalid or empty input shows an error and cannot be saved."""
    importer = _BibtexImportDialog(dialog, LocalReferences(catalog))
    qtbot.addWidget(importer)
    importer.text_edit.setPlainText("This is not a BibTeX entry.")
    qtbot.mouseClick(importer.preview_button, Qt.LeftButton)
    assert importer.status_label.text()
    assert not importer.import_button.isEnabled()
    assert LocalReferences(catalog).list() == []


def test_manual_reference_needs_no_ads_identity_or_pdf(qtbot, dialog, catalog):
    """A manual entry can be created and listed with no PDF or DOI."""
    editor = _ReferenceEditor(dialog, None, LocalReferences(catalog).add)
    qtbot.addWidget(editor)
    editor.cite_key_edit.setText("field-notes")
    editor.field_edits["title"].setText("Field notes")
    qtbot.mouseClick(editor.save_button, Qt.LeftButton)
    assert editor.result() == QDialog.Accepted
    record = editor.record
    assert record is not None
    assert not record.get("bibcode")
    assert record.get("attachments", []) == []
    dialog.refresh([record["id"]])
    assert dialog.reference_list.count() == 1
    assert "Local" in dialog.reference_list.item(0).text()
    assert dialog.copy_button.isEnabled()


def test_editor_preserves_unexposed_bibtex_fields(qtbot, dialog, catalog):
    """Changing a title does not erase imported abstract or custom fields."""
    service = LocalReferences(catalog)
    record = service.add(
        cite_key="report",
        fields={"title": "Old", "abstract": "Keep this", "customfield": "custom"},
    )
    from functools import partial

    editor = _ReferenceEditor(dialog, record, partial(service.update, record["id"]))
    qtbot.addWidget(editor)
    editor.field_edits["title"].setText("New")
    qtbot.mouseClick(editor.save_button, Qt.LeftButton)
    assert service.get(record["id"])["fields"] == {
        "title": "New",
        "abstract": "Keep this",
        "customfield": "custom",
    }


def test_editor_error_preserves_draft(qtbot, dialog, catalog):
    """Validation errors stay next to the unmodified input fields."""
    editor = _ReferenceEditor(dialog, None, LocalReferences(catalog).add)
    qtbot.addWidget(editor)
    editor.field_edits["title"].setText("Keep my draft")
    qtbot.mouseClick(editor.save_button, Qt.LeftButton)
    assert editor.result() != QDialog.Accepted
    assert editor.status_label.text()
    assert editor.field_edits["title"].text() == "Keep my draft"
    assert LocalReferences(catalog).list() == []


def test_copy_and_export_mixed_selection(dialog, catalog, tmp_path, monkeypatch):
    """ADS and local metadata export together using their stable citation keys."""
    service = LocalReferences(catalog)
    local = service.add(cite_key="local-key", fields={"title": "Local report"})
    ads = service.add(
        cite_key="ads-key", bibcode="2025ApJ...999....1A", fields={"title": "ADS paper"}
    )
    dialog.refresh([local["id"], ads["id"]])
    assert not dialog.edit_button.isEnabled()
    dialog._copy_bibtex()
    copied = QApplication.clipboard().text()
    assert "local-key" in copied
    assert "ads-key" in copied
    output = tmp_path / "references.bib"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(output), ""))
    dialog._export_bibtex()
    assert output.read_text(encoding="utf-8") == copied


def test_failed_export_preserves_clipboard_and_existing_file(
    dialog, catalog, tmp_path, monkeypatch
):
    """Incomplete local metadata cannot silently produce a partial bibliography."""
    service = LocalReferences(catalog)
    valid = service.add(cite_key="valid", fields={"title": "Valid"})
    incomplete = service.add(cite_key="draft", entry_type="article", fields={})
    dialog.refresh([valid["id"], incomplete["id"]])
    output = tmp_path / "references.bib"
    output.write_text("existing bibliography", encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(output), ""))
    QApplication.clipboard().setText("previous clipboard")
    dialog._copy_bibtex()
    assert QApplication.clipboard().text() == "previous clipboard"
    assert dialog.status_label.text()
    dialog._export_bibtex()
    assert output.read_text(encoding="utf-8") == "existing bibliography"


def test_attach_pdf_keeps_reference_identity(
    qtbot, dialog, catalog, tmp_path, monkeypatch
):
    """An existing local reference gains a PDF without becoming a new citation."""
    service = LocalReferences(catalog)
    record = service.add(cite_key="report", fields={"title": "A report"})
    pdf = tmp_path / "report.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(pdf)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(pdf), ""))
    dialog.refresh([record["id"]])
    with qtbot.waitSignal(dialog.references_changed):
        dialog._attach_pdf()
    assert service.get(record["id"])["attachments"]
    assert len(service.list()) == 1
    assert dialog._selected_ids() == [record["id"]]


def test_closing_does_not_close_borrowed_catalog(dialog, catalog):
    """The main window retains ownership of a supplied catalog."""
    dialog.reject()
    assert LocalReferences(catalog).list() == []
