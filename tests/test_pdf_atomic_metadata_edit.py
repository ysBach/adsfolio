"""Check atomic metadata edits with isolated catalogs and files."""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest

from adsfolio.config import Config
from adsfolio.pdf.catalog import (
    LOCKED_METADATA_FIELDS_KEY,
    REFERENCE_FIELD_MAP,
    REFERENCE_METADATA_KEY,
    PdfCatalog,
)
from adsfolio.pdf.readiness import METADATA_STALE_KEY
from adsfolio.pdf.registry import PdfRegistry


@pytest.fixture
def registries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    root = tmp_path / "first"
    root.mkdir()
    first = PdfRegistry(root)
    second = PdfRegistry(root)
    try:
        yield first, second
    finally:
        first.close()
        second.close()


def _register(
    catalog: PdfCatalog,
    root: Path,
    name: str = "paper.pdf",
    *,
    content: bytes = b"%PDF-1.4 first test file",
    metadata: dict[str, Any] | None = None,
) -> tuple[Path, dict[str, Any]]:
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.write_bytes(content)
    stat = path.stat()
    catalog.observe_file(
        root,
        name,
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        device=stat.st_dev,
        inode=stat.st_ino,
        metadata=metadata
        if metadata is not None
        else {
            "bibcode": "2024Test......1A",
            "title": ["Original title"],
            "notes": "Original note",
            LOCKED_METADATA_FIELDS_KEY: ["title"],
        },
        status="resolved",
    )
    record = catalog.get_file(root, name)
    assert record is not None
    return path, record


def test_success_through_registry_preserves_pdf_and_unrelated_record(registries):
    first, second = registries
    root = first.directory
    path, before = _register(first.catalog, root)
    other_root = root.parent / "other"
    other_path, other = _register(
        first.catalog,
        other_root,
        content=b"%PDF-1.4 unrelated file",
        metadata={"bibcode": "2024Test......2B", "title": ["Other"]},
    )
    before_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    other_sha = hashlib.sha256(other_path.read_bytes()).hexdigest()
    other_document = second.catalog.get_document(other["document_id"])
    edited = {**copy.deepcopy(before["metadata"]), "notes": "Edited note"}

    assert first.replace_file_metadata_if_unchanged(root, path.name, edited, before)

    after = second.find_file_by_current_path(root, path.name)
    assert after is not None
    assert after["metadata"] == edited
    for key in ("location_id", "attachment_id", "document_id", "version_id", "sha256"):
        assert after[key] == before[key]
    assert after["metadata"][LOCKED_METADATA_FIELDS_KEY] == ["title"]
    document = second.catalog.get_document(before["document_id"])
    assert document is not None
    assert document["metadata"]["title"] == ["Original title"]
    assert second.catalog.get_file(other_root, other_path.name) == other
    assert second.catalog.get_document(other["document_id"]) == other_document
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before_sha
    assert hashlib.sha256(other_path.read_bytes()).hexdigest() == other_sha


@pytest.mark.parametrize("changed_fields", [None, {"notes"}, {"notes", "bibcode"}])
def test_conditional_notes_edit_preserves_unresolved_status_with_bibcode(
    registries, changed_fields
):
    first, second = registries
    root = first.directory
    path, _ = _register(
        first.catalog, root, metadata={"bibcode": "B1", "notes": "Original"}
    )
    assert first.catalog.update_file_status(root, path.name, "unresolved")
    before = first.catalog.get_file(root, path.name)
    document = first.catalog.get_document(before["document_id"])
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    edited = {**copy.deepcopy(before["metadata"]), "notes": "Edited"}

    assert first.catalog.replace_file_metadata_if_unchanged(
        root,
        path.name,
        edited,
        before,
        expected_document=document,
        changed_fields=changed_fields,
    )

    after = second.catalog.get_file(root, path.name)
    assert after["status"] == "unresolved"
    assert after["metadata"] == edited
    assert after["document_id"] == before["document_id"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize(
    ("requested_status", "expected_status"),
    [
        ("unresolved", "unresolved"),
        ("resolved", "resolved"),
        (None, "resolved"),
        ("", "resolved"),
    ],
)
def test_conditional_metadata_edit_retains_explicit_status_behavior(
    registries, requested_status, expected_status
):
    first, second = registries
    root = first.directory
    path, _ = _register(first.catalog, root, metadata={"bibcode": "B1", "notes": "Old"})
    assert first.catalog.update_file_status(root, path.name, "unresolved")
    before = first.catalog.get_file(root, path.name)
    edited = {
        **copy.deepcopy(before["metadata"]),
        "notes": "New",
        "status": requested_status,
    }

    assert first.catalog.replace_file_metadata_if_unchanged(
        root, path.name, edited, before
    )

    assert second.catalog.get_file(root, path.name)["status"] == expected_status


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("bibcode", "B2"),
        ("doi", ["10.1234/example"]),
        ("arxiv", "arXiv:2401.12345"),
        ("arxiv_id", "2401.12345"),
        ("identifier", ["arXiv:2401.12345"]),
        ("alternate_bibcode", ["Alternate"]),
        ("_adsfolio_identity", {"source": "new proof"}),
    ],
)
def test_conditional_identity_changes_keep_existing_status_behavior(
    registries, key, value
):
    first, second = registries
    root = first.directory
    path, _ = _register(first.catalog, root, metadata={"bibcode": "B1", "notes": "Old"})
    assert first.catalog.update_file_status(root, path.name, "unresolved")
    before = first.catalog.get_file(root, path.name)
    edited = {**copy.deepcopy(before["metadata"]), key: value, "notes": "New"}

    assert first.catalog.replace_file_metadata_if_unchanged(
        root, path.name, edited, before, changed_fields={"notes"}
    )

    after = second.catalog.get_file(root, path.name)
    assert after["status"] == "resolved"
    assert after["metadata"] == edited


def test_conditional_identity_edit_honors_explicit_unresolved_status(registries):
    first, second = registries
    root = first.directory
    path, before = _register(first.catalog, root, metadata={"bibcode": "B1"})
    edited = {
        **copy.deepcopy(before["metadata"]),
        "bibcode": "B2",
        "status": "unresolved",
    }

    assert first.catalog.replace_file_metadata_if_unchanged(
        root, path.name, edited, before
    )

    after = second.catalog.get_file(root, path.name)
    assert after["status"] == "unresolved"
    assert after["document_id"] != before["document_id"]


def test_unconditional_notes_edit_retains_existing_status_behavior(registries):
    first, second = registries
    root = first.directory
    path, _ = _register(first.catalog, root, metadata={"bibcode": "B1", "notes": "Old"})
    assert first.catalog.update_file_status(root, path.name, "unresolved")
    before = first.catalog.get_file(root, path.name)
    edited = {**copy.deepcopy(before["metadata"]), "notes": "New"}

    assert first.catalog.replace_file_metadata(root, path.name, edited)

    assert second.catalog.get_file(root, path.name)["status"] == "resolved"


@pytest.mark.parametrize("replacement", ["Another note", {"nested": [1, True]}])
def test_intervening_metadata_edit_rejects_stale_snapshot(registries, replacement):
    first, second = registries
    root = first.directory
    path, before = _register(first.catalog, root)
    changed = {**copy.deepcopy(before["metadata"]), "notes": replacement}
    assert second.replace_file_metadata(root, path.name, changed)
    latest = second.catalog.get_file(root, path.name)
    document = second.catalog.get_document(latest["document_id"])
    digest = hashlib.sha256(path.read_bytes()).hexdigest()

    attempted = {**copy.deepcopy(before["metadata"]), "title": ["Stale title"]}
    assert not first.replace_file_metadata_if_unchanged(
        root, path.name, attempted, before
    )

    assert first.catalog.get_file(root, path.name) == latest
    assert first.catalog.get_document(latest["document_id"]) == document
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_metadata_comparison_distinguishes_boolean_from_number(registries):
    first, second = registries
    root = first.directory
    path, before = _register(
        first.catalog, root, metadata={"bibcode": "B1", "notes": {"flag": 1}}
    )
    changed = {**copy.deepcopy(before["metadata"]), "notes": {"flag": True}}
    assert second.replace_file_metadata(root, path.name, changed)

    assert not first.replace_file_metadata_if_unchanged(
        root, path.name, before["metadata"], before
    )
    assert first.catalog.get_file(root, path.name)["metadata"] == changed


def test_snapshot_cannot_select_same_filename_in_another_root(registries):
    first, second = registries
    root = first.directory
    path, before = _register(first.catalog, root)
    other_root = root.parent / "other"
    other_path, other = _register(
        first.catalog,
        other_root,
        content=b"%PDF-1.4 other root",
        metadata={"bibcode": "B2", "notes": "Other"},
    )

    assert not first.replace_file_metadata_if_unchanged(
        other_root, other_path.name, {"notes": "Wrong root"}, before
    )
    assert second.catalog.get_file(root, path.name) == before
    assert second.catalog.get_file(other_root, other_path.name) == other


@pytest.mark.parametrize("change", ["delete", "move", "missing", "replace"])
def test_removed_or_replaced_location_rejects_stale_snapshot(registries, change):
    first, second = registries
    root = first.directory
    path, before = _register(first.catalog, root)
    if change in {"delete", "replace"}:
        assert second.catalog.delete_locations(root, [before["location_id"]]) == 1
        if change == "replace":
            _register(
                second.catalog,
                root,
                content=b"%PDF-1.4 replacement file",
                metadata={"bibcode": "B2", "notes": "Replacement"},
            )
    elif change == "move":
        assert second.catalog.move_file(root, path.name, "moved.pdf")
    else:
        path.unlink()
        assert second.catalog.mark_location_missing(root, path.name)
    latest = second.catalog.query_files(root=root, include_missing=True)
    digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None

    assert not first.replace_file_metadata_if_unchanged(
        root, path.name, {"notes": "Stale"}, before
    )
    assert second.catalog.query_files(root=root, include_missing=True) == latest
    if digest is not None:
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_changed_catalog_digest_rejects_stale_snapshot(registries):
    first, second = registries
    root = first.directory
    path, before = _register(first.catalog, root)
    _, latest = _register(second.catalog, root, content=b"%PDF-1.4 revised file")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()

    assert not first.replace_file_metadata_if_unchanged(
        root, path.name, {"notes": "Stale"}, before
    )
    assert first.catalog.get_file(root, path.name) == latest
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_editing_stale_metadata_does_not_restore_identity(registries):
    first, second = registries
    root = first.directory
    path, _ = _register(first.catalog, root)
    _, stale = _register(second.catalog, root, content=b"%PDF-1.4 revised file")
    assert stale["metadata"][METADATA_STALE_KEY]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    edited = {
        **copy.deepcopy(stale["metadata"]),
        METADATA_STALE_KEY: False,
        "notes": "Edit",
    }

    assert first.replace_file_metadata_if_unchanged(root, path.name, edited, stale)

    after = second.catalog.get_file(root, path.name)
    assert after["metadata"][METADATA_STALE_KEY]
    assert after["metadata"]["notes"] == "Edit"
    assert after["document_id"] is None
    assert after["status"] == "unresolved"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("bibcode", ["B2", ""])
def test_success_preserves_document_relinking_semantics(registries, bibcode):
    first, second = registries
    root = first.directory
    path, before = _register(
        first.catalog, root, metadata={"bibcode": "B1", "title": ["Title"]}
    )
    original_document = second.catalog.get_document(before["document_id"])
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    edited = {**copy.deepcopy(before["metadata"]), "bibcode": bibcode}

    assert first.replace_file_metadata_if_unchanged(root, path.name, edited, before)

    after = second.catalog.get_file(root, path.name)
    assert after["attachment_id"] == before["attachment_id"]
    assert after["document_id"] != before["document_id"]
    if bibcode:
        assert after["status"] == "resolved"
        assert second.catalog.get_document(after["document_id"])["bibcode"] == bibcode
    else:
        assert after["status"] == "unresolved"
        assert after["document_id"] is None
    assert second.catalog.get_document(before["document_id"]) == original_document
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("change", ["status", "document", "file_facts", "version"])
def test_intervening_catalog_facts_reject_stale_snapshot(registries, change):
    first, second = registries
    root = first.directory
    path, before = _register(first.catalog, root)
    connection = second.catalog._connection
    with connection:
        if change == "status":
            connection.execute(
                "UPDATE attachments SET status = ? WHERE id = ?",
                ("unresolved", before["attachment_id"]),
            )
        elif change == "document":
            document_id = second.catalog._ensure_document({"bibcode": "OTHER"})
            connection.execute(
                "UPDATE attachments SET document_id = ? WHERE id = ?",
                (document_id, before["attachment_id"]),
            )
        elif change == "file_facts":
            connection.execute(
                "UPDATE file_locations SET mtime_ns = mtime_ns + 1 WHERE id = ?",
                (before["location_id"],),
            )
        else:
            version_id = connection.execute(
                "INSERT INTO content_versions "
                "(attachment_id, sha256, size_bytes, observed_at) VALUES (?, ?, ?, ?)",
                (
                    before["attachment_id"],
                    before["sha256"],
                    before["size_bytes"],
                    "now",
                ),
            ).lastrowid
            connection.execute(
                "UPDATE file_locations SET current_version_id = ? WHERE id = ?",
                (version_id, before["location_id"]),
            )
    latest = second.catalog.get_file(root, path.name)
    assert latest["metadata"] == before["metadata"]

    assert not first.replace_file_metadata_if_unchanged(
        root, path.name, {"notes": "Stale"}, before
    )
    assert first.catalog.get_file(root, path.name) == latest


def test_shared_attachment_keeps_existing_metadata_behavior(registries):
    first, second = registries
    root = first.directory
    path, before = _register(first.catalog, root)
    other_root = root.parent / "shared"
    other_path, other = _register(first.catalog, other_root)
    assert other["attachment_id"] == before["attachment_id"]
    before = first.catalog.get_file(root, path.name)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    edited = {**copy.deepcopy(before["metadata"]), "notes": "Shared edit"}

    assert first.replace_file_metadata_if_unchanged(root, path.name, edited, before)

    assert second.catalog.get_file(other_root, other_path.name)["metadata"] == edited
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    assert hashlib.sha256(other_path.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize(
    "field", ["root", "location_id", "document_id", "sha256", "metadata"]
)
def test_incomplete_snapshot_is_rejected(registries, field):
    first, second = registries
    root = first.directory
    path, before = _register(first.catalog, root)
    incomplete = {key: value for key, value in before.items() if key != field}

    assert not first.replace_file_metadata_if_unchanged(
        root, path.name, {"notes": "Stale"}, incomplete
    )
    assert second.catalog.get_file(root, path.name) == before


def test_nested_transaction_is_rejected_without_committing_caller_work(registries):
    first, second = registries
    root = first.directory
    path, before = _register(first.catalog, root)
    connection = first.catalog._connection
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(
        "INSERT INTO catalog_meta (key, value) VALUES (?, ?)",
        ("test-marker", "pending"),
    )

    with pytest.raises(RuntimeError, match="transaction"):
        first.replace_file_metadata_if_unchanged(
            root, path.name, {"notes": "Edit"}, before
        )

    assert connection.in_transaction
    assert first.catalog.get_meta("test-marker") == "pending"
    assert second.catalog.get_meta("test-marker") is None
    connection.rollback()
    assert second.catalog.get_file(root, path.name) == before


@pytest.mark.parametrize("new_document", [False, True])
def test_failure_after_document_update_rolls_back_all_changes(
    registries, monkeypatch, new_document
):
    first, second = registries
    root = first.directory
    path, before = _register(first.catalog, root)
    document = second.catalog.get_document(before["document_id"])
    digest = hashlib.sha256(path.read_bytes()).hexdigest()

    def fail_attachment_update(*args, **kwargs):
        raise RuntimeError("simulated attachment update failure")

    monkeypatch.setattr(first.catalog, "_update_attachment", fail_attachment_update)
    edited = {**copy.deepcopy(before["metadata"]), "notes": "Edit"}
    if new_document:
        edited["bibcode"] = "NEW-DOCUMENT"
    with pytest.raises(RuntimeError, match="simulated attachment"):
        first.replace_file_metadata_if_unchanged(root, path.name, edited, before)

    assert not first.catalog._connection.in_transaction
    assert second.catalog.get_file(root, path.name) == before
    assert second.catalog.get_document(before["document_id"]) == document
    assert (
        second.catalog._connection.execute(
            "SELECT COUNT(*) FROM documents WHERE bibcode = ?", ("NEW-DOCUMENT",)
        ).fetchone()[0]
        == 0
    )
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    assert second.catalog.update_file_status(root, path.name, "unresolved")


def test_managed_reference_fields_keep_existing_protection(registries):
    first, second = registries
    root = first.directory
    owned = {REFERENCE_METADATA_KEY, "bibcode", *REFERENCE_FIELD_MAP.values()}
    path, before = _register(
        first.catalog,
        root,
        metadata={
            "bibcode": "B1",
            "title": ["Managed title"],
            REFERENCE_METADATA_KEY: {"key": "reference"},
            LOCKED_METADATA_FIELDS_KEY: sorted(owned),
        },
    )
    changed = {**copy.deepcopy(before["metadata"]), "title": ["Not allowed"]}
    with pytest.raises(ValueError, match="Local References"):
        first.replace_file_metadata_if_unchanged(root, path.name, changed, before)
    assert second.catalog.get_file(root, path.name) == before
    allowed = {**copy.deepcopy(before["metadata"]), "notes": "Allowed"}
    assert first.replace_file_metadata_if_unchanged(root, path.name, allowed, before)
    assert second.catalog.get_file(root, path.name)["metadata"] == allowed


def test_write_lock_is_held_during_snapshot_read(registries, monkeypatch):
    first, second = registries
    root = first.directory
    path, before = _register(first.catalog, root)
    second.catalog._connection.execute("PRAGMA busy_timeout = 0")
    original_get_file = first.catalog.get_file
    original_update = first.catalog._update_attachment
    checked = []

    def assert_locked(stage):
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            second.catalog._connection.execute("BEGIN IMMEDIATE")
        checked.append(stage)

    def check_lock_on_read(*args, **kwargs):
        assert_locked("read")
        return original_get_file(*args, **kwargs)

    def check_lock_on_update(*args, **kwargs):
        assert_locked("update")
        return original_update(*args, **kwargs)

    monkeypatch.setattr(first.catalog, "get_file", check_lock_on_read)
    monkeypatch.setattr(first.catalog, "_update_attachment", check_lock_on_update)
    edited = {**copy.deepcopy(before["metadata"]), "notes": "Edit"}
    assert first.replace_file_metadata_if_unchanged(root, path.name, edited, before)
    assert checked == ["read", "update"]
    assert second.catalog.get_file(root, path.name)["metadata"] == edited


def test_waiting_writer_sees_intervening_commit_before_comparison(registries):
    first, second = registries
    root = first.directory
    path, before = _register(first.catalog, root)
    changed = {**copy.deepcopy(before["metadata"]), "notes": "Concurrent edit"}
    writer_ready = threading.Event()
    compare_started = threading.Event()
    errors = []

    def writer():
        try:
            with sqlite3.connect(first.catalog.path, timeout=2) as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "UPDATE attachments SET metadata_json = ? WHERE id = ?",
                    (json.dumps(changed), before["attachment_id"]),
                )
                writer_ready.set()
                if not compare_started.wait(2):
                    raise TimeoutError("Comparison did not start")
        except (sqlite3.Error, TimeoutError) as exc:
            errors.append(exc)
            writer_ready.set()

    def trace(statement: str):
        if statement == "BEGIN IMMEDIATE":
            compare_started.set()

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        assert writer_ready.wait(2)
        assert not errors
        first.catalog._connection.set_trace_callback(trace)
        assert not first.replace_file_metadata_if_unchanged(
            root, path.name, {"notes": "Stale"}, before
        )
    finally:
        compare_started.set()
        first.catalog._connection.set_trace_callback(None)
        thread.join(timeout=3)
    assert not thread.is_alive()
    assert not errors
    assert second.catalog.get_file(root, path.name)["metadata"] == changed


def test_document_guard_rejects_concurrent_document_update(registries):
    first, second = registries
    root = first.directory
    path, before = _register(
        first.catalog,
        root,
        metadata={"bibcode": "B1", "title": ["Original"], "notes": "Original"},
    )
    document = first.catalog.get_document(before["document_id"])
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    second.catalog.upsert_ads_document(
        {"bibcode": "B1", "title": ["Concurrent document edit"]}, source="test"
    )
    updated_document = second.catalog.get_document(before["document_id"])
    assert updated_document != document
    assert second.catalog.get_file(root, path.name) == before
    edited = {**copy.deepcopy(before["metadata"]), "notes": "Attachment edit"}

    assert not first.replace_file_metadata_if_unchanged(
        root, path.name, edited, before, expected_document=document
    )

    assert second.catalog.get_file(root, path.name) == before
    assert second.catalog.get_document(before["document_id"]) == updated_document
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_document_guard_accepts_unchanged_snapshot(registries):
    first, second = registries
    root = first.directory
    path, before = _register(first.catalog, root)
    document = first.catalog.get_document(before["document_id"])
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    edited = {**copy.deepcopy(before["metadata"]), "notes": "Attachment edit"}

    assert first.replace_file_metadata_if_unchanged(
        root, path.name, edited, before, expected_document=document
    )

    after = second.catalog.get_file(root, path.name)
    assert after["metadata"] == edited
    assert after["document_id"] == document["id"]
    assert second.catalog.get_document(after["document_id"])["metadata"]["notes"] == (
        "Attachment edit"
    )
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_omitted_document_guard_preserves_newer_unchanged_fields(registries):
    first, second = registries
    root = first.directory
    path, before = _register(
        first.catalog, root, metadata={"bibcode": "B1", "title": ["Original"]}
    )
    second.catalog.upsert_ads_document(
        {"bibcode": "B1", "title": ["Concurrent document edit"]}, source="test"
    )
    edited = {**copy.deepcopy(before["metadata"]), "notes": "Attachment edit"}

    assert first.replace_file_metadata_if_unchanged(root, path.name, edited, before)

    assert second.catalog.get_document(before["document_id"])["metadata"]["title"] == [
        "Concurrent document edit"
    ]


@pytest.mark.parametrize("explicit_fields", [False, True])
def test_shared_document_preserves_newer_title_when_other_attachment_edits_notes(
    registries, explicit_fields
):
    first, second = registries
    root_a = first.directory
    root_b = root_a.parent / "second"
    metadata = {"bibcode": "B1", "title": ["Original"], "notes": "Original note"}
    path_a, _ = _register(first.catalog, root_a, metadata=metadata)
    path_b, record_b = _register(
        first.catalog,
        root_b,
        content=b"%PDF-1.4 different attachment",
        metadata=metadata,
    )
    record_a = first.catalog.get_file(root_a, path_a.name)
    assert record_a["attachment_id"] != record_b["attachment_id"]
    assert record_a["document_id"] == record_b["document_id"]
    hashes = [
        hashlib.sha256(path.read_bytes()).hexdigest() for path in (path_a, path_b)
    ]

    edited_b = {**copy.deepcopy(record_b["metadata"]), "title": ["New title"]}
    assert second.replace_file_metadata_if_unchanged(
        root_b, path_b.name, edited_b, record_b
    )
    document = first.catalog.get_document(record_a["document_id"])
    assert document["metadata"]["title"] == ["New title"]
    edited_a = {**copy.deepcopy(record_a["metadata"]), "notes": "New note"}
    options = {"changed_fields": {"notes"}} if explicit_fields else {}

    assert first.replace_file_metadata_if_unchanged(
        root_a, path_a.name, edited_a, record_a, expected_document=document, **options
    )

    result = second.catalog.get_document(record_a["document_id"])
    assert result["metadata"]["title"] == ["New title"]
    assert result["metadata"]["notes"] == "New note"
    assert second.catalog.get_file(root_a, path_a.name)["metadata"] == edited_a
    assert second.catalog.get_file(root_b, path_b.name)["metadata"] == edited_b
    assert [
        hashlib.sha256(path.read_bytes()).hexdigest() for path in (path_a, path_b)
    ] == hashes


@pytest.mark.parametrize("explicit_fields", [None, {"notes"}, {"bibcode", "notes"}])
def test_changed_identifier_uses_full_metadata_to_find_existing_document(
    registries, explicit_fields
):
    first, second = registries
    root_a = first.directory
    root_b = root_a.parent / "second"
    path_a, record_a = _register(
        first.catalog,
        root_a,
        metadata={"bibcode": "B1", "title": ["Old attachment title"], "notes": "Old"},
    )
    path_b, record_b = _register(
        first.catalog,
        root_b,
        content=b"%PDF-1.4 existing target document",
        metadata={"bibcode": "B2", "title": ["Target title"], "notes": "Target note"},
    )
    original = first.catalog.get_document(record_a["document_id"])
    target = first.catalog.get_document(record_b["document_id"])
    hashes = [
        hashlib.sha256(path.read_bytes()).hexdigest() for path in (path_a, path_b)
    ]
    edited = {**copy.deepcopy(record_a["metadata"]), "bibcode": "B2", "notes": "New"}

    assert first.replace_file_metadata_if_unchanged(
        root_a,
        path_a.name,
        edited,
        record_a,
        expected_document=original,
        changed_fields=explicit_fields,
    )

    after = second.catalog.get_file(root_a, path_a.name)
    assert after["document_id"] == record_b["document_id"]
    assert after["metadata"] == edited
    assert second.catalog.get_document(record_a["document_id"]) == original
    updated_target = second.catalog.get_document(record_b["document_id"])
    assert updated_target["metadata"]["title"] == target["metadata"]["title"]
    assert updated_target["metadata"]["notes"] == "New"
    assert second.catalog.get_file(root_b, path_b.name) == record_b
    assert [
        hashlib.sha256(path.read_bytes()).hexdigest() for path in (path_a, path_b)
    ] == hashes


def test_explicit_fields_override_inference_for_existing_documents(registries):
    first, second = registries
    root = first.directory
    path, record = _register(
        first.catalog,
        root,
        metadata={"bibcode": "B1", "title": ["Original"], "notes": "Original note"},
    )
    second.catalog.upsert_ads_document(
        {"bibcode": "B1", "title": ["Newer"], "notes": "Newer note"}, source="test"
    )
    document = first.catalog.get_document(record["document_id"])
    edited = {**copy.deepcopy(record["metadata"]), "notes": "Attachment only"}
    digest = hashlib.sha256(path.read_bytes()).hexdigest()

    assert first.replace_file_metadata_if_unchanged(
        root,
        path.name,
        edited,
        record,
        expected_document=document,
        changed_fields={"title"},
    )

    updated = second.catalog.get_document(record["document_id"])["metadata"]
    assert updated["title"] == ["Original"]
    assert updated["notes"] == "Newer note"
    assert second.catalog.get_file(root, path.name)["metadata"] == edited
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_explicit_empty_fields_leave_existing_document_metadata_unchanged(registries):
    first, second = registries
    root = first.directory
    path, record = _register(
        first.catalog, root, metadata={"bibcode": "B1", "title": ["Original"]}
    )
    second.catalog.upsert_ads_document(
        {"bibcode": "B1", "title": ["Newer"]}, source="test"
    )
    document = first.catalog.get_document(record["document_id"])
    edited = {**copy.deepcopy(record["metadata"]), "notes": "Attachment only"}

    assert first.replace_file_metadata_if_unchanged(
        root,
        path.name,
        edited,
        record,
        expected_document=document,
        changed_fields=set(),
    )

    assert second.catalog.get_document(record["document_id"]) == document
    assert second.catalog.get_file(root, path.name)["metadata"] == edited


def test_new_document_receives_full_metadata_with_explicit_fields(registries):
    first, second = registries
    root = first.directory
    path, record = _register(
        first.catalog,
        root,
        metadata={"bibcode": "B1", "title": ["Title"], "notes": "Keep note"},
    )
    original = first.catalog.get_document(record["document_id"])
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    edited = {**copy.deepcopy(record["metadata"]), "bibcode": "B3"}

    assert first.replace_file_metadata_if_unchanged(
        root,
        path.name,
        edited,
        record,
        expected_document=original,
        changed_fields={"bibcode"},
    )

    after = second.catalog.get_file(root, path.name)
    assert after["document_id"] != record["document_id"]
    assert second.catalog.get_document(after["document_id"])["metadata"] == edited
    assert second.catalog.get_document(record["document_id"]) == original
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_inferred_fields_distinguish_boolean_from_number(registries):
    first, second = registries
    root = first.directory
    path, record = _register(first.catalog, root, metadata={"bibcode": "B1", "flag": 1})
    edited = {**copy.deepcopy(record["metadata"]), "flag": True}

    assert first.replace_file_metadata_if_unchanged(root, path.name, edited, record)

    document = second.catalog.get_document(record["document_id"])
    assert document["metadata"]["flag"] is True


def test_conditional_merge_preserves_document_locks(registries):
    first, second = registries
    root_a = first.directory
    root_b = root_a.parent / "second"
    metadata = {"bibcode": "B1", "title": ["Original"], "notes": "Original"}
    path_a, record_a = _register(first.catalog, root_a, metadata=metadata)
    path_b, record_b = _register(
        first.catalog, root_b, content=b"%PDF-1.4 second attachment", metadata=metadata
    )
    edited_b = {
        **copy.deepcopy(record_b["metadata"]),
        "title": ["Locked title"],
        LOCKED_METADATA_FIELDS_KEY: ["title"],
    }
    assert second.replace_file_metadata_if_unchanged(
        root_b, path_b.name, edited_b, record_b
    )
    document = first.catalog.get_document(record_a["document_id"])
    assert document["metadata"]["title"] == ["Locked title"]
    assert document["metadata"][LOCKED_METADATA_FIELDS_KEY] == ["title"]
    edited_a = {**copy.deepcopy(record_a["metadata"]), "title": ["New"], "notes": "New"}

    assert first.replace_file_metadata_if_unchanged(
        root_a,
        path_a.name,
        edited_a,
        record_a,
        expected_document=document,
        changed_fields={"title", "notes"},
    )

    updated = second.catalog.get_document(record_a["document_id"])["metadata"]
    assert updated["title"] == ["Locked title"]
    assert updated["notes"] == "New"
    assert updated[LOCKED_METADATA_FIELDS_KEY] == ["title"]
    assert second.catalog.get_file(root_a, path_a.name)["metadata"] == edited_a


def test_unlisted_bibcode_does_not_change_existing_target_identity(registries):
    first, second = registries
    root_a = first.directory
    root_b = root_a.parent / "second"
    path_a, record_a = _register(
        first.catalog, root_a, metadata={"bibcode": "B1", "title": ["Old"]}
    )
    path_b, record_b = _register(
        first.catalog,
        root_b,
        content=b"%PDF-1.4 doi-only target",
        metadata={
            "doi": ["10.1234/target"],
            "title": ["Target"],
            LOCKED_METADATA_FIELDS_KEY: ["title"],
        },
    )
    target = second.catalog.get_document(record_b["document_id"])
    assert target["bibcode"] is None
    edited = {
        **copy.deepcopy(record_a["metadata"]),
        "bibcode": "B-new",
        "doi": ["10.1234/target"],
    }
    hashes = [
        hashlib.sha256(path.read_bytes()).hexdigest() for path in (path_a, path_b)
    ]

    assert first.replace_file_metadata_if_unchanged(
        root_a, path_a.name, edited, record_a, changed_fields=set()
    )

    after = second.catalog.get_file(root_a, path_a.name)
    assert after["document_id"] == record_b["document_id"]
    assert after["metadata"] == edited
    assert second.catalog.get_document(record_b["document_id"]) == target
    assert [
        hashlib.sha256(path.read_bytes()).hexdigest() for path in (path_a, path_b)
    ] == hashes


def test_nonconditional_replacement_retains_full_document_merge(registries):
    first, second = registries
    root = first.directory
    path, record = _register(
        first.catalog, root, metadata={"bibcode": "B1", "title": ["Original"]}
    )
    second.catalog.upsert_ads_document(
        {"bibcode": "B1", "title": ["Newer"]}, source="test"
    )
    edited = {**copy.deepcopy(record["metadata"]), "notes": "Edit"}

    assert first.replace_file_metadata(root, path.name, edited)

    document = second.catalog.get_document(record["document_id"])
    assert document["metadata"]["title"] == ["Original"]
    assert document["metadata"]["notes"] == "Edit"


@pytest.mark.parametrize("changed_fields", ["title", ["title", 3]])
def test_changed_fields_rejects_invalid_names_without_writing(
    registries, changed_fields
):
    first, second = registries
    root = first.directory
    path, record = _register(first.catalog, root)
    document = first.catalog.get_document(record["document_id"])

    with pytest.raises(TypeError, match="changed_fields"):
        first.replace_file_metadata_if_unchanged(
            root, path.name, record["metadata"], record, changed_fields=changed_fields
        )

    assert not first.catalog._connection.in_transaction
    assert second.catalog.get_file(root, path.name) == record
    assert second.catalog.get_document(record["document_id"]) == document


@pytest.mark.parametrize("linked", [False, True])
def test_document_guard_requires_none_only_without_linked_document(registries, linked):
    first, second = registries
    root = first.directory
    metadata = {"bibcode": "B1"} if linked else {"notes": "Original"}
    path, before = _register(first.catalog, root, metadata=metadata)
    edited = {**copy.deepcopy(before["metadata"]), "notes": "Edit"}

    result = first.replace_file_metadata_if_unchanged(
        root, path.name, edited, before, expected_document=None
    )

    assert result is not linked
    after = second.catalog.get_file(root, path.name)
    assert after["metadata"] == (before["metadata"] if linked else edited)


def test_document_guard_rejects_snapshot_when_no_document_is_linked(registries):
    first, second = registries
    root = first.directory
    path, before = _register(first.catalog, root, metadata={"notes": "Original"})
    assert before["document_id"] is None
    document = {"id": 1, "bibcode": "B1", "metadata": {}}

    assert not first.catalog.replace_file_metadata_if_unchanged(
        root, path.name, {"notes": "Edit"}, before, expected_document=document
    )
    assert second.catalog.get_file(root, path.name) == before


@pytest.mark.parametrize(("field", "value"), [("id", -1), ("bibcode", "B2")])
def test_document_guard_rejects_wrong_document_identity(registries, field, value):
    first, second = registries
    root = first.directory
    path, before = _register(first.catalog, root)
    document = first.catalog.get_document(before["document_id"])
    expected = {**document, field: value}

    assert not first.catalog.replace_file_metadata_if_unchanged(
        root, path.name, {"notes": "Edit"}, before, expected_document=expected
    )
    assert second.catalog.get_file(root, path.name) == before
    assert second.catalog.get_document(before["document_id"]) == document


@pytest.mark.parametrize("field", ["id", "bibcode", "metadata"])
def test_document_guard_rejects_incomplete_snapshot(registries, field):
    first, second = registries
    root = first.directory
    path, before = _register(first.catalog, root)
    document = first.catalog.get_document(before["document_id"])
    incomplete = {key: value for key, value in document.items() if key != field}

    assert not first.replace_file_metadata_if_unchanged(
        root, path.name, {"notes": "Stale"}, before, expected_document=incomplete
    )
    assert second.catalog.get_file(root, path.name) == before
    assert second.catalog.get_document(before["document_id"]) == document


def test_document_guard_distinguishes_boolean_from_number(registries):
    first, second = registries
    root = first.directory
    path, before = _register(first.catalog, root, metadata={"bibcode": "B1", "flag": 1})
    document = first.catalog.get_document(before["document_id"])
    second.catalog.upsert_ads_document({"bibcode": "B1", "flag": True}, source="test")
    updated_document = second.catalog.get_document(before["document_id"])

    assert not first.replace_file_metadata_if_unchanged(
        root, path.name, {"notes": "Stale"}, before, expected_document=document
    )
    assert second.catalog.get_file(root, path.name) == before
    assert second.catalog.get_document(before["document_id"]) == updated_document
    assert updated_document["metadata"]["flag"] is True


def test_document_guard_reads_while_holding_write_lock(registries, monkeypatch):
    first, second = registries
    root = first.directory
    path, before = _register(first.catalog, root)
    document = first.catalog.get_document(before["document_id"])
    second.catalog._connection.execute("PRAGMA busy_timeout = 0")
    original_get_document = first.catalog.get_document
    checked = []

    def check_document_lock(*args, **kwargs):
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            second.catalog._connection.execute("BEGIN IMMEDIATE")
        checked.append(True)
        return original_get_document(*args, **kwargs)

    monkeypatch.setattr(first.catalog, "get_document", check_document_lock)
    edited = {**copy.deepcopy(before["metadata"]), "notes": "Edit"}
    assert first.replace_file_metadata_if_unchanged(
        root, path.name, edited, before, expected_document=document
    )
    assert checked == [True]
    assert second.catalog.get_file(root, path.name)["metadata"] == edited


def test_document_guard_errors_release_the_write_lock(registries, monkeypatch):
    first, second = registries
    root = first.directory
    path, before = _register(first.catalog, root)
    document = first.catalog.get_document(before["document_id"])

    def fail_document_read(*args, **kwargs):
        raise ValueError("document read failed")

    monkeypatch.setattr(first.catalog, "get_document", fail_document_read)
    with pytest.raises(ValueError, match="document read failed"):
        first.replace_file_metadata_if_unchanged(
            root, path.name, {"notes": "Edit"}, before, expected_document=document
        )

    assert not first.catalog._connection.in_transaction
    assert second.catalog.get_file(root, path.name) == before
    assert second.catalog.get_document(before["document_id"]) == document
    assert second.catalog.update_file_status(root, path.name, "unresolved")
