"""Citation keys for BibTeX export, for papers inside and outside ADS.

A `KeyPolicy` chooses one `KeyStyle` for ADS papers (papers with a bibcode)
and one for other papers. A key that the user typed, or that came with an
imported BibTeX entry, always wins over the style. `assign_keys` never makes
up a key: it reports papers without a key and keys that two papers share, so
that the caller can ask the user.
"""

from __future__ import annotations

import enum
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .citations import CitationFormatter, CitationStyle, CitationStyleError
from .formatting import html_to_text

__all__ = [
    "DEFAULT_KEY_TEMPLATE",
    "SCHOLAR_STOP_WORDS",
    "KeyAssignment",
    "KeyPolicy",
    "KeyStyle",
    "assign_keys",
    "citation_key",
    "sanitize_key",
    "scholar_key",
]

DEFAULT_KEY_TEMPLATE = "{first_author_initials}{year}"

# Leading title words that the Google Scholar style skips. ADSfolio uses this
# fixed list; it is not Google Scholar's own, unpublished list.
SCHOLAR_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "on",
        "of",
        "in",
        "for",
        "and",
        "to",
        "with",
        "at",
        "by",
        "from",
        "as",
        "into",
        "about",
        "is",
        "are",
        "its",
    }
)

# Characters that BibTeX and biber do not accept in a key.
_UNSAFE_KEY_CHARACTERS = re.compile(r"[\s,{}()\"'#%~\\=]+")


class KeyStyle(str, enum.Enum):
    """How a citation key is made when the paper has no key of its own."""

    BIBCODE = "bibcode"
    DOI = "doi"
    TEMPLATE = "template"
    SCHOLAR = "scholar"
    MANUAL = "manual"

    @property
    def label(self) -> str:
        """Return the name shown in the application."""
        return {
            KeyStyle.BIBCODE: "ADS bibcode",
            KeyStyle.DOI: "DOI",
            KeyStyle.TEMPLATE: "My template",
            KeyStyle.SCHOLAR: "Google Scholar style",
            KeyStyle.MANUAL: "Type each key",
        }[self]


@dataclass(frozen=True)
class KeyPolicy:
    """Key styles for ADS papers and for other papers.

    Parameters
    ----------
    ads : `KeyStyle`
        Style for papers with an ADS bibcode.
    other : `KeyStyle`
        Style for papers without a bibcode. `KeyStyle.BIBCODE` is not
        allowed here.
    template : `str`
        Template for `KeyStyle.TEMPLATE`, with the placeholders of custom
        citation styles, for example ``{first_author_initials}{year}``.
    author_format, max_authors, authors_when_truncated : `str`, `int`, `int`
        How ``{authors}`` writes the author list in a template. The defaults
        match the default Rename filenames: surname and initials, up to three
        authors, and the first author alone for a longer list.
    author_separator, author_truncation_suffix : `str`
        Text between authors, and after a shortened author list. Both default
        to ``+``, as in ``DoeJA+RoeA`` and ``DoeJA+``.
    """

    ads: KeyStyle = KeyStyle.BIBCODE
    other: KeyStyle = KeyStyle.DOI
    template: str = DEFAULT_KEY_TEMPLATE
    author_format: str = "surname_initials"
    max_authors: int = 3
    authors_when_truncated: int = 1
    author_separator: str = "+"
    author_truncation_suffix: str = "+"

    def __post_init__(self) -> None:
        object.__setattr__(self, "ads", KeyStyle(self.ads))
        object.__setattr__(self, "other", KeyStyle(self.other))
        if self.other is KeyStyle.BIBCODE:
            raise ValueError("Papers without a bibcode cannot use bibcode keys.")
        # Validates the template and author settings once, when the policy is made.
        _template_style(self)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> KeyPolicy:
        """Build a policy from saved settings, using defaults for gaps."""
        payload = payload or {}
        defaults = cls()

        def number(name: str) -> int:
            value = payload.get(name, getattr(defaults, name))
            return (
                value
                if isinstance(value, int) and not isinstance(value, bool)
                else getattr(defaults, name)
            )

        def text(name: str) -> str:
            value = payload.get(name, getattr(defaults, name))
            return value if isinstance(value, str) else getattr(defaults, name)

        return cls(
            ads=KeyStyle(str(payload.get("ads", KeyStyle.BIBCODE.value))),
            other=KeyStyle(str(payload.get("other", KeyStyle.DOI.value))),
            template=str(payload.get("template") or DEFAULT_KEY_TEMPLATE),
            author_format=text("author_format"),
            max_authors=number("max_authors"),
            authors_when_truncated=number("authors_when_truncated"),
            author_separator=text("author_separator"),
            author_truncation_suffix=text("author_truncation_suffix"),
        )

    def to_mapping(self) -> dict[str, Any]:
        """Return the policy as JSON-compatible settings."""
        return {
            "ads": self.ads.value,
            "other": self.other.value,
            "template": self.template,
            "author_format": self.author_format,
            "max_authors": self.max_authors,
            "authors_when_truncated": self.authors_when_truncated,
            "author_separator": self.author_separator,
            "author_truncation_suffix": self.author_truncation_suffix,
        }


@dataclass
class KeyAssignment:
    """Keys for a group of papers, with the problems that stop an export.

    `keys` maps each paper ID to its key. `missing` lists papers that have no
    key. `clashes` maps each shared key to the papers that share it.
    """

    keys: dict[Any, str] = field(default_factory=dict)
    missing: list[Any] = field(default_factory=list)
    clashes: dict[str, list[Any]] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        """Return whether every paper has its own key."""
        return not self.missing and not self.clashes


def _template_style(policy: KeyPolicy) -> CitationStyle:
    try:
        return CitationStyle(
            id="citation-key",
            name="Citation key",
            template=policy.template,
            author_format=policy.author_format,
            max_authors=policy.max_authors,
            authors_when_truncated=policy.authors_when_truncated,
            author_separator=policy.author_separator,
            author_truncation_suffix=policy.author_truncation_suffix,
        )
    except CitationStyleError as exc:
        raise ValueError(f"Citation key template: {exc}") from exc


def _first(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        value = next((item for item in value if item not in (None, "")), "")
    return str(value or "").strip()


def _ascii_word(text: str) -> str:
    """Return lowercase ASCII letters and digits of `text` without accents."""
    decomposed = unicodedata.normalize("NFKD", text)
    plain = decomposed.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]", "", plain)


def sanitize_key(key: str) -> str:
    """Replace characters that BibTeX keys cannot hold with ``_``.

    Spaces, commas, braces, parentheses, quotes, ``#``, ``%``, ``~``, ``\\``,
    and ``=`` are replaced. Other characters, such as ``/`` and ``.`` in a
    DOI, stay as they are.
    """
    return _UNSAFE_KEY_CHARACTERS.sub("_", key.strip()).strip("_")


def scholar_key(metadata: Mapping[str, Any]) -> str:
    """Return a Google Scholar style key, or an empty string.

    The key is the first author's surname, the year, and the first title word
    that is not in `SCHOLAR_STOP_WORDS`, all in lowercase ASCII. For
    "Einstein, A." (1905), "On the Electrodynamics of Moving Bodies", the key
    is ``einstein1905electrodynamics``.
    """
    author = _first(metadata.get("author"))
    if "," in author:
        surname = author.split(",", 1)[0]
    else:
        parts = author.split()
        surname = parts[-1] if parts else ""
    surname = _ascii_word(surname)
    year = _first(metadata.get("year"))[:4]
    title = html_to_text(_first(metadata.get("title")))
    words = [_ascii_word(word) for word in re.split(r"[\s\-–—:;,.!?/]+", title)]
    word = next((item for item in words if item and item not in SCHOLAR_STOP_WORDS), "")
    if not (surname and year.isdigit()):
        return ""
    return f"{surname}{year}{word}"


def citation_key(
    record: Mapping[str, Any],
    policy: KeyPolicy,
    *,
    own_key: str = "",
) -> str:
    """Return the key for one paper, or an empty string when none applies.

    Parameters
    ----------
    record : mapping
        Paper metadata, or a record with a ``metadata`` mapping.
    policy : `KeyPolicy`
        Styles for ADS papers and other papers.
    own_key : `str`
        A key that the user typed or that came with an imported entry. It
        always wins.
    """
    if own_key.strip():
        return sanitize_key(own_key)
    metadata = record.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else record
    bibcode = _first(metadata.get("bibcode")) or _first(record.get("bibcode"))
    style = policy.ads if bibcode else policy.other
    if style is KeyStyle.BIBCODE:
        key = bibcode
    elif style is KeyStyle.DOI:
        key = _first(metadata.get("doi"))
    elif style is KeyStyle.SCHOLAR:
        key = scholar_key(metadata)
    elif style is KeyStyle.TEMPLATE:
        key = CitationFormatter().render(_template_style(policy), metadata)
    else:
        key = ""
    return sanitize_key(key)


def assign_keys(
    papers: Iterable[tuple[Any, Mapping[str, Any], str]],
    policy: KeyPolicy,
) -> KeyAssignment:
    """Assign keys to papers and report missing and shared keys.

    Parameters
    ----------
    papers : iterable of tuple
        One ``(id, record, own_key)`` tuple per paper: its ID, its metadata
        record, and its own key (or ``""``).
    policy : `KeyPolicy`
        Key styles to apply.

    Returns
    -------
    KeyAssignment
        Keys, papers without a key, and keys that several papers share. The
        caller asks the user to resolve problems before an export.
    """
    assignment = KeyAssignment()
    owners: dict[str, list[Any]] = {}
    for paper_id, record, own_key in papers:
        key = citation_key(record, policy, own_key=own_key)
        if not key:
            assignment.missing.append(paper_id)
            continue
        assignment.keys[paper_id] = key
        owners.setdefault(key, []).append(paper_id)
    assignment.clashes = {key: ids for key, ids in owners.items() if len(ids) > 1}
    return assignment
