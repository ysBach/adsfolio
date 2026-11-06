"""Corroborate PDF identity evidence from mutual DocInfo, text, and provider matches.

This module provides a narrowly scoped helper to corroborate a PDF's self-stated
identity without relying on file names, fixed journal names, or numeric heuristics.
"""

from __future__ import annotations

import io
import logging
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from html import unescape
from html.entities import html5
from typing import Any

from pypdf import PdfReader

from .upgrade_utils import _normalize_doi

__all__ = [
    "PAGE_CITATION_AUTHORITY",
    "DocumentDoiEvidence",
    "PageCitationEvidence",
    "check_document_doi_evidence",
    "check_page_citation_evidence",
    "corroborate_document_doi_evidence",
]

# Proof authority for a match shown by the bibliographic data on page 1.
PAGE_CITATION_AUTHORITY = "page_citation"

_LOGGER = logging.getLogger(__name__)

# Pattern matching explicitly labeled DOIs (e.g. 'DOI: 10.1051/...' or 'doi:10.1051/...').
_SUBJECT_EXPLICIT_DOI_RE = re.compile(
    r"(?i)(?:^|[^a-zA-Z0-9_])doi\s*[:=]\s*(?:https?://(?:dx\.)?doi\.org/)?(\S+)"
)

# DOI prefix pattern: must start with 10.<4-9 digits>/ or 10.<4-9 digits>:
_DOI_PREFIX_RE = re.compile(r"^10\.\d{4,9}[/:]")
_TITLE_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})
_TITLE_SEPARATOR_RE = re.compile(r"(?:\s+[-–—]\s+|:\s+)")
_TITLE_DISPLAY_TAGS = frozenset(
    {"astrobj", "b", "em", "i", "span", "strong", "sub", "sup"}
)
_TITLE_DISPLAY_TAG_RE = re.compile(r"<(/?)([A-Za-z]+)\s*>")
_TITLE_ENTITY_RE = re.compile(
    r"&(?:[A-Za-z][A-Za-z0-9]*|#(?:[0-9]+|[xX][0-9A-Fa-f]+));"
)
_TITLE_DASHES = str.maketrans({character: "-" for character in "‐‑‒–—−"})
# A word broken across lines with a hyphen, for example "topogra-" + "phy".
_LINE_END_HYPHEN_RE = re.compile(r"(?<=[^\W\d_])-[ \t]*\r?\n[ \t]*(?=[^\W\d_])")
# Characters on a line other than letters, such as footnote marks or digits.
_NO_LETTERS = r"(?:(?![^\W\d_])[^\n])*"
# A line that holds only a correction-notice heading, such as "ERRATUM". The
# notice repeats the original title and citation, so the match is refused.
_CORRECTION_HEADING_RE = re.compile(
    rf"(?im)^{_NO_LETTERS}"
    r"(erratum|errata|corrigendum|correction|addendum|retraction)"
    rf"{_NO_LETTERS}$"
)
_PAGE_DOI_TOKEN_RE = re.compile(r"(?i)(?<![A-Za-z0-9_])10\.\d{4,9}/[^\s\"<>]+")


@dataclass(frozen=True)
class DocumentDoiEvidence:
    """Store a corroborated DOI or the reason corroboration failed.

    Parameters
    ----------
    doi : str or None
        Verified document DOI, or None after a refusal.
    reason : str
        User-visible reason for a refusal. Empty on success.
    """

    doi: str | None
    reason: str = ""


def _normalize_title_text(title: str) -> str:
    """Normalize Unicode, quote typography, subtitle separators, whitespace, and case.

    A spaced dash and a colon followed by whitespace separate title parts.
    Other punctuation and all word and number content remain significant.
    """
    text = unicodedata.normalize("NFC", str(title or "")).translate(_TITLE_QUOTES)
    text = " ".join(text.strip().split()).casefold()
    return " : ".join(part.strip() for part in _TITLE_SEPARATOR_RE.split(text))


def _normalize_page_title(title: str) -> str:
    """Normalize line breaks and typographic dashes for a page-title match."""
    text = unicodedata.normalize("NFKC", title).translate(_TITLE_DASHES)
    return " ".join(text.translate(_TITLE_QUOTES).casefold().split())


def _normalize_missing_title_dashes(title: str) -> str:
    """Allow a DocInfo title to omit dashes when the page title matches."""
    return " ".join(_normalize_page_title(title).replace("-", "").split())


def _decode_title_entity(match: re.Match[str]) -> str:
    """Decode a complete, valid HTML entity without dropping its contents."""
    entity = match.group()
    if not entity.startswith("&#") and entity[1:] not in html5:
        return entity
    try:
        decoded = unescape(entity)
    except ValueError:
        return entity
    return decoded if decoded and "\ufffd" not in decoded else entity


def _provider_title_text(title: str) -> str:
    """Remove balanced display tags and decode valid HTML entities.

    Unknown tags remain literal. Keep the original title when recognized tags
    do not balance or angle brackets are ambiguous. Decode entities after tag
    handling so escaped angle brackets remain title text.
    """
    parts: list[str] = []
    open_tags: list[str] = []
    position = 0
    while True:
        start = title.find("<", position)
        if start < 0:
            parts.append(title[position:])
            break
        parts.append(title[position:start])
        end = title.find(">", start + 1)
        if end < 0:
            parts.append(title[start:])
            break
        candidate = title[start : end + 1]
        if "<" in candidate[1:]:
            return title
        tag = _TITLE_DISPLAY_TAG_RE.fullmatch(candidate)
        if tag is None or tag.group(2).casefold() not in _TITLE_DISPLAY_TAGS:
            parts.append(candidate)
        elif tag.group(1):
            if not open_tags or open_tags.pop() != tag.group(2).casefold():
                return title
        else:
            open_tags.append(tag.group(2).casefold())
        position = end + 1

    if open_tags:
        return title
    return _TITLE_ENTITY_RE.sub(_decode_title_entity, "".join(parts))


def _extract_subject_labeled_dois(subject: str) -> list[str]:
    """Extract explicitly labeled DOIs from /Subject without stripping DOI characters.

    Exact DOI tokens are preserved without heuristic or provider-dependent
    punctuation trimming, failing closed if ambiguous.
    """
    dois: list[str] = []
    for match in _SUBJECT_EXPLICIT_DOI_RE.finditer(subject):
        token = match.group(1).strip()
        if (
            (token.startswith("(") and token.endswith(")"))
            or (token.startswith("<") and token.endswith(">"))
            or (token.startswith("[") and token.endswith("]"))
        ):
            token = token[1:-1].strip()
        norm = _normalize_doi(token)
        if norm and _DOI_PREFIX_RE.match(norm):
            dois.append(norm)
    return dois


def _contains_exact_doi_with_boundaries(page_text: str, target_doi: str) -> bool:
    """Check if page_text contains target_doi with strict boundaries.

    Prevents prefix collisions such as 10.1000/x matching 10.1000/x.extra,
    10.1000/x:extra, 10.1000/x/extra, or 10.1000/x-extra.
    """
    pattern = re.compile(
        rf"(?i)(?<![A-Za-z0-9_]){re.escape(target_doi)}(?![A-Za-z0-9_/:.-])"
    )
    return bool(pattern.search(page_text))


def check_document_doi_evidence(
    pdf_bytes: bytes,
    provider_document: Mapping[str, Any],
) -> DocumentDoiEvidence:
    """Check the metadata DOI against the first page and provider record.

    All of these conditions must hold:

    1. DocInfo ``/Subject`` contains exactly one explicitly labeled DOI, or
       ``/WPS-ARTICLEDOI`` contains one article DOI.
    2. The first page repeats that DOI with exact token boundaries.
    3. The metadata and provider titles match after documented formatting normalization.
    4. The provider record explicitly lists the same DOI.

    Parameters
    ----------
    pdf_bytes : bytes
        Immutable bytes of the PDF to check.
    provider_document : Mapping[str, Any]
        Authoritative provider record containing the title and DOI.

    Returns
    -------
    DocumentDoiEvidence
        Normalized DOI on success. Otherwise, the reason for refusal.

    Notes
    -----
    Title comparison folds case and collapses whitespace. It normalizes
    canonical Unicode, curly quotation marks, and spaced subtitle separators.
    It also accepts balanced provider display tags and valid HTML entities.
    Title content and unknown tags remain significant. Each resulting title
    part must match exactly. DOI tokens retain their punctuation and use exact
    token boundaries. When only the publisher article DOI is present, page 1
    must also contain the full provider title. In that case the DocInfo title
    may omit dashes, but its remaining characters must match.
    """
    if not isinstance(pdf_bytes, (bytes, bytearray, memoryview)) or not pdf_bytes:
        return DocumentDoiEvidence(None, "The PDF data cannot be read.")
    pdf_data = bytes(pdf_bytes)

    if not isinstance(provider_document, Mapping):
        return DocumentDoiEvidence(
            None, "No provider record is available for comparison."
        )

    # Parse PDF DocInfo using pypdf (fails closed on encrypted / malformed)
    try:
        reader = PdfReader(io.BytesIO(pdf_data))
        if reader.is_encrypted:
            return DocumentDoiEvidence(None, "The PDF metadata is encrypted.")
        metadata = reader.metadata or {}
        subject = str(metadata.get("/Subject") or "").strip()
        title = str(metadata.get("/Title") or "").strip()
        article_value = metadata.get("/WPS-ARTICLEDOI")
    except Exception as exc:  # noqa: BLE001 - parsers raise many types
        _LOGGER.debug("Failed to read PDF metadata: %s", exc)
        return DocumentDoiEvidence(None, "ADSfolio cannot read the PDF metadata.")

    if not title:
        return DocumentDoiEvidence(
            None, "The PDF metadata has no title for the DOI cross-check."
        )

    # Keep every DOI-bearing DocInfo field consistent with the candidate.
    subject_dois = _extract_subject_labeled_dois(subject)
    article_doi = ""
    if article_value is not None:
        if not isinstance(article_value, str):
            return DocumentDoiEvidence(None, "The PDF article DOI is invalid.")
        article_doi = _normalize_doi(article_value)
        if not re.fullmatch(r"10\.\d{4,9}/\S+", article_doi):
            return DocumentDoiEvidence(None, "The PDF article DOI is invalid.")
    candidates = set(subject_dois)
    if article_doi:
        candidates.add(article_doi)
    if not candidates:
        return DocumentDoiEvidence(
            None, "The PDF Subject metadata has no explicitly labelled DOI."
        )
    if len(candidates) != 1:
        return DocumentDoiEvidence(
            None, "The PDF metadata lists several different DOIs."
        )
    subject_doi = candidates.pop()
    article_only = not subject_dois

    # Provider DOIs
    provider_doi_raw = provider_document.get("doi")
    provider_dois: list[str] = []
    if isinstance(provider_doi_raw, (list, tuple, set, frozenset)):
        provider_dois = [str(d) for d in provider_doi_raw if d]
    elif provider_doi_raw is not None:
        provider_dois = [str(provider_doi_raw)]

    normalized_provider_dois = {_normalize_doi(d) for d in provider_dois if d}
    if not normalized_provider_dois:
        return DocumentDoiEvidence(
            None, "The provider record has no DOI for comparison."
        )
    if subject_doi not in normalized_provider_dois:
        return DocumentDoiEvidence(
            None, f"PDF metadata DOI {subject_doi} is not listed by the provider."
        )

    # Read the provider title before the final comparison of title formatting.
    provider_title_raw = provider_document.get("title")
    provider_title = ""
    if isinstance(provider_title_raw, (list, tuple)):
        provider_title = str(provider_title_raw[0]) if provider_title_raw else ""
    elif provider_title_raw is not None:
        provider_title = str(provider_title_raw)

    if not provider_title:
        return DocumentDoiEvidence(
            None, "The provider record has no title for the DOI cross-check."
        )

    target_doi = subject_doi

    # Condition 2: First-page text contains the same DOI with exact boundary
    try:
        if len(reader.pages) == 0:
            return DocumentDoiEvidence(
                None, "The PDF has no first page for the DOI cross-check."
            )
        first_page_text = reader.pages[0].extract_text() or ""
    except Exception as exc:  # noqa: BLE001 - text extraction errors
        _LOGGER.debug("Failed to extract first page text: %s", exc)
        return DocumentDoiEvidence(
            None, f"ADSfolio cannot read page 1 to check DOI {subject_doi}."
        )

    if not _contains_exact_doi_with_boundaries(first_page_text, target_doi):
        return DocumentDoiEvidence(
            None,
            f"PDF metadata states DOI {subject_doi}, but page 1 does not repeat that exact DOI.",
        )

    provider_titles = (provider_title, _provider_title_text(provider_title))
    normalized_title = _normalize_title_text(title)
    title_matches = any(
        normalized_title == _normalize_title_text(candidate)
        for candidate in provider_titles
    )
    if article_only:
        page_title = _normalize_page_title(first_page_text)
        title_matches = any(
            bool(_normalize_page_title(candidate))
            and bool(_normalize_missing_title_dashes(title))
            and _normalize_missing_title_dashes(title)
            == _normalize_missing_title_dashes(candidate)
            and bool(
                re.search(
                    rf"(?<!\w){re.escape(_normalize_page_title(candidate))}(?!\w)",
                    page_title,
                )
            )
            for candidate in provider_titles
        )
    if not title_matches:
        return DocumentDoiEvidence(
            None,
            f"DOI {subject_doi} matches the PDF metadata, page 1, and provider, but the document titles differ.",
        )

    return DocumentDoiEvidence(target_doi)


def corroborate_document_doi_evidence(
    pdf_bytes: bytes,
    provider_document: Mapping[str, Any],
) -> str | None:
    """Return the document DOI when metadata, page 1, and provider agree.

    Parameters
    ----------
    pdf_bytes : bytes
        Immutable PDF contents.
    provider_document : Mapping[str, Any]
        Authoritative metadata containing the title and DOI.

    Returns
    -------
    str or None
        Corroborated DOI, or None when a required check fails.

    See Also
    --------
    check_document_doi_evidence : Check evidence and explain a refusal.
    """
    return check_document_doi_evidence(pdf_bytes, provider_document).doi


@dataclass(frozen=True)
class PageCitationEvidence:
    """Store the result of the page-1 citation check.

    Parameters
    ----------
    matched : bool
        `True` when page 1 shows the provider record's citation data.
    reason : str
        User-visible reason for a refusal. Empty on success.
    """

    matched: bool
    reason: str = ""


def _first_text(value: Any) -> str:
    """Return the first non-empty string of a provider field."""
    values = value if isinstance(value, (list, tuple)) else [value]
    for item in values:
        if item is not None and str(item).strip():
            return str(item).strip()
    return ""


def _number_token(value: str) -> str:
    """Return a pattern for `value` as a whole token, not part of a number."""
    return rf"(?<![\w])(?<!\d\.){re.escape(value)}(?![\w])(?!\.\d)"


def check_page_citation_evidence(
    pdf_bytes: bytes,
    provider_document: Mapping[str, Any],
) -> PageCitationEvidence:
    """Check that page 1 shows the provider record's citation data.

    This check is for PDFs that state no DOI or arXiv ID, such as scans of
    papers published before DOIs. Page 1 must show all of these:

    1. The full provider title as the only text on its lines. Footnote marks,
       digits, and punctuation may surround it. Case, spacing, typographic
       dashes and quotes, and words hyphenated across a line break do not
       matter.
    2. The first author's surname as a whole word.
    3. The publication year as a whole number.
    4. The volume and then the first page, on one text line, as whole tokens.
       An example is "ICARUS 73, 462--481 (1988)".

    A DOI on page 1 that the provider record does not list refuses the match.
    A line that holds only the heading Erratum, Errata, Corrigendum,
    Correction, Addendum, or Retraction also refuses it. Such a notice repeats
    the title and citation of the original paper.

    Parameters
    ----------
    pdf_bytes : bytes
        Immutable PDF contents.
    provider_document : Mapping[str, Any]
        Authoritative record with title, author, year, volume, and page.

    Returns
    -------
    PageCitationEvidence
        The result and, after a refusal, the first failed condition.
    """
    title = _first_text(provider_document.get("title"))
    author = _first_text(provider_document.get("author"))
    year = _first_text(provider_document.get("year"))
    volume = _first_text(provider_document.get("volume"))
    first_page = _first_text(provider_document.get("page"))
    if not (title and author and year and volume and first_page):
        return PageCitationEvidence(
            False,
            "The provider record lacks the title, first author, year, volume, "
            "or first page for the page-1 citation check.",
        )
    try:
        reader = PdfReader(io.BytesIO(bytes(pdf_bytes)))
        if reader.is_encrypted or len(reader.pages) == 0:
            return PageCitationEvidence(False, "ADSfolio cannot read page 1.")
        page_text = reader.pages[0].extract_text() or ""
    except Exception as exc:  # noqa: BLE001 - parsers raise many types
        _LOGGER.debug("Failed to extract first page text: %s", exc)
        return PageCitationEvidence(False, "ADSfolio cannot read page 1.")
    page_text = unicodedata.normalize("NFKC", page_text)

    provider_dois = {
        _normalize_doi(str(value))
        for value in (
            provider_document.get("doi")
            if isinstance(provider_document.get("doi"), (list, tuple))
            else [provider_document.get("doi")]
        )
        if value
    }
    for match in _PAGE_DOI_TOKEN_RE.finditer(page_text):
        page_doi = _normalize_doi(match.group().rstrip(").,;"))
        if page_doi not in provider_dois:
            return PageCitationEvidence(
                False,
                f"Page 1 shows DOI {page_doi}, which the provider record does not list.",
            )

    heading = _CORRECTION_HEADING_RE.search(page_text)
    if heading is not None:
        return PageCitationEvidence(
            False,
            f"Warning: page 1 has the heading {heading.group(1)!r}. This PDF can "
            "be a correction notice and not the paper itself. A correction "
            "notice repeats the title and citation of the original paper.",
        )

    # The title must be the only text on its lines, so a title quoted in a
    # sentence, a reference, or an erratum heading does not count.
    title_words = _normalize_page_title(_provider_title_text(title)).split()
    title_pattern = re.compile(
        rf"(?:^|\n){_NO_LETTERS}"
        + r"\s+".join(re.escape(word) for word in title_words)
        + rf"{_NO_LETTERS}(?=\n|$)"
    )
    page_variants = [
        "\n".join(_normalize_page_title(line) for line in text.splitlines())
        for text in (page_text, _LINE_END_HYPHEN_RE.sub("", page_text))
    ]
    if not title_words or not any(title_pattern.search(text) for text in page_variants):
        return PageCitationEvidence(
            False, "Page 1 does not show the full provider title on its own lines."
        )

    surname = _normalize_page_title(author.split(",", 1)[0])
    if not surname or not re.search(
        rf"(?<!\w){re.escape(surname)}(?!\w)", " ".join(page_variants[0].split())
    ):
        return PageCitationEvidence(
            False, f"Page 1 does not show the first author's surname ({surname})."
        )

    if not re.search(_number_token(year), page_text):
        return PageCitationEvidence(False, f"Page 1 does not show the year {year}.")

    citation = re.compile(
        _number_token(volume) + r".*?" + _number_token(first_page), re.IGNORECASE
    )
    if not any(citation.search(line) for line in page_text.splitlines()):
        return PageCitationEvidence(
            False,
            f"Page 1 does not show volume {volume} and first page {first_page} "
            "on one line.",
        )
    return PageCitationEvidence(True)
