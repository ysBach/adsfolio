"""Check rename previews for files with missing template fields."""

import os

import pytest
from pdf_identity_fixtures import confirm_metadata, write_pdf

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox

from adsfolio.gui.dialogs import RenameDialog
from adsfolio.pdf.files import sha256_file
from adsfolio.pdf.identifier import PdfIdentifier
from adsfolio.pdf.identify_settings import IdentifySettings
from adsfolio.pdf.registry import open_registry_state
from adsfolio.pdf.rename_settings import RenameSettings
from adsfolio.pdf.renamer import PdfRenamer


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def rename_library(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    missing = root / "missing.pdf"
    write_pdf(missing, doi="10.1234/missing")
    missing_record = PdfIdentifier(None).build_record(
        {
            "provider": "crossref",
            "doi": ["10.1234/missing"],
            "author": ["Doe, Jane"],
            "title": ["Dust Tail"],
            "year": "2024",
        },
        missing,
    )
    state = open_registry_state(root)
    state.upsert_file(missing.name, missing_record)
    state.save()
    state.close()
    return root


def _dialog(root):
    return RenameDialog(
        root,
        RenameSettings(template="{bibcode}"),
        identify_settings=IdentifySettings(),
    )


def _files(root):
    state = open_registry_state(root)
    try:
        return state.files
    finally:
        state.close()


def test_template_help_explains_missing_fields(application, rename_library):
    dialog = _dialog(rename_library)
    help_text = dialog.template.toolTip()

    assert (
        "ADSfolio skips files when a field in the template has no metadata."
        in help_text
    )
    assert (
        "Add the missing metadata or remove that field from the template." in help_text
    )
    dialog.close()


def test_preview_shows_skipped_reason_and_preserves_no_change_confirmation(
    application, rename_library, monkeypatch
):
    root = rename_library
    original_bytes = (root / "missing.pdf").read_bytes()
    original_records = _files(root)
    dialog = _dialog(root)
    questions = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args: questions.append(args) or QMessageBox.No,
    )

    dialog.show()
    dialog.preview()
    application.processEvents()

    assert dialog.table.rowCount() == 0
    assert dialog.summary.text() == "No changes, 1 skipped"
    assert dialog.skipped_label.isVisible()
    assert dialog.skipped_details.isVisible()
    assert dialog.skipped_details.isReadOnly()
    details = dialog.skipped_details.toPlainText()
    assert "missing.pdf" in details
    assert "bibcode" in details
    assert "Add metadata or choose a template" in details
    dialog.apply()
    assert len(questions) == 1
    assert (root / "missing.pdf").read_bytes() == original_bytes
    assert _files(root) == original_records
    dialog.close()


def test_preview_shows_plans_other_skips_and_identity_refusals(
    application, rename_library
):
    root = rename_library
    nested = root / "nested"
    nested.mkdir()
    valid = nested / "valid.pdf"
    write_pdf(valid)
    valid_record = confirm_metadata(
        valid,
        {
            "status": "resolved",
            "bibcode": "2024ApJ...900..100D",
            "author": ["Doe, Jane"],
            "title": ["Valid Paper"],
            "year": "2024",
            "sha256": sha256_file(valid),
        },
    )
    refused = root / "refused.pdf"
    write_pdf(refused, doi="10.1234/actual")
    refused_record = {
        "status": "resolved",
        "bibcode": "2024ApJ...900..101D",
        "doi": "10.1234/different",
        "title": ["Conflicting Paper"],
        "sha256": sha256_file(refused),
    }
    not_ready = root / "not-ready.pdf"
    write_pdf(not_ready, doi="10.1234/not-ready")
    state = open_registry_state(root)
    state.upsert_file("nested/valid.pdf", valid_record)
    state.upsert_file(refused.name, refused_record)
    state.upsert_file(not_ready.name, {"status": "unresolved"})
    state.save()
    renamer = PdfRenamer()
    plans = renamer.batch_rename(state, template="{bibcode}", dry_run=True)
    original_records = state.files
    state.close()
    original_bytes = {path: path.read_bytes() for path in root.rglob("*.pdf")}
    assert plans == [("nested/valid.pdf", "2024ApJ...900..100D.pdf")]
    assert "refused.pdf" in renamer.identity_refused
    assert "not-ready.pdf" in renamer.skipped

    dialog = _dialog(root)
    dialog.preview()

    assert dialog.summary.text() == "1 change(s), 3 skipped"
    assert dialog.table.rowCount() == 1
    assert dialog.table.item(0, 0).text() == "nested/valid.pdf"
    assert dialog.table.item(0, 1).text() == "nested/2024ApJ...900..100D.pdf"
    details = dialog.skipped_details.toPlainText()
    for name, reason in renamer.skipped.items():
        assert name in details
        assert reason in details
    assert "refused.pdf" in details
    assert "Identity not verified" in details
    assert renamer.identity_refused["refused.pdf"] in details
    assert {path: path.read_bytes() for path in root.rglob("*.pdf")} == original_bytes
    assert _files(root) == original_records
    dialog.close()


def test_preview_preserves_destination_for_resolved_files_in_unresolved(
    application, rename_library
):
    root = rename_library
    unresolved = root / "unresolved"
    unresolved.mkdir()
    source = unresolved / "resolved.pdf"
    write_pdf(source)
    record = confirm_metadata(
        source,
        {
            "status": "resolved",
            "bibcode": "2024ApJ...900..102D",
            "title": ["Resolved Paper"],
            "sha256": sha256_file(source),
        },
    )
    state = open_registry_state(root)
    state.upsert_file("unresolved/resolved.pdf", record)
    state.save()
    original_records = state.files
    state.close()
    original_bytes = source.read_bytes()
    dialog = _dialog(root)

    dialog.preview()

    assert dialog.summary.text() == "1 change(s), 1 skipped"
    assert dialog.table.rowCount() == 1
    assert dialog.table.item(0, 0).text() == "unresolved/resolved.pdf"
    assert dialog.table.item(0, 1).text() == "2024ApJ...900..102D.pdf"
    assert "missing.pdf" in dialog.skipped_details.toPlainText()
    assert source.read_bytes() == original_bytes
    assert _files(root) == original_records
    dialog.close()


def test_preview_clears_old_skips_when_template_changes_or_fails(
    application, rename_library
):
    dialog = _dialog(rename_library)
    dialog.preview()
    assert "missing.pdf" in dialog.skipped_details.toPlainText()

    dialog.template.setText("{title}")
    dialog.preview()
    assert dialog.summary.text() == "1 change(s)"
    assert dialog.table.rowCount() == 1
    assert dialog.table.item(0, 1).text() == "Dust_Tail.pdf"
    assert dialog.skipped_details.isHidden()
    assert dialog.skipped_details.toPlainText() == ""

    dialog.template.setText("{bibcode}")
    dialog.preview()
    assert "missing.pdf" in dialog.skipped_details.toPlainText()
    dialog.template.setText("{unknown}")
    dialog.preview()
    assert "Unknown template key" in dialog.summary.text()
    assert dialog.table.rowCount() == 0
    assert dialog.skipped_details.isHidden()
    assert dialog.skipped_details.toPlainText() == ""

    dialog.template.setText("missing")
    dialog.preview()
    assert dialog.summary.text() == "No changes"
    assert dialog.table.rowCount() == 0
    assert dialog.skipped_details.isHidden()
    dialog.close()


@pytest.mark.rename_preview_process
def test_preview_runs_in_a_child_process_without_blocking(
    application, rename_library, qtbot, monkeypatch, tmp_path
):
    home = tmp_path / "child-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    before = {path: path.read_bytes() for path in rename_library.rglob("*.pdf")}
    dialog = _dialog(rename_library)
    dialog.template.setText("{title}")
    dialog.show()

    dialog.preview()

    # The call returns at once. The child process builds the preview.
    assert dialog.summary.text().startswith("Checking each PDF's identity")
    assert dialog.preview_progress.isVisible()
    assert not dialog.preview_button.isEnabled()
    qtbot.waitUntil(lambda: dialog._preview_job is None, timeout=60000)
    assert dialog.summary.text() == "1 change(s)"
    assert dialog.table.item(0, 1).text() == "Dust_Tail.pdf"
    assert not dialog.preview_progress.isVisible()
    assert dialog.preview_button.isEnabled()
    assert {p: p.read_bytes() for p in rename_library.rglob("*.pdf")} == before
    assert not list(home.rglob("pdf-library.sqlite"))
    dialog.close()


@pytest.mark.rename_preview_process
def test_closing_the_dialog_stops_a_running_preview(
    application, rename_library, qtbot, monkeypatch, tmp_path
):
    monkeypatch.setenv("HOME", str(tmp_path))
    dialog = _dialog(rename_library)
    dialog.show()
    dialog.preview()
    job = dialog._preview_job
    assert job is not None and job.running
    dialog.reject()
    assert dialog._preview_job is None
    qtbot.waitUntil(lambda: not job.running, timeout=60000)
    assert dialog.summary.text().startswith("Checking each PDF's identity")
