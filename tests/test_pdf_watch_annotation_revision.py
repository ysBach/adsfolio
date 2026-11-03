"""Keep existing PDF identifications after proven annotation-only saves."""

from __future__ import annotations

import hashlib
import io
import json
import os
from contextlib import closing
from pathlib import Path

import fitz
import pytest
from pypdf import PdfWriter

from adsfolio.config import Config
from adsfolio.pdf.identifier import PdfIdentifier
from adsfolio.pdf.identify_settings import IdentifySettings
from adsfolio.pdf.identity import (
    IDENTITY_KEY,
    IdentityValidationError,
    UnverifiedIdentityError,
    assert_mutation_allowed,
)
from adsfolio.pdf.readiness import (
    METADATA_SHA256_KEY,
    METADATA_STALE_KEY,
    PREVIOUS_RESOLUTION_KEY,
)
from adsfolio.pdf.registry import open_registry_state
from adsfolio.pdf.rename_settings import RenameSettings
from adsfolio.pdf.renamer import PdfRenamer
from adsfolio.pdf.watch_session import ArrivalIdentity, WatchEvent, WatchState
from adsfolio.pdf.watcher import PdfWatcher

DOC = {
    "bibcode": "2026ApJ...900..100S",
    "doi": ["10.1234/annotation-example"],
    "title": ["An example paper"],
    "author": ["Example, A."],
    "year": "2026",
}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _create_pdf(path: Path, *, with_doi: bool) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=400)
    metadata = {"/Title": "An example paper"}
    if with_doi:
        metadata["/DOI"] = DOC["doi"][0]
    writer.add_metadata(metadata)
    writer.write(path)


def _add_annotation(path: Path) -> None:
    with fitz.open(path) as document:
        document[0].add_text_annot((50, 50), "A reader's note")
        document.saveIncr()


def _event(root: Path, path: Path) -> WatchEvent:
    stat = path.stat()
    return WatchEvent(
        root=root,
        session_id="annotation-test",
        identity=ArrivalIdentity(stat.st_dev, stat.st_ino, None, 0),
        path=path,
        state=WatchState.DETECTED,
    )


@pytest.mark.parametrize("provenance", ["verified", "manual", "legacy"])
@pytest.mark.parametrize("scan_first", [False, True])
@pytest.mark.parametrize("atomic_save", [False, True])
def test_watcher_preserves_identification_after_annotation_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provenance: str,
    scan_first: bool,
    atomic_save: bool,
) -> None:
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    root = tmp_path / "library"
    root.mkdir()
    path = root / "paper.pdf"
    _create_pdf(path, with_doi=provenance == "verified")
    identifier = PdfIdentifier(None)
    if provenance == "legacy":
        record = {
            **DOC,
            "sha256": _digest(path),
            "status": "resolved",
            "identified_at": "2026-01-01T00:00:00+00:00",
        }
    else:
        record = identifier.build_record(
            DOC, path, manual_confirmation=provenance == "manual"
        )
    record["notes"] = "Preserve these notes"
    record["_adsfolio_locked_fields"] = ["title"]

    with closing(open_registry_state(root)) as state:
        state.upsert_file(path.name, record)
        original = state.registry.catalog.get_file(root, path.name)
        original_proof = original["metadata"].get(IDENTITY_KEY)
        _add_annotation(path)
        if atomic_save:
            temporary = root / "temporary.pdf"
            temporary.write_bytes(path.read_bytes())
            os.replace(temporary, path)
        new_bytes = path.read_bytes()
        new_digest = _digest(path)
        if scan_first:
            state.registry.catalog.discover_root(root)
            stale = state.registry.catalog.get_file(root, path.name)["metadata"]
            assert stale[METADATA_STALE_KEY]
            assert stale[PREVIOUS_RESOLUTION_KEY]["sha256"] == record["sha256"]

        def unexpected_identify(*args, **kwargs):
            raise AssertionError("An annotation-only save must not start Identify")

        monkeypatch.setattr(identifier, "resolve_many", unexpected_identify)
        outcome = PdfWatcher(identifier, PdfRenamer()).process_event(
            _event(root, path), state, RenameSettings(), IdentifySettings()
        )

        assert outcome.event.state is WatchState.RESOLVED
        assert outcome.annotation_update
        assert outcome.destination == path
        assert path.read_bytes() == new_bytes
        saved = state.registry.catalog.get_file(root, path.name)
        assert saved["attachment_id"] == original["attachment_id"]
        assert saved["status"] == "resolved"
        assert saved["bibcode"] == DOC["bibcode"]
        assert saved["sha256"] == new_digest
        assert saved["metadata"][METADATA_SHA256_KEY] == new_digest
        assert not saved["metadata"].get(METADATA_STALE_KEY)
        assert saved["metadata"]["notes"] == "Preserve these notes"
        assert saved["metadata"]["_adsfolio_locked_fields"] == ["title"]
        proof = saved["metadata"].get(IDENTITY_KEY)
        if original_proof is None:
            assert proof is None
            with pytest.raises(UnverifiedIdentityError):
                assert_mutation_allowed(path, saved["metadata"])
        else:
            assert proof["status"] == original_proof["status"]
            assert proof["sha256"] == new_digest
            lineage = proof["annotation_revision"]
            assert lineage["previous_sha256"] == original_proof["sha256"]
            assert lineage["original_authority"] == original_proof["authority"]
            assert lineage["original_checked_at"] == original_proof["checked_at"]
            assert (
                assert_mutation_allowed(path, saved["metadata"])["status"]
                == (original_proof["status"])
            )
            PdfWatcher(identifier, PdfRenamer()).process_new_pdf(
                path, state, scheme="paper"
            )
            repeated = state.registry.catalog.get_file(root, path.name)
            assert repeated["metadata"][IDENTITY_KEY]["annotation_revision"] == lineage


def test_watcher_does_not_transfer_identity_after_page_content_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    root = tmp_path / "library"
    root.mkdir()
    path = root / "paper.pdf"
    _create_pdf(path, with_doi=False)
    identifier = PdfIdentifier(None)
    record = identifier.build_record(DOC, path, manual_confirmation=True)
    with closing(open_registry_state(root)) as state:
        state.upsert_file(path.name, record)
        with fitz.open(path) as document:
            document[0].insert_text((40, 80), "Different page content")
            document.saveIncr()
        changed = path.read_bytes()
        state.registry.catalog.discover_root(root)
        outcome = PdfWatcher(identifier, PdfRenamer()).process_event(
            _event(root, path), state, RenameSettings(), IdentifySettings()
        )
        saved = state.registry.catalog.get_file(root, path.name)
        assert not outcome.annotation_update
        assert saved["status"] == "unresolved"
        assert saved["metadata"][METADATA_STALE_KEY]
        assert saved["metadata"][IDENTITY_KEY]["sha256"] != _digest(path)
        assert path.read_bytes() == changed


def test_annotation_revision_does_not_change_a_shared_live_attachment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    root = tmp_path / "library"
    root.mkdir()
    path = root / "paper.pdf"
    other = root / "other.pdf"
    _create_pdf(path, with_doi=False)
    other.write_bytes(path.read_bytes())
    record = PdfIdentifier(None).build_record(DOC, path, manual_confirmation=True)
    with closing(open_registry_state(root)) as state:
        state.upsert_file(path.name, record)
        first = state.registry.catalog.get_file(root, path.name)
        state.upsert_file(other.name, {**record, "current_filename": other.name})
        copies = state.registry.catalog.query_files(
            attachment_id=first["attachment_id"]
        )
        assert len(copies) == 2
        _add_annotation(path)
        annotated = path.read_bytes()
        result = state.registry.catalog.preserve_annotation_revision(
            root, path.name, _digest(path)
        )
        assert result is None
        assert path.read_bytes() == annotated
        assert _digest(other) == record["sha256"]
        assert (
            state.registry.catalog.get_file(root, path.name)["metadata"][IDENTITY_KEY][
                "sha256"
            ]
            == record["sha256"]
        )


def test_annotation_revision_does_not_commit_if_pdf_changes_during_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from adsfolio.pdf import annotation_revision

    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    root = tmp_path / "library"
    root.mkdir()
    path = root / "paper.pdf"
    _create_pdf(path, with_doi=False)
    record = PdfIdentifier(None).build_record(DOC, path, manual_confirmation=True)
    with closing(open_registry_state(root)) as state:
        state.upsert_file(path.name, record)
        _add_annotation(path)
        expected_digest = _digest(path)
        compare = annotation_revision.annotation_only_revision

        def change_after_comparison(*args):
            result = compare(*args)
            path.write_bytes(path.read_bytes() + b"\n% changed during check\n")
            return result

        monkeypatch.setattr(
            annotation_revision, "annotation_only_revision", change_after_comparison
        )
        assert (
            state.registry.catalog.preserve_annotation_revision(
                root, path.name, expected_digest
            )
            is None
        )
        saved = state.registry.catalog.get_file(root, path.name)
        assert saved["sha256"] == record["sha256"]
        assert saved["metadata"][IDENTITY_KEY]["sha256"] == record["sha256"]


def test_annotation_revision_dry_run_does_not_update_catalog_or_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    root = tmp_path / "library"
    root.mkdir()
    path = root / "paper.pdf"
    _create_pdf(path, with_doi=False)
    identifier = PdfIdentifier(None)
    record = identifier.build_record(DOC, path, manual_confirmation=True)
    with closing(open_registry_state(root)) as state:
        state.upsert_file(path.name, record)
        original = state.registry.catalog.get_file(root, path.name)
        _add_annotation(path)
        annotated = path.read_bytes()
        result = PdfWatcher(identifier, PdfRenamer()).process_new_pdf(
            path, state, apply=False
        )
        assert result is not None
        assert result["sha256"] == hashlib.sha256(annotated).hexdigest()
        assert state.registry.catalog.get_file(root, path.name) == original
        assert path.read_bytes() == annotated


def test_annotation_revision_does_not_transfer_a_conflicting_doi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    root = tmp_path / "library"
    root.mkdir()
    path = root / "paper.pdf"
    _create_pdf(path, with_doi=True)
    record = PdfIdentifier(None).build_record(DOC, path)
    with closing(open_registry_state(root)) as state:
        state.upsert_file(path.name, record)
        before = path.read_bytes()
        writer = PdfWriter(io.BytesIO(before), incremental=True)
        writer.add_metadata({"/DOI": "10.1234/a-different-paper"})
        output = io.BytesIO()
        writer.write(output)
        changed = output.getvalue()
        assert changed.startswith(before)
        path.write_bytes(changed)
        assert (
            state.registry.catalog.preserve_annotation_revision(
                root, path.name, _digest(path)
            )
            is None
        )
        saved = state.registry.catalog.get_file(root, path.name)
        assert saved["metadata"][IDENTITY_KEY]["sha256"] == record["sha256"]
        assert path.read_bytes() == changed


def test_annotation_revision_does_not_resolve_a_stale_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    root = tmp_path / "library"
    root.mkdir()
    path = root / "paper.pdf"
    _create_pdf(path, with_doi=False)
    record = {
        **DOC,
        "sha256": _digest(path),
        "status": "unresolved",
        "identified_at": "2026-01-01T00:00:00+00:00",
    }
    with closing(open_registry_state(root)) as state:
        state.upsert_file(path.name, record)
        _add_annotation(path)
        state.registry.catalog.discover_root(root)
        before = state.registry.catalog.get_file(root, path.name)
        assert before["status"] == "unresolved"
        assert before["metadata"][METADATA_STALE_KEY]
        assert PREVIOUS_RESOLUTION_KEY not in before["metadata"]
        assert (
            state.registry.catalog.preserve_annotation_revision(
                root, path.name, _digest(path)
            )
            is None
        )
        assert state.registry.catalog.get_file(root, path.name) == before


@pytest.mark.parametrize("scan_first", [False, True])
def test_annotation_save_does_not_overwrite_shared_document_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scan_first: bool
) -> None:
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    root = tmp_path / "library"
    root.mkdir()
    path = root / "paper.pdf"
    _create_pdf(path, with_doi=False)
    identifier = PdfIdentifier(None)
    record = identifier.build_record(DOC, path, manual_confirmation=True)
    with closing(open_registry_state(root)) as state:
        state.upsert_file(path.name, record)
        catalog = state.registry.catalog
        file_record = catalog.get_file(root, path.name)
        document_id = file_record["document_id"]
        row = catalog._connection.execute(
            "SELECT metadata_json FROM documents WHERE id = ?", (document_id,)
        ).fetchone()
        shared = json.loads(row["metadata_json"])
        shared["citation_count"] = 4321
        with catalog._connection:
            catalog._connection.execute(
                "UPDATE documents SET metadata_json = ? WHERE id = ?",
                (json.dumps(shared), document_id),
            )
        before = tuple(
            catalog._connection.execute(
                "SELECT * FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
        )
        _add_annotation(path)
        if scan_first:
            catalog.discover_root(root)
        outcome = PdfWatcher(identifier, PdfRenamer()).process_event(
            _event(root, path), state, RenameSettings(), IdentifySettings()
        )
        assert outcome.annotation_update
        after = tuple(
            catalog._connection.execute(
                "SELECT * FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
        )
        assert after == before
        catalog.discover_root(root)
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        state.registry.reconcile_file_facts(root)
        after_refresh = tuple(
            catalog._connection.execute(
                "SELECT * FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
        )
        assert after_refresh == before


@pytest.mark.parametrize("save_doi_in_record", [False, True])
def test_annotation_revision_refuses_conflicting_shared_document_identifiers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, save_doi_in_record: bool
) -> None:
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    root = tmp_path / "library"
    root.mkdir()
    path = root / "paper.pdf"
    _create_pdf(path, with_doi=True)
    record = PdfIdentifier(None).build_record(DOC, path)
    if not save_doi_in_record:
        record["doi"] = ""
        record[IDENTITY_KEY] = assert_mutation_allowed(
            path, record, provider_document=DOC
        )
    with closing(open_registry_state(root)) as state:
        state.upsert_file(path.name, record)
        catalog = state.registry.catalog
        document_id = catalog.get_file(root, path.name)["document_id"]
        _add_annotation(path)
        catalog.discover_root(root)
        row = catalog._connection.execute(
            "SELECT metadata_json FROM documents WHERE id = ?", (document_id,)
        ).fetchone()
        shared = json.loads(row["metadata_json"])
        shared["doi"] = ["10.1234/different-paper"]
        shared.pop("identifier", None)
        with catalog._connection:
            catalog._connection.execute(
                "UPDATE documents SET metadata_json = ? WHERE id = ?",
                (json.dumps(shared), document_id),
            )
        before = catalog.get_file(root, path.name)
        assert (
            catalog.preserve_annotation_revision(root, path.name, _digest(path)) is None
        )
        assert catalog.get_file(root, path.name) == before
        assert path.is_file()
        shared["doi"] = DOC["doi"]
        with catalog._connection:
            catalog._connection.execute(
                "UPDATE documents SET metadata_json = ? WHERE id = ?",
                (json.dumps(shared), document_id),
            )
        restored = catalog.preserve_annotation_revision(root, path.name, _digest(path))
        assert restored is not None
        assert restored["status"] == "resolved"


def test_annotation_revision_rolls_back_when_bytes_change_during_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    root = tmp_path / "library"
    root.mkdir()
    path = root / "paper.pdf"
    _create_pdf(path, with_doi=False)
    record = PdfIdentifier(None).build_record(DOC, path, manual_confirmation=True)
    with closing(open_registry_state(root)) as state:
        state.upsert_file(path.name, record)
        _add_annotation(path)
        catalog = state.registry.catalog
        update = catalog._update_attachment

        def replace_bytes(*args, **kwargs):
            result = update(*args, **kwargs)
            stat = path.stat()
            data = path.read_bytes()
            path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            return result

        monkeypatch.setattr(catalog, "_update_attachment", replace_bytes)
        digest = _digest(path)
        with pytest.raises(IdentityValidationError):
            catalog.preserve_annotation_revision(root, path.name, digest)
        saved = catalog.get_file(root, path.name)
        assert saved["sha256"] == record["sha256"]
        assert saved["metadata"][IDENTITY_KEY]["sha256"] == record["sha256"]


def test_native_watch_preserves_manual_identification_after_note_save(
    monkeypatch: pytest.MonkeyPatch, qtbot, tmp_path: Path
) -> None:
    pytest.importorskip("watchdog")
    from adsfolio.gui.watch import LibraryWatchController

    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    root = tmp_path / "library"
    root.mkdir()
    path = root / "paper.pdf"
    _create_pdf(path, with_doi=False)
    metadata = PdfIdentifier(None).build_record(DOC, path, manual_confirmation=True)
    with closing(open_registry_state(root)) as state:
        state.upsert_file(path.name, metadata)
    controller = LibraryWatchController()
    outcomes = []
    errors = []
    controller.outcome.connect(outcomes.append)
    controller.failed.connect(errors.append)
    controller.detected.connect(
        lambda event: controller.process(event, IdentifySettings(), RenameSettings())
    )
    with qtbot.waitSignal(controller.started, timeout=10000):
        controller.start_root(root, recursive=False)
    try:
        _add_annotation(path)
        expected = path.read_bytes()
        qtbot.waitUntil(
            lambda: any(o.annotation_update for o in outcomes), timeout=10000
        )
        assert path.read_bytes() == expected
        assert not errors
        with closing(open_registry_state(root)) as state:
            saved = state.registry.catalog.get_file(root, path.name)
            assert saved["status"] == "resolved"
            assert saved["sha256"] == hashlib.sha256(expected).hexdigest()
            assert saved["metadata"][IDENTITY_KEY]["status"] == "manual_confirmed"
    finally:
        assert controller.stop_all()
