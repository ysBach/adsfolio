"""Update one PDF by a DOI or arXiv ID that ADS does not index."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from pdf_identity_fixtures import write_pdf

from adsfolio.config import AuthenticationError
from adsfolio.pdf.cli import _parse_ads_identifier, pdf
from adsfolio.pdf.doi_providers import DoiProvider
from adsfolio.pdf.files import sha256_file
from adsfolio.pdf.identify_settings import IdentifySettings
from adsfolio.pdf.registry import open_registry_state

CROSSREF_DOC = {
    "doi": ["10.5555/not.in.ads"],
    "title": ["A paper outside ADS"],
    "author": ["Roe, Alice"],
    "year": "2021",
    "pub": "Journal of Examples",
    "volume": "3",
    "page": ["7"],
}


class EmptySearch:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def query(self, *, q: str, fl: list[str], rows: int):
        del fl, rows
        self.queries.append(q)
        return SimpleNamespace(num_found=0, docs=[])


@pytest.fixture
def library(tmp_path: Path) -> Path:
    root = tmp_path / "library"
    root.mkdir()
    pdf_path = root / "scan.pdf"
    write_pdf(pdf_path)
    state = open_registry_state(root)
    try:
        state.upsert_file(
            "scan.pdf",
            {
                "current_filename": "scan.pdf",
                "sha256": sha256_file(pdf_path),
                "status": "unresolved",
            },
        )
        state.save()
    finally:
        state.close()
    return root


@pytest.fixture
def providers(monkeypatch):
    calls: list[tuple[str, str]] = []

    def crossref(doi: str):
        calls.append(("crossref", doi))
        return dict(CROSSREF_DOC) if doi == "10.5555/not.in.ads" else None

    def datacite(doi: str):
        calls.append(("datacite", doi))
        if doi.lower() == "10.48550/arxiv.2401.12345":
            return {
                "doi": [doi],
                "title": ["An arXiv-only preprint"],
                "author": ["Kim, Sun"],
                "year": "2024",
                "publisher": "arXiv",
            }
        return None

    monkeypatch.setattr("adsfolio.pdf.identifier.fetch_crossref_doi", crossref)
    monkeypatch.setattr("adsfolio.pdf.identifier.fetch_datacite_doi", datacite)
    return calls


def _run(library: Path, identifier: str):
    return CliRunner().invoke(
        pdf,
        [
            "refresh-ads",
            str(library),
            "--identifier",
            identifier,
            "--confirm-identity",
            "--relative-path",
            "scan.pdf",
        ],
    )


def _only_record(library: Path) -> dict:
    state = open_registry_state(library)
    try:
        records = list(state.registry.iter_files())
        assert len(records) == 1
        catalog = state.registry.catalog
        document = catalog.document_for_attachment(records[0]["attachment_id"])
        if document is not None:
            row = catalog._connection.execute(
                "SELECT doi, arxiv_id FROM documents WHERE id = ?", (document["id"],)
            ).fetchone()
            document.update(doi=row["doi"], arxiv_id=row["arxiv_id"])
        return {**records[0], "_document": document}
    finally:
        state.close()


def test_doi_outside_ads_updates_and_renames_the_file(library, providers, monkeypatch):
    search = EmptySearch()
    monkeypatch.setattr(
        "adsfolio.pdf.cli._get_client", lambda _ctx: SimpleNamespace(search=search)
    )
    result = _run(library, "https://doi.org/10.5555/not.in.ads")
    assert result.exit_code == 0, result.output
    assert search.queries == ['doi:"10.5555/not.in.ads"']
    assert providers == [("crossref", "10.5555/not.in.ads")]
    assert "not in ADS" in result.output
    record = _only_record(library)
    assert record["status"] == "unresolved"
    assert not record["bibcode"]
    assert record["metadata"]["title"] == ["A paper outside ADS"]
    assert record["metadata"]["_adsfolio_external_metadata"]["provider"] == "crossref"
    assert record["current_path"] != "scan.pdf"
    assert (library / record["current_path"]).is_file()
    assert record["_document"] is not None and record["_document"]["bibcode"] is None
    assert record["_document"]["doi"] == "10.5555/not.in.ads"


def test_doi_lookup_works_without_an_ads_token(library, providers, monkeypatch):
    def no_token(_ctx):
        raise AuthenticationError("no token")

    monkeypatch.setattr("adsfolio.pdf.cli._get_client", no_token)
    result = _run(library, "10.5555/not.in.ads")
    assert result.exit_code == 0, result.output
    assert _only_record(library)["metadata"]["title"] == ["A paper outside ADS"]


def test_ads_is_skipped_when_cleared_from_the_provider_list(
    library, providers, monkeypatch
):
    state = open_registry_state(library)
    try:
        state.set_identify_settings(
            IdentifySettings(providers=(DoiProvider.DATACITE, DoiProvider.CROSSREF))
        )
        state.save()
    finally:
        state.close()
    search = EmptySearch()
    monkeypatch.setattr(
        "adsfolio.pdf.cli._get_client", lambda _ctx: SimpleNamespace(search=search)
    )
    result = _run(library, "10.5555/not.in.ads")
    assert result.exit_code == 0, result.output
    assert search.queries == []
    assert providers == [
        ("datacite", "10.5555/not.in.ads"),
        ("crossref", "10.5555/not.in.ads"),
    ]


def test_arxiv_id_outside_ads_uses_its_datacite_doi(library, providers, monkeypatch):
    search = EmptySearch()
    monkeypatch.setattr(
        "adsfolio.pdf.cli._get_client", lambda _ctx: SimpleNamespace(search=search)
    )
    result = _run(library, "arXiv:2401.12345v2")
    assert result.exit_code == 0, result.output
    assert search.queries == ['identifier:"arXiv:2401.12345"']
    assert ("datacite", "10.48550/arXiv.2401.12345") in providers
    record = _only_record(library)
    assert record["metadata"]["title"] == ["An arXiv-only preprint"]
    assert record["_document"] is not None
    assert record["_document"]["arxiv_id"] == "2401.12345"
    assert record["current_path"] != "scan.pdf"
    assert (library / record["current_path"]).is_file()


def test_unknown_identifier_leaves_the_file_unchanged(library, providers, monkeypatch):
    monkeypatch.setattr(
        "adsfolio.pdf.cli._get_client",
        lambda _ctx: SimpleNamespace(search=EmptySearch()),
    )
    result = _run(library, "10.9999/nowhere")
    assert result.exit_code == 0, result.output
    record = _only_record(library)
    assert record["current_path"] == "scan.pdf"
    assert "title" not in record["metadata"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2401.12345v2", ("arxiv", "2401.12345")),
        ("arXiv:2401.12345", ("arxiv", "2401.12345")),
        ("astro-ph/0601001v1", ("arxiv", "astro-ph/0601001")),
        ("2024arXiv240112345B", ("bibcode", "2024arXiv240112345B")),
        ("10.48550/arXiv.2401.12345", ("doi", "10.48550/arxiv.2401.12345")),
    ],
)
def test_identifier_classification(value, expected):
    assert _parse_ads_identifier(value) == expected


def test_arxiv_lookup_without_datacite_says_why(library, providers, monkeypatch):
    state = open_registry_state(library)
    try:
        state.set_identify_settings(
            IdentifySettings(providers=(DoiProvider.ADS, DoiProvider.CROSSREF))
        )
        state.save()
    finally:
        state.close()
    monkeypatch.setattr(
        "adsfolio.pdf.cli._get_client",
        lambda _ctx: SimpleNamespace(search=EmptySearch()),
    )
    result = _run(library, "2401.12345")
    assert result.exit_code == 0, result.output
    assert "need DataCite" in result.output
    assert _only_record(library)["current_path"] == "scan.pdf"


def test_paper_found_outside_ads_gains_its_bibcode_later(
    library, providers, monkeypatch
):
    monkeypatch.setattr(
        "adsfolio.pdf.cli._get_client",
        lambda _ctx: SimpleNamespace(search=EmptySearch()),
    )
    assert _run(library, "10.5555/not.in.ads").exit_code == 0
    before = _only_record(library)

    class AdsHasItNow:
        def query(self, *, q, fl, rows):
            return SimpleNamespace(
                num_found=1,
                docs=[
                    {
                        **CROSSREF_DOC,
                        "bibcode": "2021JEx.....3....7R",
                        "bibstem": ["JEx"],
                    }
                ],
            )

    monkeypatch.setattr(
        "adsfolio.pdf.cli._get_client",
        lambda _ctx: SimpleNamespace(search=AdsHasItNow()),
    )
    result = CliRunner().invoke(
        pdf,
        [
            "refresh-ads",
            str(library),
            "--identifier",
            "10.5555/not.in.ads",
            "--confirm-identity",
            "--relative-path",
            str(before["current_path"]),
        ],
    )
    assert result.exit_code == 0, result.output
    after = _only_record(library)
    assert after["status"] == "resolved"
    assert after["bibcode"] == "2021JEx.....3....7R"
    assert after["_document"]["id"] == before["_document"]["id"]
    assert after["_document"]["bibcode"] == "2021JEx.....3....7R"
