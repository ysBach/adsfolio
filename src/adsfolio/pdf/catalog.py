"""Central local catalog for PDF attachments and observed file locations."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sqlite3
import unicodedata
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from ..config import Config
from .arxiv import normalize_arxiv_id
from .files import sha256_file
from .identity import IDENTITY_KEY, assert_mutation_allowed, target_identity
from .readiness import (
    EXTERNAL_METADATA_KEY,
    METADATA_SHA256_KEY,
    METADATA_STALE_KEY,
    WATCH_PENDING_KEY,
)
from .sidecar import SIDECAR_FILENAME, Sidecar


def _sqlite_file_identity(value: int | None) -> int | str | None:
    """Store file identifiers beyond SQLite's signed 64-bit range losslessly."""
    if value is None or -(1 << 63) <= value < (1 << 63):
        return value
    # A prefix prevents INTEGER affinity from rounding large decimals to REAL.
    return hex(value)


CATALOG_FILENAME = "pdf-library.sqlite"
CATALOG_SCHEMA_VERSION = 3
LEGACY_REGISTRY_FILENAME = ".adsfolio-pdfs.sqlite"
LOCKED_METADATA_FIELDS_KEY = "_adsfolio_locked_fields"
REFERENCE_METADATA_KEY = "_adsfolio_reference"
# A citation key that the user typed for a paper that is not a local reference.
CITE_KEY_METADATA_KEY = "_adsfolio_cite_key"
REFERENCE_FIELD_MAP = {
    "title": "title",
    "author": "author",
    "year": "year",
    "journal": "pub",
    "volume": "volume",
    "number": "issue",
    "pages": "page",
    "doi": "doi",
    "abstract": "abstract",
}


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
        then a unique content digest. Operating systems reuse inode numbers,
        any stored location with that identity and a known digest is evidence:
        only attachments whose known digest equals the incoming digest match,
        and unknown-digest locations are ignored. If none match, the file can
        still rejoin through the digest lookup. Only when no stored location
        with that identity has a digest does identity alone decide (residual
        risk: a reused inode then inherits that attachment).
        Ambiguous matches create a new attachment instead of silently merging
        records.
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
            content_changed = normalized_sha is not None and (
                version is None or version["sha256"] != normalized_sha
            )

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
        if incoming_metadata.get(EXTERNAL_METADATA_KEY):
            incoming_metadata = {
                key: value
                for key, value in incoming_metadata.items()
                if key.startswith("_adsfolio_")
                or key in {"sha256", "identified_at"}
                or existing_metadata.get(key) in (None, "", [])
            }
        merged_metadata = self._merge_metadata(
            existing_metadata,
            incoming_metadata,
        )
        document_id = self._ensure_document(
            merged_metadata, existing_id=attachment["document_id"]
        )
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
        if next_status == "resolved" and (
            document_id is None or not merged_metadata.get("bibcode")
        ):
            # "resolved" means an ADS link; other documents stay unresolved.
            next_status = "unresolved"
        stale = content_changed or bool(existing_metadata.get(METADATA_STALE_KEY))
        fresh_ads = (
            status == "resolved"
            and bool(incoming_metadata.get("bibcode"))
            and bool(normalized_sha)
            and incoming_metadata.get(METADATA_SHA256_KEY) == normalized_sha
        )
        if stale and not fresh_ads:
            merged_metadata[METADATA_STALE_KEY] = True
            document_id = None
            next_status = "unresolved"
        elif fresh_ads:
            merged_metadata.pop(METADATA_STALE_KEY, None)
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
                    _sqlite_file_identity(device),
                    _sqlite_file_identity(inode),
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
                SET relative_path = ?, size_bytes = ?, mtime_ns = ?, device = ?, inode = ?,
                    current_version_id = ?, last_seen_at = ?, missing_at = NULL
                WHERE id = ?
                """,
                (
                    normalized_path,
                    size_bytes,
                    mtime_ns,
                    _sqlite_file_identity(device),
                    _sqlite_file_identity(inode),
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

    def register_verified_replacement(
        self,
        root: Path,
        relative_path: str,
        metadata: Mapping[str, Any],
    ) -> CatalogObservation | None:
        """Register verified new contents without losing an absent PDF's history.

        A different name rejoins an old attachment only when exactly one
        matching attachment has no live locations. Existing editions remain
        separate. A different paper at the same path receives a new attachment.
        File bytes are never changed. Unknown filesystem state stops the write.
        """
        base = Path(self._normalize_root(root))
        relative = self._normalize_relative_path(relative_path)
        path = base / relative
        incoming = copy.deepcopy(dict(metadata))
        digest = str(incoming.get("sha256") or "")
        provenance = assert_mutation_allowed(path, incoming, expected_sha256=digest)
        if provenance["status"] != "verified":
            return None
        authority = target_identity(incoming)
        initial = path.stat()
        facts = (initial.st_size, initial.st_mtime_ns, initial.st_dev, initial.st_ino)
        if not base.is_dir():
            raise FileNotFoundError(base)
        conn = self._connection
        if conn.in_transaction:
            raise RuntimeError("Replacement registration needs its own transaction")
        conn.execute("BEGIN IMMEDIATE")
        try:
            root_id = self._ensure_root(str(base))
            current = self._active_location(root_id, relative)
            current_id = int(current["attachment_id"]) if current is not None else None
            attachment = (
                self._attachment(current_id) if current_id is not None else None
            )
            old_at_path = (
                json.loads(attachment["metadata_json"])
                if attachment is not None
                else {}
            )
            old_identity = target_identity(old_at_path)
            same_paper = (
                old_identity.has_stable_identity
                and not old_identity.claims_outside(authority)
            )
            conflicting_path = bool(old_identity.primary) and not same_paper
            if (
                conflicting_path
                and not old_identity.has_stable_identity
                and current is not None
            ):
                old_version = conn.execute(
                    "SELECT sha256 FROM content_versions WHERE id = ?",
                    (current["current_version_id"],),
                ).fetchone()
                if old_version is not None and old_version["sha256"] == digest:
                    # Identifying the same unchanged file preserves manual work;
                    # different bytes need a shared stable paper identifier.
                    same_paper = True
                    conflicting_path = False
            changed_live_alias = False
            if same_paper and current is not None:
                prior_version = conn.execute(
                    "SELECT sha256 FROM content_versions WHERE id = ?",
                    (current["current_version_id"],),
                ).fetchone()
                if prior_version is not None and prior_version["sha256"] != digest:
                    for alias in conn.execute(
                        "SELECT f.*, r.canonical_path FROM file_locations f "
                        "JOIN library_roots r ON r.id = f.root_id "
                        "WHERE f.attachment_id = ? AND f.id != ? AND f.missing_at IS NULL",
                        (current_id, current["id"]),
                    ).fetchall():
                        alias_root = Path(alias["canonical_path"])
                        if not alias_root.is_dir():
                            raise OSError(
                                f"Attachment root is unavailable: {alias_root}"
                            )
                        try:
                            (alias_root / alias["relative_path"]).lstat()
                        except FileNotFoundError:
                            continue
                        changed_live_alias = True
                        break

            # A previously identified download may already own this path. It
            # may join a missing attachment only if it has no reader-owned data.
            automatic = {
                "bibcode",
                "doi",
                "arxiv",
                "arxiv_id",
                "identifier",
                "alternate_bibcode",
                "sha256",
                "title",
                "author",
                "year",
                "bibstem",
                "volume",
                "page",
                "pub",
                "publisher",
                "identified_at",
                "status",
                "resolution_strategy",
                "abstract",
                "author_count",
                "citation_count",
                "current_filename",
                "current_path",
                IDENTITY_KEY,
                METADATA_SHA256_KEY,
                METADATA_STALE_KEY,
                EXTERNAL_METADATA_KEY,
                WATCH_PENDING_KEY,
            }
            disposable = current_id is None or (
                not (set(old_at_path) - automatic)
                and conn.execute(
                    "SELECT COUNT(*) FROM file_locations WHERE attachment_id = ?",
                    (current_id,),
                ).fetchone()[0]
                == 1
                and conn.execute(
                    "SELECT COUNT(*) FROM content_versions WHERE attachment_id = ?",
                    (current_id,),
                ).fetchone()[0]
                <= 1
            )
            candidates: list[sqlite3.Row] = []
            absent_locations: dict[int, list[int]] = {}
            if disposable and not conflicting_path:
                rows = conn.execute(
                    "SELECT DISTINCT a.* FROM attachments a JOIN file_locations f ON f.attachment_id = a.id "
                    "WHERE f.root_id = ? AND a.id IS NOT ?",
                    (root_id, current_id),
                ).fetchall()
                for candidate in rows:
                    previous = json.loads(candidate["metadata_json"] or "{}")
                    identity = target_identity(previous)
                    if not identity.has_stable_identity or identity.claims_outside(
                        authority
                    ):
                        continue
                    missing: list[int] = []
                    eligible = True
                    for location in conn.execute(
                        "SELECT f.*, r.canonical_path FROM file_locations f "
                        "JOIN library_roots r ON r.id = f.root_id WHERE f.attachment_id = ?",
                        (candidate["id"],),
                    ).fetchall():
                        location_root = Path(location["canonical_path"])
                        if not location_root.is_dir():
                            # Another matching attachment has unknown liveness;
                            # excluding it would manufacture a unique match.
                            conn.rollback()
                            return None
                        # Historical paths can now name a different attachment.
                        # They do not make the old content a live alias.
                        if (
                            location["root_id"] == root_id
                            and location["relative_path"] == relative
                        ):
                            missing.append(int(location["id"]))
                            continue
                        owner = self._active_location(
                            location["root_id"], location["relative_path"]
                        )
                        if (
                            location["missing_at"] is not None
                            and owner is not None
                            and owner["attachment_id"] != candidate["id"]
                        ):
                            continue
                        try:
                            (location_root / location["relative_path"]).lstat()
                        except FileNotFoundError:
                            missing.append(int(location["id"]))
                        else:
                            eligible = False
                            break
                    if eligible and missing:
                        candidates.append(candidate)
                        absent_locations[int(candidate["id"])] = missing
            chosen = candidates[0] if len(candidates) == 1 else None
            if chosen is not None:
                selected_id = int(chosen["id"])
                previous_metadata = json.loads(chosen["metadata_json"] or "{}")
            elif same_paper and not changed_live_alias:
                selected_id = int(current_id)
                previous_metadata = old_at_path
            elif conflicting_path or changed_live_alias:
                selected_id = self._create_attachment(metadata={}, status="unresolved")
                previous_metadata = {}
                conn.execute(
                    "UPDATE file_locations SET missing_at = ? WHERE id = ?",
                    (self._utc_timestamp(), current["id"]),
                )
                current = None
            else:
                conn.rollback()
                return None

            # File-specific proof and state must describe the new bytes. User
            # notes and locked bibliographic fields survive only for this paper.
            for key in (
                IDENTITY_KEY,
                METADATA_SHA256_KEY,
                METADATA_STALE_KEY,
                "sha256",
                "identified_at",
                "resolution_strategy",
                "recovery_evidence",
            ):
                previous_metadata.pop(key, None)
            merged = self._merge_metadata(previous_metadata, incoming)
            merged.pop(METADATA_STALE_KEY, None)
            merged[WATCH_PENDING_KEY] = False
            merged["sha256"] = digest
            merged[METADATA_SHA256_KEY] = digest
            merged[IDENTITY_KEY] = assert_mutation_allowed(
                path,
                merged,
                expected_sha256=digest,
                provider_document=incoming,
            )
            document_id = self._ensure_document(merged)
            status = (
                "resolved"
                if merged.get("bibcode") and document_id is not None
                else "unresolved"
            )
            self._update_attachment(
                selected_id, document_id=document_id, status=status, metadata=merged
            )
            if current is not None and current_id != selected_id:
                # Move the fresh arrival's single version with its location.
                # Duplicating it would make later hash matching ambiguous.
                conn.execute(
                    "UPDATE content_versions SET attachment_id = ? WHERE attachment_id = ?",
                    (selected_id, current_id),
                )
                self._update_attachment(
                    int(current_id),
                    document_id=None,
                    status="superseded",
                    metadata={**old_at_path, "_adsfolio_superseded_by": selected_id},
                )
            version_id = self._select_or_create_version(
                selected_id, None, digest, initial.st_size
            )
            now = self._utc_timestamp()
            for location_id in absent_locations.get(selected_id, []):
                conn.execute(
                    "UPDATE file_locations SET missing_at = ? WHERE id = ? AND missing_at IS NULL",
                    (now, location_id),
                )
            if current is not None:
                location_id = int(current["id"])
                conn.execute(
                    "UPDATE file_locations SET attachment_id = ?, current_version_id = ?, size_bytes = ?, "
                    "mtime_ns = ?, device = ?, inode = ?, last_seen_at = ?, missing_at = NULL WHERE id = ?",
                    (
                        selected_id,
                        version_id,
                        initial.st_size,
                        initial.st_mtime_ns,
                        _sqlite_file_identity(initial.st_dev),
                        _sqlite_file_identity(initial.st_ino),
                        now,
                        location_id,
                    ),
                )
            else:
                cursor = conn.execute(
                    "INSERT INTO file_locations(attachment_id, root_id, relative_path, size_bytes, mtime_ns, device, inode, "
                    "current_version_id, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        selected_id,
                        root_id,
                        relative,
                        initial.st_size,
                        initial.st_mtime_ns,
                        _sqlite_file_identity(initial.st_dev),
                        _sqlite_file_identity(initial.st_ino),
                        version_id,
                        now,
                        now,
                    ),
                )
                location_id = int(cursor.lastrowid)
            final = path.stat()
            if (final.st_size, final.st_mtime_ns, final.st_dev, final.st_ino) != facts:
                raise ValueError("Replacement PDF changed while it was registered")
            conn.commit()
            return CatalogObservation(
                selected_id, location_id, version_id, "verified replacement"
            )
        except BaseException:
            conn.rollback()
            raise

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

    def get_document(self, document_id: int) -> dict[str, Any] | None:
        """Return one canonical document, or `None` when its ID is unknown."""
        row = self._connection.execute(
            "SELECT id, bibcode, metadata_json FROM documents WHERE id = ?",
            (document_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": int(row["id"]),
            "bibcode": row["bibcode"],
            "metadata": json.loads(row["metadata_json"]),
        }

    def set_document_cite_key(self, document_id: int, key: str) -> None:
        """Save or clear the typed citation key of one document.

        Parameters
        ----------
        document_id : `int`
            Catalog document ID.
        key : `str`
            The key. An empty string removes the typed key.

        Raises
        ------
        ValueError
            If `document_id` is unknown.
        """
        with self._connection:
            row = self._connection.execute(
                "SELECT metadata_json FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Unknown reference ID: {document_id}")
            metadata = json.loads(row["metadata_json"] or "{}")
            if key:
                metadata[CITE_KEY_METADATA_KEY] = key
            else:
                metadata.pop(CITE_KEY_METADATA_KEY, None)
            self._connection.execute(
                "UPDATE documents SET metadata_json = ?, updated_at = ? WHERE id = ?",
                (self._encode_json(metadata), self._utc_timestamp(), document_id),
            )

    def list_documents(self) -> list[dict[str, Any]]:
        """Return canonical documents, including references without PDFs."""
        return [
            {
                "id": int(row["id"]),
                "bibcode": row["bibcode"],
                "metadata": json.loads(row["metadata_json"]),
            }
            for row in self._connection.execute("SELECT * FROM documents ORDER BY id")
        ]

    def save_reference_documents(
        self, documents: list[Mapping[str, Any]], *, source_text: str | None = None
    ) -> list[int]:
        """Save one reference batch atomically and update attached metadata.

        Parameters
        ----------
        documents
            Document mappings containing `metadata`, optional `bibcode`, and
            optional existing `id`. Duplicate citation keys and DOIs fail the
            whole batch; existing documents are never merged implicitly.
        source_text
            Original BibTeX to store once for the batch, addressed by its
            SHA-256 digest in `catalog_meta`.
        """
        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            existing = self.list_documents()
            replacing = {item.get("id") for item in documents if item.get("id")}
            known_ids = {item["id"] for item in existing}
            if replacing - known_ids:
                raise ValueError(
                    f"Unknown reference IDs: {sorted(replacing - known_ids)}"
                )
            keys: set[str] = set()
            dois: set[str] = set()
            retained = [d for d in existing if d["id"] not in replacing]
            for index, item in enumerate(retained + documents):
                metadata = item["metadata"]
                reference = metadata.get(REFERENCE_METADATA_KEY, {})
                key = str(reference.get("cite_key") or item.get("bibcode") or "")
                if index >= len(retained) and key and key in keys:
                    raise ValueError(f"Citation key already exists: {key}")
                if key:
                    keys.add(key)
                values = metadata.get("doi") or []
                if isinstance(values, str):
                    values = [values]
                normalized = {
                    re.sub(
                        r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)",
                        "",
                        self._normalize_doi(value),
                    )
                    for value in values
                    if value
                }
                if index >= len(retained) and dois & normalized:
                    raise ValueError(f"DOI already exists: {min(dois & normalized)}")
                dois.update(normalized)
            ids: list[int] = []
            source_id = None
            if source_text is not None:
                source_id = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
                self._connection.execute(
                    "INSERT OR IGNORE INTO catalog_meta(key, value) VALUES (?, ?)",
                    (f"reference_source:{source_id}", source_text),
                )
            for item in documents:
                metadata = copy.deepcopy(dict(item["metadata"]))
                if source_id is not None:
                    metadata[REFERENCE_METADATA_KEY]["source_id"] = source_id
                now = self._utc_timestamp()
                document_id = item.get("id")
                try:
                    if document_id is None:
                        cursor = self._connection.execute(
                            "INSERT INTO documents(bibcode, metadata_json, created_at, updated_at) "
                            "VALUES (?, ?, ?, ?)",
                            (
                                item.get("bibcode"),
                                self._encode_json(metadata),
                                now,
                                now,
                            ),
                        )
                        document_id = int(cursor.lastrowid)
                    else:
                        self._connection.execute(
                            "UPDATE documents SET metadata_json = ?, updated_at = ? WHERE id = ?",
                            (self._encode_json(metadata), now, document_id),
                        )
                except sqlite3.IntegrityError as exc:
                    raise ValueError(
                        "ADS bibcode already belongs to another reference"
                    ) from exc
                self._set_document_identity(document_id, metadata)
                ids.append(document_id)
                for row in self._connection.execute(
                    "SELECT id, metadata_json FROM attachments WHERE document_id = ?",
                    (document_id,),
                ).fetchall():
                    attached = json.loads(row["metadata_json"])
                    locks = set(attached.get(LOCKED_METADATA_FIELDS_KEY, []))
                    attached.update(metadata)
                    attached[LOCKED_METADATA_FIELDS_KEY] = sorted(
                        locks | set(metadata.get(LOCKED_METADATA_FIELDS_KEY, []))
                    )
                    self._connection.execute(
                        "UPDATE attachments SET metadata_json = ?, updated_at = ? WHERE id = ?",
                        (self._encode_json(attached), now, row["id"]),
                    )
            return ids

    def attach_document_file(
        self,
        document_id: int,
        path: Path,
        *,
        sha256: str,
        size_bytes: int,
        mtime_ns: int,
        device: int,
        inode: int,
    ) -> dict[str, Any]:
        """Observe a PDF and link it without replacing another document identity."""
        with self._connection:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT metadata_json FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Unknown reference ID: {document_id}")
            observation = self._observe_file(
                path.parent,
                path.name,
                sha256=sha256,
                size_bytes=size_bytes,
                mtime_ns=mtime_ns,
                device=device,
                inode=inode,
            )
            attachment = self._attachment(observation.attachment_id)
            if attachment["document_id"] not in (None, document_id):
                raise ValueError("PDF already belongs to another reference")
            metadata = json.loads(attachment["metadata_json"])
            locks = set(metadata.get(LOCKED_METADATA_FIELDS_KEY, []))
            metadata.update(json.loads(row["metadata_json"]))
            metadata[LOCKED_METADATA_FIELDS_KEY] = sorted(
                locks | set(metadata.get(LOCKED_METADATA_FIELDS_KEY, []))
            )
            self._update_attachment(
                observation.attachment_id,
                document_id=document_id,
                status="resolved" if metadata.get("bibcode") else "unresolved",
                metadata=metadata,
            )
        return self.get_file(path.parent, path.name)

    def query_files(
        self,
        *,
        root: Path | None = None,
        status: str | None = None,
        resolved: bool | None = None,
        search: str | None = None,
        include_missing: bool = False,
        document_id: int | None = None,
        attachment_id: int | None = None,
        location_id: int | None = None,
        sha256: str | Collection[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return location records matching local catalog filters.

        Parameters
        ----------
        include_missing
            Include historical locations whose files are no longer observed.
            Normal queries return only current locations.
        document_id
            Restrict locations to one canonical document when supplied.
        attachment_id, location_id
            Restrict results to one attachment or one location identifier.
        sha256
            One digest, or a collection of digests, of the current content.
            An empty collection matches nothing. A collection runs one indexed
            query per distinct digest and merges the rows, so its size is not
            limited by the number of SQL variables.
        """
        if sha256 is not None and not isinstance(sha256, str):
            merged: dict[int, dict[str, Any]] = {}
            for digest in dict.fromkeys(sha256):
                for record in self.query_files(
                    root=root,
                    status=status,
                    resolved=resolved,
                    search=search,
                    include_missing=include_missing,
                    document_id=document_id,
                    attachment_id=attachment_id,
                    location_id=location_id,
                    sha256=digest,
                ):
                    merged[record["location_id"]] = record
            return sorted(
                merged.values(),
                key=lambda record: (record["relative_path"], record["location_id"]),
            )
        version_join, where, params = self._file_filter_sql(
            root=root,
            status=status,
            resolved=resolved,
            search=search,
            include_missing=include_missing,
            document_id=document_id,
            attachment_id=attachment_id,
            location_id=location_id,
            sha256=sha256,
        )
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
            {version_join} content_versions AS v ON v.id = l.current_version_id
            {where}
            ORDER BY l.relative_path, l.id
            """,
            params,
        ).fetchall()
        return [self._record_from_row(row) for row in rows]

    def count_files(
        self,
        *,
        root: Path | None = None,
        status: str | None = None,
        resolved: bool | None = None,
        search: str | None = None,
        include_missing: bool = False,
        document_id: int | None = None,
        attachment_id: int | None = None,
        location_id: int | None = None,
        sha256: str | None = None,
    ) -> int:
        """Count the locations that `query_files` returns for the same filters.

        The count is done in SQL, so no metadata JSON is parsed. Digest
        collections are not accepted; call `query_files` for those.
        """
        version_join, where, params = self._file_filter_sql(
            root=root,
            status=status,
            resolved=resolved,
            search=search,
            include_missing=include_missing,
            document_id=document_id,
            attachment_id=attachment_id,
            location_id=location_id,
            sha256=sha256,
        )
        row = self._connection.execute(
            f"""
            SELECT COUNT(*)
            FROM file_locations AS l
            JOIN library_roots AS r ON r.id = l.root_id
            JOIN attachments AS a ON a.id = l.attachment_id
            {version_join} content_versions AS v ON v.id = l.current_version_id
            {where}
            """,
            params,
        ).fetchone()
        return int(row[0])

    def _file_filter_sql(
        self,
        *,
        root: Path | None,
        status: str | None,
        resolved: bool | None,
        search: str | None,
        include_missing: bool,
        document_id: int | None,
        attachment_id: int | None,
        location_id: int | None,
        sha256: str | None,
    ) -> tuple[str, str, list[Any]]:
        """Return the version join, WHERE clause, and parameters for file queries.

        Shared by `query_files` and `count_files` so both select the same rows.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if not include_missing:
            clauses.append("l.missing_at IS NULL")
        if root is not None:
            clauses.append("r.canonical_path = ?")
            params.append(self._normalize_root(root))
        if document_id is not None:
            clauses.append("a.document_id = ?")
            params.append(document_id)
        if attachment_id is not None:
            clauses.append("a.id = ?")
            params.append(attachment_id)
        if location_id is not None:
            clauses.append("l.id = ?")
            params.append(location_id)
        if sha256 is not None:
            clauses.append("v.sha256 = ?")
            params.append(sha256)
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
        # A digest filter rejects locations without a version, so an inner join
        # lets SQLite start from idx_version_sha256.
        version_join = "JOIN" if sha256 is not None else "LEFT JOIN"
        return version_join, where, params

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

    def discover_root(
        self,
        root: Path,
        *,
        recursive: bool = True,
        cancelled: Callable[[], bool] | None = None,
    ) -> int:
        """Discover PDFs and verify known identity candidates by content.

        Changed tracked files are hashed before their filesystem facts are
        updated. A new path is also hashed when its filesystem identity has a
        known digest. New paths in an existing library are queued for watching;
        the first import retains the baseline behavior. The digest decides whether the file is that content again
        (see `_observe_file`). Unchanged files are never hashed. A file that
        vanishes before hashing is skipped; any other read error aborts the
        scan before the catalog changes.
        """
        normalized_root = Path(self._normalize_root(root))
        # Finish enumeration before changing locations. An inaccessible directory
        # must not look like a completed scan in which its files disappeared.
        directories = [normalized_root]
        observations: list[tuple[Path, os.stat_result]] = []
        while directories:
            if cancelled is not None and cancelled():
                raise InterruptedError("Local refresh cancelled")
            with os.scandir(directories.pop()) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        if recursive:
                            directories.append(Path(entry.path))
                    elif entry.name.lower().endswith(".pdf"):
                        try:
                            if entry.is_file():
                                # Windows DirEntry.stat omits device/inode IDs.
                                stat = (
                                    os.stat(entry.path)
                                    if os.name == "nt"
                                    else entry.stat()
                                )
                                observations.append((Path(entry.path), stat))
                        except FileNotFoundError:
                            continue
        known_rows = {
            record["current_path"]: record
            for record in self.query_files(root=normalized_root, include_missing=True)
        }
        prepared: list[tuple[str, os.stat_result, str | None]] = []
        for path, stat in observations:
            if cancelled is not None and cancelled():
                raise InterruptedError("Local refresh cancelled")
            relative_path = path.relative_to(normalized_root).as_posix()
            needs_digest = self._connection.execute(
                """
                SELECT EXISTS(
                    SELECT 1 FROM file_locations AS c
                    JOIN content_versions AS v ON v.id = c.current_version_id
                    WHERE c.device = ? AND c.inode = ? AND v.sha256 IS NOT NULL
                ) AND NOT EXISTS(
                    SELECT 1 FROM file_locations AS p
                    JOIN library_roots AS r ON r.id = p.root_id
                    WHERE r.canonical_path = ? AND p.relative_path = ?
                        AND p.missing_at IS NULL
                )
                """,
                (
                    _sqlite_file_identity(stat.st_dev),
                    _sqlite_file_identity(stat.st_ino),
                    str(normalized_root),
                    relative_path,
                ),
            ).fetchone()[0]
            previous = known_rows.get(relative_path)
            if previous is not None and previous["sha256"]:
                needs_digest = needs_digest or any(
                    previous[key] is not None and previous[key] != value
                    for key, value in (
                        ("size_bytes", stat.st_size),
                        ("mtime_ns", stat.st_mtime_ns),
                        ("device", stat.st_dev),
                        ("inode", stat.st_ino),
                    )
                )
            digest = None
            if needs_digest:
                try:
                    digest = sha256_file(path)
                except FileNotFoundError:
                    continue
            prepared.append((relative_path, stat, digest))
        seen_paths: set[str] = set()
        discovered = 0
        with self._connection:
            self._ensure_root(str(normalized_root))
            for relative_path, stat, digest in prepared:
                if cancelled is not None and cancelled():
                    raise InterruptedError("Local refresh cancelled")
                seen_paths.add(relative_path)
                self._observe_file(
                    normalized_root,
                    relative_path,
                    sha256=digest,
                    size_bytes=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                    device=stat.st_dev,
                    inode=stat.st_ino,
                    metadata=(
                        {WATCH_PENDING_KEY: True}
                        if known_rows
                        and (
                            relative_path not in known_rows
                            or known_rows[relative_path]["missing_at"] is not None
                            or any(
                                known_rows[relative_path][key] is not None
                                and known_rows[relative_path][key] != value
                                for key, value in (
                                    ("size_bytes", stat.st_size),
                                    ("mtime_ns", stat.st_mtime_ns),
                                    ("device", stat.st_dev),
                                    ("inode", stat.st_ino),
                                )
                            )
                        )
                        else None
                    ),
                    status=None,
                )
                discovered += 1
        self.mark_missing_locations(normalized_root, seen_paths, recursive=recursive)
        return discovered

    def update_file_status(self, root: Path, relative_path: str, status: str) -> bool:
        """Update the attachment status for one observed location."""
        record = self.get_file(root, relative_path)
        if record is None:
            return False
        if status == "resolved" and (
            record["document_id"] is None
            or (
                REFERENCE_METADATA_KEY in record["metadata"]
                and not record["metadata"].get("bibcode")
            )
        ):
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
        managed = REFERENCE_METADATA_KEY in record["metadata"]
        if managed:
            existing = record["metadata"]
            owned = {REFERENCE_METADATA_KEY, "bibcode", *REFERENCE_FIELD_MAP.values()}
            if any(clean_metadata.get(key) != existing.get(key) for key in owned):
                raise ValueError(
                    "Edit this reference's bibliographic fields in File → Local References…"
                )
            if not owned.issubset(
                set(clean_metadata.get(LOCKED_METADATA_FIELDS_KEY, []))
            ):
                raise ValueError(
                    "Reference fields stay protected; edit them in File → Local References…"
                )
        with self._connection:
            existing_stale = bool(
                record["metadata"].get(METADATA_STALE_KEY)
                or record.get(METADATA_STALE_KEY)
            )
            if existing_stale:
                clean_metadata[METADATA_STALE_KEY] = True

            if existing_stale:
                document_id = None
                status = "unresolved"
            else:
                if managed:
                    document_id = record["document_id"]
                elif "bibcode" in clean_metadata or not record.get("bibcode"):
                    # A paper without an ADS link becomes a document once its
                    # metadata is complete enough; see _ensure_document.
                    document_id = self._ensure_document(
                        clean_metadata, existing_id=record["document_id"]
                    )
                    if document_id is None and "bibcode" not in clean_metadata:
                        document_id = record["document_id"]
                else:
                    document_id = record["document_id"]
                if managed and not clean_metadata.get("bibcode"):
                    status = "unresolved"
                elif "status" in metadata and metadata["status"] not in (None, ""):
                    status = str(metadata["status"])
                elif "bibcode" in clean_metadata:
                    status = (
                        "resolved"
                        if document_id is not None and clean_metadata.get("bibcode")
                        else "unresolved"
                    )
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
        raw["_adsfolio_metrics"] = dict(metrics)
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
        sources: list[tuple[str, Callable[[], list[dict[str, Any]]]]] = []
        legacy_path = Path(normalized_root) / LEGACY_REGISTRY_FILENAME
        sidecar_path = Path(normalized_root) / SIDECAR_FILENAME
        legacy_ads_key = f"sqlite:{legacy_path}:ads_documents"
        sidecar_key = f"sidecar:{sidecar_path}"
        if legacy_path.exists():
            sources.append(
                (
                    f"sqlite:{legacy_path}",
                    lambda: self._read_legacy_sqlite(legacy_path),
                )
            )
        if sidecar_path.exists():
            sources.append(
                (sidecar_key, lambda: self._read_legacy_sidecar(sidecar_path))
            )
        # Settings are imported only with the first sidecar import; later opens
        # must not replace settings changed in the catalog.
        import_sidecar_settings = (
            sidecar_path.exists() and not self._migration_complete(sidecar_key)
        )

        imported = 0
        with self._connection:
            self._ensure_root(normalized_root)
            for source_key, read_records in sources:
                if self._migration_complete(source_key):
                    continue
                records = read_records()
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
            if legacy_path.exists() and not self._migration_complete(legacy_ads_key):
                documents = self._read_legacy_ads_documents(legacy_path)
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
                    (legacy_ads_key, self._utc_timestamp(), len(documents)),
                )
            if import_sidecar_settings:
                sidecar = Sidecar(
                    sidecar_path.parent,
                    filename=sidecar_path.name,
                )
                profile = sidecar.identify_profile
                if profile is not None:
                    self._connection.execute(
                        """
                        INSERT INTO catalog_meta(key, value) VALUES (?, ?)
                        ON CONFLICT(key) DO NOTHING
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
                        ON CONFLICT(key) DO NOTHING
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
                CREATE INDEX IF NOT EXISTS idx_attachment_document
                    ON attachments(document_id);
                CREATE INDEX IF NOT EXISTS idx_location_attachment
                    ON file_locations(attachment_id);
                CREATE INDEX IF NOT EXISTS idx_location_version
                    ON file_locations(current_version_id);
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
            # Version 3: documents are found by bibcode, then DOI, then arXiv
            # ID, so papers that ADS does not index are documents too. The
            # indexes are not unique: older catalogs can hold repeated DOIs.
            added_identity = self._ensure_column("documents", "doi", "TEXT")
            added_identity |= self._ensure_column("documents", "arxiv_id", "TEXT")
            self._connection.executescript(
                """
                CREATE INDEX IF NOT EXISTS documents_doi ON documents(doi);
                CREATE INDEX IF NOT EXISTS documents_arxiv_id ON documents(arxiv_id);
                """
            )
            if added_identity or stored_version < 3:
                self._backfill_document_identity()
            self._connection.execute(
                """
                INSERT INTO catalog_meta (key, value)
                VALUES ('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(CATALOG_SCHEMA_VERSION),),
            )

    def _ensure_column(self, table: str, column: str, definition: str) -> bool:
        """Add one schema column when upgrading an older catalog.

        Returns
        -------
        bool
            `True` when the column was added.
        """
        columns = {
            str(row["name"])
            for row in self._connection.execute(f"PRAGMA table_info({table})")
        }
        if column not in columns:
            self._connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
            )
            return True
        return False

    def _backfill_document_identity(self) -> None:
        """Fill the DOI and arXiv columns of every document from its metadata."""
        rows = self._connection.execute(
            "SELECT id, metadata_json FROM documents"
        ).fetchall()
        for row in rows:
            metadata = json.loads(row["metadata_json"] or "{}")
            if isinstance(metadata, dict):
                self._set_document_identity(int(row["id"]), metadata)

    @classmethod
    def canonical_doi(cls, value: Any) -> str:
        """Return a DOI in comparable form: lowercase, without URL or prefix."""
        text = cls._as_scalar(value) if not isinstance(value, str) else value
        return re.sub(
            r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)",
            "",
            cls._normalize_doi(text or ""),
        )

    @classmethod
    def document_identity(cls, metadata: Mapping[str, Any]) -> tuple[str, str, str]:
        """Return the bibcode, canonical DOI, and arXiv ID of document metadata.

        Missing identifiers are empty strings. The arXiv ID has no version.
        """
        bibcode = cls._as_scalar(metadata.get("bibcode")) or ""
        doi = cls.canonical_doi(metadata.get("doi"))
        arxiv = ""
        for key in ("arxiv", "arxiv_id", "doi", "identifier"):
            values = metadata.get(key)
            for value in values if isinstance(values, list) else [values]:
                arxiv = normalize_arxiv_id(value) if value else ""
                if arxiv:
                    break
            if arxiv:
                break
        if not arxiv and bibcode:
            arxiv = normalize_arxiv_id(bibcode)
        return bibcode.strip(), doi, arxiv

    def _set_document_identity(
        self, document_id: int, metadata: Mapping[str, Any]
    ) -> None:
        _bibcode, doi, arxiv = self.document_identity(metadata)
        self._connection.execute(
            "UPDATE documents SET doi = ?, arxiv_id = ? WHERE id = ?",
            (doi or None, arxiv or None, document_id),
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
        exact = self._connection.execute(
            """
            SELECT * FROM file_locations
            WHERE root_id = ? AND relative_path = ? AND attachment_id = ?
            ORDER BY missing_at IS NULL DESC, id DESC
            LIMIT 1
            """,
            (root_id, relative_path, attachment_id),
        ).fetchone()

        if exact is not None and exact["missing_at"] is None:
            return exact
        spelling = unicodedata.normalize("NFC", relative_path)
        if spelling.isascii():
            return exact
        root = self._connection.execute(
            "SELECT canonical_path FROM library_roots WHERE id = ?", (root_id,)
        ).fetchone()
        if root is None:
            return None
        # Unicode spellings can name one file on macOS and distinct files on Linux.
        base = Path(root[0])
        path = base / relative_path
        directory_names: dict[Path, set[str]] = {}

        def has_exact_spelling(relative: str) -> bool:
            parent = base
            for part in PurePosixPath(relative).parts:
                if parent not in directory_names:
                    directory_names[parent] = set(os.listdir(parent))
                if part not in directory_names[parent]:
                    return False
                parent = parent / part
            return True

        for row in self._connection.execute(
            "SELECT * FROM file_locations WHERE root_id = ? AND attachment_id = ? AND missing_at IS NULL",
            (root_id, attachment_id),
        ):
            if unicodedata.normalize("NFC", row["relative_path"]) != spelling:
                continue
            try:
                if not (
                    has_exact_spelling(row["relative_path"])
                    and has_exact_spelling(relative_path)
                ) and path.samefile(base / row["relative_path"]):
                    return row
            except OSError:
                continue
        return exact

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
                SELECT DISTINCT l.attachment_id, v.sha256
                FROM file_locations AS l
                LEFT JOIN content_versions AS v ON v.id = l.current_version_id
                WHERE l.device = ? AND l.inode = ?
                """,
                (_sqlite_file_identity(device), _sqlite_file_identity(inode)),
            ).fetchall()
            known = [row for row in rows if row["sha256"] is not None]
            candidates = list(
                {
                    int(row["attachment_id"])
                    for row in (known or rows)
                    if not known or row["sha256"] == sha256
                }
            )
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

    @staticmethod
    def _describes_paper(metadata: Mapping[str, Any]) -> bool:
        """Return whether metadata without a bibcode can stand as a document.

        It needs a title and verified provenance: Crossref, DataCite, or arXiv
        metadata, a local reference, or fields that the user entered or locked.
        A DOI found in PDF text alone is not enough.
        """
        title = metadata.get("title")
        has_title = (
            bool(title.strip())
            if isinstance(title, str)
            else any(str(item).strip() for item in title or [])
            if isinstance(title, list)
            else False
        )
        return has_title and any(
            metadata.get(key)
            for key in (
                EXTERNAL_METADATA_KEY,
                REFERENCE_METADATA_KEY,
                LOCKED_METADATA_FIELDS_KEY,
            )
        )

    def _find_document(self, bibcode: str, doi: str, arxiv: str) -> sqlite3.Row | None:
        """Find a document by bibcode, then DOI, then arXiv ID.

        A DOI or arXiv match with a different bibcode is another paper.
        """
        if bibcode:
            row = self._connection.execute(
                "SELECT id, bibcode, metadata_json FROM documents WHERE bibcode = ?",
                (bibcode,),
            ).fetchone()
            if row is not None:
                return row
        for column, value in (("doi", doi), ("arxiv_id", arxiv)):
            if not value:
                continue
            row = self._connection.execute(
                f"SELECT id, bibcode, metadata_json FROM documents "
                f"WHERE {column} = ? AND (bibcode IS NULL OR bibcode = ?) "
                "ORDER BY id LIMIT 1",
                (value, bibcode or ""),
            ).fetchone()
            if row is not None:
                return row
        return None

    def _ensure_document(
        self, metadata: Mapping[str, Any], *, existing_id: int | None = None
    ) -> int | None:
        """Return the document for `metadata`, creating or updating it.

        Documents are found by bibcode, then DOI, then arXiv ID. Without any
        identifier, `existing_id` is updated when given. Metadata without a
        bibcode creates a document only when ``_describes_paper`` accepts it.
        A DOI-only document that later gains a bibcode keeps its row.

        Returns
        -------
        int or None
            The document ID, or `None` when the metadata identifies no paper.
        """
        bibcode, doi, arxiv = self.document_identity(metadata)
        row = self._find_document(bibcode, doi, arxiv)
        if row is None and existing_id is not None and not (doi or arxiv):
            row = self._connection.execute(
                "SELECT id, bibcode, metadata_json FROM documents WHERE id = ?",
                (existing_id,),
            ).fetchone()
            # Reuse only a non-ADS document, or the same ADS paper. Clearing a
            # bibcode therefore unlinks the file from its ADS document.
            if row is not None and row["bibcode"] not in (None, bibcode):
                row = None
        if row is None and not (bibcode or self._describes_paper(metadata)):
            return None
        now = self._utc_timestamp()
        if row is None:
            cursor = self._connection.execute(
                """
                INSERT INTO documents (
                    bibcode, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?)
                """,
                (bibcode or None, self._encode_json(dict(metadata)), now, now),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("Catalog document insert did not return an id")
            document_id = int(cursor.lastrowid)
            self._set_document_identity(document_id, metadata)
            return document_id

        existing = json.loads(row["metadata_json"] or "{}")
        if not isinstance(existing, dict):
            existing = {}
        merged = self._merge_metadata(existing, metadata)
        document_id = int(row["id"])
        self._connection.execute(
            """
            UPDATE documents
            SET bibcode = COALESCE(bibcode, ?), metadata_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (bibcode or None, self._encode_json(merged), now, document_id),
        )
        self._set_document_identity(document_id, merged)
        return document_id

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
            if key == LOCKED_METADATA_FIELDS_KEY:
                merged[key] = sorted(locked | {str(field) for field in value})
                continue
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
            "device": int(row["device"], 16)
            if isinstance(row["device"], str)
            else row["device"],
            "inode": int(row["inode"], 16)
            if isinstance(row["inode"], str)
            else row["inode"],
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
            "metrics": raw.get("_adsfolio_metrics", {}),
            "raw": raw,
            "identifier": raw.get("identifier", []),
            "alternate_bibcode": raw.get("alternate_bibcode", []),
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
