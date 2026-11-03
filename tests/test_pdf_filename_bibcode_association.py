"""Exercise filename associations with isolated PDFs, catalogs, and ADS results."""

import copy
import hashlib
import io
from types import SimpleNamespace

import fitz
import pytest
from pypdf import PdfReader, PdfWriter

from adsfolio.config import ADSError, Config
from adsfolio.pdf.ads_recheck import recheck_existing
from adsfolio.pdf.identifier import PdfIdentifier
from adsfolio.pdf.identify_settings import IdentifySettings, IdentifyStrategy
from adsfolio.pdf.identity import (
    IDENTITY_KEY,
    IdentityValidationError,
    assert_mutation_allowed,
)
from adsfolio.pdf.registry import open_registry_state
from adsfolio.pdf.rename_settings import RenameSettings
from adsfolio.pdf.renamer import PdfRenamer, verify_record_identity
from adsfolio.pdf.watcher import PdfWatcher

CODE = "2024ApJ...900..100A"
OTHER = "2024ApJ...900..101B"
DOI = "10.5555/example"
DOCUMENT = {
    "bibcode": CODE,
    "title": ["Synthetic paper"],
    "author": ["Example, A."],
    "year": "2024",
    "doi": [DOI],
}


def make_pdf(path, *, text="", metadata=None):
    document = fitz.open()
    page = document.new_page()
    if text:
        page.insert_text((40, 100), text)
    data = document.tobytes()
    document.close()
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
    writer.add_metadata(metadata or {})
    with path.open("wb") as stream:
        writer.write(stream)
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Search:
    def __init__(self, documents, *, incomplete=False):
        self.documents = documents
        self.incomplete = incomplete
        self.calls = []

    def query(self, *, q, fl, rows, start=0):
        self.calls.append(q)
        if start and self.incomplete:
            raise ADSError("incomplete test response")
        docs = copy.deepcopy(self.documents[start : start + rows])
        return SimpleNamespace(
            docs=docs,
            num_found=len(self.documents) + int(self.incomplete),
            start=start,
        )


def resolve(path, documents, **kwargs):
    search = Search(documents)
    engine = PdfIdentifier(SimpleNamespace(search=search))
    record = engine.resolve(
        path, strategies=(IdentifyStrategy.FILENAME_BIBCODE,), **kwargs
    )
    return engine, record


def test_scanned_pdf_association_survives_rename_and_checks_identity(tmp_path):
    path = tmp_path / f"Author_{CODE}_1.pdf"
    digest = make_pdf(path)
    engine, record = resolve(path, [DOCUMENT])

    assert record is not None
    proof = record[IDENTITY_KEY]
    assert proof["status"] == "filename_associated"
    assert proof["authority"] == "ads_filename_bibcode"
    assert proof["source_bibcode"] == CODE
    assert proof["sha256"] == digest
    assert proof["provider_identity"]
    assert proof["provider_identifiers"]["bibcode"] == CODE
    renamed = tmp_path / "renamed.pdf"
    path.rename(renamed)
    assert verify_record_identity(renamed, record)["status"] == "filename_associated"
    assert hashlib.sha256(renamed.read_bytes()).hexdigest() == digest
    changed = {**record, "doi": "10.5555/wrong"}
    with pytest.raises(IdentityValidationError):
        assert_mutation_allowed(renamed, changed)
    with pytest.raises(IdentityValidationError):
        assert_mutation_allowed(
            renamed, record, provider_document={**DOCUMENT, "doi": ["10.5555/new"]}
        )
    make_pdf(renamed, text="Changed PDF")
    with pytest.raises(IdentityValidationError):
        assert_mutation_allowed(renamed, record, expected_sha256=digest)
    assert not engine.identity_failures


def test_filename_bibcode_can_be_an_exact_ads_alias(tmp_path):
    path = tmp_path / f"{CODE}.pdf"
    make_pdf(path)
    document = {**DOCUMENT, "bibcode": OTHER, "alternate_bibcode": [CODE]}
    _, record = resolve(path, [document])
    assert record["bibcode"] == OTHER
    assert record[IDENTITY_KEY]["source_bibcode"] == CODE
    assert record[IDENTITY_KEY]["provider_identifiers"]["bibcode"] == OTHER
    assert verify_record_identity(path, record)["status"] == "filename_associated"


@pytest.mark.parametrize(
    "metadata",
    [
        {"/DOI": "10.5555/wrong"},
        {"/Subject": "DOI: 10.5555/wrong"},
        {"/WPS-ARTICLEDOI": "10.5555/wrong"},
        {"/arXiv": "2401.12345"},
    ],
)
def test_filename_association_rejects_conflicting_pdf_identifiers(tmp_path, metadata):
    path = tmp_path / f"{CODE}.pdf"
    before = make_pdf(path, metadata=metadata)
    engine, record = resolve(path, [DOCUMENT])
    assert record is None
    assert path in engine.identity_failures
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_first_page_reference_doi_does_not_conflict_with_verified_metadata(tmp_path):
    path = tmp_path / f"{CODE}.pdf"
    make_pdf(path, text="Reference DOI: 10.5555/other", metadata={"/DOI": DOI})
    _, record = resolve(path, [DOCUMENT])
    assert record[IDENTITY_KEY]["status"] == "verified"


def test_first_page_references_allow_filename_association_and_rename(tmp_path):
    path = tmp_path / f"{CODE}.pdf"
    digest = make_pdf(
        path,
        text=(f"References:\n10.5555/ref1\n10.5555/ref2\narXiv:2401.12345\n{OTHER}"),
    )
    _, record = resolve(path, [DOCUMENT])
    assert record[IDENTITY_KEY]["status"] == "filename_associated"
    renamed = tmp_path / "renamed.pdf"
    path.rename(renamed)
    assert verify_record_identity(renamed, record)["status"] == "filename_associated"
    assert hashlib.sha256(renamed.read_bytes()).hexdigest() == digest


def test_first_page_citation_doi_does_not_override_metadata_match(tmp_path):
    path = tmp_path / f"{CODE}.pdf"
    make_pdf(
        path,
        text=f"Citation: 10.5555/citation Paper: {DOI}",
        metadata={"/DOI": DOI},
    )
    _, record = resolve(path, [DOCUMENT])
    assert record[IDENTITY_KEY]["status"] == "verified"


@pytest.mark.parametrize(
    "missing", [("title",), ("author",), ("year",), ("title", "author", "year")]
)
def test_missing_descriptive_ads_fields_allow_filename_association(tmp_path, missing):
    path = tmp_path / f"{CODE}.pdf"
    make_pdf(path)
    document = {key: value for key, value in DOCUMENT.items() if key not in missing}
    _, record = resolve(path, [document])
    assert record[IDENTITY_KEY]["status"] == "filename_associated"
    for field in missing:
        assert not record[field]


def test_missing_primary_ads_bibcode_does_not_create_association(tmp_path):
    path = tmp_path / f"{CODE}.pdf"
    make_pdf(path)
    document = {key: value for key, value in DOCUMENT.items() if key != "bibcode"}
    _, record = resolve(path, [document])
    assert record is None


def test_ambiguous_filename_and_ads_owners_do_not_identify(tmp_path):
    path = tmp_path / f"{CODE}_{OTHER}.pdf"
    make_pdf(path)
    _, record = resolve(path, [DOCUMENT])
    assert record is None
    path = tmp_path / f"{CODE}.pdf"
    make_pdf(path)
    competing = {**DOCUMENT, "bibcode": OTHER, "alternate_bibcode": [CODE]}
    _, record = resolve(path, [DOCUMENT, competing])
    assert record is None
    _, record = resolve(path, [DOCUMENT, {"identifier": [CODE]}])
    assert record is None


def test_mismatched_ads_result_and_incomplete_pages_do_not_identify(tmp_path):
    path = tmp_path / f"{CODE}.pdf"
    make_pdf(path)
    _, record = resolve(path, [{**DOCUMENT, "bibcode": OTHER}])
    assert record is None
    engine = PdfIdentifier(SimpleNamespace(search=Search([DOCUMENT], incomplete=True)))
    with pytest.raises(ADSError, match="incomplete"):
        engine.resolve(path, strategies=(IdentifyStrategy.FILENAME_BIBCODE,))


def test_cached_filename_alone_is_not_a_complete_unique_lookup(tmp_path):
    path = tmp_path / f"{CODE}.pdf"
    make_pdf(path)
    engine = PdfIdentifier()
    assert (
        engine.resolve(
            path,
            strategies=(IdentifyStrategy.FILENAME_BIBCODE,),
            cached_candidate_lookup=lambda kind, value: (True, DOCUMENT),
            allow_network_lookup=False,
        )
        is None
    )


def test_cached_filename_requires_a_fresh_lookup_for_a_scanned_pdf(tmp_path):
    path = tmp_path / f"{CODE}.pdf"
    make_pdf(path)
    search = Search([DOCUMENT])
    engine = PdfIdentifier(SimpleNamespace(search=search))
    record = engine.resolve(
        path,
        strategies=(IdentifyStrategy.FILENAME_BIBCODE,),
        cached_candidate_lookup=lambda kind, value: (True, DOCUMENT),
    )
    assert record[IDENTITY_KEY]["status"] == "filename_associated"
    assert len(search.calls) == 1


def test_watcher_identifies_and_renames_filename_association(library):
    root, state = library
    path = root / f"{CODE}.pdf"
    digest = make_pdf(path)
    search = Search([DOCUMENT])
    watcher = PdfWatcher(PdfIdentifier(SimpleNamespace(search=search)), PdfRenamer())

    record = watcher.process_new_pdf(
        path,
        state,
        scheme="renamed",
        strategies=(IdentifyStrategy.FILENAME_BIBCODE,),
    )

    renamed = root / "renamed.pdf"
    assert not path.exists()
    assert hashlib.sha256(renamed.read_bytes()).hexdigest() == digest
    assert record[IDENTITY_KEY]["status"] == "filename_associated"
    _, saved = state.find_by_current_filename(renamed.name)
    assert (
        verify_record_identity(renamed, saved, state=state)["status"]
        == "filename_associated"
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


@pytest.mark.parametrize("apply", [False, True])
def test_scoped_recheck_preserves_notes_locks_other_attachment_and_bytes(
    library, apply
):
    root, state = library
    path = root / f"{CODE}.pdf"
    digest = make_pdf(path)
    metadata = {
        "sha256": digest,
        "status": "unresolved",
        "title": ["My title"],
        "notes": "My note",
        "_adsfolio_locked_fields": ["title"],
    }
    state.upsert_file(path.name, metadata)
    other = root / "unrelated.pdf"
    other_digest = make_pdf(other, text="Other attachment")
    state.upsert_file(other.name, {"sha256": other_digest, "status": "unresolved"})
    before = state.registry.catalog.get_file(root, path.name)
    unrelated = state.registry.catalog.get_file(root, other.name)
    settings = IdentifySettings(strategies=(IdentifyStrategy.FILENAME_BIBCODE,))

    result = recheck_existing(
        state,
        SimpleNamespace(search=Search([DOCUMENT])),
        RenameSettings(template="{bibcode}"),
        identify_settings=settings,
        apply=apply,
    )
    after = state.registry.catalog.get_file(root, path.name)
    assert result.failures == []
    assert result.planned_updates == 1
    assert result.renamed == 0
    if apply:
        assert after["attachment_id"] == before["attachment_id"]
        assert after["metadata"]["notes"] == "My note"
        assert after["metadata"]["title"] == ["My title"]
        assert after["metadata"][IDENTITY_KEY]["status"] == "filename_associated"
    else:
        assert after == before
    assert state.registry.catalog.get_file(root, other.name) == unrelated
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_recheck_keeps_excluded_unresolved_folder_unchanged(library):
    root, state = library
    folder = root / "unresolved"
    folder.mkdir()
    path = folder / f"{CODE}.pdf"
    digest = make_pdf(path)
    state.upsert_file(
        f"unresolved/{path.name}", {"sha256": digest, "status": "unresolved"}
    )
    before = state.registry.catalog.get_file(root, f"unresolved/{path.name}")
    settings = IdentifySettings(
        strategies=(IdentifyStrategy.FILENAME_BIBCODE,),
        recursive=True,
        output_unresolved=str(folder),
        skip_unresolved_directory=True,
    )
    search = Search([DOCUMENT])
    result = recheck_existing(
        state,
        SimpleNamespace(search=search),
        RenameSettings(),
        identify_settings=settings,
    )
    assert result.checked == 0
    assert not search.calls
    assert state.registry.catalog.get_file(root, f"unresolved/{path.name}") == before
    assert settings.skip_unresolved_directory


def test_recheck_uses_filename_with_saved_doi_and_allows_tracked_rename(library):
    root, state = library
    path = root / f"{CODE}.pdf"
    digest = make_pdf(path)
    state.upsert_file(
        path.name,
        {"sha256": digest, "status": "unresolved", "doi": DOI, "notes": "Keep"},
    )
    settings = IdentifySettings(strategies=(IdentifyStrategy.FILENAME_BIBCODE,))
    result = recheck_existing(
        state,
        SimpleNamespace(search=Search([DOCUMENT])),
        RenameSettings(),
        identify_settings=settings,
    )
    assert result.updated == 1
    _, metadata = state.find_by_current_filename(path.name)
    assert metadata[IDENTITY_KEY]["status"] == "filename_associated"
    assert metadata["notes"] == "Keep"
    target = PdfRenamer().rename_tracked_file(
        state, path, metadata, template="renamed", dry_run=False
    )
    assert target.name == "renamed.pdf"
    assert hashlib.sha256(target.read_bytes()).hexdigest() == digest
    _, moved = state.find_by_current_filename(target.name)
    assert (
        verify_record_identity(target, moved, state=state)["status"]
        == "filename_associated"
    )


def test_recheck_accepts_minimal_ads_record_with_first_page_references(library):
    root, state = library
    path = root / f"{CODE}.pdf"
    digest = make_pdf(path, text="References:\n10.5555/ref1\n10.5555/ref2")
    state.upsert_file(
        path.name, {"sha256": digest, "status": "unresolved", "notes": "Keep"}
    )
    settings = IdentifySettings(strategies=(IdentifyStrategy.FILENAME_BIBCODE,))
    result = recheck_existing(
        state,
        SimpleNamespace(search=Search([{"bibcode": CODE}])),
        RenameSettings(),
        identify_settings=settings,
    )
    assert result.updated == 1
    assert result.failures == []
    _, metadata = state.find_by_current_filename(path.name)
    assert metadata[IDENTITY_KEY]["status"] == "filename_associated"
    assert metadata["notes"] == "Keep"
    assert not metadata.get("title")
    assert not metadata.get("author")
    assert not metadata.get("year")
    target = PdfRenamer().rename_tracked_file(
        state, path, metadata, template="renamed", dry_run=False
    )
    _, moved = state.find_by_current_filename(target.name)
    assert (
        verify_record_identity(target, moved, state=state)["status"]
        == "filename_associated"
    )
    assert hashlib.sha256(target.read_bytes()).hexdigest() == digest


def test_recheck_refuses_filename_when_saved_doi_conflicts(library):
    root, state = library
    path = root / f"{CODE}.pdf"
    digest = make_pdf(path)
    state.upsert_file(
        path.name,
        {
            "sha256": digest,
            "status": "unresolved",
            "doi": "10.5555/other",
            "notes": "Keep",
        },
    )
    before = state.registry.catalog.get_file(root, path.name)
    settings = IdentifySettings(strategies=(IdentifyStrategy.FILENAME_BIBCODE,))
    result = recheck_existing(
        state,
        SimpleNamespace(search=Search([DOCUMENT])),
        RenameSettings(),
        identify_settings=settings,
    )
    assert result.updated == 0
    assert result.failures
    assert state.registry.catalog.get_file(root, path.name) == before
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize(
    ("text", "metadata", "expected"),
    [
        ("DOI: 10.5555/only", {}, "10.5555/only"),
        ("10.5555/only and 10.5555/only", {}, "10.5555/only"),
        ("10.5555/citation and 10.5555/paper", {}, None),
        (
            "10.5555/citation and 10.5555/paper",
            {"/Subject": "DOI: 10.5555/paper"},
            "10.5555/paper",
        ),
        (
            "10.5555/citation and 10.5555/paper",
            {"/Subject": "DOI: 10.5555/other"},
            None,
        ),
        (
            "10.5555/citation and 10.5555/paper",
            {"/DOI": "10.5555/citation", "/Subject": "DOI: 10.5555/paper"},
            None,
        ),
    ],
)
def test_first_page_multiple_dois_require_unique_metadata_match(
    tmp_path, text, metadata, expected
):
    path = tmp_path / "paper.pdf"
    make_pdf(path, text=text, metadata=metadata)
    assert PdfIdentifier().extract_doi_from_text(path) == expected
