"""Qt-free data definitions for the PDF library table."""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..citations import CitationFormatter, CitationStyle, citation_preference_key
from ..formatting import html_to_text
from ..pdf.arxiv import arxiv_id
from ..pdf.classification import ResolutionState, resolution_state, verification_stale
from ..pdf.registry import DUPLICATE_LABELS, RecordFact, duplicate_kinds, stored_count

ISO8601_TIMESTAMP_FORMAT = "iso8601"
STALE_VERIFICATION_TOOLTIP = (
    "PDF changed since identification. Run Identify to verify it again."
)
SAVED_AUTHOR_COUNT_TOOLTIP = (
    "Saved author count from metadata or a manual edit. "
    "It is a snapshot, not a live ADS value."
)
SAVED_CITATION_COUNT_TOOLTIP = (
    "Saved citation count from metadata or a manual edit. "
    "It is a snapshot, not a live ADS value."
)


def format_count(record: dict[str, Any], key: str) -> str:
    """Return a saved count as text, or an empty string when it is unknown.

    Zero is shown as `"0"`. The same value drives numeric sorting in
    ``PdfRegistry.query_files()``.
    """
    count = stored_count(record, key)
    return "" if count is None else str(count)


class LibraryView(str, Enum):
    """A virtual view over records in one PDF library."""

    ALL = "all"
    RESOLVED = "resolved"
    IDENTIFIED_WITHOUT_ADS = "identified_without_ads"
    UNRESOLVED = "unresolved"
    ARXIV_ONLY = "arxiv_only"
    DUPLICATES = "duplicates"

    @property
    def label(self) -> str:
        """Name each catalog view without claiming current PDF verification."""
        return {
            self.ALL: "All PDFs",
            self.RESOLVED: "ADS linked",
            self.IDENTIFIED_WITHOUT_ADS: "Identified without ADS",
            self.UNRESOLVED: "Needs identification",
            self.ARXIV_ONLY: "arXiv",
            self.DUPLICATES: "Duplicates",
        }[self]

    @property
    def description(self) -> str:
        """Explain identification evidence and the action for each view."""
        if self is self.ALL:
            return (
                "All current PDFs. ADS remains the primary identification service. "
                "An ADS link alone does not prove the PDF content matches."
            )
        if self is self.RESOLVED:
            return (
                "These PDFs have current saved ADS links. "
                "A linked ADS record does not prove the PDF content matches. "
                "Changed PDFs appear under Needs identification."
            )
        if self is self.IDENTIFIED_WITHOUT_ADS:
            return (
                "These PDFs have current verified or user-confirmed identities without ADS links. "
                "They do not need identification again. ADS rechecks can add links later."
            )
        if self is self.UNRESOLVED:
            return (
                "These PDFs need identification or review of changed files. "
                "Usable metadata alone does not confirm a match. "
                "Run Identify or use Update by ID for the selected PDF."
            )
        return self.label


def _metadata(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("metadata")
    return value if isinstance(value, dict) else {}


def _first(value: Any) -> str:
    """Return a display string for a scalar or sequence value."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return html_to_text(str(value[0])) if value and value[0] is not None else ""
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
    sortable: bool = True


@dataclass(frozen=True)
class FileQuery:
    """Current view, search, and sort settings for a library table."""

    view: LibraryView = LibraryView.ALL
    search: str = ""
    # Match accents and letter case exactly. By default both are ignored.
    search_exact: bool = False
    # Read % and _ in `search` as wildcards. By default they are literal.
    search_wildcards: bool = False
    # Fields that plain search terms search. Empty means all search text.
    search_fields: tuple[str, ...] = ()
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
            filters["identification"] = "ads_linked"
        elif self.view is LibraryView.IDENTIFIED_WITHOUT_ADS:
            filters["identification"] = "identified_without_ads"
        elif self.view is LibraryView.UNRESOLVED:
            filters["identification"] = "needs_identification"
        elif self.view is LibraryView.ARXIV_ONLY:
            filters["source"] = "arxiv"
        elif self.view is LibraryView.DUPLICATES:
            filters["duplicates_only"] = True
        if self.search:
            filters["search"] = self.search
            filters["search_exact"] = self.search_exact
            filters["search_wildcards"] = self.search_wildcards
            filters["search_fields"] = self.search_fields
        return filters


_VIEW_GROUPS = {
    LibraryView.RESOLVED: "ads_linked",
    LibraryView.IDENTIFIED_WITHOUT_ADS: "identified_without_ads",
    LibraryView.UNRESOLVED: "needs_identification",
}


def count_matching(
    facts: Iterable[RecordFact],
    view: LibraryView = LibraryView.ALL,
    *,
    group: str | None = None,
    search_keys: Collection[tuple[str, int]] | None = None,
) -> int:
    """Count facts with the filters that ``FileQuery(view).to_filters()`` sets.

    The filters apply in the registry order: search, identification group,
    source, and then duplicates. Duplicates are digests or paper records that
    occur more than once among the rows that remain after the earlier filters.

    Parameters
    ----------
    facts : Iterable[RecordFact]
        Facts from ``record_facts()``.
    view : LibraryView, optional
        View whose filters apply.
    group : str, optional
        Identification group. It replaces the group of an identification view.
    search_keys : Collection[tuple[str, int]], optional
        ``(root, location_id)`` keys that match the search. `None` means no search.
    """
    rows: Iterable[RecordFact] = facts
    if search_keys is not None:
        rows = [fact for fact in rows if (fact.root, fact.location_id) in search_keys]
    wanted_group = group if group is not None else _VIEW_GROUPS.get(view)
    if wanted_group is not None:
        rows = [fact for fact in rows if fact.group == wanted_group]
    if view is LibraryView.ARXIV_ONLY:
        rows = [fact for fact in rows if fact.source == "arxiv"]
    if view is LibraryView.DUPLICATES:
        rows = list(rows)
        kinds = duplicate_kinds(
            [(fact.sha256, fact.document_id, fact.edition) for fact in rows]
        )
        return sum(1 for kind in kinds if kind)
    return sum(1 for _fact in rows)


BASE_COLUMNS: tuple[Column, ...] = (
    Column("title", "Title", 380, _title),
    Column("author", "First author", 160, _author),
    Column(
        "author_count",
        "N_author",
        70,
        lambda record: format_count(record, "author_count"),
    ),
    Column("year", "Year", 60, lambda record: _text(record, "year")),
    Column("pub", "Journal", 120, _pub),
    Column(
        "citation_count",
        "Citations",
        75,
        lambda record: format_count(record, "citation_count"),
    ),
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
    # Shown only in the Duplicates view.
    Column(
        "duplicate_kind",
        "Duplicate",
        90,
        lambda record: DUPLICATE_LABELS.get(
            str(record.get("duplicate_kind") or ""), ""
        ),
    ),
)

COLUMNS = BASE_COLUMNS


def build_columns(styles: Sequence[CitationStyle] = ()) -> tuple[Column, ...]:
    """Construct the active table column definitions including custom citation columns.

    Parameters
    ----------
    styles : Sequence[CitationStyle], optional
        Custom citation styles to insert as runtime columns, by default ().

    Returns
    -------
    tuple[Column, ...]
        Immutable column sequence with generated citation columns inserted
        directly after Bibcode.
    """
    if not styles:
        return BASE_COLUMNS

    formatter = CitationFormatter()
    generated: list[Column] = []
    for style in styles:

        def getter(
            record: dict[str, Any],
            bound_style: CitationStyle = style,
        ) -> str:
            return formatter.render(bound_style, record)

        generated.append(
            Column(
                key=citation_preference_key(style.id),
                header=style.name,
                width=190,
                getter=getter,
                sortable=False,
            )
        )

    bibcode_index = next(
        (i for i, col in enumerate(BASE_COLUMNS) if col.key == "bibcode"),
        len(BASE_COLUMNS) - 1,
    )
    return (
        BASE_COLUMNS[: bibcode_index + 1]
        + tuple(generated)
        + BASE_COLUMNS[bibcode_index + 1 :]
    )


__all__ = [
    "BASE_COLUMNS",
    "COLUMNS",
    "ISO8601_TIMESTAMP_FORMAT",
    "Column",
    "FileQuery",
    "LibraryView",
    "ResolutionState",
    "ads_url",
    "arxiv_id",
    "arxiv_url",
    "build_columns",
    "format_count",
    "format_first_seen",
    "format_mtime",
    "resolution_state",
    "verification_stale",
]
