"""
Search API wrapper for ADS.

Provides access to:
- /search/query - Standard search
- /search/qtree - Query parsing
- /search/bigquery - Bulk bibcode search
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..client import BaseClient

__all__ = ["SearchAPI", "SearchResult", "escape_query_phrase"]


def escape_query_phrase(value: str) -> str:
    """Escape a value interpolated inside an ADS quoted query phrase."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


@dataclass
class SearchResult:
    """Container for search results."""

    num_found: int
    """Total number of matching documents."""

    start: int
    """Starting index of returned results."""

    docs: list[dict]
    """List of document records."""

    query_time: int | None = None
    """Query execution time in ms."""

    @classmethod
    def from_response(cls, response: dict) -> "SearchResult":
        """Create SearchResult from API response."""
        resp = response.get("response", {})
        header = response.get("responseHeader", {})
        return cls(
            num_found=resp.get("numFound", 0),
            start=resp.get("start", 0),
            docs=resp.get("docs", []),
            query_time=header.get("QTime"),
        )

    def __len__(self) -> int:
        return len(self.docs)

    def __iter__(self):
        return iter(self.docs)

    @property
    def bibcodes(self) -> list[str]:
        """Extract bibcodes from results."""
        return [doc.get("bibcode") for doc in self.docs if "bibcode" in doc]


class SearchAPI:
    """
    Search API for querying ADS.

    Examples
    --------
    >>> api = SearchAPI(client)
    >>> results = api.query("author:Einstein", fl=["bibcode", "title"], rows=10)
    >>> for doc in results:
    ...     print(doc["bibcode"], doc["title"])
    """

    # Available fields for fl parameter (non-exhaustive)
    FIELDS = [
        "abstract",
        "ack",
        "aff",
        "alternate_bibcode",
        "alternate_title",
        "arxiv_class",
        "author",
        "bibcode",
        "bibgroup",
        "bibstem",
        "citation_count",
        "copyright",
        "data",
        "date",
        "database",
        "doi",
        "doctype",
        "first_author",
        "grant",
        "id",
        "identifier",
        "indexstamp",
        "issue",
        "keyword",
        "orcid_pub",
        "orcid_user",
        "orcid_other",
        "page",
        "property",
        "pub",
        "pubdate",
        "read_count",
        "title",
        "vizier",
        "volume",
        "year",
    ]

    def __init__(self, client: "BaseClient"):
        self._client = client

    def query(
        self,
        q: str,
        fl: list[str] | None = None,
        rows: int = 10,
        start: int = 0,
        sort: str | None = None,
        fq: str | None = None,
    ) -> SearchResult:
        """
        Search the ADS database.

        Parameters
        ----------
        q : str
            Search query string (Solr syntax).
        fl : list[str], optional
            Fields to return. Default: ["bibcode", "title", "author", "year"].
        rows : int, optional
            Number of results to return. Default: 10, max: 2000.
        start : int, optional
            Starting index for pagination. Default: 0.
        sort : str, optional
            Sort order, e.g., "citation_count desc" or "date asc".
        fq : str, optional
            Filter query to narrow results.

        Returns
        -------
        SearchResult
            Search results with docs and metadata.

        Examples
        --------
        >>> results = api.query("author:Einstein year:1905", rows=5)
        >>> results = api.query("black holes", fq="database:astronomy",
        ...                     sort="citation_count desc")
        """
        if fl is None:
            fl = ["bibcode", "title", "author", "year"]

        params = {
            "q": q,
            "fl": ",".join(fl),
            "rows": min(rows, 2000),
            "start": start,
        }

        if sort:
            params["sort"] = sort
        if fq:
            params["fq"] = fq

        response = self._client.get("/search/query", params=params)
        return SearchResult.from_response(response)

    def bigquery(
        self,
        bibcodes: list[str],
        fl: list[str] | None = None,
        rows: int | None = None,
    ) -> SearchResult:
        """
        Search by a list of bibcodes (max 2000).

        Parameters
        ----------
        bibcodes : list[str]
            List of bibcodes to retrieve.
        fl : list[str], optional
            Fields to return.
        rows : int, optional
            Number of results. Default: len(bibcodes).

        Returns
        -------
        SearchResult
            Search results for requested bibcodes.
        """
        if fl is None:
            fl = ["bibcode", "title", "author", "year"]

        if rows is None:
            rows = len(bibcodes)

        params = {
            "q": "*:*",
            "fl": ",".join(fl),
            "rows": min(rows, 2000),
        }

        # Build payload with bibcodes
        payload = "bibcode\n" + "\n".join(bibcodes)
        response = self._client.post_raw(
            "/search/bigquery",
            data=payload,
            params=params,
            headers={"Content-Type": "text/plain"},
        )
        return SearchResult.from_response(response)

    def qtree(self, q: str) -> dict:
        """
        Parse a query and return its Abstract Syntax Tree.

        Useful for understanding how queries are interpreted.

        Parameters
        ----------
        q : str
            Query string to parse.

        Returns
        -------
        dict
            Query tree structure.
        """
        response = self._client.get("/search/qtree", params={"q": q})
        return response.get("qtree", {})

    def query_all(
        self,
        q: str,
        fl: list[str] | None = None,
        page_size: int = 2000,
        sort: str | None = None,
        fq: str | None = None,
        max_records: int | None = None,
    ) -> SearchResult:
        """
        Search all pages and return aggregated results.

        Parameters
        ----------
        q : str
            Search query string (Solr syntax).
        fl : list[str], optional
            Fields to return.
        page_size : int, optional
            Number of records per request. Default: 2000 (ADS max).
        sort : str, optional
            Sort order.
        fq : str, optional
            Filter query.
        max_records : int, optional
            Maximum number of docs to collect. If None, collect all.

        Returns
        -------
        SearchResult
            Aggregated search result from all fetched pages.
        """
        if page_size <= 0:
            raise ValueError("page_size must be > 0.")
        if max_records is not None and max_records < 0:
            raise ValueError("max_records must be >= 0.")

        start = 0
        docs: list[dict] = []
        num_found = 0

        while True:
            rows = min(page_size, 2000)
            if max_records is not None:
                remain = max_records - len(docs)
                if remain <= 0:
                    break
                rows = min(rows, remain)

            page = self.query(
                q=q,
                fl=fl,
                rows=rows,
                start=start,
                sort=sort,
                fq=fq,
            )
            if start == 0:
                num_found = page.num_found

            if not page.docs:
                break

            docs.extend(page.docs)
            start += len(page.docs)

            if start >= page.num_found:
                break

        return SearchResult(
            num_found=num_found or len(docs),
            start=0,
            docs=docs,
            query_time=None,
        )
