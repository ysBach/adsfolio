from __future__ import annotations

import io
from pathlib import Path

import fitz
import pytest
from pypdf import PdfReader, PdfWriter

from adsfolio.pdf import identity
from adsfolio.pdf.identity import (
    IDENTITY_KEY,
    IdentityConflictError,
    IdentityValidationError,
    UnverifiedIdentityError,
    assert_mutation_allowed,
    extract_pdf_own_identity,
)


def xmp_packet(doi: str, prefix: str = "prism") -> str:
    return (
        "<x:xmpmeta xmlns:x='adobe:ns:meta/'><rdf:RDF "
        "xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>"
        f"<rdf:Description xmlns:{prefix}='http://prismstandard.org/namespaces/basic/2.0/'>"
        f"<{prefix}:doi>{doi}</{prefix}:doi></rdf:Description></rdf:RDF></x:xmpmeta>"
    )


def make_pdf(
    path: Path,
    *,
    stamp: str | None = None,
    body: str = "A paper about stars. See arXiv:9999.0001 and doi 10.9999/body.ref.",
    docinfo: dict[str, str] | None = None,
    xmp: str | None = None,
) -> Path:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 100), body)
    if stamp:
        page.insert_text((30, 500), stamp, rotate=90)
    if xmp:
        document.set_xml_metadata(xmp)
    data = document.tobytes()
    document.close()
    if docinfo:
        writer = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
        writer.add_metadata(docinfo)
        buffer = io.BytesIO()
        writer.write(buffer)
        data = buffer.getvalue()
    path.write_bytes(data)
    return path


STAMP = "arXiv:1208.4606v2 [astro-ph.EP] 2 Oct 2012"
ROBITAILLE = {"bibcode": "2012A&A...545A..88R", "identifier": ["arXiv:1208.4606"]}
KOHN = {"bibcode": "1965PhRv..140.1133K", "identifier": ["arXiv:1601.00001"]}
# The real Kohn ADS record: a DOI and no arXiv alias.
KOHN_ACTUAL = {"bibcode": "1965PhRv..140.1133K", "doi": ["10.1103/PhysRev.140.A1133"]}


def verify(pdf, record, **kwargs):
    return assert_mutation_allowed(pdf, record, provider_document=record, **kwargs)


def test_rotated_margin_stamp_is_own_identity_and_body_arxiv_is_not(tmp_path):
    pdf = make_pdf(tmp_path / "paper.pdf", stamp=STAMP)
    own = extract_pdf_own_identity(pdf)
    assert own.arxiv_ids == frozenset({"1208.4606"})
    assert own.identifiers == frozenset({("arxiv", "1208.4606")})
    assert own.dois == frozenset()
    assert own.errors == ()


def test_horizontal_arxiv_citation_and_body_doi_are_not_identity(tmp_path):
    pdf = make_pdf(tmp_path / "paper.pdf", body="arXiv:1111.2222 doi:10.1000/ref")
    assert not extract_pdf_own_identity(pdf).has_identifier


def test_provider_aliases_verify_across_versions_and_bibcode_forms(tmp_path):
    pdf = make_pdf(tmp_path / "paper.pdf", stamp=STAMP)
    for record in (
        ROBITAILLE,
        {"bibcode": "x", "identifier": ["arXiv:1208.4606v1"]},
        {
            "bibcode": "2012A&A...545A..88R",
            "alternate_bibcode": ["2012arXiv1208.4606R"],
        },
        {"bibcode": "2012A&A...545A..88R", "arxiv": "1208.4606v3"},
    ):
        provenance = verify(pdf, record)
        assert provenance["status"] == "verified"
        assert provenance["authority"] == "provider_document"
        assert provenance["own_arxiv"] == ["1208.4606"]
        assert provenance["sha256"] == extract_pdf_own_identity(pdf).sha256


def test_matching_metadata_alone_does_not_verify(tmp_path):
    pdf = make_pdf(tmp_path / "paper.pdf", stamp=STAMP)
    with pytest.raises(UnverifiedIdentityError) as info:
        assert_mutation_allowed(pdf, ROBITAILLE)
    assert not info.value.manually_confirmable


def test_contradicting_provider_conflicts_even_with_manual_confirmation(tmp_path):
    pdf = make_pdf(tmp_path / "paper.pdf", stamp=STAMP)
    with pytest.raises(IdentityConflictError):
        verify(pdf, KOHN)
    with pytest.raises(IdentityConflictError):
        verify(pdf, KOHN, manual_confirmation=True)


def test_real_kohn_record_without_arxiv_cannot_be_forced_by_manual_flag(tmp_path):
    pdf = make_pdf(tmp_path / "Robitaille2012-1208.4606.pdf", stamp=STAMP)
    for manual in (False, True):
        with pytest.raises(UnverifiedIdentityError) as info:
            assert_mutation_allowed(
                pdf,
                KOHN_ACTUAL,
                provider_document=KOHN_ACTUAL,
                manual_confirmation=manual,
            )
        assert not info.value.manually_confirmable


def test_wrong_primary_with_correct_secondary_arxiv_is_blocked(tmp_path):
    pdf = make_pdf(tmp_path / "paper.pdf", stamp=STAMP)
    mixed = {**KOHN_ACTUAL, "identifier": ["arXiv:1208.4606"]}
    # The actual provider record for the wrong primary does not carry the arXiv id.
    for manual in (False, True):
        with pytest.raises(IdentityValidationError):
            assert_mutation_allowed(
                pdf, mixed, provider_document=KOHN_ACTUAL, manual_confirmation=manual
            )
    # Offering the right paper's record as provider for the wrong primary fails too.
    with pytest.raises(IdentityValidationError):
        assert_mutation_allowed(pdf, mixed, provider_document=ROBITAILLE)
    # Without a provider the merged secondary alias cannot verify.
    with pytest.raises(UnverifiedIdentityError):
        assert_mutation_allowed(pdf, mixed)
    with pytest.raises(UnverifiedIdentityError):
        assert_mutation_allowed(pdf, mixed, manual_confirmation=True)


def test_legitimate_multiple_dois_in_one_provider_record_verify(tmp_path):
    dois = ["10.1051/0004-6361/201219212", "10.1051/0004-6361/201219212e"]
    record = {
        "bibcode": "2012A&A...545A..88R",
        "doi": dois,
        "identifier": ["arXiv:1208.4606", "arXiv:1208.4606v2", *dois],
    }
    pdf = make_pdf(tmp_path / "paper.pdf", stamp=STAMP, docinfo={"/DOI": dois[1]})
    assert verify(pdf, record)["status"] == "verified"


def test_pdf_without_own_identifier_is_unverified_until_manual(tmp_path):
    pdf = make_pdf(tmp_path / "paper.pdf")
    with pytest.raises(UnverifiedIdentityError) as info:
        verify(pdf, ROBITAILLE)
    assert info.value.manually_confirmable
    assert info.value.candidate_bibcode == ROBITAILLE["bibcode"]
    provenance = verify(pdf, ROBITAILLE, manual_confirmation=True)
    assert provenance["status"] == "manual_confirmed"
    assert provenance["authority"] == "manual"


def test_non_ads_candidate_does_not_offer_an_ads_entry(tmp_path):
    pdf = make_pdf(tmp_path / "paper.pdf")
    with pytest.raises(UnverifiedIdentityError) as info:
        verify(pdf, {"doi": ["10.5555/example"]})
    assert info.value.candidate_bibcode is None


@pytest.mark.parametrize("payload", [b"", b"not a pdf", b"%PDF-1.7\n1 0 obj<<"])
def test_unparseable_pdf_fails_closed(tmp_path, payload):
    pdf = tmp_path / "broken.pdf"
    pdf.write_bytes(payload)
    assert extract_pdf_own_identity(pdf).errors
    with pytest.raises(UnverifiedIdentityError):
        verify(pdf, ROBITAILLE)


def test_missing_file_raises_validation_error(tmp_path):
    with pytest.raises(IdentityValidationError):
        verify(tmp_path / "gone.pdf", ROBITAILLE)


def test_docinfo_and_xmp_doi_are_own_identity_and_keep_punctuation(tmp_path):
    doi = "10.1051/0004-6361/201219212."
    pdf = make_pdf(
        tmp_path / "paper.pdf",
        docinfo={"/DOI": f"doi:{doi.upper()}"},
        xmp=xmp_packet(doi),
    )
    assert extract_pdf_own_identity(pdf).dois == frozenset({doi})
    record = {"bibcode": "2012A&A...545A..88R", "doi": [doi]}
    assert verify(pdf, record)["status"] == "verified"
    with pytest.raises(IdentityConflictError):
        verify(pdf, {"bibcode": "b", "doi": ["10.1000/other"]})
    # Dropping the final period names a different DOI.
    with pytest.raises(IdentityConflictError):
        verify(pdf, {"bibcode": "b", "doi": [doi.rstrip(".")]})


def test_docinfo_reference_lists_are_not_own_identity(tmp_path):
    pdf = make_pdf(
        tmp_path / "paper.pdf",
        docinfo={"/DOIReferences": "10.1000/cited", "/Subject": "doi:10.1000/subj"},
    )
    assert not extract_pdf_own_identity(pdf).has_identifier


@pytest.mark.parametrize(
    "prefix", ["doi:", "DOI: ", "https://doi.org/", "https://dx.doi.org/"]
)
def test_complete_doi_in_title_metadata_is_own_identity(
    tmp_path: Path, prefix: str
) -> None:
    doi = "10.1000/own.(paper)."
    pdf = make_pdf(tmp_path / "paper.pdf", docinfo={"/Title": f"{prefix}{doi}"})
    original = pdf.read_bytes()
    assert extract_pdf_own_identity(pdf).dois == frozenset({doi})
    record = {"bibcode": "2024MNRAS.529.3521P", "doi": [doi]}
    assert verify(pdf, record)["status"] == "verified"
    with pytest.raises(IdentityConflictError):
        verify(pdf, {**record, "doi": ["10.1000/other"]})
    assert pdf.read_bytes() == original


@pytest.mark.parametrize(
    "title",
    [
        "A discussion of doi:10.1000/cited",
        "doi:10.1000/cited in a paper title",
        "doi:10.1000/one doi:10.1000/two",
        "https://doi.org/10.1000/cited extra text",
        "10.1000/unlabeled",
    ],
)
def test_doi_mentioned_inside_title_does_not_establish_identity(
    tmp_path: Path, title: str
) -> None:
    pdf = make_pdf(tmp_path / "paper.pdf", docinfo={"/Title": title})
    assert not extract_pdf_own_identity(pdf).has_identifier


def test_title_doi_does_not_override_other_metadata_conflict(tmp_path: Path) -> None:
    pdf = make_pdf(
        tmp_path / "paper.pdf",
        docinfo={
            "/Title": "doi:10.1000/right",
            "/DOI": "10.1000/wrong",
        },
    )
    with pytest.raises(IdentityConflictError):
        verify(pdf, {"bibcode": "b", "doi": ["10.1000/right"]})


def test_xmp_prefix_may_vary_and_bad_xmp_does_not_hide_matching_stamp(tmp_path):
    odd = make_pdf(tmp_path / "odd.pdf", xmp=xmp_packet("10.1000/odd", prefix="zz"))
    assert extract_pdf_own_identity(odd).dois == frozenset({"10.1000/odd"})
    bad = make_pdf(tmp_path / "bad.pdf", xmp="<x:xmpmeta><unclosed>", stamp=STAMP)
    assert extract_pdf_own_identity(bad).errors
    assert verify(bad, ROBITAILLE)["status"] == "verified"


@pytest.mark.parametrize("docinfo_doi", [None, "10.1000/right"])
@pytest.mark.parametrize("xmp_doi", ["10.1000/right", "10.1000/wrong"])
def test_undeclared_unrelated_xmp_prefix_does_not_hide_doi_or_conflict(
    tmp_path: Path, docinfo_doi: str | None, xmp_doi: str
) -> None:
    packet = xmp_packet(xmp_doi, prefix="journal").replace(
        "<journal:doi>", "<pdf:Keywords>comets</pdf:Keywords><journal:doi>"
    )
    pdf = make_pdf(
        tmp_path / "paper.pdf",
        xmp=packet,
        docinfo={"/DOI": docinfo_doi} if docinfo_doi else None,
    )
    before = pdf.read_bytes()
    record = {"bibcode": "2024MNRAS.529.3521P", "doi": ["10.1000/right"]}
    own = extract_pdf_own_identity(pdf)
    assert xmp_doi in own.dois
    assert own.warnings == ("XMP metadata is not well-formed",)
    assert own.blocking_errors == ()
    if xmp_doi == "10.1000/wrong":
        with pytest.raises(IdentityConflictError):
            verify(pdf, record)
    else:
        proof = verify(pdf, record)
        assert proof["status"] == "verified"
        assert proof["parser_warnings"] == list(own.warnings)
        stored = {**record, IDENTITY_KEY: proof}
        assert assert_mutation_allowed(pdf, stored)["authority"] == "stored_proof"
    assert pdf.read_bytes() == before


def test_undeclared_identifier_prefix_cannot_establish_own_identity(
    tmp_path: Path,
) -> None:
    pdf = make_pdf(
        tmp_path / "paper.pdf",
        xmp="<root><prism:doi>10.1000/right</prism:doi></root>",
    )
    assert not extract_pdf_own_identity(pdf).has_identifier
    with pytest.raises(UnverifiedIdentityError):
        verify(pdf, {"bibcode": "b", "doi": ["10.1000/right"]})


def test_xmp_namespace_recovery_respects_nested_prefix_bindings(tmp_path: Path) -> None:
    packet = (
        "<root xmlns:p='http://prismstandard.org/namespaces/basic/3.0/'>"
        "<unknown:Keywords>comets</unknown:Keywords><p:doi>10.1000/right</p:doi>"
        "<other xmlns:p='https://example.org/unrelated'><p:doi>10.1000/wrong</p:doi></other>"
        "</root>"
    )
    pdf = make_pdf(tmp_path / "paper.pdf", xmp=packet)
    assert extract_pdf_own_identity(pdf).dois == frozenset({"10.1000/right"})
    assert (
        verify(pdf, {"bibcode": "b", "doi": ["10.1000/right"]})["status"] == "verified"
    )


@pytest.mark.parametrize("bad_source", ["DocInfo", "XMP"])
def test_malformed_own_identifier_still_blocks_a_matching_other_source(
    tmp_path: Path, bad_source: str
) -> None:
    pdf = make_pdf(
        tmp_path / "paper.pdf",
        docinfo={"/DOI": "not a DOI" if bad_source == "DocInfo" else "10.1000/right"},
        xmp=xmp_packet("not a DOI" if bad_source == "XMP" else "10.1000/right"),
    )
    with pytest.raises(UnverifiedIdentityError):
        verify(pdf, {"bibcode": "2024MNRAS.529.3521P", "doi": ["10.1000/right"]})


@pytest.mark.parametrize("failure", ["unverified", "conflict", "provider"])
def test_identity_errors_preserve_bibcode_case_without_changing_identity(
    tmp_path: Path, failure: str
) -> None:
    record = {"bibcode": "2024MNRAS.529.3521P", "doi": ["10.1000/right"]}
    target = identity.target_identity(record)
    normalized = identity.target_identity(
        {**record, "bibcode": record["bibcode"].lower()}
    )
    assert target.primary == normalized.primary
    assert target.fingerprint == normalized.fingerprint
    pdf = make_pdf(
        tmp_path / "paper.pdf",
        docinfo={"/DOI": "10.1000/wrong"} if failure == "conflict" else None,
    )
    provider = (
        {**record, "bibcode": "2024A&A...999A...1A"}
        if failure == "provider"
        else record
    )
    with pytest.raises(IdentityValidationError) as info:
        assert_mutation_allowed(pdf, record, provider_document=provider)
    assert "2024MNRAS.529.3521P" in str(info.value)
    if failure == "provider":
        assert "2024A&A...999A...1A" in str(info.value)


def test_all_own_identifiers_must_agree(tmp_path):
    pdf = make_pdf(
        tmp_path / "paper.pdf", stamp=STAMP, docinfo={"/DOI": "10.1000/right"}
    )
    record = {"bibcode": "b", "doi": "10.1000/right", "identifier": ["arXiv:9998.0002"]}
    with pytest.raises(IdentityConflictError):
        verify(pdf, record)
    only_doi = {"bibcode": "b", "doi": "10.1000/right"}
    with pytest.raises(UnverifiedIdentityError):
        verify(pdf, only_doi)


def test_expected_hash_must_match_current_file(tmp_path):
    pdf = make_pdf(tmp_path / "paper.pdf", stamp=STAMP)
    stale = extract_pdf_own_identity(pdf).sha256
    make_pdf(pdf, stamp=STAMP, body="different bytes")
    with pytest.raises(IdentityValidationError, match="expected content hash"):
        verify(pdf, ROBITAILLE, expected_sha256=stale)


def test_file_changing_during_read_is_rejected(tmp_path, monkeypatch):
    pdf = make_pdf(tmp_path / "paper.pdf", stamp=STAMP)
    original = Path.read_bytes

    def racing_read(self):
        data = original(self)
        self.write_bytes(data + b"\n%appended")
        return data

    monkeypatch.setattr(Path, "read_bytes", racing_read)
    with pytest.raises(IdentityValidationError, match="changed"):
        extract_pdf_own_identity(pdf)


def test_manual_stamp_is_bound_to_file_hash_and_record(tmp_path):
    pdf = make_pdf(tmp_path / "a.pdf")
    stamp = verify(pdf, ROBITAILLE, manual_confirmation=True)
    trusted = {**ROBITAILLE, IDENTITY_KEY: stamp}
    assert assert_mutation_allowed(pdf, trusted)["status"] == "manual_confirmed"

    with pytest.raises(UnverifiedIdentityError):
        assert_mutation_allowed(pdf, {**KOHN, IDENTITY_KEY: stamp})
    changed = make_pdf(tmp_path / "b.pdf", body="another document")
    with pytest.raises(UnverifiedIdentityError):
        assert_mutation_allowed(changed, trusted)


def test_manual_stamp_never_hides_a_conflict_or_unconfirmed_own_identifier(tmp_path):
    pdf = make_pdf(tmp_path / "a.pdf", stamp=STAMP)
    sha = extract_pdf_own_identity(pdf).sha256
    for record in (KOHN, KOHN_ACTUAL):
        forged = {
            **record,
            IDENTITY_KEY: {
                "status": "manual_confirmed",
                "sha256": sha,
                "identity": identity.target_identity(record).fingerprint,
            },
        }
        with pytest.raises(IdentityValidationError):
            assert_mutation_allowed(pdf, forged)
        with pytest.raises(IdentityValidationError):
            assert_mutation_allowed(pdf, forged, provider_document=record)


def test_stored_verified_proof_needs_same_file_and_primary(tmp_path):
    pdf = make_pdf(tmp_path / "a.pdf", stamp=STAMP)
    proof = verify(pdf, ROBITAILLE)
    again = assert_mutation_allowed(pdf, {**ROBITAILLE, IDENTITY_KEY: proof})
    assert again["authority"] == "stored_proof"

    other_pdf = make_pdf(tmp_path / "b.pdf", stamp=STAMP, body="edited")
    with pytest.raises(UnverifiedIdentityError):
        assert_mutation_allowed(other_pdf, {**ROBITAILLE, IDENTITY_KEY: proof})
    wrong_primary = {
        "bibcode": "2020WrongPrimary.1....1X",
        "identifier": ["arXiv:1208.4606"],
    }
    with pytest.raises(UnverifiedIdentityError):
        assert_mutation_allowed(pdf, {**wrong_primary, IDENTITY_KEY: proof})


def test_hand_entered_metadata_without_ids_is_manual_and_bound_to_its_fields(tmp_path):
    pdf = make_pdf(tmp_path / "a.pdf", stamp=STAMP)
    entered = {"title": ["My Notes"], "author": ["Doe, J."], "year": "2020"}
    with pytest.raises(UnverifiedIdentityError) as info:
        assert_mutation_allowed(pdf, entered)
    assert info.value.manually_confirmable
    proof = assert_mutation_allowed(pdf, entered, manual_confirmation=True)
    assert proof["status"] == "manual_confirmed"
    assert proof["target"].startswith("bib:")

    assert assert_mutation_allowed(pdf, {**entered, IDENTITY_KEY: proof})
    with pytest.raises(UnverifiedIdentityError):
        assert_mutation_allowed(
            pdf, {**entered, "title": ["Other"], IDENTITY_KEY: proof}
        )


def test_alternate_bibcode_primary_must_be_listed_by_provider(tmp_path):
    pdf = make_pdf(tmp_path / "a.pdf", stamp=STAMP)
    provider = {**ROBITAILLE, "alternate_bibcode": ["2012arXiv1208.4606R"]}
    legit = {"bibcode": "2012arXiv1208.4606R", "identifier": ["arXiv:1208.4606"]}
    assert assert_mutation_allowed(pdf, legit, provider_document=provider)
    unlisted = {"bibcode": "2012arXiv1208.4606X", "identifier": ["arXiv:1208.4606"]}
    with pytest.raises(IdentityValidationError):
        assert_mutation_allowed(pdf, unlisted, provider_document=provider)


def test_proof_is_invalidated_by_any_changed_claimed_identifier(tmp_path):
    pdf = make_pdf(tmp_path / "a.pdf", stamp=STAMP)
    proof = verify(pdf, ROBITAILLE)
    stored = {**ROBITAILLE, IDENTITY_KEY: proof}
    assert assert_mutation_allowed(pdf, stored)["authority"] == "stored_proof"
    edited = {**stored, "doi": "10.1000/wrong-secondary"}
    with pytest.raises(UnverifiedIdentityError):
        assert_mutation_allowed(pdf, edited)
    # The edit is rejected by a fresh provider document that lacks it...
    with pytest.raises(IdentityValidationError):
        assert_mutation_allowed(pdf, edited, provider_document=ROBITAILLE)
    # ...and a fresh provider document that lists it re-verifies.
    fuller = {**ROBITAILLE, "doi": "10.1000/wrong-secondary"}
    assert assert_mutation_allowed(pdf, edited, provider_document=fuller)


def test_manual_proof_after_parse_error_needs_explicit_confirmation(tmp_path):
    pdf = tmp_path / "broken.pdf"
    pdf.write_bytes(b"not a pdf")
    proof = verify(pdf, ROBITAILLE, manual_confirmation=True)
    stored = {**ROBITAILLE, IDENTITY_KEY: proof}
    with pytest.raises(UnverifiedIdentityError):
        assert_mutation_allowed(pdf, stored)
    assert assert_mutation_allowed(pdf, stored, manual_confirmation=True)


def test_arxiv_primary_must_match_own_eprint_despite_aliases(tmp_path):
    pdf = make_pdf(tmp_path / "a.pdf", stamp=STAMP)
    other = {
        "bibcode": "2013arXiv1301.00001B",
        "identifier": ["arXiv:1301.00001", "arXiv:1208.4606"],
    }
    for manual in (False, True):
        with pytest.raises(IdentityConflictError):
            assert_mutation_allowed(
                pdf, other, provider_document=other, manual_confirmation=manual
            )
    exact = {"bibcode": "2012arXiv1208.4606R", "identifier": ["arXiv:1208.4606"]}
    assert verify(pdf, exact)["status"] == "verified"


def test_journal_primary_may_list_several_preprint_and_doi_aliases(tmp_path):
    pdf = make_pdf(tmp_path / "a.pdf", stamp=STAMP)
    journal = {
        "bibcode": "2012A&A...545A..88R",
        "doi": ["10.1051/a", "10.1051/b"],
        "identifier": ["arXiv:1208.4606", "arXiv:1208.9999"],
    }
    assert verify(pdf, journal)["status"] == "verified"
