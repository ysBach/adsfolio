"""Find annotation locations locally for explicit review.

Text similarity does not establish the same scientific meaning. This module
reads PDF text and coordinates without accessing credentials or a network.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .annotation_merge import (
    AnnotationItem,
    Placement,
    WordIndex,
    _words_in,
    line_rects,
)

Word = tuple[Any, ...]
Edit = tuple[str, str, str]


@dataclass(frozen=True)
class TextMatch:
    """An unaccepted placement with its text evidence and alignment edits."""

    placement: Placement
    text: str
    method: str
    edits: int | None = None
    source_words: int | None = None
    context: str = ""
    matched_words: int | None = None
    source_text: str = ""
    operations: tuple[Edit, ...] = ()


def _letters(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKC", text).casefold() if c.isalnum()
    )


def _word_key(text: str, *, strict: bool = False) -> str:
    """Normalize typography while keeping scientific symbols and numbers."""
    text = (
        unicodedata.normalize("NFC", text)
        if strict
        else unicodedata.normalize("NFKC", text).casefold()
    )
    text = re.sub(r"(?<=[^\W\d_])[-\u2010\u00ad](?=[^\W\d_])", "", text)
    return text.strip(".,;:!?\"'“”‘’()[]{}")


class TextIndex(WordIndex):
    """Index PDF words and normalized character-to-word coordinates."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.normalized: list[tuple[str, list[int], set[int], set[int]]] = []
        for words in self.pages:
            characters, positions, starts, ends = [], [], set(), set()
            for word_number, word in enumerate(words):
                text = _letters(str(word[4]))
                if text:
                    starts.add(len(positions))
                    characters.append(text)
                    positions.extend([word_number] * len(text))
                    ends.add(len(positions))
            self.normalized.append(("".join(characters), positions, starts, ends))


def text_matches(
    item: AnnotationItem,
    index: TextIndex,
    query: str | None = None,
    offset: tuple[float, float] = (0.0, 0.0),
    source_words: list[Word] | None = None,
) -> list[TextMatch]:
    """Find all whole-word occurrences, allowing typography differences.

    Repeated occurrences remain separate candidates. Symbol changes are
    labelled explicitly. Every occurrence still requires visual review.
    """
    text = item.text if query is None else query
    needle = _letters(text)
    if not needle or not item.supported:
        return []
    matches = []
    query_words = (
        source_words
        if source_words is not None
        else [(0, 0, 0, 0, word, 0, 0) for word in text.split()]
    )
    query_tokens, _ = _alignment_words(query_words, strict=True)
    for page, (haystack, positions, starts, ends) in enumerate(index.normalized):
        begin = haystack.find(needle)
        while begin >= 0:
            end = begin + len(needle)
            if begin in starts and end in ends:
                words = index.pages[page][positions[begin] : positions[end - 1] + 1]
                found = " ".join(str(w[4]) for w in words)
                if found.split() == text.split():
                    method = "Exact text"
                elif _alignment_words(words, strict=True)[0] == query_tokens:
                    method = "Typography differs"
                else:
                    method = "Characters differ — check numbers and symbols"
                placement = Placement(item.item_id, page, rects=line_rects(words))
                if item.kind == "point":
                    placement = Placement(
                        item.item_id,
                        page,
                        point=(words[0][0] + offset[0], words[0][1] + offset[1]),
                    )
                matches.append(TextMatch(placement, found, method, source_text=text))
            begin = haystack.find(needle, begin + 1)
    return matches


def source_evidence(item: AnnotationItem, index: TextIndex) -> dict[str, Any]:
    """Read the source text anchor and its original PDF word coordinates.

    Notes and shapes use the nearest block, measured by Euclidean rectangle
    gap. Markup follows the stored quad order, as does its displayed text.
    """
    page_words = index.pages[item.page]
    blocks: dict[int, list[Word]] = {}
    for word in page_words:
        blocks.setdefault(int(word[5]), []).append(word)
    if not blocks:
        return {
            "marked_text": item.text,
            "context": "",
            "offset": (0.0, 0.0),
            "words": [],
        }

    def gap(words: list[Word]) -> float:
        left, top = min(w[0] for w in words), min(w[1] for w in words)
        right, bottom = max(w[2] for w in words), max(w[3] for w in words)
        return math.hypot(
            max(left - item.rect[2], item.rect[0] - right, 0),
            max(top - item.rect[3], item.rect[1] - bottom, 0),
        )

    block = min(blocks.values(), key=gap)
    words = block
    if item.kind == "markup":
        words = []
        for quad in item.quads:
            rect = (
                min(p[0] for p in quad),
                min(p[1] for p in quad),
                max(p[0] for p in quad),
                max(p[1] for p in quad),
            )
            words.extend(_words_in(page_words, rect))
    return {
        "marked_text": " ".join(str(w[4]) for w in words) or item.text,
        "context": " ".join(str(w[4]) for w in block),
        "offset": (item.rect[0] - block[0][0], item.rect[1] - block[0][1]),
        "words": words,
    }


def _alignment_words(
    words: list[Word], *, strict: bool = False
) -> tuple[list[str], list[tuple[int, int]]]:
    """Normalize tokens and retain their original PDF word spans."""
    tokens: list[str] = []
    spans: list[tuple[int, int]] = []
    for number, word in enumerate(words):
        text = _word_key(str(word[4]), strict=strict)
        if not text:
            continue
        previous = words[spans[-1][1] - 1] if spans else None
        if (
            tokens
            and previous is not None
            and str(previous[4]).endswith(("-", "‐", "\u00ad"))
            and str(previous[4])[-2:-1].isalpha()
            and text[0].isalpha()
            and previous[5] == word[5]
            and previous[6] != word[6]
        ):
            tokens[-1] = _word_key(tokens[-1][:-1] + text, strict=strict)
            spans[-1] = (spans[-1][0], number + 1)
        else:
            tokens.append(text)
            spans.append((number, number + 1))
    return tokens, spans


def _closest_spans(
    query: list[str], tokens: list[str], cancelled: Callable[[], bool]
) -> tuple[int, list[tuple[int, int]]]:
    """Minimize word edits, then maximize matched words in target substrings.

    A match costs -1. Each edit costs ``len(query) + 1``, so one fewer edit
    always wins over any match-count difference. Keep minimal tied spans:
    surrounding unmatched words stay outside the suggested annotation.
    """
    size, base = len(query), len(query) + 1
    previous = [0] * (len(tokens) + 1)
    previous_starts = list(range(len(tokens) + 1))
    for row, word in enumerate(query, 1):
        if cancelled():
            return size * base, []
        current, starts = [row * base], [0]
        for column, target_word in enumerate(tokens, 1):
            options = (
                (
                    previous[column - 1] + (-1 if word == target_word else base),
                    previous_starts[column - 1],
                ),
                (previous[column] + base, previous_starts[column]),
                (current[column - 1] + base, starts[column - 1]),
            )
            cost, begin = min(options, key=lambda value: (value[0], -value[1]))
            current.append(cost)
            starts.append(begin)
        previous, previous_starts = current, starts
    best = min(previous, default=size * base)
    if best >= size * base:
        return size * base, []
    found = {
        (previous_starts[end], end)
        for end, cost in enumerate(previous)
        if cost == best and previous_starts[end] < end
    }
    minimal, earliest_end = [], len(tokens) + 1
    for begin, end in sorted(found, key=lambda span: (-span[0], span[1])):
        if end < earliest_end:
            minimal.append((begin, end))
            earliest_end = end
    return best, sorted(minimal)


def _trace_alignment(
    query: list[str], target: list[str]
) -> list[tuple[int | None, int | None]]:
    """Recover matched and edited word pairs in a selected candidate window."""
    base = len(query) + 1
    costs = [[column * base for column in range(len(target) + 1)]]
    for row, word in enumerate(query, 1):
        current = [row * base]
        for column, other in enumerate(target, 1):
            current.append(
                min(
                    costs[-1][column - 1] + (-1 if word == other else base),
                    costs[-1][column] + base,
                    current[-1] + base,
                )
            )
        costs.append(current)
    row, column = len(query), len(target)
    steps = []
    while row or column:
        cost = costs[row][column]
        if (
            row
            and column
            and cost
            == costs[row - 1][column - 1]
            + (-1 if query[row - 1] == target[column - 1] else base)
        ):
            steps.append((row - 1, column - 1))
            row, column = row - 1, column - 1
        elif row and cost == costs[row - 1][column] + base:
            steps.append((row - 1, None))
            row -= 1
        else:
            steps.append((None, column - 1))
            column -= 1
    return list(reversed(steps))


def wording_matches(
    items: list[AnnotationItem],
    source: TextIndex,
    target: TextIndex,
    cancelled: Callable[[], bool] = lambda: False,
) -> dict[str, list[TextMatch]]:
    """Find each page's closest wording locally for human review.

    Sort by fewest word edits, then most matched words. Keep tied minimal
    spans on every page. No confidence threshold accepts a placement. If no
    words match, leave the annotation unresolved. Meaning can still differ.
    """
    pages = [_alignment_words(words) for words in target.pages]
    result: dict[str, list[TextMatch]] = {}
    for item in items:
        if cancelled():
            break
        if not item.supported:
            continue
        evidence = source_evidence(item, source)
        source_words = evidence["words"] or [
            (0, 0, 0, 0, word, 0, 0) for word in item.text.split()
        ]
        query, query_spans = _alignment_words(source_words)
        if not query:
            continue
        candidates = []
        for page, (tokens, spans) in enumerate(pages):
            if cancelled():
                return result
            score, found = _closest_spans(query, tokens, cancelled)
            edits = (score + len(query)) // (len(query) + 1)
            matched = edits * (len(query) + 1) - score
            for begin, end in found:
                words = target.pages[page][spans[begin][0] : spans[end - 1][1]]
                steps = _trace_alignment(query, tokens[begin:end])
                operations: list[Edit] = []
                pairs = []
                for left, right in steps:
                    source_text = (
                        " ".join(
                            str(w[4]) for w in source_words[slice(*query_spans[left])]
                        )
                        if left is not None
                        else ""
                    )
                    target_text = (
                        " ".join(
                            str(w[4])
                            for w in target.pages[page][slice(*spans[begin + right])]
                        )
                        if right is not None
                        else ""
                    )
                    equal = (
                        left is not None
                        and right is not None
                        and query[left] == tokens[begin + right]
                    )
                    tag = (
                        "equal"
                        if equal and source_text == target_text
                        else "format"
                        if equal
                        else "delete"
                        if right is None
                        else "insert"
                        if left is None
                        else "replace"
                    )
                    operations.append((tag, source_text, target_text))
                    if equal:
                        pairs.append((query_spans[left][0], spans[begin + right][0]))
                placement = Placement(item.item_id, page, rects=line_rects(words))
                if item.kind == "point" and pairs:
                    sx, sy = item.rect[:2]
                    left, right = min(
                        pairs,
                        key=lambda pair: math.hypot(
                            source_words[pair[0]][0] - sx, source_words[pair[0]][1] - sy
                        ),
                    )
                    anchor, destination = source_words[left], target.pages[page][right]
                    placement = Placement(
                        item.item_id,
                        page,
                        point=(
                            destination[0] + sx - anchor[0],
                            destination[1] + sy - anchor[1],
                        ),
                    )
                blocks = {word[5] for word in words}
                context = " ".join(
                    str(w[4]) for w in target.pages[page] if w[5] in blocks
                )
                candidates.append(
                    TextMatch(
                        placement,
                        " ".join(str(w[4]) for w in words),
                        "Closest wording — review text",
                        edits,
                        len(query),
                        context,
                        matched,
                        evidence["marked_text"],
                        tuple(operations),
                    )
                )
        if candidates:
            result[item.item_id] = sorted(
                candidates,
                key=lambda match: (
                    match.edits,
                    -match.matched_words,
                    match.placement.page,
                ),
            )
    return result
