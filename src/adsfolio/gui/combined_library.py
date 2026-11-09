"""Read-only queries over the selected PDF library roots."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..pdf.registry import (
    SORTABLE_COLUMNS,
    PdfRegistry,
    RecordFact,
    mark_duplicates,
    order_duplicate_groups,
)
from .library import Column


def _combined_filename(record: Mapping[str, Any], labels: Mapping[str, str]) -> str:
    """Return the library label and the full root-relative path."""
    root = str(record.get("root") or "")
    path = str(record.get("current_path") or "")
    label = labels.get(root, Path(root).name or root)
    return f"{label} / {path}" if label else path


class CombinedLibrary:
    """Query selected registries without changing their catalog records.

    Each result keeps its original `root` and location identifier. The query
    omits missing roots. `unavailable_roots` reports them.
    """

    def __init__(
        self,
        registries: Mapping[str, PdfRegistry],
        labels: Mapping[str, str] | None = None,
    ) -> None:
        self.registries = dict(registries)
        self.labels = dict(labels or {})

    @property
    def unavailable_roots(self) -> tuple[str, ...]:
        """Return selected roots that are not currently directories."""
        return tuple(key for key in self.registries if not Path(key).is_dir())

    def query_files(
        self,
        *,
        order_by: str = "relative_path",
        descending: bool = False,
        secondary_order_by: str | None = None,
        tertiary_order_by: str | None = None,
        limit: int | None = None,
        offset: int = 0,
        **filters: Any,
    ) -> list[dict[str, Any]]:
        """Return globally sorted locations from available selected roots."""
        order_keys = [
            key
            for key in (order_by, secondary_order_by, tertiary_order_by)
            if key is not None
        ]
        invalid = next((key for key in order_keys if key not in SORTABLE_COLUMNS), None)
        if invalid is not None:
            raise ValueError(f"Unsortable column: {invalid!r}")
        if limit is not None and limit < 0:
            raise ValueError("limit must be non-negative or None")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        duplicates_only = bool(filters.get("duplicates_only"))
        records = self.query_records(**filters)
        self.sort_records(
            records,
            order_by=order_by,
            descending=descending,
            secondary_order_by=secondary_order_by,
            tertiary_order_by=tertiary_order_by,
        )
        if duplicates_only:
            order_duplicate_groups(records)
        return records[offset:] if limit is None else records[offset : offset + limit]

    def query_records(self, **filters: Any) -> list[dict[str, Any]]:
        """Return filtered locations before the requested sort.

        Records follow the selected-root order. Each root keeps its
        ``relative_path`` order. Duplicates compare digests and paper records
        across all roots.
        """
        duplicates_only = bool(filters.pop("duplicates_only", False))
        unavailable = set(self.unavailable_roots)
        records = [
            record
            for key, registry in self.registries.items()
            if key not in unavailable
            for record in registry.query_files(**filters)
        ]
        if duplicates_only:
            records = mark_duplicates(records)
        return records

    def sort_records(
        self,
        records: list[dict[str, Any]],
        *,
        order_by: str = "relative_path",
        descending: bool = False,
        secondary_order_by: str | None = None,
        tertiary_order_by: str | None = None,
    ) -> None:
        """Sort `records` in place with the `query_files` order rules."""
        order_keys = [
            key
            for key in (order_by, secondary_order_by, tertiary_order_by)
            if key is not None
        ]
        invalid = next((key for key in order_keys if key not in SORTABLE_COLUMNS), None)
        if invalid is not None:
            raise ValueError(f"Unsortable column: {invalid!r}")

        def sort_value(record: dict[str, Any], key: str) -> tuple[bool, str | int]:
            if key == "filename":
                name = str(record.get("current_path") or "")
                if not name:
                    return True, ""
                return False, _combined_filename(record, self.labels)
            return PdfRegistry._sort_value(record, key)

        for key in reversed(order_keys):
            records.sort(
                key=lambda record, sort_key=key: sort_value(record, sort_key)[1],
                reverse=descending,
            )
            records.sort(
                key=lambda record, sort_key=key: sort_value(record, sort_key)[0]
            )

    def change_token(self) -> tuple[Any, ...]:
        """Return a value that changes with any selected catalog or label."""
        unavailable = set(self.unavailable_roots)
        return (
            tuple(self.registries),
            tuple(sorted(unavailable)),
            tuple(sorted(self.labels.items())),
            tuple(
                registry.change_token()
                for key, registry in self.registries.items()
                if key not in unavailable
            ),
        )

    def record_facts(self) -> tuple[RecordFact, ...]:
        """Return cached count facts from the available selected roots."""
        unavailable = set(self.unavailable_roots)
        return tuple(
            fact
            for key, registry in self.registries.items()
            if key not in unavailable
            for fact in registry.record_facts()
        )

    def search_documents(
        self,
    ) -> tuple[tuple[int, dict[str, str], dict[str, str]], ...]:
        """Return cached search documents from the available selected roots."""
        unavailable = set(self.unavailable_roots)
        return tuple(
            entry
            for key, registry in self.registries.items()
            if key not in unavailable
            for entry in registry.search_documents()
        )

    def search_location_keys(
        self,
        search: str,
        *,
        exact: bool = False,
        wildcards: bool = False,
        fields: Sequence[str] = (),
    ) -> frozenset[tuple[str, int]]:
        """Return ``(root, location_id)`` keys that match `search`."""
        unavailable = set(self.unavailable_roots)
        keys: set[tuple[str, int]] = set()
        for key, registry in self.registries.items():
            if key not in unavailable:
                keys.update(
                    registry.search_location_keys(
                        search, exact=exact, wildcards=wildcards, fields=fields
                    )
                )
        return frozenset(keys)

    def count_files_matching(self, **filters: Any) -> int:
        """Count locations across the selected roots."""
        if filters.get("duplicates_only"):
            return len(self.query_files(**filters))
        unavailable = set(self.unavailable_roots)
        return sum(
            registry.count_files_matching(**filters)
            for key, registry in self.registries.items()
            if key not in unavailable
        )


def combined_columns(
    columns: Sequence[Column], labels: Mapping[str, str]
) -> tuple[Column, ...]:
    """Show each file's library and root-relative path in the combined view."""

    def filename(record: dict[str, Any]) -> str:
        return _combined_filename(record, labels)

    return tuple(
        replace(column, header="Library / Filename", getter=filename)
        if column.key == "filename"
        else column
        for column in columns
    )
