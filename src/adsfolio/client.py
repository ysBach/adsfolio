"""
Base HTTP client for ADS API with rate limiting and caching.
"""

import json
import re
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote, urljoin

import requests

from .config import ADSError, AuthenticationError, Config, RateLimitError

__all__ = ["ADSClient", "BaseClient"]


class BaseClient:
    """
    Low-level HTTP client for ADS API.

    Handles authentication, rate limiting, and error handling.
    """

    def __init__(
        self,
        token: str | None = None,
        timeout: tuple[float, float] = (3.05, 30.0),
    ):
        """
        Initialize the client.

        Parameters
        ----------
        token : str, optional
            ADS API token. If not provided, will be loaded from config.
        timeout : tuple of float, optional
            Connection and read timeout in seconds.
        """
        self._token = token
        self._timeout = timeout
        self._session = requests.Session()

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self._session.close()

    @property
    def token(self) -> str:
        """Get token, loading from config if needed."""
        if self._token is None:
            self._token = Config.get_token()
        return self._token

    @property
    def headers(self) -> dict:
        """Standard headers for API requests."""
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def _handle_response(self, response: requests.Response) -> dict:
        """
        Handle API response, checking for errors.

        Parameters
        ----------
        response : requests.Response
            Response from the API.

        Returns
        -------
        dict
            JSON response data.

        Raises
        ------
        AuthenticationError
            If authentication failed (401).
        RateLimitError
            If rate limit exceeded (429).
        ADSError
            For other API errors.
        """
        # Update rate limits from headers
        Config.update_rate_limits(dict(response.headers))

        if response.status_code == 401:
            raise AuthenticationError("Authentication failed. Check your API token.")

        if response.status_code == 429:
            raise RateLimitError("Rate limit exceeded. Try again later.")

        if not response.ok:
            try:
                error_data = response.json()
                msg = error_data.get("error", response.text)
            except Exception:
                msg = response.text
            raise ADSError(f"API error {response.status_code}: {msg}")

        try:
            return response.json()
        except requests.exceptions.JSONDecodeError:
            # Some endpoints return plain text
            return {"text": response.text}

    def get(self, endpoint: str, params: dict | None = None) -> dict:
        """
        Make a GET request to the API.

        Parameters
        ----------
        endpoint : str
            API endpoint (e.g., "/search/query").
        params : dict, optional
            Query parameters.

        Returns
        -------
        dict
            JSON response.
        """
        url = f"{Config.API_BASE}{endpoint}"
        response = self._session.get(
            url,
            headers=self.headers,
            params=params,
            timeout=self._timeout,
        )
        return self._handle_response(response)

    def post(self, endpoint: str, data: dict | None = None) -> dict:
        """
        Make a POST request to the API.

        Parameters
        ----------
        endpoint : str
            API endpoint.
        data : dict, optional
            JSON payload.

        Returns
        -------
        dict
            JSON response.
        """
        url = f"{Config.API_BASE}{endpoint}"
        response = self._session.post(
            url,
            headers=self.headers,
            data=json.dumps(data) if data else None,
            timeout=self._timeout,
        )
        return self._handle_response(response)

    def post_raw(
        self,
        endpoint: str,
        data: str | bytes | None = None,
        params: dict | None = None,
        headers: dict | None = None,
    ) -> dict:
        """
        Make a POST request with a raw payload (non-JSON body).

        Parameters
        ----------
        endpoint : str
            API endpoint.
        data : str | bytes, optional
            Raw payload body.
        params : dict, optional
            Query parameters.
        headers : dict, optional
            Extra headers merged over default auth headers.

        Returns
        -------
        dict
            JSON response (or {"text": ...} for non-JSON responses).
        """
        url = f"{Config.API_BASE}{endpoint}"
        request_headers = dict(self.headers)
        if headers:
            request_headers.update(headers)
        response = self._session.post(
            url,
            headers=request_headers,
            params=params,
            data=data,
            timeout=self._timeout,
        )
        return self._handle_response(response)

    def put(self, endpoint: str, data: dict | None = None) -> dict:
        """Make a PUT request to the API."""
        url = f"{Config.API_BASE}{endpoint}"
        response = self._session.put(
            url,
            headers=self.headers,
            data=json.dumps(data) if data else None,
            timeout=self._timeout,
        )
        return self._handle_response(response)

    def delete(self, endpoint: str) -> dict:
        """Make a DELETE request to the API."""
        url = f"{Config.API_BASE}{endpoint}"
        response = self._session.delete(
            url,
            headers=self.headers,
            timeout=self._timeout,
        )
        return self._handle_response(response)


class ADSClient:
    """
    High-level ADS API client.

    Provides access to all API modules (Search, Export, Libraries, etc.)
    through a unified interface.

    Examples
    --------
    >>> client = ADSClient()
    >>> results = client.search.query("author:Einstein", rows=5)
    >>> bibtex = client.export.export(["2020ApJ...900..100S"], format="bibtex")
    """

    def __init__(
        self,
        token: str | None = None,
        timeout: tuple[float, float] = (3.05, 30.0),
    ):
        """
        Initialize the ADS client.

        Parameters
        ----------
        token : str, optional
            ADS API token. If not provided, will be loaded from config.
        timeout : tuple of float, optional
            Connection and read timeout in seconds.
        """
        self._base = BaseClient(token, timeout=timeout)

        # Lazy-loaded API modules
        self._search = None
        self._export = None
        self._libraries = None
        self._metrics = None
        self._journals = None

    def close(self) -> None:
        """Close the HTTP resources owned by this client."""
        self._base.close()

    @property
    def search(self):
        """Access Search API."""
        if self._search is None:
            from .api.search import SearchAPI

            self._search = SearchAPI(self._base)
        return self._search

    @property
    def export(self):
        """Access Export API."""
        if self._export is None:
            from .api.export import ExportAPI

            self._export = ExportAPI(self._base)
        return self._export

    @property
    def libraries(self):
        """Access Libraries API."""
        if self._libraries is None:
            from .api.libraries import LibrariesAPI

            self._libraries = LibrariesAPI(self._base)
        return self._libraries

    @property
    def metrics(self):
        """Access Metrics API."""
        if self._metrics is None:
            from .api.metrics import MetricsAPI

            self._metrics = MetricsAPI(self._base)
        return self._metrics

    @property
    def journals(self):
        """Access Journals API."""
        if self._journals is None:
            from .api.journals import JournalsAPI

            self._journals = JournalsAPI(self._base)
        return self._journals

    def rate_limits(self) -> dict:
        """Get current rate limit status."""
        return Config.get_rate_limits()

    @staticmethod
    def abstract_url(bibcode: str) -> str:
        """
        Build the ADS abstract URL for a bibcode.

        Parameters
        ----------
        bibcode : str
            ADS bibcode.

        Returns
        -------
        str
            URL to ADS abstract page.
        """
        bibcode = (bibcode or "").strip()
        if not bibcode:
            raise ValueError("bibcode must be a non-empty string.")
        return f"https://ui.adsabs.harvard.edu/abs/{quote(bibcode, safe='')}/abstract"

    @staticmethod
    def _safe_filename(text: str) -> str:
        """Convert text to a filesystem-safe filename stem."""
        return re.sub(r"[^A-Za-z0-9._-]", "_", text)

    @staticmethod
    def _is_pdf_response(response: requests.Response) -> bool:
        """Check whether an HTTP response appears to be PDF content."""
        content_type = (response.headers.get("Content-Type") or "").lower()
        content_disp = (response.headers.get("Content-Disposition") or "").lower()
        url = (response.url or "").lower()
        return (
            "application/pdf" in content_type
            or ".pdf" in content_disp
            or url.endswith(".pdf")
        )

    @staticmethod
    def _extract_pdf_links(html: str) -> list[str]:
        """Extract candidate PDF links from HTML content."""
        if not html:
            return []
        pattern = re.compile(
            r"""href=["']([^"']+?\.pdf(?:\?[^"']*)?)["']""",
            re.IGNORECASE,
        )
        return pattern.findall(html)

    @staticmethod
    def _save_stream_to_file(
        response: requests.Response,
        output_path: Path,
        chunk_size: int = 65536,
    ) -> Path:
        """Save streamed response content to disk."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("wb") as f:
            for chunk in response.iter_content(chunk_size=chunk_size):
                if chunk:
                    f.write(chunk)
        return output_path

    @staticmethod
    def _save_if_pdf_payload(
        response: requests.Response,
        output_path: Path,
    ) -> Path | None:
        """
        Save response and validate PDF magic bytes.

        This handles servers that return PDF bytes with non-standard
        Content-Type headers.
        """
        saved_path = ADSClient._save_stream_to_file(response, output_path)
        try:
            with saved_path.open("rb") as f:
                is_pdf = b"%PDF-" in f.read(1024)
        except OSError:
            return None

        if is_pdf:
            return saved_path

        try:
            saved_path.unlink()
        except OSError:
            pass
        return None

    @staticmethod
    def _print_webpage_to_pdf(
        url: str,
        output_path: Path,
        timeout: int = 30,
        html_content: str | None = None,
    ) -> bool:
        """
        Print a webpage to PDF using Playwright.

        Parameters
        ----------
        url : str
            Page URL, used as base URL when resolving relative resources.
        output_path : pathlib.Path
            Destination PDF file.
        timeout : int, optional
            Navigation / rendering timeout in seconds.
        html_content : str, optional
            Pre-fetched HTML to inject via ``page.set_content``.  When
            provided the browser never makes a fresh GET to *url*, which
            avoids bot-detection redirects from sites like Copernicus.
            Sub-resources (CSS, images) are still fetched using *url* as
            the base URL.

        Returns False when Playwright is unavailable or printing fails.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return False

        timeout_ms = max(int(timeout * 1000), 1000)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                context = browser.new_context(
                    viewport={"width": 1280, "height": 900},
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/122.0.0.0 Safari/537.36"
                    ),
                )
                context.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
                )
                page = context.new_page()
                if html_content is not None:
                    # Inject pre-fetched HTML; no server round-trip → no bot-detection.
                    # networkidle lets CSS/images finish loading via base_url.
                    page.set_content(html_content, base_url=url, timeout=timeout_ms)
                    page.wait_for_load_state("networkidle", timeout=timeout_ms)
                else:
                    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    page.wait_for_timeout(3000)
                page.emulate_media(media="print")
                page.pdf(
                    path=str(output_path),
                    format="A4",
                    print_background=True,
                    margin={
                        "top": "12mm",
                        "right": "10mm",
                        "bottom": "12mm",
                        "left": "10mm",
                    },
                )
                context.close()
                browser.close()
            return True
        except Exception:
            return False

    def download_pdf(
        self,
        bibcode: str,
        output_dir: str | Path = ".",
        link_types: Sequence[str] | None = None,
        force_print_html: bool = False,
        timeout: int = 30,
        printed_suffix: str = "__printed",
    ) -> Path | None:
        """
        Download a paper PDF by ADS bibcode via link-gateway.

        Parameters
        ----------
        bibcode : str
            ADS bibcode to fetch.
        output_dir : str or pathlib.Path, optional
            Destination directory for downloaded/printed PDFs.
        link_types : sequence of str, optional
            ADS gateway link types to try in order.
            Defaults to ("PUB_PDF", "EPRINT_PDF", "AUTHOR_PDF").
        force_print_html : bool, optional
            If True and only HTML landing pages are found, print final HTML page
            to PDF using Playwright as a fallback.
        timeout : int, optional
            Request timeout in seconds.
        printed_suffix : str, optional
            Suffix for printed fallback files.

        Returns
        -------
        pathlib.Path or None
            Saved PDF path on success, else None.
        """
        bibcode = (bibcode or "").strip()
        if not bibcode:
            raise ValueError("bibcode must be a non-empty string.")

        if link_types is None:
            link_types = ("PUB_PDF", "EPRINT_PDF", "AUTHOR_PDF", "PUB_HTML")

        output_dir = Path(output_dir)
        filename_stem = self._safe_filename(bibcode)
        direct_pdf_path = output_dir / f"{filename_stem}.pdf"
        printed_pdf_path = output_dir / f"{filename_stem}{printed_suffix}.pdf"

        ads_headers = {
            "Authorization": f"Bearer {self._base.token}",
            "User-Agent": "adsfolio-pdf-downloader",
        }
        external_headers = {"User-Agent": "adsfolio-pdf-downloader"}
        session = self._base._session

        for link_type in link_types:
            gateway_url = (
                f"https://ui.adsabs.harvard.edu/link_gateway/"
                f"{quote(bibcode, safe='')}/{quote(str(link_type), safe='')}"
            )
            try:
                response = session.get(
                    gateway_url,
                    headers=ads_headers,
                    allow_redirects=True,
                    timeout=timeout,
                    stream=True,
                )
            except requests.RequestException:
                continue

            if response.status_code != 200:
                continue

            if self._is_pdf_response(response):
                return self._save_stream_to_file(response, direct_pdf_path)

            content_type = (response.headers.get("Content-Type") or "").lower()
            if "text/html" not in content_type:
                continue

            html = response.text or ""
            for href in self._extract_pdf_links(html):
                pdf_url = urljoin(response.url, href)
                try:
                    candidate = session.get(
                        pdf_url,
                        headers=external_headers,
                        allow_redirects=True,
                        timeout=timeout,
                        stream=True,
                    )
                except requests.RequestException:
                    continue

                if candidate.status_code == 200:
                    if self._is_pdf_response(candidate):
                        return self._save_stream_to_file(candidate, direct_pdf_path)
                    saved = self._save_if_pdf_payload(candidate, direct_pdf_path)
                    if saved is not None:
                        return saved

            if force_print_html and self._print_webpage_to_pdf(
                response.url,
                printed_pdf_path,
                timeout=timeout,
                html_content=html,
            ):
                return printed_pdf_path

        return None

    def download_pdfs(
        self,
        bibcodes: Sequence[str],
        output_dir: str | Path = ".",
        link_types: Sequence[str] | None = None,
        force_print_html: bool = False,
        timeout: int = 30,
        printed_suffix: str = "__printed",
        show_progress: bool = True,
        progress_desc: str = "Downloading ADS PDFs",
        progress_leave: bool = False,
        max_workers: int = 8,
    ) -> dict[str, Path | None]:
        """
        Download PDFs for multiple bibcodes.

        Parameters
        ----------
        bibcodes : sequence of str
            Bibcodes to download.
        output_dir : str or pathlib.Path, optional
            Destination directory for downloaded/printed PDFs.
        link_types : sequence of str, optional
            Gateway link types to try in order.
        force_print_html : bool, optional
            If True, print HTML pages when direct PDF retrieval fails.
        timeout : int, optional
            Request timeout in seconds.
        printed_suffix : str, optional
            Suffix for printed fallback files.
        show_progress : bool, optional
            Show progress bar using tqdm when available. Default: True.
        progress_desc : str, optional
            Description text for progress bar.
        progress_leave : bool, optional
            Leave completed progress bar on screen.
        max_workers : int, optional
            Maximum number of parallel download workers. Use 1 for serial mode.
            Default: 8.

        Returns
        -------
        dict[str, pathlib.Path | None]
            Mapping from bibcode to downloaded file path (or None on failure).
        """
        clean_bibcodes = [(raw or "").strip() for raw in bibcodes]
        clean_bibcodes = [b for b in clean_bibcodes if b]
        if not clean_bibcodes:
            return {}

        if max_workers < 1:
            raise ValueError("max_workers must be >= 1.")

        results: dict[str, Path | None] = {}
        progress_bar = None
        if show_progress:
            try:
                from tqdm.auto import tqdm

                progress_bar = tqdm(
                    total=len(clean_bibcodes),
                    desc=progress_desc,
                    unit="paper",
                    leave=progress_leave,
                )
            except ImportError:
                progress_bar = None

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_bibcode = {
                executor.submit(
                    self.download_pdf,
                    bibcode,
                    output_dir,
                    link_types,
                    force_print_html,
                    timeout,
                    printed_suffix,
                ): bibcode
                for bibcode in clean_bibcodes
            }
            for future in as_completed(future_to_bibcode):
                bibcode = future_to_bibcode[future]
                try:
                    results[bibcode] = future.result()
                except Exception:
                    results[bibcode] = None
                if progress_bar is not None:
                    progress_bar.update(1)

        if progress_bar is not None:
            progress_bar.close()
        return results
