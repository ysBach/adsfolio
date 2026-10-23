"""Behavior tests for local annotation text matching."""

from __future__ import annotations

import socket
from dataclasses import replace
from pathlib import Path

import pytest

from adsfolio.pdf.annotation_matching import (
    TextIndex,
    _closest_spans,
    source_evidence,
    text_matches,
    wording_matches,
)
from adsfolio.pdf.annotation_merge import (
    AnnotationItem,
    build_merged_copy,
    enumerate_annotations,
)

fitz = pytest.importorskip("fitz")


def _pdf(path: Path, pages: list[str], highlighted: str | None = None) -> Path:
    with fitz.open() as document:
        for text in pages:
            page = document.new_page()
            page.insert_text((72, 100), text)
            if highlighted:
                rects = page.search_for(highlighted)
                if rects:
                    page.add_highlight_annot(rects).update()
        document.save(path)
    return path


def _item(
    tmp_path: Path,
    text: str = "The original dust claim.",
    highlighted: str = "original dust",
) -> tuple[Path, AnnotationItem]:
    path = _pdf(tmp_path / "source.pdf", [text], highlighted)
    return path, next(i for i in enumerate_annotations(path) if i.supported)


@pytest.fixture(autouse=True)
def offline_matching(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every matching test with network connections blocked."""

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Annotation matching attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def test_text_candidates_find_typography_changes_and_repeated_occurrences(
    tmp_path: Path,
) -> None:
    _, item = _item(tmp_path)
    target = _pdf(
        tmp_path / "target.pdf", ["orig-\ninal dust claim.", "original dust claim."]
    )
    matches = text_matches(item, TextIndex(target))
    assert [m.placement.page for m in matches] == [0, 1]
    assert all(m.placement.rects for m in matches)
    assert matches[0].method == "Typography differs"
    assert matches[1].method == "Exact text"
    assert not text_matches(replace(item, text="ori"), TextIndex(target))


def test_local_wording_search_finds_moved_edited_text_without_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, item = _item(
        tmp_path, "The dust contains warm silicates.", "dust contains warm silicates"
    )
    target = _pdf(
        tmp_path / "target.pdf",
        ["Ice sublimates rapidly.", "The dust contains cool silicates."],
    )
    monkeypatch.setattr(
        Path,
        "home",
        lambda: (_ for _ in ()).throw(
            AssertionError("Matching must not read account credentials")
        ),
    )
    matches = wording_matches([item], TextIndex(source), TextIndex(target))[
        item.item_id
    ]
    assert len(matches) == 1
    assert matches[0].placement.page == 1
    assert matches[0].edits == 1
    assert matches[0].source_words == 4
    assert matches[0].text == "dust contains cool silicates."
    assert matches[0].method == "Closest wording — review text"
    with fitz.open(target) as document:
        assert list(document[1].annots() or []) == []


def test_tied_locations_are_retained_for_review(tmp_path: Path) -> None:
    source, item = _item(
        tmp_path, "dust contains warm silicates", "dust contains warm silicates"
    )
    target = _pdf(
        tmp_path / "target.pdf",
        [
            "dust contains cold silicates\ndust contains cold silicates",
            "dust contains cool silicates",
        ],
    )
    matches = wording_matches([item], TextIndex(source), TextIndex(target))[
        item.item_id
    ]
    assert [m.placement.page for m in matches] == [0, 0, 1]
    assert [m.edits for m in matches] == [1, 1, 1]
    assert len({m.placement.rects for m in matches[:2]}) == 2


@pytest.mark.parametrize(
    "source_text,target_text",
    [
        ("alpha beta gamma", "delta epsilon zeta"),
        ("alpha", "omega"),
        ("", "alpha"),
        ("alpha", ""),
    ],
)
def test_no_shared_words_or_empty_inputs_stay_unresolved(
    tmp_path: Path, source_text: str, target_text: str
) -> None:
    source, item = _item(tmp_path)
    target = _pdf(tmp_path / "target.pdf", [target_text])
    item = replace(item, text=source_text, quads=())
    assert wording_matches([item], TextIndex(source), TextIndex(target)) == {}


def test_cancel_stops_local_matching(tmp_path: Path) -> None:
    source, item = _item(tmp_path)
    target = _pdf(tmp_path / "target.pdf", ["original dust"])
    assert (
        wording_matches([item], TextIndex(source), TextIndex(target), lambda: True)
        == {}
    )


def test_changed_negation_and_quantity_are_presented_as_edits(tmp_path: Path) -> None:
    text = "dust is not detected at 183 K"
    source, item = _item(tmp_path, text, text)
    target = _pdf(tmp_path / "target.pdf", ["dust is detected at 193 K"])
    match = wording_matches([item], TextIndex(source), TextIndex(target))[item.item_id][
        0
    ]
    assert match.edits == 2
    assert "193" in match.text
    assert "not" not in match.text
    assert "review" in match.method


def test_line_wrap_join_uses_source_and_target_pdf_words(tmp_path: Path) -> None:
    source = _pdf(tmp_path / "source.pdf", ["The interstel-\nlar dust is warm"])
    with fitz.open(source) as document:
        page = document[0]
        quads = [
            fitz.Rect(*w[:4]).quad for w in page.get_text("words") if w[4] != "The"
        ]
        page.add_highlight_annot(quads).update()
        document.saveIncr()
    item = next(i for i in enumerate_annotations(source) if i.supported)
    target = _pdf(tmp_path / "target.pdf", ["interstellar dust is cold"])
    match = wording_matches([item], TextIndex(source), TextIndex(target))[item.item_id][
        0
    ]
    assert match.edits == 1
    assert "interstellar" in match.text
    assert "cold" in match.context


def test_note_text_anchor_preserves_its_offset(tmp_path: Path) -> None:
    source, item = _item(tmp_path)
    note = replace(item, subtype="Text", rect=(90, 120, 108, 138), text="")
    evidence = source_evidence(note, TextIndex(source))
    target = TextIndex(_pdf(tmp_path / "target.pdf", ["The original dust claim."]))
    matches = text_matches(note, target, evidence["marked_text"], evidence["offset"])
    assert matches[0].placement.point == pytest.approx(note.rect[:2])


def test_saved_local_placement_covers_candidate_words_and_preserves_originals(
    tmp_path: Path,
) -> None:
    source, item = _item(
        tmp_path, "The dust contains warm silicates.", "dust contains warm silicates"
    )
    target = _pdf(tmp_path / "target.pdf", ["The dust contains\ncool silicates."])
    source_bytes, target_bytes = source.read_bytes(), target.read_bytes()
    index = TextIndex(target)
    match = wording_matches([item], TextIndex(source), index)[item.item_id][0]
    output = tmp_path / "merged.pdf"
    report = build_merged_copy(
        target, source, [item], {item.item_id: match.placement}, set(), output
    )
    assert report.verified
    assert source.read_bytes() == source_bytes
    assert target.read_bytes() == target_bytes
    with fitz.open(output) as document:
        page = document[0]
        annotation = next(page.annots())
        vertices = annotation.vertices
        quads = [fitz.Quad(vertices[n : n + 4]) for n in range(0, len(vertices), 4)]
        for word in index.pages[0][1:]:
            centre = fitz.Point((word[0] + word[2]) / 2, (word[1] + word[3]) / 2)
            assert any(centre in quad.rect for quad in quads)


def test_substring_alignment_matches_exhaustive_span_ranking() -> None:
    from itertools import product

    def rank(left: list[str], right: list[str]) -> tuple[int, int]:
        previous = [(n, 0) for n in range(len(right) + 1)]
        for row, word in enumerate(left, 1):
            current = [(row, 0)]
            for column, other in enumerate(right, 1):
                edits, negative_matches = previous[column - 1]
                diagonal = (edits + (word != other), negative_matches - (word == other))
                deleted = (previous[column][0] + 1, previous[column][1])
                inserted = (current[-1][0] + 1, current[-1][1])
                current.append(min(diagonal, deleted, inserted))
            previous = current
        return previous[-1]

    for query_size, target_size in product(range(1, 4), repeat=2):
        for source in product(("a", "b"), repeat=query_size):
            for target in product(("a", "b"), repeat=target_size):
                query, words = list(source), list(target)
                spans = [
                    (begin, end)
                    for begin in range(len(words))
                    for end in range(begin + 1, len(words) + 1)
                ]
                optimum = min(
                    (len(query), 0),
                    *(rank(query, words[begin:end]) for begin, end in spans),
                )
                expected = {
                    span
                    for span in spans
                    if rank(query, words[slice(*span)]) == optimum
                }
                expected = {
                    span
                    for span in expected
                    if not any(
                        span != other and span[0] <= other[0] and other[1] <= span[1]
                        for other in expected
                    )
                }
                cost, found = _closest_spans(query, words, lambda: False)
                assert cost == optimum[0] * (len(query) + 1) + optimum[1]
                assert set(found) == (expected if optimum < (len(query), 0) else set())


def test_candidate_context_retains_a_changed_quantity_outside_the_span(
    tmp_path: Path,
) -> None:
    source, item = _item(
        tmp_path, "dust temperature is 183 K", "dust temperature is 183 K"
    )
    target = _pdf(tmp_path / "target.pdf", ["dust temperature is 193 K"])
    matches = wording_matches([item], TextIndex(source), TextIndex(target))[
        item.item_id
    ]
    assert all("193" in match.context for match in matches)


@pytest.mark.parametrize(
    "source,target,expected",
    [
        (
            "as a function of heliocentric distance",
            "we plot it versus heliocentric distance",
            ["heliocentric distance"],
        ),
        ("dust grains", "small dust silicate grains", ["dust silicate grains"]),
        (
            "the dust grains",
            "small dust silicate grains here",
            ["dust silicate grains"],
        ),
        (
            "flux decreases with distance",
            "flux decreases with radius and",
            ["flux decreases with"],
        ),
    ],
)
def test_matched_core_boundaries(source: str, target: str, expected: list[str]) -> None:
    words = target.split()
    _, spans = _closest_spans(source.split(), words, lambda: False)
    assert [" ".join(words[begin:end]) for begin, end in spans] == expected


def test_every_page_keeps_its_best_location(tmp_path: Path) -> None:
    source, item = _item(
        tmp_path, "dust contains warm silicates", "dust contains warm silicates"
    )
    target = _pdf(
        tmp_path / "target.pdf",
        ["dust contains cool silicates", "dust contains cool hydrated silicates"],
    )
    matches = wording_matches([item], TextIndex(source), TextIndex(target))[
        item.item_id
    ]
    assert {match.placement.page for match in matches} == {0, 1}
    assert matches[0].edits < next(
        match.edits for match in matches if match.placement.page == 1
    )
    for match in matches:
        assert (
            sum(
                tag in ("replace", "insert", "delete") for tag, _, _ in match.operations
            )
            == match.edits
        )
        assert (
            sum(tag in ("equal", "format") for tag, _, _ in match.operations)
            == match.matched_words
        )


def test_scientific_symbol_changes_are_explicit(tmp_path: Path) -> None:
    _, item = _item(tmp_path, "T < 100 K", "T < 100 K")
    target = _pdf(tmp_path / "target.pdf", ["T > 100 K"])
    matches = text_matches(item, TextIndex(target))
    assert matches and all("Characters differ" in match.method for match in matches)
    assert matches[0].source_text == "T < 100 K"
    assert matches[0].text == "T > 100 K"


def test_midline_and_wrapped_hyphens_have_the_same_word_key(tmp_path: Path) -> None:
    source, item = _item(tmp_path, "X-ray emission is warm", "X-ray emission is warm")
    target = _pdf(tmp_path / "target.pdf", ["X-\nray emission is warm"])
    matches = wording_matches([item], TextIndex(source), TextIndex(target))[
        item.item_id
    ]
    assert matches[0].edits == 0
    assert matches[0].matched_words == 4


def test_note_uses_a_matched_anchor_when_leading_words_change(tmp_path: Path) -> None:
    source = _pdf(tmp_path / "source.pdf", ["Old intro dust grains are warm"])
    target = _pdf(
        tmp_path / "target.pdf",
        ["Completely revised introduction dust grains are warm"],
    )
    with fitz.open(source) as document:
        page = document[0]
        dust = next(word for word in page.get_text("words") if word[4] == "dust")
        page.add_text_annot((dust[0], dust[1] + 25), "reader note").update()
        document.saveIncr()
    item = next(
        item for item in enumerate_annotations(source) if item.subtype == "Text"
    )
    match = wording_matches([item], TextIndex(source), TextIndex(target))[item.item_id][
        0
    ]
    with fitz.open(target) as document:
        target_dust = next(
            word for word in document[0].get_text("words") if word[4] == "dust"
        )
    assert match.placement.point == pytest.approx(
        (
            target_dust[0] + item.rect[0] - dust[0],
            target_dust[1] + item.rect[1] - dust[1],
        )
    )
    assert match.source_text != item.content


def test_markup_evidence_follows_annotation_quad_order(tmp_path: Path) -> None:
    source = _pdf(tmp_path / "source.pdf", ["alpha beta gamma delta"])
    with fitz.open(source) as document:
        page = document[0]
        words = page.get_text("words")
        page.add_highlight_annot(
            [fitz.Rect(*words[2][:4]).quad, fitz.Rect(*words[0][:4]).quad]
        ).update()
        document.saveIncr()
    item = next(item for item in enumerate_annotations(source) if item.supported)
    evidence = source_evidence(item, TextIndex(source))
    assert evidence["marked_text"] == item.text == "gamma alpha"
    assert [word[4] for word in evidence["words"]] == ["gamma", "alpha"]


@pytest.mark.parametrize(
    "original,revised", [("1.5 au", "15 au"), ("50% flux", "50 flux"), ("km/s", "km s")]
)
def test_changed_numbers_and_units_have_a_review_label(
    tmp_path: Path, original: str, revised: str
) -> None:
    _, item = _item(tmp_path, original, original)
    target = _pdf(tmp_path / "target.pdf", [revised])
    matches = text_matches(item, TextIndex(target))
    assert matches
    assert all(
        match.method == "Characters differ — check numbers and symbols"
        for match in matches
    )


def test_source_line_wrap_is_classified_as_typography(tmp_path: Path) -> None:
    source = _pdf(tmp_path / "source.pdf", ["photo-\nmetry measures flux"])
    with fitz.open(source) as document:
        page = document[0]
        words = page.get_text("words")
        page.add_highlight_annot(
            [fitz.Rect(*word[:4]).quad for word in words[:2]]
        ).update()
        document.saveIncr()
    item = next(item for item in enumerate_annotations(source) if item.supported)
    target = TextIndex(_pdf(tmp_path / "target.pdf", ["photometry measures flux"]))
    evidence = source_evidence(item, TextIndex(source))
    matches = text_matches(item, target, source_words=evidence["words"])
    assert matches[0].method == "Typography differs"


@pytest.mark.parametrize("original,revised", [("1 MJy", "1 mJy"), ("10²", "102")])
def test_unit_case_and_exponents_have_a_review_label(
    tmp_path: Path, original: str, revised: str
) -> None:
    _, item = _item(tmp_path, original, original)
    target = _pdf(tmp_path / "target.pdf", [revised])
    matches = text_matches(item, TextIndex(target))
    assert matches
    assert all(
        match.method == "Characters differ — check numbers and symbols"
        for match in matches
    )
