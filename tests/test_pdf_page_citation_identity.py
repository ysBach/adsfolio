"""Check the page-1 citation rule for PDFs that state no identifier."""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from adsfolio.pdf.document_identity_evidence import (
    PAGE_CITATION_AUTHORITY,
    check_page_citation_evidence,
)
from adsfolio.pdf.identifier import PdfIdentifier
from adsfolio.pdf.identity import (
    IDENTITY_KEY,
    UnverifiedIdentityError,
    assert_mutation_allowed,
)

# Fictional record for a scan from before DOIs.
BIBCODE = "1987Icar...71..123Q"
DOC = {
    "bibcode": BIBCODE,
    "identifier": [BIBCODE, "10.1016/0019-1035(87)90123-4"],
    "doi": ["10.1016/0019-1035(87)90123-4"],
    "title": ["Fictional photometry of icy satellites"],
    "author": ["Quill, Avery", "Doe, Jordan"],
    "year": "1987",
    "volume": "71",
    "page": ["123"],
    "pub": "Icarus",
}
PAGE = (
    "ICARUS 71, 123--140 (1987)\n"
    "Fictional Photometry of Icy\n"
    "Satellites\n"
    "AVERY QUILL AND JORDAN DOE\n"
    "Received March 3, 1986\n"
    "We model the photometry of 12 icy surfaces.\n"
)


def make_scan(path: Path, text: str = PAGE, subject: str = "") -> Path:
    """Create a PDF with page-1 text and no identifier in its metadata."""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 72), text, fontsize=10)
    doc.set_metadata({"title": "PII: 0019-1035(87)90123-4", "subject": subject})
    doc.save(path)
    doc.close()
    return path


def test_page_citation_matches_title_author_year_volume_and_page(
    tmp_path: Path,
) -> None:
    pdf = make_scan(tmp_path / "scan.pdf")
    assert check_page_citation_evidence(pdf.read_bytes(), DOC).matched


def test_page_citation_joins_a_title_word_hyphenated_across_lines(
    tmp_path: Path,
) -> None:
    text = PAGE.replace(
        "Fictional Photometry of Icy\nSatellites",
        "Fictional Photo-\nmetry of Icy Satellites",
    )
    pdf = make_scan(tmp_path / "scan.pdf", text)
    assert check_page_citation_evidence(pdf.read_bytes(), DOC).matched


@pytest.mark.parametrize(
    ("text", "document", "reason"),
    [
        (PAGE.replace("ICARUS 71, 123", "ICARUS 72, 123"), DOC, "volume 71"),
        (PAGE.replace("ICARUS 71, 123", "ICARUS 71,\n123"), DOC, "on one line"),
        (PAGE.replace("ICARUS 71, 123", "ICARUS 171, 123"), DOC, "volume 71"),
        (PAGE.replace("Icy", "Cold"), DOC, "full provider title"),
        (PAGE.replace("AVERY QUILL", "AVERY QUILLS"), DOC, "surname (quill)"),
        (PAGE.replace("(1987)", "(1988)"), DOC, "year 1987"),
        (PAGE + "doi:10.1016/0019-1035(87)90999-9\n", DOC, "does not list"),
        (PAGE, {**DOC, "volume": None}, "lacks the title"),
    ],
)
def test_page_citation_refuses_each_missing_condition(
    tmp_path: Path, text: str, document: dict, reason: str
) -> None:
    pdf = make_scan(tmp_path / "scan.pdf", text)
    result = check_page_citation_evidence(pdf.read_bytes(), document)
    assert not result.matched
    assert reason in result.reason


def test_page_citation_allows_a_footnote_mark_after_the_title(tmp_path: Path) -> None:
    pdf = make_scan(
        tmp_path / "scan.pdf", PAGE.replace("Satellites\n", "Satellites 1*\n")
    )
    assert check_page_citation_evidence(pdf.read_bytes(), DOC).matched


@pytest.mark.parametrize(
    "text",
    [
        # An addendum repeats the original title and citation on page 1.
        "ADDENDUM: FICTIONAL PHOTOMETRY OF ICY SATELLITES\n"
        "[ICARUS 71, 123 (1987)]\nAVERY QUILL\nWe correct one table.\n",
        # Another paper by the same author quotes the title in a sentence and
        # cites it in its references.
        "ICARUS 72, 10--20 (1987)\nA Second Fictional Paper\nAVERY QUILL\n"
        "We saw fictional photometry of icy satellites 1 in new data.\n"
        "1. Quill, A. Icarus 71, 123 (1987).\n",
    ],
)
def test_page_citation_ignores_a_title_inside_other_text(
    tmp_path: Path, text: str
) -> None:
    pdf = make_scan(tmp_path / "scan.pdf", text)
    result = check_page_citation_evidence(pdf.read_bytes(), DOC)
    assert not result.matched
    assert "on its own lines" in result.reason


@pytest.mark.parametrize(
    "heading",
    ["ERRATUM", "Errata", "CORRIGENDUM:", "Correction", "Addendum 1", "RETRACTION"],
)
def test_page_citation_refuses_a_correction_notice_heading(
    tmp_path: Path, heading: str
) -> None:
    """A notice that repeats the original title and citation must not match."""
    text = (
        f"{heading}\nFICTIONAL PHOTOMETRY OF ICY SATELLITES\n"
        "[ICARUS 71, 123 (1987)]\nAVERY QUILL\nTable 2 had a sign error.\n"
    )
    pdf = make_scan(tmp_path / "notice.pdf", text)
    result = check_page_citation_evidence(pdf.read_bytes(), DOC)
    assert not result.matched
    assert "correction notice" in result.reason

    # The needs-review notice leads with the warning and keeps the candidate.
    with pytest.raises(UnverifiedIdentityError) as info:
        assert_mutation_allowed(pdf, DOC, provider_document=DOC)
    message = str(info.value)
    assert message.index("Warning: page 1 has the heading") < message.index(
        "no explicitly labelled DOI"
    )
    assert info.value.candidate_bibcode == BIBCODE
    assert info.value.manually_confirmable


def test_page_citation_allows_a_heading_word_inside_a_sentence(
    tmp_path: Path,
) -> None:
    pdf = make_scan(tmp_path / "scan.pdf", PAGE + "We apply a correction for phase.\n")
    assert check_page_citation_evidence(pdf.read_bytes(), DOC).matched


def test_page_citation_verifies_and_saved_proof_is_reused(tmp_path: Path) -> None:
    pdf = make_scan(tmp_path / "scan.pdf")
    proof = assert_mutation_allowed(pdf, DOC, provider_document=DOC)
    assert proof["status"] == "verified"
    assert proof["authority"] == PAGE_CITATION_AUTHORITY

    # Later checks without the provider record reuse the proof for this file.
    reused = assert_mutation_allowed(pdf, {**DOC, IDENTITY_KEY: proof})
    assert reused["status"] == "verified"
    assert reused["authority"] == PAGE_CITATION_AUTHORITY

    # A changed file does not inherit the proof.
    make_scan(pdf, PAGE + "Added page text.\n")
    with pytest.raises(UnverifiedIdentityError):
        assert_mutation_allowed(pdf, {**DOC, IDENTITY_KEY: proof})


def test_identifier_builds_a_verified_record_from_the_page_citation(
    tmp_path: Path,
) -> None:
    pdf = make_scan(tmp_path / "scan.pdf")
    record = PdfIdentifier(None).build_record(DOC, pdf)
    assert record["bibcode"] == BIBCODE
    assert record[IDENTITY_KEY]["status"] == "verified"
    assert record[IDENTITY_KEY]["authority"] == PAGE_CITATION_AUTHORITY


def test_page_citation_does_not_apply_when_metadata_claims_a_doi(
    tmp_path: Path,
) -> None:
    """A PDF that claims a DOI must pass the DOI check instead."""
    pdf = make_scan(
        tmp_path / "scan.pdf", subject="Icarus 71. doi:10.1016/0019-1035(87)90123-4"
    )
    with pytest.raises(UnverifiedIdentityError) as info:
        assert_mutation_allowed(pdf, DOC, provider_document=DOC)
    assert "page 1 does not repeat that exact DOI" in str(info.value)
