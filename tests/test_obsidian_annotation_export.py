from pathlib import Path

import pytest

from adsfolio.pdf.obsidian import ObsidianNoteWriter
from adsfolio.pdf.sidecar import Sidecar

fitz = pytest.importorskip("fitz")


def make_pdf(path: Path, comment: str = "First comment") -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Highlighted passage and other text")
    highlight = page.add_highlight_annot(page.search_for("Highlighted passage"))
    highlight.set_info(content=comment)
    highlight.update()
    sticky = page.add_text_annot((72, 110), "A sticky note")
    sticky.update()
    page.add_freetext_annot(fitz.Rect(72, 150, 300, 180), "A free text note")
    doc.save(path)
    doc.close()


def test_existing_note_gets_annotations_and_repeat_does_not_write(tmp_path):
    pdf = tmp_path / "paper.pdf"
    make_pdf(pdf)
    state = Sidecar(tmp_path)
    state.upsert_file(
        "paper.pdf",
        {
            "current_filename": "paper.pdf",
            "bibcode": "2024ApJ...900..100S",
            "title": "Example",
            "abstract": "Cached",
        },
    )
    note = tmp_path / "paper.md"
    note.write_text("My own notes\n", encoding="utf-8")
    writer = ObsidianNoteWriter()
    plans = writer.generate(tmp_path, state, apply=True)
    content = note.read_text(encoding="utf-8")
    assert plans[0].action == "update"
    assert "My own notes" in content
    assert "Highlighted passage" in content
    assert "First comment" in content
    assert "A sticky note" in content
    assert "A free text note" in content
    before = note.stat().st_mtime_ns
    plans = writer.generate(tmp_path, state, apply=True)
    assert plans[0].action == "skip-unchanged"
    assert note.stat().st_mtime_ns == before
    assert note.read_text(encoding="utf-8") == content


def test_changes_to_annotations_update_only_the_managed_section(tmp_path):
    pdf = tmp_path / "paper.pdf"
    make_pdf(pdf)
    state = Sidecar(tmp_path)
    state.upsert_file(
        "paper.pdf",
        {"bibcode": "2024ApJ...900..100S", "title": "Example", "abstract": "Cached"},
    )
    note = tmp_path / "paper.md"
    note.write_text("Personal text\n", encoding="utf-8")
    writer = ObsidianNoteWriter()
    writer.generate(tmp_path, state, apply=True)
    note.write_text(note.read_text() + "\nMore personal text\n", encoding="utf-8")
    pdf.unlink()
    make_pdf(pdf, comment="Changed comment")
    assert writer.generate(tmp_path, state, apply=True)[0].action == "update"
    content = note.read_text()
    assert "Personal text" in content and "More personal text" in content
    assert "Changed comment" in content and "First comment" not in content
    assert content.count("<!-- adsfolio:pdf-annotations:start -->") == 1


def test_corrupt_pdf_does_not_replace_existing_note(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"not a pdf")
    state = Sidecar(tmp_path)
    state.upsert_file(
        "paper.pdf",
        {"bibcode": "2024ApJ...900..100S", "title": "Example", "abstract": "Cached"},
    )
    note = tmp_path / "paper.md"
    note.write_text("Keep this note", encoding="utf-8")
    plans = ObsidianNoteWriter().generate(tmp_path, state, apply=True, force=True)
    assert plans[0].action == "skip-error"
    assert note.read_text() == "Keep this note"


def test_force_does_not_rewrite_identical_note(tmp_path):
    pdf = tmp_path / "paper.pdf"
    make_pdf(pdf)
    state = Sidecar(tmp_path)
    state.upsert_file(
        "paper.pdf",
        {"bibcode": "2024ApJ...900..100S", "title": "Example", "abstract": "Cached"},
    )
    writer = ObsidianNoteWriter()
    writer.generate(tmp_path, state, apply=True)
    note = tmp_path / "paper.md"
    before = note.stat().st_mtime_ns
    plans = writer.generate(tmp_path, state, apply=True, force=True)
    assert plans[0].action == "skip-unchanged"
    assert note.stat().st_mtime_ns == before


def test_removed_annotations_and_crlf_note(tmp_path):
    pdf = tmp_path / "paper.pdf"
    make_pdf(pdf)
    state = Sidecar(tmp_path)
    state.upsert_file(
        "paper.pdf",
        {"bibcode": "2024ApJ...900..100S", "title": "Example", "abstract": "Cached"},
    )
    writer = ObsidianNoteWriter()
    note = tmp_path / "paper.md"
    note.write_bytes(b"Personal text\r\n")
    writer.generate(tmp_path, state, apply=True)
    content = note.read_bytes().replace(b"\n", b"\r\n").replace(b"\r\r\n", b"\r\n")
    note.write_bytes(content)
    pdf.unlink()
    with fitz.open() as document:
        document.new_page()
        document.save(pdf)
    writer.generate(tmp_path, state, apply=True)
    result = note.read_text()
    assert result.startswith("Personal text\n")
    assert result.count("<!-- adsfolio:pdf-annotations:start -->") == 1
    assert "_No PDF annotations found._" in result
    assert "First comment" not in result
