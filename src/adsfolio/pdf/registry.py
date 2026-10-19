"""Root-scoped views over the central PDF catalog."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator, Mapping

from .catalog import (
    CATALOG_SCHEMA_VERSION,
    LEGACY_REGISTRY_FILENAME,
    LOCKED_METADATA_FIELDS_KEY,
    PdfCatalog,
)
from .identify_settings import IdentifySettings

if TYPE_CHECKING:
    from .rename_settings import RenameSettings

REGISTRY_FILENAME = LEGACY_REGISTRY_FILENAME
SCHEMA_VERSION = CATALOG_SCHEMA_VERSION
ADS_METADATA_FIELDS = (
    "abstract",
    "author",
    "author_count",
    "bibcode",
    "citation_count",
    "doi",
    "page",
    "pub",
    "title",
    "volume",
    "year",
    "bibstem",
)

SORTABLE_COLUMNS = {
    key: key
    for key in (
        "title",
        "author",
        "year",
        "pub",
        "bibcode",
        "arxiv",
        "doi",
        "status",
        "source",
        "filename",
        "relative_path",
        "size_bytes",
        "mtime_ns",
        "first_seen_at",
        "updated_at",
        "identified_at",
    )
}

_ARXIV_NUMERIC_PATTERN = re.compile(r"(?<![A-Za-z0-9])(\d{4}\.\d{4,5})(?:v\d+)?")
_LEGACY_ARXIV_BIBCODE_PATTERN = re.compile(
    r"\d{4}arXiv(?P<identifier>\d{9})", re.IGNORECASE
)

__all__ = [
    "ADS_METADATA_FIELDS",
    "FileReconciliation",
    "LOCKED_METADATA_FIELDS_KEY",
    "PdfRegistry",
    "PdfRegistryState",
    "REGISTRY_FILENAME",
    "SCHEMA_VERSION",
    "SORTABLE_COLUMNS",
    "merge_ads_metadata",
    "migrate_sidecar",
    "open_registry_state",
]


@dataclass(frozen=True)
class FileReconciliation:
    """Summary of filesystem facts reconciled against one catalog root."""

    scanned: int
    updated: int
    missing: int


def merge_ads_metadata(
    existing: Mapping[str, Any], document: Mapping[str, Any]
) -> dict[str, Any]:
    """Merge nonempty ADS values while retaining locally locked fields.

    Parameters
    ----------
    existing
        Metadata currently stored for one attachment.
    document
        ADS document returned for the attachment's Bibcode.

    Returns
    -------
    dict[str, Any]
        Merged metadata.
    """
    merged = dict(existing)
    locked = {
        str(field)
        for field in existing.get(LOCKED_METADATA_FIELDS_KEY, [])
        if str(field).strip()
    }
    for field in ADS_METADATA_FIELDS:
        value = document.get(field)
        if field not in locked and value not in (None, "", []):
            merged[field] = copy.deepcopy(value)
    return merged


class CentralPdfRegistry:
    """Root-scoped view over the user-wide :class:`PdfCatalog`."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory).expanduser().resolve()
        self.catalog = PdfCatalog()
        self.root_id = self.catalog.ensure_root(self.directory)
        self.catalog.migrate_legacy_root(self.directory)
        self.path = self.catalog.path
        self._connection = self.catalog._connection

    @property
    def schema_version(self) -> int:
        """Return the central catalog schema version."""
        return self.catalog.schema_version

    def count_files(self) -> int:
        """Return the number of current locations in this root."""
        return len(self.catalog.query_files(root=self.directory))

    def count_ads_documents(self) -> int:
        """Return the number of shared cached ADS documents."""
        return self.catalog.count_ads_documents()

    def reconcile_file_facts(self, root: Path) -> FileReconciliation:
        """Refresh current filesystem facts without deleting locations."""
        records = self.catalog.query_files(root=root)
        updated = 0
        missing = 0
        for record in records:
            path = Path(root) / str(record["current_path"])
            try:
                stat = path.stat()
            except OSError:
                missing += 1
                continue
            facts_match = (
                stat.st_size == record["size_bytes"]
                and stat.st_mtime_ns == record["mtime_ns"]
                and stat.st_dev == record["device"]
                and stat.st_ino == record["inode"]
            )
            if facts_match:
                continue
            try:
                sha256 = _sha256_file(path)
            except OSError:
                missing += 1
                continue
            self.catalog.observe_file(
                root,
                str(record["current_path"]),
                sha256=sha256,
                size_bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                device=stat.st_dev,
                inode=stat.st_ino,
                metadata=record["metadata"],
                status=record["status"],
            )
            updated += 1
        return FileReconciliation(
            scanned=len(records),
            updated=updated,
            missing=missing,
        )

    def upsert_file(
        self,
        *,
        root: Path,
        relative_path: str,
        current_path: str,
        sha256: str,
        size_bytes: int,
        mtime_ns: int,
        status: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> int:
        """Register one current root-relative file location."""
        del relative_path
        path = Path(current_path)
        actual_path = Path(root) / path
        device: int | None = None
        inode: int | None = None
        try:
            stat = actual_path.stat()
            device = stat.st_dev
            inode = stat.st_ino
        except OSError:
            pass
        observation = self.catalog.observe_file(
            root,
            current_path,
            sha256=sha256 or None,
            size_bytes=max(0, int(size_bytes)),
            mtime_ns=max(0, int(mtime_ns)),
            device=device,
            inode=inode,
            metadata=metadata,
            status=status,
        )
        return observation.attachment_id

    def find_file_by_sha256(self, sha256: str) -> dict[str, Any] | None:
        """Return the first current location matching a digest."""
        records = [
            record
            for record in self.catalog.query_files(root=self.directory)
            if record["sha256"] == sha256
        ]
        return records[0] if records else None

    def find_files_by_sha256(self, sha256_values: list[str]) -> list[dict[str, Any]]:
        """Return current locations matching any supplied digest."""
        values = {str(value).strip() for value in sha256_values if str(value).strip()}
        return [
            record
            for record in self.catalog.query_files(root=self.directory)
            if record["sha256"] in values
        ]

    def find_file_by_id(self, file_id: int) -> dict[str, Any] | None:
        """Return a current location for one attachment identifier."""
        for record in self.catalog.query_files(root=self.directory):
            if record["attachment_id"] == int(file_id):
                return record
        return None

    def find_location_by_id(self, location_id: int) -> dict[str, Any] | None:
        """Return a current location for one location identifier."""
        for record in self.catalog.query_files(root=self.directory):
            if record["location_id"] == int(location_id):
                return record
        return None

    def find_file_by_relative_path(
        self, root: Path, relative_path: str
    ) -> dict[str, Any] | None:
        """Return the current location at a root-relative path."""
        return self.catalog.get_file(root, relative_path)

    def find_files_by_bibcode(self, root: Path, bibcode: str) -> list[dict[str, Any]]:
        """Return current locations linked to one Bibcode."""
        return [
            record
            for record in self.catalog.query_files(root=root)
            if str(record.get("bibcode") or "").strip() == bibcode
        ]

    def find_file_by_current_path(
        self, root: Path, current_path: str
    ) -> dict[str, Any] | None:
        """Return the current location at ``current_path``."""
        return self.catalog.get_file(root, current_path)

    def update_file_status(self, root: Path, relative_path: str, status: str) -> bool:
        """Update one attachment's local status."""
        return self.catalog.update_file_status(root, relative_path, status)

    def replace_file_metadata(
        self,
        root: Path,
        relative_path: str,
        metadata: Mapping[str, Any],
    ) -> bool:
        """Replace one attachment's local metadata."""
        return self.catalog.replace_file_metadata(root, relative_path, metadata)

    def record_rename(
        self,
        root: Path,
        relative_path: str,
        current_path: str,
        new_relative_path: str | None = None,
    ) -> bool:
        """Record a location move while retaining attachment identity."""
        return self.catalog.move_file(
            root,
            relative_path,
            new_relative_path or current_path,
        )

    def iter_files(
        self,
        status: str | None = None,
        *,
        include_missing: bool = False,
    ) -> Iterator[dict[str, Any]]:
        """Yield locations in this root, optionally including missing history."""
        yield from self.catalog.query_files(
            root=self.directory,
            status=status,
            include_missing=include_missing,
        )

    def count_files_matching(self, **filters: Any) -> int:
        """Count current locations matching supported catalog filters."""
        return len(self._query_records(**filters))

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
        """Return sorted and paged current locations for this root."""
        if order_by not in SORTABLE_COLUMNS:
            raise ValueError(f"Unsortable column: {order_by!r}")
        if limit is not None and limit < 0:
            raise ValueError("limit must be non-negative or None")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        order_keys = [
            key
            for key in (order_by, secondary_order_by, tertiary_order_by)
            if key is not None
        ]
        invalid = next(
            (key for key in order_keys if key not in SORTABLE_COLUMNS),
            None,
        )
        if invalid is not None:
            raise ValueError(f"Unsortable column: {invalid!r}")
        records = self._query_records(**filters)
        for key in reversed(order_keys):
            records.sort(
                key=lambda record, sort_key=key: self._sort_value(record, sort_key)[1],
                reverse=descending,
            )
            records.sort(
                key=lambda record, sort_key=key: self._sort_value(record, sort_key)[0]
            )
        if limit is None:
            return records[offset:]
        return records[offset : offset + int(limit)]

    def delete_files_by_ids(self, file_ids: list[int]) -> int:
        """Delete selected current locations and orphaned attachments."""
        return self.catalog.delete_locations(self.directory, file_ids)

    def upsert_ads_document(self, document: Mapping[str, Any], *, source: str) -> None:
        """Cache one ADS document in the central catalog."""
        self.catalog.upsert_ads_document(document, source=source)

    def upsert_ads_documents(
        self, documents: list[Mapping[str, Any]], *, source: str
    ) -> None:
        """Cache ADS documents atomically in the central catalog."""
        self.catalog.upsert_ads_documents(documents, source=source)

    def find_ads_document_by_bibcode(self, bibcode: str) -> dict[str, Any] | None:
        """Return a cached ADS document by Bibcode."""
        return self.catalog.find_ads_document_by_bibcode(bibcode)

    def set_ads_document_metrics(
        self,
        bibcode: str,
        metrics: Mapping[str, int | None],
    ) -> bool:
        """Store cached citation metrics."""
        return self.catalog.set_ads_document_metrics(bibcode, metrics)

    def find_ads_documents_by_bibcodes(
        self, bibcodes: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Return cached documents by Bibcode."""
        return self.catalog.find_ads_documents_by_bibcodes(bibcodes)

    def find_ads_documents_by_doi(self, doi: str) -> list[dict[str, Any]]:
        """Return cached documents by normalized DOI."""
        return self.catalog.find_ads_documents_by_doi(doi)

    def find_ads_documents_by_dois(
        self, dois: list[str]
    ) -> dict[str, list[dict[str, Any]]]:
        """Return cached documents keyed by normalized DOI."""
        return self.catalog.find_ads_documents_by_dois(dois)

    def get_meta(self, key: str) -> str | None:
        """Return root-specific catalog metadata, falling back to global metadata."""
        return self.catalog.get_meta(
            f"root:{self.directory}:{key}"
        ) or self.catalog.get_meta(key)

    def set_meta(self, key: str, value: str) -> None:
        """Set root-specific catalog metadata."""
        self.catalog.set_meta(f"root:{self.directory}:{key}", value)

    def export_payload(self) -> dict[str, Any]:
        """Return this root as a sidecar-shaped export."""
        payload: dict[str, Any] = {"version": 2, "files": {}}
        profile = self.get_meta("identify_profile")
        if profile:
            payload["identify_profile"] = json.loads(profile)
        settings = self.catalog.get_meta(f"root:{self.directory}:identify_settings")
        if settings:
            payload["identify_settings"] = json.loads(settings)
        rename = self.catalog.get_meta(f"root:{self.directory}:rename_settings")
        if rename:
            payload["rename_settings"] = json.loads(rename)
        for record in self.iter_files():
            payload["files"][record["relative_path"]] = {
                **record.get("metadata", {}),
                "current_path": record["current_path"],
                "sha256": record["sha256"],
                "status": record["status"],
            }
        return payload

    def save(self) -> Path:
        """Commit pending catalog writes and return the central path."""
        self._connection.commit()
        return self.path

    def close(self) -> None:
        """Close the central catalog connection."""
        self.catalog.close()

    def _query_records(self, **filters: Any) -> list[dict[str, Any]]:
        source = filters.pop("source", None)
        duplicates_only = bool(filters.pop("duplicates_only", False))
        records = self.catalog.query_files(root=self.directory, **filters)
        if source is not None:
            records = [
                record
                for record in records
                if str(record.get("source") or "") == str(source)
            ]
        if duplicates_only:
            counts: dict[str, int] = {}
            for record in records:
                digest = str(record.get("sha256") or "")
                if digest:
                    counts[digest] = counts.get(digest, 0) + 1
            records = [
                record
                for record in records
                if counts.get(str(record.get("sha256") or ""), 0) > 1
            ]
        return records

    @staticmethod
    def _sort_value(record: Mapping[str, Any], key: str) -> tuple[bool, str]:
        if key == "status":
            resolved = (
                bool(record.get("bibcode")) and record.get("status") == "resolved"
            )
            return resolved, str(record.get("current_path") or "")
        if key == "filename":
            value = record.get("current_path")
        elif key == "relative_path":
            value = record.get("relative_path")
        elif key == "sha256":
            value = record.get("sha256")
        elif key == "arxiv":
            value = CentralPdfRegistry._arxiv_sort_value(record)
        else:
            value = record.get(key)
            if value in (None, "", []):
                value = (record.get("metadata") or {}).get(key)
        if isinstance(value, (list, tuple)):
            value = value[0] if value else ""
        return value in (None, "", []), str(value or "")

    @staticmethod
    def _arxiv_sort_value(record: Mapping[str, Any]) -> str:
        """Derive a sortable arXiv identifier from common ADS metadata forms."""
        metadata = record.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        candidates = [
            record.get("arxiv"),
            record.get("arxiv_id"),
            metadata.get("arxiv"),
            metadata.get("arxiv_id"),
            record.get("doi"),
            metadata.get("doi"),
            record.get("bibcode"),
            metadata.get("bibcode"),
        ]
        for candidate in candidates:
            values = candidate if isinstance(candidate, (list, tuple)) else [candidate]
            for value in values:
                text = str(value or "")
                match = _ARXIV_NUMERIC_PATTERN.search(text)
                if match:
                    return match.group(1)
                legacy_match = _LEGACY_ARXIV_BIBCODE_PATTERN.search(text)
                if legacy_match:
                    identifier = legacy_match.group("identifier")
                    return f"{identifier[:4]}.{identifier[4:]}"
        return ""


class CentralPdfRegistryState:
    """State adapter exposing current catalog locations to CLI workflows."""

    def __init__(self, registry: CentralPdfRegistry) -> None:
        self.registry = registry
        self.directory = registry.directory
        self.path = registry.path

    @staticmethod
    def _record_view(record: Mapping[str, Any]) -> dict[str, Any]:
        metadata = copy.deepcopy(record.get("metadata") or {})
        return {
            **metadata,
            "current_filename": record["current_path"],
            "sha256": record["sha256"],
            "status": record["status"],
        }

    @property
    def files(self) -> dict[str, dict[str, Any]]:
        """Return current location metadata keyed by relative path."""
        return {
            str(record["relative_path"]): self._record_view(record)
            for record in self.registry.iter_files()
        }

    @property
    def identify_profile(self) -> dict[str, Any] | None:
        """Return the saved identify profile."""
        raw = self.registry.get_meta("identify_profile")
        return json.loads(raw) if raw else None

    @property
    def identify_settings(self) -> IdentifySettings | None:
        """Return root-specific reusable Identify settings."""
        raw = self.registry.catalog.get_meta(f"root:{self.directory}:identify_settings")
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return IdentifySettings.from_mapping(payload)

    @property
    def rename_settings(self) -> RenameSettings | None:
        """Return root-specific reusable Rename settings."""
        from .rename_settings import RenameSettings

        raw = self.registry.catalog.get_meta(f"root:{self.directory}:rename_settings")
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return RenameSettings.from_mapping(payload)

    def get_file(self, relative_path: str) -> dict[str, Any] | None:
        """Return one current location in sidecar-shaped form."""
        record = self.registry.find_file_by_relative_path(self.directory, relative_path)
        return self._record_view(record) if record is not None else None

    def find_by_sha256(self, sha256: str) -> tuple[str, dict[str, Any]] | None:
        """Find a current location by digest."""
        record = self.registry.find_file_by_sha256(sha256)
        if record is None:
            return None
        return str(record["relative_path"]), self._record_view(record)

    def find_by_current_filename(
        self, filename: str
    ) -> tuple[str, dict[str, Any]] | None:
        """Find a current location by root-relative path."""
        record = self.registry.find_file_by_current_path(self.directory, filename)
        if record is None:
            return None
        return str(record["relative_path"]), self._record_view(record)

    def upsert_file(
        self, relative_path: str, metadata: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Register one current location through the central catalog."""
        record = dict(metadata)
        current = str(record.get("current_filename", relative_path))
        path = self.directory / current
        try:
            stat = path.stat()
            size_bytes, mtime_ns = stat.st_size, stat.st_mtime_ns
            device, inode = stat.st_dev, stat.st_ino
        except OSError:
            size_bytes = int(record.get("size_bytes", 0) or 0)
            mtime_ns = int(record.get("mtime_ns", 0) or 0)
            device = inode = None
        status = str(
            record.get("status")
            or ("resolved" if record.get("bibcode") else "unresolved")
        )
        self.registry.catalog.observe_file(
            self.directory,
            current,
            sha256=str(record.get("sha256", "") or "") or None,
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
            device=device,
            inode=inode,
            metadata=record,
            status=status,
        )
        stored = self.registry.find_file_by_current_path(self.directory, current)
        if stored is None:
            raise RuntimeError(f"Catalog location disappeared after upsert: {current}")
        return self._record_view(stored)

    def set_current_filename(self, relative_path: str, current_filename: str) -> bool:
        """Record a move between current root-relative locations."""
        record = self.registry.find_file_by_relative_path(self.directory, relative_path)
        if record is None:
            return False
        return self.registry.record_rename(
            self.directory,
            str(record["relative_path"]),
            current_filename,
            new_relative_path=current_filename,
        )

    def set_identify_profile(self, profile: Mapping[str, Any] | None) -> bool:
        """Set identify profile metadata and report whether it changed."""
        previous = self.identify_profile
        normalized = dict(profile) if profile is not None else None
        if previous == normalized:
            return False
        self.registry.set_meta(
            "identify_profile",
            "" if normalized is None else json.dumps(normalized, sort_keys=True),
        )
        return True

    def set_identify_settings(self, settings: IdentifySettings | None) -> bool:
        """Set root-specific reusable Identify settings."""
        if self.identify_settings == settings:
            return False
        key = f"root:{self.directory}:identify_settings"
        if settings is None:
            self.registry.catalog.delete_meta(key)
        else:
            self.registry.catalog.set_meta(
                key,
                json.dumps(settings.to_mapping(), sort_keys=True),
            )
        return True

    def set_rename_settings(self, settings: RenameSettings | None) -> bool:
        """Set root-specific reusable Rename settings."""
        if self.rename_settings == settings:
            return False
        key = f"root:{self.directory}:rename_settings"
        if settings is None:
            self.registry.catalog.delete_meta(key)
        else:
            self.registry.catalog.set_meta(
                key,
                json.dumps(settings.to_mapping(), sort_keys=True),
            )
        return True

    def save(self) -> Path:
        """Commit pending catalog writes."""
        return self.registry.save()

    def duplicates_by_sha256(self) -> dict[str, list[str]]:
        """Return duplicate current locations grouped by digest."""
        duplicates: dict[str, list[str]] = {}
        for record in self.registry.iter_files():
            digest = str(record.get("sha256") or "")
            if digest:
                duplicates.setdefault(digest, []).append(record["relative_path"])
        return {digest: paths for digest, paths in duplicates.items() if len(paths) > 1}

    def close(self) -> None:
        """Close the central catalog."""
        self.registry.close()


PdfRegistry = CentralPdfRegistry
PdfRegistryState = CentralPdfRegistryState


def _sha256_file(path: Path) -> str:
    """Hash one changed file in bounded memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def migrate_sidecar(json_path: Path, registry: CentralPdfRegistry) -> int:
    """Import legacy root data without modifying its source files."""
    return registry.catalog.migrate_legacy_root(json_path.parent).imported


def open_registry_state(directory: Path) -> CentralPdfRegistryState:
    """Open a central catalog state view for one library root."""
    return CentralPdfRegistryState(CentralPdfRegistry(directory))
