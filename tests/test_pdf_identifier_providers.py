"""Tests for PdfIdentifier provider order and non-ADS integration."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from adsfolio.config import ADSError
from adsfolio.pdf.doi_providers import (
    DoiProvider,
    ProviderNetworkError,
    ProviderRateLimitError,
)
from adsfolio.pdf.identifier import PdfIdentifier
from adsfolio.pdf.identify_settings import IdentifyStrategy


def test_pdf_identifier_optional_client_without_token(tmp_path: Path):
    pdf = tmp_path / "10.1038_sample.pdf"
    pdf.write_bytes(b"%PDF-1.4 sample")

    identifier = PdfIdentifier(client=None)
    assert identifier.client is None

    mock_doc = {
        "doi": ["10.1038/sample"],
        "title": ["Sample Paper"],
        "author": ["Author, A."],
        "year": "2024",
    }
    with patch("adsfolio.pdf.identifier.fetch_crossref_doi", return_value=mock_doc):
        record = identifier.resolve(
            pdf,
            strategies=(IdentifyStrategy.FILENAME_DOI,),
            providers=(DoiProvider.CROSSREF,),
        )

    assert record is not None
    assert record["status"] == "unresolved"
    assert record["bibcode"] == ""
    assert record["doi"] == "10.1038/sample"
    assert record["title"] == ["Sample Paper"]
    assert record["_adsfolio_external_metadata"]["doi"] == "10.1038/sample"
    assert record["_adsfolio_metadata_sha256"] == identifier.compute_sha256(pdf)


def test_provider_order_crossref_before_ads(tmp_path: Path):
    pdf = tmp_path / "10.1038_sample.pdf"
    pdf.write_bytes(b"%PDF-1.4 sample")

    client = MagicMock()
    identifier = PdfIdentifier(client=client)

    crossref_doc = {
        "doi": ["10.1038/sample"],
        "title": ["Crossref Title"],
        "author": ["Author, C."],
        "year": "2023",
    }
    with patch(
        "adsfolio.pdf.identifier.fetch_crossref_doi", return_value=crossref_doc
    ) as mock_cr:
        record = identifier.resolve(
            pdf,
            strategies=(IdentifyStrategy.FILENAME_DOI,),
            providers=(DoiProvider.CROSSREF, DoiProvider.ADS),
        )

    assert mock_cr.called
    client.search.query.assert_not_called()
    assert record is not None
    assert record["title"] == ["Crossref Title"]
    assert record["status"] == "unresolved"


def test_provider_order_fallback_to_datacite_when_ads_misses(tmp_path: Path):
    pdf = tmp_path / "10.5281_zenodo.1234.pdf"
    pdf.write_bytes(b"%PDF-1.4 sample")

    client = MagicMock()
    # ADS search returns 0 documents
    client.search.query.return_value = MagicMock(docs=[])
    identifier = PdfIdentifier(client=client)

    datacite_doc = {
        "doi": ["10.5281/zenodo.1234"],
        "title": ["Dataset Title"],
        "author": ["Researcher, R."],
        "year": "2022",
    }
    with patch(
        "adsfolio.pdf.identifier.fetch_datacite_doi", return_value=datacite_doc
    ) as mock_dc:
        record = identifier.resolve(
            pdf,
            strategies=(IdentifyStrategy.FILENAME_DOI,),
            providers=(DoiProvider.ADS, DoiProvider.DATACITE),
        )

    assert mock_dc.called
    assert record is not None
    assert record["title"] == ["Dataset Title"]
    assert record["status"] == "unresolved"
    assert record["_adsfolio_external_metadata"]["doi"] == "10.5281/zenodo.1234"


def test_disabling_ads_skips_bibcode_and_title_strategies(tmp_path: Path):
    pdf = tmp_path / "2020ApJ...900..100S.pdf"
    pdf.write_bytes(b"%PDF-1.4 sample")

    client = MagicMock()
    identifier = PdfIdentifier(client=client)

    record = identifier.resolve(
        pdf,
        strategies=(
            IdentifyStrategy.FILENAME_BIBCODE,
            IdentifyStrategy.FIRST_PAGE_TITLE,
        ),
        providers=(DoiProvider.CROSSREF, DoiProvider.DATACITE),
    )

    # Since ADS is disabled in providers, bibcode and title lookups must be skipped
    client.search.query.assert_not_called()
    assert record is None


def test_cache_lookup_does_not_bypass_disabled_ads(tmp_path: Path):
    pdf = tmp_path / "10.1038_sample.pdf"
    pdf.write_bytes(b"%PDF-1.4 sample")

    identifier = PdfIdentifier(client=None)

    ads_cached_doc = {
        "bibcode": "2020ApJ...900..100S",
        "doi": ["10.1038/sample"],
        "title": ["ADS Cached"],
    }
    cached_lookup = MagicMock(return_value=(True, ads_cached_doc))

    with patch("adsfolio.pdf.identifier.fetch_crossref_doi", return_value=None):
        record = identifier.resolve(
            pdf,
            strategies=(IdentifyStrategy.FILENAME_DOI,),
            providers=(DoiProvider.CROSSREF,),
            cached_candidate_lookup=cached_lookup,
        )

    # cached_lookup should NOT have been consulted because ADS was not in providers
    cached_lookup.assert_not_called()
    assert record is None


def test_external_rate_limit_propagates_without_negative_cache(tmp_path: Path):
    pdf = tmp_path / "10.1038_sample.pdf"
    pdf.write_bytes(b"%PDF-1.4 sample")

    identifier = PdfIdentifier(client=None)

    with patch(
        "adsfolio.pdf.identifier.fetch_crossref_doi",
        side_effect=ProviderRateLimitError("429"),
    ):
        with pytest.raises(ProviderRateLimitError):
            identifier.resolve(
                pdf,
                strategies=(IdentifyStrategy.FILENAME_DOI,),
                providers=(DoiProvider.CROSSREF,),
            )


def test_external_network_error_propagates_without_negative_cache(tmp_path: Path):
    pdf = tmp_path / "10.1038_sample.pdf"
    pdf.write_bytes(b"%PDF-1.4 sample")

    identifier = PdfIdentifier(client=None)

    with patch(
        "adsfolio.pdf.identifier.fetch_crossref_doi",
        side_effect=ProviderNetworkError("timeout"),
    ):
        with pytest.raises(ProviderNetworkError):
            identifier.resolve(
                pdf,
                strategies=(IdentifyStrategy.FILENAME_DOI,),
                providers=(DoiProvider.CROSSREF,),
            )


def test_ads_error_propagates_when_queried(tmp_path: Path):
    pdf = tmp_path / "10.1038_sample.pdf"
    pdf.write_bytes(b"%PDF-1.4 sample")

    client = MagicMock()
    client.search.query.side_effect = ADSError("ADS server error")
    identifier = PdfIdentifier(client=client)

    with pytest.raises(ADSError):
        identifier.resolve(
            pdf,
            strategies=(IdentifyStrategy.FILENAME_DOI,),
            providers=(DoiProvider.ADS,),
        )
