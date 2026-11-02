"""Reference hints must not associate a PDF with a coauthored different paper."""

from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from pdf_identity_fixtures import write_pdf

from adsfolio.pdf.cli import pdf
from adsfolio.pdf.files import sha256_file
from adsfolio.pdf.registry import open_registry_state

OWN_DOI = "10.5555/methane"
OTHER_DOI = "10.5555/water"
OWN_DOC = {
    "doi": [OWN_DOI],
    "title": ["Methane in an interstellar object"],
    "author": ["Example, Alice"],
    "year": "2026",
}
OTHER_DOC = {
    "bibcode": "2026NatAs..10..111O",
    "doi": [OTHER_DOI],
    "title": ["Water in an interstellar object"],
    "author": ["Other, Bob", "Example, Alice"],
    "year": "2026",
}


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    source = root / "paper.pdf"
    write_pdf(source, doi=OWN_DOI)
    state = open_registry_state(root)
    state.upsert_file(
        source.name,
        {
            "current_filename": source.name,
            "sha256": sha256_file(source),
            "status": "unresolved",
            "notes": "Keep my notes",
        },
    )
    state.close()
    return root


class ReferenceSearch:
    def __init__(self, candidates, own_documents=()):
        self.candidates = candidates
        self.own_documents = list(own_documents)
        self.queries = []

    def query(self, *, q, fl, rows):
        self.queries.append(q)
        documents = self.own_documents if q == f'doi:"{OWN_DOI}"' else self.candidates
        return SimpleNamespace(num_found=len(documents), docs=documents)


def invoke(root, identifier="Example-2026-NatAs"):
    return CliRunner().invoke(
        pdf,
        [
            "refresh-ads",
            str(root),
            "--relative-path",
            "paper.pdf",
            "--identifier",
            identifier,
        ],
    )


def only_record(root):
    state = open_registry_state(root)
    try:
        records = list(state.registry.iter_files())
        assert len(records) == 1
        return records[0]
    finally:
        state.close()


def test_reference_recovers_from_coauthor_match_using_pdf_doi(library, monkeypatch):
    digest = sha256_file(library / "paper.pdf")
    search = ReferenceSearch([OTHER_DOC])
    monkeypatch.setattr(
        "adsfolio.pdf.cli._get_client", lambda ctx: SimpleNamespace(search=search)
    )
    monkeypatch.setattr(
        "adsfolio.pdf.identifier.fetch_crossref_doi",
        lambda doi: dict(OWN_DOC) if doi == OWN_DOI else None,
    )

    result = invoke(library)

    assert result.exit_code == 0, result.output
    record = only_record(library)
    assert not record["bibcode"]
    assert record["metadata"]["doi"] == OWN_DOI
    assert record["metadata"]["notes"] == "Keep my notes"
    assert record["current_path"] != "paper.pdf"
    assert sha256_file(library / record["current_path"]) == digest
    assert f'doi:"{OWN_DOI}"' in search.queries
    assert "PDF's own identifiers" in result.output


def test_reference_uses_only_matching_candidate_among_coauthored_records(
    library, monkeypatch
):
    own = {**OWN_DOC, "bibcode": "2026NatAs..10..222E"}
    search = ReferenceSearch([OTHER_DOC, own])
    monkeypatch.setattr(
        "adsfolio.pdf.cli._get_client", lambda ctx: SimpleNamespace(search=search)
    )

    result = invoke(library)

    assert result.exit_code == 0, result.output
    record = only_record(library)
    assert record["bibcode"] == own["bibcode"]
    assert "Multiple ADS records" not in result.output


def test_explicit_conflicting_bibcode_is_not_replaced_by_pdf_doi(library, monkeypatch):
    before = only_record(library)
    source = library / "paper.pdf"
    digest = sha256_file(source)
    search = ReferenceSearch([OTHER_DOC])
    monkeypatch.setattr(
        "adsfolio.pdf.cli._get_client", lambda ctx: SimpleNamespace(search=search)
    )
    monkeypatch.setattr(
        "adsfolio.pdf.identifier.fetch_crossref_doi",
        lambda doi: pytest.fail("Do not replace an explicit conflicting identifier"),
    )

    result = invoke(library, OTHER_DOC["bibcode"])

    assert result.exit_code == 2
    assert only_record(library) == before
    assert sha256_file(source) == digest


def test_reference_with_no_matching_provider_leaves_metadata_and_paths_unchanged(
    library, monkeypatch
):
    before = only_record(library)
    search = ReferenceSearch([OTHER_DOC])
    monkeypatch.setattr(
        "adsfolio.pdf.cli._get_client", lambda ctx: SimpleNamespace(search=search)
    )
    monkeypatch.setattr("adsfolio.pdf.identifier.fetch_crossref_doi", lambda doi: None)
    monkeypatch.setattr("adsfolio.pdf.identifier.fetch_datacite_doi", lambda doi: None)

    result = invoke(library)

    assert result.exit_code == 0, result.output
    assert only_record(library) == before
    assert "No reference result matches this PDF" in result.output


@pytest.mark.parametrize("failure", ["token", "service"])
def test_reference_uses_pdf_doi_when_ads_is_unavailable(library, monkeypatch, failure):
    from adsfolio.config import ADSError, AuthenticationError

    def unavailable(**kwargs):
        raise ADSError("ADS unavailable")

    def client(ctx):
        if failure == "token":
            raise AuthenticationError("No ADS token")
        return SimpleNamespace(search=SimpleNamespace(query=unavailable))

    monkeypatch.setattr("adsfolio.pdf.cli._get_client", client)
    monkeypatch.setattr(
        "adsfolio.pdf.identifier.fetch_crossref_doi", lambda doi: dict(OWN_DOC)
    )

    result = invoke(library)

    assert result.exit_code == 0, result.output
    assert only_record(library)["metadata"]["doi"] == OWN_DOI
