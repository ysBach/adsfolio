"""ADS query + classification helpers for ``adsapi cv``."""

from __future__ import annotations

import logging
from typing import Any

from ..client import ADSClient
from ..config import ADSError
from .config import CVConfig, build_query

CV_FIELDS = [
    "bibcode",
    "title",
    "author",
    "first_author",
    "year",
    "date",
    "author_count",
    "citation_count",
    "bibstem",
    "volume",
    "issue",
    "page",
    "doi",
    "property",
    "comment",
    "pub",
]

LOGGER = logging.getLogger(__name__)


def fetch_publications(client: ADSClient, cfg: CVConfig) -> list[dict[str, Any]]:
    """Query ADS and return publication docs sorted by date descending."""
    result = client.search.query_all(
        q=build_query(cfg),
        fl=CV_FIELDS,
        sort="date desc",
    )
    return list(result.docs)


def apply_author_format_overrides(
    docs: list[dict[str, Any]],
    client: ADSClient,
    cfg: CVConfig,
) -> list[dict[str, Any]]:
    """Attach author-text overrides for ``ads_custom:`` formats."""
    if not cfg.author_format.startswith("ads_custom:"):
        return [dict(doc) for doc in docs]

    format_string = cfg.author_format.split(":", 1)[1]
    overridden_docs: list[dict[str, Any]] = []
    for doc in docs:
        clone = dict(doc)
        bibcode = str(get_first_item(clone, "bibcode") or "").strip()
        if not bibcode:
            overridden_docs.append(clone)
            continue
        try:
            author_text = client.export.custom(
                [bibcode], format_string=format_string
            ).strip()
        except ADSError as exc:
            LOGGER.warning(
                "Failed ads_custom export for %s with format %s: %s",
                bibcode,
                format_string,
                exc,
            )
            overridden_docs.append(clone)
            continue
        if author_text:
            clone["_adsapi_author_text"] = author_text
        overridden_docs.append(clone)
    return overridden_docs


def get_first_item(doc: dict[str, Any], field: str) -> Any:
    """Return first item when a field is list-like, else return the field value."""
    value = doc.get(field)
    if isinstance(value, list):
        return value[0] if value else None
    return value


def classify_publications(
    docs: list[dict[str, Any]],
    first_author_prefix: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split docs into first-authored and co-authored groups."""
    first_docs: list[dict[str, Any]] = []
    co_docs: list[dict[str, Any]] = []

    prefix = first_author_prefix.lower().strip()
    for doc in docs:
        first_author = str(get_first_item(doc, "first_author") or "").lower()
        if prefix and first_author.startswith(prefix):
            first_docs.append(doc)
        else:
            co_docs.append(doc)
    return first_docs, co_docs
