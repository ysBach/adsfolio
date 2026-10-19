"""Unit tests for external DOI providers (Crossref and DataCite)."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from adsfolio.pdf.doi_providers import (
    ProviderError,
    ProviderNetworkError,
    ProviderRateLimitError,
    fetch_crossref_doi,
    fetch_datacite_doi,
)


def _make_mock_response(
    status_code: int, json_data: dict | None = None, ok: bool = True
):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.ok = ok
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError("No JSON")
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = None
    return resp


def test_invalid_doi_format_raises_value_error():
    with pytest.raises(ValueError, match="Invalid DOI format"):
        fetch_crossref_doi("not-a-doi")
    with pytest.raises(ValueError, match="Invalid DOI format"):
        fetch_datacite_doi("")


def test_crossref_success():
    payload = {
        "message": {
            "DOI": "10.1038/s41586-020-2649-2",
            "title": ["Array programming with NumPy"],
            "author": [
                {"given": "Charles R.", "family": "Harris"},
                {"name": "NumPy Team"},
            ],
            "issued": {"date-parts": [[2020, 9, 16]]},
            "container-title": ["Nature"],
            "publisher": "Springer Science and Business Media LLC",
            "volume": "585",
            "page": "357-362",
        }
    }
    with patch("requests.get", return_value=_make_mock_response(200, payload)):
        doc = fetch_crossref_doi("10.1038/s41586-020-2649-2")
    assert doc is not None
    assert doc["doi"] == ["10.1038/s41586-020-2649-2"]
    assert doc["title"] == ["Array programming with NumPy"]
    assert doc["author"] == ["Harris, Charles R.", "NumPy Team"]
    assert doc["year"] == "2020"
    assert doc["pub"] == "Nature"
    assert doc["publisher"] == "Springer Science and Business Media LLC"
    assert doc["volume"] == "585"
    assert doc["page"] == ["357-362"]
    assert "source" not in doc


def test_crossref_missing_doi_field_raises_provider_error():
    payload = {
        "message": {
            "title": ["Missing DOI"],
        }
    }
    with patch("requests.get", return_value=_make_mock_response(200, payload)):
        with pytest.raises(ProviderError, match="missing DOI"):
            fetch_crossref_doi("10.1038/s41586-020-2649-2")


def test_crossref_404_returns_none():
    with patch("requests.get", return_value=_make_mock_response(404, None, ok=False)):
        assert fetch_crossref_doi("10.1038/1234567") is None


def test_crossref_429_raises_rate_limit():
    with patch("requests.get", return_value=_make_mock_response(429, None, ok=False)):
        with pytest.raises(ProviderRateLimitError, match="rate limit exceeded"):
            fetch_crossref_doi("10.1038/1234567")


def test_crossref_500_raises_provider_error():
    with patch("requests.get", return_value=_make_mock_response(500, None, ok=False)):
        with pytest.raises(ProviderError, match="status 500"):
            fetch_crossref_doi("10.1038/1234567")


def test_crossref_network_timeout():
    with patch("requests.get", side_effect=requests.exceptions.Timeout("Timed out")):
        with pytest.raises(ProviderNetworkError, match="timeout"):
            fetch_crossref_doi("10.1038/1234567")


def test_crossref_mismatched_doi():
    payload = {
        "message": {
            "DOI": "10.1038/completely-different",
            "title": ["Different Title"],
        }
    }
    with patch("requests.get", return_value=_make_mock_response(200, payload)):
        with pytest.raises(ProviderError, match="mismatched DOI"):
            fetch_crossref_doi("10.1038/1234567")


def test_crossref_ignores_created_date():
    payload = {
        "message": {
            "DOI": "10.1038/1234567",
            "title": ["Title"],
            "created": {"date-parts": [[2025, 1, 1]]},
        }
    }
    with patch("requests.get", return_value=_make_mock_response(200, payload)):
        doc = fetch_crossref_doi("10.1038/1234567")
    assert doc["year"] == ""


def test_datacite_success_personal_and_organizational():
    payload = {
        "data": {
            "attributes": {
                "doi": "10.5281/zenodo.23258232",
                "titles": [
                    {"title": "Translation", "titleType": "TranslatedTitle"},
                    {"title": "Analysis code for project"},
                ],
                "creators": [
                    {
                        "nameType": "Personal",
                        "familyName": "Wang",
                        "givenName": "Jing",
                        "name": "Wang, Jing (redundant)",
                    },
                    {
                        "nameType": "Organizational",
                        "name": "Software Heritage Foundation",
                    },
                ],
                "publicationYear": 2026,
                "publisher": "Zenodo",
                "container": {
                    "title": "Zenodo Repository",
                    "volume": "1",
                    "firstPage": "10",
                },
            }
        }
    }
    with patch("requests.get", return_value=_make_mock_response(200, payload)):
        doc = fetch_datacite_doi("10.5281/zenodo.23258232")
    assert doc is not None
    assert doc["doi"] == ["10.5281/zenodo.23258232"]
    assert doc["title"] == ["Analysis code for project"]
    assert doc["author"] == ["Wang, Jing", "Software Heritage Foundation"]
    assert doc["year"] == "2026"
    assert doc["pub"] == "Zenodo Repository"
    assert doc["publisher"] == "Zenodo"
    assert doc["volume"] == "1"
    assert doc["page"] == ["10"]
    assert "source" not in doc


def test_datacite_missing_doi_field_raises_provider_error():
    payload = {
        "data": {
            "attributes": {
                "titles": [{"title": "Title"}],
            }
        }
    }
    with patch("requests.get", return_value=_make_mock_response(200, payload)):
        with pytest.raises(ProviderError, match="missing DOI"):
            fetch_datacite_doi("10.5281/zenodo.23258232")


def test_datacite_404_returns_none():
    with patch("requests.get", return_value=_make_mock_response(404, None, ok=False)):
        assert fetch_datacite_doi("10.5281/notfound123") is None


def test_datacite_429_raises_rate_limit():
    with patch("requests.get", return_value=_make_mock_response(429, None, ok=False)):
        with pytest.raises(ProviderRateLimitError, match="rate limit exceeded"):
            fetch_datacite_doi("10.5281/sample123")


def test_datacite_500_raises_provider_error():
    with patch("requests.get", return_value=_make_mock_response(500, None, ok=False)):
        with pytest.raises(ProviderError, match="status 500"):
            fetch_datacite_doi("10.5281/sample123")


def test_datacite_mismatched_doi():
    payload = {
        "data": {
            "attributes": {
                "doi": "10.5281/other",
                "titles": [{"title": "Title"}],
            }
        }
    }
    with patch("requests.get", return_value=_make_mock_response(200, payload)):
        with pytest.raises(ProviderError, match="mismatched DOI"):
            fetch_datacite_doi("10.5281/sample123")


def test_datacite_year_from_issued_date():
    payload = {
        "data": {
            "attributes": {
                "doi": "10.5281/sample123",
                "titles": [{"title": "Title"}],
                "dates": [
                    {"dateType": "Updated", "date": "2025-10-10"},
                    {"dateType": "Issued", "date": "2024-05-01"},
                ],
            }
        }
    }
    with patch("requests.get", return_value=_make_mock_response(200, payload)):
        doc = fetch_datacite_doi("10.5281/sample123")
    assert doc["year"] == "2024"
