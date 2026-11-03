"""Explicit PDF identifier corrections remain bound to the reviewed file."""

from pathlib import Path

import pytest
from pdf_identity_fixtures import write_pdf

from adsfolio.pdf.files import sha256_file
from adsfolio.pdf.identity import (
    IDENTITY_KEY,
    IdentityConflictError,
    IdentityValidationError,
    assert_mutation_allowed,
)
from adsfolio.pdf.renamer import verify_record_identity


def test_reviewed_conflict_allows_only_the_same_file_and_record(tmp_path: Path) -> None:
    pdf = tmp_path / "paper.pdf"
    write_pdf(pdf, doi="10.1234/wrong")
    digest = sha256_file(pdf)
    document = {"doi": ["10.1234/correct"], "title": ["A paper"]}

    with pytest.raises(IdentityConflictError):
        assert_mutation_allowed(pdf, document, provider_document=document)
    with pytest.raises(IdentityConflictError):
        assert_mutation_allowed(
            pdf, document, provider_document=document, manual_confirmation=True
        )
    for kwargs in (
        {"provider_document": document},
        {"expected_sha256": digest},
    ):
        with pytest.raises(IdentityValidationError):
            assert_mutation_allowed(
                pdf, document, manual_conflict_override=True, **kwargs
            )

    proof = assert_mutation_allowed(
        pdf,
        document,
        expected_sha256=digest,
        provider_document=document,
        manual_conflict_override=True,
    )
    assert proof["status"] == "manual_confirmed"
    assert proof["authority"] == "manual_conflict_override"
    stored = {**document, IDENTITY_KEY: proof}
    assert (
        verify_record_identity(pdf, stored)["authority"] == "manual_conflict_override"
    )

    with pytest.raises(IdentityValidationError):
        verify_record_identity(pdf, {**stored, "doi": ["10.1234/another"]})
    with pytest.raises(IdentityValidationError):
        assert_mutation_allowed(
            pdf,
            document,
            expected_sha256=digest,
            provider_document={"doi": ["10.1234/another"]},
            manual_conflict_override=True,
        )
    write_pdf(pdf, doi="10.1234/different")
    with pytest.raises(IdentityValidationError):
        verify_record_identity(pdf, stored)
    with pytest.raises(IdentityValidationError):
        assert_mutation_allowed(
            pdf,
            document,
            expected_sha256=digest,
            provider_document=document,
            manual_conflict_override=True,
        )


def test_reviewed_unconfirmed_pdf_identifier_can_be_corrected(tmp_path: Path) -> None:
    pdf = tmp_path / "paper.pdf"
    write_pdf(pdf, doi="10.1234/wrong")
    document = {"bibcode": "2024Test...1A", "title": ["A paper"]}
    proof = assert_mutation_allowed(
        pdf,
        document,
        expected_sha256=sha256_file(pdf),
        provider_document=document,
        manual_conflict_override=True,
    )
    assert proof["status"] == "manual_confirmed"
    assert verify_record_identity(pdf, {**document, IDENTITY_KEY: proof})
