"""Test provider fallback during PDF identification."""

import hashlib
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import fitz
import pytest
from pypdf import PdfReader, PdfWriter

from adsfolio.api.search import SearchResult
from adsfolio.config import ADSError
from adsfolio.pdf.doi_providers import DoiProvider
from adsfolio.pdf.identifier import PdfIdentifier
from adsfolio.pdf.identify_settings import IdentifyStrategy
from adsfolio.pdf.identity import (
    IDENTITY_KEY,
    IdentityConflictError,
    UnverifiedIdentityError,
)

DOI = "10.5555/example"
OTHER_DOI = "10.5555/other"
BIBCODE = "2024ApJ...900..100A"
OTHER_BIBCODE = "2024ApJ...900..101B"
ARXIV = "2401.12345"


@pytest.fixture(autouse=True)
def block_unexpected_external_requests(monkeypatch):
    def unexpected(_doi):
        raise AssertionError("Unexpected external provider request")

    monkeypatch.setattr("adsfolio.pdf.identifier.fetch_crossref_doi", unexpected)
    monkeypatch.setattr("adsfolio.pdf.identifier.fetch_datacite_doi", unexpected)


def make_pdf(path: Path, metadata: dict[str, str], *, stamp: bool = False) -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 100), "Synthetic paper")
    if stamp:
        page.insert_text((30, 500), f"arXiv:{ARXIV}v2", rotate=90)
    data = document.tobytes()
    document.close()
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
    writer.add_metadata(metadata)
    output = io.BytesIO()
    writer.write(output)
    data = output.getvalue()
    path.write_bytes(data)
    return data


def provider_document(doi: str = DOI, *, arxiv: bool = False) -> dict:
    document = {"doi": [doi], "title": ["Synthetic paper"]}
    if arxiv:
        document["identifier"] = [f"arXiv:{ARXIV}"]
    return document


def test_xmp_only_doi_uses_metadata_strategy_and_provider_order(tmp_path, monkeypatch):
    path = tmp_path / "paper.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 100), "Synthetic paper with no printed DOI")
    document.set_xml_metadata(
        "<x:xmpmeta xmlns:x='adobe:ns:meta/'><rdf:RDF "
        "xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>"
        "<rdf:Description xmlns:prism='http://prismstandard.org/namespaces/basic/2.0/'>"
        f"<prism:doi>{DOI}</prism:doi></rdf:Description></rdf:RDF></x:xmpmeta>"
    )
    path.write_bytes(document.tobytes())
    document.close()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    client = ads_client(lambda **_: SearchResult(num_found=0, start=0, docs=[]))
    crossref = Mock(return_value=provider_document())
    monkeypatch.setattr("adsfolio.pdf.identifier.fetch_crossref_doi", crossref)

    identifier = PdfIdentifier(client)
    assert identifier.extract_doi_from_metadata(path) == DOI
    assert identifier.extract_doi_candidates(path) == (DOI, None)
    result = identifier.resolve(
        path,
        strategies=(IdentifyStrategy.PDF_METADATA_DOI,),
        providers=(DoiProvider.ADS, DoiProvider.CROSSREF),
    )

    assert result is not None
    assert result["doi"] == DOI
    assert result["resolution_strategy"] == "pdf_metadata_doi"
    assert result[IDENTITY_KEY]["status"] == "verified"
    assert client.search.query.called
    crossref.assert_called_once_with(DOI)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_external_provider_still_runs_when_only_ads_network_is_declined(
    tmp_path, monkeypatch
):
    path = tmp_path / "paper.pdf"
    make_pdf(path, {"/DOI": DOI})
    client = ads_client(lambda **_: pytest.fail("ADS network was declined"))
    crossref = Mock(return_value=provider_document())
    monkeypatch.setattr("adsfolio.pdf.identifier.fetch_crossref_doi", crossref)

    result = PdfIdentifier(client).resolve(
        path,
        strategies=(IdentifyStrategy.PDF_METADATA_DOI,),
        providers=(DoiProvider.ADS, DoiProvider.CROSSREF),
        allow_network_lookup=False,
        allow_external_network_lookup=True,
    )

    assert result is not None
    assert result["doi"] == DOI
    assert result[IDENTITY_KEY]["status"] == "verified"
    crossref.assert_called_once_with(DOI)


def test_fully_offline_identification_does_not_call_external_providers(tmp_path):
    path = tmp_path / "paper.pdf"
    make_pdf(path, {"/DOI": DOI})
    client = ads_client(lambda **_: pytest.fail("Network is disabled"))

    result = PdfIdentifier(client).resolve(
        path,
        strategies=(IdentifyStrategy.PDF_METADATA_DOI,),
        providers=(DoiProvider.ADS, DoiProvider.CROSSREF),
        allow_network_lookup=False,
    )

    assert result is None


def ads_client(response) -> SimpleNamespace:
    return SimpleNamespace(search=SimpleNamespace(query=Mock(side_effect=response)))


@pytest.mark.parametrize(
    "early_strategy",
    [IdentifyStrategy.FILENAME_BIBCODE, IdentifyStrategy.FIRST_PAGE_ARXIV],
)
@pytest.mark.parametrize("provider", [DoiProvider.CROSSREF, DoiProvider.DATACITE])
def test_early_ads_outage_reaches_verified_doi_fallback(
    tmp_path, monkeypatch, early_strategy, provider
):
    is_arxiv = early_strategy is IdentifyStrategy.FIRST_PAGE_ARXIV
    path = tmp_path / ("paper.pdf" if is_arxiv else f"{BIBCODE}.pdf")
    original = make_pdf(path, {"/DOI": DOI}, stamp=is_arxiv)
    client = ads_client(ADSError("ADS unavailable"))
    doc = provider_document(arxiv=is_arxiv)
    crossref = Mock(return_value=doc if provider is DoiProvider.CROSSREF else None)
    datacite = Mock(return_value=doc)
    monkeypatch.setattr("adsfolio.pdf.identifier.fetch_crossref_doi", crossref)
    monkeypatch.setattr("adsfolio.pdf.identifier.fetch_datacite_doi", datacite)

    record = PdfIdentifier(client).resolve(
        path,
        strategies=(early_strategy, IdentifyStrategy.PDF_METADATA_DOI),
        providers=(DoiProvider.ADS, DoiProvider.CROSSREF, DoiProvider.DATACITE),
    )

    assert record is not None
    assert record["doi"] == DOI
    assert record["resolution_strategy"] == "pdf_metadata_doi"
    assert record["_adsfolio_external_metadata"]["provider"] == provider.value
    assert record[IDENTITY_KEY]["status"] == "verified"
    assert record["sha256"] == hashlib.sha256(original).hexdigest()
    assert path.read_bytes() == original
    first_query = client.search.query.call_args_list[0].kwargs["q"]
    assert first_query.startswith("identifier:" if is_arxiv else "bibcode:")
    crossref.assert_called_once_with(DOI)
    assert datacite.called is (provider is DoiProvider.DATACITE)


@pytest.mark.parametrize(
    "early_strategy",
    [IdentifyStrategy.FILENAME_BIBCODE, IdentifyStrategy.FIRST_PAGE_ARXIV],
)
def test_unresolved_ads_outage_remains_an_error(tmp_path, monkeypatch, early_strategy):
    is_arxiv = early_strategy is IdentifyStrategy.FIRST_PAGE_ARXIV
    path = tmp_path / ("paper.pdf" if is_arxiv else f"{BIBCODE}.pdf")
    make_pdf(path, {"/DOI": DOI}, stamp=is_arxiv)
    error = ADSError("ADS unavailable")
    client = ads_client(error)
    crossref = Mock(return_value=None)
    monkeypatch.setattr("adsfolio.pdf.identifier.fetch_crossref_doi", crossref)

    with pytest.raises(ADSError, match="ADS unavailable"):
        PdfIdentifier(client).resolve(
            path,
            strategies=(early_strategy, IdentifyStrategy.PDF_METADATA_DOI),
            providers=(DoiProvider.ADS, DoiProvider.CROSSREF),
        )
    crossref.assert_called_once_with(DOI)


@pytest.mark.parametrize("configured_client", [False, True])
def test_absent_ads_result_is_not_an_error(tmp_path, monkeypatch, configured_client):
    path = tmp_path / f"{BIBCODE}.pdf"
    make_pdf(path, {"/DOI": DOI})
    client = ads_client(lambda **_: SearchResult(num_found=0, start=0, docs=[]))
    monkeypatch.setattr(
        "adsfolio.pdf.identifier.fetch_crossref_doi", Mock(return_value=None)
    )

    record = PdfIdentifier(client if configured_client else None).resolve(
        path,
        strategies=(
            IdentifyStrategy.FILENAME_BIBCODE,
            IdentifyStrategy.PDF_METADATA_DOI,
        ),
        providers=(DoiProvider.ADS, DoiProvider.CROSSREF),
    )

    assert record is None


def test_fallback_rejects_filename_candidate_without_pdf_identity(
    tmp_path, monkeypatch
):
    path = tmp_path / f"{BIBCODE}.pdf"
    original = make_pdf(path, {"/Subject": f"DOI {DOI}"})
    client = ads_client(ADSError("ADS unavailable"))
    crossref = Mock(return_value=provider_document())
    monkeypatch.setattr("adsfolio.pdf.identifier.fetch_crossref_doi", crossref)
    identifier = PdfIdentifier(client)

    with pytest.raises(ADSError, match="ADS unavailable"):
        identifier.resolve(
            path,
            strategies=(
                IdentifyStrategy.FILENAME_BIBCODE,
                IdentifyStrategy.PDF_METADATA_DOI,
            ),
            providers=(DoiProvider.ADS, DoiProvider.CROSSREF),
        )

    assert isinstance(identifier.identity_failures[path], UnverifiedIdentityError)
    crossref.assert_called_once_with(DOI)
    assert path.read_bytes() == original


def test_fallback_requires_the_pdf_arxiv_alias(tmp_path, monkeypatch):
    path = tmp_path / "paper.pdf"
    original = make_pdf(path, {"/DOI": DOI}, stamp=True)
    client = ads_client(ADSError("ADS unavailable"))
    monkeypatch.setattr(
        "adsfolio.pdf.identifier.fetch_crossref_doi",
        Mock(return_value=provider_document()),
    )
    identifier = PdfIdentifier(client)

    with pytest.raises(ADSError, match="ADS unavailable"):
        identifier.resolve(
            path,
            strategies=(
                IdentifyStrategy.FIRST_PAGE_ARXIV,
                IdentifyStrategy.PDF_METADATA_DOI,
            ),
            providers=(DoiProvider.ADS, DoiProvider.CROSSREF),
        )

    assert isinstance(identifier.identity_failures[path], UnverifiedIdentityError)
    assert path.read_bytes() == original


def test_fallback_rejects_a_conflicting_provider_doi(tmp_path, monkeypatch):
    path = tmp_path / f"{BIBCODE}.pdf"
    make_pdf(path, {"/DOI": DOI})
    client = ads_client(ADSError("ADS unavailable"))
    monkeypatch.setattr(
        "adsfolio.pdf.identifier.fetch_crossref_doi",
        Mock(return_value=provider_document(OTHER_DOI)),
    )
    identifier = PdfIdentifier(client)

    with pytest.raises(ADSError, match="ADS unavailable"):
        identifier.resolve(
            path,
            strategies=(
                IdentifyStrategy.FILENAME_BIBCODE,
                IdentifyStrategy.PDF_METADATA_DOI,
            ),
            providers=(DoiProvider.ADS, DoiProvider.CROSSREF),
        )

    assert isinstance(identifier.identity_failures[path], IdentityConflictError)


def test_disabled_ads_and_network_settings_are_respected(tmp_path, monkeypatch):
    path = tmp_path / f"{BIBCODE}.pdf"
    make_pdf(path, {"/DOI": DOI})
    client = ads_client(AssertionError("Unexpected ADS request"))
    crossref = Mock(return_value=provider_document())
    monkeypatch.setattr("adsfolio.pdf.identifier.fetch_crossref_doi", crossref)
    strategies = (IdentifyStrategy.FILENAME_BIBCODE, IdentifyStrategy.PDF_METADATA_DOI)

    record = PdfIdentifier(client).resolve(
        path, strategies=strategies, providers=(DoiProvider.CROSSREF,)
    )
    assert record is not None
    client.search.query.assert_not_called()
    crossref.assert_called_once_with(DOI)

    crossref.reset_mock()
    record = PdfIdentifier(client).resolve(
        path,
        strategies=strategies,
        providers=(DoiProvider.ADS, DoiProvider.CROSSREF),
        allow_network_lookup=False,
    )
    assert record is None
    client.search.query.assert_not_called()
    crossref.assert_not_called()


def test_offline_ads_cache_hit_still_requires_identity_validation(tmp_path):
    path = tmp_path / f"{BIBCODE}.pdf"
    make_pdf(path, {"/DOI": DOI})
    client = ads_client(AssertionError("Unexpected ADS request"))
    ads_doc = {"bibcode": BIBCODE, "doi": [DOI]}
    cached = Mock(return_value=(True, ads_doc))

    record = PdfIdentifier(client).resolve(
        path,
        strategies=(IdentifyStrategy.FILENAME_BIBCODE,),
        providers=(DoiProvider.ADS,),
        cached_candidate_lookup=cached,
        allow_network_lookup=False,
    )

    assert record is not None
    assert record[IDENTITY_KEY]["status"] == "verified"
    client.search.query.assert_not_called()
    cached.assert_called_once_with("bibcode", BIBCODE)


def test_earlier_cached_hit_survives_another_candidates_ads_outage(
    tmp_path, monkeypatch
):
    first = tmp_path / f"{BIBCODE}.pdf"
    second = tmp_path / f"{OTHER_BIBCODE}.pdf"
    make_pdf(first, {"/DOI": DOI})
    make_pdf(second, {"/DOI": OTHER_DOI})
    ads_doc = {"bibcode": BIBCODE, "doi": [DOI]}
    client = ads_client(ADSError("ADS unavailable"))
    crossref = Mock(return_value=provider_document(OTHER_DOI))
    monkeypatch.setattr("adsfolio.pdf.identifier.fetch_crossref_doi", crossref)

    def cached(kind, value):
        if kind == "bibcode" and value == BIBCODE:
            return True, ads_doc
        return False, None

    records = PdfIdentifier(client).resolve_many(
        [first, second],
        strategies=(
            IdentifyStrategy.FILENAME_BIBCODE,
            IdentifyStrategy.PDF_METADATA_DOI,
        ),
        providers=(DoiProvider.ADS, DoiProvider.CROSSREF),
        cached_candidate_lookup=cached,
    )

    assert records[first]["resolution_strategy"] == "filename_bibcode"
    assert records[first]["bibcode"] == BIBCODE
    assert records[second]["resolution_strategy"] == "pdf_metadata_doi"
    crossref.assert_called_once_with(OTHER_DOI)


def test_failed_bibcode_lookup_is_retried_for_the_next_strategy(tmp_path):
    path = tmp_path / f"{BIBCODE}.pdf"
    make_pdf(path, {"/DOI": DOI, "/Bibcode": BIBCODE})
    document = {"bibcode": BIBCODE, "doi": [DOI]}
    response = SearchResult(num_found=1, start=0, docs=[document])
    client = ads_client([ADSError("ADS unavailable"), response])

    record = PdfIdentifier(client).resolve(
        path,
        strategies=(
            IdentifyStrategy.FILENAME_BIBCODE,
            IdentifyStrategy.PDF_METADATA_BIBCODE,
        ),
        providers=(DoiProvider.ADS,),
    )

    assert record is not None
    assert record["resolution_strategy"] == "pdf_metadata_bibcode"
    assert client.search.query.call_count == 2


def test_successful_retry_can_establish_bibcode_absence(tmp_path):
    path = tmp_path / f"{BIBCODE}.pdf"
    make_pdf(path, {"/DOI": DOI, "/Bibcode": BIBCODE})
    empty = SearchResult(num_found=0, start=0, docs=[])
    client = ads_client([ADSError("ADS unavailable"), empty])

    record = PdfIdentifier(client).resolve(
        path,
        strategies=(
            IdentifyStrategy.FILENAME_BIBCODE,
            IdentifyStrategy.PDF_METADATA_BIBCODE,
        ),
        providers=(DoiProvider.ADS,),
    )

    assert record is None
    assert client.search.query.call_count == 2


def test_unrelated_unresolved_pdf_does_not_inherit_an_ads_error(tmp_path, monkeypatch):
    identified = tmp_path / f"{BIBCODE}.pdf"
    unrelated = tmp_path / "unrelated.pdf"
    make_pdf(identified, {"/DOI": DOI})
    make_pdf(unrelated, {})
    client = ads_client(ADSError("ADS unavailable"))
    monkeypatch.setattr(
        "adsfolio.pdf.identifier.fetch_crossref_doi",
        Mock(return_value=provider_document()),
    )

    records = PdfIdentifier(client).resolve_many(
        [identified, unrelated],
        strategies=(
            IdentifyStrategy.FILENAME_BIBCODE,
            IdentifyStrategy.PDF_METADATA_DOI,
        ),
        providers=(DoiProvider.ADS, DoiProvider.CROSSREF),
    )

    assert records[identified][IDENTITY_KEY]["status"] == "verified"
    assert records[unrelated] is None


def test_ads_error_on_one_doi_does_not_block_a_later_doi(tmp_path, monkeypatch):
    path = tmp_path / f"{BIBCODE} 10.5555_other.pdf"
    make_pdf(path, {"/DOI": DOI})
    client = ads_client(ADSError("ADS unavailable"))
    crossref = Mock(side_effect=lambda doi: provider_document() if doi == DOI else None)
    monkeypatch.setattr("adsfolio.pdf.identifier.fetch_crossref_doi", crossref)

    record = PdfIdentifier(client).resolve(
        path,
        strategies=(
            IdentifyStrategy.FILENAME_BIBCODE,
            IdentifyStrategy.FILENAME_DOI,
            IdentifyStrategy.PDF_METADATA_DOI,
        ),
        providers=(DoiProvider.ADS, DoiProvider.CROSSREF),
    )

    assert record is not None
    assert record["doi"] == DOI
    assert record["resolution_strategy"] == "pdf_metadata_doi"
    assert crossref.call_args_list[0].args == (OTHER_DOI,)
    assert crossref.call_args_list[1].args == (DOI,)


def test_successful_doi_retry_can_establish_absence(tmp_path, monkeypatch):
    path = tmp_path / "10.5555_example.pdf"
    make_pdf(path, {"/DOI": DOI})
    empty = SearchResult(num_found=0, start=0, docs=[])
    client = ads_client([ADSError("ADS unavailable"), empty])
    crossref = Mock(return_value=None)
    monkeypatch.setattr("adsfolio.pdf.identifier.fetch_crossref_doi", crossref)

    record = PdfIdentifier(client).resolve(
        path,
        strategies=(IdentifyStrategy.FILENAME_DOI, IdentifyStrategy.PDF_METADATA_DOI),
        providers=(DoiProvider.ADS, DoiProvider.CROSSREF),
    )

    assert record is None
    assert client.search.query.call_count == 2
    assert crossref.call_count == 2
