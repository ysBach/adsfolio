"""
Repair malformed field values in ADS BibTeX exports.

ADS sometimes escapes math inside keyword or abstract strings
asymmetrically, e.g. ``2001 KX\\$\\_\\{76}``. BibTeX counts every brace,
escaped or not, so the ``\\{`` opens a group that never closes; BibTeX then
reads the following entry as part of this one, reports an error, and
latexmk stops before resolving any citation.

Only single-line field values whose braces do not balance are touched;
well-formed output passes through unchanged.
"""

import logging
import re

LOGGER = logging.getLogger(__name__)

__all__ = ["BIBTEX_FORMATS", "brace_balanced", "repair_bibtex", "repair_field_text"]

#: ADS export formats whose output is BibTeX.
BIBTEX_FORMATS = frozenset({"bibtex", "bibtexabs"})

_ENTRY_START = re.compile(r"^\s*@\s*(\w+)\s*\{\s*([^,\s]*)")
_FIELD_LINE = re.compile(
    r"^(?P<prefix>\s*[A-Za-z][\w-]*\s*=\s*)(?P<value>.*?)(?P<comma>,?)(?P<trail>\s*)$"
)
# ADS escapes these LaTeX specials in free-text fields.
_ADS_ESCAPE = re.compile(r"\\([$_{}^])")
_UNESCAPED_DOLLAR = re.compile(r"(?<!\\)\$")


def brace_balanced(text: str) -> bool:
    """
    Check braces the way BibTeX does (backslash escapes are ignored).

    Parameters
    ----------
    text : str
        Field content without its outer delimiters.

    Returns
    -------
    bool
        True if depth never drops below zero and ends at zero.
    """
    depth = 0
    for char in text:
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _drop_unmatched_braces(text: str) -> str:
    """Remove every brace that has no partner."""
    out: list[str] = []
    opened: list[int] = []
    for char in text:
        if char == "{":
            opened.append(len(out))
        elif char == "}":
            if not opened:
                continue
            opened.pop()
        out.append(char)
    for index in reversed(opened):
        del out[index]
    return "".join(out)


def repair_field_text(text: str) -> str:
    """
    Return a brace-balanced version of a field's content.

    Parameters
    ----------
    text : str
        Field content without its outer delimiters.

    Returns
    -------
    str
        `text` unchanged if already balanced. Otherwise ADS escapes of
        ``$ _ { } ^`` are undone, open groups are closed, and an unpaired
        ``$`` is closed. If that still does not balance, unmatched braces
        are dropped instead of closed.

    Examples
    --------
    >>> repair_field_text(r"28978 Ixion: 2001 KX\\$\\_\\{76")
    '28978 Ixion: 2001 KX$_{76}$'
    """
    if brace_balanced(text):
        return text
    fixed = _ADS_ESCAPE.sub(r"\1", text)
    depth = 0
    for char in fixed:
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                break
    if depth > 0:
        fixed += "}" * depth
    if not brace_balanced(fixed):
        fixed = _drop_unmatched_braces(fixed)
    if len(_UNESCAPED_DOLLAR.findall(fixed)) % 2:
        fixed += "$"
    return fixed


def _split_delimiters(value: str) -> tuple[str, str, str] | None:
    """Split ``{...}`` or ``"..."`` into (open, inner, close)."""
    if len(value) >= 2 and value[0] == "{" and value[-1] == "}":
        return "{", value[1:-1], "}"
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return '"', value[1:-1], '"'
    return None


def repair_bibtex(text: str) -> str:
    """
    Repair brace-unbalanced field values in ADS BibTeX output.

    Parameters
    ----------
    text : str
        BibTeX as returned by the ADS export service (one field per line).

    Returns
    -------
    str
        The same text with each unbalanced field value repaired by
        `repair_field_text`. A warning is logged per repaired field.
    """
    if "{" not in text:
        return text
    lines = text.splitlines(keepends=True)
    key = "?"
    in_entry = False
    changed = False
    for index, raw in enumerate(lines):
        body = raw.rstrip("\r\n")
        ending = raw[len(body) :]
        start = _ENTRY_START.match(body)
        if start:
            key = start.group(2) or "?"
            in_entry = True
            continue
        if not in_entry:
            continue
        if body.strip() == "}":
            in_entry = False
            continue
        match = _FIELD_LINE.match(body)
        if not match:
            continue
        parts = _split_delimiters(match.group("value"))
        if parts is None:
            continue
        opener, inner, closer = parts
        if brace_balanced(inner):
            continue
        repaired = repair_field_text(inner)
        field = match.group("prefix").split("=")[0].strip()
        LOGGER.warning(
            "Repaired unbalanced braces in %s.%s: %r -> %r",
            key,
            field,
            inner,
            repaired,
        )
        lines[index] = (
            match.group("prefix")
            + opener
            + repaired
            + closer
            + match.group("comma")
            + match.group("trail")
            + ending
        )
        changed = True
    return "".join(lines) if changed else text
