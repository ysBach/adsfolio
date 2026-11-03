"""Match library search text against catalog records.

The catalog SQL filter and the GUI's in-memory search use these functions,
so both return the same records.

A record's search text holds its relative path, the decoded values of its
metadata (keys excluded), and its content digest. Decoding matters: stored
JSON can hold escapes such as ``\\u00e9``, and these must match ``é``.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable, Collection, Iterator, Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

# Separates the searched fields. A match never crosses a separator.
FIELD_SEPARATOR = "\x00"
# The field that holds all search text: path, every metadata value, digest.
ALL_FIELDS = "*"
# Search fields and their labels. A query names a field as ``field:text``.
SEARCH_FIELDS: dict[str, str] = {
    "author": "Authors",
    "first_author": "First author",
    "title": "Title",
    "year": "Year",
    "journal": "Journal",
    "abstract": "Abstract",
    "bibcode": "Bibcode",
    "doi": "DOI",
    "identifier": "Identifiers",
    "filename": "Filename",
}
# Other accepted names for the fields above.
FIELD_ALIASES: dict[str, str] = {
    "authors": "author",
    "pub": "journal",
    "bibstem": "journal",
    "file": "filename",
    "arxiv": "identifier",
}
# Metadata keys whose values fill each field. `filename` uses the path.
_FIELD_KEYS: dict[str, tuple[str, ...]] = {
    "author": ("author",),
    "title": ("title",),
    "year": ("year",),
    "journal": ("pub", "bibstem"),
    "abstract": ("abstract",),
    "bibcode": ("bibcode", "alternate_bibcode"),
    "doi": ("doi",),
    "identifier": ("identifier",),
}


@dataclass(frozen=True)
class SearchTerm:
    """One part of a search query.

    `field` is a key of `SEARCH_FIELDS`, or `None` for a plain term. A plain
    term searches the default fields.
    """

    field: str | None
    text: str


def resolve_field(name: str) -> str | None:
    """Return the field key for a field name or alias, ignoring letter case."""
    key = name.casefold()
    key = FIELD_ALIASES.get(key, key)
    return key if key in SEARCH_FIELDS else None


@lru_cache(maxsize=256)
def parse_query(query: str) -> tuple[SearchTerm, ...]:
    """Split a query into terms that must all match.

    Spaces separate terms. Double quotes keep a phrase together, also after
    a field name: ``author:"Bach, Y."``. A missing closing quote ends the
    phrase at the end of the query. A prefix that is not a known field name,
    as in ``https://``, stays part of the text.
    """
    terms: list[SearchTerm] = []
    index = 0
    length = len(query)
    while index < length:
        if query[index].isspace():
            index += 1
            continue
        field: str | None = None
        match = re.match(r"([A-Za-z_]+):", query[index:])
        if match is not None and resolve_field(match.group(1)) is not None:
            field = resolve_field(match.group(1))
            index += match.end()
        if index < length and query[index] == '"':
            end = query.find('"', index + 1)
            end = length if end < 0 else end
            text = query[index + 1 : end]
            index = end + 1
        else:
            end = index
            while end < length and not query[end].isspace():
                end += 1
            text = query[index:end]
            index = end
        if text.strip() or field is not None:
            terms.append(SearchTerm(field, text.strip()))
    return tuple(terms)


def _values(value: Any) -> Iterator[str]:
    """Yield the scalar values of decoded JSON, without dictionary keys."""
    if isinstance(value, dict):
        for item in value.values():
            yield from _values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _values(item)
    elif value is not None:
        yield str(value)


def record_search_text(
    relative_path: str | None, metadata_json: str | None, sha256: str | None
) -> str:
    """Return the exact search text of one catalog location.

    Parameters
    ----------
    relative_path : str or None
        Path of the PDF relative to its library root.
    metadata_json : str or None
        Stored metadata JSON. Invalid JSON is searched as plain text.
    sha256 : str or None
        Current content digest.

    Returns
    -------
    str
        The fields and metadata values, joined by `FIELD_SEPARATOR`.
    """
    parts: list[str] = [relative_path or ""]
    if metadata_json:
        try:
            parts.extend(_values(json.loads(metadata_json)))
        except ValueError:
            parts.append(metadata_json)
    if sha256:
        parts.append(sha256)
    return FIELD_SEPARATOR.join(part.replace(FIELD_SEPARATOR, " ") for part in parts)


def record_search_document(
    relative_path: str | None, metadata_json: str | None, sha256: str | None
) -> dict[str, str]:
    """Return the exact search text of each field of one catalog location.

    The `ALL_FIELDS` entry equals `record_search_text`. Each other entry
    holds the values of that field, joined by `FIELD_SEPARATOR`.
    """
    metadata: Any = {}
    if metadata_json:
        try:
            metadata = json.loads(metadata_json)
        except ValueError:
            metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}

    def join(values: Iterator[str]) -> str:
        return FIELD_SEPARATOR.join(
            value.replace(FIELD_SEPARATOR, " ") for value in values
        )

    document = {
        field: join(value for key in keys for value in _values(metadata.get(key)))
        for field, keys in _FIELD_KEYS.items()
    }
    authors = metadata.get("author")
    first = authors[0] if isinstance(authors, list) and authors else authors
    document["first_author"] = join(_values(first))
    document["filename"] = (relative_path or "").replace(FIELD_SEPARATOR, " ")
    document[ALL_FIELDS] = record_search_text(relative_path, metadata_json, sha256)
    return document


def fold_document(document: Mapping[str, str]) -> dict[str, str]:
    """Return `document` with `fold_text` applied to every field."""
    return {field: fold_text(text) for field, text in document.items()}


def document_matches(
    document: Mapping[str, str],
    query: str,
    *,
    exact: bool = False,
    wildcards: bool = False,
    default_fields: Collection[str] = (),
) -> bool:
    """Return whether a search document matches every term of `query`.

    Parameters
    ----------
    document : Mapping[str, str]
        Output of `record_search_document`. When `exact` is false, pass the
        output of `fold_document` instead.
    query : str
        Search query. See `parse_query`.
    exact, wildcards : bool, optional
        Search options. See `search_matcher`.
    default_fields : Collection[str], optional
        Fields that plain terms search. Empty means all search text.
    """
    plain_fields = tuple(default_fields) or (ALL_FIELDS,)
    for term in parse_query(query):
        matcher = search_matcher(term.text, exact=exact, wildcards=wildcards)
        fields = (term.field,) if term.field is not None else plain_fields
        if not any(matcher(document.get(field, "")) for field in fields):
            return False
    return True


def fold_text(text: str) -> str:
    """Remove accents and letter case, so ``Bockelée`` becomes ``bockelee``.

    The text is decomposed (NFKD), combining marks are removed, and the
    result is case-folded.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return stripped.casefold()


def _pattern(needle: str, wildcards: bool) -> re.Pattern[str]:
    """Compile a normalized query into a regular expression."""
    if not wildcards:
        return re.compile(re.escape(needle))
    return re.compile(
        "".join(
            f"[^{FIELD_SEPARATOR}]*"
            if char == "%"
            else f"[^{FIELD_SEPARATOR}]"
            if char == "_"
            else re.escape(char)
            for char in needle
        ),
        re.DOTALL,
    )


@lru_cache(maxsize=4096)
def search_spans(
    text: str, query: str, *, exact: bool = False, wildcards: bool = False
) -> tuple[tuple[int, int], ...]:
    """Return the parts of `text` that the query matches, for highlighting.

    The rules are the same as for `search_matcher`. Each span is a
    ``(start, end)`` index pair into `text`, not into its folded form.
    Spans do not overlap. A wildcard match of no characters is skipped.

    Parameters
    ----------
    text : str
        Displayed text, for example a title or a filename.
    query : str
        Search text.
    exact, wildcards : bool, optional
        Search options. See `search_matcher`.
    """
    needle = query.strip()
    if not exact:
        needle = fold_text(needle)
    if not needle or not text:
        return ()
    if exact:
        haystack = text
        origin = list(range(len(text) + 1))
    else:
        # Fold each character separately and remember where its folded form
        # starts, so folded match positions map back to the displayed text.
        pieces = [fold_text(char) for char in text]
        haystack = "".join(pieces)
        origin = []
        for index, piece in enumerate(pieces):
            origin.extend([index] * len(piece))
        origin.append(len(text))
    spans: list[tuple[int, int]] = []
    for match in _pattern(needle, wildcards).finditer(haystack):
        if match.end() == match.start():
            continue
        start = origin[match.start()]
        # The end is after the character that holds the last folded letter.
        end = origin[match.end() - 1] + 1
        if spans and start < spans[-1][1]:
            spans[-1] = (spans[-1][0], max(end, spans[-1][1]))
        else:
            spans.append((start, end))
    return tuple(spans)


@lru_cache(maxsize=64)
def search_matcher(
    query: str, *, exact: bool = False, wildcards: bool = False
) -> Callable[[str], bool]:
    """Return a test for one search query.

    Apply the test to `record_search_text` output when `exact` is true, and
    to `fold_text` of that output when `exact` is false.

    Parameters
    ----------
    query : str
        Search text. Leading and trailing spaces are ignored. An empty query
        matches every record.
    exact : bool, optional
        Match accents and letter case exactly. By default both are ignored.
    wildcards : bool, optional
        Read ``%`` as any text and ``_`` as any one character. By default
        they match only themselves. A wildcard does not cross fields.

    Returns
    -------
    Callable[[str], bool]
        Function that returns `True` when a search text contains the query.
    """
    needle = query.strip()
    if not exact:
        needle = fold_text(needle)
    if not needle:
        return lambda _text: True
    if wildcards and ("%" in needle or "_" in needle):
        compiled = _pattern(needle, wildcards=True)
        return lambda text: compiled.search(text) is not None
    return lambda text: needle in text


def sql_search_match(
    relative_path: str | None,
    metadata_json: str | None,
    sha256: str | None,
    query: str,
    exact: int,
    wildcards: int,
    default_fields: str,
) -> int:
    """Apply `document_matches` to one SQL row. SQLite calls this function.

    `default_fields` is a comma-separated list of field keys.
    """
    document = record_search_document(relative_path, metadata_json, sha256)
    if not exact:
        document = fold_document(document)
    fields = tuple(field for field in default_fields.split(",") if field)
    return int(
        document_matches(
            document,
            query,
            exact=bool(exact),
            wildcards=bool(wildcards),
            default_fields=fields,
        )
    )
