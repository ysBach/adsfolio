"""Recover changed PDF identities from isolated cached ADS records."""

import socket
from contextlib import closing
from copy import deepcopy
from io import BytesIO

import fitz
import pytest
from pdf_identity_fixtures import write_pdf
from pypdf import PdfReader, PdfWriter

from adsfolio.pdf.files import sha256_file
from adsfolio.pdf.identifier import PdfIdentifier
from adsfolio.pdf.identify_settings import IdentifySettings
from adsfolio.pdf.identity import IDENTITY_KEY, IdentityValidationError
from adsfolio.pdf.readiness import (
    METADATA_SHA256_KEY,
    METADATA_STALE_KEY,
    PREVIOUS_RESOLUTION_KEY,
    WATCH_PENDING_KEY,
)
from adsfolio.pdf.registry import open_registry_state
from adsfolio.pdf.rename_settings import RenameSettings
from adsfolio.pdf.renamer import PdfRenamer, recover_stale_record
from adsfolio.pdf.watch_session import ArrivalIdentity, WatchEvent, WatchState
from adsfolio.pdf.watcher import PdfWatcher

DOI = "10.1234/recovery-example"
DOCUMENT = {
    "bibcode": "2024ApJ...900..100S",
    "doi": [DOI],
    "title": ["An Example Paper"],
    "author": ["Example, A."],
    "year": "2024",
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("This test must not contact a network")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    path = root / "paper.pdf"
    write_pdf(path, doi=DOI)
    with closing(open_registry_state(root)) as state:
        state.registry.upsert_ads_document(DOCUMENT, source="test")
        metadata = PdfIdentifier(None).build_record(DOCUMENT, path)
        metadata["notes"] = "Keep my notes"
        metadata["_adsfolio_locked_fields"] = ["title"]
        metadata["user_history"] = [{"note": "Keep my history"}]
        state.upsert_file(path.name, metadata)
        before = state.registry.catalog.get_file(root, path.name)
        path.write_bytes(path.read_bytes() + b"\n% A saved PDF revision\n")
        yield root, path, state, before


def _scan(state, root, path):
    state.registry.catalog.discover_root(root)
    record = state.registry.catalog.get_file(root, path.name)
    assert record["metadata"][METADATA_STALE_KEY]
    assert record["sha256"] == sha256_file(path)
    return record


def _database_snapshot(state):
    return tuple(state.registry.catalog._connection.iterdump())


def _event(root, path):
    stat = path.stat()
    return WatchEvent(
        root=root,
        session_id="stale-recovery-test",
        identity=ArrivalIdentity(stat.st_dev, stat.st_ino, None, 0),
        path=path,
        state=WatchState.DETECTED,
    )


def test_rename_preview_and_apply_recover_verified_stale_pdf(library):
    root, path, state, before = library
    stale = _scan(state, root, path)
    catalog = state.registry.catalog
    prior_document = catalog.get_document(before["document_id"])
    prior_versions = catalog.content_versions(before["attachment_id"])
    before_db = _database_snapshot(state)
    content = path.read_bytes()
    target = root / f"{DOCUMENT['bibcode']}.pdf"

    renamer = PdfRenamer()
    expected = [(path.name, target.name)]
    assert renamer.batch_rename(state, template="{bibcode}", dry_run=True) == expected
    assert _database_snapshot(state) == before_db
    assert path.read_bytes() == content and not target.exists()
    assert catalog.get_file(root, path.name) == stale

    assert renamer.batch_rename(state, template="{bibcode}", dry_run=False) == expected
    saved = catalog.get_file(root, target.name)
    assert not path.exists() and target.read_bytes() == content
    assert saved["document_id"] == before["document_id"]
    assert saved["status"] == "resolved"
    assert saved["sha256"] == sha256_file(target)
    assert saved["metadata"][METADATA_SHA256_KEY] == saved["sha256"]
    assert saved["metadata"][IDENTITY_KEY]["status"] == "verified"
    assert not saved["metadata"].get(METADATA_STALE_KEY)
    assert not saved["metadata"].get(WATCH_PENDING_KEY)
    assert PREVIOUS_RESOLUTION_KEY not in saved["metadata"]
    assert saved["metadata"]["notes"] == "Keep my notes"
    assert saved["metadata"]["_adsfolio_locked_fields"] == ["title"]
    assert saved["metadata"]["user_history"] == [{"note": "Keep my history"}]
    history = saved["metadata"]["_adsfolio_stale_recovery_history"][-1]
    assert history["identity_proof"] == before["metadata"][IDENTITY_KEY]
    assert history["previous_resolution"] == stale["metadata"][PREVIOUS_RESOLUTION_KEY]
    assert catalog.content_versions(before["attachment_id"]) == prior_versions
    assert catalog.get_document(before["document_id"]) == prior_document


def test_rename_recovers_selected_pdf_before_catalog_scan(library):
    root, path, state, before = library
    catalog = state.registry.catalog
    assert catalog.get_file(root, path.name) == before
    assert before["status"] == "resolved"
    assert before["sha256"] != sha256_file(path)
    assert not before["metadata"].get(METADATA_STALE_KEY)

    other = root / "unselected.pdf"
    other_document = {
        **DOCUMENT,
        "bibcode": "2025ApJ...901..101B",
        "doi": ["10.1234/unselected"],
    }
    write_pdf(other, doi=other_document["doi"][0])
    state.registry.upsert_ads_document(other_document, source="test")
    state.upsert_file(
        other.name, PdfIdentifier(None).build_record(other_document, other)
    )
    other_before = catalog.get_file(root, other.name)
    other.write_bytes(other.read_bytes() + b"\n% Another saved revision\n")
    other_bytes = other.read_bytes()
    original = path.read_bytes()
    before_db = _database_snapshot(state)
    old_versions = catalog.content_versions(before["attachment_id"])
    target = root / f"{DOCUMENT['bibcode']}.pdf"
    expected = [(path.name, target.name)]
    renamer = PdfRenamer()

    assert (
        renamer.batch_rename(
            state, template="{bibcode}", dry_run=True, current_filenames=[path.name]
        )
        == expected
    )
    assert _database_snapshot(state) == before_db
    assert path.read_bytes() == original and not target.exists()
    assert other.read_bytes() == other_bytes

    assert (
        renamer.batch_rename(
            state, template="{bibcode}", dry_run=False, current_filenames=[path.name]
        )
        == expected
    )
    saved = catalog.get_file(root, target.name)
    assert not path.exists() and target.read_bytes() == original
    assert saved["attachment_id"] == before["attachment_id"]
    assert saved["document_id"] == before["document_id"]
    assert saved["metadata"][IDENTITY_KEY]["status"] == "verified"
    assert saved["metadata"][METADATA_SHA256_KEY] == sha256_file(target)
    assert saved["metadata"]["notes"] == "Keep my notes"
    assert saved["metadata"]["_adsfolio_locked_fields"] == ["title"]
    assert saved["metadata"]["user_history"] == [{"note": "Keep my history"}]
    versions = catalog.content_versions(before["attachment_id"])
    assert versions[:-1] == old_versions
    assert versions[-1]["sha256"] == sha256_file(target)
    assert catalog.get_file(root, other.name) == other_before
    assert other.read_bytes() == other_bytes


@pytest.mark.parametrize(
    "failure", ["missing_cache", "conflicting_pdf", "no_pdf_proof"]
)
def test_rename_refuses_unscanned_pdf_without_verified_identity(library, failure):
    root, path, state, before = library
    catalog = state.registry.catalog
    if failure == "missing_cache":
        with catalog._connection:
            catalog._connection.execute("DELETE FROM ads_documents")
    elif failure == "conflicting_pdf":
        write_pdf(path, doi="10.1234/different-paper")
    else:
        write_pdf(path)
    assert catalog.get_file(root, path.name) == before
    before_db = _database_snapshot(state)
    content = path.read_bytes()
    renamer = PdfRenamer()
    assert (
        renamer.batch_rename(
            state, template="{bibcode}", dry_run=False, current_filenames=[path.name]
        )
        == []
    )
    reason = renamer.identity_refused[path.name]
    assert {
        "missing_cache": "not cached",
        "conflicting_pdf": "contradicts",
        "no_pdf_proof": "needs review",
    }[failure] in reason
    assert "Update by ID" in reason
    assert _database_snapshot(state) == before_db
    assert path.read_bytes() == content


@pytest.mark.parametrize("scan_first", [False, True])
def test_rename_accepts_corroborated_publisher_article_doi(library, scan_first):
    root, path, state, _ = library
    pdf = fitz.open()
    try:
        page = pdf.new_page()
        page.insert_text((72, 72), f"{DOCUMENT['title'][0]}\nDOI: {DOI}")
        pdf.set_metadata({"title": DOCUMENT["title"][0]})
        writer = PdfWriter(clone_from=PdfReader(BytesIO(pdf.tobytes())))
        writer.add_metadata({"/WPS-ARTICLEDOI": DOI})
        with path.open("wb") as output:
            writer.write(output)
    finally:
        pdf.close()
    if scan_first:
        _scan(state, root, path)
    content = path.read_bytes()
    target = root / f"{DOCUMENT['bibcode']}.pdf"
    renamer = PdfRenamer()
    assert renamer.batch_rename(state, template="{bibcode}", dry_run=True)
    assert renamer.batch_rename(state, template="{bibcode}", dry_run=False)
    saved = state.registry.catalog.get_file(root, target.name)
    assert saved["metadata"][IDENTITY_KEY]["status"] == "verified"
    assert saved["metadata"][IDENTITY_KEY]["authority"] == "corroborated_document_doi"
    assert target.read_bytes() == content


@pytest.mark.parametrize("blocker", ["collision", "missing_field"])
@pytest.mark.parametrize("scan_first", [False, True])
def test_rename_does_not_recover_when_no_rename_can_be_planned(
    library, blocker, scan_first
):
    root, path, state, _ = library
    if scan_first:
        _scan(state, root, path)
    target = root / f"{DOCUMENT['bibcode']}.pdf"
    if blocker == "collision":
        target.write_bytes(b"Existing file")
    before_db = _database_snapshot(state)
    original = path.read_bytes()
    renamer = PdfRenamer()
    template = "{bibcode}" if blocker == "collision" else "{volume}"
    assert renamer.batch_rename(state, template=template, dry_run=False) == []
    assert path.name in renamer.skipped
    assert _database_snapshot(state) == before_db
    assert path.read_bytes() == original
    if blocker == "collision":
        assert target.read_bytes() == b"Existing file"


@pytest.mark.parametrize("change", ["pdf", "metadata"])
@pytest.mark.parametrize("scan_first", [False, True])
def test_rename_refuses_changes_after_recovery_preview(
    library, monkeypatch, change, scan_first
):
    root, path, state, before = library
    tracked = _scan(state, root, path) if scan_first else before
    renamer = PdfRenamer()
    render = renamer.render
    calls = []

    def render_and_change(*args, **kwargs):
        # Rename renders once before the recovery preview and once after it.
        # Each call makes a new change, so a change follows the preview.
        result = render(*args, **kwargs)
        calls.append(None)
        if change == "pdf":
            path.write_bytes(path.read_bytes() + b"\n% concurrent change\n")
        else:
            changed = deepcopy(tracked["metadata"])
            changed["notes"] = f"Concurrent note {len(calls)}"
            state.registry.replace_file_metadata(root, path.name, changed)
        return result

    monkeypatch.setattr(renamer, "render", render_and_change)
    assert renamer.batch_rename(state, template="{bibcode}", dry_run=False) == []
    assert "changed before Rename" in renamer.identity_refused[path.name]
    saved = state.registry.catalog.get_file(root, path.name)
    assert bool(saved["metadata"].get(METADATA_STALE_KEY)) is scan_first
    assert saved["document_id"] == (None if scan_first else before["document_id"])
    assert not (root / f"{DOCUMENT['bibcode']}.pdf").exists()
    if change == "metadata":
        assert saved["metadata"]["notes"] == f"Concurrent note {len(calls)}"


@pytest.mark.parametrize("change", ["pdf", "metadata"])
@pytest.mark.parametrize("scan_first", [False, True])
def test_rename_checks_for_changes_after_recovery_commit(
    library, monkeypatch, change, scan_first
):
    import adsfolio.pdf.renamer as module

    root, path, state, _ = library
    if scan_first:
        _scan(state, root, path)
    recover = module._recover_stale_record

    def recover_and_change(*args, **kwargs):
        result = recover(*args, **kwargs)
        if kwargs["apply"]:
            if change == "pdf":
                path.write_bytes(path.read_bytes() + b"\n% concurrent change\n")
            else:
                current = state.registry.catalog.get_file(root, path.name)
                changed = deepcopy(current["metadata"])
                changed["notes"] = "Concurrent note"
                state.registry.replace_file_metadata(root, path.name, changed)
        return result

    monkeypatch.setattr(module, "_recover_stale_record", recover_and_change)
    renamer = PdfRenamer()
    assert renamer.batch_rename(state, template="{bibcode}", dry_run=False) == []
    assert "changed before Rename" in renamer.identity_refused[path.name]
    assert path.exists()
    assert not (root / f"{DOCUMENT['bibcode']}.pdf").exists()
    if change == "metadata":
        saved = state.registry.catalog.get_file(root, path.name)
        assert saved["metadata"]["notes"] == "Concurrent note"


@pytest.mark.parametrize("scan_first", [False, True])
def test_watcher_recovers_existing_saved_pdf_without_network(
    library, monkeypatch, scan_first
):
    root, path, state, before = library
    if scan_first:
        _scan(state, root, path)
    content = path.read_bytes()
    document = state.registry.catalog.get_document(before["document_id"])
    identifier = PdfIdentifier(None)
    monkeypatch.setattr(
        identifier, "resolve_many", lambda *args, **kwargs: pytest.fail("Identify ran")
    )
    outcome = PdfWatcher(identifier, PdfRenamer()).process_event(
        _event(root, path),
        state,
        RenameSettings(template="{bibcode}"),
        IdentifySettings(),
    )
    target = root / f"{DOCUMENT['bibcode']}.pdf"
    assert outcome.event.state is WatchState.RENAMED
    assert not outcome.annotation_update
    assert outcome.destination == target
    assert target.read_bytes() == content and not path.exists()
    saved = state.registry.catalog.get_file(root, target.name)
    assert saved["attachment_id"] == before["attachment_id"]
    assert saved["metadata"][IDENTITY_KEY]["status"] == "verified"
    assert saved["metadata"][METADATA_SHA256_KEY] == sha256_file(target)
    assert not saved["metadata"].get(METADATA_STALE_KEY)
    assert state.registry.catalog.get_document(before["document_id"]) == document


@pytest.mark.parametrize("scan_first", [False, True])
def test_watcher_preview_does_not_write(library, scan_first):
    root, path, state, _ = library
    if scan_first:
        _scan(state, root, path)
    before_db = _database_snapshot(state)
    before_pdf = path.read_bytes()
    record = PdfWatcher(PdfIdentifier(None), PdfRenamer()).process_new_pdf(
        path, state, scheme="{bibcode}", apply=False
    )
    assert record["planned_filename"] == f"{DOCUMENT['bibcode']}.pdf"
    assert _database_snapshot(state) == before_db
    assert path.read_bytes() == before_pdf
    assert not (root / record["planned_filename"]).exists()


def test_watcher_reports_reason_when_stale_recovery_cannot_verify(library):
    root, path, state, _ = library
    write_pdf(path, doi="10.1234/different-paper")
    stale = _scan(state, root, path)
    outcome = PdfWatcher(PdfIdentifier(None), PdfRenamer()).process_event(
        _event(root, path),
        state,
        RenameSettings(template="{bibcode}"),
        IdentifySettings(),
    )
    assert outcome.event.state is WatchState.FAILED
    assert "contradicts" in outcome.message
    assert "Update by ID" in outcome.message
    assert state.registry.catalog.get_file(root, path.name) == stale


@pytest.mark.parametrize(
    "failure", ["missing_cache", "conflicting_pdf", "no_pdf_proof"]
)
def test_rename_refuses_stale_identity_with_reason(library, failure):
    root, path, state, _ = library
    catalog = state.registry.catalog
    if failure == "missing_cache":
        with catalog._connection:
            catalog._connection.execute("DELETE FROM ads_documents")
    elif failure == "conflicting_pdf":
        write_pdf(path, doi="10.1234/different-paper")
    else:
        write_pdf(path)
    stale = _scan(state, root, path)
    before_db = _database_snapshot(state)
    content = path.read_bytes()

    renamer = PdfRenamer()
    assert renamer.batch_rename(state, template="{bibcode}", dry_run=False) == []
    reason = renamer.identity_refused[path.name]
    assert "Update by ID" in reason
    assert {
        "missing_cache": "not cached",
        "conflicting_pdf": "contradicts",
        "no_pdf_proof": "needs review",
    }[failure] in reason
    assert catalog.get_file(root, path.name) == stale
    assert _database_snapshot(state) == before_db
    assert path.read_bytes() == content


def test_recovery_refuses_another_active_location(library, tmp_path):
    root, path, state, _ = library
    stale = _scan(state, root, path)
    other_root = tmp_path / "other-library"
    other_root.mkdir()
    other = other_root / "other.pdf"
    other.write_bytes(path.read_bytes())
    with closing(open_registry_state(other_root)) as other_state:
        other_state.upsert_file(other.name, {"sha256": sha256_file(other)})
        other_record = other_state.registry.catalog.get_file(other_root, other.name)
        assert other_record["attachment_id"] == stale["attachment_id"]
        before_db = _database_snapshot(state)
        with pytest.raises(IdentityValidationError, match="another active location"):
            recover_stale_record(state, path.name, apply=True)
        assert _database_snapshot(state) == before_db


def test_recovery_preserves_other_attachment_and_shared_document(library, tmp_path):
    root, path, state, original = library
    _scan(state, root, path)
    other_root = tmp_path / "other-library"
    other_root.mkdir()
    other = other_root / "other.pdf"
    write_pdf(other, doi=DOI)
    other.write_bytes(other.read_bytes() + b"\n% A separate edition\n")
    with closing(open_registry_state(other_root)) as other_state:
        metadata = PdfIdentifier(None).build_record(DOCUMENT, other)
        metadata["notes"] = "Another reader's notes"
        other_state.upsert_file(other.name, metadata)
        sibling = other_state.registry.catalog.get_file(other_root, other.name)
        assert sibling["attachment_id"] != original["attachment_id"]
        assert sibling["document_id"] == original["document_id"]
        document = state.registry.catalog.get_document(original["document_id"])
        sibling_bytes = other.read_bytes()
        recovered = recover_stale_record(state, path.name, apply=True)
        assert recovered["status"] == "resolved"
        assert other_state.registry.catalog.get_file(other_root, other.name) == sibling
        assert state.registry.catalog.get_document(original["document_id"]) == document
        assert other.read_bytes() == sibling_bytes


def test_recovery_returns_none_for_current_record(library):
    root, path, state, _ = library
    _scan(state, root, path)
    assert recover_stale_record(state, path.name, apply=True) is not None
    before_db = _database_snapshot(state)
    assert recover_stale_record(state, path.name, apply=True) is None
    assert _database_snapshot(state) == before_db


def test_recovery_rejects_changed_pdf_during_check(library, monkeypatch):
    import adsfolio.pdf.renamer as module

    root, path, state, _ = library
    _scan(state, root, path)
    before_db = _database_snapshot(state)
    verify = module.assert_mutation_allowed

    def change_after_verification(*args, **kwargs):
        result = verify(*args, **kwargs)
        path.write_bytes(path.read_bytes() + b"\n% concurrent change\n")
        return result

    monkeypatch.setattr(module, "assert_mutation_allowed", change_after_verification)
    with pytest.raises(IdentityValidationError, match="PDF changed"):
        recover_stale_record(state, path.name, apply=True)
    assert _database_snapshot(state) == before_db


def test_recovery_rolls_back_if_pdf_changes_during_commit(library, monkeypatch):
    root, path, state, _ = library
    _scan(state, root, path)
    catalog = state.registry.catalog
    before_db = _database_snapshot(state)
    update = catalog._update_attachment

    def change_after_update(*args, **kwargs):
        update(*args, **kwargs)
        path.write_bytes(path.read_bytes() + b"\n% concurrent change\n")

    monkeypatch.setattr(catalog, "_update_attachment", change_after_update)
    with pytest.raises(IdentityValidationError, match="PDF changed"):
        recover_stale_record(state, path.name, apply=True)
    assert _database_snapshot(state) == before_db


def test_recovery_rejects_catalog_change_during_check(library, monkeypatch):
    import adsfolio.pdf.renamer as module

    root, path, state, _ = library
    stale = _scan(state, root, path)
    verify = module.assert_mutation_allowed

    def change_after_verification(*args, **kwargs):
        result = verify(*args, **kwargs)
        changed = deepcopy(stale["metadata"])
        changed["notes"] = "Concurrent note"
        state.registry.replace_file_metadata(root, path.name, changed)
        return result

    monkeypatch.setattr(module, "assert_mutation_allowed", change_after_verification)
    with pytest.raises(IdentityValidationError, match="catalog changed"):
        recover_stale_record(state, path.name, apply=True)
    saved = state.registry.catalog.get_file(root, path.name)
    assert saved["metadata"]["notes"] == "Concurrent note"
    assert saved["metadata"][METADATA_STALE_KEY]
    assert saved["document_id"] is None


def test_recovery_rejects_cached_ads_change_during_check(library, monkeypatch):
    import adsfolio.pdf.renamer as module

    root, path, state, _ = library
    stale = _scan(state, root, path)
    verify = module.assert_mutation_allowed

    def change_after_verification(*args, **kwargs):
        result = verify(*args, **kwargs)
        state.registry.upsert_ads_document(
            {**DOCUMENT, "doi": ["10.1234/different"]}, source="concurrent-test"
        )
        return result

    monkeypatch.setattr(module, "assert_mutation_allowed", change_after_verification)
    with pytest.raises(IdentityValidationError, match="catalog changed"):
        recover_stale_record(state, path.name, apply=True)
    saved = state.registry.catalog.get_file(root, path.name)
    assert saved == stale
