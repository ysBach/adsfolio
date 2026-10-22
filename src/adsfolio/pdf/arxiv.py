"""Derive arXiv identifiers from PDF file and ADS metadata records."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from ..formatting import html_to_text

__all__ = ["arxiv_id", "normalize_arxiv_id"]

_ARXIV_ID_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])((?:\d{4}\.\d{4,5}|[A-Za-z][A-Za-z0-9-]*(?:\.[A-Za-z]{2})?/\d{7})(?:v\d+)?)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_LEGACY_ARXIV_BIBCODE_PATTERN = re.compile(
    r"\d{4}arXiv(?P<identifier>\d{9})",
    re.IGNORECASE,
)
_DOI_PATTERN = re.compile(r"(?<![A-Za-z0-9.])10\.\d{4,9}/\S+")
_ARXIV_DOI_PATTERN = re.compile(
    r"(?<![A-Za-z0-9.])10\.48550/arxiv\.(\S+)", re.IGNORECASE
)


def _normalize_candidate(value: Any) -> str:
    text = html_to_text(str(value or ""))
    if "48550/arxiv." in text.lower():
        doi = _ARXIV_DOI_PATTERN.search(text)
        if doi is None:
            return ""
        text = doi.group(1)
    elif _DOI_PATTERN.search(text):
        return ""
    legacy_match = _LEGACY_ARXIV_BIBCODE_PATTERN.search(text)
    if legacy_match:
        identifier = legacy_match.group("identifier")
        return f"{identifier[:4]}.{identifier[4:]}"
    match = _ARXIV_ID_PATTERN.search(text)
    return match.group(1) if match else ""


def normalize_arxiv_id(value: Any) -> str:
    """Return the version-free arXiv identifier in `value`, or an empty string.

    Accepts a bare identifier, an ``arXiv:`` label, an arXiv DOI
    (``10.48550/arXiv.<id>``), or a legacy arXiv bibcode. Other DOIs give an
    empty string. The result identifies the paper across versions, for example
    ``2401.12345v2`` gives ``2401.12345``.
    """
    identifier = _normalize_candidate(value)
    return re.sub(r"v\d+$", "", identifier, flags=re.IGNORECASE).lower()


def arxiv_id(record: Mapping[str, Any]) -> str:
    """Return the arXiv identifier of a file record, or an empty string.

    Checks the ``arxiv``, ``arxiv_id``, ``identifier`` and ``doi`` fields, then
    the bibcode, then the file path. Any value that contains a DOI is used only
    if it is an arXiv DOI (``10.48550/arXiv.<id>``); other DOIs are skipped
    wherever they appear. New (``2401.12345v2``) and old-style
    (``astro-ph/0601001``) identifiers are recognized, as are legacy bibcodes
    such as ``2024arXiv240112345B``.

    The file path is scanned for identifier-like text only, so a file name
    that looks like an arXiv identifier is reported as one.

    Parameters
    ----------
    record
        File record with optional top-level fields and a ``metadata`` mapping.

    Returns
    -------
    str
        Identifier as found, including any version suffix.
    """
    metadata = record.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    candidates = [
        *(metadata.get(key, []) for key in ("arxiv", "arxiv_id", "identifier", "doi")),
        record.get("arxiv"),
        record.get("arxiv_id"),
        record.get("identifier"),
        record.get("doi"),
        record.get("bibcode"),
        metadata.get("bibcode"),
        record.get("current_path"),
    ]
    for candidate in candidates:
        values = candidate if isinstance(candidate, (list, tuple)) else [candidate]
        for value in values:
            normalized = _normalize_candidate(value)
            if normalized:
                return normalized
    return ""
