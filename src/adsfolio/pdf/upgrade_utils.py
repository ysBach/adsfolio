"""Utility models and helpers for arXiv upgrade workflow."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

from ..api.search import escape_query_phrase

if TYPE_CHECKING:
    from ..client import ADSClient

ARXIV_BIBCODE_RE = re.compile(r"^\d{4}arXiv", re.IGNORECASE)
ARXIV_DOI_PREFIX = "10.48550/arxiv."

SEARCH_FIELDS = [
    "bibcode",
    "alternate_bibcode",
    "doi",
    "bibstem",
    "identifier",
    "title",
    "author",
    "year",
    "volume",
    "page",
    "pub",
]

__all__ = [
    "PublishedMatch",
    "UpgradeResult",
    "_as_list",
    "_first_string",
    "_relative_path",
    "_safe_bibcode_name",
    "_utc_now_iso",
    "check_published_version",
    "check_published_versions",
    "detect_arxiv_entries",
    "is_arxiv_bibcode",
    "is_arxiv_doi",
    "is_arxiv_entry",
]

MAX_BATCH_TERMS = 25


@dataclass
class PublishedMatch:
    """Published bibcode resolution result."""

    bibcode: str
    document: dict[str, Any]


@dataclass
class UpgradeResult:
    """Result for one sidecar-entry upgrade attempt."""

    original_filename: str
    current_filename: str
    old_bibcode: str
    new_bibcode: str | None
    status: str
    message: str
    downloaded_path: str | None = None
    backup_path: str | None = None
    annotations_ported: int = 0
    annotations_failed: int = 0
    unported_annotations_file: str | None = None


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []
    return [text]


def _first_string(value: Any) -> str:
    values = _as_list(value)
    if not values:
        return ""
    return values[0]


def is_arxiv_bibcode(bibcode: str) -> bool:
    """Return True if bibcode appears to be arXiv-derived."""
    if not bibcode:
        return False
    return bool(ARXIV_BIBCODE_RE.search(bibcode) or "arxiv" in bibcode.lower())


def is_arxiv_doi(doi: str) -> bool:
    """Return True if DOI is arXiv DOI namespace."""
    if not doi:
        return False
    return doi.lower().startswith(ARXIV_DOI_PREFIX)


def is_arxiv_entry(entry: dict[str, Any]) -> bool:
    """Detect whether sidecar metadata corresponds to arXiv source."""
    source = str(entry.get("source", "")).strip().lower()
    if source == "arxiv":
        return True
    if is_arxiv_bibcode(str(entry.get("bibcode", "")).strip()):
        return True

    bibstem_values = [item.lower() for item in _as_list(entry.get("bibstem"))]
    if any("arxiv" in value for value in bibstem_values):
        return True

    doi = _first_string(entry.get("doi"))
    return is_arxiv_doi(doi)


def detect_arxiv_entries(sidecar: Any) -> list[str]:
    """Return sidecar keys that are identified as arXiv entries."""
    entries: list[str] = []
    for original_filename, metadata in sidecar.files.items():
        if is_arxiv_entry(metadata):
            entries.append(original_filename)
    return entries


def _find_non_arxiv_doc(
    docs: Sequence[dict[str, Any]], arxiv_bibcode: str
) -> PublishedMatch | None:
    for doc in docs:
        primary = _first_string(doc.get("bibcode"))
        if not primary:
            continue
        alternate = _as_list(doc.get("alternate_bibcode"))
        if not is_arxiv_bibcode(primary) and (
            arxiv_bibcode in alternate or primary != arxiv_bibcode
        ):
            return PublishedMatch(bibcode=primary, document=doc)
    return None


def _chunked(items: list[str], chunk_size: int) -> list[list[str]]:
    return [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]


def _extract_doi_resolver_path(value: str) -> str | None:
    """Extract and percent-decode the DOI path from an HTTP(S) doi.org URL."""
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"}:
        return None
    if parsed.netloc.lower() not in {"doi.org", "dx.doi.org"}:
        return None
    candidate = unquote(parsed.path.lstrip("/")).strip()
    return candidate or None


def _normalize_doi(doi: str) -> str:
    raw = str(doi or "").strip()
    if not raw:
        return ""
    resolver_doi = _extract_doi_resolver_path(raw)
    if resolver_doi is not None:
        return resolver_doi.lower()
    raw = re.sub(r"^doi\s*:\s*", "", raw, flags=re.IGNORECASE)
    resolver_after_prefix = _extract_doi_resolver_path(raw)
    if resolver_after_prefix is not None:
        return resolver_after_prefix.lower()
    return raw.strip().lower()


def _doc_mentions_bibcode(doc: dict[str, Any], bibcode: str) -> bool:
    primary = _first_string(doc.get("bibcode"))
    if primary == bibcode:
        return True
    alternate = _as_list(doc.get("alternate_bibcode"))
    return bibcode in alternate


def _batch_query_docs_by_doi(
    client: "ADSClient", doi_values: list[str]
) -> dict[str, dict[str, dict[str, Any]]]:
    normalized = []
    seen: set[str] = set()
    for raw in doi_values:
        doi = _normalize_doi(raw)
        if not doi or doi in seen:
            continue
        seen.add(doi)
        normalized.append(doi)

    docs_by_doi: dict[str, dict[str, dict[str, Any]]] = {doi: {} for doi in normalized}
    for chunk in _chunked(normalized, MAX_BATCH_TERMS):
        terms = [f'doi:"{escape_query_phrase(doi)}"' for doi in chunk]
        query = " OR ".join(terms)
        rows = min(max(len(chunk) * 10, 10), 2000)
        result = client.search.query(q=query, fl=SEARCH_FIELDS, rows=rows)
        for doc in result.docs:
            primary = _first_string(doc.get("bibcode"))
            if not primary:
                continue
            for doi in _as_list(doc.get("doi")):
                doi_norm = _normalize_doi(doi)
                if doi_norm in docs_by_doi:
                    docs_by_doi[doi_norm][primary] = doc
    return docs_by_doi


def check_published_versions(
    client: "ADSClient", bibcodes: Sequence[str]
) -> dict[str, PublishedMatch | None]:
    """
    Check published counterparts for many arXiv bibcodes with batched queries.

    Returns a mapping ``arxiv_bibcode -> PublishedMatch | None``.
    """
    normalized_bibcodes = []
    seen: set[str] = set()
    for raw in bibcodes:
        bibcode = str(raw or "").strip()
        if not bibcode or bibcode in seen:
            continue
        seen.add(bibcode)
        normalized_bibcodes.append(bibcode)

    matches: dict[str, PublishedMatch | None] = {
        bibcode: None for bibcode in normalized_bibcodes
    }
    if not normalized_bibcodes:
        return matches

    docs_by_bibcode: dict[str, dict[str, dict[str, Any]]] = {
        bibcode: {} for bibcode in normalized_bibcodes
    }
    for chunk in _chunked(normalized_bibcodes, MAX_BATCH_TERMS):
        terms = [f'bibcode:"{bibcode.replace(chr(34), " ")}"' for bibcode in chunk]
        query = " OR ".join(terms)
        rows = min(max(len(chunk) * 10, 10), 2000)
        result = client.search.query(q=query, fl=SEARCH_FIELDS, rows=rows)
        for doc in result.docs:
            primary = _first_string(doc.get("bibcode"))
            if not primary:
                continue
            for bibcode in chunk:
                if _doc_mentions_bibcode(doc, bibcode):
                    docs_by_bibcode[bibcode][primary] = doc

    unresolved: list[str] = []
    dois_by_bibcode: dict[str, set[str]] = {}
    for bibcode in normalized_bibcodes:
        docs = list(docs_by_bibcode[bibcode].values())
        direct_match = _find_non_arxiv_doc(docs, bibcode)
        if direct_match is not None:
            matches[bibcode] = direct_match
            continue

        doi_values: set[str] = set()
        for doc in docs:
            for doi in _as_list(doc.get("doi")):
                doi_norm = _normalize_doi(doi)
                if doi_norm and not is_arxiv_doi(doi_norm):
                    doi_values.add(doi_norm)
        if doi_values:
            unresolved.append(bibcode)
            dois_by_bibcode[bibcode] = doi_values

    if unresolved:
        all_dois = sorted(
            {doi for doi_values in dois_by_bibcode.values() for doi in doi_values}
        )
        docs_by_doi = _batch_query_docs_by_doi(client, all_dois)
        for bibcode in unresolved:
            merged_docs: dict[str, dict[str, Any]] = {}
            for doi in dois_by_bibcode[bibcode]:
                for primary, doc in docs_by_doi.get(doi, {}).items():
                    merged_docs[primary] = doc
            doi_match = _find_non_arxiv_doc(list(merged_docs.values()), bibcode)
            if doi_match is not None:
                matches[bibcode] = doi_match

    return matches


def check_published_version(client: "ADSClient", bibcode: str) -> PublishedMatch | None:
    """
    Check whether arXiv bibcode has a published counterpart in ADS.

    Returns published bibcode + doc if found, else None.
    """
    return check_published_versions(client, [bibcode]).get(bibcode)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_bibcode_name(bibcode: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", bibcode)


def _relative_path(base_dir: Path, candidate: Path | None) -> str | None:
    if candidate is None:
        return None
    try:
        return str(candidate.relative_to(base_dir))
    except ValueError:
        return str(candidate)
