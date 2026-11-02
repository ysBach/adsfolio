"""Fetch authoritative records before asking readers to review known candidates."""

from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from pdf_identity_fixtures import write_pdf

from adsfolio.config import ADSError, Config
from adsfolio.pdf.cli import pdf
from adsfolio.pdf.doi_providers import DoiProvider
from adsfolio.pdf.files import sha256_file
from adsfolio.pdf.identifier import PdfIdentifier
from adsfolio.pdf.identify_settings import IdentifySettings
from adsfolio.pdf.identity import IDENTITY_KEY
from adsfolio.pdf.registry import open_registry_state
from adsfolio.pdf.rename_settings import RenameSettings
from adsfolio.pdf.renamer import PdfRenamer
from adsfolio.pdf.watch_session import ArrivalIdentity, WatchEvent, WatchState
from adsfolio.pdf.watcher import PdfWatcher

DOC = {
    "bibcode": "2026ApJ..1009L..41B",
    "doi": ["10.5555/automatic-verification"],
    "title": ["Provider title"],
    "author": ["Example, A."],
    "year": "2026",
}


class Search:
    def __init__(self, documents, effect=None):
        self.documents = documents
        self.effect = effect
        self.queries = []

    def query(self, *, q, fl, rows, start=0):
        self.queries.append(q)
        if self.effect is not None:
            self.effect()
        return SimpleNamespace(
            docs=self.documents, num_found=len(self.documents), start=start
        )


def library(monkeypatch, tmp_path, *, doi=DOC["doi"][0], invalid_proof=False):
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    root = tmp_path / "library"
    root.mkdir()
    source = root / "paper.pdf"
    write_pdf(source, doi=doi)
    state = open_registry_state(root)
    record = {
        **DOC,
        "title": ["My locked title"],
        "notes": "Keep my notes",
        "_adsfolio_locked_fields": ["title"],
        "sha256": sha256_file(source),
        "status": "resolved",
        "current_filename": source.name,
    }
    if invalid_proof:
        record[IDENTITY_KEY] = {"status": "unverified", "sha256": "obsolete"}
    stat = source.stat()
    state.registry.upsert_file(
        root=root,
        relative_path=source.name,
        current_path=source.name,
        sha256=record["sha256"],
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        status="resolved",
        metadata=record,
    )
    return root, source, state


def event(root, source):
    stat = source.stat()
    return WatchEvent(
        root=root,
        session_id="test",
        identity=ArrivalIdentity(stat.st_dev, stat.st_ino, None, 0),
        path=source,
        state=WatchState.DETECTED,
    )


@pytest.mark.parametrize("workflow", ["watcher", "identify"])
@pytest.mark.parametrize("invalid_proof", [False, True])
def test_missing_provider_is_fetched_and_verified_without_review(
    monkeypatch, tmp_path, workflow, invalid_proof
):
    root, source, state = library(monkeypatch, tmp_path, invalid_proof=invalid_proof)
    digest = sha256_file(source)
    before = state.registry.find_file_by_current_path(root, source.name)
    search = Search([dict(DOC)])
    client = SimpleNamespace(search=search)
    if workflow == "watcher":
        outcome = PdfWatcher(PdfIdentifier(client), PdfRenamer()).process_event(
            event(root, source),
            state,
            RenameSettings(template="{bibcode}"),
            IdentifySettings(),
        )
        assert outcome.event.state is WatchState.RENAMED, outcome.message
        target = root / f"{DOC['bibcode']}.pdf"
    else:
        state.close()
        monkeypatch.setattr("adsfolio.pdf.cli._get_client", lambda ctx: client)
        result = CliRunner().invoke(
            pdf, ["identify", str(root), "--file", str(source), "--allow-ads"]
        )
        assert result.exit_code == 0, result.output
        assert "Identity not verified" not in result.output
        assert "Already identified PDFs (verified): 1" in result.output
        state = open_registry_state(root)
        target = source
    [after] = list(state.registry.iter_files())
    assert after["metadata"][IDENTITY_KEY]["status"] == "verified"
    assert after["metadata"]["notes"] == "Keep my notes"
    assert after["metadata"]["title"] == ["My locked title"]
    assert after["attachment_id"] == before["attachment_id"]
    assert sha256_file(target) == digest
    assert len(search.queries) == 1 and "bibcode:" in search.queries[0]
    assert state.registry.find_ads_document_by_bibcode(DOC["bibcode"]) is not None
    outcome = PdfWatcher(PdfIdentifier(client), PdfRenamer()).process_event(
        event(root, target),
        state,
        RenameSettings(template="{bibcode}"),
        IdentifySettings(),
    )
    assert outcome.event.state in {WatchState.RESOLVED, WatchState.RENAMED}
    assert len(search.queries) == 1
    state.close()


@pytest.mark.parametrize(
    "failure",
    [
        "missing",
        "conflict",
        "no_identifier",
        "service",
        "changed",
        "disabled",
        "no_client",
    ],
)
def test_unverifiable_candidates_remain_unchanged(monkeypatch, tmp_path, failure):
    root, source, state = library(
        monkeypatch, tmp_path, doi="" if failure == "no_identifier" else DOC["doi"][0]
    )
    original = source.read_bytes()
    before = state.get_file(source.name)
    documents = [] if failure == "missing" else [dict(DOC)]
    if failure == "conflict":
        documents = [{**DOC, "doi": ["10.5555/different-paper"]}]

    def effect():
        if failure == "service":
            raise ADSError("ADS unavailable")
        if failure == "changed":
            source.write_bytes(original + b"\n%external edit\n")

    search = Search(documents, effect)
    client = None if failure == "no_client" else SimpleNamespace(search=search)
    settings = (
        IdentifySettings(providers=(DoiProvider.CROSSREF,))
        if failure == "disabled"
        else IdentifySettings()
    )
    outcome = PdfWatcher(PdfIdentifier(client), PdfRenamer()).process_event(
        event(root, source), state, RenameSettings(template="{bibcode}"), settings
    )
    assert outcome.event.state is WatchState.FAILED
    assert source.read_bytes() == (
        original + b"\n%external edit\n" if failure == "changed" else original
    )
    assert state.get_file(source.name) == before
    assert not (root / f"{DOC['bibcode']}.pdf").exists()
    assert len(search.queries) == (0 if failure in {"disabled", "no_client"} else 1)
    state.close()


@pytest.mark.parametrize("workflow", ["watcher", "identify"])
def test_automatic_verification_for_another_location_of_known_contents(
    monkeypatch, tmp_path, workflow
):
    root, source, state = library(monkeypatch, tmp_path)
    original = source.read_bytes()
    arrival = root / "another-name.pdf"
    arrival.write_bytes(original)
    search = Search([dict(DOC)])
    client = SimpleNamespace(search=search)
    if workflow == "watcher":
        outcome = PdfWatcher(PdfIdentifier(client), PdfRenamer()).process_event(
            event(root, arrival),
            state,
            RenameSettings(template="{bibcode}"),
            IdentifySettings(),
        )
        assert outcome.event.state is WatchState.RENAMED, outcome.message
        assert source.read_bytes() == original
        target = root / f"{DOC['bibcode']}.pdf"
    else:
        source.unlink()
        state.close()
        monkeypatch.setattr("adsfolio.pdf.cli._get_client", lambda ctx: client)
        result = CliRunner().invoke(
            pdf, ["identify", str(root), "--file", str(arrival), "--allow-ads"]
        )
        assert result.exit_code == 0, result.output
        assert "Identity not verified" not in result.output
        state = open_registry_state(root)
        target = arrival
    saved = state.registry.find_file_by_current_path(root, target.name)
    assert saved["metadata"][IDENTITY_KEY]["status"] == "verified"
    assert target.read_bytes() == original
    assert len(search.queries) == 1
    state.close()


def test_automatic_verification_during_preview_keeps_pdf_and_association_unchanged(
    monkeypatch, tmp_path
):
    root, source, state = library(monkeypatch, tmp_path)
    before = state.get_file(source.name)
    original = source.read_bytes()
    search = Search([dict(DOC)])
    watcher = PdfWatcher(PdfIdentifier(SimpleNamespace(search=search)), PdfRenamer())

    record = watcher.process_new_pdf(source, state, scheme="{bibcode}", apply=False)

    assert record[IDENTITY_KEY]["status"] == "verified"
    assert record["planned_filename"] == f"{DOC['bibcode']}.pdf"
    assert state.get_file(source.name) == before
    assert source.read_bytes() == original
    assert not (root / f"{DOC['bibcode']}.pdf").exists()
    assert len(search.queries) == 1
    state.close()
