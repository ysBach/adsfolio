"""Mutation boundaries: a PDF must prove the identity a write or rename uses."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

import pytest
from click.testing import CliRunner
from pypdf import PdfWriter

from adsfolio.cli.commands import main
from adsfolio.config import Config
from adsfolio.pdf.cli import _refresh_file_metadata
from adsfolio.pdf.files import sha256_file
from adsfolio.pdf.identifier import PdfIdentifier
from adsfolio.pdf.identify_settings import IdentifySettings
from adsfolio.pdf.identity import (
    IDENTITY_KEY,
    IdentityConflictError,
    IdentityValidationError,
    UnverifiedIdentityError,
)
from adsfolio.pdf.registry import PdfRegistry, open_registry_state
from adsfolio.pdf.rename_settings import RenameSettings
from adsfolio.pdf.renamer import PdfRenamer, verify_record_identity
from adsfolio.pdf.watch_session import ArrivalIdentity, WatchEvent, WatchState
from adsfolio.pdf.watcher import PdfWatcher

OWN_ARXIV = "2401.00001"
OTHER_ARXIV = "2401.99999"
BIBCODE = "2024arXiv240100001A"
OTHER_BIBCODE = "2024arXiv240199999B"
DOC = {
    "bibcode": BIBCODE,
    "identifier": [f"arXiv:{OWN_ARXIV}", BIBCODE],
    "title": ["Own paper"],
    "author": ["Doe, Jane"],
    "year": "2024",
}
OTHER_DOC = {
    "bibcode": OTHER_BIBCODE,
    "identifier": [f"arXiv:{OTHER_ARXIV}", OTHER_BIBCODE],
    "title": ["Other paper"],
    "author": ["Roe, Rick"],
    "year": "2024",
}


@pytest.fixture(autouse=True)
def configured(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    monkeypatch.setattr(
        "adsfolio.pubcache.cache_path", lambda: tmp_path / "publications.json"
    )


def make_pdf(path: Path, *, arxiv: str | None = None, tag: str = "a") -> Path:
    """Write a PDF whose DocInfo names `arxiv` as its own identifier, if given."""
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    info = {"/Title": f"fixture {tag}"}
    if arxiv:
        info["/arXiv"] = f"arXiv:{arxiv}"
    writer.add_metadata(info)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def register(
    root: Path,
    name: str,
    doc: dict,
    *,
    arxiv: str | None,
    cache: bool = True,
) -> str:
    """Register a resolved record for a PDF; return the file digest.

    With "cache" the raw ADS document is also cached, which is what lets a
    legacy record without a stored proof be checked against its primary Bibcode.
    """
    path = make_pdf(root / name, arxiv=arxiv, tag=name)
    digest = sha256_file(path)
    with closing(open_registry_state(root)) as state:
        state.upsert_file(
            name,
            {
                **doc,
                "current_filename": name,
                "sha256": digest,
                "status": "resolved",
                "_adsfolio_metadata_sha256": digest,
            },
        )
        if cache:
            state.registry.upsert_ads_documents([doc], source="test")
        state.save()
    return digest


def stored(root: Path, name: str) -> dict:
    registry = PdfRegistry(root)
    try:
        return registry.find_file_by_current_path(root, name)
    finally:
        registry.close()


# --- refresh -------------------------------------------------------------


def test_refresh_blocks_document_the_pdf_contradicts(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    register(root, "paper.pdf", DOC, arxiv=OTHER_ARXIV)
    before = stored(root, "paper.pdf")
    registry = PdfRegistry(root)
    refusals: dict[str, str] = {}
    try:
        assert not _refresh_file_metadata(
            registry, root, "paper.pdf", DOC, refusals=refusals
        )
    finally:
        registry.close()
    assert "paper.pdf" in refusals
    assert stored(root, "paper.pdf")["metadata"] == before["metadata"]
    assert (root / "paper.pdf").exists()


def test_refresh_blocks_merge_that_locked_fields_would_make_wrong(
    tmp_path: Path,
) -> None:
    """A fresh document the PDF proves is still refused if the merge is not proven."""
    root = tmp_path / "lib"
    register(root, "paper.pdf", OTHER_DOC, arxiv=OWN_ARXIV)
    registry = PdfRegistry(root)
    try:
        record = registry.find_file_by_current_path(root, "paper.pdf")
        metadata = dict(record["metadata"])
        metadata["_adsfolio_locked_fields"] = ["bibcode", "identifier"]
        registry.replace_file_metadata(root, "paper.pdf", metadata)
        refusals: dict[str, str] = {}
        assert not _refresh_file_metadata(
            registry, root, "paper.pdf", DOC, refusals=refusals
        )
        assert "paper.pdf" in refusals
        assert (
            registry.find_file_by_current_path(root, "paper.pdf")["bibcode"]
            == OTHER_BIBCODE
        )
    finally:
        registry.close()


def test_refresh_accepts_document_the_pdf_proves_and_stamps_proof(
    tmp_path: Path,
) -> None:
    root = tmp_path / "lib"
    digest = register(root, "paper.pdf", DOC, arxiv=OWN_ARXIV)
    registry = PdfRegistry(root)
    try:
        assert _refresh_file_metadata(
            registry, root, "paper.pdf", {**DOC, "abstract": "New abstract"}
        )
    finally:
        registry.close()
    metadata = stored(root, "paper.pdf")["metadata"]
    assert metadata["abstract"] == "New abstract"
    assert metadata[IDENTITY_KEY]["status"] == "verified"
    assert metadata[IDENTITY_KEY]["sha256"] == digest


def test_unverified_refresh_needs_manual_confirmation_but_conflict_never_passes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "lib"
    register(root, "unknown.pdf", DOC, arxiv=None)
    register(root, "conflict.pdf", DOC, arxiv=OTHER_ARXIV)
    registry = PdfRegistry(root)
    try:
        assert not _refresh_file_metadata(registry, root, "unknown.pdf", DOC)
        assert _refresh_file_metadata(
            registry, root, "unknown.pdf", DOC, manual_confirmation=True
        )
        assert not _refresh_file_metadata(
            registry, root, "conflict.pdf", DOC, manual_confirmation=True
        )
    finally:
        registry.close()
    proof = stored(root, "unknown.pdf")["metadata"][IDENTITY_KEY]
    assert proof["status"] == "manual_confirmed"
    assert IDENTITY_KEY not in stored(root, "conflict.pdf")["metadata"]


def test_refresh_command_refuses_wrong_pdf_and_counts_it(
    monkeypatch, tmp_path: Path
) -> None:
    root = tmp_path / "lib"
    register(root, "paper.pdf", DOC, arxiv=OTHER_ARXIV)
    before = stored(root, "paper.pdf")["metadata"]
    monkeypatch.setattr("adsfolio.pdf.cli._get_client", lambda ctx: object())
    monkeypatch.setattr(
        PdfIdentifier,
        "query_docs_for_bibcodes",
        lambda self, bibcodes, **kwargs: {BIBCODE: DOC},
    )
    result = CliRunner().invoke(main, ["pdf", "refresh-ads", str(root), "--all"])
    assert result.exit_code == 0, result.output
    assert "Refused 1 file(s)" in result.output
    assert "Refreshed ADS metadata for 0 bibcode(s)" in result.output
    assert (root / "paper.pdf").exists()
    assert stored(root, "paper.pdf")["metadata"] == before


def test_refresh_command_keeps_identity_when_ads_has_no_document(
    monkeypatch, tmp_path: Path
) -> None:
    root = tmp_path / "lib"
    register(root, "paper.pdf", DOC, arxiv=OWN_ARXIV)
    monkeypatch.setattr("adsfolio.pdf.cli._get_client", lambda ctx: object())
    monkeypatch.setattr(
        PdfIdentifier,
        "query_docs_for_bibcodes",
        lambda self, bibcodes, **kwargs: {BIBCODE: None},
    )
    result = CliRunner().invoke(main, ["pdf", "refresh-ads", str(root), "--all"])
    assert result.exit_code == 0, result.output
    assert stored(root, "paper.pdf")["bibcode"] == BIBCODE


def test_confirm_identity_requires_one_file(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    root.mkdir()
    result = CliRunner().invoke(
        main, ["pdf", "refresh-ads", str(root), "--all", "--confirm-identity"]
    )
    assert result.exit_code != 0
    assert "--relative-path" in result.output


# --- rename --------------------------------------------------------------


def test_rename_refuses_wrong_record_even_in_dry_run_and_same_target(
    tmp_path: Path,
) -> None:
    root = tmp_path / "lib"
    register(root, "paper.pdf", DOC, arxiv=OTHER_ARXIV)
    record = stored(root, "paper.pdf")
    metadata = {**record["metadata"], "sha256": record["sha256"], "status": "resolved"}
    renamer = PdfRenamer()
    source = root / "paper.pdf"
    with closing(open_registry_state(root)) as state:
        for dry_run in (True, False):
            with pytest.raises(IdentityConflictError):
                renamer.rename_tracked_file(state, source, metadata, dry_run=dry_run)
        # The template renders to the current name, so only the guard can refuse.
        with pytest.raises(IdentityConflictError):
            renamer.rename_tracked_file(
                state, source, metadata, template="paper", dry_run=True
            )
        assert renamer.batch_rename(state, dry_run=False) == []
    assert "paper.pdf" in renamer.identity_refused
    assert source.exists()
    assert [path.name for path in root.glob("*.pdf")] == ["paper.pdf"]


def test_rename_refuses_legacy_resolved_record_without_content_proof(
    tmp_path: Path,
) -> None:
    root = tmp_path / "lib"
    register(root, "scan.pdf", DOC, arxiv=None, cache=False)
    record = stored(root, "scan.pdf")
    metadata = {**record["metadata"], "sha256": record["sha256"], "status": "resolved"}
    with (
        closing(open_registry_state(root)) as state,
        pytest.raises(UnverifiedIdentityError),
    ):
        PdfRenamer().rename_tracked_file(
            state, root / "scan.pdf", metadata, dry_run=False
        )
    assert (root / "scan.pdf").exists()


def test_rename_proceeds_for_verified_record(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    register(root, "paper.pdf", DOC, arxiv=OWN_ARXIV)
    record = stored(root, "paper.pdf")
    metadata = {**record["metadata"], "sha256": record["sha256"], "status": "resolved"}
    with closing(open_registry_state(root)) as state:
        planned = PdfRenamer().rename_tracked_file(
            state, root / "paper.pdf", metadata, dry_run=True
        )
        assert planned.name != "paper.pdf"
        assert (root / "paper.pdf").exists()
        moved = PdfRenamer().rename_tracked_file(
            state, root / "paper.pdf", metadata, dry_run=False
        )
    assert moved.exists()
    assert not (root / "paper.pdf").exists()


# --- watcher -------------------------------------------------------------


def _watch_event(root: Path, path: Path) -> WatchEvent:
    stat = path.stat()
    return WatchEvent(
        root=root,
        session_id="session-1",
        identity=ArrivalIdentity(
            device=stat.st_dev,
            inode=stat.st_ino,
            birth_time_ns=getattr(stat, "st_birthtime_ns", None),
            generation=0,
        ),
        path=path,
        state=WatchState.DETECTED,
    )


def test_watcher_fails_without_touching_record_or_file_for_wrong_identity(
    tmp_path: Path,
) -> None:
    root = tmp_path / "lib"
    register(root, "paper.pdf", DOC, arxiv=OTHER_ARXIV)
    before = stored(root, "paper.pdf")
    watcher = PdfWatcher(PdfIdentifier(None), PdfRenamer())
    with closing(open_registry_state(root)) as state:
        event = _watch_event(root, root / "paper.pdf")
        outcome = watcher.process_event(
            event, state, RenameSettings(), IdentifySettings()
        )
    assert outcome.event.state is WatchState.FAILED
    assert OTHER_ARXIV in (outcome.message or "")
    assert "contradicts the proposed record" in (outcome.message or "")
    assert (root / "paper.pdf").exists()
    assert stored(root, "paper.pdf")["metadata"] == before["metadata"]


def test_watcher_renames_verified_resolved_pdf(tmp_path: Path) -> None:
    root = tmp_path / "lib"
    register(root, "paper.pdf", DOC, arxiv=OWN_ARXIV)
    watcher = PdfWatcher(PdfIdentifier(None), PdfRenamer())
    with closing(open_registry_state(root)) as state:
        record = watcher.process_new_pdf(root / "paper.pdf", state)
    assert record is not None
    assert record["current_filename"] != "paper.pdf"
    assert (root / record["current_filename"]).exists()
    assert not (root / "paper.pdf").exists()


def test_watcher_does_not_register_copy_of_unproven_resolved_record(
    tmp_path: Path,
) -> None:
    """Same bytes elsewhere do not inherit a record the PDF cannot prove."""
    root = tmp_path / "lib"
    register(root, "paper.pdf", DOC, arxiv=OTHER_ARXIV)
    copy = root / "sub" / "copy.pdf"
    copy.parent.mkdir()
    copy.write_bytes((root / "paper.pdf").read_bytes())
    watcher = PdfWatcher(PdfIdentifier(None), PdfRenamer())
    with (
        closing(open_registry_state(root)) as state,
        pytest.raises(IdentityValidationError),
    ):
        watcher.process_new_pdf(copy, state)
    assert copy.exists()
    assert stored(root, "sub/copy.pdf") is None


# --- identify ------------------------------------------------------------


def _identify(root: Path, *args: str):
    return CliRunner().invoke(
        main,
        [
            "pdf",
            "identify",
            str(root),
            "--apply",
            "--recursive",
            "--include-unresolved-directory",
            *args,
        ],
    )


def test_identify_keeps_unproven_resolved_pdf_in_unresolved_folder(
    tmp_path: Path,
) -> None:
    root = tmp_path / "lib"
    register(root, "unresolved/paper.pdf", DOC, arxiv=OTHER_ARXIV)
    before = stored(root, "unresolved/paper.pdf")["metadata"]
    result = _identify(root)
    assert result.exit_code == 0, result.output
    assert "Identity not verified" in result.output
    assert (root / "unresolved" / "paper.pdf").exists()
    assert not (root / "paper.pdf").exists()
    assert stored(root, "unresolved/paper.pdf")["metadata"] == before


def test_identify_returns_verified_resolved_pdf_from_unresolved_folder(
    tmp_path: Path,
) -> None:
    root = tmp_path / "lib"
    register(root, "unresolved/paper.pdf", DOC, arxiv=OWN_ARXIV)
    result = _identify(root)
    assert result.exit_code == 0, result.output
    assert (root / "paper.pdf").exists()
    assert not (root / "unresolved" / "paper.pdf").exists()


# --- provider documents and copied proofs ---------------------------------


def _legacy_metadata(root: Path, name: str) -> dict:
    record = stored(root, name)
    return {**record["metadata"], "sha256": record["sha256"], "status": "resolved"}


def test_rename_blocks_wrong_primary_with_right_secondary_identifier(
    tmp_path: Path,
) -> None:
    """A secondary arXiv alias on the record does not authorize another primary."""
    root = tmp_path / "lib"
    unioned = {
        **OTHER_DOC,
        "identifier": [*OTHER_DOC["identifier"], f"arXiv:{OWN_ARXIV}"],
    }
    register(root, "paper.pdf", unioned, arxiv=OWN_ARXIV, cache=False)
    metadata = _legacy_metadata(root, "paper.pdf")
    with closing(open_registry_state(root)) as state:
        # No cached provider document: nothing independent supports the Bibcode.
        with pytest.raises(IdentityValidationError):
            PdfRenamer().rename_tracked_file(
                state, root / "paper.pdf", metadata, dry_run=False
            )
        # The cached raw document for that primary Bibcode names another arXiv ID.
        state.registry.upsert_ads_documents([OTHER_DOC], source="test")
        with pytest.raises(IdentityValidationError):
            PdfRenamer().rename_tracked_file(
                state, root / "paper.pdf", metadata, dry_run=False
            )
    assert (root / "paper.pdf").exists()


def test_rename_accepts_legacy_record_backed_by_cached_primary_document(
    tmp_path: Path,
) -> None:
    root = tmp_path / "lib"
    register(root, "paper.pdf", DOC, arxiv=OWN_ARXIV)
    metadata = _legacy_metadata(root, "paper.pdf")
    with closing(open_registry_state(root)) as state:
        state.registry.upsert_ads_documents([DOC], source="test")
        moved = PdfRenamer().rename_tracked_file(
            state, root / "paper.pdf", metadata, dry_run=False
        )
    assert moved.exists()


def test_proof_copied_from_another_record_does_not_authorize_rename(
    tmp_path: Path,
) -> None:
    root = tmp_path / "lib"
    register(root, "paper.pdf", DOC, arxiv=OWN_ARXIV)
    registry = PdfRegistry(root)
    try:
        assert _refresh_file_metadata(registry, root, "paper.pdf", DOC)
    finally:
        registry.close()
    proof = stored(root, "paper.pdf")["metadata"][IDENTITY_KEY]
    metadata = {
        **_legacy_metadata(root, "paper.pdf"),
        "bibcode": OTHER_BIBCODE,
        "identifier": [OTHER_BIBCODE],
        IDENTITY_KEY: proof,
    }
    with (
        closing(open_registry_state(root)) as state,
        pytest.raises(IdentityValidationError),
    ):
        PdfRenamer().rename_tracked_file(
            state, root / "paper.pdf", metadata, dry_run=False
        )
    assert (root / "paper.pdf").exists()


def test_refresh_blocks_raw_document_whose_primary_arxiv_is_another_paper(
    tmp_path: Path,
) -> None:
    """Listing the PDF's arXiv ID as a secondary alias cannot adopt the PDF."""
    root = tmp_path / "lib"
    register(root, "paper.pdf", DOC, arxiv=OWN_ARXIV)
    before = stored(root, "paper.pdf")["metadata"]
    raw = {**OTHER_DOC, "identifier": [*OTHER_DOC["identifier"], f"arXiv:{OWN_ARXIV}"]}
    registry = PdfRegistry(root)
    refusals: dict[str, str] = {}
    try:
        assert not _refresh_file_metadata(
            registry, root, "paper.pdf", raw, refusals=refusals
        )
    finally:
        registry.close()
    assert "paper.pdf" in refusals
    assert stored(root, "paper.pdf")["metadata"] == before


def test_refresh_accepts_authoritative_document_with_several_dois(
    tmp_path: Path,
) -> None:
    root = tmp_path / "lib"
    path = make_pdf(root / "journal.pdf", tag="journal")
    writer = PdfWriter(clone_from=path)
    writer.add_metadata({"/DOI": "10.1000/journal.version"})
    with path.open("wb") as handle:
        writer.write(handle)
    raw = {
        "bibcode": "2020ApJ...900....1D",
        "doi": ["10.1000/journal.version", "10.1000/other.alias"],
        "title": ["Journal paper"],
        "author": ["Doe, Jane"],
        "year": "2020",
    }
    with closing(open_registry_state(root)) as state:
        state.upsert_file(
            "journal.pdf",
            {
                "bibcode": raw["bibcode"],
                "current_filename": "journal.pdf",
                "sha256": sha256_file(path),
                "status": "unresolved",
            },
        )
        state.save()
    registry = PdfRegistry(root)
    try:
        assert _refresh_file_metadata(registry, root, "journal.pdf", raw)
    finally:
        registry.close()
    assert stored(root, "journal.pdf")["metadata"][IDENTITY_KEY]["status"] == "verified"


@pytest.mark.parametrize("change_claims", [False, True])
def test_saved_proof_is_checked_before_incomplete_ads_cache(
    tmp_path: Path,
    change_claims: bool,
) -> None:
    """A missing cached alias cannot invalidate a bound proof or admit an edit."""
    root = tmp_path / "library"
    path = make_pdf(root / "paper.pdf", arxiv=OWN_ARXIV)
    fresh = {**DOC, "alternate_bibcode": ["2024ApJ...900..100A"]}
    record = PdfIdentifier(None).build_record(fresh, path)
    if change_claims:
        record["identifier"] = [f"arXiv:{OTHER_ARXIV}"]
    with closing(open_registry_state(root)) as state:
        state.registry.upsert_ads_documents([DOC], source="test")
        if change_claims:
            with pytest.raises(IdentityValidationError):
                verify_record_identity(path, record, state=state)
        else:
            assert (
                verify_record_identity(path, record, state=state)["status"]
                == "verified"
            )
            # An explicitly supplied provider still has to list every claim.
            with pytest.raises(IdentityValidationError):
                verify_record_identity(path, record, provider_document=DOC, state=state)
