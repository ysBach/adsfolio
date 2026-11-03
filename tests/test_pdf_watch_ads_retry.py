"""Exercise the watcher's ADS retry with isolated PDFs and fake providers."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pypdf import PdfWriter

from adsfolio.config import ADSError, Config
from adsfolio.pdf.doi_providers import DoiProvider
from adsfolio.pdf.files import sha256_file
from adsfolio.pdf.identifier import PdfIdentifier
from adsfolio.pdf.identify_settings import IdentifySettings, IdentifyStrategy
from adsfolio.pdf.identity import IDENTITY_KEY
from adsfolio.pdf.registry import open_registry_state
from adsfolio.pdf.rename_settings import RenameSettings
from adsfolio.pdf.renamer import PdfRenamer
from adsfolio.pdf.watch_session import ArrivalIdentity, WatchEvent, WatchState
from adsfolio.pdf.watcher import PdfWatcher

DOI = "10.5555/watch-retry"
BIBCODE = "2024ApJ...900..100E"
ARXIV = "2401.12345"
EXTERNAL_DOCUMENT = {
    "doi": [DOI],
    "title": ["Synthetic paper"],
    "author": ["Example, A."],
    "year": "2024",
    "identifier": [f"arXiv:{ARXIV}"],
}
ADS_DOCUMENT = {**EXTERNAL_DOCUMENT, "bibcode": BIBCODE}
PROVIDERS = (DoiProvider.CROSSREF, DoiProvider.ADS)


@pytest.fixture
def watch_case(tmp_path, monkeypatch):
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    monkeypatch.setattr(
        "adsfolio.pubcache.cache_path",
        lambda: tmp_path / "config" / "publications.json",
    )
    root = tmp_path / "library"
    root.mkdir()
    path = root / "incoming.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=400)
    writer.add_metadata({"/DOI": DOI, "/arXiv": ARXIV})
    writer.write(path)
    original = path.read_bytes()

    crossref = Mock(return_value=EXTERNAL_DOCUMENT.copy())
    monkeypatch.setattr("adsfolio.pdf.identifier.fetch_crossref_doi", crossref)
    monkeypatch.setattr(
        "adsfolio.pdf.identifier.fetch_datacite_doi",
        Mock(side_effect=AssertionError("Unexpected DataCite request")),
    )
    search = Mock(
        return_value=SimpleNamespace(num_found=1, docs=[ADS_DOCUMENT.copy()], start=0)
    )
    identifier = PdfIdentifier(SimpleNamespace(search=SimpleNamespace(query=search)))
    state = open_registry_state(root)
    watcher = PdfWatcher(identifier, PdfRenamer())
    event = WatchEvent(
        root=root,
        session_id="isolated",
        identity=ArrivalIdentity(device=1, inode=1, birth_time_ns=None, generation=1),
        path=path,
        state=WatchState.PROCESSING,
        newly_added=True,
    )

    def process(template="{bibcode}", providers=PROVIDERS):
        return watcher.process_event(
            event,
            state,
            RenameSettings(template=template),
            IdentifySettings(
                strategies=(IdentifyStrategy.PDF_METADATA_DOI,),
                providers=providers,
                allow_ads=False,
            ),
        )

    yield SimpleNamespace(
        root=root,
        path=path,
        original=original,
        state=state,
        crossref=crossref,
        search=search,
        identifier=identifier,
        process=process,
    )
    state.close()


def test_watch_retries_ads_after_crossref_for_required_bibcode(watch_case):
    outcome = watch_case.process()

    target = watch_case.root / f"{BIBCODE}.pdf"
    assert outcome.event.state is WatchState.RENAMED
    assert outcome.bibcode == BIBCODE
    assert outcome.destination == target
    assert target.read_bytes() == watch_case.original
    assert not watch_case.path.exists()
    saved = watch_case.state.registry.catalog.get_file(watch_case.root, target.name)
    assert saved["bibcode"] == BIBCODE
    assert saved["metadata"][IDENTITY_KEY]["status"] == "verified"
    assert saved["sha256"] == sha256_file(target)
    watch_case.crossref.assert_called_once_with(DOI)
    watch_case.search.assert_called_once()


@pytest.mark.parametrize(
    "response", ["unavailable", "absent", "conflict", "unverified"]
)
def test_failed_ads_retry_keeps_external_record_and_pdf(watch_case, response):
    if response == "unavailable":
        watch_case.search.side_effect = ADSError("ADS unavailable")
    elif response == "absent":
        watch_case.search.return_value = SimpleNamespace(num_found=0, docs=[], start=0)
    elif response == "conflict":
        conflicting = {**ADS_DOCUMENT, "identifier": ["arXiv:2301.54321"]}
        watch_case.search.return_value = SimpleNamespace(
            num_found=1, docs=[conflicting], start=0
        )
    else:
        unverified = {
            key: value for key, value in ADS_DOCUMENT.items() if key != "identifier"
        }
        watch_case.search.return_value = SimpleNamespace(
            num_found=1, docs=[unverified], start=0
        )

    outcome = watch_case.process()

    assert outcome.event.state is WatchState.FAILED
    assert "bibcode" in outcome.message
    assert watch_case.path.read_bytes() == watch_case.original
    saved = watch_case.state.registry.catalog.get_file(
        watch_case.root, watch_case.path.name
    )
    assert not saved["bibcode"]
    assert saved["metadata"]["doi"] == DOI
    assert saved["metadata"]["_adsfolio_external_metadata"]["provider"] == "crossref"
    assert saved["metadata"][IDENTITY_KEY]["status"] == "verified"
    assert saved["sha256"] == sha256_file(watch_case.path)
    watch_case.crossref.assert_called_once_with(DOI)
    watch_case.search.assert_called_once()


@pytest.mark.parametrize("response", ["unavailable", "absent", "conflict"])
def test_failed_ads_retry_preserves_preexisting_metadata(watch_case, response):
    external = PdfIdentifier(None).build_record(
        {**EXTERNAL_DOCUMENT, "provider": "crossref"}, watch_case.path
    )
    external["notes"] = "Preserve this user note"
    external["_adsfolio_locked_fields"] = ["title"]
    watch_case.state.upsert_file(watch_case.path.name, external)
    before = watch_case.state.registry.catalog.get_file(
        watch_case.root, watch_case.path.name
    )
    if response == "unavailable":
        watch_case.search.side_effect = ADSError("ADS unavailable")
    elif response == "absent":
        watch_case.search.return_value = SimpleNamespace(num_found=0, docs=[], start=0)
    else:
        conflicting = {**ADS_DOCUMENT, "identifier": ["arXiv:2301.54321"]}
        watch_case.search.return_value = SimpleNamespace(
            num_found=1, docs=[conflicting], start=0
        )

    outcome = watch_case.process()

    assert outcome.event.state is WatchState.FAILED
    after = watch_case.state.registry.catalog.get_file(
        watch_case.root, watch_case.path.name
    )
    assert after["metadata"] == before["metadata"]
    assert after["status"] == before["status"]
    assert watch_case.path.read_bytes() == watch_case.original
    watch_case.search.assert_called_once()


def test_successful_ads_retry_preserves_user_metadata(watch_case):
    external = PdfIdentifier(None).build_record(
        {**EXTERNAL_DOCUMENT, "provider": "crossref"}, watch_case.path
    )
    external["notes"] = "Preserve this user note"
    external["_adsfolio_locked_fields"] = ["title"]
    watch_case.state.upsert_file(watch_case.path.name, external)

    outcome = watch_case.process()

    assert outcome.event.state is WatchState.RENAMED
    saved = watch_case.state.registry.catalog.get_file(
        watch_case.root, outcome.destination.name
    )
    assert saved["metadata"]["notes"] == "Preserve this user note"
    assert saved["metadata"]["_adsfolio_locked_fields"] == ["title"]
    assert outcome.destination.read_bytes() == watch_case.original


def test_disabled_ads_does_not_trigger_retry(watch_case):
    outcome = watch_case.process(providers=(DoiProvider.CROSSREF,))

    assert outcome.event.state is WatchState.FAILED
    assert "bibcode" in outcome.message
    assert watch_case.path.read_bytes() == watch_case.original
    watch_case.crossref.assert_called_once_with(DOI)
    watch_case.search.assert_not_called()


@pytest.mark.parametrize("template", ["{title}", "{{bibcode}}_{title}"])
def test_template_without_bibcode_does_not_trigger_retry(watch_case, template):
    outcome = watch_case.process(template=template)

    assert outcome.event.state is WatchState.RENAMED
    assert outcome.destination.read_bytes() == watch_case.original
    saved = watch_case.state.registry.catalog.get_file(
        watch_case.root, outcome.destination.name
    )
    assert not saved["bibcode"]
    assert saved["metadata"]["_adsfolio_external_metadata"]["provider"] == "crossref"
    watch_case.crossref.assert_called_once_with(DOI)
    watch_case.search.assert_not_called()


def test_ads_first_does_not_query_again(watch_case):
    outcome = watch_case.process(providers=(DoiProvider.ADS, DoiProvider.CROSSREF))

    assert outcome.event.state is WatchState.RENAMED
    assert outcome.bibcode == BIBCODE
    assert outcome.destination.read_bytes() == watch_case.original
    watch_case.search.assert_called_once()
    watch_case.crossref.assert_not_called()


def test_dry_run_plans_ads_rename_without_writes(watch_case):
    record = PdfWatcher(watch_case.identifier, PdfRenamer()).process_new_pdf(
        watch_case.path,
        watch_case.state,
        scheme="{bibcode}",
        apply=False,
        strategies=(IdentifyStrategy.PDF_METADATA_DOI,),
        providers=PROVIDERS,
    )

    assert record["bibcode"] == BIBCODE
    assert record["planned_filename"] == f"{BIBCODE}.pdf"
    assert watch_case.path.read_bytes() == watch_case.original
    assert (
        watch_case.state.registry.catalog.get_file(
            watch_case.root, watch_case.path.name
        )
        is None
    )
    watch_case.search.assert_called_once()
