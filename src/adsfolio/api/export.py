"""
Export API wrapper for ADS.

Provides access to bibliography export in various formats:
- BibTeX, AASTeX, MNRAS, Icarus, etc.
- Custom format strings
"""

from collections.abc import Callable, Sequence
from concurrent.futures import CancelledError
from typing import TYPE_CHECKING, ClassVar

from ..bibtex.sanitize import BIBTEX_FORMATS, repair_bibtex
from ..config import ADSError

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

    # ADS export_service/config.py specifies a maximum of 2,000 Solr records
    # per request. Keep this separate from the library pagination limit.
    MAX_RECORDS_PER_REQUEST = 2000
    BIBLIOGRAPHY_FORMATS = (
        "bibtex",
        "bibtexabs",
        "ris",
        "endnote",
        "aastex",
        "mnras",
        "icarus",
        "soph",
        "ads",
        "procite",
        "refworks",
        "medlars",
    )

    # Supported export formats
    FORMATS: ClassVar[list[str]] = [
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
        payload = {"bibcode": bibcodes}
        if sort:
            payload["sort"] = sort
        if format == "custom" or format not in self.FORMATS or custom_format:
            format_string = custom_format or (format if format != "custom" else "")
            if not format_string.strip():
                raise ValueError("custom_format is required for a custom export.")
            payload["format"] = format_string
            endpoint = "/export/custom"
        else:
            endpoint = f"/export/{format}"

        response = self._client.post(endpoint, data=payload)
        text = response.get("export", "")
        if endpoint == f"/export/{format}" and format in BIBTEX_FORMATS:
            text = repair_bibtex(text)
        return text

    def export_complete(
        self,
        bibcodes: Sequence[str],
        format: str = "bibtex",
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> str:
        """Export every requested record as one bibliography, or fail.

        Parameters
        ----------
        bibcodes : sequence of str
            Unique ADS Bibcodes. The input order is preserved across batches.
        format : str, optional
            A format in `BIBLIOGRAPHY_FORMATS`. Defaults to ``bibtex``.
            XML and custom formats require different assembly rules and are
            supported separately by ``export()``.
        cancelled : callable, optional
            Cancellation check before and after each network request.

        Returns
        -------
        str
            Complete bibliography. An empty input returns an empty string.

        Raises
        ------
        ValueError
            If the format is unsupported or Bibcodes are empty or duplicated.
        TypeError
            If `bibcodes` is a string rather than a sequence of identifiers.
        ADSError
            If ADS returns missing, repeated, or malformed records.
        concurrent.futures.CancelledError
            If cancellation is requested. No partial bibliography is returned.
        """
        if format not in self.BIBLIOGRAPHY_FORMATS:
            raise ValueError(f"Unsupported bibliography format: {format}")
        if isinstance(bibcodes, str):
            raise TypeError("Pass a sequence of Bibcodes, not a single string.")
        codes = tuple(code.strip() for code in bibcodes)
        if any(not code for code in codes) or len(
            {code.lower() for code in codes}
        ) != len(codes):
            raise ValueError("Bibcodes must be non-empty and unique.")
        references: list[str] = []
        wrappers: tuple[str, str] | None = None
        for start in range(0, len(codes), self.MAX_RECORDS_PER_REQUEST):
            if cancelled is not None and cancelled():
                raise CancelledError("Bibliography export cancelled.")
            batch = codes[start : start + self.MAX_RECORDS_PER_REQUEST]
            response = self._client.post(
                f"/export/{format}",
                data={"bibcode": list(batch), "sort": "no sort", "outputformat": 2},
            )
            if cancelled is not None and cancelled():
                raise CancelledError("Bibliography export cancelled.")
            documents = response.get("docs")
            count = response.get("num_docs")
            if (
                type(count) is not int
                or count != len(batch)
                or not isinstance(documents, list)
                or len(documents) != len(batch)
            ):
                raise ADSError(
                    "ADS returned an incomplete bibliography; no file was saved."
                )
            by_bibcode: dict[str, str] = {}
            for document in documents:
                if not isinstance(document, dict):
                    raise ADSError("ADS returned a malformed bibliography record.")
                code = document.get("bibcode")
                reference = document.get("reference")
                if (
                    not isinstance(code, str)
                    or not isinstance(reference, str)
                    or not reference.strip()
                    or code.lower() in by_bibcode
                ):
                    raise ADSError(
                        "ADS returned a missing or repeated bibliography record."
                    )
                if format in BIBTEX_FORMATS:
                    reference = repair_bibtex(reference)
                by_bibcode[code.lower()] = reference
            if set(by_bibcode) != {code.lower() for code in batch}:
                raise ADSError(
                    "ADS bibliography records did not match the requested library."
                )
            header, footer = response.get("header", ""), response.get("footer", "")
            if not isinstance(header, str) or not isinstance(footer, str):
                raise ADSError("ADS returned malformed bibliography headers.")
            if wrappers is not None and wrappers != (header, footer):
                raise ADSError("ADS bibliography headers changed between batches.")
            wrappers = (header, footer)
            references.extend(by_bibcode[code.lower()] for code in batch)
        if not references:
            return ""
        assert wrappers is not None
        return (
            "\n\n".join(
                part.rstrip("\r\n")
                for part in (wrappers[0], *references, wrappers[1])
                if part
            )
            + "\n"
        )

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
