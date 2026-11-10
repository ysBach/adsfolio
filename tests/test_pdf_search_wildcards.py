"""Wildcard search stays equivalent to its former regex rules."""

from __future__ import annotations

import itertools
import re

from adsfolio.pdf.search import FIELD_SEPARATOR, search_matcher, search_spans


def _legacy_pattern(query: str) -> re.Pattern[str]:
    return re.compile(
        "".join(
            f"[^{FIELD_SEPARATOR}]*"
            if char == "%"
            else f"[^{FIELD_SEPARATOR}]"
            if char == "_"
            else re.escape(char)
            for char in query
        ),
        re.DOTALL,
    )


def _legacy_spans(text: str, query: str) -> tuple[tuple[int, int], ...]:
    spans = []
    for match in _legacy_pattern(query).finditer(text):
        if match.end() == match.start():
            continue
        if spans and match.start() < spans[-1][1]:
            spans[-1] = (spans[-1][0], max(match.end(), spans[-1][1]))
        else:
            spans.append((match.start(), match.end()))
    return tuple(spans)


def test_bounded_wildcards_match_legacy_regex_on_small_inputs() -> None:
    alphabet = ("a", "b", "_", "%", FIELD_SEPARATOR, "é")
    texts = (
        "".join(chars)
        for size in range(4)
        for chars in itertools.product(alphabet, repeat=size)
    )
    queries = (
        "".join(chars)
        for size in range(1, 4)
        for chars in itertools.product(alphabet, repeat=size)
    )
    query_list = tuple(queries)
    for text in texts:
        for query in query_list:
            query = query.strip()
            expected = _legacy_spans(text, query)
            assert search_spans(text, query, exact=True, wildcards=True) == expected, (
                repr(text),
                repr(query),
                expected,
            )
            assert search_matcher(query, exact=True, wildcards=True)(text) is (
                _legacy_pattern(query).search(text) is not None
            )


def test_repeated_prefix_percent_no_match_is_bounded() -> None:
    text = "a" * 50_000 + "c"
    assert not search_matcher("a%z", exact=True, wildcards=True)(text)
    assert search_spans(text, "a%z", exact=True, wildcards=True) == ()


def test_percent_stops_at_separators_and_highlights_greedy_match() -> None:
    text = f"abac{FIELD_SEPARATOR}abac"
    assert search_spans(text, "a%c", exact=True, wildcards=True) == (
        (0, 4),
        (5, 9),
    )


def test_percent_boolean_match_keeps_zero_width_regex_behavior() -> None:
    matcher = search_matcher("%", exact=True, wildcards=True)
    assert matcher("")
    assert matcher(FIELD_SEPARATOR)
