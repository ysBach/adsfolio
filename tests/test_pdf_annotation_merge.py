"""Tests for merging annotations between synthetic PDF editions."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fitz")

import fitz

from adsfolio.pdf.annotation_merge import (
    AnnotationMergeError,
    Placement,
    WordIndex,
    build_merged_copy,
    enumerate_annotations,
    suggest_exact_placement,
)

TEXT = ["alpha beta gamma delta", "repeated phrase here", "repeated phrase here"]


def _pdf(path: Path, lines: list[str], *, indent: float = 72.0) -> Path:
    doc = fitz.open()
    page = doc.new_page()
    for number, line in enumerate(lines):
        page.insert_text((indent, 100 + 30 * number), line, fontsize=12)
    doc.save(str(path))
    doc.close()
    return path


def _source(tmp_path: Path) -> Path:
    path = _pdf(tmp_path / "source.pdf", TEXT)
    doc = fitz.open(str(path))
    page = doc[0]
    quads = page.search_for("beta gamma", quads=True)
    for make, color in (
        (page.add_highlight_annot, (1, 0, 0)),
        (page.add_underline_annot, (0, 0.5, 0)),
        (page.add_strikeout_annot, (0, 0, 1)),
        (page.add_squiggly_annot, (0.5, 0, 0.5)),
    ):
        annot = make(quads)
        annot.set_colors(stroke=color)
        annot.set_info(content=f"{annot.type[1]} comment", title="Reader")
        annot.set_opacity(0.5)
        annot.update()
    note = page.add_text_annot((300, 300), "a note", icon="Comment")
    note.set_info(title="Reader")
    note.update()
    square = page.add_rect_annot(fitz.Rect(100, 400, 180, 440))
    square.set_colors(stroke=(0, 0, 1), fill=(1, 1, 0))
    square.set_border(width=3, dashes=[2, 2])
    square.set_info(content="box")
    square.update()
    line = page.add_line_annot((50, 500), (150, 540))
    line.set_line_ends(fitz.PDF_ANNOT_LE_OPEN_ARROW, fitz.PDF_ANNOT_LE_NONE)
    line.update()
    ink = page.add_ink_annot([[(300, 500), (320, 530), (340, 500)]])
    ink.update()
    page.add_freetext_annot(fitz.Rect(300, 100, 420, 150), "free text")
    page.insert_link(
        {"kind": fitz.LINK_URI, "from": fitz.Rect(72, 90, 120, 105), "uri": "http://x"}
    )
    out = tmp_path / "source_annotated.pdf"
    doc.save(str(out))
    doc.close()
    return out


def _place_all(items, target: Path):
    placements = {}
    for item in items:
        if not item.supported:
            continue
        if item.kind == "markup":
            suggestion = suggest_exact_placement(item, WordIndex(target))
            assert suggestion is not None
            placements[item.item_id] = suggestion
        else:
            placements[item.item_id] = Placement(
                item.item_id, 0, point=(item.rect[0] + 10, item.rect[1] + 20)
            )
    return placements


def test_enumerates_every_annotation_and_flags_unsupported(tmp_path: Path) -> None:
    items = enumerate_annotations(_source(tmp_path))

    by_type = {}
    for item in items:
        by_type.setdefault(item.subtype, []).append(item)
    assert {"Highlight", "Underline", "StrikeOut", "Squiggly", "Text", "Square"} <= set(
        by_type
    )
    assert {"Line", "Ink", "FreeText", "Link", "Popup"} <= set(by_type)
    assert not by_type["FreeText"][0].supported
    assert by_type["FreeText"][0].needs_waiver
    assert not by_type["Link"][0].supported
    assert by_type["Popup"][0].companion and not by_type["Popup"][0].needs_waiver
    assert by_type["Highlight"][0].text == "beta gamma"


def test_exact_unique_text_suggests_but_ambiguous_or_changed_text_does_not(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    highlight = next(
        i for i in enumerate_annotations(source) if i.subtype == "Highlight"
    )
    same = _pdf(tmp_path / "same.pdf", TEXT, indent=90)
    changed = _pdf(tmp_path / "changed.pdf", ["alpha beta, gamma delta"])
    twice = _pdf(tmp_path / "twice.pdf", ["beta gamma", "beta gamma"])

    assert suggest_exact_placement(highlight, WordIndex(same)) is not None
    assert suggest_exact_placement(highlight, WordIndex(changed)) is None
    assert suggest_exact_placement(highlight, WordIndex(twice)) is None


def test_merge_preserves_styles_and_comments_and_is_verified(tmp_path: Path) -> None:
    source = _source(tmp_path)
    target = _pdf(tmp_path / "target.pdf", TEXT, indent=90)
    target_bytes, source_bytes = target.read_bytes(), source.read_bytes()
    items = enumerate_annotations(source)
    waived = {i.item_id for i in items if i.needs_waiver}
    output = tmp_path / "merged.pdf"

    report = build_merged_copy(
        target, source, items, _place_all(items, target), waived, output
    )

    assert report.complete and report.verified
    assert len(report.transferred) == 8
    assert target.read_bytes() == target_bytes
    assert source.read_bytes() == source_bytes
    doc = fitz.open(str(output))
    page = doc[0]
    found = {a.type[1]: a for a in page.annots()}
    square_dashes = found["Square"].border["dashes"]
    assert found["Highlight"].info["content"] == "Highlight comment"
    assert found["Highlight"].colors["stroke"] == pytest.approx([1, 0, 0])
    assert found["Highlight"].opacity == pytest.approx(0.5)
    assert found["Squiggly"].colors["stroke"] == pytest.approx([0.5, 0, 0.5])
    assert found["Text"].info["name"] == "Comment"
    assert found["Square"].colors["fill"] == pytest.approx([1, 1, 0])
    assert square_dashes == (2, 2)
    assert tuple(found["Line"].line_ends) == (fitz.PDF_ANNOT_LE_OPEN_ARROW, 0)
    doc.close()


def test_unwaived_unsupported_items_make_the_merge_partial(tmp_path: Path) -> None:
    source = _source(tmp_path)
    target = _pdf(tmp_path / "target.pdf", TEXT)
    items = enumerate_annotations(source)
    output = tmp_path / "merged.pdf"

    report = build_merged_copy(
        target, source, items, _place_all(items, target), set(), output
    )

    assert not report.complete
    assert len(report.unresolved) == 2  # FreeText and Link
    assert "not a full consolidation" in report.summary()


def test_cannot_waive_supported_annotation(tmp_path: Path) -> None:
    source = _source(tmp_path)
    target = _pdf(tmp_path / "target.pdf", TEXT)
    items = enumerate_annotations(source)
    supported = next(i for i in items if i.supported)

    with pytest.raises(AnnotationMergeError, match="waived"):
        build_merged_copy(
            target, source, items, {}, {supported.item_id}, tmp_path / "out.pdf"
        )
    assert not (tmp_path / "out.pdf").exists()


def test_never_overwrites_existing_output(tmp_path: Path) -> None:
    source = _source(tmp_path)
    target = _pdf(tmp_path / "target.pdf", TEXT)
    existing = tmp_path / "exists.pdf"
    existing.write_bytes(b"keep me")

    with pytest.raises(AnnotationMergeError, match="already exists"):
        build_merged_copy(
            target, source, enumerate_annotations(source), {}, set(), existing
        )
    assert existing.read_bytes() == b"keep me"


def test_repeat_merge_does_not_duplicate_annotations(tmp_path: Path) -> None:
    source = _source(tmp_path)
    target = _pdf(tmp_path / "target.pdf", TEXT)
    items = enumerate_annotations(source)
    waived = {i.item_id for i in items if i.needs_waiver}
    first = tmp_path / "first.pdf"
    build_merged_copy(target, source, items, _place_all(items, target), waived, first)

    second = tmp_path / "second.pdf"
    report = build_merged_copy(
        first, source, items, _place_all(items, first), waived, second
    )

    assert report.transferred == []
    assert len(report.already_present) == 8
    assert len(enumerate_annotations(second)) == len(enumerate_annotations(first))


def test_geometry_outside_page_is_rejected(tmp_path: Path) -> None:
    source = _source(tmp_path)
    target = _pdf(tmp_path / "target.pdf", TEXT)
    items = enumerate_annotations(source)
    square = next(i for i in items if i.subtype == "Square")

    with pytest.raises(AnnotationMergeError, match="page edge"):
        build_merged_copy(
            target,
            source,
            items,
            {square.item_id: Placement(square.item_id, 0, point=(5000, 5000))},
            set(),
            tmp_path / "out.pdf",
        )


def test_changed_source_is_rejected(tmp_path: Path) -> None:
    source = _source(tmp_path)
    target = _pdf(tmp_path / "target.pdf", TEXT)
    items = enumerate_annotations(source)
    with source.open("ab") as handle:
        handle.write(b"\n%changed")

    with pytest.raises(AnnotationMergeError, match="changed"):
        build_merged_copy(target, source, items, {}, set(), tmp_path / "out.pdf")


def test_missing_pymupdf_gives_actionable_error(monkeypatch) -> None:
    import builtins

    from adsfolio.pdf import annotation_merge

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "fitz":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(
        annotation_merge.MissingDependencyError, match=r"adsfolio\[full\]"
    ):
        annotation_merge.import_fitz()


def test_existing_target_annotations_are_preserved_byte_for_byte(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    target = _pdf(tmp_path / "target.pdf", TEXT)
    doc = fitz.open(str(target))
    page = doc[0]
    own = page.add_highlight_annot(page.search_for("repeated", quads=True)[:1])
    own.set_info(content="mine", title="Me")
    own.update()
    xref = own.xref
    doc.save(str(tmp_path / "t2.pdf"))
    doc.close()
    target = tmp_path / "t2.pdf"
    items = enumerate_annotations(source)
    waived = {i.item_id for i in items if i.needs_waiver}
    output = tmp_path / "merged.pdf"

    build_merged_copy(target, source, items, _place_all(items, target), waived, output)

    before, after = fitz.open(str(target)), fitz.open(str(output))
    try:
        assert before.xref_object(xref) == after.xref_object(xref)
        assert after.xref_get_key(xref, "Contents")[1] == "mine"
    finally:
        before.close()
        after.close()


def test_markup_placement_must_be_finite_and_inside_page(tmp_path: Path) -> None:
    from adsfolio.pdf.annotation_merge import placement_error

    items = enumerate_annotations(_source(tmp_path))
    highlight = next(i for i in items if i.subtype == "Highlight")
    page = (0.0, 0.0, 612.0, 792.0)

    def check(rect):
        return placement_error(
            highlight, Placement(highlight.item_id, 0, rects=(rect,)), page
        )

    assert check((10, 10, 50, 20)) is None
    assert check((10, 10, float("nan"), 20))
    assert check((10, 10, 10, 20))
    assert check((-5, 10, 50, 20))
    assert check((10, 10, 900, 20))


def test_omitted_source_items_are_rejected(tmp_path: Path) -> None:
    source = _source(tmp_path)
    target = _pdf(tmp_path / "target.pdf", TEXT)
    items = enumerate_annotations(source)

    with pytest.raises(AnnotationMergeError, match="does not match"):
        build_merged_copy(target, source, items[1:], {}, set(), tmp_path / "o.pdf")


def test_rotated_cropped_target_uses_unrotated_page_frame(tmp_path: Path) -> None:
    source = _source(tmp_path)
    target = _pdf(tmp_path / "target.pdf", TEXT)
    doc = fitz.open(str(target))
    doc[0].set_cropbox(fitz.Rect(20, 30, 500, 700))
    doc[0].set_rotation(90)
    doc.save(str(tmp_path / "rotated.pdf"))
    doc.close()
    rotated = tmp_path / "rotated.pdf"
    items = enumerate_annotations(source)
    markup = [i for i in items if i.kind == "markup"]
    placements = _place_all(markup, rotated)
    waived = {i.item_id for i in items if i.needs_waiver}
    output = tmp_path / "merged.pdf"

    report = build_merged_copy(rotated, source, items, placements, waived, output)

    assert report.verified and len(report.transferred) == 4
    assert not report.complete  # note and shapes were not placed
    merged = fitz.open(str(output))
    page = merged[0]
    assert page.rotation == 90
    word = next(w for w in page.get_text("words") if w[4] == "beta")
    highlight = next(a for a in page.annots() if a.type[1] == "Highlight")
    assert highlight.rect.intersects(fitz.Rect(*word[:4]))
    merged.close()
    # A note cannot be moved to a page with a different rotation unchanged.
    note = next(i for i in items if i.subtype == "Text")
    with pytest.raises(AnnotationMergeError, match="same rotation"):
        build_merged_copy(
            rotated,
            source,
            items,
            {note.item_id: Placement(note.item_id, 0, point=(100, 100))},
            set(),
            tmp_path / "n.pdf",
        )
