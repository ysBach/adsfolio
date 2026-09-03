"""Central local catalog for PDF attachments and observed file locations."""

from __future__ import annotations

import copy
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from ..config import Config
from .sidecar import SIDECAR_FILENAME, Sidecar

CATALOG_FILENAME = "pdf-library.sqlite"
CATALOG_SCHEMA_VERSION = 2
LEGACY_REGISTRY_FILENAME = ".adsapi-pdfs.sqlite"
LOCKED_METADATA_FIELDS_KEY = "_adsapi_locked_fields"


@dataclass(frozen=True)
class CatalogObservation:
    """Result of registering one observed filesystem location."""

    attachment_id: int
    location_id: int
    version_id: int | None
    matched_by: str


@dataclass(frozen=True)
class LegacyMigrationReport:
    """Summary of one legacy-root migration."""

    imported: int


class PdfCatalog:
    """Store PDF attachment identity independently of filesystem paths."""

    def __init__(self, path: Path | None = None) -> None:
        """Open the central catalog database.

        Parameters
        ----------
        path
            Database path. When omitted, the user configuration directory is
            used so library roots do not contain live SQLite state.
        """
        self.path = (
            Path(path)
            if path is not None
            else Config.get_config_dir() / CATALOG_FILENAME
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, timeout=5.0)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 5000")
        try:
            self._connection.execute("PRAGMA journal_mode = WAL")
        except sqlite3.OperationalError:
            pass
        self._initialize_schema()

    @property
    def schema_version(self) -> int:
        """Return the catalog schema version."""
        row = self._connection.execute(
            "SELECT value FROM catalog_meta WHERE key = 'schema_version'"
        ).fetchone()
        return int(row["value"]) if row is not None else 0

    def observe_file(
        self,
        root: Path,
        relative_path: str,
        *,
        sha256: str | None,
        size_bytes: int,
        mtime_ns: int,
        device: int | None = None,
        inode: int | None = None,
        metadata: Mapping[str, Any] | None = None,
        status: str | None = None,
    ) -> CatalogObservation:
        """Register one filesystem observation atomically."""
        with self._connection:
            return self._observe_file(
                root,
                relative_path,
                sha256=sha256,
                size_bytes=size_bytes,
                mtime_ns=mtime_ns,
                device=device,
                inode=inode,
                metadata=metadata,
                status=status,
            )

    def _observe_file(
        self,
        root: Path,
        relative_path: str,
        *,
        sha256: str | None,
        size_bytes: int,
        mtime_ns: int,
        device: int | None = None,
        inode: int | None = None,
        metadata: Mapping[str, Any] | None = None,
        status: str | None = None,
    ) -> CatalogObservation:
        """Register one observation without opening a transaction.

        Matching uses an active path first, then a unique filesystem identity,
        then a unique content digest. Ambiguous matches create a new
        attachment instead of silently merging records.
        """
        normalized_root = self._normalize_root(root)
        normalized_path = self._normalize_relative_path(relative_path)
        normalized_sha = str(sha256 or "").strip() or None
        if size_bytes < 0 or mtime_ns < 0:
            raise ValueError("size_bytes and mtime_ns must be non-negative")

        root_id = self._ensure_root(normalized_root)
        location = self._active_location(root_id, normalized_path)
        matched_by = "path"
        if location is not None:
            attachment_id = int(location["attachment_id"])
        else:
            attachment_id, matched_by = self._match_attachment(
                normalized_sha,
                device,
                inode,
            )
            if attachment_id is None:
                attachment_id = self._create_attachment(
                    metadata=metadata,
                    status=status,
                )

        location = self._location_for_attachment_path(
            root_id,
            normalized_path,
            attachment_id,
        )
        content_changed = False
        if location is not None and location["current_version_id"] is not None:
            version = self._connection.execute(
                "SELECT sha256 FROM content_versions WHERE id = ?",
                (location["current_version_id"],),
            ).fetchone()
            content_changed = version is None or version["sha256"] != normalized_sha

        attachment = self._attachment(attachment_id)
        if attachment is None:
            raise RuntimeError(f"Catalog attachment disappeared: {attachment_id}")
        existing_metadata = json.loads(attachment["metadata_json"] or "{}")
        if not isinstance(existing_metadata, dict):
            existing_metadata = {}
        existing_status = str(attachment["status"] or "unresolved")
        incoming_metadata = dict(metadata or {})
        if existing_status == "resolved" and status != "resolved":
            incoming_metadata = {
                key: value
                for key, value in incoming_metadata.items()
                if key not in existing_metadata
                or existing_metadata[key] in (None, "", [])
            }
        merged_metadata = self._merge_metadata(
            existing_metadata,
            incoming_metadata,
        )
        document_id = self._ensure_document(merged_metadata)
        if document_id is None:
            document_id = attachment["document_id"]
        if (
            status in (None, "")
            or (status == "new" and not content_changed)
            or (
                existing_status == "resolved"
                and status != "resolved"
                and not content_changed
            )
        ):
            next_status = existing_status
        else:
            next_status = str(status or existing_status)
        if next_status == "resolved" and document_id is None:
            next_status = "unresolved"
        self._update_attachment(
            attachment_id,
            document_id=document_id,
            status=next_status,
            metadata=merged_metadata,
        )

        version_id = self._select_or_create_version(
            attachment_id,
            location,
            normalized_sha,
            size_bytes,
        )
        now = self._utc_timestamp()
        if location is None:
            cursor = self._connection.execute(
                """
                INSERT INTO file_locations (
                    attachment_id, root_id, relative_path, size_bytes,
                    mtime_ns, device, inode, current_version_id,
                    first_seen_at, last_seen_at, missing_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    attachment_id,
                    root_id,
                    normalized_path,
                    size_bytes,
                    mtime_ns,
                    device,
                    inode,
                    version_id,
                    now,
                    now,
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("Catalog location insert did not return an id")
            location_id = int(cursor.lastrowid)
        else:
            location_id = int(location["id"])
            self._connection.execute(
                """
                UPDATE file_locations
                SET size_bytes = ?, mtime_ns = ?, device = ?, inode = ?,
                    current_version_id = ?, last_seen_at = ?, missing_at = NULL
                WHERE id = ?
                """,
                (
                    size_bytes,
                    mtime_ns,
                    device,
                    inode,
                    version_id,
                    now,
                    location_id,
                ),
            )

        return CatalogObservation(
            attachment_id=attachment_id,
            location_id=location_id,
            version_id=version_id,
            matched_by=matched_by,
        )

    def get_file(self, root: Path, relative_path: str) -> dict[str, Any] | None:
        """Return one location record and its attachment metadata."""
        normalized_root = self._normalize_root(root)
        normalized_path = self._normalize_relative_path(relative_path)
        row = self._connection.execute(
            """
            SELECT
                l.id AS location_id, l.attachment_id, r.canonical_path AS root,
                l.relative_path, l.size_bytes, l.mtime_ns, l.device, l.inode,
                l.current_version_id, l.first_seen_at, l.missing_at,
                a.document_id, a.status, a.metadata_json,
                v.sha256
            FROM file_locations AS l
            JOIN library_roots AS r ON r.id = l.root_id
            JOIN attachments AS a ON a.id = l.attachment_id
            LEFT JOIN content_versions AS v ON v.id = l.current_version_id
            WHERE r.canonical_path = ? AND l.relative_path = ?
            ORDER BY l.missing_at IS NULL DESC, l.id DESC
            LIMIT 1
            """,
            (normalized_root, normalized_path),
        ).fetchone()
        return self._record_from_row(row) if row is not None else None

    def document_for_attachment(self, attachment_id: int) -> dict[str, Any] | None:
        """Return the canonical document linked to an attachment."""
        row = self._connection.execute(
            """
            SELECT d.id, d.bibcode, d.metadata_json
            FROM attachments AS a
            JOIN documents AS d ON d.id = a.document_id
            WHERE a.id = ?
            """,
            (attachment_id,),
        ).fetchone()
        if row is None:
            return None
        metadata = json.loads(row["metadata_json"] or "{}")
        if not isinstance(metadata, dict):
            metadata = {}
        return {
            "id": int(row["id"]),
            "bibcode": row["bibcode"],
            "metadata": metadata,
        }

    def query_files(
        self,
        *,
        root: Path | None = None,
        status: str | None = None,
        resolved: bool | None = None,
        search: str | None = None,
        include_missing: bool = False,
    ) -> list[dict[str, Any]]:
        """Return location records matching local catalog filters.

        Parameters
        ----------
        include_missing
            Include historical locations whose files are no longer observed.
            Normal queries return only current locations.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if not include_missing:
            clauses.append("l.missing_at IS NULL")
        if root is not None:
            clauses.append("r.canonical_path = ?")
            params.append(self._normalize_root(root))
        if status is not None:
            clauses.append("a.status = ?")
            params.append(status)
        if resolved is True:
            clauses.append("a.status = 'resolved' AND a.document_id IS NOT NULL")
        elif resolved is False:
            clauses.append("(a.status != 'resolved' OR a.document_id IS NULL)")
        if (search or "").strip():
            pattern = f"%{search.strip()}%"
            clauses.append(
                "(l.relative_path LIKE ? OR a.metadata_json LIKE ? OR v.sha256 LIKE ?)"
            )
            params.extend([pattern, pattern, pattern])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._connection.execute(
            f"""
            SELECT
                l.id AS location_id, l.attachment_id, r.canonical_path AS root,
                l.relative_path, l.size_bytes, l.mtime_ns, l.device, l.inode,
                l.current_version_id, l.first_seen_at, l.missing_at,
                a.document_id, a.status, a.metadata_json,
                v.sha256
            FROM file_locations AS l
            JOIN library_roots AS r ON r.id = l.root_id
            JOIN attachments AS a ON a.id = l.attachment_id
            LEFT JOIN content_versions AS v ON v.id = l.current_version_id
            {where}
            ORDER BY l.relative_path, l.id
            """,
            params,
        ).fetchall()
        return [self._record_from_row(row) for row in rows]

    def content_versions(self, attachment_id: int) -> list[dict[str, Any]]:
        """Return content versions in observation order."""
        rows = self._connection.execute(
            """
            SELECT id, attachment_id, sha256, size_bytes, observed_at
            FROM content_versions
            WHERE attachment_id = ?
            ORDER BY id
            """,
            (attachment_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def ensure_root(self, root: Path) -> int:
        """Register a library root and return its catalog identifier."""
        with self._connection:
            return self._ensure_root(self._normalize_root(root))

    def discover_root(self, root: Path, *, recursive: bool = True) -> int:
        """Register every PDF in a root using filesystem facts only.

        The discovery pass deliberately leaves SHA-256 unknown. A later sync or
        identify pass can hash only new or changed locations while unresolved
        files already remain queryable in the catalog.
        """
        normalized_root = Path(self._normalize_root(root))
        paths = (
            normalized_root.rglob("*.pdf")
            if recursive
            else normalized_root.glob("*.pdf")
        )
        seen_paths: set[str] = set()
        discovered = 0
        with self._connection:
            self._ensure_root(str(normalized_root))
            for path in paths:
                try:
                    stat = path.stat()
                    relative_path = path.relative_to(normalized_root).as_posix()
                except OSError:
                    continue
                seen_paths.add(relative_path)
                self._observe_file(
                    normalized_root,
                    relative_path,
                    sha256=None,
                    size_bytes=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                    device=stat.st_dev,
                    inode=stat.st_ino,
                    metadata=None,
                    status=None,
                )
                discovered += 1
        self.mark_missing_locations(normalized_root, seen_paths)
        return discovered

    def update_file_status(self, root: Path, relative_path: str, status: str) -> bool:
        """Update the attachment status for one observed location."""
        record = self.get_file(root, relative_path)
        if record is None:
            return False
        if status == "resolved" and record["document_id"] is None:
            status = "unresolved"
        with self._connection:
            cursor = self._connection.execute(
                """
                UPDATE attachments
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, self._utc_timestamp(), record["attachment_id"]),
            )
        return cursor.rowcount > 0

    def replace_file_metadata(
        self,
        root: Path,
        relative_path: str,
        metadata: Mapping[str, Any],
    ) -> bool:
        """Replace local metadata for one attachment without changing identity."""
        record = self.get_file(root, relative_path)
        if record is None:
            return False
        clean_metadata = {
            str(key): copy.deepcopy(value)
            for key, value in metadata.items()
            if key not in {"status", "current_filename", "current_path"}
        }
        with self._connection:
            if "bibcode" in clean_metadata:
                document_id = self._ensure_document(clean_metadata)
            else:
                document_id = record["document_id"]
            if "status" in metadata and metadata["status"] not in (None, ""):
                status = str(metadata["status"])
            elif "bibcode" in clean_metadata:
                status = "resolved" if document_id is not None else "unresolved"
            else:
                status = str(record["status"])
            if status == "resolved" and document_id is None:
                status = "unresolved"
            self._update_attachment(
                int(record["attachment_id"]),
                document_id=document_id,
                status=status,
                metadata=clean_metadata,
            )
        return True

    def move_file(
        self,
        root: Path,
        relative_path: str,
        new_relative_path: str,
    ) -> bool:
        """Move a location while retaining its attachment and version history."""
        record = self.get_file(root, relative_path)
        if record is None:
            return False
        root_id = self._ensure_root(self._normalize_root(root))
        normalized_old_path = self._normalize_relative_path(relative_path)
        normalized_new_path = self._normalize_relative_path(new_relative_path)
        if normalized_old_path == normalized_new_path:
            return True
        if self._active_location(root_id, normalized_new_path) is not None:
            raise ValueError(f"Catalog location already exists: {normalized_new_path}")
        with self._connection:
            self._connection.execute(
                """
                UPDATE file_locations
                SET relative_path = ?, last_seen_at = ?, missing_at = NULL
                WHERE id = ?
                """,
                (
                    normalized_new_path,
                    self._utc_timestamp(),
                    record["location_id"],
                ),
            )
        return True

    def mark_missing_locations(
        self, root: Path, seen_paths: set[str], recursive: bool = True
    ) -> int:
        """Mark active root locations absent from a completed scan as missing."""
        normalized_root = self._normalize_root(root)
        normalized_seen = {self._normalize_relative_path(path) for path in seen_paths}
        with self._connection:
            root_row = self._connection.execute(
                "SELECT id FROM library_roots WHERE canonical_path = ?",
                (normalized_root,),
            ).fetchone()
            if root_row is None:
                return 0
            rows = self._connection.execute(
                """
                SELECT id, relative_path FROM file_locations
                WHERE root_id = ? AND missing_at IS NULL
                """,
                (root_row["id"],),
            ).fetchall()
            missing_ids = [
                int(row["id"])
                for row in rows
                if row["relative_path"] not in normalized_seen
                and (recursive or "/" not in str(row["relative_path"]))
            ]
            if missing_ids:
                placeholders = ",".join("?" for _ in missing_ids)
                self._connection.execute(
                    f"""
                    UPDATE file_locations SET missing_at = ?
                    WHERE id IN ({placeholders})
                    """,
                    [self._utc_timestamp(), *missing_ids],
                )
        return len(missing_ids)

    def delete_locations(self, root: Path, location_ids: list[int]) -> int:
        """Delete selected locations and orphaned attachments explicitly."""
        if not location_ids:
            return 0
        normalized_root = self._normalize_root(root)
        placeholders = ",".join("?" for _ in location_ids)
        with self._connection:
            root_row = self._connection.execute(
                "SELECT id FROM library_roots WHERE canonical_path = ?",
                (normalized_root,),
            ).fetchone()
            if root_row is None:
                return 0
            params: list[Any] = [
                int(root_row["id"]),
                *[int(value) for value in location_ids],
            ]
            rows = self._connection.execute(
                f"""
                SELECT attachment_id FROM file_locations
                WHERE root_id = ? AND id IN ({placeholders})
                """,
                params,
            ).fetchall()
            attachment_ids = {int(row["attachment_id"]) for row in rows}
            result = self._connection.execute(
                f"""
                DELETE FROM file_locations
                WHERE root_id = ? AND id IN ({placeholders})
                """,
                params,
            )
            for attachment_id in attachment_ids:
                remaining = self._connection.execute(
                    "SELECT 1 FROM file_locations WHERE attachment_id = ? LIMIT 1",
                    (int(attachment_id),),
                ).fetchone()
                if remaining is None:
                    self._connection.execute(
                        "DELETE FROM attachments WHERE id = ?",
                        (int(attachment_id),),
                    )
        return int(result.rowcount)

    def get_meta(self, key: str) -> str | None:
        """Return catalog metadata by key."""
        row = self._connection.execute(
            "SELECT value FROM catalog_meta WHERE key = ?", (key,)
        ).fetchone()
        return str(row["value"]) if row is not None else None

    def set_meta(self, key: str, value: str) -> None:
        """Set catalog metadata by key."""
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO catalog_meta(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def delete_meta(self, key: str) -> None:
        """Delete catalog metadata by key."""
        with self._connection:
            self._connection.execute("DELETE FROM catalog_meta WHERE key = ?", (key,))

    def count_ads_documents(self) -> int:
        """Return the number of cached provider documents."""
        row = self._connection.execute(
            "SELECT COUNT(*) AS count FROM ads_documents"
        ).fetchone()
        return int(row["count"]) if row is not None else 0

    def upsert_ads_document(self, document: Mapping[str, Any], *, source: str) -> None:
        """Insert or update one cached ADS document."""
        with self._connection:
            self._upsert_ads_document(document, source=source)

    def _upsert_ads_document(self, document: Mapping[str, Any], *, source: str) -> None:
        """Write one ADS snapshot inside the caller's transaction."""
        bibcode = str(document.get("bibcode", "")).strip()
        if not bibcode:
            raise ValueError("ADS document cache entries require a bibcode.")
        self._ensure_document(document)
        doi_values = self._as_list(document.get("doi"))
        now = self._utc_timestamp()
        self._connection.execute(
            """
            INSERT INTO ads_documents (
                bibcode, doi_json, title_json, author_json, year, bibstem_json,
                volume, page_json, pub, raw_json, source, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(bibcode) DO UPDATE SET
                doi_json = excluded.doi_json,
                title_json = excluded.title_json,
                author_json = excluded.author_json,
                year = excluded.year,
                bibstem_json = excluded.bibstem_json,
                volume = excluded.volume,
                page_json = excluded.page_json,
                pub = excluded.pub,
                raw_json = excluded.raw_json,
                source = excluded.source,
                fetched_at = excluded.fetched_at
            """,
            (
                bibcode,
                self._encode_json(doi_values),
                self._encode_json(self._as_list(document.get("title"))),
                self._encode_json(self._as_list(document.get("author"))),
                self._as_scalar(document.get("year")),
                self._encode_json(self._as_list(document.get("bibstem"))),
                self._as_scalar(document.get("volume")),
                self._encode_json(self._as_list(document.get("page"))),
                self._as_scalar(document.get("pub")),
                self._encode_json(dict(document)),
                source,
                now,
            ),
        )
        self._connection.execute(
            "DELETE FROM ads_document_dois WHERE bibcode = ?", (bibcode,)
        )
        self._connection.executemany(
            "INSERT OR IGNORE INTO ads_document_dois(doi, bibcode) VALUES (?, ?)",
            [
                (self._normalize_doi(doi), bibcode)
                for doi in doi_values
                if self._normalize_doi(doi)
            ],
        )

    def upsert_ads_documents(
        self, documents: list[Mapping[str, Any]], *, source: str
    ) -> None:
        """Insert or update cached provider documents atomically."""
        with self._connection:
            for document in documents:
                self._upsert_ads_document(document, source=source)

    def find_ads_document_by_bibcode(self, bibcode: str) -> dict[str, Any] | None:
        """Return a cached provider document by Bibcode."""
        row = self._connection.execute(
            "SELECT * FROM ads_documents WHERE bibcode = ?", (bibcode,)
        ).fetchone()
        return self._document_from_row(row) if row is not None else None

    def set_ads_document_metrics(
        self,
        bibcode: str,
        metrics: Mapping[str, int | None],
    ) -> bool:
        """Store citation metrics inside a cached provider document."""
        row = self._connection.execute(
            "SELECT raw_json FROM ads_documents WHERE bibcode = ?", (bibcode,)
        ).fetchone()
        if row is None:
            return False
        raw = json.loads(row["raw_json"] or "{}")
        if not isinstance(raw, dict):
            raw = {}
        raw["_adsapi_metrics"] = dict(metrics)
        with self._connection:
            result = self._connection.execute(
                "UPDATE ads_documents SET raw_json = ? WHERE bibcode = ?",
                (self._encode_json(raw), bibcode),
            )
        return result.rowcount > 0

    def find_ads_documents_by_bibcodes(
        self, bibcodes: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Return cached provider documents keyed by lowercase Bibcode."""
        values = [str(value).strip() for value in bibcodes if str(value).strip()]
        if not values:
            return {}
        placeholders = ",".join("?" for _ in values)
        rows = self._connection.execute(
            f"SELECT * FROM ads_documents WHERE bibcode IN ({placeholders})", values
        ).fetchall()
        return {
            str(row["bibcode"]).lower(): self._document_from_row(row) for row in rows
        }

    def find_ads_documents_by_doi(self, doi: str) -> list[dict[str, Any]]:
        """Return cached provider documents matching one DOI."""
        normalized = self._normalize_doi(doi)
        if not normalized:
            return []
        rows = self._connection.execute(
            """
            SELECT ads_documents.* FROM ads_documents
            JOIN ads_document_dois USING (bibcode)
            WHERE ads_document_dois.doi = ? ORDER BY ads_documents.bibcode
            """,
            (normalized,),
        ).fetchall()
        return [self._document_from_row(row) for row in rows]

    def find_ads_documents_by_dois(
        self, dois: list[str]
    ) -> dict[str, list[dict[str, Any]]]:
        """Return cached provider documents keyed by normalized DOI."""
        normalized = list(dict.fromkeys(self._normalize_doi(doi) for doi in dois))
        return {doi: self.find_ads_documents_by_doi(doi) for doi in normalized if doi}

    def close(self) -> None:
        """Close the catalog connection."""
        self._connection.close()

    def migrate_legacy_root(self, root: Path) -> LegacyMigrationReport:
        """Import legacy root state without changing its source files."""
        normalized_root = self._normalize_root(root)
        sources: list[tuple[str, list[dict[str, Any]]]] = []
        legacy_ads_source: tuple[str, list[dict[str, Any]]] | None = None
        legacy_path = Path(normalized_root) / LEGACY_REGISTRY_FILENAME
        sidecar_path = Path(normalized_root) / SIDECAR_FILENAME
        if legacy_path.exists():
            sources.append(
                (f"sqlite:{legacy_path}", self._read_legacy_sqlite(legacy_path))
            )
            legacy_ads_source = (
                f"sqlite:{legacy_path}:ads_documents",
                self._read_legacy_ads_documents(legacy_path),
            )
        if sidecar_path.exists():
            sources.append(
                (f"sidecar:{sidecar_path}", self._read_legacy_sidecar(sidecar_path))
            )

        imported = 0
        with self._connection:
            self._ensure_root(normalized_root)
            for source_key, records in sources:
                if self._migration_complete(source_key):
                    continue
                for record in records:
                    current_path = self._normalize_relative_path(
                        str(
                            record.get("current_path")
                            or record.get("current_filename")
                            or ""
                        )
                    )
                    file_path = Path(normalized_root) / current_path
                    try:
                        stat = file_path.stat()
                        size_bytes = int(record.get("size_bytes", stat.st_size))
                        mtime_ns = int(record.get("mtime_ns", stat.st_mtime_ns))
                        device = int(record.get("device", stat.st_dev))
                        inode = int(record.get("inode", stat.st_ino))
                    except OSError:
                        size_bytes = int(record.get("size_bytes", 0) or 0)
                        mtime_ns = int(record.get("mtime_ns", 0) or 0)
                        device = record.get("device")
                        inode = record.get("inode")
                    metadata = dict(record.get("metadata") or record)
                    metadata.pop("current_filename", None)
                    metadata.pop("current_path", None)
                    metadata.pop("size_bytes", None)
                    metadata.pop("mtime_ns", None)
                    metadata.pop("device", None)
                    metadata.pop("inode", None)
                    self._observe_file(
                        Path(normalized_root),
                        current_path,
                        sha256=str(record.get("sha256") or "") or None,
                        size_bytes=size_bytes,
                        mtime_ns=mtime_ns,
                        device=device,
                        inode=inode,
                        metadata=metadata,
                        status=str(record.get("status") or "") or None,
                    )
                    imported += 1
                self._connection.execute(
                    """
                    INSERT INTO catalog_migrations(source_key, imported_at, imported_count)
                    VALUES (?, ?, ?)
                    """,
                    (source_key, self._utc_timestamp(), len(records)),
                )
            if legacy_ads_source is not None:
                source_key, documents = legacy_ads_source
                if not self._migration_complete(source_key):
                    for document in documents:
                        self._upsert_ads_document(
                            document,
                            source=f"migration:{legacy_path}",
                        )
                    self._connection.execute(
                        """
                        INSERT INTO catalog_migrations(
                            source_key, imported_at, imported_count
                        ) VALUES (?, ?, ?)
                        """,
                        (source_key, self._utc_timestamp(), len(documents)),
                    )
            if sidecar_path.exists():
                sidecar = Sidecar(
                    sidecar_path.parent,
                    filename=sidecar_path.name,
                )
                profile = sidecar.identify_profile
                if profile is not None:
                    self._connection.execute(
                        """
                        INSERT INTO catalog_meta(key, value) VALUES (?, ?)
                        ON CONFLICT(key) DO UPDATE SET value = excluded.value
                        """,
                        (
                            f"root:{normalized_root}:identify_profile",
                            self._encode_json(profile),
                        ),
                    )
                settings = sidecar.identify_settings
                if settings is not None:
                    self._connection.execute(
                        """
                        INSERT INTO catalog_meta(key, value) VALUES (?, ?)
                        ON CONFLICT(key) DO UPDATE SET value = excluded.value
                        """,
                        (
                            f"root:{normalized_root}:identify_settings",
                            self._encode_json(settings.to_mapping()),
                        ),
                    )
        return LegacyMigrationReport(imported=imported)

    def _migration_complete(self, source_key: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM catalog_migrations WHERE source_key = ?", (source_key,)
        ).fetchone()
        return row is not None

    @staticmethod
    def _read_legacy_sqlite(path: Path) -> list[dict[str, Any]]:
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        try:
            tables = {
                str(row["name"])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            if "files" not in tables:
                return []
            rows = connection.execute("SELECT * FROM files ORDER BY id").fetchall()
            records: list[dict[str, Any]] = []
            for row in rows:
                record = dict(row)
                metadata = json.loads(record.pop("metadata_json", "{}") or "{}")
                if not isinstance(metadata, dict):
                    metadata = {}
                for key in (
                    "bibcode",
                    "doi",
                    "title",
                    "author",
                    "year",
                    "bibstem",
                    "volume",
                    "page",
                    "pub",
                    "source",
                    "identified_at",
                ):
                    if key not in metadata and record.get(key) not in (None, "", []):
                        metadata[key] = record[key]
                record["metadata"] = metadata
                records.append(record)
            return records
        finally:
            connection.close()

    @staticmethod
    def _read_legacy_ads_documents(path: Path) -> list[dict[str, Any]]:
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        try:
            tables = {
                str(row["name"])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            if "ads_documents" not in tables:
                return []
            rows = connection.execute(
                "SELECT * FROM ads_documents ORDER BY bibcode"
            ).fetchall()
            documents: list[dict[str, Any]] = []
            for row in rows:
                raw = json.loads(row["raw_json"] or "{}")
                if not isinstance(raw, dict):
                    raw = {}
                documents.append(
                    {
                        "bibcode": row["bibcode"],
                        "doi": PdfCatalog._decode_json_list(row["doi_json"]),
                        "title": PdfCatalog._decode_json_list(row["title_json"]),
                        "author": PdfCatalog._decode_json_list(row["author_json"]),
                        "year": row["year"],
                        "bibstem": PdfCatalog._decode_json_list(row["bibstem_json"]),
                        "volume": row["volume"],
                        "page": PdfCatalog._decode_json_list(row["page_json"]),
                        "pub": row["pub"],
                        **raw,
                    }
                )
            return documents
        finally:
            connection.close()

    @staticmethod
    def _read_legacy_sidecar(path: Path) -> list[dict[str, Any]]:
        sidecar = Sidecar(path.parent, filename=path.name)
        records: list[dict[str, Any]] = []
        for original, metadata in sidecar.files.items():
            record = dict(metadata)
            record.setdefault("current_filename", original)
            records.append(record)
        return records

    def _initialize_schema(self) -> None:
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS catalog_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            version_row = self._connection.execute(
                "SELECT value FROM catalog_meta WHERE key = 'schema_version'"
            ).fetchone()
            stored_version = int(version_row["value"]) if version_row else 0
            if stored_version > CATALOG_SCHEMA_VERSION:
                raise RuntimeError(
                    "Catalog schema is newer than this package: "
                    f"{stored_version} > {CATALOG_SCHEMA_VERSION}"
                )
            if self._table_exists("attachments"):
                self._ensure_column(
                    "attachments",
                    "document_id",
                    "INTEGER REFERENCES documents(id)",
                )
            if self._table_exists("file_locations"):
                self._ensure_column(
                    "file_locations",
                    "missing_at",
                    "TEXT",
                )
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS catalog_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS library_roots (
                    id INTEGER PRIMARY KEY,
                    canonical_path TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY,
                    bibcode TEXT UNIQUE,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ads_documents (
                    bibcode TEXT PRIMARY KEY,
                    doi_json TEXT NOT NULL DEFAULT '[]',
                    title_json TEXT NOT NULL DEFAULT '[]',
                    author_json TEXT NOT NULL DEFAULT '[]',
                    year TEXT,
                    bibstem_json TEXT NOT NULL DEFAULT '[]',
                    volume TEXT,
                    page_json TEXT NOT NULL DEFAULT '[]',
                    pub TEXT,
                    raw_json TEXT NOT NULL,
                    source TEXT NOT NULL,
                    fetched_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ads_document_dois (
                    doi TEXT NOT NULL,
                    bibcode TEXT NOT NULL REFERENCES ads_documents(bibcode)
                        ON DELETE CASCADE,
                    UNIQUE(doi, bibcode)
                );
                CREATE INDEX IF NOT EXISTS idx_ads_document_dois_doi
                    ON ads_document_dois(doi);
                CREATE TABLE IF NOT EXISTS attachments (
                    id INTEGER PRIMARY KEY,
                    document_id INTEGER REFERENCES documents(id),
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS content_versions (
                    id INTEGER PRIMARY KEY,
                    attachment_id INTEGER NOT NULL REFERENCES attachments(id)
                        ON DELETE CASCADE,
                    sha256 TEXT,
                    size_bytes INTEGER NOT NULL,
                    observed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS file_locations (
                    id INTEGER PRIMARY KEY,
                    attachment_id INTEGER NOT NULL REFERENCES attachments(id)
                        ON DELETE CASCADE,
                    root_id INTEGER NOT NULL REFERENCES library_roots(id)
                        ON DELETE CASCADE,
                    relative_path TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    device INTEGER,
                    inode INTEGER,
                    current_version_id INTEGER REFERENCES content_versions(id),
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    missing_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS
                    idx_active_location_path
                    ON file_locations(root_id, relative_path)
                    WHERE missing_at IS NULL;
                CREATE INDEX IF NOT EXISTS idx_location_identity
                    ON file_locations(device, inode);
                CREATE INDEX IF NOT EXISTS idx_version_sha256
                    ON content_versions(sha256);
                CREATE TABLE IF NOT EXISTS catalog_migrations (
                    source_key TEXT PRIMARY KEY,
                    imported_at TEXT NOT NULL,
                    imported_count INTEGER NOT NULL
                );
                """
            )
            self._ensure_column(
                "attachments",
                "document_id",
                "INTEGER REFERENCES documents(id)",
            )
            self._ensure_column(
                "file_locations",
                "missing_at",
                "TEXT",
            )
            self._connection.execute(
                """
                INSERT INTO catalog_meta (key, value)
                VALUES ('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(CATALOG_SCHEMA_VERSION),),
            )

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        """Add one schema column when upgrading an older catalog."""
        columns = {
            str(row["name"])
            for row in self._connection.execute(f"PRAGMA table_info({table})")
        }
        if column not in columns:
            self._connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
            )

    def _table_exists(self, table: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        return row is not None

    def _ensure_root(self, root: str) -> int:
        now = self._utc_timestamp()
        self._connection.execute(
            """
            INSERT INTO library_roots (canonical_path, created_at, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(canonical_path) DO UPDATE SET updated_at = excluded.updated_at
            """,
            (root, now, now),
        )
        row = self._connection.execute(
            "SELECT id FROM library_roots WHERE canonical_path = ?", (root,)
        ).fetchone()
        if row is None:
            raise RuntimeError(f"Catalog root was not created: {root}")
        return int(row["id"])

    def _active_location(self, root_id: int, relative_path: str) -> sqlite3.Row | None:
        return self._connection.execute(
            """
            SELECT * FROM file_locations
            WHERE root_id = ? AND relative_path = ? AND missing_at IS NULL
            LIMIT 1
            """,
            (root_id, relative_path),
        ).fetchone()

    def _location_for_attachment_path(
        self,
        root_id: int,
        relative_path: str,
        attachment_id: int,
    ) -> sqlite3.Row | None:
        return self._connection.execute(
            """
            SELECT * FROM file_locations
            WHERE root_id = ? AND relative_path = ? AND attachment_id = ?
            ORDER BY missing_at IS NULL DESC, id DESC
            LIMIT 1
            """,
            (root_id, relative_path, attachment_id),
        ).fetchone()

    def _match_attachment(
        self,
        sha256: str | None,
        device: int | None,
        inode: int | None,
    ) -> tuple[int | None, str]:
        candidates: list[int] = []
        if device is not None and inode is not None:
            rows = self._connection.execute(
                """
                SELECT DISTINCT attachment_id FROM file_locations
                WHERE device = ? AND inode = ?
                """,
                (device, inode),
            ).fetchall()
            candidates = [int(row["attachment_id"]) for row in rows]
            if len(candidates) == 1:
                return candidates[0], "filesystem identity"
            if len(candidates) > 1:
                return None, "ambiguous filesystem identity"

        if sha256:
            rows = self._connection.execute(
                """
                SELECT DISTINCT attachment_id FROM content_versions
                WHERE sha256 = ?
                """,
                (sha256,),
            ).fetchall()
            candidates = [int(row["attachment_id"]) for row in rows]
            if len(candidates) == 1:
                return candidates[0], "content digest"
            if len(candidates) > 1:
                return None, "ambiguous content digest"
        return None, "new"

    def _create_attachment(
        self,
        *,
        metadata: Mapping[str, Any] | None,
        status: str | None,
    ) -> int:
        now = self._utc_timestamp()
        normalized_metadata = self._merge_metadata({}, metadata or {})
        next_status = status or (
            "resolved" if normalized_metadata.get("bibcode") else "unresolved"
        )
        cursor = self._connection.execute(
            """
            INSERT INTO attachments (
                status, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?)
            """,
            (next_status, json.dumps(normalized_metadata, sort_keys=True), now, now),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("Catalog attachment insert did not return an id")
        return int(cursor.lastrowid)

    def _attachment(self, attachment_id: int) -> sqlite3.Row | None:
        return self._connection.execute(
            "SELECT * FROM attachments WHERE id = ?", (attachment_id,)
        ).fetchone()

    def _update_attachment(
        self,
        attachment_id: int,
        *,
        document_id: int | None,
        status: str,
        metadata: Mapping[str, Any],
    ) -> None:
        self._connection.execute(
            """
            UPDATE attachments
            SET document_id = ?, status = ?, metadata_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                document_id,
                status,
                json.dumps(metadata, sort_keys=True),
                self._utc_timestamp(),
                attachment_id,
            ),
        )

    def _ensure_document(self, metadata: Mapping[str, Any]) -> int | None:
        bibcode = self._as_scalar(metadata.get("bibcode"))
        if not bibcode:
            return None
        row = self._connection.execute(
            "SELECT id, metadata_json FROM documents WHERE bibcode = ?",
            (bibcode,),
        ).fetchone()
        now = self._utc_timestamp()
        if row is None:
            cursor = self._connection.execute(
                """
                INSERT INTO documents (
                    bibcode, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?)
                """,
                (bibcode, self._encode_json(dict(metadata)), now, now),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("Catalog document insert did not return an id")
            return int(cursor.lastrowid)

        existing = json.loads(row["metadata_json"] or "{}")
        if not isinstance(existing, dict):
            existing = {}
        merged = self._merge_metadata(existing, metadata)
        self._connection.execute(
            """
            UPDATE documents
            SET metadata_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (self._encode_json(merged), now, int(row["id"])),
        )
        return int(row["id"])

    def _select_or_create_version(
        self,
        attachment_id: int,
        location: sqlite3.Row | None,
        sha256: str | None,
        size_bytes: int,
    ) -> int:
        current_version_id = (
            int(location["current_version_id"])
            if location is not None and location["current_version_id"] is not None
            else None
        )
        if current_version_id is not None:
            if sha256 is None:
                return current_version_id
            row = self._connection.execute(
                "SELECT * FROM content_versions WHERE id = ?", (current_version_id,)
            ).fetchone()
            if row is not None and row["sha256"] == sha256:
                return current_version_id
        if sha256 is not None:
            row = self._connection.execute(
                """
                SELECT id FROM content_versions
                WHERE attachment_id = ? AND sha256 = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (attachment_id, sha256),
            ).fetchone()
            if row is not None:
                return int(row["id"])
        cursor = self._connection.execute(
            """
            INSERT INTO content_versions (
                attachment_id, sha256, size_bytes, observed_at
            ) VALUES (?, ?, ?, ?)
            """,
            (attachment_id, sha256, size_bytes, self._utc_timestamp()),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("Catalog content-version insert did not return an id")
        return int(cursor.lastrowid)

    @staticmethod
    def _merge_metadata(
        existing: Mapping[str, Any], incoming: Mapping[str, Any]
    ) -> dict[str, Any]:
        merged = copy.deepcopy(dict(existing))
        locked = {
            str(field)
            for field in merged.get(LOCKED_METADATA_FIELDS_KEY, [])
            if str(field).strip()
        }
        for key, value in incoming.items():
            if key in locked or key in {"current_filename", "current_path"}:
                continue
            if value in (None, "", []):
                continue
            merged[str(key)] = copy.deepcopy(value)
        return merged

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> dict[str, Any]:
        metadata = json.loads(row["metadata_json"] or "{}")
        if not isinstance(metadata, dict):
            metadata = {}
        record = {
            "id": int(row["attachment_id"]),
            "location_id": int(row["location_id"]),
            "attachment_id": int(row["attachment_id"]),
            "document_id": row["document_id"],
            "root": row["root"],
            "relative_path": row["relative_path"],
            "current_path": row["relative_path"],
            "size_bytes": int(row["size_bytes"]),
            "mtime_ns": int(row["mtime_ns"]),
            "first_seen_at": row["first_seen_at"],
            "device": row["device"],
            "inode": row["inode"],
            "version_id": row["current_version_id"],
            "missing_at": row["missing_at"],
            "sha256": row["sha256"] or "",
            "status": row["status"],
            "metadata": metadata,
        }
        for key in (
            "bibcode",
            "doi",
            "title",
            "author",
            "year",
            "bibstem",
            "volume",
            "page",
            "pub",
            "source",
            "identified_at",
        ):
            record[key] = metadata.get(key)
        for key, value in metadata.items():
            record.setdefault(key, value)
        return record

    @staticmethod
    def _document_from_row(row: sqlite3.Row) -> dict[str, Any]:
        raw = json.loads(row["raw_json"] or "{}")
        if not isinstance(raw, dict):
            raw = {}
        return {
            "bibcode": row["bibcode"],
            "doi": PdfCatalog._decode_json_list(row["doi_json"]),
            "title": PdfCatalog._decode_json_list(row["title_json"]),
            "author": PdfCatalog._decode_json_list(row["author_json"]),
            "year": row["year"],
            "bibstem": PdfCatalog._decode_json_list(row["bibstem_json"]),
            "volume": row["volume"],
            "page": PdfCatalog._decode_json_list(row["page_json"]),
            "pub": row["pub"],
            "abstract": raw.get("abstract", ""),
            "author_count": raw.get("author_count"),
            "citation_count": raw.get("citation_count"),
            "metrics": raw.get("_adsapi_metrics", {}),
            "raw": raw,
            "source": row["source"],
            "fetched_at": row["fetched_at"],
        }

    @staticmethod
    def _decode_json_list(value: str) -> list[str]:
        loaded = json.loads(value or "[]")
        return [str(item) for item in loaded] if isinstance(loaded, list) else []

    @staticmethod
    def _encode_json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _as_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value if item is not None]
        return [str(value)]

    @staticmethod
    def _as_scalar(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            return next((str(item) for item in value if item is not None), None)
        return str(value)

    @staticmethod
    def _normalize_doi(doi: str) -> str:
        return str(doi).strip().lower()

    @staticmethod
    def _normalize_root(root: Path) -> str:
        return str(Path(root).expanduser().resolve())

    @staticmethod
    def _normalize_relative_path(relative_path: str) -> str:
        candidate = Path(relative_path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError(
                f"relative_path must stay within the library root: {relative_path!r}"
            )
        if not candidate.parts:
            raise ValueError("relative_path must not be empty")
        return PurePosixPath(*candidate.parts).as_posix()

    @staticmethod
    def _utc_timestamp() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


__all__ = [
    "CATALOG_FILENAME",
    "CATALOG_SCHEMA_VERSION",
    "CatalogObservation",
    "LegacyMigrationReport",
    "PdfCatalog",
]
