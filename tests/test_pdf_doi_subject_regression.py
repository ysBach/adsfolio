"""Regression test: identify and rename a PDF with DocInfo title and DOI in Subject and page 1.

Verifies end-to-end identification, mutation proof recording, subsequent rename,
byte preservation, and rejection of mismatching/conflicting identities.
"""

from __future__ import annotations

import io
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, StreamObject

from adsfolio.config import Config
from adsfolio.pdf.files import sha256_file
from adsfolio.pdf.identifier import PdfIdentifier
from adsfolio.pdf.identify_settings import IdentifySettings
from adsfolio.pdf.identity import (
    IDENTITY_KEY,
    IdentityConflictError,
    IdentityValidationError,
    UnverifiedIdentityError,
    assert_mutation_allowed,
    target_identity,
)
from adsfolio.pdf.registry import open_registry_state
from adsfolio.pdf.rename_settings import RenameSettings
from adsfolio.pdf.renamer import PdfRenamer
from adsfolio.pdf.watch_session import ArrivalIdentity, WatchEvent, WatchState
from adsfolio.pdf.watcher import PdfWatcher

DOI = "10.1051/0004-6361/202612345"
CONFLICTING_DOI = "10.1051/0004-6361/202699999"
BIBCODE = "2026A&A...712A.162B"
CONFLICTING_BIBCODE = "2026A&A...712A.999C"

MATCHING_DOC = {
    "bibcode": BIBCODE,
    "doi": [DOI],
    "title": ["Spectroscopic Analysis of Asteroid 2026 AA"],
    "author": ["Bach, Y.", "Opitom, C."],
    "year": "2026",
}

CONFLICTING_DOC = {
    "bibcode": CONFLICTING_BIBCODE,
    "doi": [CONFLICTING_DOI],
    "title": ["Different Paper on Stellar Interiors"],
    "author": ["Other, A."],
    "year": "2026",
}


def make_subject_doi_pdf(
    path: Path,
    *,
    title: str = "Spectroscopic Analysis of Asteroid 2026 AA",
    doi: str = DOI,
    explicit_doi: str | None = None,
    malformed_xmp: bool = False,
) -> Path:
    """Create a synthetic PDF with DocInfo title, bibliographic Subject with DOI, and page 1 text."""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page_text = (
        f"{title}\n"
        f"Astronomy & Astrophysics manuscript no. aa60056-26\n"
        f"https://doi.org/{doi}\n"
        f"Abstract. Observations and reflectance spectroscopy...\n"
    )
    page.insert_text((72, 72), page_text, fontsize=11)
    doc.set_metadata(
        {
            "title": title,
            "subject": f"Astronomy & Astrophysics, 2026. doi:{doi}",
        }
    )
    pdf_bytes = doc.tobytes()
    doc.close()

    if explicit_doi or malformed_xmp:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        writer = PdfWriter(clone_from=reader)
        if explicit_doi:
            writer.add_metadata({"/DOI": explicit_doi})
        if malformed_xmp:
            stream = StreamObject()
            stream._data = b"<root><pdf:Keywords>comets</pdf:Keywords></root>"
            stream[NameObject("/Type")] = NameObject("/Metadata")
            stream[NameObject("/Subtype")] = NameObject("/XML")
            writer._root_object[NameObject("/Metadata")] = writer._add_object(stream)
        buf = io.BytesIO()
        writer.write(buf)
        pdf_bytes = buf.getvalue()

    path.write_bytes(pdf_bytes)
    return path


class FakeADSClient:
    """Fake ADS API client returning canned document responses."""

    def __init__(self, docs: list[dict]):
        self.docs = docs
        self.queries: list[str] = []
        self.search = SimpleNamespace(query=self._query)

    def _query(self, q: str, fl: list[str] | None = None, rows: int = 10):
        self.queries.append(q)
        return SimpleNamespace(docs=self.docs, num_found=len(self.docs))


@pytest.mark.parametrize("explicit_doi", [None, DOI])
def test_malformed_xmp_identifies_and_watcher_renames_original_bytes(
    tmp_path: Path, explicit_doi: str | None
) -> None:
    """Unreadable optional XMP preserves automatic DOI identification and rename."""
    root = tmp_path / "library"
    pdf = make_subject_doi_pdf(
        root / "paper.pdf", explicit_doi=explicit_doi, malformed_xmp=True
    )
    original = pdf.read_bytes()
    digest = sha256_file(pdf)
    engine = PdfIdentifier(FakeADSClient([MATCHING_DOC]))
    resolved = engine.resolve_many([pdf], allow_network_lookup=True)[pdf]
    assert resolved is not None
    assert resolved[IDENTITY_KEY]["status"] == "verified"
    assert resolved[IDENTITY_KEY]["parser_warnings"] == [
        "XMP metadata is not well-formed"
    ]
    assert assert_mutation_allowed(pdf, resolved)["status"] == "verified"
    stat = pdf.stat()
    event = WatchEvent(
        root=root,
        session_id="xmp-regression",
        identity=ArrivalIdentity(stat.st_dev, stat.st_ino, None, 0),
        path=pdf,
        state=WatchState.DETECTED,
    )
    with closing(open_registry_state(root)) as state:
        state.registry.upsert_ads_documents([MATCHING_DOC], source="test")
        outcome = PdfWatcher(engine, PdfRenamer()).process_event(
            event, state, RenameSettings(template="{bibcode}"), IdentifySettings()
        )
        assert outcome.event.state is WatchState.RENAMED, outcome.message
        assert outcome.destination is not None
        assert outcome.destination.name == f"{BIBCODE}.pdf"
        assert outcome.destination.read_bytes() == original
        stored = state.registry.find_file_by_current_path(
            root, outcome.destination.name
        )
        assert stored is not None
        assert stored["bibcode"] == BIBCODE
        assert stored["sha256"] == digest
        assert stored["metadata"][IDENTITY_KEY]["status"] == "verified"


@pytest.fixture(autouse=True)
def setup_isolated_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    monkeypatch.setattr(
        "adsfolio.pubcache.cache_path", lambda: tmp_path / "cache" / "publications.json"
    )


def test_publisher_title_typography_automatically_identifies_and_renames(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    pdf = make_subject_doi_pdf(
        root / "publisher.pdf",
        title="Ice grains show ‘dust’ release - A model for activity",
    )
    original = pdf.read_bytes()
    digest = sha256_file(pdf)
    doc = {
        **MATCHING_DOC,
        "title": ["Ice grains show 'dust' release: A model for activity"],
    }
    engine = PdfIdentifier(FakeADSClient([doc]))
    resolved = engine.resolve_many([pdf], allow_network_lookup=True)[pdf]
    assert resolved is not None
    assert resolved[IDENTITY_KEY]["status"] == "verified"
    stat = pdf.stat()
    event = WatchEvent(
        root=root,
        session_id="publisher-typography",
        identity=ArrivalIdentity(stat.st_dev, stat.st_ino, None, 0),
        path=pdf,
        state=WatchState.DETECTED,
    )
    with closing(open_registry_state(root)) as state:
        state.registry.upsert_ads_documents([doc], source="test")
        outcome = PdfWatcher(engine, PdfRenamer()).process_event(
            event, state, RenameSettings(template="{bibcode}"), IdentifySettings()
        )
        assert outcome.event.state is WatchState.RENAMED, outcome.message
        assert outcome.destination is not None
        assert outcome.destination.name == f"{BIBCODE}.pdf"
        assert outcome.destination.read_bytes() == original
        saved = state.registry.find_file_by_current_path(root, outcome.destination.name)
        assert saved is not None
        assert saved["sha256"] == digest
        assert saved["metadata"][IDENTITY_KEY]["status"] == "verified"


def test_unverified_candidate_reports_the_failed_doi_check(tmp_path: Path) -> None:
    pdf = make_subject_doi_pdf(tmp_path / "paper.pdf", title="A Different Article")

    with pytest.raises(UnverifiedIdentityError) as info:
        PdfIdentifier(None).build_record(MATCHING_DOC, pdf)

    message = str(info.value)
    assert BIBCODE in message
    assert DOI in message
    assert "document titles differ" in message
    assert "Update by ID" in message
    assert "no readable identifier" not in message
    assert message.count(pdf.name) == 1
    assert info.value.candidate_bibcode == BIBCODE


def test_unverified_watcher_preserves_candidate_for_review_and_pdf_bytes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    pdf = make_subject_doi_pdf(root / "publisher.pdf")
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf.read_bytes())))
    writer.add_metadata({"/Subject": "Astronomy & Astrophysics"})
    buffer = io.BytesIO()
    writer.write(buffer)
    pdf.write_bytes(buffer.getvalue())
    original = pdf.read_bytes()
    stat = pdf.stat()
    event = WatchEvent(
        root=root,
        session_id="candidate-review",
        identity=ArrivalIdentity(stat.st_dev, stat.st_ino, None, 0),
        path=pdf,
        state=WatchState.DETECTED,
    )
    with closing(open_registry_state(root)) as state:
        state.registry.upsert_ads_documents([MATCHING_DOC], source="test")
        outcome = PdfWatcher(PdfIdentifier(None), PdfRenamer()).process_event(
            event, state, RenameSettings(template="{bibcode}"), IdentifySettings()
        )
        assert outcome.event.state is WatchState.FAILED
        assert outcome.candidate_bibcode == BIBCODE
        assert outcome.candidate_sha256 == sha256_file(pdf)
        assert (
            outcome.candidate_fingerprint == target_identity(MATCHING_DOC).fingerprint
        )
        assert outcome.candidate_confirmable
        assert outcome.bibcode is None
        assert "no explicitly labelled DOI" in outcome.message
        assert pdf.read_bytes() == original
        assert not (root / f"{BIBCODE}.pdf").exists()


def test_complete_title_doi_automatically_identifies_and_renames(
    tmp_path: Path,
) -> None:
    """A DOI used as the metadata title supports automatic identification."""
    root = tmp_path / "library"
    root.mkdir()
    pdf = root / "download.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=400)
    writer.add_metadata({"/Title": f"doi:{DOI}"})
    writer.write(pdf)
    original = pdf.read_bytes()
    engine = PdfIdentifier(FakeADSClient([MATCHING_DOC]))
    stat = pdf.stat()
    event = WatchEvent(
        root=root,
        session_id="title-doi",
        identity=ArrivalIdentity(stat.st_dev, stat.st_ino, None, 0),
        path=pdf,
        state=WatchState.DETECTED,
    )
    with closing(open_registry_state(root)) as state:
        outcome = PdfWatcher(engine, PdfRenamer()).process_event(
            event, state, RenameSettings(template="{bibcode}"), IdentifySettings()
        )
        assert outcome.event.state is WatchState.RENAMED, outcome.message
        assert outcome.destination is not None
        assert outcome.destination.name == f"{BIBCODE}.pdf"
        assert outcome.destination.read_bytes() == original
        record = state.registry.find_file_by_current_path(
            root, outcome.destination.name
        )
        assert record["metadata"][IDENTITY_KEY]["status"] == "verified"


def test_doi_in_subject_and_first_page_identifies_and_renames(tmp_path: Path) -> None:
    """A PDF with DOI in DocInfo Subject and page 1 identifies, preserves bytes, and renames."""
    library_dir = tmp_path / "library"
    pdf_path = library_dir / "unresolved_input.pdf"
    make_subject_doi_pdf(pdf_path, doi=DOI)
    original_bytes = pdf_path.read_bytes()
    original_sha256 = sha256_file(pdf_path)

    client = FakeADSClient([MATCHING_DOC])
    identifier = PdfIdentifier(client=client)

    with closing(open_registry_state(library_dir)) as state:
        state.registry.upsert_ads_documents([MATCHING_DOC], source="fake_ads")

        # 1. Identify step
        record = identifier.build_record(MATCHING_DOC, pdf_path)
        assert record["bibcode"] == BIBCODE
        assert record["doi"] == DOI
        assert record["sha256"] == original_sha256
        assert IDENTITY_KEY in record
        assert record[IDENTITY_KEY]["status"] == "verified"
        assert record[IDENTITY_KEY]["sha256"] == original_sha256

        # Register file with proven metadata
        state.upsert_file(pdf_path.name, record)

        # Verify bytes are untouched after identification
        assert pdf_path.read_bytes() == original_bytes

        # 2. Rename step
        renamer = PdfRenamer()
        dest_path = renamer.rename_tracked_file(
            state,
            pdf_path,
            record,
            template="{bibcode}_{title}",
            dry_run=False,
        )

        assert dest_path.exists()
        assert not pdf_path.exists()
        assert dest_path.name.startswith(BIBCODE)
        assert dest_path.read_bytes() == original_bytes
        assert sha256_file(dest_path) == original_sha256


def test_automatic_identify_resolve_many_persists_corroborated_record(
    tmp_path: Path,
) -> None:
    """Automatic identify via resolve_many resolves and persists a PDF with DOI in Subject and page 1."""
    library_dir = tmp_path / "library"
    pdf_path = library_dir / "unresolved_paper.pdf"
    make_subject_doi_pdf(pdf_path, doi=DOI)
    original_bytes = pdf_path.read_bytes()
    original_sha256 = sha256_file(pdf_path)

    client = FakeADSClient([MATCHING_DOC])
    identifier = PdfIdentifier(client=client)

    # 1. Run through actual automatic identify path (resolve_many)
    resolved_map = identifier.resolve_many([pdf_path], allow_network_lookup=True)
    assert pdf_path in resolved_map
    record = resolved_map[pdf_path]
    assert record is not None
    assert record["bibcode"] == BIBCODE
    assert record["doi"] == DOI
    assert record["sha256"] == original_sha256
    assert IDENTITY_KEY in record
    assert record[IDENTITY_KEY]["status"] == "verified"
    assert record[IDENTITY_KEY]["sha256"] == original_sha256

    # 2. Verify record persistence in registry state
    with closing(open_registry_state(library_dir)) as state:
        state.upsert_file(pdf_path.name, record)
        stored = state.get_file(pdf_path.name)
        assert stored is not None
        assert stored["bibcode"] == BIBCODE
        assert stored[IDENTITY_KEY]["status"] == "verified"
        assert stored[IDENTITY_KEY]["sha256"] == original_sha256

    # Source PDF bytes must remain untouched
    assert pdf_path.read_bytes() == original_bytes


def test_doi_subject_refuses_mismatching_candidate_metadata(tmp_path: Path) -> None:
    """A PDF with DOI in Subject and page 1 refuses mismatching candidate records as unverified."""
    library_dir = tmp_path / "library"
    pdf_path = library_dir / "paper.pdf"
    make_subject_doi_pdf(pdf_path, doi=DOI)
    original_bytes = pdf_path.read_bytes()

    client = FakeADSClient([CONFLICTING_DOC])
    identifier = PdfIdentifier(client=client)

    # Building a record with a mismatching candidate must be refused
    with pytest.raises(IdentityValidationError) as exc_info:
        identifier.build_record(CONFLICTING_DOC, pdf_path)

    assert isinstance(exc_info.value, (UnverifiedIdentityError, IdentityConflictError))
    assert pdf_path.read_bytes() == original_bytes


def test_stored_identity_proof_refuses_rename_on_conflict(tmp_path: Path) -> None:
    """Renaming is refused if the record or stored proof conflicts with the PDF's own identity."""
    library_dir = tmp_path / "library"
    pdf_path = library_dir / "paper.pdf"
    # Establish a real contradiction using dedicated DocInfo /DOI
    make_subject_doi_pdf(pdf_path, doi=DOI, explicit_doi=DOI)
    original_bytes = pdf_path.read_bytes()
    digest = sha256_file(pdf_path)

    with closing(open_registry_state(library_dir)) as state:
        # State holds a conflicting record for this file
        conflicting_record = {
            **CONFLICTING_DOC,
            "current_filename": pdf_path.name,
            "sha256": digest,
            "status": "resolved",
            "_adsfolio_metadata_sha256": digest,
        }
        state.upsert_file(pdf_path.name, conflicting_record)

        renamer = PdfRenamer()
        with pytest.raises(IdentityConflictError):
            renamer.rename_tracked_file(
                state,
                pdf_path,
                conflicting_record,
                dry_run=False,
            )

        assert pdf_path.exists()
        assert pdf_path.read_bytes() == original_bytes


def test_corroborated_doi_stored_proof_repeated_reuse(tmp_path: Path) -> None:
    """A corroborated DOI proof can be reused repeatedly across subsequent renames without provider."""
    library_dir = tmp_path / "library"
    pdf_path = library_dir / "paper.pdf"
    make_subject_doi_pdf(pdf_path, doi=DOI)
    original_bytes = pdf_path.read_bytes()

    # Initial corroboration with provider
    proof_1 = assert_mutation_allowed(
        pdf_path, MATCHING_DOC, provider_document=MATCHING_DOC
    )
    assert proof_1["status"] == "verified"
    assert proof_1["authority"] == "corroborated_document_doi"
    assert proof_1["own_doi"] == [DOI]

    # First reuse with no provider (authority becomes stored_proof)
    record_1 = {**MATCHING_DOC, IDENTITY_KEY: proof_1}
    proof_2 = assert_mutation_allowed(pdf_path, record_1)
    assert proof_2["status"] == "verified"
    assert proof_2["authority"] == "stored_proof"
    assert proof_2["own_doi"] == [DOI]

    # Subsequent reuse using proof_2 with no provider
    record_2 = {**MATCHING_DOC, IDENTITY_KEY: proof_2}
    proof_3 = assert_mutation_allowed(pdf_path, record_2)
    assert proof_3["status"] == "verified"
    assert proof_3["authority"] == "stored_proof"
    assert proof_3["own_doi"] == [DOI]

    # Modification to PDF content invalidates stored proof
    tampered_pdf = library_dir / "tampered.pdf"
    tampered_pdf.write_bytes(original_bytes + b"\n%extra")
    with pytest.raises(UnverifiedIdentityError):
        assert_mutation_allowed(tampered_pdf, record_2)

    # Modification to target DOI invalidates stored proof
    altered_target = {**record_2, "doi": ["10.1051/0004-6361/different"]}
    with pytest.raises(UnverifiedIdentityError):
        assert_mutation_allowed(pdf_path, altered_target)


def test_fallback_provider_does_not_bypass_uncorroborated_own_arxiv(
    tmp_path: Path,
) -> None:
    """A PDF with an uncorroborated arXiv stamp must fail closed even if DocInfo Subject has a DOI."""
    library_dir = tmp_path / "library"
    library_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = library_dir / "paper_with_stamp.pdf"
    # Create PDF with Subject DOI AND margin arXiv stamp
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    # Horizontal text contains Subject DOI
    page.insert_text((72, 72), "Title\ndoi:" + DOI, fontsize=11)
    # Rotated margin stamp contains uncorroborated arXiv ID
    page.insert_text(
        (36, 100),
        "arXiv:1208.4606v1",
        fontsize=9,
        rotate=90,
    )
    doc.set_metadata(
        {
            "title": MATCHING_DOC["title"][0],
            "subject": f"Astronomy & Astrophysics. doi:{DOI}",
        }
    )
    pdf_path.write_bytes(doc.tobytes())
    doc.close()

    # Provider record matches title and DOI, but does NOT list the arXiv ID
    # Since the PDF has an own identifier (arXiv) that provider does not corroborate,
    # it must fail closed and NOT be bypassed by the fallback provider corroboration.
    with pytest.raises(UnverifiedIdentityError) as exc_info:
        assert_mutation_allowed(pdf_path, MATCHING_DOC, provider_document=MATCHING_DOC)
    assert "does not corroborate" in str(exc_info.value)
