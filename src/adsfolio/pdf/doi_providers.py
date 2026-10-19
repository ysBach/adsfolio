"""Metadata provider abstractions for DOI lookup."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from enum import Enum
from typing import Any
from urllib.parse import quote

import requests

from .. import __version__
from .upgrade_utils import _extract_doi_resolver_path, _normalize_doi

logger = logging.getLogger(__name__)

DOI_PATTERN = re.compile(r"^10\.[0-9]{4,9}/[-._;()/:A-Za-z0-9<>]+$")


class DoiProvider(str, Enum):
    """Supported metadata providers for DOI lookup."""

    ADS = "ads"
    CROSSREF = "crossref"
    DATACITE = "datacite"


DEFAULT_DOI_PROVIDERS: tuple[DoiProvider, ...] = (
    DoiProvider.ADS,
    DoiProvider.CROSSREF,
    DoiProvider.DATACITE,
)

DOI_PROVIDER_LABELS: Mapping[DoiProvider, str] = {
    DoiProvider.ADS: "NASA/ADS",
    DoiProvider.CROSSREF: "Crossref",
    DoiProvider.DATACITE: "DataCite",
}

CROSSREF_API_URL = "https://api.crossref.org/works/{doi}"
DATACITE_API_URL = "https://api.datacite.org/dois/{doi}"
DEFAULT_TIMEOUT = (3.05, 15.0)
DEFAULT_HEADERS = {
    "User-Agent": f"ADSfolio/{__version__} (+https://github.com/ysbach/adsfolio)"
}


class ProviderError(Exception):
    """Base error for external DOI provider lookups."""


class ProviderRateLimitError(ProviderError):
    """Rate limit exceeded by DOI provider."""


class ProviderNetworkError(ProviderError):
    """Network connection or timeout failure contacting DOI provider."""


def _format_crossref_author(author: Mapping[str, Any]) -> str:
    family = str(author.get("family") or "").strip()
    given = str(author.get("given") or "").strip()
    name = str(author.get("name") or "").strip()
    if family and given:
        return f"{family}, {given}"
    if family:
        return family
    if name:
        return name
    return ""


def _extract_crossref_year(message: Mapping[str, Any]) -> str:
    for date_field in ("issued", "published-print", "published-online"):
        date_obj = message.get(date_field)
        if isinstance(date_obj, dict):
            parts = date_obj.get("date-parts")
            if (
                isinstance(parts, list)
                and parts
                and isinstance(parts[0], list)
                and parts[0]
            ):
                first_part = parts[0][0]
                if first_part is not None:
                    year_str = str(first_part).strip()
                    if year_str.isdigit() and len(year_str) == 4:
                        return year_str
    return ""


def fetch_crossref_doi(
    doi: str,
    *,
    session: requests.Session | None = None,
    timeout: tuple[float, float] = DEFAULT_TIMEOUT,
) -> dict[str, Any] | None:
    """Fetch metadata for an exact DOI from Crossref.

    Parameters
    ----------
    doi : str
        Exact DOI string.
    session : `requests.Session`, optional
        Reusable HTTP session.
    timeout : tuple of float
        Connect and read timeout in seconds.

    Returns
    -------
    dict or None
        Normalized document mapping if found, otherwise `None` on HTTP 404.

    Raises
    ------
    ValueError
        If doi is not a valid DOI format.
    ProviderRateLimitError
        If Crossref returns status 429.
    ProviderNetworkError
        If network connection or timeout fails.
    ProviderError
        If Crossref returns a 5xx/other error, malformed payload, or mismatched DOI.
    """
    clean_doi = _normalize_doi(doi)
    if not clean_doi or not DOI_PATTERN.fullmatch(clean_doi):
        raise ValueError(f"Invalid DOI format: {doi!r}")
    url = CROSSREF_API_URL.format(doi=quote(clean_doi, safe=""))
    http = session or requests
    try:
        response = http.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
    except requests.exceptions.Timeout as exc:
        logger.warning("Crossref network timeout for DOI %s: %s", clean_doi, exc)
        raise ProviderNetworkError(f"Crossref timeout for DOI {clean_doi}") from exc
    except requests.exceptions.RequestException as exc:
        logger.warning("Crossref network error for DOI %s: %s", clean_doi, exc)
        raise ProviderNetworkError(
            f"Crossref request failed for DOI {clean_doi}"
        ) from exc

    with response:
        if response.status_code == 404:
            return None
        if response.status_code == 429:
            logger.warning("Crossref rate limit exceeded (429) for DOI %s", clean_doi)
            raise ProviderRateLimitError(
                f"Crossref rate limit exceeded for DOI {clean_doi}"
            )
        if not response.ok:
            logger.warning(
                "Crossref error %d for DOI %s", response.status_code, clean_doi
            )
            raise ProviderError(
                f"Crossref returned status {response.status_code} for DOI {clean_doi}"
            )

        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            logger.warning("Malformed Crossref JSON for DOI %s: %s", clean_doi, exc)
            raise ProviderError(
                f"Malformed Crossref JSON response for DOI {clean_doi}"
            ) from exc

    if not isinstance(payload, dict):
        raise ProviderError(f"Crossref payload must be an object for DOI {clean_doi}")

    message = payload.get("message")
    if not isinstance(message, dict):
        raise ProviderError(
            f"Missing message object in Crossref response for DOI {clean_doi}"
        )

    raw_doi = str(message.get("DOI") or "").strip()
    if not raw_doi:
        raise ProviderError(f"Crossref response missing DOI field for {clean_doi}")
    resolved_doi = _normalize_doi(raw_doi)
    if resolved_doi.casefold() != clean_doi.casefold():
        raise ProviderError(
            f"Crossref returned mismatched DOI: expected {clean_doi}, got {resolved_doi}"
        )

    raw_titles = message.get("title")
    title_list: list[str] = []
    if isinstance(raw_titles, list):
        for item in raw_titles:
            if isinstance(item, str) and item.strip():
                title_list.append(item.strip())
    elif isinstance(raw_titles, str) and raw_titles.strip():
        title_list.append(raw_titles.strip())

    authors: list[str] = []
    raw_authors = message.get("author")
    if raw_authors is not None:
        if not isinstance(raw_authors, list):
            raise ProviderError(
                f"Crossref author field must be a list for DOI {clean_doi}"
            )
        for a in raw_authors:
            if isinstance(a, dict):
                formatted = _format_crossref_author(a)
                if formatted:
                    authors.append(formatted)

    year = _extract_crossref_year(message)

    containers = message.get("container-title")
    pub = ""
    if isinstance(containers, list) and containers:
        pub = str(containers[0]).strip()
    elif isinstance(containers, str):
        pub = containers.strip()

    publisher = str(message.get("publisher") or "").strip()

    volume = str(message.get("volume") or "").strip()
    page = str(message.get("page") or "").strip()
    page_list = [page] if page else []

    return {
        "doi": [resolved_doi],
        "title": title_list,
        "author": authors,
        "year": year,
        "pub": pub,
        "publisher": publisher,
        "volume": volume,
        "page": page_list,
    }


def _format_datacite_creator(creator: Mapping[str, Any]) -> str:
    name_type = str(creator.get("nameType") or "").strip().lower()
    name = str(creator.get("name") or "").strip()
    family = str(creator.get("familyName") or "").strip()
    given = str(creator.get("givenName") or "").strip()

    if name_type == "organizational":
        return name

    if family and given:
        return f"{family}, {given}"
    if family:
        return family
    if name:
        return name
    return ""


def _extract_datacite_year(attrs: Mapping[str, Any]) -> str:
    pub_year = str(attrs.get("publicationYear") or "").strip()
    if pub_year.isdigit() and len(pub_year) == 4:
        return pub_year
    dates = attrs.get("dates")
    if isinstance(dates, list):
        for d in dates:
            if isinstance(d, dict) and str(d.get("dateType") or "").lower() == "issued":
                date_val = str(d.get("date") or "").strip()
                if len(date_val) >= 4 and date_val[:4].isdigit():
                    return date_val[:4]
    return ""


def fetch_datacite_doi(
    doi: str,
    *,
    session: requests.Session | None = None,
    timeout: tuple[float, float] = DEFAULT_TIMEOUT,
) -> dict[str, Any] | None:
    """Fetch metadata for an exact DOI from DataCite.

    Parameters
    ----------
    doi : str
        Exact DOI string.
    session : `requests.Session`, optional
        Reusable HTTP session.
    timeout : tuple of float
        Connect and read timeout in seconds.

    Returns
    -------
    dict or None
        Normalized document mapping if found, otherwise `None` on HTTP 404.

    Raises
    ------
    ValueError
        If doi is not a valid DOI format.
    ProviderRateLimitError
        If DataCite returns status 429.
    ProviderNetworkError
        If network connection or timeout fails.
    ProviderError
        If DataCite returns a 5xx/other error, malformed payload, or mismatched DOI.
    """
    clean_doi = _normalize_doi(doi)
    if not clean_doi or not DOI_PATTERN.fullmatch(clean_doi):
        raise ValueError(f"Invalid DOI format: {doi!r}")
    url = DATACITE_API_URL.format(doi=quote(clean_doi, safe=""))
    headers = {**DEFAULT_HEADERS, "Accept": "application/vnd.api+json"}
    http = session or requests
    try:
        response = http.get(url, headers=headers, timeout=timeout)
    except requests.exceptions.Timeout as exc:
        logger.warning("DataCite network timeout for DOI %s: %s", clean_doi, exc)
        raise ProviderNetworkError(f"DataCite timeout for DOI {clean_doi}") from exc
    except requests.exceptions.RequestException as exc:
        logger.warning("DataCite network error for DOI %s: %s", clean_doi, exc)
        raise ProviderNetworkError(
            f"DataCite request failed for DOI {clean_doi}"
        ) from exc

    with response:
        if response.status_code == 404:
            return None
        if response.status_code == 429:
            logger.warning("DataCite rate limit exceeded (429) for DOI %s", clean_doi)
            raise ProviderRateLimitError(
                f"DataCite rate limit exceeded for DOI {clean_doi}"
            )
        if not response.ok:
            logger.warning(
                "DataCite error %d for DOI %s", response.status_code, clean_doi
            )
            raise ProviderError(
                f"DataCite returned status {response.status_code} for DOI {clean_doi}"
            )

        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            logger.warning("Malformed DataCite JSON for DOI %s: %s", clean_doi, exc)
            raise ProviderError(
                f"Malformed DataCite JSON response for DOI {clean_doi}"
            ) from exc

    if not isinstance(payload, dict):
        raise ProviderError(f"DataCite payload must be an object for DOI {clean_doi}")

    data = payload.get("data")
    if not isinstance(data, dict):
        raise ProviderError(
            f"Missing data object in DataCite response for DOI {clean_doi}"
        )
    attrs = data.get("attributes")
    if not isinstance(attrs, dict):
        raise ProviderError(
            f"Missing attributes in DataCite response for DOI {clean_doi}"
        )

    raw_doi = str(attrs.get("doi") or "").strip()
    if not raw_doi:
        raise ProviderError(f"DataCite response missing DOI field for {clean_doi}")
    resolved_doi = _normalize_doi(raw_doi)
    if resolved_doi.casefold() != clean_doi.casefold():
        raise ProviderError(
            f"DataCite returned mismatched DOI: expected {clean_doi}, got {resolved_doi}"
        )

    raw_titles = attrs.get("titles")
    main_titles: list[str] = []
    other_titles: list[str] = []
    if isinstance(raw_titles, list):
        for item in raw_titles:
            if isinstance(item, dict):
                t_str = str(item.get("title") or "").strip()
                if not t_str:
                    continue
                if not item.get("titleType"):
                    main_titles.append(t_str)
                else:
                    other_titles.append(t_str)
            elif isinstance(item, str) and item.strip():
                main_titles.append(item.strip())
    elif isinstance(raw_titles, str) and raw_titles.strip():
        main_titles.append(raw_titles.strip())

    title_list = main_titles if main_titles else other_titles

    authors: list[str] = []
    raw_creators = attrs.get("creators")
    if raw_creators is not None:
        if not isinstance(raw_creators, list):
            raise ProviderError(
                f"DataCite creators field must be a list for DOI {clean_doi}"
            )
        for c in raw_creators:
            if isinstance(c, dict):
                formatted = _format_datacite_creator(c)
                if formatted:
                    authors.append(formatted)

    year = _extract_datacite_year(attrs)

    container = attrs.get("container")
    pub = ""
    volume = ""
    page_list: list[str] = []
    if isinstance(container, dict):
        pub = str(container.get("title") or "").strip()
        volume = str(container.get("volume") or "").strip()
        first_page = str(container.get("firstPage") or "").strip()
        if first_page:
            page_list = [first_page]

    publisher = str(attrs.get("publisher") or "").strip()

    return {
        "doi": [resolved_doi],
        "title": title_list,
        "author": authors,
        "year": year,
        "pub": pub,
        "publisher": publisher,
        "volume": volume,
        "page": page_list,
    }


__all__ = [
    "DEFAULT_DOI_PROVIDERS",
    "DOI_PATTERN",
    "DOI_PROVIDER_LABELS",
    "DoiProvider",
    "ProviderError",
    "ProviderNetworkError",
    "ProviderRateLimitError",
    "_extract_doi_resolver_path",
    "_normalize_doi",
    "fetch_crossref_doi",
    "fetch_datacite_doi",
]
