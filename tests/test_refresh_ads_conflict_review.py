"""Review and apply an explicit identifier that conflicts with PDF metadata."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from pdf_identity_fixtures import write_pdf

from adsfolio.config import Config
from adsfolio.pdf.cli import pdf
from adsfolio.pdf.doi_providers import DoiProvider
from adsfolio.pdf.files import sha256_file
from adsfolio.pdf.identify_settings import IdentifySettings
from adsfolio.pdf.registry import open_registry_state
from adsfolio.pdf.rename_settings import RenameSettings

DOCUMENT = {
    "doi": ["10.5555/correct"],
    "title": ["Correct paper"],
    "author": ["Roe, Alice"],
    "year": "2021",
}


@pytest.fixture
def conflict_library(tmp_path: Path) -> Path:
    root = tmp_path / "library"
    root.mkdir()
    source = root / "paper.pdf"
    write_pdf(source, doi="10.5555/wrong")
    state = open_registry_state(root)
    try:
        state.upsert_file(
            source.name,
            {
                "current_filename": source.name,
                "sha256": sha256_file(source),
                "status": "unresolved",
                "notes": "Keep this note",
            },
        )
        state.save()
    finally:
        state.close()
    Config.save_rename_settings(RenameSettings(template="{authors}_{year}-{title}"))
    return root


def record(root: Path) -> dict:
    state = open_registry_state(root)
    try:
        return next(iter(state.registry.iter_files()))
    finally:
        state.close()


def choose_provider(monkeypatch, provider: str, document: dict) -> None:
    selected = DoiProvider.ADS if provider == "ads" else DoiProvider.CROSSREF
    monkeypatch.setattr(
        Config,
        "load_identify_settings",
        classmethod(lambda cls: IdentifySettings(providers=(selected,))),
    )
    if provider == "ads":
        document["bibcode"] = "2021JEx.....3....7R"
        search = SimpleNamespace(
            query=lambda **kwargs: SimpleNamespace(num_found=1, docs=[document])
        )
        monkeypatch.setattr(
            "adsfolio.pdf.cli._get_client", lambda _ctx: SimpleNamespace(search=search)
        )
    else:
        monkeypatch.setattr(
            "adsfolio.pdf.identifier.fetch_crossref_doi", lambda doi: dict(document)
        )


def preview(root: Path) -> tuple[dict, list[str]]:
    args = [
        "refresh-ads",
        str(root),
        "--identifier",
        "10.5555/correct",
        "--relative-path",
        "paper.pdf",
    ]
    result = CliRunner().invoke(pdf, [*args, "--offer-conflict-review"])
    assert result.exit_code == 0, result.output
    line = next(
        line
        for line in result.output.splitlines()
        if line.startswith("PDF_IDENTITY_CONFLICT_JSON:")
    )
    return json.loads(line.split(":", 1)[1]), args


def retry(args: list[str], reviewed: dict, *, rename: bool = False):
    return CliRunner().invoke(
        pdf,
        [
            *args,
            "--confirm-conflicting-identity",
            "--expected-sha256",
            reviewed["pdf_sha256"],
            "--expected-identity",
            reviewed["identity_fingerprint"],
            "--expected-review-digest",
            reviewed["review_digest"],
            "--expected-catalog-digest",
            reviewed["catalog_digest"],
            *([] if rename else ["--no-rename"]),
        ],
    )


@pytest.mark.parametrize("provider", ["ads", "crossref"])
@pytest.mark.parametrize("rename", [False, True])
def test_reviewed_identifier_updates_and_can_rename(
    conflict_library: Path, monkeypatch, provider: str, rename: bool
) -> None:
    document = dict(DOCUMENT)
    choose_provider(monkeypatch, provider, document)
    source = conflict_library / "paper.pdf"
    before_hash = sha256_file(source)
    before = record(conflict_library)
    reviewed, args = preview(conflict_library)
    assert "10.5555/wrong" in reviewed["conflict"]
    assert "10.5555/correct" in reviewed["identifiers"]
    assert reviewed["title"] == "Correct paper"
    assert record(conflict_library)["metadata"] == before["metadata"]

    result = retry(args, reviewed, rename=rename)
    assert result.exit_code == 0, result.output
    after = record(conflict_library)
    assert after["metadata"]["notes"] == "Keep this note"
    assert after["metadata"]["_adsfolio_identity"]["status"] == "manual_confirmed"
    assert (
        after["metadata"]["_adsfolio_identity"]["authority"]
        == "manual_conflict_override"
    )
    assert sha256_file(conflict_library / after["current_path"]) == before_hash
    if rename:
        assert after["current_path"] != "paper.pdf"


@pytest.mark.parametrize("provider", ["ads", "crossref"])
@pytest.mark.parametrize("changed", ["candidate", "catalog", "pdf"])
def test_conflict_review_refuses_changes_before_acceptance(
    conflict_library: Path, monkeypatch, provider: str, changed: str
) -> None:
    document = dict(DOCUMENT)
    choose_provider(monkeypatch, provider, document)
    reviewed, args = preview(conflict_library)
    source = conflict_library / "paper.pdf"
    if changed == "candidate":
        document["title"] = ["Different candidate details"]
    elif changed == "catalog":
        state = open_registry_state(conflict_library)
        try:
            current = state.registry.find_file_by_relative_path(
                conflict_library, source.name
            )
            metadata = {**current["metadata"], "notes": "Edited while reviewing"}
            assert state.registry.replace_file_metadata(
                conflict_library, source.name, metadata
            )
        finally:
            state.close()
    else:
        write_pdf(source, doi="10.5555/changed")
    before = record(conflict_library)
    before_hash = sha256_file(source)
    result = retry(args, reviewed)
    assert result.exit_code != 0
    assert record(conflict_library)["metadata"] == before["metadata"]
    assert sha256_file(source) == before_hash


@pytest.mark.parametrize("provider", ["ads", "crossref"])
def test_reviewed_conflict_preserves_a_locked_catalog_identifier(
    conflict_library: Path, monkeypatch, provider: str
) -> None:
    document = dict(DOCUMENT)
    choose_provider(monkeypatch, provider, document)
    state = open_registry_state(conflict_library)
    try:
        current = state.registry.find_file_by_relative_path(
            conflict_library, "paper.pdf"
        )
        metadata = {
            **current["metadata"],
            "doi": "10.5555/locked",
            "_adsfolio_locked_fields": ["doi"],
        }
        assert state.registry.replace_file_metadata(
            conflict_library, "paper.pdf", metadata
        )
    finally:
        state.close()
    reviewed, args = preview(conflict_library)
    before = record(conflict_library)
    result = retry(args, reviewed)
    assert result.exit_code != 0
    assert record(conflict_library)["metadata"] == before["metadata"]


@pytest.mark.parametrize("provider", ["ads", "crossref"])
def test_reviewed_conflict_preserves_an_existing_target_document(
    conflict_library: Path, monkeypatch, provider: str
) -> None:
    document = dict(DOCUMENT)
    choose_provider(monkeypatch, provider, document)
    other = conflict_library / "other.pdf"
    write_pdf(other, doi="10.5555/correct")
    other_digest = sha256_file(other)
    state = open_registry_state(conflict_library)
    try:
        state.upsert_file(
            other.name,
            {
                "sha256": other_digest,
                "doi": "10.5555/correct",
                "bibcode": document.get("bibcode", ""),
                "title": ["Existing target title"],
                "author": ["Existing, Author"],
                "year": "2021",
                "pub": "Journal of Examples",
                "volume": "3",
                "page": ["7"],
                "_adsfolio_external_metadata": {"provider": "crossref"},
                "status": "resolved" if provider == "ads" else "unresolved",
            },
        )
        original = state.registry.find_file_by_relative_path(
            conflict_library, other.name
        )
        original_document = state.registry.catalog.get_document(original["document_id"])
    finally:
        state.close()

    reviewed, args = preview(conflict_library)
    result = retry(args, reviewed)
    assert result.exit_code == 0, result.output
    state = open_registry_state(conflict_library)
    try:
        other_after = state.registry.find_file_by_relative_path(
            conflict_library, other.name
        )
        document_after = state.registry.catalog.get_document(other_after["document_id"])
        corrected = state.registry.find_file_by_relative_path(
            conflict_library, "paper.pdf"
        )
        assert other_after["metadata"] == original["metadata"]
        assert document_after == original_document
        assert corrected["document_id"] == other_after["document_id"]
        assert sha256_file(other) == other_digest
    finally:
        state.close()


def test_reference_conflict_reviews_the_user_candidate(
    conflict_library: Path, monkeypatch
) -> None:
    document = dict(DOCUMENT)
    choose_provider(monkeypatch, "ads", document)
    before = record(conflict_library)
    result = CliRunner().invoke(
        pdf,
        [
            "refresh-ads",
            str(conflict_library),
            "--identifier",
            "Examples-21-155",
            "--relative-path",
            "paper.pdf",
            "--offer-conflict-review",
        ],
    )
    assert result.exit_code == 0, result.output
    line = next(
        line
        for line in result.output.splitlines()
        if line.startswith("PDF_IDENTITY_CONFLICT_JSON:")
    )
    reviewed = json.loads(line.split(":", 1)[1])
    assert reviewed["identifier_option"] == "--bibcode"
    assert reviewed["identifier"] == document["bibcode"]
    assert record(conflict_library)["metadata"] == before["metadata"]
    args = [
        "refresh-ads",
        str(conflict_library),
        "--bibcode",
        reviewed["identifier"],
        "--relative-path",
        "paper.pdf",
    ]
    result = retry(args, reviewed)
    assert result.exit_code == 0, result.output
    assert record(conflict_library)["bibcode"] == document["bibcode"]


def test_gui_reference_with_no_match_does_not_apply_the_old_pdf_identifier(
    conflict_library: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        Config,
        "load_identify_settings",
        classmethod(lambda cls: IdentifySettings(providers=(DoiProvider.ADS,))),
    )
    search = SimpleNamespace(
        query=lambda **kwargs: SimpleNamespace(num_found=0, docs=[])
    )
    monkeypatch.setattr(
        "adsfolio.pdf.cli._get_client", lambda _ctx: SimpleNamespace(search=search)
    )
    before = record(conflict_library)
    result = CliRunner().invoke(
        pdf,
        [
            "refresh-ads",
            str(conflict_library),
            "--identifier",
            "Examples-21-155",
            "--relative-path",
            "paper.pdf",
            "--offer-conflict-review",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "No ADS record matched this reference" in result.output
    assert record(conflict_library)["metadata"] == before["metadata"]


def test_conflict_retry_preserves_a_concurrent_catalog_edit(
    conflict_library: Path, monkeypatch
) -> None:
    from adsfolio.pdf.registry import CentralPdfRegistry

    document = dict(DOCUMENT)
    choose_provider(monkeypatch, "crossref", document)
    reviewed, args = preview(conflict_library)
    original = CentralPdfRegistry.replace_file_metadata_if_unchanged

    def edit_before_commit(registry, root, relative_path, metadata, expected, **kwargs):
        concurrent = open_registry_state(root)
        try:
            current = concurrent.registry.find_file_by_relative_path(
                root, relative_path
            )
            edited = {**current["metadata"], "notes": "Concurrent note"}
            assert concurrent.registry.replace_file_metadata(
                root, relative_path, edited
            )
        finally:
            concurrent.close()
        return original(registry, root, relative_path, metadata, expected, **kwargs)

    monkeypatch.setattr(
        CentralPdfRegistry, "replace_file_metadata_if_unchanged", edit_before_commit
    )
    digest = sha256_file(conflict_library / "paper.pdf")
    result = retry(args, reviewed)
    assert result.exit_code != 0
    assert record(conflict_library)["metadata"]["notes"] == "Concurrent note"
    assert sha256_file(conflict_library / "paper.pdf") == digest


def test_conflicting_exact_doi_keeps_all_ads_candidates_for_review(
    conflict_library: Path, monkeypatch
) -> None:
    documents = [
        {**DOCUMENT, "bibcode": "2021JEx.....3....7R"},
        {**DOCUMENT, "bibcode": "2021JEx.....3....8R", "title": ["Another version"]},
    ]
    monkeypatch.setattr(
        Config,
        "load_identify_settings",
        classmethod(lambda cls: IdentifySettings(providers=(DoiProvider.ADS,))),
    )
    search = SimpleNamespace(
        query=lambda **kwargs: SimpleNamespace(num_found=2, docs=documents)
    )
    monkeypatch.setattr(
        "adsfolio.pdf.cli._get_client", lambda _ctx: SimpleNamespace(search=search)
    )
    before = record(conflict_library)
    result = CliRunner().invoke(
        pdf,
        [
            "refresh-ads",
            str(conflict_library),
            "--identifier",
            "10.5555/correct",
            "--relative-path",
            "paper.pdf",
            "--offer-conflict-review",
        ],
    )
    assert result.exit_code == 0, result.output
    line = next(
        line
        for line in result.output.splitlines()
        if line.startswith("ADS_MATCH_CANDIDATES_JSON:")
    )
    candidates = json.loads(line.split(":", 1)[1])
    assert {candidate["bibcode"] for candidate in candidates} == {
        item["bibcode"] for item in documents
    }
    assert all(candidate["pdf_sha256"] == before["sha256"] for candidate in candidates)
    assert all(
        candidate["identity_fingerprint"] and candidate["review_digest"]
        for candidate in candidates
    )
    assert record(conflict_library)["metadata"] == before["metadata"]


@pytest.mark.parametrize("provider", ["ads", "crossref"])
def test_reviewed_conflict_recovers_stale_metadata_and_preserves_notes(
    conflict_library: Path, monkeypatch, provider: str
) -> None:
    document = dict(DOCUMENT)
    choose_provider(monkeypatch, provider, document)
    source = conflict_library / "paper.pdf"
    write_pdf(source, doi="10.5555/updated-wrong")
    digest = sha256_file(source)
    state = open_registry_state(conflict_library)
    try:
        stat = source.stat()
        state.registry.upsert_file(
            root=conflict_library,
            relative_path=source.name,
            current_path=source.name,
            sha256=digest,
            size_bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            status="unresolved",
        )
    finally:
        state.close()
    assert record(conflict_library)["metadata"]["_adsfolio_metadata_stale"]
    reviewed, args = preview(conflict_library)
    result = retry(args, reviewed)
    assert result.exit_code == 0, result.output
    after = record(conflict_library)
    assert not after["metadata"].get("_adsfolio_metadata_stale")
    assert after["metadata"]["notes"] == "Keep this note"
    assert (
        after["metadata"]["_adsfolio_identity"]["authority"]
        == "manual_conflict_override"
    )
    assert sha256_file(source) == digest
