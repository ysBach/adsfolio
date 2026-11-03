"""Reference hints must not associate a PDF with a coauthored different paper."""

import json
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from pdf_identity_fixtures import write_pdf

from adsfolio.config import Config
from adsfolio.pdf.cli import pdf
from adsfolio.pdf.doi_providers import DoiProvider
from adsfolio.pdf.files import sha256_file
from adsfolio.pdf.identify_settings import IdentifySettings
from adsfolio.pdf.identity import target_identity
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


def test_reference_name_with_articles_offers_candidate_before_apply(
    tmp_path, monkeypatch
):
    root = tmp_path / "library"
    root.mkdir()
    source = root / "paper.pdf"
    write_pdf(source)
    digest = sha256_file(source)
    state = open_registry_state(root)
    state.upsert_file(source.name, {"sha256": digest, "status": "unresolved"})
    before = state.registry.find_file_by_current_path(root, source.name)
    state.close()
    document = {
        "bibcode": "1979M&P....21..155C",
        "title": ["Vaporization of comet nuclei"],
        "author": ["Cowan, J. J."],
        "doi": ["10.1007/BF00897085"],
        "pub": "Moon and Planets",
        "volume": "21",
        "page": ["155"],
    }
    queries = []

    class Search:
        def query(self, *, q, fl, rows):
            queries.append(q)
            if 'pub:"The Moon and the Planets"' in q:
                return SimpleNamespace(num_found=0, docs=[])
            return SimpleNamespace(num_found=1, docs=[document])

    monkeypatch.setattr(
        "adsfolio.pdf.cli._get_client", lambda ctx: SimpleNamespace(search=Search())
    )
    result = CliRunner().invoke(
        pdf,
        [
            "refresh-ads",
            str(root),
            "--relative-path",
            source.name,
            "--identifier",
            "The Moon and the Planets-21-155",
            "--review-reference",
            "--confirm-identity",
            "--no-rename",
        ],
    )
    assert result.exit_code == 0, result.output
    assert 'pub:"Moon and Planets"' in queries[1]
    line = next(
        line
        for line in result.output.splitlines()
        if line.startswith("ADS_MATCH_CANDIDATES_JSON:")
    )
    candidate = json.loads(line.split(":", 1)[1])[0]
    assert candidate["bibcode"] == document["bibcode"]
    assert candidate["pdf_sha256"] == digest
    assert candidate["identity_fingerprint"] == target_identity(document).fingerprint
    assert only_record(root)["metadata"] == before["metadata"]

    apply_args = [
        "refresh-ads",
        str(root),
        "--relative-path",
        source.name,
        "--bibcode",
        candidate["bibcode"],
        "--expected-sha256",
        candidate["pdf_sha256"],
        "--expected-identity",
        candidate["identity_fingerprint"],
        "--expected-review-digest",
        candidate["review_digest"],
        "--confirm-identity",
        "--no-rename",
    ]
    original_title = document["title"]
    document["title"] = ["Changed after review"]
    refused = CliRunner().invoke(pdf, apply_args)
    assert refused.exit_code == 2
    assert "candidate details changed" in refused.output
    assert only_record(root)["metadata"] == before["metadata"]
    document["title"] = original_title
    result = CliRunner().invoke(pdf, apply_args)
    assert result.exit_code == 0, result.output
    assert only_record(root)["bibcode"] == document["bibcode"]
    assert sha256_file(source) == digest


def test_incomplete_reference_result_is_not_offered_as_a_unique_match(
    tmp_path, monkeypatch
):
    root = tmp_path / "library"
    root.mkdir()
    source = root / "paper.pdf"
    write_pdf(source)
    digest = sha256_file(source)
    state = open_registry_state(root)
    state.upsert_file(source.name, {"sha256": digest, "status": "unresolved"})
    before = state.registry.find_file_by_current_path(root, source.name)
    state.close()
    search = SimpleNamespace(
        query=lambda **kwargs: SimpleNamespace(
            num_found=2,
            docs=[{"bibcode": "2020Natur.123...45A", "title": ["Candidate"]}],
        )
    )
    monkeypatch.setattr(
        "adsfolio.pdf.cli._get_client", lambda ctx: SimpleNamespace(search=search)
    )

    result = CliRunner().invoke(
        pdf,
        [
            "refresh-ads",
            str(root),
            "--relative-path",
            source.name,
            "--identifier",
            "Nature-123-45",
            "--review-reference",
            "--confirm-identity",
            "--no-rename",
        ],
    )

    assert result.exit_code == 2
    assert "incomplete reference results" in result.output
    assert only_record(root)["metadata"] == before["metadata"]
    assert sha256_file(source) == digest


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
    assert record["current_path"] == "paper.pdf"
    assert "bibcode" in result.output
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


def test_reference_uses_enabled_doi_provider_without_calling_disabled_ads(
    library, monkeypatch
):
    monkeypatch.setattr(
        Config,
        "load_identify_settings",
        classmethod(lambda cls: IdentifySettings(providers=(DoiProvider.CROSSREF,))),
    )
    monkeypatch.setattr(
        "adsfolio.pdf.cli._get_client", lambda ctx: pytest.fail("ADS is disabled")
    )
    monkeypatch.setattr(
        "adsfolio.pdf.identifier.fetch_crossref_doi", lambda doi: dict(OWN_DOC)
    )

    result = invoke(library)

    assert result.exit_code == 0, result.output
    assert only_record(library)["metadata"]["doi"] == OWN_DOI


def test_reference_explains_when_no_enabled_provider_can_search_it(
    tmp_path, monkeypatch
):
    root = tmp_path / "library"
    root.mkdir()
    source = root / "paper.pdf"
    write_pdf(source)
    state = open_registry_state(root)
    state.upsert_file(
        source.name, {"sha256": sha256_file(source), "status": "unresolved"}
    )
    state.close()
    monkeypatch.setattr(
        Config,
        "load_identify_settings",
        classmethod(lambda cls: IdentifySettings(providers=(DoiProvider.CROSSREF,))),
    )
    monkeypatch.setattr(
        "adsfolio.pdf.cli._get_client", lambda ctx: pytest.fail("ADS is disabled")
    )

    result = invoke(root)

    assert result.exit_code == 2
    assert "Reference searches require NASA/ADS" in result.output
