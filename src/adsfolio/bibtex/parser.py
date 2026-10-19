"""
BibTeX parsing utilities.

"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyparsing as pp
from bibtexparser.bibdatabase import (
    BibDatabase,
    BibDataString,
    BibDataStringExpression,
)
from bibtexparser.bparser import BibTexParser
from pyparsing import ParseBaseException

__all__ = ["extract_cite_keys", "make_rawfile", "parse_bibtex_entries"]


@dataclass
class BibEntry:
    """Parsed BibTeX entry."""

    bibcode: str
    entry_type: str
    authors: list[str]
    title: str
    year: str
    journal: str | None = None
    volume: str | None = None
    pages: str | None = None
    raw: str = ""


def extract_cite_keys(tex_file: str | Path) -> list[str]:
    """
    Extract citation keys from a LaTeX file.

    Parameters
    ----------
    tex_file : str or Path
        Path to the LaTeX file.

    Returns
    -------
    list[str]
        List of unique bibcode-style citation keys (19 characters, year prefix).
    """
    path = Path(tex_file)
    if not path.exists():
        return []

    contents = path.read_text(encoding="utf-8")

    # Match \cite{...}, \citep{...}, \citet{...} but ignore commented lines
    # Also handles multiple keys in single cite command
    rx = re.compile(r"""(?<!\\)%.+|(\\\w*cite\w*\{([^{}]+)\})""")

    keys = []
    for match in rx.finditer(contents):
        if match.group(2):
            key_list = match.group(2).split(",")
            for key in key_list:
                key = key.strip()
                # Check if it looks like a bibcode (19 chars, starts with 4 digits)
                if len(key) == 19 and key[:4].isdigit():
                    if key not in keys:
                        keys.append(key)

    return keys


def _resolve_bib_value(
    value: Any,
    database: BibDatabase,
    seen: frozenset[str] = frozenset(),
) -> str:
    """Resolve a parsed BibTeX value to text, preserving undefined macro names."""
    if isinstance(value, BibDataString):
        if value.name in seen or value.name not in database.strings:
            return str(value.name)
        return _resolve_bib_value(
            database.strings[value.name],
            database,
            seen | {value.name},
        )
    if isinstance(value, BibDataStringExpression):
        return "".join(_resolve_bib_value(part, database, seen) for part in value.expr)
    return str(value)


def _split_bibtex_authors(value: str) -> list[str]:
    """Split a BibTeX author field on top-level ``and`` tokens."""
    names: list[str] = []
    depth = 0
    start = 0
    for match in re.finditer(r"[{}]|\band\b", value, flags=re.IGNORECASE):
        token = match.group()
        if token == "{":
            depth += 1
        elif token == "}":
            depth = max(0, depth - 1)
        elif (
            depth == 0
            and match.start() > 0
            and match.end() < len(value)
            and value[match.start() - 1 : match.start()].isspace()
            and value[match.end() : match.end() + 1].isspace()
        ):
            candidate = value[start : match.start()].strip()
            if candidate:
                names.append(candidate)
            start = match.end()
    tail = value[start:].strip()
    if tail:
        names.append(tail)
    return names


def _is_single_brace_group(text: str) -> bool:
    """Return whether `text` is enclosed by one balanced outer brace pair."""
    if len(text) < 2 or text[0] != "{" or text[-1] != "}":
        return False
    depth = 0
    for index, char in enumerate(text):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and index < len(text) - 1:
                return False
    return depth == 0


def _strip_outer_braces(text: str) -> str:
    """Peel balanced outer brace pairs from `text`."""
    cleaned = text.strip()
    while _is_single_brace_group(cleaned):
        cleaned = cleaned[1:-1].strip()
    return cleaned


def _extract_author_surname(author: str) -> str:
    """Extract the surname or corporate name from one BibTeX author token."""
    token = author.strip()
    if not token:
        return ""
    depth = 0
    for index, char in enumerate(token):
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            return _strip_outer_braces(token[:index])

    if _is_single_brace_group(token):
        return _strip_outer_braces(token)

    parts: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(token):
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        elif char.isspace() and depth == 0:
            if index > start:
                parts.append(token[start:index])
            start = index + 1
    if start < len(token):
        parts.append(token[start:])
    last = parts[-1] if parts else token
    return _strip_outer_braces(last)


def parse_bibtex_entries(bibtex_text: str) -> list[BibEntry]:
    """
    Parse BibTeX text into structured entries.

    Parameters
    ----------
    bibtex_text : str
        Raw BibTeX content.

    Returns
    -------
    list[BibEntry]
        Parsed BibTeX entries.
    """
    if not bibtex_text or not bibtex_text.strip():
        return []

    parser = BibTexParser(
        ignore_nonstandard_types=False,
        common_strings=True,
        interpolate_strings=False,
    )
    raw_slices: list[str] = []
    located_entry = pp.Located(parser._expr.entry)

    def _capture_raw(source: str, _loc: int, tokens: pp.ParseResults) -> None:
        if len(parser.bib_database.entries) > len(raw_slices):
            raw_slices.append(source[tokens.locn_start : tokens.locn_end])

    located_entry.add_parse_action(_capture_raw)
    parser._expr.main_expression = pp.ZeroOrMore(
        parser._expr.string_def
        | parser._expr.preamble_decl
        | parser._expr.explicit_comment
        | located_entry
        | parser._expr.implicit_comment
    )
    parser._expr.main_expression.parse_with_tabs()
    try:
        database = parser.parse(bibtex_text)
    except ParseBaseException:
        return []

    entries: list[BibEntry] = []
    for index, record in enumerate(database.entries):
        fields = {
            key: _resolve_bib_value(value, database)
            for key, value in record.items()
            if key not in {"ID", "ENTRYTYPE"}
        }
        author_text = fields.get("author", "")
        authors = [
            surname
            for token in _split_bibtex_authors(author_text)
            if (surname := _extract_author_surname(token))
        ]
        raw_text = raw_slices[index] if index < len(raw_slices) else ""
        entries.append(
            BibEntry(
                bibcode=str(record.get("ID", "")).strip(),
                entry_type=str(record.get("ENTRYTYPE", "")).lower(),
                authors=authors,
                title=fields.get("title", ""),
                year=fields.get("year", ""),
                journal=fields.get("journal"),
                volume=fields.get("volume"),
                pages=fields.get("pages"),
                raw=raw_text,
            )
        )

    return entries


def make_rawfile(bibtex_text: str, output_file: str | Path) -> None:
    """
    Create a summary file from BibTeX.

    Write one line per entry whose key is a 19-character bibcode::

        bibcode  # author_summary || title

    Entries are read with `parse_bibtex_entries`, so single-line entries,
    nested braces, and quoted or multiline fields are supported. The author
    summary is the sole surname, surnames joined by ``+``, or the first
    surname plus ``+N`` when there are more than three authors.

    Parameters
    ----------
    bibtex_text : str
        BibTeX content.
    output_file : str or Path
        Output file path, written as UTF-8.
    """
    lines = []
    for entry in parse_bibtex_entries(bibtex_text):
        if len(entry.bibcode) != 19:
            continue
        surnames = entry.authors
        if len(surnames) > 3:
            author_summary = f"{surnames[0]}+{len(surnames) - 1}"
        else:
            author_summary = "+".join(surnames)
        title = " ".join(_strip_outer_braces(entry.title).split())
        lines.append(f"{entry.bibcode}  # {author_summary} || {title}")

    Path(output_file).write_text("\n".join(lines), encoding="utf-8")
