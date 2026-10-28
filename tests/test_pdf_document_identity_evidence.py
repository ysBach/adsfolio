"""Tests for PDF document identity evidence corroboration.

Covers portable synthetic regression tests covering matching titles,
mismatching titles, first-page prefix collisions (including . and :),
terminal punctuation preservation without heuristic trimming,
malformed inputs, and multiple conflicting subject DOIs.
"""

from __future__ import annotations

from adsfolio.pdf.document_identity_evidence import corroborate_document_doi_evidence


def _create_synthetic_pdf(
    *,
    title: str = "Test Title",
    subject: str = "A&A 123. DOI: 10.1051/0004-6361/202612345",
    page_text: str = "Test Title\nhttps://doi.org/10.1051/0004-6361/202612345\nAbstract",
) -> bytes:
    """Helper to generate an in-memory PDF using fitz."""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    if page_text:
        page.insert_text((72, 72), page_text, fontsize=11)
    metadata = {}
    if title is not None:
        metadata["title"] = title
    if subject is not None:
        metadata["subject"] = subject
    doc.set_metadata(metadata)
    data = doc.tobytes()
    doc.close()
    return data


def test_synthetic_pdf_full_mutual_corroboration() -> None:
    pdf_bytes = _create_synthetic_pdf(
        title="  A Study on Asteroids  ",
        subject="Journal XYZ. DOI: 10.1051/0004-6361/202611111",
        page_text="A Study on Asteroids\nDOI: 10.1051/0004-6361/202611111\nAbstract",
    )
    provider_doc = {
        "title": ["A study ON asteroids"],
        "doi": ["10.1051/0004-6361/202611111"],
    }
    res = corroborate_document_doi_evidence(pdf_bytes, provider_doc)
    assert res == "10.1051/0004-6361/202611111"


def test_refuses_when_provider_title_mismatches() -> None:
    pdf_bytes = _create_synthetic_pdf(
        title="A Study on Asteroids",
        subject="Journal XYZ. DOI: 10.1051/0004-6361/202611111",
        page_text="A Study on Asteroids\nDOI: 10.1051/0004-6361/202611111\nAbstract",
    )
    provider_doc = {
        "title": ["Completely Different Title Here"],
        "doi": ["10.1051/0004-6361/202611111"],
    }
    assert corroborate_document_doi_evidence(pdf_bytes, provider_doc) is None


def test_refuses_when_provider_doi_mismatches() -> None:
    pdf_bytes = _create_synthetic_pdf(
        title="A Study on Asteroids",
        subject="Journal XYZ. DOI: 10.1051/0004-6361/202611111",
        page_text="A Study on Asteroids\nDOI: 10.1051/0004-6361/202611111\nAbstract",
    )
    provider_doc = {
        "title": ["A Study on Asteroids"],
        "doi": ["10.1051/0004-6361/999999999"],
    }
    assert corroborate_document_doi_evidence(pdf_bytes, provider_doc) is None


def test_refuses_when_first_page_has_doi_prefix_collision() -> None:
    # Target DOI is 10.1051/abc, but page 1 only contains longer variants
    target_doi = "10.1051/0004-6361/202611111"
    provider_doc = {
        "title": ["A Study on Asteroids"],
        "doi": [target_doi],
    }
    # Test collision with alphanumeric extension
    pdf_bytes_alnum = _create_synthetic_pdf(
        title="A Study on Asteroids",
        subject=f"Journal XYZ. DOI: {target_doi}",
        page_text=f"A Study on Asteroids\nDOI: {target_doi}222\nAbstract",
    )
    assert corroborate_document_doi_evidence(pdf_bytes_alnum, provider_doc) is None

    # Test collision with dot extension: 10.1000/x.extra
    pdf_bytes_dot = _create_synthetic_pdf(
        title="A Study on Asteroids",
        subject=f"Journal XYZ. DOI: {target_doi}",
        page_text=f"A Study on Asteroids\nDOI: {target_doi}.extra\nAbstract",
    )
    assert corroborate_document_doi_evidence(pdf_bytes_dot, provider_doc) is None

    # Test collision with colon extension: 10.1000/x:extra
    pdf_bytes_colon = _create_synthetic_pdf(
        title="A Study on Asteroids",
        subject=f"Journal XYZ. DOI: {target_doi}",
        page_text=f"A Study on Asteroids\nDOI: {target_doi}:extra\nAbstract",
    )
    assert corroborate_document_doi_evidence(pdf_bytes_colon, provider_doc) is None

    # Test collision with slash extension: 10.1000/x/extra
    pdf_bytes_slash = _create_synthetic_pdf(
        title="A Study on Asteroids",
        subject=f"Journal XYZ. DOI: {target_doi}",
        page_text=f"A Study on Asteroids\nDOI: {target_doi}/extra\nAbstract",
    )
    assert corroborate_document_doi_evidence(pdf_bytes_slash, provider_doc) is None


def test_preserves_terminal_punctuation_in_doi_without_heuristic_trimming() -> None:
    # DOI legitimately ending with terminal period: 10.1234/test.
    doi_with_dot = "10.1234/test."
    pdf_bytes = _create_synthetic_pdf(
        title="Punctuation Test",
        subject=f"Journal. DOI: {doi_with_dot}",
        page_text=f"Punctuation Test\nDOI: {doi_with_dot}\nAbstract",
    )
    provider_doc_with_dot = {
        "title": ["Punctuation Test"],
        "doi": [doi_with_dot],
    }
    # Exact dotted DOI corroborates when provider and PDF agree
    res = corroborate_document_doi_evidence(pdf_bytes, provider_doc_with_dot)
    assert res == doi_with_dot

    # If provider expects DOI without the dot, the helper must FAIL CLOSED
    # rather than heuristic auto-trimming the period
    provider_doc_without_dot = {
        "title": ["Punctuation Test"],
        "doi": ["10.1234/test"],
    }
    assert (
        corroborate_document_doi_evidence(pdf_bytes, provider_doc_without_dot) is None
    )


def test_refuses_when_subject_doi_missing_from_first_page_text() -> None:
    pdf_bytes = _create_synthetic_pdf(
        title="A Study on Asteroids",
        subject="Journal XYZ. DOI: 10.1051/0004-6361/202611111",
        page_text="A Study on Asteroids\nNo DOI printed here\nAbstract",
    )
    provider_doc = {
        "title": ["A Study on Asteroids"],
        "doi": ["10.1051/0004-6361/202611111"],
    }
    assert corroborate_document_doi_evidence(pdf_bytes, provider_doc) is None


def test_refuses_when_subject_has_multiple_conflicting_dois() -> None:
    pdf_bytes = _create_synthetic_pdf(
        title="A Study on Asteroids",
        subject="DOI: 10.1051/0004-6361/202611111 and DOI: 10.1051/0004-6361/202622222",
        page_text="A Study on Asteroids\n10.1051/0004-6361/202611111",
    )
    provider_doc = {
        "title": ["A Study on Asteroids"],
        "doi": ["10.1051/0004-6361/202611111"],
    }
    assert corroborate_document_doi_evidence(pdf_bytes, provider_doc) is None


def test_refuses_when_subject_doi_not_explicitly_labeled() -> None:
    pdf_bytes = _create_synthetic_pdf(
        title="A Study on Asteroids",
        subject="Journal XYZ 10.1051/0004-6361/202611111",
        page_text="A Study on Asteroids\n10.1051/0004-6361/202611111",
    )
    provider_doc = {
        "title": ["A Study on Asteroids"],
        "doi": ["10.1051/0004-6361/202611111"],
    }
    assert corroborate_document_doi_evidence(pdf_bytes, provider_doc) is None


def test_first_page_doi_alone_without_matching_subject_does_not_corroborate() -> None:
    pdf_bytes = _create_synthetic_pdf(
        title="A Study on Asteroids",
        subject="Solar System Research",
        page_text="A Study on Asteroids\nReferences:\nSmith et al. 10.1051/0004-6361/202611111",
    )
    provider_doc = {
        "title": ["A Study on Asteroids"],
        "doi": ["10.1051/0004-6361/202611111"],
    }
    assert corroborate_document_doi_evidence(pdf_bytes, provider_doc) is None


def test_malformed_and_empty_inputs_fail_closed() -> None:
    provider_doc = {"title": ["Test"], "doi": ["10.1051/test"]}
    assert corroborate_document_doi_evidence(b"", provider_doc) is None
    assert corroborate_document_doi_evidence(b"not a pdf", provider_doc) is None
    assert (
        corroborate_document_doi_evidence(b"%PDF-1.4\ntruncated", provider_doc) is None
    )
    assert corroborate_document_doi_evidence(b"\x00" * 50, provider_doc) is None
    assert corroborate_document_doi_evidence("not bytes", provider_doc) is None  # type: ignore[arg-type]
    assert corroborate_document_doi_evidence(b"%PDF-1.4...", None) is None  # type: ignore[arg-type]
