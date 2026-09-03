"""Qt-free data definitions for the PDF library table."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from ..formatting import html_to_text

ISO8601_TIMESTAMP_FORMAT = "iso8601"
_ARXIV_ID_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])((?:\d{4}\.\d{4,5}|[A-Za-z][A-Za-z0-9-]*(?:\.[A-Za-z]{2})?/\d{7})(?:v\d+)?)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_LEGACY_ARXIV_BIBCODE_PATTERN = re.compile(
    r"\d{4}arXiv(?P<identifier>\d{9})",
    re.IGNORECASE,
)


class LibraryView(str, Enum):
    """A virtual view over records in one PDF library."""

    ALL = "all"
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    ARXIV_ONLY = "arxiv_only"
    DUPLICATES = "duplicates"


def _metadata(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("metadata")
    return value if isinstance(value, dict) else {}


def _first(value: Any) -> str:
    """Return a display string for a scalar or sequence value."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value and value[0] is not None else ""
    return html_to_text(str(value))


def _text(record: dict[str, Any], key: str) -> str:
    value = record.get(key)
    if value in (None, ""):
        value = _metadata(record).get(key)
    return _first(value)


def _title(record: dict[str, Any]) -> str:
    return _text(record, "title")


def _author(record: dict[str, Any]) -> str:
    return _text(record, "author")


def _pub(record: dict[str, Any]) -> str:
    metadata = _metadata(record)
    bibstem = _first(metadata.get("bibstem"))
    return bibstem or _text(record, "pub")


def _filename(record: dict[str, Any]) -> str:
    current_path = _text(record, "current_path")
    return Path(current_path).name if current_path else ""


def _normalize_arxiv_candidate(value: Any) -> str:
    text = html_to_text(str(value or ""))
    legacy_match = _LEGACY_ARXIV_BIBCODE_PATTERN.search(text)
    if legacy_match:
        identifier = legacy_match.group("identifier")
        return f"{identifier[:4]}.{identifier[4:]}"
    match = _ARXIV_ID_PATTERN.search(text)
    return match.group(1) if match else ""


def arxiv_id(record: dict[str, Any]) -> str:
    """Return the normalized arXiv identifier embedded in a file record."""
    metadata = _metadata(record)
    candidates = [
        *(metadata.get(key, []) for key in ("arxiv", "arxiv_id", "identifier")),
        record.get("arxiv"),
        record.get("arxiv_id"),
        record.get("doi"),
        metadata.get("doi"),
        record.get("bibcode"),
        metadata.get("bibcode"),
        record.get("current_path"),
    ]
    for candidate in candidates:
        values = candidate if isinstance(candidate, (list, tuple)) else [candidate]
        for value in values:
            normalized = _normalize_arxiv_candidate(value)
            if normalized:
                return normalized
    return ""


def ads_url(bibcode: str) -> str:
    """Return the ADS abstract URL for `bibcode`."""
    return f"https://adsabs.harvard.edu/abs/{quote(str(bibcode), safe='')}"


def arxiv_url(identifier: str) -> str:
    """Return the arXiv abstract URL for a normalized identifier."""
    return f"https://arxiv.org/abs/{quote(str(identifier), safe='/.')}"


def _size(record: dict[str, Any]) -> str:
    value = record.get("size_bytes")
    if value in (None, ""):
        return ""
    try:
        size = float(value)
    except (TypeError, ValueError):
        return str(value)
    if size < 0:
        return str(value)
    units = ("B", "KB", "MB", "GB", "TB")
    unit_index = 0
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    if unit_index == 0:
        return f"{int(size)} B"
    return f"{size:.1f} {units[unit_index]}"


def format_mtime(record: dict[str, Any], timestamp_format: str) -> str:
    """Format a record timestamp using ISO 8601 or a ``strftime`` pattern."""
    value = record.get("mtime_ns")
    if value in (None, ""):
        return ""
    try:
        timestamp = int(value) / 1_000_000_000
        moment = datetime.fromtimestamp(timestamp).astimezone()
        if timestamp_format == ISO8601_TIMESTAMP_FORMAT:
            return moment.isoformat(timespec="seconds")
        return moment.strftime(timestamp_format)
    except (TypeError, ValueError, OSError, OverflowError):
        return str(value)


def format_first_seen(record: dict[str, Any], timestamp_format: str) -> str:
    """Format a location's ISO first-seen timestamp.

    Parameters
    ----------
    record
        File record containing `first_seen_at`.
    timestamp_format
        `iso8601` or a ``strftime`` pattern.

    Returns
    -------
    str
        Formatted local timestamp, or an empty string for missing or malformed
        values.
    """
    value = record.get("first_seen_at")
    if value in (None, ""):
        return ""
    try:
        moment = datetime.fromisoformat(str(value)).astimezone()
        if timestamp_format == ISO8601_TIMESTAMP_FORMAT:
            return moment.isoformat(timespec="seconds")
        return moment.strftime(timestamp_format)
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _mtime(record: dict[str, Any]) -> str:
    return format_mtime(record, ISO8601_TIMESTAMP_FORMAT)


@dataclass(frozen=True)
class Column:
    """Definition of one table column."""

    key: str
    header: str
    width: int
    getter: Callable[[dict[str, Any]], str]


@dataclass(frozen=True)
class FileQuery:
    """Current view, search, and sort settings for a library table."""

    view: LibraryView = LibraryView.ALL
    search: str = ""
    order_by: str = "first_seen_at"
    descending: bool = True
    secondary_order_by: str | None = None
    tertiary_order_by: str | None = None

    def to_filters(self) -> dict[str, Any]:
        """Translate this query into ``PdfRegistry`` filter arguments.

        Returns
        -------
        dict
            Keyword arguments accepted by ``PdfRegistry.query_files``.
        """
        filters: dict[str, Any] = {}
        if self.view is LibraryView.RESOLVED:
            filters["resolved"] = True
        elif self.view is LibraryView.UNRESOLVED:
            filters["resolved"] = False
        elif self.view is LibraryView.ARXIV_ONLY:
            filters["source"] = "arxiv"
        elif self.view is LibraryView.DUPLICATES:
            filters["duplicates_only"] = True
        if self.search:
            filters["search"] = self.search
        return filters


COLUMNS: tuple[Column, ...] = (
    Column("title", "Title", 380, _title),
    Column("author", "First author", 160, _author),
    Column("year", "Year", 60, lambda record: _text(record, "year")),
    Column("pub", "Journal", 120, _pub),
    Column("bibcode", "Bibcode", 190, lambda record: _text(record, "bibcode")),
    Column("arxiv", "arXiv", 120, arxiv_id),
    Column("status", "", 22, lambda record: _text(record, "status")),
    Column("filename", "Filename", 260, _filename),
    Column("size_bytes", "Size", 80, _size),
    Column(
        "first_seen_at",
        "Added",
        140,
        lambda record: format_first_seen(record, ISO8601_TIMESTAMP_FORMAT),
    ),
    Column("mtime_ns", "Modified", 140, _mtime),
)


__all__ = [
    "COLUMNS",
    "ISO8601_TIMESTAMP_FORMAT",
    "Column",
    "FileQuery",
    "LibraryView",
    "ads_url",
    "arxiv_id",
    "arxiv_url",
    "format_first_seen",
    "format_mtime",
]
