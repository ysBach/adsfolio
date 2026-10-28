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
from typing import Any

from pypdf import PdfReader

from .upgrade_utils import _normalize_doi

__all__ = [
    "DocumentDoiEvidence",
    "check_document_doi_evidence",
    "corroborate_document_doi_evidence",
]

_LOGGER = logging.getLogger(__name__)

# Pattern matching explicitly labeled DOIs (e.g. 'DOI: 10.1051/...' or 'doi:10.1051/...').
_SUBJECT_EXPLICIT_DOI_RE = re.compile(
    r"(?i)(?:^|[^a-zA-Z0-9_])doi\s*[:=]\s*(?:https?://(?:dx\.)?doi\.org/)?(\S+)"
)

# DOI prefix pattern: must start with 10.<4-9 digits>/ or 10.<4-9 digits>:
_DOI_PREFIX_RE = re.compile(r"^10\.\d{4,9}[/:]")
_TITLE_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})
_TITLE_SEPARATOR_RE = re.compile(r"(?:\s+[-–—]\s+|:\s+)")


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

    1. DocInfo ``/Subject`` contains exactly one explicitly labeled DOI.
    2. The first page repeats that DOI with exact token boundaries.
    3. The metadata and provider titles match after documented formatting normalization.
    4. The provider record explicitly lists the same DOI.

    Parameters
    ----------
    pdf_bytes : bytes
        Immutable bytes of the PDF to verify.
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
    Each resulting title part must match exactly. DOI tokens retain their
    punctuation and use exact token boundaries.
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
    except Exception as exc:  # noqa: BLE001 - parsers raise many types
        _LOGGER.debug("Failed to read PDF metadata: %s", exc)
        return DocumentDoiEvidence(None, "ADSfolio cannot read the PDF metadata.")

    if not title:
        return DocumentDoiEvidence(
            None, "The PDF metadata has no title for the DOI cross-check."
        )

    # Condition 1: PDF DocInfo /Subject has explicitly labeled DOI(s).
    subject_dois = _extract_subject_labeled_dois(subject)
    # Must have exactly one explicitly labeled DOI (reject multiple or conflicting).
    if not subject_dois:
        return DocumentDoiEvidence(
            None, "The PDF Subject metadata has no explicitly labelled DOI."
        )
    if len(set(subject_dois)) != 1:
        return DocumentDoiEvidence(
            None, "The PDF Subject metadata lists several different DOIs."
        )
    subject_doi = subject_dois[0]

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

    if _normalize_title_text(title) != _normalize_title_text(provider_title):
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
