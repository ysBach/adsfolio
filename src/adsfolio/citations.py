"""Plain-text citation domain models, validation, and rendering."""

from __future__ import annotations

import re
import string
import unicodedata
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from .formatting import html_to_text

__all__ = [
    "ALLOWED_AUTHOR_FORMATS",
    "ALLOWED_CITATION_FIELDS",
    "CITATION_FIELD_HELP",
    "DEFAULT_CITATION_STYLE_ID",
    "CitationFormatter",
    "CitationStyle",
    "CitationStyleError",
    "citation_preference_key",
    "initial_citation_styles",
    "new_citation_style",
    "validate_citation_styles",
]

DEFAULT_CITATION_STYLE_ID = "my-bibcode-1"

ALLOWED_CITATION_FIELDS = frozenset(
    {
        "bibcode",
        "year",
        "title",
        "first_author_surname",
        "first_author_initials",
        "authors",
        "authors_without_suffix",
        "journal",
        "bibstem",
        "pub",
        "volume",
        "page",
    }
)

ALLOWED_AUTHOR_FORMATS = frozenset(
    {
        "surname",
        "surname_initials",
        "last_comma_first",
    }
)

CITATION_FIELD_HELP = """Available template fields:

- {bibcode} — ADS Bibcode, unchanged.
- {year} — publication year.
- {title} — first title value, with ADS HTML converted to plain text.
- {first_author_surname} — surname of the first author only. For 'Bach, Yoonsoo P.', this renders 'Bach'.
- {first_author_initials} — first-author surname followed by given-name initials. For 'Bach, Yoonsoo P.', this renders 'BachYP'.
- {authors} — author list using the selected author format and separator. If the total author count exceeds Max authors, retain Authors when truncated leading authors and append the configured truncation suffix.
- {authors_without_suffix} — the same rendered author list, but never append the truncation suffix.
- {journal} — first bibstem value, falling back to pub when bibstem is absent.
- {bibstem} — first ADS bibstem value only; it has no fallback.
- {pub} — ADS publication or journal text.
- {volume} — publication volume.
- {page} — first page or article identifier.
- {{ and }} — literal opening and closing braces.

Rules:
- Missing metadata renders as an empty string.
- Spaces, punctuation, and Unicode are preserved."""


class CitationStyleError(ValueError):
    """Report an invalid citation style or style collection."""


def _validate_template_syntax(template: str) -> None:
    """Validate template syntax using string.Formatter parser.

    Parameters
    ----------
    template : str
        The template string to validate.

    Raises
    ------
    CitationStyleError
        If the template is invalid, contains unmatched braces,
        format specifiers, conversions, or disallowed fields.
    """
    formatter = string.Formatter()
    try:
        parsed = list(formatter.parse(template))
    except ValueError as exc:
        raise CitationStyleError(f"Invalid template syntax: {exc}") from exc

    for _, field_name, format_spec, conversion in parsed:
        if field_name is None:
            continue
        if not field_name:
            raise CitationStyleError("Empty field name in template.")
        if format_spec:
            raise CitationStyleError(
                f"Format specifiers like ':{format_spec}' are not permitted in citation templates."
            )
        if conversion:
            raise CitationStyleError(
                f"Conversions like '!{conversion}' are not permitted in citation templates."
            )
        if "." in field_name or "[" in field_name:
            raise CitationStyleError(
                f"Attribute or item access in field '{field_name}' is not permitted."
            )
        if field_name not in ALLOWED_CITATION_FIELDS:
            raise CitationStyleError(
                f"Field '{field_name}' is not a permitted citation field."
            )


@dataclass(frozen=True)
class CitationStyle:
    """Immutable custom citation style definition.

    Parameters
    ----------
    id : str
        Unique persistent identifier for this style.
    name : str
        User-facing name of the style.
    template : str
        Formatting template containing allowed placeholders.
    author_format : str, default="surname_initials"
        Format for author items ("surname", "surname_initials", "last_comma_first").
    max_authors : int, default=3
        Threshold above which author list is truncated.
    authors_when_truncated : int, default=1
        Number of authors to retain when truncation occurs.
    author_separator : str, default=", "
        Delimiter between authors.
    author_truncation_suffix : str, default=" et al."
        Suffix appended when author count exceeds max_authors.
    """

    id: str
    name: str
    template: str
    author_format: str = "surname_initials"
    max_authors: int = 3
    authors_when_truncated: int = 1
    author_separator: str = ", "
    author_truncation_suffix: str = " et al."

    def __post_init__(self) -> None:
        """Validate fields on instance creation."""
        if not isinstance(self.id, str) or not self.id.strip():
            raise CitationStyleError("Citation style ID must be a non-empty string.")
        if not isinstance(self.name, str) or not self.name.strip():
            raise CitationStyleError("Citation style name must be a non-empty string.")
        if not isinstance(self.template, str) or not self.template.strip():
            raise CitationStyleError(
                "Citation style template must be a non-empty string."
            )

        if self.author_format not in ALLOWED_AUTHOR_FORMATS:
            raise CitationStyleError(
                f"Invalid author_format '{self.author_format}'. Must be one of {sorted(ALLOWED_AUTHOR_FORMATS)}."
            )

        if not isinstance(self.max_authors, int) or not isinstance(
            self.authors_when_truncated, int
        ):
            raise CitationStyleError("Author limits must be integers.")

        if not (1 <= self.authors_when_truncated <= self.max_authors <= 99):
            raise CitationStyleError(
                f"Author limits must satisfy 1 <= authors_when_truncated ({self.authors_when_truncated}) "
                f"<= max_authors ({self.max_authors}) <= 99."
            )

        _validate_template_syntax(self.template)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> CitationStyle:
        """Create a CitationStyle from a mapping with normalization of malformed values.

        Parameters
        ----------
        payload : Mapping[str, Any]
            Raw dictionary or mapping defining a citation style.

        Returns
        -------
        CitationStyle
            Validated immutable citation style instance.
        """
        raw_id = payload.get("id")
        id_val = str(raw_id).strip() if raw_id is not None else ""

        raw_name = payload.get("name")
        name_val = str(raw_name).strip() if raw_name is not None else ""

        raw_template = payload.get("template")
        template_val = str(raw_template).strip() if raw_template is not None else ""

        author_format = payload.get("author_format")
        if author_format not in ALLOWED_AUTHOR_FORMATS:
            author_format = "surname_initials"

        raw_max = payload.get("max_authors")
        try:
            max_authors = int(raw_max) if raw_max is not None else 3
        except (ValueError, TypeError):
            max_authors = 3

        raw_trunc = payload.get("authors_when_truncated")
        try:
            authors_when_truncated = int(raw_trunc) if raw_trunc is not None else 1
        except (ValueError, TypeError):
            authors_when_truncated = 1

        if not (1 <= authors_when_truncated <= max_authors <= 99):
            max_authors = 3
            authors_when_truncated = 1

        raw_sep = payload.get("author_separator")
        author_separator = str(raw_sep) if raw_sep is not None else ", "

        raw_suffix = payload.get("author_truncation_suffix")
        author_truncation_suffix = (
            str(raw_suffix) if raw_suffix is not None else " et al."
        )

        return cls(
            id=id_val,
            name=name_val,
            template=template_val,
            author_format=author_format,
            max_authors=max_authors,
            authors_when_truncated=authors_when_truncated,
            author_separator=author_separator,
            author_truncation_suffix=author_truncation_suffix,
        )

    def to_mapping(self) -> dict[str, object]:
        """Serialize citation style to a dictionary.

        Returns
        -------
        dict[str, object]
            Serialized representation of this style.
        """
        return asdict(self)


def _first_scalar(value: Any) -> str:
    """Extract a single string value from scalar or sequence metadata.

    Parameters
    ----------
    value : Any
        Metadata value which may be a string, number, or sequence.

    Returns
    -------
    str
        First element if sequence, or string representation, or empty string if None/empty.
    """
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        if not value:
            return ""
        first = value[0]
        return "" if first is None else str(first)
    return str(value)


def _split_author_name(author: str) -> tuple[str, str]:
    """Split an author string into surname and given names.

    Parameters
    ----------
    author : str
        Author name in 'Last, First' or 'First Last' form.

    Returns
    -------
    tuple[str, str]
        Pair of (surname, given_names).
    """
    if "," in author:
        last, first = author.split(",", 1)
        return last.strip(), first.strip()
    parts = author.split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[-1], " ".join(parts[:-1])


def _extract_initials(given_names: str) -> str:
    """Extract given name initials from given name string.

    Parameters
    ----------
    given_names : str
        Given name string (e.g. 'Yoonsoo P.').

    Returns
    -------
    str
        Concatenated initials (e.g. 'YP').
    """
    if not given_names:
        return ""
    tokens = re.findall(r"[^\W\d_]+", given_names)
    return "".join(token[0] for token in tokens if token)


def _format_author_item(surname: str, given_names: str, author_format: str) -> str:
    """Format a single author according to the requested format.

    Parameters
    ----------
    surname : str
        Author surname.
    given_names : str
        Author given names.
    author_format : str
        One of 'surname', 'surname_initials', 'last_comma_first'.

    Returns
    -------
    str
        Formatted author string.
    """
    if author_format == "surname":
        return surname
    if author_format == "last_comma_first":
        if given_names:
            return f"{surname}, {given_names}"
        return surname
    # surname_initials
    initials = _extract_initials(given_names)
    return f"{surname}{initials}"


class CitationFormatter:
    """Format citation strings against record metadata using a CitationStyle."""

    def render(self, style: CitationStyle, record: Mapping[str, Any]) -> str:
        """Render the given record metadata with the citation style.

        Parameters
        ----------
        style : CitationStyle
            Citation style definition.
        record : Mapping[str, Any]
            Catalog or ADS paper record containing a 'metadata' mapping.

        Returns
        -------
        str
            Rendered citation string.
        """
        metadata: Mapping[str, Any]
        if isinstance(record.get("metadata"), Mapping):
            metadata = record["metadata"]
        else:
            metadata = record

        raw_authors = metadata.get("author", [])
        author_list: list[str] = []
        if isinstance(raw_authors, (list, tuple)):
            for a in raw_authors:
                if a is not None and str(a).strip():
                    author_list.append(str(a).strip())
        elif isinstance(raw_authors, str) and raw_authors.strip():
            author_list.append(raw_authors.strip())

        parsed_authors = [_split_author_name(a) for a in author_list]

        if parsed_authors:
            first_sur, first_giv = parsed_authors[0]
            first_author_surname = first_sur
            first_author_initials = f"{first_sur}{_extract_initials(first_giv)}"
        else:
            first_author_surname = ""
            first_author_initials = ""

        if parsed_authors:
            formatted_tokens = [
                _format_author_item(sur, giv, style.author_format)
                for sur, giv in parsed_authors
            ]
            is_truncated = len(formatted_tokens) > style.max_authors
            retained_count = (
                style.authors_when_truncated if is_truncated else len(formatted_tokens)
            )
            retained_tokens = formatted_tokens[:retained_count]
            joined_no_suffix = style.author_separator.join(
                token for token in retained_tokens if token
            )
            if is_truncated and joined_no_suffix:
                joined_with_suffix = (
                    f"{joined_no_suffix}{style.author_truncation_suffix}"
                )
            else:
                joined_with_suffix = joined_no_suffix
            authors = joined_with_suffix
            authors_without_suffix = joined_no_suffix
        else:
            authors = ""
            authors_without_suffix = ""

        bibcode = _first_scalar(metadata.get("bibcode"))
        year = _first_scalar(metadata.get("year"))
        title = html_to_text(_first_scalar(metadata.get("title")))
        bibstem = _first_scalar(metadata.get("bibstem"))
        pub = html_to_text(_first_scalar(metadata.get("pub")))
        volume = _first_scalar(metadata.get("volume"))
        page = _first_scalar(metadata.get("page"))
        journal = bibstem if bibstem else pub

        values: dict[str, str] = {
            "bibcode": bibcode,
            "year": year,
            "title": title,
            "first_author_surname": first_author_surname,
            "first_author_initials": first_author_initials,
            "authors": authors,
            "authors_without_suffix": authors_without_suffix,
            "journal": journal,
            "bibstem": bibstem,
            "pub": pub,
            "volume": volume,
            "page": page,
        }

        return style.template.format_map(values)


def initial_citation_styles() -> tuple[CitationStyle, ...]:
    """Return default citation styles provided out-of-the-box.

    Returns
    -------
    tuple[CitationStyle, ...]
        Tuple containing the default 'My bibcode 1' style.
    """
    return (
        CitationStyle(
            id=DEFAULT_CITATION_STYLE_ID,
            name="My bibcode 1",
            template="{first_author_initials} ({year}) {bibstem}, {volume}, {page}",
        ),
    )


def new_citation_style(
    existing_names: Iterable[str],
    existing_ids: Iterable[str],
) -> CitationStyle:
    """Generate a new CitationStyle with lowest unused positive number in name.

    Parameters
    ----------
    existing_names : Iterable[str]
        Existing style names to avoid name collisions.
    existing_ids : Iterable[str]
        Existing style IDs to avoid ID collisions.

    Returns
    -------
    CitationStyle
        New citation style with unique name and ID.
    """
    name_set = {
        unicodedata.normalize("NFC", n.strip().casefold()) for n in existing_names
    }
    id_set = set(existing_ids)

    n = 1
    while True:
        candidate_name = f"My bibcode {n}"
        if unicodedata.normalize("NFC", candidate_name.casefold()) not in name_set:
            break
        n += 1

    while True:
        candidate_id = uuid.uuid4().hex
        if candidate_id not in id_set:
            break

    return CitationStyle(
        id=candidate_id,
        name=candidate_name,
        template="{first_author_initials} ({year}) {bibstem}, {volume}, {page}",
    )


def citation_preference_key(style_id: str) -> str:
    """Build preference storage key for a citation style ID.

    Parameters
    ----------
    style_id : str
        Style ID string.

    Returns
    -------
    str
        Key in the form 'citation:<style_id>'.

    Raises
    ------
    CitationStyleError
        If style_id is empty or whitespace.
    """
    if not isinstance(style_id, str) or not style_id.strip():
        raise CitationStyleError("Style ID cannot be empty.")
    return f"citation:{style_id.strip()}"


def validate_citation_styles(styles: Sequence[CitationStyle]) -> None:
    """Validate an entire collection of CitationStyles.

    Parameters
    ----------
    styles : Sequence[CitationStyle]
        Collection of citation styles.

    Raises
    ------
    CitationStyleError
        If duplicate names or IDs exist in the collection, or any style is invalid.
    """
    seen_ids: set[str] = set()
    seen_names: set[str] = set()

    for style in styles:
        if not isinstance(style, CitationStyle):
            raise CitationStyleError(
                f"Expected CitationStyle instance, got {type(style).__name__}."
            )

        if style.id in seen_ids:
            raise CitationStyleError(f"Duplicate citation style ID '{style.id}'.")
        seen_ids.add(style.id)

        folded_name = unicodedata.normalize("NFC", style.name.strip().casefold())
        if folded_name in seen_names:
            raise CitationStyleError(f"Duplicate citation style name '{style.name}'.")
        seen_names.add(folded_name)
