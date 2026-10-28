"""Reversible repair of PDFs that are attached to the wrong paper.

The repair is explicit and manifest-driven. Each manifest entry names one
catalog attachment, the PDF hash it was verified against, and the full ADS
document that the PDF really is. Every check runs before any change is made.
Applying an entry reassociates only that attachment with the correct document,
replaces the automatic bibliographic metadata, renames the file, and writes a
durable journal, a database backup, and PDF copies so the operation can be
undone. PDF bytes are never modified or overwritten.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import shutil
import sqlite3
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO

from .catalog import LOCKED_METADATA_FIELDS_KEY, REFERENCE_METADATA_KEY, PdfCatalog
from .cli_common import unique_destination
from .files import sha256_file
from .identifier import PdfIdentifier
from .identity import (
    IDENTITY_KEY,
    UnverifiedIdentityError,
    assert_mutation_allowed,
    extract_pdf_own_identity,
    target_identity,
)
from .readiness import EXTERNAL_METADATA_KEY, METADATA_SHA256_KEY, METADATA_STALE_KEY
from .rename_settings import RenameSettings
from .renamer import PdfRenamer, _resolve_rename_template

__all__ = [
    "AUTOMATIC_FIELDS",
    "IdentityRepairError",
    "RepairEntry",
    "apply_repairs",
    "build_plan",
    "load_manifest",
    "undo_repair",
]

_LOGGER = logging.getLogger(__name__)
_JOURNAL_VERSION = 1

# Fields that identification or an ADS refresh writes. They are replaced as a
# group by the repair, so a value that the correct record lacks is cleared.
# Everything else in the attachment metadata (user fields, locks, cite keys)
# is preserved.
AUTOMATIC_FIELDS = frozenset(
    {
        "abstract",
        "alternate_bibcode",
        "arxiv",
        "arxiv_id",
        "author",
        "author_count",
        "bibcode",
        "bibstem",
        "citation_count",
        "current_filename",
        "current_path",
        "doi",
        "identified_at",
        "identifier",
        "page",
        "original_bibcode",
        "arxiv_backup",
        "planned_filename",
        "provider",
        "pub",
        "publisher",
        "resolution_strategy",
        "sha256",
        "source",
        "status",
        "upgrade_status",
        "upgraded_at",
        "title",
        "volume",
        "year",
        EXTERNAL_METADATA_KEY,
        IDENTITY_KEY,
        METADATA_SHA256_KEY,
        METADATA_STALE_KEY,
    }
)
_ADS_EXTRA_FIELDS = ("abstract", "author_count", "citation_count")
_UNSTORED_FIELDS = frozenset({"status", "current_filename", "current_path"})


class IdentityRepairError(RuntimeError):
    """A preflight check failed, or an applied repair could not be completed."""


@dataclass(frozen=True)
class RepairEntry:
    """One verified wrong association from the manifest.

    Notes
    -----
    "path" is the absolute PDF path inside the library root and "sha256" the
    hash of the verified bytes. "location_id" and "attachment_id" must stay
    unchanged; "document_id" is the wrongly associated document, or None to
    skip that check. "metadata_before" holds fields the catalog must still
    contain. "proposed_ads_record" is the full ADS document of the paper the
    PDF really is. "authorized_locked_fields" lists locked fields the plan may
    overwrite; a change to any other locked field is rejected.
    `reviewed_parser_errors` and `audit_reference` record an explicit audit
    of partially readable evidence, without granting automatic verification.
    """

    path: str
    sha256: str
    location_id: int
    attachment_id: int
    document_id: int | None
    metadata_before: dict[str, Any]
    proposed_ads_record: dict[str, Any]
    authorized_locked_fields: tuple[str, ...] = ()
    reviewed_parser_errors: tuple[str, ...] = ()
    audit_reference: str = ""


def load_manifest(path: Path) -> list[RepairEntry]:
    """Read a JSON list of manifest entries.

    Raises
    ------
    IdentityRepairError
        The file is not a list of objects with the required fields.
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise IdentityRepairError("Manifest must be a JSON list of entries")
    entries = []
    for index, raw in enumerate(payload):
        try:
            document_id = raw.get("document_id")
            entries.append(
                RepairEntry(
                    path=str(raw["path"]),
                    sha256=str(raw["sha256"]),
                    location_id=int(raw["location_id"]),
                    attachment_id=int(raw["attachment_id"]),
                    document_id=None if document_id is None else int(document_id),
                    metadata_before=dict(raw["metadata_before"]),
                    proposed_ads_record=dict(raw["proposed_ads_record"]),
                    authorized_locked_fields=tuple(
                        raw.get("authorized_locked_fields", ())
                    ),
                    reviewed_parser_errors=tuple(raw.get("reviewed_parser_errors", ())),
                    audit_reference=str(raw.get("audit_reference", "")),
                )
            )
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise IdentityRepairError(
                f"Manifest entry {index} is malformed: {exc!r}"
            ) from exc
    return entries


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _fetch(
    conn: sqlite3.Connection, table: str, column: str, value: Any
) -> dict[str, Any] | None:
    """Return one row of a fixed catalog table as a dict."""
    row = conn.execute(f"SELECT * FROM {table} WHERE {column} = ?", (value,)).fetchone()
    return dict(row) if row is not None else None


def _restore_row(conn: sqlite3.Connection, table: str, row: Mapping[str, Any]) -> None:
    """Write every column of a snapshot row back to its table, by ID."""
    columns = [key for key in row if key != "id"]
    assignments = ", ".join(f"{column} = ?" for column in columns)
    with conn:
        conn.execute(
            f"UPDATE {table} SET {assignments} WHERE id = ?",
            [row[column] for column in columns] + [row["id"]],
        )


def _contained_path(root: Path, raw_path: str) -> Path:
    raw = Path(raw_path)
    if not raw.is_absolute():
        raise IdentityRepairError(f"Manifest path is not absolute: {raw_path}")
    candidate = raw.parent.resolve() / raw.name
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise IdentityRepairError(f"{raw.name} is outside the root {root}") from exc
    if candidate.is_symlink() or not candidate.is_file():
        raise IdentityRepairError(f"{raw.name} is not a regular file")
    return candidate


def _location_root_id(conn: sqlite3.Connection, location_id: int) -> int:
    """Return the root ID of one catalog location."""
    row = conn.execute(
        "SELECT root_id FROM file_locations WHERE id = ?", (location_id,)
    ).fetchone()
    return int(row[0])


def _reviewed_provenance(
    source: Path,
    metadata: Mapping[str, Any],
    provider: Mapping[str, Any],
    entry: RepairEntry,
) -> dict[str, Any]:
    """Record an explicit audit of partial evidence without verifying automation.

    Every readable own identifier must agree with the provider. The exact
    parser failures and unchanged file hash must match the reviewed manifest.
    This status is deliberately rejected by the normal mutation guard.
    """
    evidence = extract_pdf_own_identity(source)
    target = target_identity(metadata)
    authority = target_identity(provider)
    if (
        evidence.sha256 != entry.sha256
        or not entry.reviewed_parser_errors
        or tuple(evidence.errors) != entry.reviewed_parser_errors
        or not entry.audit_reference.strip()
        or not evidence.has_identifier
        or not target.has_stable_identity
        or target.claims_outside(authority)
        or not evidence.dois <= authority.dois
        or not evidence.arxiv_ids <= authority.arxiv_ids
    ):
        raise IdentityRepairError(
            f"{source.name}: reviewed parser failures, hash, audit reference, "
            "or provider identifiers do not match the manifest"
        )
    # Retain all core conflict checks, including immutable arXiv primaries.
    # Only the final refusal for partial parsing can receive an audit record.
    try:
        assert_mutation_allowed(
            source, metadata, expected_sha256=entry.sha256, provider_document=provider
        )
    except UnverifiedIdentityError:
        pass
    return {
        "status": "reviewed_repair",
        "authority": "manual_audit_manifest",
        "sha256": evidence.sha256,
        "identity": target.fingerprint,
        "target": target.primary,
        "own_doi": sorted(evidence.dois),
        "own_arxiv": sorted(evidence.arxiv_ids),
        "reviewed_parser_errors": list(evidence.errors),
        "audit_reference": entry.audit_reference,
        "checked_at": _now(),
    }


def _plan_item(
    catalog: PdfCatalog,
    root: Path,
    index: int,
    entry: RepairEntry,
    settings: RenameSettings,
    claimed: set[str],
) -> dict[str, Any]:
    """Run every preflight check for one entry and describe its changes."""
    conn = catalog._connection
    source = _contained_path(root, entry.path)
    name = source.name
    old_rel = source.relative_to(root).as_posix()
    doc = copy.deepcopy(entry.proposed_ads_record)
    bibcode, doi, arxiv = PdfCatalog.document_identity(doc)
    if not bibcode:
        # A DOI provider record (for example Crossref) needs a stable
        # identifier and full descriptive metadata; no bibcode is invented.
        missing = [
            key
            for key in ("provider", "title", "author")
            if not doc.get(key) or doc.get(key) == [""]
        ]
        if not (doi or arxiv) or missing:
            raise IdentityRepairError(
                f"{name}: record without bibcode needs a DOI or arXiv ID and "
                f"provider, title and author (missing: {missing or 'identifier'})"
            )

    record = catalog.get_file(root, old_rel)
    if record is None or record["missing_at"] is not None:
        raise IdentityRepairError(f"{name}: no active catalog location at {old_rel}")
    if (
        record["location_id"] != entry.location_id
        or record["attachment_id"] != entry.attachment_id
    ):
        raise IdentityRepairError(f"{name}: catalog location/attachment IDs differ")
    if entry.document_id is not None and record["document_id"] != entry.document_id:
        raise IdentityRepairError(f"{name}: catalog document ID differs")
    active = conn.execute(
        "SELECT COUNT(*) FROM file_locations "
        "WHERE attachment_id = ? AND missing_at IS NULL",
        (entry.attachment_id,),
    ).fetchone()[0]
    if active != 1:
        raise IdentityRepairError(
            f"{name}: attachment has {active} active locations (aliases)"
        )
    if record["sha256"] != entry.sha256 or sha256_file(source) != entry.sha256:
        raise IdentityRepairError(f"{name}: content hash differs from the manifest")

    live = copy.deepcopy(record["metadata"])
    for key, value in entry.metadata_before.items():
        if live.get(key) != value:
            raise IdentityRepairError(f"{name}: live metadata field {key!r} changed")
    if live.get(METADATA_STALE_KEY) or record.get(METADATA_STALE_KEY):
        raise IdentityRepairError(f"{name}: metadata is flagged stale")
    if REFERENCE_METADATA_KEY in live:
        raise IdentityRepairError(
            f"{name}: managed local reference; edit it in Local References"
        )

    if entry.reviewed_parser_errors:
        _reviewed_provenance(source, doc, doc, entry)
        # Provider mappings already contain normalized bibliographic fields.
        # Keep this explicit repair separate from verified identification.
        built = {
            key: copy.deepcopy(value)
            for key, value in doc.items()
            if key in AUTOMATIC_FIELDS and not key.startswith("_adsfolio_")
        }
        built.update(
            bibcode=bibcode,
            sha256=entry.sha256,
            identified_at=_now(),
            status="resolved" if bibcode else "unresolved",
        )
        built[METADATA_SHA256_KEY] = entry.sha256
        if not bibcode:
            built[EXTERNAL_METADATA_KEY] = {
                "provider": doc["provider"],
                "doi": doi,
                "identified_at": _now(),
            }
    else:
        assert_mutation_allowed(
            source, doc, expected_sha256=entry.sha256, provider_document=doc
        )
        built = PdfIdentifier(None).build_record(doc, source, sha256=entry.sha256)
    after = {key: value for key, value in live.items() if key not in AUTOMATIC_FIELDS}
    after.update(built)
    for key in _ADS_EXTRA_FIELDS:
        if doc.get(key) not in (None, "", []):
            after[key] = copy.deepcopy(doc[key])
    after[IDENTITY_KEY] = (
        _reviewed_provenance(source, after, doc, entry)
        if entry.reviewed_parser_errors
        else assert_mutation_allowed(
            source, after, expected_sha256=entry.sha256, provider_document=doc
        )
    )

    locked = {str(field) for field in live.get(LOCKED_METADATA_FIELDS_KEY, [])}
    changed = {
        key for key in locked & AUTOMATIC_FIELDS if live.get(key) != after.get(key)
    }
    blocked = sorted(changed - set(entry.authorized_locked_fields))
    if blocked:
        raise IdentityRepairError(f"{name}: locked fields would change: {blocked}")

    after_identity = PdfCatalog.document_identity(after)
    if after_identity[0] != bibcode:
        raise IdentityRepairError(f"{name}: rebuilt record changed the bibcode")
    old_document = (
        _fetch(conn, "documents", "id", record["document_id"])
        if record["document_id"] is not None
        else None
    )
    if (
        not (after_identity[1] or after_identity[2])
        and old_document is not None
        and old_document["bibcode"] is None
        and catalog._find_document(*after_identity) is None
    ):
        # The catalog would reuse and rewrite the wrong canonical document.
        raise IdentityRepairError(
            f"{name}: record has no DOI or arXiv ID and the wrong document has "
            "no bibcode; the catalog would edit that document in place"
        )
    rendered = PdfRenamer().render(
        _resolve_rename_template(settings.template, after),
        after,
        author_format=settings.author_format,
        max_authors=settings.max_authors,
        authors_when_truncated=settings.authors_when_truncated,
        char_policy=settings.char_policy,
        author_separator=settings.author_separator,
        author_truncation_suffix=settings.author_truncation_suffix,
    )
    same = source.parent / rendered
    if same.exists() and os.path.samefile(same, source):
        target = source
    else:
        target = unique_destination(source.parent, rendered)
    new_rel = target.relative_to(root).as_posix()
    if target != source:
        existing = catalog.get_file(root, new_rel)
        if new_rel in claimed or (existing and existing["missing_at"] is None):
            raise IdentityRepairError(f"{name}: target {new_rel} is already claimed")
        claimed.add(new_rel)

    return {
        "index": index,
        "old_rel": old_rel,
        "new_rel": new_rel,
        "sha256": entry.sha256,
        "location_id": entry.location_id,
        "attachment_id": entry.attachment_id,
        "document_id_before": record["document_id"],
        "after_bibcode": bibcode,
        "after_identity": list(after_identity),
        "after_status": after.get("status"),
        "metadata_before": live,
        "metadata_after": after,
        "provider_document": doc,
        "location_before": _fetch(conn, "file_locations", "id", entry.location_id),
        "root_id": _location_root_id(conn, entry.location_id),
        "attachment_before": _fetch(conn, "attachments", "id", entry.attachment_id),
    }


def build_plan(
    catalog: PdfCatalog,
    root: Path,
    entries: Sequence[RepairEntry],
    settings: RenameSettings,
) -> dict[str, Any]:
    """Check every entry and return the exact changes.

    Library files and catalog rows are not modified.

    Parameters
    ----------
    catalog : PdfCatalog
        Catalog holding the library.
    root : Path
        Library root; every manifest path must be inside it.
    entries : sequence of RepairEntry
        Manifest entries.
    settings : RenameSettings
        Rename format. A target that already exists gets the numeric suffix of
        unique_destination; the existing file is never touched.

    Returns
    -------
    dict
        JSON-compatible plan with root-relative old and new names, hashes,
        IDs, and the metadata before and after, per item.

    Raises
    ------
    IdentityRepairError
        Any preflight check fails.
    IdentityValidationError
        A PDF's own identifiers contradict or fail to confirm the record.
    """
    root = Path(root).expanduser().resolve()
    seen: set[tuple[str, Any]] = set()
    for entry in entries:
        for key in (("attachment", entry.attachment_id), ("path", entry.path)):
            if key in seen:
                raise IdentityRepairError(f"Duplicate manifest entry: {key}")
            seen.add(key)
    claimed: set[str] = set()
    items = [
        _plan_item(catalog, root, index, entry, settings, claimed)
        for index, entry in enumerate(entries)
    ]
    return {"root": str(root), "rename_settings": settings.to_mapping(), "items": items}


def _write_journal(path: Path, journal: dict[str, Any]) -> None:
    """Replace the journal atomically after flushing its data.

    POSIX also flushes the parent directory. Windows does not support directory
    handles through `os.open`.
    """
    journal["updated_at"] = _now()
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(journal, handle, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    if os.name == "posix":
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _fsync_file(path: Path) -> None:
    """Flush a writable backup file through the operating system."""
    with path.open("r+b") as handle:
        os.fsync(handle.fileno())


def _assert_no_other_writer(catalog: PdfCatalog) -> None:
    """Fail if another connection currently holds the catalog write lock."""
    probe = sqlite3.connect(catalog.path, timeout=0)
    try:
        probe.execute("BEGIN IMMEDIATE")
        probe.rollback()
    except sqlite3.OperationalError as exc:
        raise IdentityRepairError(
            f"Catalog is in use by another writer: {exc}"
        ) from exc
    finally:
        probe.close()


def _acquire_lock(directory: Path) -> BinaryIO:
    """Acquire an exclusive repair lock without waiting or truncating its file."""
    handle = (directory / ".repair.lock").open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise IdentityRepairError("Another repair is running") from exc
    return handle


def _release_lock(handle: BinaryIO) -> None:
    """Release the repair lock and close its file."""
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        handle.close()


def _move_no_overwrite(source: Path, destination: Path) -> None:
    """Move a file without ever replacing an existing destination.

    A hard link fails atomically if the destination exists. A filesystem
    without hard links is rejected, because a check-then-rename fallback
    could overwrite a file created in between.
    """
    try:
        os.link(source, destination)
    except FileExistsError:
        raise
    except OSError as exc:
        raise IdentityRepairError(
            f"Cannot move {source.name} without overwrite risk: hard links "
            f"unsupported here ({exc})"
        ) from exc
    os.unlink(source)


def _backup(
    catalog: PdfCatalog, plan: Mapping[str, Any], directory: Path
) -> dict[str, Any]:
    """Copy the database (SQLite backup API) and every affected PDF."""
    (directory / "pdfs").mkdir(parents=True)
    database = directory / "catalog.sqlite"
    target = sqlite3.connect(database)
    try:
        catalog._connection.backup(target)
        if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise IdentityRepairError("Catalog backup failed its integrity check")
    finally:
        target.close()
    _fsync_file(database)
    pdfs = {}
    root = Path(plan["root"])
    for item in plan["items"]:
        copy_path = directory / "pdfs" / f"{item['index']:02d}-{item['sha256']}.pdf"
        source_path = root / item["old_rel"]
        with source_path.open("rb") as source, copy_path.open("xb") as target:
            shutil.copyfileobj(source, target)
            target.flush()
            os.fsync(target.fileno())
        shutil.copystat(source_path, copy_path)
        if sha256_file(copy_path) != item["sha256"]:
            raise IdentityRepairError(f"Backup of {item['old_rel']} differs")
        pdfs[str(item["index"])] = str(copy_path)
    return {"directory": str(directory), "database": str(database), "pdfs": pdfs}


def _other_rows(
    conn: sqlite3.Connection, item: Mapping[str, Any], document_id: int | None
) -> list[Any]:
    """Snapshot every row the repair of one item must not change.

    The target document, identified by its row ID, is excluded.
    """
    queries = (
        ("SELECT * FROM attachments WHERE id != ?", item["attachment_id"]),
        ("SELECT * FROM file_locations WHERE id != ?", item["location_id"]),
        ("SELECT * FROM documents WHERE id IS NOT ?", document_id),
        ("SELECT * FROM content_versions WHERE ? IS NOT NULL", 1),
    )
    return [
        [tuple(row) for row in conn.execute(f"{sql} ORDER BY 1", (arg,))]
        for sql, arg in queries
    ]


def _target_document(
    catalog: PdfCatalog, item: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Return the document that the correct identity resolves to, if it exists.

    Uses the catalog's own lookup (bibcode, then DOI, then arXiv ID), the same
    one replace_file_metadata applies, and never matches on empty identifiers.
    """
    bibcode, doi, arxiv = item["after_identity"]
    if not (bibcode or doi or arxiv):
        raise IdentityRepairError(f"{item['old_rel']}: record has no identifier")
    row = catalog._find_document(bibcode, doi, arxiv)
    if row is None:
        return None
    return _fetch(catalog._connection, "documents", "id", row["id"])


def _replace_metadata(catalog: PdfCatalog, root: Path, item: dict[str, Any]) -> bool:
    """Write the new metadata; keep a preexisting target document unchanged.

    The fresh metadata belongs to this attachment. replace_file_metadata also
    merges it into the canonical document of the correct paper, which other
    attachments share, so that document row is put back from its snapshot.
    """
    replaced = catalog.replace_file_metadata(
        root, item["new_rel"], copy.deepcopy(item["metadata_after"])
    )
    before = item["target_document_before"]
    if replaced and before is not None:
        _restore_row(catalog._connection, "documents", before)
    return replaced


def _verify_item(catalog: PdfCatalog, root: Path, item: dict[str, Any]) -> None:
    """Confirm one applied item, and record the resulting rows."""
    conn = catalog._connection
    if sha256_file(root / item["new_rel"]) != item["sha256"]:
        raise IdentityRepairError("PDF bytes changed during repair")
    if item["old_rel"] != item["new_rel"] and (root / item["old_rel"]).exists():
        raise IdentityRepairError("Old path still exists after the move")
    before = item["location_before"]
    location = _fetch(conn, "file_locations", "id", item["location_id"])
    unchanged = (
        "attachment_id",
        "root_id",
        "size_bytes",
        "mtime_ns",
        "device",
        "inode",
        "current_version_id",
        "first_seen_at",
    )
    if (
        location is None
        or location["relative_path"] != item["new_rel"]
        or location["missing_at"] is not None
        or any(location[key] != before[key] for key in unchanged)
    ):
        raise IdentityRepairError("Catalog location is not as planned")
    attachment = _fetch(conn, "attachments", "id", item["attachment_id"])
    document = (
        _fetch(conn, "documents", "id", attachment["document_id"])
        if attachment is not None and attachment["document_id"] is not None
        else None
    )
    found = catalog._find_document(*item["after_identity"])
    expected = {
        key: value
        for key, value in item["metadata_after"].items()
        if key not in _UNSTORED_FIELDS
    }
    if (
        attachment is None
        or document is None
        or (document["bibcode"] or "") != item["after_bibcode"]
        or found is None
        or found["id"] != document["id"]
        or (
            item["target_document_before"] is not None
            and document != item["target_document_before"]
        )
        or attachment["document_id"] != document["id"]
        or attachment["status"] != item["after_status"]
        or json.loads(attachment["metadata_json"]) != expected
    ):
        raise IdentityRepairError("Catalog attachment is not as planned")
    item["after"] = {
        "location": location,
        "attachment": attachment,
        "document": document,
    }


def _apply_item(
    catalog: PdfCatalog,
    root: Path,
    item: dict[str, Any],
    journal: dict[str, Any],
    journal_path: Path,
) -> None:
    conn = catalog._connection
    old_abs, new_abs = root / item["old_rel"], root / item["new_rel"]
    moves = item["old_rel"] != item["new_rel"]
    if (
        _fetch(conn, "file_locations", "id", item["location_id"])
        != item["location_before"]
        or _fetch(conn, "attachments", "id", item["attachment_id"])
        != item["attachment_before"]
        or sha256_file(old_abs) != item["sha256"]
        or (moves and new_abs.exists())
    ):
        raise IdentityRepairError(f"{item['old_rel']} changed since the plan")
    item["target_document_before"] = _target_document(catalog, item)
    item["root_before"] = _fetch(conn, "library_roots", "id", item["root_id"])
    before_target = item["target_document_before"]
    others_before = _other_rows(conn, item, before_target and before_target["id"])

    def step(name: str, action: Callable[[], Any]) -> None:
        item["pending_step"] = name
        _write_journal(journal_path, journal)
        if action() is False:
            raise IdentityRepairError(f"Catalog step {name} did not apply")
        item["steps"].append(name)
        item["pending_step"] = None
        _write_journal(journal_path, journal)

    if moves:
        step("fs_move", lambda: _move_no_overwrite(old_abs, new_abs))
        step(
            "catalog_move",
            lambda: catalog.move_file(root, item["old_rel"], item["new_rel"]),
        )
    step("catalog_metadata", lambda: _replace_metadata(catalog, root, item))
    _verify_item(catalog, root, item)
    if _other_rows(conn, item, item["after"]["document"]["id"]) != others_before:
        raise IdentityRepairError(f"{item['old_rel']}: unrelated rows changed")
    item["state"] = "done"
    _write_journal(journal_path, journal)


def _revert_fs(root: Path, item: Mapping[str, Any]) -> None:
    old_abs, new_abs = root / item["old_rel"], root / item["new_rel"]
    if new_abs.exists():
        if old_abs.exists():
            if not os.path.samefile(old_abs, new_abs):
                raise IdentityRepairError(f"{item['old_rel']} is occupied")
            os.unlink(new_abs)
        else:
            _move_no_overwrite(new_abs, old_abs)
    if sha256_file(old_abs) != item["sha256"]:
        raise IdentityRepairError(f"{item['old_rel']} differs after restore")


def _revert_metadata(conn: sqlite3.Connection, item: Mapping[str, Any]) -> None:
    current = _fetch(conn, "attachments", "id", item["attachment_id"])
    _restore_row(conn, "attachments", item["attachment_before"])
    before = item.get("target_document_before")
    if before is not None:
        if _fetch(conn, "documents", "id", before["id"]) is not None:
            _restore_row(conn, "documents", before)
        return
    created = current["document_id"] if current else None
    if created is not None and created != item["attachment_before"]["document_id"]:
        with conn:
            conn.execute(
                "DELETE FROM documents WHERE id = ? AND NOT EXISTS "
                "(SELECT 1 FROM attachments WHERE document_id = documents.id)",
                (created,),
            )


def _revert_item(catalog: PdfCatalog, root: Path, item: Mapping[str, Any]) -> list[str]:
    """Undo the recorded steps of one item; every step is idempotent."""
    conn = catalog._connection
    steps = list(item["steps"])
    if item.get("pending_step"):
        steps.append(item["pending_step"])
    errors: list[str] = []
    for name in reversed(steps):
        try:
            if name == "catalog_metadata":
                _revert_metadata(conn, item)
            elif name == "catalog_move":
                _restore_row(conn, "file_locations", item["location_before"])
                _restore_row(conn, "library_roots", item["root_before"])
            elif name == "fs_move":
                _revert_fs(root, item)
        except Exception as exc:  # noqa: BLE001 - keep reverting the other steps
            errors.append(f"{name}: {exc!r}")
    return errors


def apply_repairs(
    catalog: PdfCatalog,
    root: Path,
    entries: Sequence[RepairEntry],
    settings: RenameSettings,
    journal_dir: Path,
) -> Path:
    """Preflight, back up, and apply every entry; revert everything on failure.

    SQLite cannot hold one transaction across catalog methods (each commits),
    so every item records its steps in the journal and a failure reverts the
    recorded steps from exact row snapshots. If a revert step itself fails,
    the journal phase is failed_partial and lists what still needs
    undo_repair; the database is never restored wholesale.

    Parameters
    ----------
    catalog : PdfCatalog
        Catalog holding the library. No other writer may be active.
    root : Path
        Library root.
    entries : sequence of RepairEntry
        Manifest entries; all are checked before the first change.
    settings : RenameSettings
        Rename format.
    journal_dir : Path
        Directory for the journal and the backup.

    Returns
    -------
    Path
        The journal file (phase applied).

    Raises
    ------
    IdentityRepairError
        A check failed or an item could not be applied. When the revert
        succeeded, nothing has changed.
    """
    journal_dir = Path(journal_dir).expanduser().resolve()
    journal_dir.mkdir(parents=True, exist_ok=True)
    lock = _acquire_lock(journal_dir)
    try:
        plan = build_plan(catalog, root, entries, settings)
        _assert_no_other_writer(catalog)
        root = Path(plan["root"])
        stamp = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
        journal_id = f"repair-{stamp}"
        journal_path = journal_dir / f"{journal_id}.json"
        journal: dict[str, Any] = {
            "version": _JOURNAL_VERSION,
            "journal_id": journal_id,
            "phase": "prepared",
            "created_at": _now(),
            "root": plan["root"],
            "catalog": str(catalog.path),
            "rename_settings": plan["rename_settings"],
            "backup": _backup(catalog, plan, journal_dir / f"{journal_id}-backup"),
            "items": [
                {**item, "state": "pending", "steps": [], "pending_step": None}
                for item in plan["items"]
            ],
        }
        _write_journal(journal_path, journal)
        journal["phase"] = "applying"
        _write_journal(journal_path, journal)
        try:
            for item in journal["items"]:
                _apply_item(catalog, root, item, journal, journal_path)
        except Exception as exc:
            _LOGGER.error("Repair failed; reverting: %r", exc)
            failed: list[str] = []
            for item in reversed(journal["items"]):
                if not (item["steps"] or item["pending_step"]):
                    continue
                errors = _revert_item(catalog, root, item)
                item["state"] = "rollback_failed" if errors else "rolledback"
                item["rollback_errors"] = errors
                failed += [f"{item['old_rel']}: {e}" for e in errors]
            journal["phase"] = "failed_partial" if failed else "rolledback"
            journal["error"] = repr(exc)
            journal["rollback_errors"] = failed
            _write_journal(journal_path, journal)
            raise IdentityRepairError(
                f"Repair failed ({exc!r}); journal phase {journal['phase']}: "
                f"{journal_path}"
            ) from exc
        journal["phase"] = "applied"
        _write_journal(journal_path, journal)
        return journal_path
    finally:
        _release_lock(lock)


def _undo_problems(
    catalog: PdfCatalog, root: Path, item: Mapping[str, Any]
) -> list[str]:
    """Return why a completed item cannot be undone safely (empty if it can)."""
    conn = catalog._connection
    name, after = item["old_rel"], item["after"]
    problems = []
    new_abs, old_abs = root / item["new_rel"], root / item["old_rel"]
    if not new_abs.is_file() or sha256_file(new_abs) != item["sha256"]:
        problems.append(f"{name}: repaired file is missing or changed")
    if item["old_rel"] != item["new_rel"] and old_abs.exists():
        problems.append(f"{name}: original path is occupied")
    for table, value, snapshot in (
        ("file_locations", item["location_id"], after["location"]),
        ("attachments", item["attachment_id"], after["attachment"]),
        ("documents", after["document"]["id"], after["document"]),
    ):
        if _fetch(conn, table, "id", value) != snapshot:
            problems.append(f"{name}: {table} row changed since the repair")
    return problems


def undo_repair(catalog: PdfCatalog, journal_path: Path) -> Path:
    """Reverse a completed repair from its journal.

    Every item is checked first: the repaired file, the catalog rows, and the
    target document must be exactly as the repair left them, and the original
    path must be free. An intervening edit or collision refuses the whole undo
    before anything changes.

    Parameters
    ----------
    catalog : PdfCatalog
        Catalog the repair was applied to.
    journal_path : Path
        Journal written by apply_repairs.

    Returns
    -------
    Path
        The journal file (phase undone).

    Raises
    ------
    IdentityRepairError
        The journal phase does not allow undo, a check fails, or a revert
        step fails (the journal then lists what remains).
    """
    journal_path = Path(journal_path).expanduser().resolve()
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    if journal.get("phase") not in {"applied", "failed_partial"}:
        raise IdentityRepairError(
            f"Journal phase {journal.get('phase')!r} cannot be undone"
        )
    root = Path(journal["root"])
    lock = _acquire_lock(journal_path.parent)
    try:
        problems: list[str] = []
        for item in journal["items"]:
            if item["state"] == "done":
                problems += _undo_problems(catalog, root, item)
        if problems:
            raise IdentityRepairError("Undo refused: " + "; ".join(problems))
        _assert_no_other_writer(catalog)
        conn = catalog._connection
        remaining: list[str] = []
        for item in reversed(journal["items"]):
            if item["state"] not in {"done", "rollback_failed"}:
                continue
            errors = _revert_item(catalog, root, item)
            if not errors and (
                _fetch(conn, "file_locations", "id", item["location_id"])
                != item["location_before"]
                or _fetch(conn, "attachments", "id", item["attachment_id"])
                != item["attachment_before"]
            ):
                errors.append("catalog rows differ after restore")
            item["state"] = "undo_failed" if errors else "undone"
            remaining += [f"{item['old_rel']}: {e}" for e in errors]
        journal["phase"] = "undo_failed" if remaining else "undone"
        journal["undo_errors"] = remaining
        _write_journal(journal_path, journal)
        if remaining:
            raise IdentityRepairError("Undo incomplete: " + "; ".join(remaining))
        return journal_path
    finally:
        _release_lock(lock)
