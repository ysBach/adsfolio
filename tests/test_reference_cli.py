"""Exercise external-reference intake and mixed export through Click."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from adsfolio.cli.commands import main
from adsfolio.pdf.catalog import PdfCatalog


def test_import_edit_export_without_pdf_or_token(tmp_path: Path) -> None:
    runner = CliRunner()
    args = ["reference", "--catalog", str(tmp_path / "catalog.sqlite")]
    imported = runner.invoke(
        main,
        [*args, "import", "--source", "google-scholar"],
        input="@book{SomeKey, title={An {ADS}-external book}, author={Smith, A}, year={2025}, publisher={Example}}",
    )
    assert imported.exit_code == 0, imported.output
    listed = runner.invoke(main, [*args, "list", "--json"])
    assert listed.exit_code == 0, listed.output
    record = json.loads(listed.output)[0]
    assert record["cite_key"] == "SomeKey"
    assert record["bibcode"] is None
    assert record["attachments"] == []
    assert record["source"] == "google-scholar"
    edited = runner.invoke(
        main, [*args, "edit", str(record["id"]), "--field", "title=Corrected title"]
    )
    assert edited.exit_code == 0, edited.output
    output = tmp_path / "references.bib"
    exported = runner.invoke(main, [*args, "export", "--all", "-o", str(output)])
    assert exported.exit_code == 0, exported.output
    text = output.read_text()
    assert "SomeKey" in text
    assert "Corrected title" in text
    assert "publisher" in text.lower()
    assert "Example" in text


def test_manual_add_and_export_with_existing_ads_document(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.sqlite"
    catalog = PdfCatalog(catalog_path)
    try:
        catalog.observe_file(
            tmp_path,
            "ads.pdf",
            sha256=None,
            size_bytes=1,
            mtime_ns=1,
            metadata={
                "bibcode": "2025ApJ...999....1S",
                "title": ["ADS paper"],
                "author": ["Smith, A"],
                "year": "2025",
                "pub": "The Astrophysical Journal",
            },
        )
    finally:
        catalog.close()
    runner = CliRunner()
    args = ["reference", "--catalog", str(catalog_path)]
    added = runner.invoke(
        main,
        [
            *args,
            "add",
            "--key",
            "External2025",
            "--type",
            "misc",
            "--field",
            "title=External work",
        ],
    )
    assert added.exit_code == 0, added.output
    output = runner.invoke(main, [*args, "export", "--all"])
    assert output.exit_code == 0, output.output
    assert "2025ApJ...999....1S" in output.output
    assert "External2025" in output.output
    assert "ADS paper" in output.output
    assert "External work" in output.output


def test_failed_export_preserves_existing_destination(tmp_path: Path) -> None:
    output = tmp_path / "references.bib"
    output.write_text("Keep this bibliography.")
    result = CliRunner().invoke(
        main,
        [
            "reference",
            "--catalog",
            str(tmp_path / "catalog.sqlite"),
            "export",
            "123",
            "-o",
            str(output),
        ],
    )
    assert result.exit_code != 0
    assert output.read_text() == "Keep this bibliography."


def test_preview_does_not_save_references(tmp_path: Path) -> None:
    runner = CliRunner()
    args = ["reference", "--catalog", str(tmp_path / "catalog.sqlite")]
    preview = runner.invoke(
        main, [*args, "import", "--preview"], input="@misc{Key,title={Preview title}}"
    )
    assert preview.exit_code == 0, preview.output
    assert json.loads(preview.output)[0]["fields"]["title"] == "Preview title"
    listed = runner.invoke(main, [*args, "list", "--json"])
    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.output) == []


def test_export_requires_explicit_selection(tmp_path: Path) -> None:
    args = ["reference", "--catalog", str(tmp_path / "catalog.sqlite"), "export"]
    runner = CliRunner()
    for selection in ([], ["1", "--all"]):
        result = runner.invoke(main, [*args, *selection])
        assert result.exit_code == 2
        assert "Supply reference IDs or --all" in result.output


def test_duplicate_fields_and_conflicting_edits_are_reported(tmp_path: Path) -> None:
    runner = CliRunner()
    args = ["reference", "--catalog", str(tmp_path / "catalog.sqlite")]
    duplicate = runner.invoke(
        main,
        [*args, "add", "--key", "Key", "--field", "title=One", "--field", "TITLE=Two"],
    )
    assert duplicate.exit_code != 0
    assert "more than once" in duplicate.output
    conflict = runner.invoke(
        main, [*args, "edit", "1", "--field", "title=One", "--remove-field", "title"]
    )
    assert conflict.exit_code == 2
    assert "set and removed" in conflict.output


def test_attach_existing_pdf_without_moving_it(tmp_path: Path) -> None:
    runner = CliRunner()
    args = ["reference", "--catalog", str(tmp_path / "catalog.sqlite")]
    added = runner.invoke(
        main, [*args, "add", "--key", "Key", "--field", "title=Local report"]
    )
    assert added.exit_code == 0, added.output
    document_id = added.output.split()[0]
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    attached = runner.invoke(main, [*args, "attach", document_id, str(pdf)])
    assert attached.exit_code == 0, attached.output
    listed = runner.invoke(main, [*args, "list", "--json"])
    assert listed.exit_code == 0, listed.output
    assert len(json.loads(listed.output)[0]["attachments"]) == 1
    assert pdf.read_bytes() == b"%PDF-1.4\n"
