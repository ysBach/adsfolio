"""
Metrics API wrapper for ADS.

Provides access to citation metrics, indicators, and histograms.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..client import BaseClient

__all__ = ["MetricsAPI", "MetricsResult"]


@dataclass
class MetricsResult:
    """Container for metrics data."""

    skipped_bibcodes: list[str]
    """Bibcodes for which no metrics were found."""

    basic_stats: dict | None = None
    """Basic publication and usage statistics."""

    basic_stats_refereed: dict | None = None
    """Basic stats for refereed papers only."""

    citation_stats: dict | None = None
    """Citation statistics."""

    citation_stats_refereed: dict | None = None
    """Citation stats for refereed papers."""

    indicators: dict | None = None
    """Bibliometric indicators (h-index, g-index, etc.)."""

    indicators_refereed: dict | None = None
    """Indicators for refereed papers."""

    histograms: dict | None = None
    """Publication, reads, downloads, citation histograms."""

    time_series: dict | None = None
    """Time series for indicators."""

    @classmethod
    def from_response(cls, data: dict) -> "MetricsResult":
        """Create MetricsResult from API response."""
        return cls(
            skipped_bibcodes=data.get("skipped bibcodes", []),
            basic_stats=data.get("basic stats"),
            basic_stats_refereed=data.get("basic stats refereed"),
            citation_stats=data.get("citation stats"),
            citation_stats_refereed=data.get("citation stats refereed"),
            indicators=data.get("indicators"),
            indicators_refereed=data.get("indicators refereed"),
            histograms=data.get("histograms"),
            time_series=data.get("time series"),
        )


class MetricsAPI:
    """
    Metrics API for retrieving citation and usage statistics.

    Examples
    --------
    >>> api = MetricsAPI(client)
    >>> metrics = api.get_metrics(["2020ApJ...900..100S"])
    >>> print(metrics.indicators["h"])
    """

    # Available metric types
    TYPES = ["basic", "citations", "indicators", "histograms", "timeseries"]

    # Available histogram types
    HISTOGRAM_TYPES = ["publications", "reads", "downloads", "citations"]

    def __init__(self, client: "BaseClient"):
        self._client = client

    def get_metrics(
        self,
        bibcodes: list[str],
        types: list[str] | None = None,
        histograms: list[str] | None = None,
    ) -> MetricsResult:
        """
        Get metrics for a set of bibcodes.

        Parameters
        ----------
        bibcodes : list[str]
            List of bibcodes to get metrics for.
        types : list[str], optional
            Types of metrics to retrieve. Default: all types.
            Options: "basic", "citations", "indicators", "histograms", "timeseries".
        histograms : list[str], optional
            Specific histograms to retrieve if "histograms" in types.
            Options: "publications", "reads", "downloads", "citations".

        Returns
        -------
        MetricsResult
            Metrics data for the bibcodes.

        Examples
        --------
        >>> metrics = api.get_metrics(bibcodes, types=["basic", "indicators"])
        >>> print(f"h-index: {metrics.indicators['h']}")
        """
        payload = {"bibcodes": bibcodes}

        if types:
            payload["types"] = types
        if histograms:
            payload["histograms"] = histograms

        response = self._client.post("/metrics", data=payload)
        return MetricsResult.from_response(response)

    def basic(self, bibcodes: list[str]) -> MetricsResult:
        """Get basic stats only."""
        return self.get_metrics(bibcodes, types=["basic"])

    def citations(self, bibcodes: list[str]) -> MetricsResult:
        """Get citation stats only."""
        return self.get_metrics(bibcodes, types=["citations"])

    def indicators(self, bibcodes: list[str]) -> MetricsResult:
        """Get bibliometric indicators (h-index, etc.)."""
        return self.get_metrics(bibcodes, types=["indicators"])

    def h_index(self, bibcodes: list[str]) -> int:
        """Get h-index for bibcodes."""
        result = self.indicators(bibcodes)
        if result.indicators:
            return result.indicators.get("h", 0)
        return 0
