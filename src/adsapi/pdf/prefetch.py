"""Publication prefetch helpers to reduce first-run ADS query volume."""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..api.search import escape_query_phrase
from ..config import Config
from .identifier import PdfIdentifier

PREFETCH_FIELDS = [
    "abstract",
    "bibcode",
    "citation_count",
    "doi",
    "title",
    "author",
    "author_count",
    "year",
    "bibstem",
    "volume",
    "page",
    "pub",
]
BIGQUERY_CHUNK_SIZE = 2000

__all__ = [
    "PublicationProfile",
    "PublicationIndex",
    "load_profile",
    "query_publications",
    "build_publication_index",
    "match_prefetch_from_doi_candidates",
    "match_pdf_from_prefetch",
    "estimated_query_budget",
]


@dataclass
class PublicationProfile:
    """Saved setup profile for low-query identify bootstrap."""

    orcid: str = ""
    query_names: list[str] = field(default_factory=list)
    debut_year: int | None = None
    retire_year: int | None = None
    library_ids: list[str] = field(default_factory=list)
    end_year: int | None = None

    def __post_init__(self) -> None:
        """Keep the old in-memory name usable while storage uses `end_year`."""
        if self.end_year is None:
            self.end_year = self.retire_year
        self.retire_year = self.end_year

    @property
    def has_identity(self) -> bool:
        return bool(self.orcid or self.query_names or self.library_ids)


@dataclass
class PublicationIndex:
    """In-memory lookup index built from prefetched ADS docs."""

    documents: list[dict[str, Any]]
    by_doi: dict[str, list[dict[str, Any]]]
    by_filename_key: dict[tuple[str, str, str, str, str], list[dict[str, Any]]]


def load_profile() -> PublicationProfile:
    """Load setup profile from config storage."""
    return Config.load_user_profile().publication_profile()


def _as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _build_profile_query(profile: PublicationProfile) -> str:
    clauses: list[str] = []
    if profile.orcid:
        clauses.append(f'orcid_pub:"{escape_query_phrase(profile.orcid)}"')
    for name in profile.query_names:
        clauses.append(f'author:"{escape_query_phrase(name)}"')

    if not clauses:
        return ""

    base = " OR ".join(clauses)
    if len(clauses) > 1:
        base = f"({base})"

    start = "*" if profile.debut_year is None else str(profile.debut_year)
    end = "*" if profile.end_year is None else str(profile.end_year)
    if start == "*" and end == "*":
        return base
    return f"{base} AND year:[{start} TO {end}]"


def _chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _clean_bibcodes(bibcodes: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for bibcode in bibcodes:
        value = str(bibcode).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        cleaned.append(value)
    return cleaned


def _fetch_library_bibcodes(client: Any, library_ids: list[str]) -> list[str]:
    bibcodes: list[str] = []
    for library_id in _clean_bibcodes(library_ids):
        start = 0
        page_size = BIGQUERY_CHUNK_SIZE
        while True:
            library = client.libraries.get_library(
                library_id,
                raw=True,
                start=start,
                rows=page_size,
            )
            docs = _clean_bibcodes(list(getattr(library, "documents", [])))
            bibcodes.extend(docs)
            fetched = len(docs)
            total = int(getattr(library, "num_documents", 0) or 0)
            if fetched < page_size:
                break
            if total and start + fetched >= total:
                break
            start += fetched
    return _clean_bibcodes(bibcodes)


def _fetch_docs_by_bibcodes(
    client: Any,
    bibcodes: list[str],
    fields: list[str],
) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    cleaned = _clean_bibcodes(bibcodes)
    for chunk in _chunked(cleaned, BIGQUERY_CHUNK_SIZE):
        if not chunk:
            continue
        response = client.search.bigquery(
            bibcodes=chunk,
            fl=fields,
            rows=len(chunk),
        )
        docs.extend(response.docs)
    return docs


def query_publications(
    client: Any, profile: PublicationProfile
) -> list[dict[str, Any]]:
    """Query ADS publications for profile identity (ORCID + optional names)."""
    dedup: dict[str, dict[str, Any]] = {}

    query = _build_profile_query(profile)
    if query:
        result = client.search.query_all(
            q=query,
            fl=PREFETCH_FIELDS,
            page_size=2000,
            max_records=10000,
        )
        identity_docs = result.docs
    else:
        identity_docs = []

    library_bibcodes = _fetch_library_bibcodes(client, profile.library_ids)
    library_docs = _fetch_docs_by_bibcodes(client, library_bibcodes, PREFETCH_FIELDS)

    for doc in [*identity_docs, *library_docs]:
        bibcode = str(doc.get("bibcode", "")).strip()
        if not bibcode:
            continue
        dedup[bibcode] = doc
    return list(dedup.values())


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []
    return [text]


def _first_author_surname(authors: Any) -> str:
    if not authors:
        return ""
    first = _as_list(authors)
    if not first:
        return ""
    head = first[0]
    if "," in head:
        return head.split(",", 1)[0].strip().lower()
    parts = head.split()
    if not parts:
        return ""
    return parts[-1].strip().lower()


def _norm_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _norm_page(value: str) -> str:
    digits = re.sub(r"[^0-9]", "", value)
    return digits or _norm_text(value)


def _author_key(author: str) -> str:
    first = str(author).split("+", 1)[0].strip()
    if "," in first:
        first = first.split(",", 1)[0].strip()
    cleaned = re.sub(r"[^A-Za-z'`-]", "", first)
    if not cleaned:
        return ""
    match = re.match(r"^(.*[a-z])([A-Z]{1,6})$", cleaned)
    if match is not None:
        cleaned = match.group(1)
    return _norm_text(cleaned)


def _filename_key(
    author: str,
    year: str,
    journal: str,
    volume: str,
    page: str,
) -> tuple[str, str, str, str, str]:
    return (
        _author_key(author),
        _norm_text(year),
        _norm_text(journal),
        _norm_text(volume),
        _norm_page(page),
    )


def _doc_filename_key(doc: dict[str, Any]) -> tuple[str, str, str, str, str]:
    author = _first_author_surname(doc.get("author"))
    year = str(doc.get("year", ""))
    bibstem = _as_list(doc.get("bibstem"))
    journal = bibstem[0] if bibstem else str(doc.get("pub", ""))
    volume = str(doc.get("volume", ""))
    page_list = _as_list(doc.get("page"))
    page = page_list[0] if page_list else ""
    return _filename_key(author, year, journal, volume, page)


def build_publication_index(documents: list[dict[str, Any]]) -> PublicationIndex:
    """Build DOI and filename-derived lookup indexes."""
    by_doi: dict[str, list[dict[str, Any]]] = {}
    by_filename_key: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = {}

    for doc in documents:
        for doi in _as_list(doc.get("doi")):
            key = doi.strip().lower()
            if key:
                by_doi.setdefault(key, []).append(doc)

        key = _doc_filename_key(doc)
        if any(key):
            by_filename_key.setdefault(key, []).append(doc)

    return PublicationIndex(
        documents=documents,
        by_doi=by_doi,
        by_filename_key=by_filename_key,
    )


def _unique_match(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(candidates) != 1:
        return None
    return candidates[0]


def match_prefetch_from_doi_candidates(
    metadata_doi: str | None,
    text_doi: str | None,
    index: PublicationIndex,
) -> dict[str, Any] | None:
    """Match prefetched doc using pre-extracted DOI candidates."""
    if metadata_doi:
        return _unique_match(index.by_doi.get(str(metadata_doi).lower(), []))
    if text_doi:
        return _unique_match(index.by_doi.get(str(text_doi).lower(), []))
    return None


def match_pdf_from_prefetch(
    pdf_path: Path,
    identifier: PdfIdentifier,
    index: PublicationIndex,
) -> dict[str, Any] | None:
    """Match PDF against prefetched docs without performing ADS query."""
    metadata_doi, text_doi = identifier.extract_doi_candidates(pdf_path)
    return match_prefetch_from_doi_candidates(metadata_doi, text_doi, index)


def estimated_query_budget(
    unmatched_file_count: int, strategies_per_file: int = 4
) -> int:
    """Rough upper bound estimate for ADS query count during fallback phase."""
    return max(0, unmatched_file_count * max(1, strategies_per_file))
