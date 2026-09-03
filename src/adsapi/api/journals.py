"""
Journals API wrapper for ADS.

Provides access to journal metadata:
- Summary information by bibstem
- Journal name search
- Holdings and reference sources
- ISSN lookup
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..client import BaseClient

__all__ = ["JournalsAPI"]


class JournalsAPI:
    """
    Journals API for querying journal metadata.

    Examples
    --------
    >>> api = JournalsAPI(client)
    >>> summary = api.summary("ApJ")
    >>> print(summary["master"]["journal_name"])
    """

    def __init__(self, client: "BaseClient"):
        self._client = client

    def summary(self, bibstem: str) -> dict:
        """
        Get summary information for a journal by bibstem.

        Parameters
        ----------
        bibstem : str
            Journal bibstem (case-sensitive), e.g., "ApJ", "MNRAS".

        Returns
        -------
        dict
            Journal summary including master record, identifiers, abbreviations.
        """
        response = self._client.get(f"/journals/summary/{bibstem}")
        return response.get("summary", {})

    def search(self, query: str) -> list[dict]:
        """
        Search for journals by name or abbreviation.

        Parameters
        ----------
        query : str
            Search text to match against journal names.

        Returns
        -------
        list[dict]
            List of matching journal records.
        """
        from urllib.parse import quote

        encoded_query = quote(query)
        response = self._client.get(f"/journals/journal/{encoded_query}")
        return response.get("journal", [])

    def refsource(self, bibstem: str) -> dict:
        """
        Get citation data sources by volume for a journal.

        Parameters
        ----------
        bibstem : str
            Journal bibstem.

        Returns
        -------
        dict
            Reference source data per volume.
        """
        response = self._client.get(f"/journals/refsource/{bibstem}")
        return response.get("refsource", {})

    def holdings(self, bibstem: str, volume: str) -> dict:
        """
        Get available electronic sources for a journal/volume.

        Parameters
        ----------
        bibstem : str
            Journal bibstem.
        volume : str
            Volume number.

        Returns
        -------
        dict
            Holdings data with available sources.
        """
        response = self._client.get(f"/journals/holdings/{bibstem}/{volume}")
        return response.get("holdings", {})

    def issn(self, issn_number: str) -> str | None:
        """
        Look up bibstem by ISSN.

        Parameters
        ----------
        issn_number : str
            ISSN identifier (with or without hyphens).

        Returns
        -------
        str or None
            Matching bibstem, or None if not found.
        """
        response = self._client.get(f"/journals/issn/{issn_number}")
        return response.get("issn", {}).get("bibstem")

    def browse(self, bibstem: str) -> dict:
        """
        Get abbreviated summary for a journal.

        Parameters
        ----------
        bibstem : str
            Journal bibstem.

        Returns
        -------
        dict
            Abbreviated journal summary.
        """
        response = self._client.get(f"/journals/browse/{bibstem}")
        return response.get("browse", {})
