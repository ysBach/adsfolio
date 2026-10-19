"""
Export API wrapper for ADS.

Provides access to bibliography export in various formats:
- BibTeX, AASTeX, MNRAS, Icarus, etc.
- Custom format strings
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..client import BaseClient

__all__ = ["ExportAPI"]


class ExportAPI:
    """
    Export API for retrieving bibliographic data in various formats.

    Examples
    --------
    >>> api = ExportAPI(client)
    >>> bibtex = api.export(["2020ApJ...900..100S"], format="bibtex")
    >>> print(bibtex)
    """

    # Supported export formats
    FORMATS = [
        "bibtex",  # Standard BibTeX
        "bibtexabs",  # BibTeX with abstracts
        "ads",  # ADS format
        "aastex",  # AASTeX format
        "icarus",  # Icarus journal format
        "mnras",  # MNRAS format
        "soph",  # Solar Physics format
        "endnote",  # EndNote format
        "procite",  # ProCite format
        "ris",  # RIS format
        "refworks",  # RefWorks format
        "rss",  # RSS format
        "medlars",  # MEDLARS format
        "dcxml",  # Dublin Core XML
        "refxml",  # Reference XML
        "refabsxml",  # Reference with abstract XML
        "votable",  # VOTable format
        "custom",  # Custom format string
    ]

    def __init__(self, client: "BaseClient"):
        self._client = client

    def export(
        self,
        bibcodes: list[str],
        format: str = "bibtex",
        sort: str | None = None,
        custom_format: str | None = None,
    ) -> str:
        """
        Export bibcodes in the specified format.

        Parameters
        ----------
        bibcodes : list[str]
            List of bibcodes to export.
        format : str, optional
            Export format. Default: "bibtex".
            See FORMATS for available options.
        sort : str, optional
            Sort order, e.g., "first_author asc".
        custom_format : str, optional
            Custom format string (required if format="custom").
            See ADS documentation for format codes.

        Returns
        -------
        str
            Exported bibliography in requested format.

        Examples
        --------
        >>> bibtex = api.export(["2020ApJ...900..100S"], format="bibtex")
        >>> custom = api.export(bibcodes, format="custom",
        ...                     custom_format="%R %T")
        """
        # Determine endpoint
        if format == "custom" or format not in self.FORMATS:
            endpoint = "/export/custom"
        else:
            endpoint = f"/export/{format}"

        # Build payload
        payload = {"bibcode": bibcodes}

        if sort:
            payload["sort"] = sort

        if format == "custom" or custom_format:
            if not custom_format and format != "custom":
                # Treat unknown format as custom format string
                payload["format"] = format
            else:
                payload["format"] = custom_format
            endpoint = "/export/custom"

        response = self._client.post(endpoint, data=payload)
        return response.get("export", "")

    def bibtex(
        self,
        bibcodes: list[str],
        sort: str | None = None,
        with_abstract: bool = False,
    ) -> str:
        """
        Convenience method for BibTeX export.

        Parameters
        ----------
        bibcodes : list[str]
            List of bibcodes.
        sort : str, optional
            Sort order.
        with_abstract : bool, optional
            Include abstracts in output. Default: False.

        Returns
        -------
        str
            BibTeX formatted bibliography.
        """
        fmt = "bibtexabs" if with_abstract else "bibtex"
        return self.export(bibcodes, format=fmt, sort=sort)

    def aastex(self, bibcodes: list[str], sort: str | None = None) -> str:
        """Export in AASTeX format."""
        return self.export(bibcodes, format="aastex", sort=sort)

    def custom(
        self,
        bibcodes: list[str],
        format_string: str,
        sort: str | None = None,
    ) -> str:
        """
        Export with custom format string.

        Parameters
        ----------
        bibcodes : list[str]
            List of bibcodes.
        format_string : str
            Custom format string using ADS format codes.
            Examples: "%R %T", "%m %Y", "%10.5N_%Y_%q_%V_%p"
        sort : str, optional
            Sort order.

        Returns
        -------
        str
            Custom formatted output.
        """
        return self.export(
            bibcodes, format="custom", custom_format=format_string, sort=sort
        )
