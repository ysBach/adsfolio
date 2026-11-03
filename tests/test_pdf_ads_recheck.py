"""Check ADS rechecks with isolated catalogs, PDFs, and provider responses."""

from __future__ import annotations

import copy
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter

from adsfolio.config import ADSError, Config, RateLimitError, RequestTooLongError
from adsfolio.pdf.ads_recheck import (
    PENDING_RENAMES_KEY,
    PREVIOUS_IDENTITIES_KEY,
    recheck_existing,
)
from adsfolio.pdf.files import sha256_file
from adsfolio.pdf.identifier import PdfIdentifier
from adsfolio.pdf.identify_settings import IdentifySettings, IdentifyStrategy
from adsfolio.pdf.registry import open_registry_state
from adsfolio.pdf.rename_settings import RenameSettings

DOI = "10.1007/s10686-022-09854-9"
OLD = "2022ExA...tmp...44G"
CANONICAL = "2022ExA....54.1307G"
ADS_DOCUMENT = {
    "bibcode": CANONICAL,
    "alternate_bibcode": [OLD],
    "doi": [DOI],
    "title": ["A synthetic paper"],
    "author": ["Example, A."],
    "year": "2022",
}


def write_pdf(path: Path, *, doi: str = DOI, arxiv: str = "", tag: str = "") -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=400)
    metadata = {"/Title": "A synthetic paper", "/Subject": tag}
    if doi:
        metadata["/DOI"] = doi
    if arxiv:
        metadata["/arXiv"] = arxiv
    writer.add_metadata(metadata)
    writer.write(path)


def values(value):
    if isinstance(value, (list, tuple)):
        return [str(item).casefold() for item in value]
    return [str(value).casefold()] if value else []


class FakeSearch:
    """Model exact ADS field searches, including former bibcode fields."""

    def __init__(self, documents=(), *, error=None, before_query=None):
        self.documents = list(documents)
        self.error = error
        self.before_query = before_query
        self.queries = []

    def query(self, *, q, fl, rows, start=0):
        del fl
        self.queries.append(q)
        if self.before_query is not None:
            callback, self.before_query = self.before_query, None
            callback()
        if self.error is not None:
            raise self.error
        terms = re.findall(r'(bibcode|alternate_bibcode|identifier|doi):"([^"]+)"', q)
        matched = []
        for document in self.documents:
            for field, value in terms:
                if value.casefold() in values(document.get(field)):
                    matched.append(copy.deepcopy(document))
                    break
        return SimpleNamespace(
            num_found=len(matched), docs=matched[start : start + rows], start=start
        )


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    monkeypatch.setattr(Config, "check_rate_limit", classmethod(lambda cls: True))
    root = tmp_path / "library"
    root.mkdir()
    state = open_registry_state(root)
    yield root, state
    state.close()


def add_record(
    state, name="paper.pdf", *, source="crossref", doi=DOI, arxiv="", tag=""
):
    path = state.directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    write_pdf(path, doi=doi, arxiv=arxiv, tag=tag)
    doc = {
        "doi": [doi] if doi else [],
        "title": ["A synthetic paper"],
        "author": ["Example, A."],
        "year": "2022",
    }
    if arxiv:
        doc["identifier"] = [f"arXiv:{arxiv}"]
    if source in {"crossref", "datacite"}:
        doc["provider"] = source
        metadata = PdfIdentifier(None).build_record(doc, path)
    elif source == "ads":
        doc["bibcode"] = OLD
        metadata = PdfIdentifier(None).build_record(doc, path)
    elif source == "legacy":
        metadata = {"doi": doi, "sha256": sha256_file(path), "status": "unresolved"}
    elif source == "unknown-hash":
        metadata = {"doi": doi, "status": "unresolved"}
    elif source == "bibcode-only":
        metadata = {"bibcode": OLD, "sha256": sha256_file(path), "status": "unresolved"}
    else:
        raise AssertionError(source)
    state.upsert_file(name, metadata)
    return path, state.registry.catalog.get_file(state.directory, name)


def run(state, search, **kwargs):
    return recheck_existing(
        state,
        SimpleNamespace(search=search),
        RenameSettings(template="{bibcode}"),
        **kwargs,
    )


@pytest.mark.parametrize("provider", ["crossref", "datacite"])
def test_external_match_updates_existing_document_and_renames(library, provider):
    root, state = library
    path, before = add_record(state, source=provider)
    metadata = dict(before["metadata"])
    metadata["notes"] = "Keep this note"
    metadata["title"] = ["A reader's title"]
    metadata["_adsfolio_locked_fields"] = ["title"]
    state.registry.replace_file_metadata(root, path.name, metadata)
    state.registry.catalog.set_document_cite_key(before["document_id"], "reader2022")
    before = state.registry.catalog.get_file(root, path.name)
    digest = sha256_file(path)
    result = run(state, FakeSearch([ADS_DOCUMENT]))
    target = root / f"{CANONICAL}.pdf"
    after = state.registry.catalog.get_file(root, target.name)
    assert (result.checked, result.updated, result.renamed, result.failures) == (
        1,
        1,
        1,
        [],
    )
    assert after["attachment_id"] == before["attachment_id"]
    assert after["document_id"] == before["document_id"]
    assert after["status"] == "resolved"
    assert after["metadata"]["notes"] == "Keep this note"
    assert after["metadata"]["title"] == ["A reader's title"]
    assert after["metadata"]["_adsfolio_external_metadata"]["provider"] == provider
    assert after["metadata"]["_adsfolio_identity"]["status"] == "verified"
    assert (
        state.registry.catalog.get_document(after["document_id"])["metadata"][
            "_adsfolio_cite_key"
        ]
        == "reader2022"
    )
    assert sha256_file(target) == digest
    assert not path.exists()


@pytest.mark.parametrize("source", ["legacy", "unknown-hash"])
def test_legacy_record_can_use_current_pdf_identity(library, source):
    root, state = library
    path, before = add_record(state, source=source)
    assert not before["metadata"].get("_adsfolio_identity")
    digest = sha256_file(path)
    result = run(state, FakeSearch([ADS_DOCUMENT]))
    after = state.registry.catalog.get_file(root, f"{CANONICAL}.pdf")
    assert (result.updated, result.renamed, result.failures) == (1, 1, [])
    assert after["attachment_id"] == before["attachment_id"]
    assert after["sha256"] == digest
    assert after["metadata"]["_adsfolio_identity"]["status"] == "verified"


def test_temporary_bibcode_resolves_through_doi_and_real_field_behavior(library):
    root, state = library
    path, before = add_record(state, source="ads")
    digest = sha256_file(path)
    search = FakeSearch([ADS_DOCUMENT])
    result = run(state, search)
    after = state.registry.catalog.get_file(root, f"{CANONICAL}.pdf")
    assert (result.updated, result.renamed, result.failures) == (1, 1, [])
    assert after["document_id"] == before["document_id"]
    assert OLD in after["metadata"]["alternate_bibcode"]
    assert sha256_file(root / after["current_path"]) == digest
    assert any('doi:"' in query and DOI in query for query in search.queries)
    assert any(
        'alternate_bibcode:"' in query and OLD in query for query in search.queries
    )


def test_changed_bibcode_without_alias_uses_prior_proof_and_pdf_doi(library):
    root, state = library
    path, before = add_record(state, source="ads")
    prior_proof = copy.deepcopy(before["metadata"]["_adsfolio_identity"])
    document = {**ADS_DOCUMENT, "alternate_bibcode": []}
    result = run(state, FakeSearch([document]))
    after = state.registry.catalog.get_file(root, f"{CANONICAL}.pdf")
    assert (result.updated, result.renamed, result.failures) == (1, 1, [])
    assert after["metadata"][PREVIOUS_IDENTITIES_KEY][-1] == {
        "bibcode": OLD,
        "proof": prior_proof,
    }
    assert OLD not in after["metadata"].get("alternate_bibcode", [])
    assert after["metadata"]["_adsfolio_identity"]["status"] == "verified"
    assert not path.exists()


def test_changed_bibcode_without_alias_requires_prior_verified_proof(library):
    root, state = library
    path = root / "unproven.pdf"
    write_pdf(path)
    state.upsert_file(
        path.name,
        {"bibcode": OLD, "doi": DOI, "sha256": sha256_file(path), "status": "resolved"},
    )
    before = state.registry.catalog.get_file(root, path.name)
    document = {**ADS_DOCUMENT, "alternate_bibcode": []}
    result = run(state, FakeSearch([document]))
    assert result.updated == 0 and result.failures
    assert state.registry.catalog.get_file(root, path.name) == before


def test_changed_bibcode_without_alias_rejects_conflicting_arxiv(library):
    root, state = library
    path, before = add_record(state, source="ads", arxiv="2401.12345")
    document = {
        **ADS_DOCUMENT,
        "alternate_bibcode": [],
        "identifier": ["arXiv:2301.54321"],
    }
    result = run(state, FakeSearch([document]))
    assert result.updated == 0 and result.failures
    assert state.registry.catalog.get_file(root, path.name) == before


@pytest.mark.parametrize("lock_volume", [False, True])
def test_changed_ads_fields_and_locks_drive_saved_template(library, lock_volume):
    root, state = library
    path, before = add_record(state)
    metadata = copy.deepcopy(before["metadata"])
    metadata.update(
        {
            "year": "2000",
            "volume": "7",
            "page": ["8"],
            "pub": "Old journal",
            "notes": "Keep note",
            "custom": "Keep custom",
        }
    )
    metadata["_adsfolio_locked_fields"] = ["volume"] if lock_volume else []
    state.registry.replace_file_metadata(root, path.name, metadata)
    document = {
        **ADS_DOCUMENT,
        "year": "2023",
        "volume": "42",
        "page": ["1307"],
        "pub": "New journal",
    }
    settings = RenameSettings(template="{year}_{volume}_{page}-{bibcode}")
    result = recheck_existing(
        state, SimpleNamespace(search=FakeSearch([document])), settings
    )
    expected_volume = "7" if lock_volume else "42"
    target = root / f"2023_{expected_volume}_1307-{CANONICAL}.pdf"
    after = state.registry.catalog.get_file(root, target.name)
    assert (result.updated, result.renamed, result.failures) == (1, 1, [])
    assert after["metadata"]["year"] == "2023"
    assert after["metadata"]["volume"] == expected_volume
    assert after["metadata"]["page"] == ["1307"]
    assert after["metadata"]["pub"] == "New journal"
    assert after["metadata"]["notes"] == "Keep note"
    assert after["metadata"]["custom"] == "Keep custom"


def test_bibcode_only_record_finds_former_bibcode_in_ads_alias_field(library):
    root, state = library
    path, before = add_record(state, source="bibcode-only")
    search = FakeSearch([ADS_DOCUMENT])
    result = run(state, search)
    after = state.registry.catalog.get_file(root, f"{CANONICAL}.pdf")
    assert (result.updated, result.renamed, result.failures) == (1, 1, [])
    assert after["attachment_id"] == before["attachment_id"]
    assert OLD in after["metadata"]["alternate_bibcode"]
    assert not path.exists()
    assert len(search.queries) == 1
    assert f'alternate_bibcode:"{OLD}"' in search.queries[0]


def test_datacite_arxiv_id_uses_batched_arxiv_lookup(library):
    root, state = library
    path, before = add_record(state, source="datacite", doi="", arxiv="2401.12345")
    document = {**ADS_DOCUMENT, "doi": [], "identifier": ["arXiv:2401.12345"]}
    search = FakeSearch([document])
    result = run(state, search)
    after = state.registry.catalog.get_file(root, f"{CANONICAL}.pdf")
    assert (result.updated, result.renamed, result.failures) == (1, 1, [])
    assert after["attachment_id"] == before["attachment_id"]
    assert search.queries == ['identifier:"arXiv:2401.12345"']
    assert not path.exists()


@pytest.mark.parametrize(
    "reason", ["no-match", "ambiguous", "conflict", "outage", "rate-limit"]
)
def test_refused_candidate_preserves_catalog_and_pdf(library, reason):
    root, state = library
    path, before = add_record(
        state, source="legacy", arxiv="2401.12345" if reason == "conflict" else ""
    )
    digest = sha256_file(path)
    document = copy.deepcopy(ADS_DOCUMENT)
    if reason == "ambiguous":
        search = FakeSearch([document, {**document, "bibcode": "2022ExA....54.1308X"}])
    elif reason == "conflict":
        document["identifier"] = ["arXiv:2301.54321"]
        search = FakeSearch([document])
    elif reason == "outage":
        search = FakeSearch(error=ADSError("unavailable"))
    elif reason == "rate-limit":
        search = FakeSearch(error=RateLimitError("429"))
    else:
        search = FakeSearch()
    result = run(state, search)
    assert (result.updated, result.renamed) == (0, 0)
    if reason != "no-match":
        assert result.failures
    assert state.registry.catalog.get_file(root, path.name) == before
    assert sha256_file(path) == digest


def test_locked_bibcode_blocks_update(library):
    root, state = library
    path, before = add_record(state, source="ads")
    metadata = dict(before["metadata"])
    metadata["_adsfolio_locked_fields"] = ["bibcode"]
    state.registry.replace_file_metadata(root, path.name, metadata)
    before = state.registry.catalog.get_file(root, path.name)
    result = run(state, FakeSearch([ADS_DOCUMENT]))
    assert result.updated == 0
    assert any("locked" in message for message in result.failures)
    assert state.registry.catalog.get_file(root, path.name) == before


@pytest.mark.parametrize("change", ["bytes", "metadata"])
def test_change_during_lookup_is_not_overwritten(library, change):
    root, state = library
    path, before = add_record(state, source="legacy")
    if change == "bytes":

        def modify():
            write_pdf(path, doi=DOI, tag="modified during query")
    else:

        def modify():
            conn = sqlite3.connect(state.registry.catalog.path)
            try:
                metadata = dict(before["metadata"])
                metadata["notes"] = "Concurrent edit"
                import json

                with conn:
                    conn.execute(
                        "UPDATE attachments SET metadata_json = ? WHERE id = ?",
                        (json.dumps(metadata), before["attachment_id"]),
                    )
            finally:
                conn.close()

    result = run(state, FakeSearch([ADS_DOCUMENT], before_query=modify))
    after = state.registry.catalog.get_file(root, path.name)
    assert (result.updated, result.renamed) == (0, 0)
    assert result.failures
    if change == "metadata":
        assert after["metadata"]["notes"] == "Concurrent edit"
    else:
        assert after == before


def test_collision_keeps_both_files_and_reports_metadata_update(library):
    root, state = library
    path, before = add_record(state)
    destination = root / f"{CANONICAL}.pdf"
    destination.write_bytes(b"existing destination")
    digest = sha256_file(path)
    result = run(state, FakeSearch([ADS_DOCUMENT]))
    assert (result.updated, result.renamed) == (1, 0)
    assert any("rename failed" in message for message in result.failures)
    assert destination.read_bytes() == b"existing destination"
    assert sha256_file(path) == digest
    after = state.registry.catalog.get_file(root, path.name)
    assert after["attachment_id"] == before["attachment_id"]
    assert after["bibcode"] == CANONICAL
    assert after["location_id"] in after["metadata"][PENDING_RENAMES_KEY]["locations"]
    destination.unlink()
    retry = run(state, FakeSearch([]))
    assert (retry.updated, retry.renamed, retry.failures) == (0, 1, [])
    assert sha256_file(destination) == digest


def test_destination_created_during_rename_is_never_overwritten(library, monkeypatch):
    root, state = library
    path, _ = add_record(state)
    digest = sha256_file(path)
    import adsfolio.pdf.ads_recheck as module

    link = module.os.link

    def raced_link(source, destination, **kwargs):
        Path(destination).write_bytes(b"another process")
        return link(source, destination, **kwargs)

    monkeypatch.setattr(module.os, "link", raced_link)
    result = run(state, FakeSearch([ADS_DOCUMENT]))
    assert (result.updated, result.renamed) == (1, 0)
    assert any("rename failed" in failure for failure in result.failures)
    assert (root / f"{CANONICAL}.pdf").read_bytes() == b"another process"
    assert sha256_file(path) == digest


def test_missing_template_field_keeps_pending_rename(library):
    root, state = library
    path, _ = add_record(state, source="legacy")
    result = recheck_existing(
        state,
        SimpleNamespace(search=FakeSearch([ADS_DOCUMENT])),
        RenameSettings(template="{bibcode}_{volume}"),
    )
    saved = state.registry.catalog.get_file(root, path.name)
    assert (result.updated, result.renamed) == (1, 0)
    assert any("volume" in message for message in result.failures)
    assert PENDING_RENAMES_KEY in saved["metadata"]


@pytest.mark.parametrize("apply", [False, True])
def test_ads_custom_author_format_uses_guarded_client_outside_transaction(
    library, apply
):
    root, state = library
    path, before = add_record(state)
    calls = []

    def custom(bibcodes, *, format_string):
        assert not state.registry.catalog._connection.in_transaction
        calls.append((bibcodes, format_string))
        return "Example"

    client = SimpleNamespace(
        search=FakeSearch([ADS_DOCUMENT]), export=SimpleNamespace(custom=custom)
    )
    settings = RenameSettings(
        template="{authors}-{bibcode}", author_format="ads_custom:%3.1m"
    )
    result = recheck_existing(state, client, settings, apply=apply)
    assert calls == [([CANONICAL], "%3.1m")]
    assert not result.failures
    if apply:
        assert (result.updated, result.renamed) == (1, 1)
        assert (root / f"Example-{CANONICAL}.pdf").exists()
    else:
        assert (result.updated, result.renamed, result.planned_renames) == (0, 0, 1)
        assert state.registry.catalog.get_file(root, path.name) == before


def test_ads_custom_export_failure_keeps_pending_rename_for_retry(library):
    root, state = library
    path, _ = add_record(state)
    digest = sha256_file(path)

    def unavailable(bibcodes, *, format_string):
        assert not state.registry.catalog._connection.in_transaction
        raise RateLimitError("429")

    settings = RenameSettings(
        template="{authors}-{bibcode}", author_format="ads_custom:%3.1m"
    )
    failed_client = SimpleNamespace(
        search=FakeSearch([ADS_DOCUMENT]), export=SimpleNamespace(custom=unavailable)
    )
    result = recheck_existing(state, failed_client, settings)
    record = state.registry.catalog.get_file(root, path.name)
    assert (result.updated, result.renamed) == (1, 0)
    assert any("429" in message for message in result.failures)
    assert PENDING_RENAMES_KEY in record["metadata"]
    assert sha256_file(path) == digest

    def custom(bibcodes, *, format_string):
        assert not state.registry.catalog._connection.in_transaction
        return "Example"

    retry_client = SimpleNamespace(
        search=FakeSearch([]), export=SimpleNamespace(custom=custom)
    )
    retry = recheck_existing(state, retry_client, settings)
    target = root / f"Example-{CANONICAL}.pdf"
    assert (retry.updated, retry.renamed, retry.failures) == (0, 1, [])
    assert sha256_file(target) == digest


def test_ads_custom_export_respects_quota_before_request(library, monkeypatch):
    root, state = library
    path, _ = add_record(state)
    search = FakeSearch([ADS_DOCUMENT])
    monkeypatch.setattr(
        Config, "check_rate_limit", classmethod(lambda cls: not search.queries)
    )
    export = SimpleNamespace(
        custom=lambda *args, **kwargs: pytest.fail("export requested")
    )
    settings = RenameSettings(template="{authors}", author_format="ads_custom:%3.1m")
    result = recheck_existing(
        state, SimpleNamespace(search=search, export=export), settings
    )
    assert (result.updated, result.renamed) == (1, 0)
    assert any("quota" in message for message in result.failures)
    assert (
        PENDING_RENAMES_KEY
        in state.registry.catalog.get_file(root, path.name)["metadata"]
    )


def test_cancel_before_custom_export_keeps_pending_rename(library, monkeypatch):
    root, state = library
    path, _ = add_record(state)
    import adsfolio.pdf.ads_recheck as module

    original = module._GuardedExport.custom
    cancel = [False]

    def cancel_before_export(guard, bibcodes, *, format_string):
        cancel[0] = True
        return original(guard, bibcodes, format_string=format_string)

    monkeypatch.setattr(module._GuardedExport, "custom", cancel_before_export)
    export = SimpleNamespace(
        custom=lambda *args, **kwargs: pytest.fail("export requested")
    )
    settings = RenameSettings(template="{authors}", author_format="ads_custom:%3.1m")
    result = recheck_existing(
        state,
        SimpleNamespace(search=FakeSearch([ADS_DOCUMENT]), export=export),
        settings,
        cancelled=lambda: cancel[0],
    )
    assert result.cancelled and (result.updated, result.renamed) == (1, 0)
    assert (
        PENDING_RENAMES_KEY
        in state.registry.catalog.get_file(root, path.name)["metadata"]
    )


def test_active_catalog_transaction_prevents_all_ads_requests(library):
    root, state = library
    path, before = add_record(state)
    search = FakeSearch(error=AssertionError("search requested"))
    export = SimpleNamespace(
        custom=lambda *args, **kwargs: pytest.fail("export requested")
    )
    settings = RenameSettings(template="{authors}", author_format="ads_custom:%3.1m")
    connection = state.registry.catalog._connection
    connection.execute("BEGIN IMMEDIATE")
    try:
        result = recheck_existing(
            state, SimpleNamespace(search=search, export=export), settings
        )
        assert result.updated == 0 and any(
            "transaction" in error for error in result.failures
        )
        assert not search.queries
    finally:
        connection.rollback()
    assert state.registry.catalog.get_file(root, path.name) == before


def test_unchanged_no_match_and_disabled_do_not_open_pdfs(library, monkeypatch):
    root, state = library
    path, before = add_record(state, source="ads")
    same = {**ADS_DOCUMENT, "bibcode": OLD, "alternate_bibcode": []}
    import adsfolio.pdf.ads_recheck as module

    monkeypatch.setattr(
        module, "extract_pdf_own_identity", lambda *_: pytest.fail("PDF opened")
    )
    monkeypatch.setattr(
        module, "assert_mutation_allowed", lambda *_, **__: pytest.fail("PDF opened")
    )
    assert run(state, FakeSearch([same])).updated == 0
    assert run(state, FakeSearch()).updated == 0
    disabled = FakeSearch(error=AssertionError("network request"))
    result = run(state, disabled, ads_enabled=False)
    assert (result.checked, result.updated, result.renamed) == (0, 0, 0)
    assert disabled.queries == []
    assert state.registry.catalog.get_file(root, path.name) == before


def test_unchanged_bibcode_reverifies_old_proof_without_renaming(library):
    root, state = library
    path, before = add_record(state, source="ads")
    old = copy.deepcopy(before["metadata"])
    old["_adsfolio_identity"]["status"] = "reviewed_repair"
    old["_adsfolio_identity"]["audit_reference"] = "Keep the audit evidence"
    old["notes"] = "Keep this note"
    state.registry.replace_file_metadata(root, path.name, old)
    digest = sha256_file(path)
    same = {**ADS_DOCUMENT, "bibcode": OLD, "alternate_bibcode": []}

    result = run(state, FakeSearch([same]))

    after = state.registry.catalog.get_file(root, path.name)
    assert (result.updated, result.renamed, result.failures) == (1, 0, [])
    assert after["metadata"]["_adsfolio_identity"]["status"] == "verified"
    assert (
        after["metadata"]["_adsfolio_identity_history"][-1]["audit_reference"]
        == "Keep the audit evidence"
    )
    assert after["metadata"]["notes"] == "Keep this note"
    assert sha256_file(path) == digest


@pytest.mark.parametrize("claims_old", [False, True])
def test_unchanged_bibcode_ignores_doi_only_match_but_not_an_alias(
    library, monkeypatch, claims_old
):
    root, state = library
    path, before = add_record(state, source="ads")
    current = {**ADS_DOCUMENT, "bibcode": OLD, "alternate_bibcode": []}
    other = {
        **ADS_DOCUMENT,
        "bibcode": CANONICAL,
        "alternate_bibcode": [OLD] if claims_old else [],
    }
    import adsfolio.pdf.ads_recheck as module

    monkeypatch.setattr(
        module, "extract_pdf_own_identity", lambda *_: pytest.fail("PDF opened")
    )
    result = run(state, FakeSearch([current, other]))
    assert (result.updated, result.renamed) == (0, 0)
    assert bool(result.failures) is claims_old
    assert state.registry.catalog.get_file(root, path.name) == before


def test_preview_and_repeated_check(library):
    root, state = library
    path, before = add_record(state)
    preview = run(state, FakeSearch([ADS_DOCUMENT]), apply=False)
    assert (
        preview.updated,
        preview.renamed,
        preview.planned_updates,
        preview.planned_renames,
    ) == (0, 0, 1, 1)
    assert state.registry.catalog.get_file(root, path.name) == before
    assert not (root / f"{CANONICAL}.pdf").exists()
    first = run(state, FakeSearch([ADS_DOCUMENT]))
    after = state.registry.catalog.get_file(root, f"{CANONICAL}.pdf")
    document = state.registry.catalog.get_document(after["document_id"])
    second = run(state, FakeSearch([ADS_DOCUMENT]))
    assert (first.updated, second.updated, second.renamed, second.failures) == (
        1,
        0,
        0,
        [],
    )
    assert state.registry.catalog.get_file(root, after["current_path"]) == after
    assert state.registry.catalog.get_document(after["document_id"]) == document


def test_stale_hash_and_record_without_identifier_do_not_change(library):
    root, state = library
    path, before = add_record(state, source="legacy")
    write_pdf(path, doi=DOI, tag="new bytes")
    empty = root / "empty.pdf"
    write_pdf(empty, doi="")
    state.upsert_file(
        empty.name, {"sha256": sha256_file(empty), "status": "unresolved"}
    )
    empty_before = state.registry.catalog.get_file(root, empty.name)
    result = run(state, FakeSearch([ADS_DOCUMENT]))
    assert result.checked == 1
    assert result.updated == 0
    assert any("hash" in message for message in result.failures)
    assert state.registry.catalog.get_file(root, path.name) == before
    assert state.registry.catalog.get_file(root, empty.name) == empty_before


def test_stale_identified_pdf_is_reverified_before_update_and_rename(library):
    root, state = library
    path, before = add_record(state, source="ads")
    old = dict(before["metadata"])
    old["notes"] = "Keep my notes"
    state.registry.replace_file_metadata(root, path.name, old)
    write_pdf(path, doi=DOI, tag="saved PDF")
    digest = sha256_file(path)
    state.upsert_file(path.name, {"sha256": digest, "status": "unresolved"})
    stale = state.registry.catalog.get_file(root, path.name)
    assert stale["metadata"]["_adsfolio_metadata_stale"]

    result = run(state, FakeSearch([ADS_DOCUMENT]))

    target = root / f"{CANONICAL}.pdf"
    after = state.registry.catalog.get_file(root, target.name)
    assert result.updated == 1
    assert result.renamed == 1
    assert result.failures == []
    assert any("verified and updated" in message for message in result.activities)
    assert any("renamed to" in message for message in result.activities)
    assert after["attachment_id"] == before["attachment_id"]
    assert after["metadata"]["notes"] == "Keep my notes"
    assert not after["metadata"].get("_adsfolio_metadata_stale")
    assert after["metadata"]["_adsfolio_identity"]["status"] == "verified"
    assert sha256_file(target) == digest


@pytest.mark.parametrize("apply", [False, True])
def test_filename_bibcode_is_identified_only_after_pdf_proves_it(library, apply):
    root, state = library
    path = root / f"{OLD}.pdf"
    write_pdf(path, doi="", arxiv="1403.7143")
    digest = sha256_file(path)
    state.upsert_file(
        path.name,
        {"sha256": digest, "status": "unresolved", "notes": "Keep this note"},
    )
    record = state.registry.catalog.get_file(root, path.name)
    doc = {
        "bibcode": OLD,
        "identifier": ["arXiv:1403.7143"],
        "title": ["A synthetic paper"],
        "author": ["Example, A."],
        "year": "2022",
    }
    settings = IdentifySettings(strategies=(IdentifyStrategy.FILENAME_BIBCODE,))

    result = recheck_existing(
        state,
        SimpleNamespace(search=FakeSearch([doc])),
        RenameSettings(template="identified-{bibcode}"),
        apply=apply,
        identify_settings=settings,
    )

    assert result.failures == []
    assert result.planned_updates == 1
    assert result.renamed == result.planned_renames == 0
    after = state.registry.catalog.get_file(root, path.name)
    if apply:
        assert after["attachment_id"] == record["attachment_id"]
        assert after["bibcode"] == OLD
        assert after["metadata"]["notes"] == "Keep this note"
        assert after["metadata"]["_adsfolio_identity"]["status"] == "verified"
        assert any("identified as" in message for message in result.activities)
    else:
        assert after == record
    assert sha256_file(path) == digest


def test_filename_bibcode_with_conflicting_pdf_identity_is_not_identified(library):
    root, state = library
    path = root / f"{OLD}.pdf"
    write_pdf(path, doi="", arxiv="1403.7143")
    state.upsert_file(path.name, {"sha256": sha256_file(path), "status": "unresolved"})
    before = state.registry.catalog.get_file(root, path.name)
    doc = {
        "bibcode": OLD,
        "identifier": ["arXiv:1403.9999"],
        "title": ["A synthetic paper"],
        "author": ["Example, A."],
        "year": "2022",
    }
    settings = IdentifySettings(strategies=(IdentifyStrategy.FILENAME_BIBCODE,))

    result = run(state, FakeSearch([doc]), identify_settings=settings)

    assert result.updated == 0
    assert any("contradicts" in failure for failure in result.failures)
    assert state.registry.catalog.get_file(root, path.name) == before


def test_filename_bibcode_respects_a_disabled_strategy(library):
    root, state = library
    path = root / f"{OLD}.pdf"
    write_pdf(path, doi="", arxiv="1403.7143")
    state.upsert_file(path.name, {"sha256": sha256_file(path), "status": "unresolved"})
    before = state.registry.catalog.get_file(root, path.name)
    search = FakeSearch([ADS_DOCUMENT])

    result = run(
        state,
        search,
        identify_settings=IdentifySettings(
            strategies=(IdentifyStrategy.FIRST_PAGE_DOI,)
        ),
    )

    assert result.checked == 0
    assert search.queries == []
    assert state.registry.catalog.get_file(root, path.name) == before


def test_stale_pdf_with_conflicting_own_doi_stays_unresolved(library):
    root, state = library
    path, before = add_record(state, source="ads")
    write_pdf(path, doi="10.1000/a-different-paper")
    digest = sha256_file(path)
    state.upsert_file(path.name, {"sha256": digest, "status": "unresolved"})
    stale = state.registry.catalog.get_file(root, path.name)

    result = run(state, FakeSearch([ADS_DOCUMENT]))

    assert result.updated == 0
    assert any("contradicts" in failure for failure in result.failures)
    assert state.registry.catalog.get_file(root, path.name) == stale
    assert stale["attachment_id"] == before["attachment_id"]
    assert sha256_file(path) == digest


def test_stale_pdf_with_no_ads_match_reports_why_it_was_not_updated(library):
    root, state = library
    path, _before = add_record(state, source="ads")
    write_pdf(path, doi=DOI, tag="saved PDF")
    state.upsert_file(path.name, {"sha256": sha256_file(path), "status": "unresolved"})
    stale = state.registry.catalog.get_file(root, path.name)

    result = run(state, FakeSearch([]))

    assert any("ADS found no match" in failure for failure in result.failures)
    assert state.registry.catalog.get_file(root, path.name) == stale


def test_pdf_change_during_recheck_commit_rolls_back(library, monkeypatch):
    import adsfolio.pdf.ads_recheck as recheck_module

    root, state = library
    path, before = add_record(state, source="ads")
    original_pending = recheck_module._with_pending

    def change_after_verification(metadata, snapshot):
        result = original_pending(metadata, snapshot)
        write_pdf(path, doi=DOI, tag="changed during commit")
        return result

    monkeypatch.setattr(recheck_module, "_with_pending", change_after_verification)
    result = run(state, FakeSearch([ADS_DOCUMENT]))

    assert result.updated == 0
    assert any(
        "changed during the ADS update" in failure for failure in result.failures
    )
    assert state.registry.catalog.get_file(root, path.name) == before


def test_stale_shared_attachment_in_another_root_is_not_revalidated(library, tmp_path):
    root, state = library
    path, original = add_record(state, source="ads")
    other_root = tmp_path / "other-library"
    other_root.mkdir()
    other_path = other_root / "copy.pdf"
    other_path.write_bytes(path.read_bytes())
    other_state = open_registry_state(other_root)
    try:
        other_state.upsert_file(other_path.name, original["metadata"])
        other = other_state.registry.catalog.get_file(other_root, other_path.name)
        assert other["attachment_id"] == original["attachment_id"]
        metadata = dict(original["metadata"])
        metadata["_adsfolio_metadata_stale"] = True
        state.registry.replace_file_metadata(root, path.name, metadata)
        before = state.registry.catalog.get_file(root, path.name)

        result = run(state, FakeSearch([ADS_DOCUMENT]))

        assert result.updated == 0
        assert any("another library" in failure for failure in result.failures)
        assert state.registry.catalog.get_file(root, path.name) == before
    finally:
        other_state.close()


def test_multiple_locations_in_other_root_keep_identity_and_bytes(library, tmp_path):
    root, state = library
    path, before = add_record(state)
    second_root = tmp_path / "other"
    second_root.mkdir()
    other_path = second_root / "other.pdf"
    other_path.write_bytes(path.read_bytes())
    other_state = open_registry_state(second_root)
    try:
        other_state.upsert_file(other_path.name, before["metadata"])
        other_before = other_state.registry.catalog.get_file(
            second_root, other_path.name
        )
        assert other_before["attachment_id"] == before["attachment_id"]
        digest = sha256_file(other_path)
        result = run(state, FakeSearch([ADS_DOCUMENT]))
        after = state.registry.catalog.get_file(root, f"{CANONICAL}.pdf")
        other_after = other_state.registry.catalog.get_file(
            second_root, other_path.name
        )
        assert (result.updated, result.renamed, result.failures) == (1, 1, [])
        assert other_after["location_id"] == other_before["location_id"]
        assert other_after["attachment_id"] == after["attachment_id"]
        assert other_after["document_id"] == before["document_id"]
        assert sha256_file(other_path) == digest
        assert (
            other_after["location_id"]
            in other_after["metadata"][PENDING_RENAMES_KEY]["locations"]
        )
        blocked_search = FakeSearch(error=AssertionError("network request"))
        disabled = recheck_existing(
            other_state,
            SimpleNamespace(search=blocked_search),
            RenameSettings(template="other-{bibcode}"),
            ads_enabled=False,
        )
        assert (disabled.updated, disabled.renamed) == (0, 0)
        assert other_path.exists() and not blocked_search.queries
        later = recheck_existing(
            other_state,
            SimpleNamespace(search=FakeSearch([ADS_DOCUMENT])),
            RenameSettings(template="other-{bibcode}"),
        )
        renamed_other = second_root / f"other-{CANONICAL}.pdf"
        assert (later.updated, later.renamed, later.failures) == (0, 1, [])
        assert sha256_file(renamed_other) == digest
        assert (
            PENDING_RENAMES_KEY
            not in other_state.registry.catalog.get_file(
                second_root, renamed_other.name
            )["metadata"]
        )
    finally:
        other_state.close()


def test_shared_document_preserves_citation_key_and_attachment_links(library):
    root, state = library
    first, first_before = add_record(state, "one.pdf", source="ads", tag="edition one")
    second, second_before = add_record(
        state, "two.pdf", source="ads", tag="edition two"
    )
    assert first_before["attachment_id"] != second_before["attachment_id"]
    assert first_before["document_id"] == second_before["document_id"]
    state.registry.catalog.set_document_cite_key(
        first_before["document_id"], "shared-key"
    )
    digests = (sha256_file(first), sha256_file(second))
    result = run(state, FakeSearch([ADS_DOCUMENT]))
    records = list(state.registry.iter_files())
    assert result.updated == 2
    assert result.renamed == 1
    assert any("rename failed" in message for message in result.failures)
    assert {record["attachment_id"] for record in records} == {
        first_before["attachment_id"],
        second_before["attachment_id"],
    }
    assert {record["document_id"] for record in records} == {
        first_before["document_id"]
    }
    assert (
        state.registry.catalog.get_document(first_before["document_id"])["metadata"][
            "_adsfolio_cite_key"
        ]
        == "shared-key"
    )
    assert sorted(
        sha256_file(root / record["current_path"]) for record in records
    ) == sorted(digests)


def test_conflicting_existing_canonical_document_is_not_merged(library):
    root, state = library
    path, before = add_record(state, source="ads")
    catalog = state.registry.catalog
    # Create a distinct canonical document, as an existing catalog can contain both.
    with catalog._connection:
        catalog._connection.execute(
            "UPDATE documents SET bibcode = ? WHERE id = ?",
            (OLD, before["document_id"]),
        )
        catalog._connection.execute(
            "INSERT INTO documents (bibcode, metadata_json, created_at, updated_at) VALUES (?, '{}', 'x', 'x')",
            (CANONICAL,),
        )
    before = catalog.get_file(root, path.name)
    documents = catalog.list_documents()
    result = run(state, FakeSearch([ADS_DOCUMENT]))
    assert result.updated == 0
    assert any("another canonical document" in message for message in result.failures)
    assert catalog.get_file(root, path.name) == before
    assert catalog.list_documents() == documents


def test_cancel_and_batched_unique_identifiers(library, monkeypatch):
    _, state = library
    add_record(state, "one.pdf", source="legacy")
    add_record(state, "two.pdf", source="legacy", tag="another attachment")
    search = FakeSearch([ADS_DOCUMENT])
    result = run(state, search, cancelled=lambda: True)
    assert result.cancelled and not search.queries
    monkeypatch.setattr(PdfIdentifier, "MAX_BATCH_TERMS", 2)
    result = run(state, search, apply=False)
    assert result.checked == 2
    assert len(search.queries) == 1
    assert result.planned_updates == 2


def test_multiple_locations_in_one_root_use_same_root_settings(library):
    root, state = library
    path, before = add_record(state)
    nested = root / "nested" / "second.pdf"
    nested.parent.mkdir()
    nested.write_bytes(path.read_bytes())
    state.upsert_file(
        "nested/second.pdf",
        {**before["metadata"], "current_filename": "nested/second.pdf"},
    )
    second = state.registry.catalog.get_file(root, "nested/second.pdf")
    assert second["attachment_id"] == before["attachment_id"]
    digest = sha256_file(path)
    result = run(state, FakeSearch([ADS_DOCUMENT]), recursive=True)
    assert (result.updated, result.renamed, result.failures) == (1, 2, [])
    for target in (root / f"{CANONICAL}.pdf", nested.parent / f"{CANONICAL}.pdf"):
        assert sha256_file(target) == digest
        record = state.registry.catalog.get_file(root, str(target.relative_to(root)))
        assert PENDING_RENAMES_KEY not in record["metadata"]


def test_excluded_paths_do_not_query_ads_or_change_records(library):
    root, state = library
    included, _ = add_record(state, "included.pdf", source="legacy")
    skipped, skipped_before = add_record(
        state, "unresolved/skipped.pdf", source="legacy", doi="10.5555/skipped"
    )
    nested, nested_before = add_record(
        state, "nested/skipped.pdf", source="legacy", doi="10.5555/nested"
    )
    skipped_digest, nested_digest = sha256_file(skipped), sha256_file(nested)
    settings = IdentifySettings(
        recursive=True, output_unresolved="unresolved", skip_unresolved_directory=True
    )
    search = FakeSearch([ADS_DOCUMENT])
    result = run(state, search, identify_settings=settings, recursive=False)
    assert (result.checked, result.updated, result.renamed, result.failures) == (
        1,
        1,
        1,
        [],
    )
    assert all(
        "10.5555/skipped" not in q and "10.5555/nested" not in q for q in search.queries
    )
    assert (
        state.registry.catalog.get_file(root, "unresolved/skipped.pdf")
        == skipped_before
    )
    assert state.registry.catalog.get_file(root, "nested/skipped.pdf") == nested_before
    assert sha256_file(skipped) == skipped_digest
    assert sha256_file(nested) == nested_digest
    assert not included.exists()


def test_unresolved_exclusion_is_honored_when_recursive(library):
    root, state = library
    _, before = add_record(state, "unresolved/paper.pdf", source="legacy")
    search = FakeSearch([ADS_DOCUMENT])
    settings = IdentifySettings(
        recursive=True, output_unresolved="unresolved", skip_unresolved_directory=True
    )
    result = run(state, search, identify_settings=settings, recursive=True)
    assert (result.checked, result.updated, result.renamed) == (0, 0, 0)
    assert not search.queries
    assert state.registry.catalog.get_file(root, "unresolved/paper.pdf") == before


def test_excluded_alias_of_included_attachment_blocks_shared_write(library):
    root, state = library
    path, before = add_record(state)
    excluded = root / "unresolved" / "same.pdf"
    excluded.parent.mkdir()
    excluded.write_bytes(path.read_bytes())
    state.upsert_file(
        "unresolved/same.pdf",
        {**before["metadata"], "current_filename": "unresolved/same.pdf"},
    )
    excluded_before = state.registry.catalog.get_file(root, "unresolved/same.pdf")
    settings = IdentifySettings(
        recursive=True, output_unresolved="unresolved", skip_unresolved_directory=True
    )
    result = run(
        state, FakeSearch([ADS_DOCUMENT]), identify_settings=settings, recursive=True
    )
    assert result.updated == 0
    assert any("excluded" in message for message in result.failures)
    assert state.registry.catalog.get_file(root, path.name) == before
    assert (
        state.registry.catalog.get_file(root, "unresolved/same.pdf") == excluded_before
    )


def test_cancelled_shared_update_rolls_back_every_attachment(library, monkeypatch):
    root, state = library
    first, before_first = add_record(state, "one.pdf", source="ads", tag="one")
    second, before_second = add_record(state, "two.pdf", source="ads", tag="two")
    catalog = state.registry.catalog
    before_doc = catalog.get_document(before_first["document_id"])
    import adsfolio.pdf.ads_recheck as module

    original = module._with_pending
    cancel = [False]

    def cancel_inside_transaction(metadata, snapshot):
        cancel[0] = True
        return original(metadata, snapshot)

    monkeypatch.setattr(module, "_with_pending", cancel_inside_transaction)
    result = run(state, FakeSearch([ADS_DOCUMENT]), cancelled=lambda: cancel[0])
    assert result.cancelled and result.updated == 0
    assert catalog.get_file(root, first.name) == before_first
    assert catalog.get_file(root, second.name) == before_second
    assert catalog.get_document(before_first["document_id"]) == before_doc


def test_cancel_after_update_leaves_pending_rename_for_next_pass(library, monkeypatch):
    root, state = library
    path, before = add_record(state)
    import adsfolio.pdf.ads_recheck as module

    original = module._process_pending
    cancel = [False]

    def cancel_before_new_renames(*args, **kwargs):
        if kwargs.get("attachment_ids") is not None:
            cancel[0] = True
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "_process_pending", cancel_before_new_renames)
    result = run(state, FakeSearch([ADS_DOCUMENT]), cancelled=lambda: cancel[0])
    assert result.cancelled and (result.updated, result.renamed) == (1, 0)
    record = state.registry.catalog.get_file(root, path.name)
    assert record["attachment_id"] == before["attachment_id"]
    assert record["metadata"][PENDING_RENAMES_KEY]["locations"] == [
        record["location_id"]
    ]
    monkeypatch.setattr(module, "_process_pending", original)
    next_result = run(state, FakeSearch([]))
    assert (next_result.updated, next_result.renamed, next_result.failures) == (
        0,
        1,
        [],
    )
    assert (root / f"{CANONICAL}.pdf").exists()


def test_manual_confirmation_alone_does_not_transfer_identity(library):
    root, state = library
    path = root / "manual.pdf"
    write_pdf(path, doi="")
    external = {**ADS_DOCUMENT, "bibcode": "", "provider": "crossref"}
    external.pop("alternate_bibcode")
    metadata = PdfIdentifier(None).build_record(
        external, path, manual_confirmation=True
    )
    assert metadata["_adsfolio_identity"]["status"] == "manual_confirmed"
    state.upsert_file(path.name, metadata)
    before = state.registry.catalog.get_file(root, path.name)
    result = run(state, FakeSearch([ADS_DOCUMENT]))
    assert result.updated == 0 and result.failures
    assert state.registry.catalog.get_file(root, path.name) == before


def test_identifier_field_bibcode_can_supply_lookup(library):
    root, state = library
    path = root / "identifier.pdf"
    write_pdf(path)
    state.upsert_file(
        path.name,
        {"identifier": [OLD], "sha256": sha256_file(path), "status": "unresolved"},
    )
    result = run(state, FakeSearch([ADS_DOCUMENT]))
    assert (result.updated, result.renamed, result.failures) == (1, 1, [])
    assert (root / f"{CANONICAL}.pdf").exists()


def test_document_doi_index_is_updated_when_filled(library):
    root, state = library
    path = root / "legacy-ads.pdf"
    write_pdf(path)
    state.upsert_file(
        path.name, {"bibcode": OLD, "sha256": sha256_file(path), "status": "resolved"}
    )
    before = state.registry.catalog.get_file(root, path.name)
    result = run(state, FakeSearch([ADS_DOCUMENT]))
    assert result.updated == 1 and not result.failures
    row = state.registry.catalog._connection.execute(
        "SELECT doi FROM documents WHERE id = ?", (before["document_id"],)
    ).fetchone()
    assert row["doi"] == DOI


def test_unverified_shared_attachment_blocks_whole_document_update(library):
    root, state = library
    first, first_before = add_record(state, "one.pdf", source="ads", tag="one")
    second, second_before = add_record(
        state, "two.pdf", source="ads", arxiv="2401.12345", tag="two"
    )
    assert first_before["document_id"] == second_before["document_id"]
    catalog = state.registry.catalog
    doc_before = catalog.get_document(first_before["document_id"])
    result = run(state, FakeSearch([ADS_DOCUMENT]))
    assert result.updated == 0 and result.failures
    assert catalog.get_file(root, first.name) == first_before
    assert catalog.get_file(root, second.name) == second_before
    assert catalog.get_document(first_before["document_id"]) == doc_before


def test_shared_document_in_another_root_fails_closed_without_its_scope(
    library, tmp_path
):
    root, state = library
    first, first_before = add_record(state, "one.pdf", source="ads", tag="one")
    other_root = tmp_path / "other-root"
    other_root.mkdir()
    other_state = open_registry_state(other_root)
    try:
        second, second_before = add_record(
            other_state, "two.pdf", source="ads", tag="two"
        )
        assert first_before["document_id"] == second_before["document_id"]
        assert first_before["attachment_id"] != second_before["attachment_id"]
        catalog = state.registry.catalog
        document_before = catalog.get_document(first_before["document_id"])
        result = run(state, FakeSearch([ADS_DOCUMENT]))
        assert result.updated == 0
        assert any("another root" in message for message in result.failures)
        assert catalog.get_file(root, first.name) == first_before
        assert catalog.get_file(other_root, second.name) == second_before
        assert catalog.get_document(first_before["document_id"]) == document_before
    finally:
        other_state.close()


def test_low_quota_stops_without_requests(library, monkeypatch):
    root, state = library
    path, before = add_record(state)
    monkeypatch.setattr(Config, "check_rate_limit", classmethod(lambda cls: False))
    search = FakeSearch([ADS_DOCUMENT])
    result = run(state, search)
    assert result.updated == 0 and any(
        "quota" in message for message in result.failures
    )
    assert not search.queries
    assert state.registry.catalog.get_file(root, path.name) == before


def test_quota_is_checked_between_ads_result_pages(library, monkeypatch):
    root, state = library
    path, before = add_record(state, source="legacy")
    calls = []

    class PagedSearch:
        def query(self, *, q, fl, rows, start=0):
            calls.append(start)
            return SimpleNamespace(num_found=2, start=start, docs=[dict(ADS_DOCUMENT)])

    monkeypatch.setattr(Config, "check_rate_limit", classmethod(lambda cls: not calls))
    result = run(state, PagedSearch())
    assert calls == [0]
    assert result.updated == 0 and any(
        "quota" in message for message in result.failures
    )
    assert state.registry.catalog.get_file(root, path.name) == before


def test_quota_is_checked_before_retrying_a_split_query(library, monkeypatch):
    root, state = library
    first, first_before = add_record(state, "one.pdf", source="ads")
    second, second_before = add_record(
        state, "two.pdf", source="ads", doi="10.5555/second", tag="other"
    )
    second_metadata = dict(second_before["metadata"])
    second_metadata["bibcode"] = "2022ExA...tmp...45H"
    # A valid prior proof is not needed until ADS returns a candidate.
    second_metadata.pop("_adsfolio_identity")
    state.registry.replace_file_metadata(root, second.name, second_metadata)
    second_before = state.registry.catalog.get_file(root, second.name)
    calls = []

    class SplitSearch:
        def query(self, *, q, fl, rows, start=0):
            calls.append(q)
            raise RequestTooLongError("414")

    monkeypatch.setattr(Config, "check_rate_limit", classmethod(lambda cls: not calls))
    result = run(state, SplitSearch())
    assert len(calls) == 1
    assert result.updated == 0 and any(
        "quota" in message for message in result.failures
    )
    assert state.registry.catalog.get_file(root, first.name) == first_before
    assert state.registry.catalog.get_file(root, second.name) == second_before
