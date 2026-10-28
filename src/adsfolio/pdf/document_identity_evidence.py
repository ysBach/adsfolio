"""Corroborate PDF identity evidence from mutual DocInfo, text, and provider matches.

This module provides a narrowly scoped helper to corroborate a PDF's self-stated
identity without relying on file names, fixed journal names, or numeric heuristics.
"""

from __future__ import annotations

import io
import logging
import re
from collections.abc import Mapping
from typing import Any

from pypdf import PdfReader

from .upgrade_utils import _normalize_doi

__all__ = [
    "corroborate_document_doi_evidence",
]

_LOGGER = logging.getLogger(__name__)

# Pattern matching explicitly labeled DOIs (e.g. 'DOI: 10.1051/...' or 'doi:10.1051/...').
_SUBJECT_EXPLICIT_DOI_RE = re.compile(
    r"(?i)(?:^|[^a-zA-Z0-9_])doi\s*[:=]\s*(?:https?://(?:dx\.)?doi\.org/)?(\S+)"
)

# DOI prefix pattern: must start with 10.<4-9 digits>/ or 10.<4-9 digits>:
_DOI_PREFIX_RE = re.compile(r"^10\.\d{4,9}[/:]")


def _normalize_title_text(title: str) -> str:
    """Normalize title by collapsing whitespace and folding case."""
    return " ".join(str(title or "").strip().split()).casefold()


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


def corroborate_document_doi_evidence(
    pdf_bytes: bytes,
    provider_document: Mapping[str, Any],
) -> str | None:
    """Return a DOI supported by the PDF metadata, first page, and provider.

    All of these conditions must hold:

    1. DocInfo ``/Subject`` contains exactly one explicitly labeled DOI.
    2. The first page repeats that DOI with exact token boundaries.
    3. DocInfo ``/Title`` matches the provider title after whitespace and case normalization.
    4. The provider record explicitly lists the same DOI.

    Parameters
    ----------
    pdf_bytes : bytes
        Immutable bytes of the PDF to verify.
    provider_document : Mapping[str, Any]
        Authoritative provider record containing the title and DOI.

    Returns
    -------
    str | None
        Normalized DOI when all conditions hold. Otherwise, `None`.
    """
    if not isinstance(pdf_bytes, (bytes, bytearray, memoryview)) or not pdf_bytes:
        return None
    pdf_data = bytes(pdf_bytes)

    if not isinstance(provider_document, Mapping):
        return None

    # Parse PDF DocInfo using pypdf (fails closed on encrypted / malformed)
    try:
        reader = PdfReader(io.BytesIO(pdf_data))
        if reader.is_encrypted:
            return None
        metadata = reader.metadata or {}
        subject = str(metadata.get("/Subject") or "").strip()
        title = str(metadata.get("/Title") or "").strip()
    except Exception as exc:  # noqa: BLE001 - parsers raise many types
        _LOGGER.debug("Failed to read PDF metadata: %s", exc)
        return None

    if not subject or not title:
        return None

    # Condition 1: PDF DocInfo /Subject has explicitly labeled DOI(s).
    subject_dois = _extract_subject_labeled_dois(subject)
    # Must have exactly one explicitly labeled DOI (reject multiple or conflicting).
    if len(set(subject_dois)) != 1:
        return None
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
        return None

    # Condition 3: PDF DocInfo /Title equals provider title under whitespace/case normalization,
    # and provider explicitly lists this exact DOI.
    provider_title_raw = provider_document.get("title")
    provider_title = ""
    if isinstance(provider_title_raw, (list, tuple)):
        provider_title = str(provider_title_raw[0]) if provider_title_raw else ""
    elif provider_title_raw is not None:
        provider_title = str(provider_title_raw)

    if not provider_title:
        return None

    if _normalize_title_text(title) != _normalize_title_text(provider_title):
        return None

    if subject_doi not in normalized_provider_dois:
        return None

    target_doi = subject_doi

    # Condition 2: First-page text contains the same DOI with exact boundary
    try:
        if len(reader.pages) == 0:
            return None
        first_page_text = reader.pages[0].extract_text() or ""
    except Exception as exc:  # noqa: BLE001 - text extraction errors
        _LOGGER.debug("Failed to extract first page text: %s", exc)
        return None

    if not _contains_exact_doi_with_boundaries(first_page_text, target_doi):
        return None

    return target_doi
