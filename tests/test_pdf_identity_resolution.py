from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest
from pypdf import PdfReader, PdfWriter

from adsfolio.pdf.identifier import PdfIdentifier
from adsfolio.pdf.identify_settings import (
    RECOMMENDED_IDENTIFY_STRATEGIES,
    IdentifyStrategy,
)
from adsfolio.pdf.identity import (
    IDENTITY_KEY,
    IdentityConflictError,
    UnverifiedIdentityError,
)


def make_pdf(
    path: Path,
    *,
    stamp: str | None = None,
    body: str = "A paper about stars. See arXiv:9999.0001 and doi 10.9999/body.ref.",
    doi: str | None = None,
    xmp_doi: str | None = None,
) -> Path:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 100), body)
    if stamp:
        page.insert_text((30, 500), stamp, rotate=90)
    if xmp_doi:
        document.set_xml_metadata(
            "<x:xmpmeta xmlns:x='adobe:ns:meta/'><rdf:RDF "
            "xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>"
            "<rdf:Description xmlns:prism='http://prismstandard.org/namespaces/basic/2.0/'>"
            f"<prism:doi>{xmp_doi}</prism:doi></rdf:Description></rdf:RDF></x:xmpmeta>"
        )
    data = document.tobytes()
    document.close()
    if doi:
        writer = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
        writer.add_metadata({"/DOI": doi})
        buffer = io.BytesIO()
        writer.write(buffer)
        data = buffer.getvalue()
    path.write_bytes(data)
    return path


STAMP = "arXiv:1208.4606v2 [astro-ph.EP] 2 Oct 2012"
KOHN_BIBCODE = "2016ApJ...823...76K"
KOHN = {"bibcode": KOHN_BIBCODE, "identifier": ["arXiv:1601.00001"], "title": ["Kohn"]}
ROBITAILLE = {
    "bibcode": "2012A&A...545A..88R",
    "identifier": ["arXiv:1208.4606", "2012arXiv1208.4606R"],
    "title": ["Robitaille"],
}


class FakeClient:
    def __init__(self, docs):
        self.docs = docs
        self.queries: list[str] = []
        self.search = SimpleNamespace(query=self._query)

    def _query(self, q, fl, rows):
        self.queries.append(q)
        return SimpleNamespace(docs=self.docs, num_found=len(self.docs))


def kohn_named_pdf(tmp_path: Path, **kwargs) -> Path:
    return make_pdf(
        tmp_path / f"{KOHN_BIBCODE}-Robitaille2012-arxiv1208.4606.pdf", **kwargs
    )


def cached_kohn(kind, value):
    return (True, KOHN) if kind == "bibcode" else (False, None)


def test_filename_bibcode_cannot_associate_pdf_with_contradicting_record(tmp_path):
    pdf = kohn_named_pdf(tmp_path, stamp=STAMP)
    engine = PdfIdentifier()
    result = engine.resolve(
        pdf,
        cached_candidate_lookup=cached_kohn,
        allow_network_lookup=False,
        strategies=(IdentifyStrategy.FILENAME_BIBCODE,),
    )
    assert result is None
    assert isinstance(engine.identity_failures[pdf], IdentityConflictError)


def test_filename_matched_record_for_pdf_without_identifier_is_unverified(tmp_path):
    pdf = kohn_named_pdf(tmp_path)
    engine = PdfIdentifier()
    assert (
        engine.resolve(
            pdf, cached_candidate_lookup=cached_kohn, allow_network_lookup=False
        )
        is None
    )
    assert isinstance(engine.identity_failures[pdf], UnverifiedIdentityError)


def test_unparseable_pdf_is_recorded_as_failure(tmp_path):
    pdf = tmp_path / f"{KOHN_BIBCODE}.pdf"
    pdf.write_bytes(b"garbage")
    engine = PdfIdentifier()
    assert engine.resolve(pdf, cached_candidate_lookup=cached_kohn) is None
    assert isinstance(engine.identity_failures[pdf], UnverifiedIdentityError)


def test_own_arxiv_stamp_resolves_the_correct_record_after_filename_conflict(tmp_path):
    pdf = kohn_named_pdf(tmp_path, stamp=STAMP)
    client = FakeClient([ROBITAILLE, KOHN])
    engine = PdfIdentifier(client=client)
    record = engine.resolve(
        pdf,
        cached_candidate_lookup=cached_kohn,
        strategies=(
            IdentifyStrategy.FILENAME_BIBCODE,
            IdentifyStrategy.FIRST_PAGE_ARXIV,
        ),
    )
    assert record["bibcode"] == ROBITAILLE["bibcode"]
    assert record["resolution_strategy"] == "first_page_arxiv"
    assert record[IDENTITY_KEY]["status"] == "verified"
    assert 'identifier:"arXiv:1208.4606"' in client.queries[0]
    assert pdf not in engine.identity_failures


def test_recommended_strategies_start_with_own_content_and_cannot_be_bypassed(tmp_path):
    assert RECOMMENDED_IDENTIFY_STRATEGIES[0] is IdentifyStrategy.FIRST_PAGE_ARXIV
    pdf = kohn_named_pdf(tmp_path, stamp=STAMP)
    engine = PdfIdentifier(client=FakeClient([KOHN]))
    legacy_order = tuple(
        s for s in IdentifyStrategy if s is not IdentifyStrategy.FIRST_PAGE_ARXIV
    )
    assert (
        engine.resolve(
            pdf, cached_candidate_lookup=cached_kohn, strategies=legacy_order
        )
        is None
    )
    assert isinstance(engine.identity_failures[pdf], IdentityConflictError)


def test_arxiv_query_uses_only_uniquely_owned_aliases(tmp_path):
    pdf = make_pdf(tmp_path / "paper.pdf", stamp=STAMP)
    unrelated = {"bibcode": "2020Other.1....1X", "identifier": ["arXiv:2001.00001"]}
    clone = {**ROBITAILLE, "bibcode": "2012arXiv1208.4606R"}
    strategies = (IdentifyStrategy.FIRST_PAGE_ARXIV,)
    only_one = PdfIdentifier(client=FakeClient([unrelated, ROBITAILLE]))
    assert (
        only_one.resolve(pdf, strategies=strategies)["bibcode"] == ROBITAILLE["bibcode"]
    )
    ambiguous = PdfIdentifier(client=FakeClient([ROBITAILLE, clone]))
    assert ambiguous.resolve(pdf, strategies=strategies) is None
    none = PdfIdentifier(client=FakeClient([unrelated]))
    assert none.resolve(pdf, strategies=strategies) is None


def test_identity_failures_are_cleared_at_the_start_of_each_resolve_many(tmp_path):
    pdf = kohn_named_pdf(tmp_path, stamp=STAMP)
    engine = PdfIdentifier()
    kwargs = {
        "cached_candidate_lookup": cached_kohn,
        "allow_network_lookup": False,
        "strategies": (IdentifyStrategy.FILENAME_BIBCODE,),
    }
    engine.resolve_many([pdf], **kwargs)
    assert pdf in engine.identity_failures
    engine.resolve_many([tmp_path / "other.pdf"], **kwargs)
    assert pdf not in engine.identity_failures


def test_build_record_validates_and_stamps_provenance(tmp_path):
    verified = make_pdf(tmp_path / "v.pdf", stamp=STAMP)
    engine = PdfIdentifier()
    record = engine.build_record(ROBITAILLE, verified)
    assert record[IDENTITY_KEY]["status"] == "verified"
    assert record["sha256"] == record[IDENTITY_KEY]["sha256"]

    with pytest.raises(IdentityConflictError):
        engine.build_record(KOHN, verified, manual_confirmation=True)

    stale = record["sha256"]
    make_pdf(verified, stamp=STAMP, body="edited")
    with pytest.raises(ValueError, match="expected content hash"):
        engine.build_record(ROBITAILLE, verified, sha256=stale)


def test_build_record_manual_confirmation_only_admits_unverified(tmp_path):
    unknown = make_pdf(tmp_path / "u.pdf")
    engine = PdfIdentifier()
    with pytest.raises(UnverifiedIdentityError):
        engine.build_record(ROBITAILLE, unknown)
    record = engine.build_record(ROBITAILLE, unknown, manual_confirmation=True)
    assert record[IDENTITY_KEY]["status"] == "manual_confirmed"


def test_actual_kohn_record_without_arxiv_alias_is_not_forced_by_manual_flag(tmp_path):
    actual = {"bibcode": KOHN_BIBCODE, "doi": ["10.1103/PhysRev.140.A1133"]}
    pdf = kohn_named_pdf(tmp_path, stamp=STAMP)
    engine = PdfIdentifier()
    result = engine.resolve(
        pdf,
        cached_candidate_lookup=lambda kind, value: (kind == "bibcode", actual),
        allow_network_lookup=False,
        strategies=(IdentifyStrategy.FILENAME_BIBCODE,),
    )
    assert result is None
    failure = engine.identity_failures[pdf]
    assert isinstance(failure, UnverifiedIdentityError)
    assert not failure.manually_confirmable
    with pytest.raises(UnverifiedIdentityError):
        engine.build_record(actual, pdf, manual_confirmation=True)


def test_build_record_proof_matches_stored_projection_with_all_dois(tmp_path):
    from adsfolio.pdf.identity import assert_mutation_allowed

    pdf = make_pdf(tmp_path / "v.pdf", stamp=STAMP)
    doc = {**ROBITAILLE, "doi": ["10.1000/a", "10.1000/b"]}
    record = PdfIdentifier().build_record(doc, pdf)
    assert "10.1000/b" in record["identifier"]
    assert assert_mutation_allowed(pdf, record)["authority"] == "stored_proof"
    record["doi"] = "10.1000/mutated"
    with pytest.raises(UnverifiedIdentityError):
        assert_mutation_allowed(pdf, record)
