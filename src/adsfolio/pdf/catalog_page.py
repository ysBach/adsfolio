"""Connection-local snapshots for paged catalog reads."""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from .catalog import PdfCatalog

_SORT_COLUMNS = {
    "first_seen_at": (
        "CASE WHEN l.first_seen_at IS NULL OR l.first_seen_at = '' "
        "THEN adsfolio_metadata_first_seen_sort(a.metadata_json) "
        "ELSE l.first_seen_at END"
    ),
    "relative_path": "l.relative_path",
    "size_bytes": "l.size_bytes",
    "mtime_ns": "l.mtime_ns",
}


def _metadata_first_seen_sort(metadata_json: str | None) -> str | None:
    """Match the registry sort value for a missing stored first-seen date."""
    metadata = json.loads(metadata_json or "{}")
    if not isinstance(metadata, dict):
        metadata = {}
    from .registry import CentralPdfRegistry

    missing, value = CentralPdfRegistry._sort_value(
        {"first_seen_at": None, "metadata": metadata}, "first_seen_at"
    )
    return None if missing else str(value)


class TempSnapshotUnavailable(RuntimeError):
    """Report a non-lock failure while creating the TEMP snapshot table."""


def _is_lock_error(error: sqlite3.Error) -> bool:
    """Return whether SQLite reports a busy or locked database."""
    code = getattr(error, "sqlite_errorcode", None)
    if code is not None and code & 0xFF in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
        return True
    message = str(error).casefold()
    return "locked" in message or "busy" in message


class CatalogPageSnapshot:
    """Hold frozen raw catalog rows in a private SQLite TEMP table.

    The snapshot connection is read-only for the catalog file. SQLite permits
    TEMP writes on that connection. Each statement ends before the next page
    request, so the snapshot does not retain a read transaction.
    """

    def __init__(
        self,
        path: Path,
        root: str,
        order_by: str,
        descending: bool,
        secondary_order_by: str | None,
        tertiary_order_by: str | None,
    ) -> None:
        keys = [
            key
            for key in (order_by, secondary_order_by, tertiary_order_by)
            if key is not None
        ]
        if not keys or any(key not in _SORT_COLUMNS for key in keys):
            raise ValueError("Unsupported catalog page sort")
        self._table = f"catalog_page_{uuid.uuid4().hex}"
        self._connection = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=0,
            isolation_level=None,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.create_function(
            "adsfolio_metadata_first_seen_sort",
            1,
            _metadata_first_seen_sort,
            deterministic=True,
        )
        try:
            self._connection.execute("PRAGMA busy_timeout = 0")
            order_parts = []
            for key in keys:
                expression = _SORT_COLUMNS[key]
                if key == "first_seen_at":
                    missing = f"({expression} IS NULL)"
                else:
                    missing = f"({expression} IS NULL OR {expression} = '')"
                direction = "DESC" if descending else "ASC"
                order_parts.extend((f"{missing} ASC", f"{expression} {direction}"))
            order_parts.extend(("l.relative_path ASC", "l.id ASC"))
            try:
                self._connection.execute(
                    f"""CREATE TEMP TABLE {self._table} AS
                    SELECT
                        l.id AS location_id, l.attachment_id,
                        r.canonical_path AS root, l.relative_path,
                        l.size_bytes, l.mtime_ns, l.device, l.inode,
                        l.current_version_id, l.first_seen_at, l.missing_at,
                        a.document_id, a.status, a.metadata_json, v.sha256
                    FROM file_locations AS l
                    JOIN library_roots AS r ON r.id = l.root_id
                    JOIN attachments AS a ON a.id = l.attachment_id
                    LEFT JOIN content_versions AS v ON v.id = l.current_version_id
                    WHERE l.missing_at IS NULL AND r.canonical_path = ?
                    ORDER BY {", ".join(order_parts)}""",
                    (root,),
                )
            except sqlite3.Error as exc:
                self.close()
                if _is_lock_error(exc):
                    raise
                raise TempSnapshotUnavailable(str(exc)) from exc
            self._total = int(
                self._connection.execute(
                    f"SELECT COUNT(*) FROM {self._table}"
                ).fetchone()[0]
            )
        except BaseException:
            self.close()
            raise

    @property
    def total(self) -> int:
        """Return the frozen row count."""
        return self._total

    def fetch(self, offset: int, limit: int) -> list[dict[str, Any]]:
        """Return and hydrate a bounded page in frozen order."""
        if offset < 0 or limit < 0:
            raise ValueError("Page offset and limit must be non-negative")
        rows = self._connection.execute(
            f"SELECT rowid AS _ordinal, * FROM {self._table} "
            "WHERE rowid > ? AND rowid <= ? ORDER BY rowid",
            (offset, offset + limit),
        ).fetchall()
        return [PdfCatalog._record_from_row(row) for row in rows]

    def close(self) -> None:
        """Drop TEMP state by closing its owning connection."""
        connection = getattr(self, "_connection", None)
        if connection is not None:
            self._connection = None
            connection.close()
