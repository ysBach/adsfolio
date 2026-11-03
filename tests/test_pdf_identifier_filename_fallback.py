"""Check identity verification when a filename candidate is not exact."""

import hashlib
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter

from adsfolio.pdf.identifier import PdfIdentifier
from adsfolio.pdf.identify_settings import IdentifyStrategy
from adsfolio.pdf.identity import (
    IDENTITY_KEY,
    IdentityConflictError,
    UnverifiedIdentityError,
)

CODE = "2024ApJ...900..100A"
OTHER = "2024ApJ...900..101B"
DOI = "10.5555/example"
DOCUMENT = {"bibcode": CODE, "doi": [DOI]}


class Search:
    def __init__(self):
        self.calls = 0

    def query(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(docs=[DOCUMENT], num_found=1, start=0)


@pytest.mark.parametrize(
    ("filename", "candidate"),
    [
        (CODE.replace(".", "_") + ".pdf", CODE),
        (f"{CODE}_{OTHER}.pdf", CODE),
        (f"{CODE}.pdf", CODE.lower()),
    ],
)
@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize(
    ("pdf_doi", "expected_status", "failure"),
    [
        (DOI, "verified", None),
        (None, None, UnverifiedIdentityError),
        ("10.5555/conflict", None, IdentityConflictError),
    ],
)
def test_nonexact_filename_candidate_uses_ordinary_identity_check(
    tmp_path,
    monkeypatch,
    filename,
    candidate,
    cached,
    pdf_doi,
    expected_status,
    failure,
):
    path = tmp_path / filename
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    if pdf_doi:
        writer.add_metadata({"/DOI": pdf_doi})
    writer.write(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    search = Search()
    engine = PdfIdentifier(SimpleNamespace(search=search))
    monkeypatch.setattr(engine, "extract_bibcode_from_filename", lambda _: candidate)
    options = {}
    if cached:
        options = {
            "cached_candidate_lookup": lambda kind, value: (True, DOCUMENT),
            "allow_network_lookup": False,
        }

    record = engine.resolve_many(
        [path], strategies=(IdentifyStrategy.FILENAME_BIBCODE,), **options
    )[path]

    if failure is None:
        assert record[IDENTITY_KEY]["status"] == expected_status
        assert record[IDENTITY_KEY]["sha256"] == digest
    else:
        assert record is None
        assert isinstance(engine.identity_failures[path], failure)
    assert search.calls == (0 if cached else 1)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_exact_filename_still_allows_filename_association(tmp_path):
    path = tmp_path / f"{CODE}.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(path)
    engine = PdfIdentifier(SimpleNamespace(search=Search()))

    record = engine.resolve_many(
        [path], strategies=(IdentifyStrategy.FILENAME_BIBCODE,)
    )[path]

    assert record[IDENTITY_KEY]["status"] == "filename_associated"
