"""PDF-to-ADS record identification utilities."""

import hashlib
import logging
import os
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from math import hypot
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, TypeVar

if TYPE_CHECKING:
    from ..client import ADSClient

from ..api.search import escape_query_phrase
from ..config import AuthenticationError, RequestTooLongError
from .arxiv import normalize_arxiv_id
from .doi_providers import (
    DEFAULT_DOI_PROVIDERS,
    DoiProvider,
    ProviderError,
    fetch_crossref_doi,
    fetch_datacite_doi,
)
from .identify_settings import (
    RECOMMENDED_IDENTIFY_STRATEGIES,
    IdentifyStrategy,
)
from .identity import (
    IDENTITY_KEY,
    IdentityConflictError,
    IdentityValidationError,
    assert_mutation_allowed,
    extract_pdf_own_identity,
    target_identity,
)
from .upgrade_utils import _extract_doi_resolver_path, _normalize_doi

DOI_PATTERN = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
BIBCODE_PATTERN = re.compile(
    r"\b(\d{4}[A-Za-z&.]{5}[A-Za-z0-9.]{4}[A-Za-z0-9.][A-Za-z0-9.]{4}[A-Za-z])\b"
)
_ExtractedValue = TypeVar("_ExtractedValue")
AdsDocumentsCallback = Callable[[list[dict[str, Any]]], None]
_LOGGER = logging.getLogger(__name__)


def _fetch_external_doi(provider: DoiProvider, doi: str) -> dict[str, Any] | None:
    doc = None
    if provider is DoiProvider.CROSSREF:
        doc = fetch_crossref_doi(doi)
    elif provider is DoiProvider.DATACITE:
        doc = fetch_datacite_doi(doi)
    if doc is not None:
        doc["provider"] = provider.value
    return doc


def fetch_external_identifier(
    kind: str,
    value: str,
    providers: Iterable[DoiProvider],
) -> dict[str, Any] | None:
    """Look up a DOI or arXiv ID outside ADS, in the given provider order.

    An arXiv ID is looked up through its DataCite DOI, ``10.48550/arXiv.<id>``.
    ADS entries in `providers` are skipped. A provider error moves on to the
    next provider.

    Parameters
    ----------
    kind : `str`
        ``"doi"`` or ``"arxiv"``.
    value : `str`
        The DOI, or the arXiv ID without its version.
    providers : iterable of `DoiProvider`
        Provider order, usually the saved Identify setting.

    Returns
    -------
    dict or None
        Document metadata with a ``provider`` key, or `None` when no provider
        knows the identifier.
    """
    doi = value if kind == "doi" else f"10.48550/arXiv.{value}"
    for provider in providers:
        if provider is DoiProvider.ADS:
            continue
        try:
            doc = _fetch_external_doi(provider, doi)
        except (ValueError, ProviderError) as exc:
            _LOGGER.warning("%s lookup failed for %s: %s", provider.value, doi, exc)
            continue
        if doc is not None:
            if kind == "arxiv":
                doc["arxiv"] = value
            return doc
    return None


CachedCandidateLookup = Callable[[str, Any], tuple[bool, dict[str, Any] | None]]

__all__ = [
    "DOI_PATTERN",
    "AdsQueryResult",
    "PdfIdentifier",
    "extract_doi_candidates_pdf",
    "extract_first_page_text_pdf",
    "fetch_external_identifier",
]


@dataclass(frozen=True)
class AdsQueryResult:
    """Documents returned by one ADS query, including the total match count."""

    num_found: int
    documents: tuple[dict[str, Any], ...]


def _extract_doi_text(text: str) -> str | None:
    if not text:
        return None
    match = DOI_PATTERN.search(text)
    if not match:
        return None
    return match.group(0).rstrip(").,;")


def _extract_doi_from_metadata_map(metadata: Any) -> str | None:
    metadata_text = []
    for key, value in dict(metadata).items():
        if value is None:
            continue
        key_text = str(key).strip("/").lower()
        value_text = str(value).strip()
        if "doi" in key_text:
            candidate = re.sub(
                r"^doi\s*:\s*", "", value_text.strip(), flags=re.IGNORECASE
            ).strip()
            candidate = _extract_doi_resolver_path(candidate) or candidate
            if DOI_PATTERN.fullmatch(candidate):
                return candidate
            doi = _extract_doi_text(value_text)
            if doi:
                return doi
        metadata_text.append(value_text)
    return _extract_doi_text(" ".join(metadata_text))


def _extract_bibcode_from_metadata_map(metadata: Any) -> str | None:
    metadata_text = []
    for key, value in dict(metadata).items():
        if value is None:
            continue
        key_text = str(key).strip("/").lower()
        value_text = str(value).strip()
        if "bibcode" in key_text:
            match = BIBCODE_PATTERN.search(value_text)
            if match:
                return match.group(1)
        metadata_text.append(value_text)
    joined = " ".join(metadata_text)
    match = BIBCODE_PATTERN.search(joined)
    if not match:
        return None
    return match.group(1)


def extract_doi_candidates_pdf(
    pdf_path: Path,
    suppress_parser_warnings: bool = True,
) -> tuple[str | None, str | None]:
    """
    Extract DOI candidates directly from a PDF path in a single parser pass.

    Returns ``(metadata_doi, text_doi)`` where text DOI is only attempted when
    metadata DOI is missing.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        return None, None

    logger_names: set[str] = set()
    previous_levels: dict[str, int] = {}
    try:
        if suppress_parser_warnings:
            logger_names = {
                "pypdf",
                "pypdf._cmap",
            }
            logger_names.update(
                name
                for name in logging.root.manager.loggerDict
                if name.startswith("pypdf.")
            )
            for name in logger_names:
                logger = logging.getLogger(name)
                previous_levels[name] = logger.level
                logger.setLevel(logging.ERROR)

        reader = PdfReader(str(pdf_path))
        metadata = getattr(reader, "metadata", None) or {}
        metadata_doi = _extract_doi_from_metadata_map(metadata)
        if metadata_doi:
            return metadata_doi, None
        if not getattr(reader, "pages", None):
            return None, None
        page_text = reader.pages[0].extract_text() or ""
        return None, _extract_doi_text(page_text)
    except Exception:
        return None, None
    finally:
        for name, level in previous_levels.items():
            logging.getLogger(name).setLevel(level)


def extract_first_page_text_pdf(
    pdf_path: Path,
    suppress_parser_warnings: bool = True,
    max_chars: int = 1200,
) -> str:
    """Extract first-page text preview from PDF and normalize whitespace."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""

    logger_names: set[str] = set()
    previous_levels: dict[str, int] = {}
    try:
        if suppress_parser_warnings:
            logger_names = {
                "pypdf",
                "pypdf._cmap",
            }
            logger_names.update(
                name
                for name in logging.root.manager.loggerDict
                if name.startswith("pypdf.")
            )
            for name in logger_names:
                logger = logging.getLogger(name)
                previous_levels[name] = logger.level
                logger.setLevel(logging.ERROR)

        reader = PdfReader(str(pdf_path))
        if not getattr(reader, "pages", None):
            return ""
        text = reader.pages[0].extract_text() or ""
    except Exception:
        return ""
    finally:
        for name, level in previous_levels.items():
            logging.getLogger(name).setLevel(level)

    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return ""
    if max_chars > 0 and len(cleaned) > max_chars:
        return f"{cleaned[:max_chars].rstrip()}..."
    return cleaned


class PdfIdentifier:
    """Resolve ADS metadata for local PDF files."""

    SEARCH_FIELDS: ClassVar[list[str]] = [
        "abstract",
        "bibcode",
        "identifier",
        "alternate_bibcode",
        "citation_count",
        "doi",
        "title",
        "author",
        "author_count",
        "year",
        "bibstem",
        "volume",
        "page",
        "pub",
    ]
    MAX_BATCH_TERMS = 50
    MAX_ANALYSIS_WORKERS = 24

    def __init__(
        self,
        client: "ADSClient | None" = None,
        logger: logging.Logger | None = None,
        suppress_parser_warnings: bool = True,
    ) -> None:
        self.client = client
        self.logger = logger or logging.getLogger(__name__)
        self.suppress_parser_warnings = suppress_parser_warnings
        # Files whose candidate record was rejected by identity validation,
        # keyed by path. Callers must not move or overwrite these files.
        self.identity_failures: dict[Path, IdentityValidationError] = {}

    def compute_sha256(self, pdf_path: Path) -> str:
        """Compute SHA-256 for duplicate detection."""
        digest = hashlib.sha256()
        with Path(pdf_path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def pdf_is_readable(self, pdf_path: Path) -> bool:
        """Return whether a PDF can be parsed and contains at least one page."""
        try:
            with self._quiet_pypdf_logs():
                reader = self._load_reader(Path(pdf_path))
                if getattr(reader, "is_encrypted", False):
                    return False
                return len(reader.pages) > 0
        except (OSError, ValueError):
            return False
        except Exception as exc:
            self.logger.debug("PDF is not ready for parsing (%s): %s", pdf_path, exc)
            return False

    def _load_reader(self, pdf_path: Path) -> Any:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError(
                "pypdf is required for PDF identification. Install adsfolio with pypdf."
            ) from exc
        return PdfReader(str(pdf_path))

    @contextmanager
    def _quiet_pypdf_logs(self):
        """
        Temporarily suppress noisy non-fatal parser logs from pypdf.

        Many real-world publisher PDFs emit warnings that are not actionable for
        normal identify flows and can drown CLI output.
        """
        if not self.suppress_parser_warnings:
            yield
            return

        logger_names = {
            "pypdf",
            "pypdf._cmap",
        }
        logger_names.update(
            name
            for name in logging.root.manager.loggerDict
            if name.startswith("pypdf.")
        )

        previous_levels: dict[str, int] = {}
        for name in logger_names:
            logger = logging.getLogger(name)
            previous_levels[name] = logger.level
            logger.setLevel(logging.ERROR)
        try:
            yield
        finally:
            for name, level in previous_levels.items():
                logging.getLogger(name).setLevel(level)

    def _extract_doi(self, text: str) -> str | None:
        return _extract_doi_text(text)

    def extract_bibcode_from_text(self, text: str) -> str | None:
        """Extract likely ADS bibcode token from text."""
        if not text:
            return None
        match = BIBCODE_PATTERN.search(text)
        if not match:
            return None
        return match.group(1)

    def extract_doi_from_filename(self, filename: str) -> str | None:
        """Extract DOI-like token from filename variants."""
        stem = Path(filename).stem
        variants = [
            stem,
            stem.replace("_", "/"),
            stem.replace("_", "."),
            stem.replace("-", "/"),
        ]
        for candidate in variants:
            doi = _extract_doi_text(candidate)
            normalized = _normalize_doi(doi or "")
            if normalized:
                return normalized
        return None

    def extract_bibcode_from_filename(self, filename: str) -> str | None:
        """Extract bibcode-like token from filename variants."""
        stem = Path(filename).stem
        variants = [
            stem,
            stem.replace("_", "."),
            stem.replace("-", "."),
        ]
        for candidate in variants:
            bibcode = self.extract_bibcode_from_text(candidate)
            if bibcode:
                return bibcode
        return None

    def _first_page_text(self, pdf_path: Path) -> str:
        with self._quiet_pypdf_logs():
            reader = self._load_reader(pdf_path)
            if not getattr(reader, "pages", None):
                return ""
            page = reader.pages[0]
            return page.extract_text() or ""

    def extract_doi_from_metadata(self, pdf_path: Path) -> str | None:
        """Extract DOI from PDF metadata first, then metadata free text."""
        try:
            with self._quiet_pypdf_logs():
                reader = self._load_reader(pdf_path)
                metadata = getattr(reader, "metadata", None) or {}
        except Exception as exc:
            self.logger.debug("Failed reading metadata from %s: %s", pdf_path, exc)
            return None

        return self._extract_doi_from_metadata_obj(metadata)

    def _extract_doi_from_metadata_obj(self, metadata: Any) -> str | None:
        return _extract_doi_from_metadata_map(metadata)

    def extract_bibcode_from_metadata(self, pdf_path: Path) -> str | None:
        """Extract bibcode-like token from PDF metadata fields."""
        try:
            with self._quiet_pypdf_logs():
                reader = self._load_reader(pdf_path)
                metadata = getattr(reader, "metadata", None) or {}
        except Exception as exc:
            self.logger.debug("Failed reading metadata from %s: %s", pdf_path, exc)
            return None
        return _extract_bibcode_from_metadata_map(metadata)

    def extract_bibcode_from_pdf(self, pdf_path: Path) -> str | None:
        """Find a bibcode in searchable PDF text before requiring OCR."""
        try:
            with self._quiet_pypdf_logs():
                for page in self._load_reader(pdf_path).pages:
                    bibcode = self.extract_bibcode_from_text(page.extract_text() or "")
                    if bibcode:
                        return bibcode
        except Exception as exc:
            self.logger.debug("Failed extracting bibcode from %s: %s", pdf_path, exc)
        return None

    def extract_bibcode_from_first_page(self, pdf_path: Path) -> str | None:
        """Find a bibcode in first-page PDF text."""
        try:
            return self.extract_bibcode_from_text(self._first_page_text(pdf_path))
        except Exception as exc:
            self.logger.debug(
                "Failed extracting first-page bibcode from %s: %s", pdf_path, exc
            )
            return None

    def extract_preferred_bibcode(self, pdf_path: Path) -> str | None:
        """Extract one Bibcode candidate in filename/metadata/first-page order."""
        path = Path(pdf_path)
        return (
            self.extract_bibcode_from_filename(path.name)
            or self.extract_bibcode_from_metadata(path)
            or self.extract_bibcode_from_first_page(path)
        )

    def extract_doi_candidates(self, pdf_path: Path) -> tuple[str | None, str | None]:
        """
        Extract DOI candidates in one parse pass.

        Returns ``(metadata_doi, text_doi)`` where text DOI is only populated when
        metadata DOI is missing.
        """
        try:
            with self._quiet_pypdf_logs():
                reader = self._load_reader(pdf_path)
                metadata = getattr(reader, "metadata", None) or {}
                metadata_doi = self._extract_doi_from_metadata_obj(metadata)
                if metadata_doi:
                    return metadata_doi, None
                if not getattr(reader, "pages", None):
                    return None, None
                page_text = reader.pages[0].extract_text() or ""
        except Exception as exc:
            self.logger.debug(
                "Failed extracting DOI candidates from %s: %s",
                pdf_path,
                exc,
            )
            return None, None

        return None, self._extract_doi(page_text)

    def extract_doi_from_text(self, pdf_path: Path) -> str | None:
        """Extract DOI from first-page text."""
        try:
            return self._extract_doi(self._first_page_text(pdf_path))
        except Exception as exc:
            self.logger.debug("Failed extracting DOI text from %s: %s", pdf_path, exc)
            return None

    def extract_title_candidates(self, pdf_path: Path) -> tuple[str, ...]:
        """Extract first-page heading blocks for exact ADS title verification.

        Text larger than the character-weighted modal font size supplies
        candidates. This assumes the dominant size is body text. Font sizes
        include the PDF text and page transforms. Uniform-size OCR falls back
        to the text-boundary extractor; neither method proves a title's identity.
        """
        runs: list[tuple[float, str]] = []

        def visit(
            text: str, cm: list[float], tm: list[float], font: Any, size: float
        ) -> None:
            # pypdf also visits whole Form XObjects with no font. Those are
            # aggregate text, not glyph runs, and would double-count the page.
            if not text.strip():
                if runs:
                    runs[-1] = (runs[-1][0], runs[-1][1] + text)
                return
            if font is not None:
                scale = hypot(
                    tm[2] * cm[0] + tm[3] * cm[2], tm[2] * cm[1] + tm[3] * cm[3]
                )
                runs.append((abs(size * scale), text))

        try:
            with self._quiet_pypdf_logs():
                reader = self._load_reader(pdf_path)
                if not reader.pages:
                    return ()
                reader.pages[0].extract_text(visitor_text=visit)
        except Exception as exc:
            self.logger.debug("Typography extraction failed for %s: %s", pdf_path, exc)
        if not runs:
            title = self.extract_title_from_text(pdf_path)
            return (title,) if title else ()
        weights: Counter[float] = Counter()
        for size, text in runs:
            weights[size] += len(text.strip())
        body_size = weights.most_common(1)[0][0]
        blocks: list[tuple[float, str]] = []
        for size, text in runs:
            # Submission dates and abstract labels delimit the article heading.
            if re.match(
                r"^\s*[([]?\s*(?:abstract|received|accepted|submitted)\b",
                text,
                re.IGNORECASE,
            ):
                break
            if blocks and blocks[-1][0] == size:
                blocks[-1] = (size, blocks[-1][1] + text)
            else:
                blocks.append((size, text))
        candidates_list: list[str] = []
        for size, text in sorted(blocks, key=lambda block: -block[0]):
            if size <= body_size or not text.strip():
                continue
            block = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()
            candidates_list.append(block)
            # Proceedings often put the title and authors in the same run.
            # Retain the full block and its sentences for ADS to distinguish.
            candidates_list.extend(re.split(r"(?<=[.!?])\s+", block))
        candidates = tuple(dict.fromkeys(candidates_list))
        if candidates:
            return candidates
        title = self.extract_title_from_text(pdf_path)
        return (title,) if title else ()

    def extract_title_from_text(self, pdf_path: Path) -> str | None:
        """Heuristically infer paper title from the first page."""
        try:
            text = self._first_page_text(pdf_path)
        except Exception as exc:
            self.logger.debug("Failed extracting title text from %s: %s", pdf_path, exc)
            return None

        boundary_prefixes = (
            "abstract",
            "accepted",
            "affiliation",
            "arxiv",
            "author",
            "doi",
            "http",
            "received",
            "submitted",
            "to cite",
        )
        title_lines: list[str] = []
        for raw_line in text.splitlines():
            line = re.sub(r"\s+", " ", raw_line).strip()
            if not line:
                if title_lines:
                    break
                continue
            lower = line.casefold().lstrip("([ ")
            is_publisher_banner = "open access" in lower and (
                "pdf" in lower or "provided" in lower
            )
            if is_publisher_banner:
                continue
            if lower.startswith(boundary_prefixes) or DOI_PATTERN.search(line):
                if title_lines:
                    break
                continue
            title_lines.append(line)

        title = " ".join(title_lines).strip()
        return title or None

    def extract_title_from_metadata(self, pdf_path: Path) -> str | None:
        """Extract title from metadata fields."""
        try:
            with self._quiet_pypdf_logs():
                reader = self._load_reader(pdf_path)
                metadata = getattr(reader, "metadata", None) or {}
        except Exception as exc:
            self.logger.debug(
                "Failed reading title metadata from %s: %s", pdf_path, exc
            )
            return None

        for key, value in dict(metadata).items():
            if "title" not in str(key).lower() or value is None:
                continue
            title = re.sub(r"\s+", " ", str(value)).strip()
            if title:
                return title
        return None

    def parse_filename(
        self, filename: str, journal_map: Mapping[str, Any] | None = None
    ) -> dict[str, str]:
        """Extract author/year/journal/volume/page hints from filename."""
        pattern = re.compile(
            r"(?P<author>[A-Za-z][A-Za-z'`+.-]*)[_\-\s]+"
            r"(?P<year>(?:19|20)\d{2})[_\-\s]+"
            r"(?P<journal>[A-Za-z.&]+)[_\-\s]+"
            r"(?P<volume>\d{1,5})[_\-\s]+"
            r"(?P<page>[A-Za-z]?\d{1,6})"
        )
        stem = Path(filename).stem
        match = pattern.search(stem)
        if match is None:
            return {}

        parsed = match.groupdict()
        parsed["author"] = self._normalize_author_hint(parsed["author"])
        parsed["journal"] = self._resolve_journal(parsed["journal"], journal_map or {})
        return parsed

    def _normalize_author_hint(self, author_token: str) -> str:
        first = str(author_token).split("+", 1)[0].strip()
        if not first:
            return ""
        if "," in first:
            return first.split(",", 1)[0].strip()

        cleaned = re.sub(r"[^A-Za-z'`-]", "", first)
        if not cleaned:
            return ""

        # Common filename scheme is Surname+Initials (e.g., SmithJ).
        match = re.match(r"^(.*[a-z])([A-Z]{1,6})$", cleaned)
        if match is not None:
            return match.group(1)
        return cleaned

    def _resolve_journal(self, token: str, journal_map: Mapping[str, Any]) -> str:
        normalized = re.sub(r"[^A-Za-z0-9]", "", token).lower()
        if not normalized:
            return token

        for key, value in journal_map.items():
            key_norm = re.sub(r"[^A-Za-z0-9]", "", str(key)).lower()
            if key_norm == normalized:
                return str(key).rstrip(".")
            if isinstance(value, str):
                value_norm = re.sub(r"[^A-Za-z0-9]", "", value).lower()
                if value_norm == normalized:
                    return value.rstrip(".")
            if isinstance(value, dict):
                bibstem = str(value.get("bibstem", "")).rstrip(".")
                if re.sub(r"[^A-Za-z0-9]", "", bibstem).lower() == normalized:
                    return bibstem
        return token.rstrip(".")

    def query_documents(
        self,
        query: str,
        ads_documents_callback: AdsDocumentsCallback | None = None,
    ) -> AdsQueryResult:
        """Return one ADS result page and its total match count."""
        if self.client is None:
            self.logger.debug("No ADS client configured; skipping ADS query.")
            return AdsQueryResult(0, ())
        result = self.client.search.query(
            q=query,
            fl=self.SEARCH_FIELDS,
            rows=2000,
        )
        documents = tuple(result.docs)
        if ads_documents_callback is not None:
            ads_documents_callback(list(documents))
        return AdsQueryResult(num_found=int(result.num_found), documents=documents)

    def _query_unique_doc(
        self,
        query: str,
        ads_documents_callback: AdsDocumentsCallback | None = None,
    ) -> dict[str, Any] | None:
        result = self.query_documents(query, ads_documents_callback)
        if result.num_found != 1 or len(result.documents) != 1:
            self.logger.info("No unique ADS match for query=%s", query)
            return None
        return result.documents[0]

    def _chunked(self, items: list[str], chunk_size: int) -> list[list[str]]:
        return [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]

    def _analysis_worker_count(self, task_count: int) -> int:
        if task_count <= 1:
            return 1
        cpu_count = os.cpu_count() or 1
        return min(self.MAX_ANALYSIS_WORKERS, max(4, cpu_count * 2), task_count)

    def _parallel_extract_values(
        self,
        paths: list[Path],
        extractor: Callable[[Path], _ExtractedValue | None],
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> dict[Path, _ExtractedValue]:
        if not paths:
            return {}

        total = len(paths)
        worker_count = self._analysis_worker_count(total)
        extracted: dict[Path, _ExtractedValue] = {}

        if worker_count == 1:
            for index, path in enumerate(paths, start=1):
                value = extractor(path)
                if value:
                    extracted[path] = value
                if progress_callback is not None:
                    progress_callback(index, total)
            return extracted

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {executor.submit(extractor, path): path for path in paths}
            completed = 0
            for future in as_completed(futures):
                path = futures[future]
                try:
                    value = future.result()
                except Exception as exc:
                    self.logger.debug(
                        "Parallel extraction failed for %s: %s",
                        path,
                        exc,
                    )
                    value = None
                if value:
                    extracted[path] = value
                completed += 1
                if progress_callback is not None:
                    progress_callback(completed, total)
        return extracted

    def query_docs_for_dois(
        self,
        dois: list[str],
        progress_callback: Callable[[int, int], None] | None = None,
        ads_documents_callback: AdsDocumentsCallback | None = None,
    ) -> dict[str, dict[str, Any] | None]:
        """
        Resolve DOI candidates using batched ADS queries.

        Returns normalized DOI -> unique matching doc, or None when ambiguous/missing.
        """
        if self.client is None:
            self.logger.debug("No ADS client configured; skipping ADS DOI query.")
            return {}
        normalized = []
        seen: set[str] = set()
        for raw in dois:
            doi = _normalize_doi(raw)
            if not doi or doi in seen:
                continue
            seen.add(doi)
            normalized.append(doi)

        resolved: dict[str, dict[str, Any] | None] = {doi: None for doi in normalized}
        chunks = self._chunked(normalized, self.MAX_BATCH_TERMS)
        total_chunks = len(chunks)
        if progress_callback is not None and total_chunks > 0:
            progress_callback(0, total_chunks)
        for index, chunk in enumerate(chunks, start=1):
            terms = [f'doi:"{escape_query_phrase(doi)}"' for doi in chunk]
            query = " OR ".join(terms)
            rows = min(max(len(chunk) * 5, 10), 2000)
            result = self.client.search.query(
                q=query,
                fl=self.SEARCH_FIELDS,
                rows=rows,
            )
            documents = list(result.docs)
            if ads_documents_callback is not None:
                ads_documents_callback(documents)

            bucket: dict[str, dict[str, dict[str, Any]]] = {doi: {} for doi in chunk}
            for doc in documents:
                bibcode = self._first_scalar(doc.get("bibcode"))
                if not bibcode:
                    continue
                for doc_doi in self._as_list(doc.get("doi")):
                    doc_doi_norm = _normalize_doi(str(doc_doi))
                    if doc_doi_norm in bucket:
                        bucket[doc_doi_norm][bibcode] = doc

            for doi in chunk:
                docs = list(bucket[doi].values())
                if len(docs) == 1:
                    resolved[doi] = docs[0]
                elif len(docs) > 1:
                    self.logger.info("Ambiguous DOI match for doi=%s", doi)
            if progress_callback is not None:
                progress_callback(index, total_chunks)
        return resolved

    def query_docs_for_bibcodes(
        self,
        bibcodes: list[str],
        progress_callback: Callable[[int, int], None] | None = None,
        ads_documents_callback: AdsDocumentsCallback | None = None,
    ) -> dict[str, dict[str, Any] | None]:
        """
        Resolve bibcode candidates using batched ADS queries.

        Returns bibcode -> unique matching ADS document, or None when missing.
        """
        if self.client is None:
            self.logger.debug("No ADS client configured; skipping ADS bibcode query.")
            return {}
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in bibcodes:
            bibcode = str(raw).strip()
            if not bibcode:
                continue
            key = bibcode.lower()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(bibcode)

        resolved: dict[str, dict[str, Any] | None] = {code: None for code in normalized}
        chunks = self._chunked(normalized, self.MAX_BATCH_TERMS)
        total_chunks = len(chunks)
        if progress_callback is not None and total_chunks > 0:
            progress_callback(0, total_chunks)

        for index, chunk in enumerate(chunks, start=1):
            terms = [f'bibcode:"{code.replace(chr(34), " ")}"' for code in chunk]
            query = " OR ".join(terms)
            rows = min(max(len(chunk) * 2, 10), 2000)
            result = self.client.search.query(
                q=query,
                fl=self.SEARCH_FIELDS,
                rows=rows,
            )
            documents = list(result.docs)
            if ads_documents_callback is not None:
                ads_documents_callback(documents)

            docs_by_bibcode: dict[str, dict[str, Any]] = {}
            for doc in documents:
                bibcode = self._first_scalar(doc.get("bibcode"))
                if not bibcode:
                    continue
                docs_by_bibcode[bibcode.lower()] = doc

            for code in chunk:
                doc = docs_by_bibcode.get(code.lower())
                if doc is None:
                    arxiv = normalize_arxiv_id(code)
                    aliases = {
                        self._first_scalar(candidate.get("bibcode")): candidate
                        for candidate in documents
                        if any(
                            str(value).casefold() == code.casefold()
                            or (arxiv and normalize_arxiv_id(value) == arxiv)
                            for field in ("alternate_bibcode", "identifier")
                            for value in self._as_list(candidate.get(field))
                        )
                    }
                    if len(aliases) == 1:
                        doc = next(iter(aliases.values()))
                if doc is not None:
                    resolved[code] = doc

            if progress_callback is not None:
                progress_callback(index, total_chunks)
        return resolved

    def _resolve_doc_for_doi(
        self,
        doi: str,
        ads_documents_callback: AdsDocumentsCallback | None = None,
    ) -> dict[str, Any] | None:
        normalized = _normalize_doi(doi)
        if not normalized:
            return None
        docs_by_doi = self.query_docs_for_dois(
            [normalized], ads_documents_callback=ads_documents_callback
        )
        return docs_by_doi.get(normalized)

    def _resolve_doc_for_bibcode(
        self,
        bibcode: str,
        ads_documents_callback: AdsDocumentsCallback | None = None,
    ) -> dict[str, Any] | None:
        candidate = str(bibcode).strip()
        if not candidate:
            return None
        docs_by_bibcode = self.query_docs_for_bibcodes(
            [candidate], ads_documents_callback=ads_documents_callback
        )
        return docs_by_bibcode.get(candidate)

    def _resolve_many_doi_candidates(
        self,
        ordered_paths: list[Path],
        doi_by_path: Mapping[Path, str],
        sha256_by_path: Mapping[Path, str] | None = None,
        progress_callback: Callable[[str, int, int], None] | None = None,
        stage_prefix: str = "",
        ads_documents_callback: AdsDocumentsCallback | None = None,
    ) -> tuple[dict[Path, dict[str, Any]], list[Path], int]:
        def emit(stage: str, done: int, total: int) -> None:
            if progress_callback is None:
                return
            label = f"{stage_prefix}_{stage}" if stage_prefix else stage
            progress_callback(label, done, total)

        unique_dois: list[str] = []
        seen_dois: set[str] = set()
        for path in ordered_paths:
            normalized = _normalize_doi(doi_by_path.get(path, ""))
            if not normalized or normalized in seen_dois:
                continue
            seen_dois.add(normalized)
            unique_dois.append(normalized)

        if not unique_dois:
            return {}, list(ordered_paths), 0

        docs_by_doi = self.query_docs_for_dois(
            unique_dois,
            progress_callback=lambda done, total: emit("ads_doi_batches", done, total),
            ads_documents_callback=ads_documents_callback,
        )

        resolved: dict[Path, dict[str, Any]] = {}
        unresolved: list[Path] = []

        for path in ordered_paths:
            normalized = _normalize_doi(doi_by_path.get(path, ""))
            if not normalized:
                unresolved.append(path)
                continue

            doc = docs_by_doi.get(normalized)
            if doc is None:
                unresolved.append(path)
                continue

            resolved[path] = self.build_record(
                doc,
                path,
                sha256=sha256_by_path.get(path) if sha256_by_path is not None else None,
            )

        return resolved, unresolved, len(unique_dois)

    def _search_by_title(
        self,
        title: str,
        ads_documents_callback: AdsDocumentsCallback | None = None,
    ) -> dict[str, Any] | None:
        clean_title = escape_query_phrase(title.strip())
        return self._query_unique_doc(
            f'title:"{clean_title}"',
            ads_documents_callback=ads_documents_callback,
        )

    def _build_filename_query(self, parsed: Mapping[str, str]) -> str | None:
        if not parsed:
            return None
        query_parts = [
            f'author:"{parsed["author"]}"',
            f"year:{parsed['year']}",
            f'bibstem:"{parsed["journal"]}"',
            f"volume:{parsed['volume']}",
            f"page:{parsed['page']}",
        ]
        return " AND ".join(query_parts)

    def _norm_text(self, value: str) -> str:
        return re.sub(r"[^a-z0-9]", "", str(value).lower())

    def _norm_page(self, value: str) -> str:
        digits = re.sub(r"[^0-9]", "", str(value))
        return digits or self._norm_text(value)

    def _first_author_surname(self, authors: Any) -> str:
        values = self._as_list(authors)
        if not values:
            return ""
        first = str(values[0]).strip()
        if "," in first:
            return first.split(",", 1)[0].strip()
        parts = first.split()
        return parts[-1].strip() if parts else ""

    def _filename_hint_key(
        self, author: str, year: str, journal: str, volume: str, page: str
    ) -> tuple[str, str, str, str, str]:
        return (
            self._norm_text(author),
            self._norm_text(year),
            self._norm_text(journal),
            self._norm_text(volume),
            self._norm_page(page),
        )

    def _parsed_filename_hint_key(
        self, parsed: Mapping[str, str]
    ) -> tuple[str, str, str, str, str]:
        return self._filename_hint_key(
            parsed.get("author", ""),
            parsed.get("year", ""),
            parsed.get("journal", ""),
            parsed.get("volume", ""),
            parsed.get("page", ""),
        )

    def _doc_filename_hint_key(
        self, doc: Mapping[str, Any]
    ) -> tuple[str, str, str, str, str]:
        bibstem = self._as_list(doc.get("bibstem"))
        journal = str(bibstem[0]) if bibstem else self._first_scalar(doc.get("pub"))
        page_list = self._as_list(doc.get("page"))
        page = str(page_list[0]) if page_list else ""
        return self._filename_hint_key(
            self._first_author_surname(doc.get("author")),
            self._first_scalar(doc.get("year")),
            journal,
            self._first_scalar(doc.get("volume")),
            page,
        )

    def _query_docs_for_filename_hints(
        self,
        hints_by_key: Mapping[tuple[str, str, str, str, str], Mapping[str, str]],
        ads_documents_callback: AdsDocumentsCallback | None = None,
    ) -> dict[tuple[str, str, str, str, str], dict[str, Any] | None]:
        keys = list(hints_by_key.keys())
        resolved: dict[tuple[str, str, str, str, str], dict[str, Any] | None] = {
            key: None for key in keys
        }
        if not keys:
            return resolved

        buckets: dict[tuple[str, str, str, str, str], dict[str, dict[str, Any]]] = {
            key: {} for key in keys
        }
        for chunk_keys in self._chunked(keys, self.MAX_BATCH_TERMS):
            clauses: list[str] = []
            for key in chunk_keys:
                clause = self._build_filename_query(hints_by_key[key])
                if clause:
                    clauses.append(f"({clause})")
            if not clauses:
                continue

            query = " OR ".join(clauses)
            rows = min(max(len(chunk_keys) * 8, 20), 2000)
            result = self.client.search.query(q=query, fl=self.SEARCH_FIELDS, rows=rows)
            documents = list(result.docs)
            if ads_documents_callback is not None:
                ads_documents_callback(documents)
            for doc in documents:
                bibcode = self._first_scalar(doc.get("bibcode"))
                if not bibcode:
                    continue
                key = self._doc_filename_hint_key(doc)
                if key not in buckets:
                    continue
                buckets[key][bibcode] = doc

        for key, docs_by_bibcode in buckets.items():
            docs = list(docs_by_bibcode.values())
            if len(docs) == 1:
                resolved[key] = docs[0]
            elif len(docs) > 1:
                self.logger.info("Ambiguous filename-hint match for key=%s", key)
        return resolved

    def _resolve_many_filename_hints(
        self,
        pdf_paths: list[Path],
        journal_map: Mapping[str, Any] | None = None,
        sha256_by_path: Mapping[Path, str] | None = None,
        ads_documents_callback: AdsDocumentsCallback | None = None,
    ) -> tuple[dict[Path, dict[str, Any]], list[Path]]:
        parsed_by_path: dict[Path, tuple[str, str, str, str, str]] = {}
        hints_by_key: dict[tuple[str, str, str, str, str], dict[str, str]] = {}

        for path in pdf_paths:
            parsed = self.parse_filename(path.name, journal_map or {})
            if not self._build_filename_query(parsed):
                continue
            key = self._parsed_filename_hint_key(parsed)
            if not any(key):
                continue
            parsed_by_path[path] = key
            hints_by_key.setdefault(key, dict(parsed))

        docs_by_key = self._query_docs_for_filename_hints(
            hints_by_key, ads_documents_callback=ads_documents_callback
        )
        resolved: dict[Path, dict[str, Any]] = {}
        unresolved: list[Path] = []

        for path in pdf_paths:
            key = parsed_by_path.get(path)
            if key is None:
                unresolved.append(path)
                continue
            doc = docs_by_key.get(key)
            if doc is None:
                unresolved.append(path)
                continue
            resolved[path] = self.build_record(
                doc,
                path,
                sha256=sha256_by_path.get(path) if sha256_by_path is not None else None,
            )
        return resolved, unresolved

    def _as_list(self, value: Any) -> list:
        if isinstance(value, list):
            return value
        if value in (None, ""):
            return []
        return [value]

    def _first_scalar(self, value: Any) -> str:
        if isinstance(value, list):
            return str(value[0]) if value else ""
        if value is None:
            return ""
        return str(value)

    def build_record(
        self,
        doc: Mapping[str, Any],
        pdf_path: Path,
        sha256: str | None = None,
        *,
        manual_confirmation: bool = False,
    ) -> dict[str, Any]:
        """Build a library record, after checking the PDF is the paper in `doc`.

        Raises
        ------
        IdentityConflictError
            The PDF's own identifiers contradict `doc`.
        UnverifiedIdentityError
            The PDF does not confirm `doc` and `manual_confirmation` is false.
        """
        bibcode = self._first_scalar(doc.get("bibcode"))
        is_ads = bool(bibcode)
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        provider = str(doc.get("provider") or "").strip()
        record: dict[str, Any] = {
            "bibcode": bibcode,
            "doi": self._first_scalar(doc.get("doi")),
            "sha256": "",
            "title": self._as_list(doc.get("title")),
            "author": self._as_list(doc.get("author")),
            "year": self._first_scalar(doc.get("year")),
            "bibstem": self._as_list(doc.get("bibstem")),
            "volume": self._first_scalar(doc.get("volume")),
            "page": self._as_list(doc.get("page")),
            "pub": self._first_scalar(doc.get("pub")),
            "publisher": self._first_scalar(doc.get("publisher")),
            "current_filename": Path(pdf_path).name,
            "identified_at": now,
            "_adsfolio_metadata_sha256": "",
        }
        for field in ("identifier", "alternate_bibcode"):
            if doc.get(field):
                record[field] = self._as_list(doc[field])
        extra_dois = [
            str(doi)
            for doi in self._as_list(doc.get("doi"))[1:]
            if str(doi) not in record.get("identifier", [])
        ]
        if extra_dois:
            record["identifier"] = [*record.get("identifier", []), *extra_dois]
        if is_ads:
            record["status"] = "resolved"
        else:
            record["status"] = "unresolved"
            record["_adsfolio_external_metadata"] = {
                "provider": provider or "external",
                "doi": record["doi"],
                "identified_at": now,
            }
        # Validate the projection that will be stored, against the full
        # provider document, so the proof matches the stored identifiers.
        provenance = assert_mutation_allowed(
            pdf_path,
            record,
            expected_sha256=sha256 or None,
            manual_confirmation=manual_confirmation,
            provider_document=doc,
        )
        record["sha256"] = provenance["sha256"]
        record["_adsfolio_metadata_sha256"] = provenance["sha256"]
        record[IDENTITY_KEY] = provenance
        return record

    @staticmethod
    def _normalized_title(value: str) -> str:
        """Return a case- and punctuation-insensitive title key."""
        return re.sub(r"[^\w]+", "", str(value).casefold(), flags=re.UNICODE)

    def extract_arxiv_from_first_page(self, pdf_path: Path) -> str | None:
        """Return the PDF's own arXiv identifier if it states exactly one."""
        ids = extract_pdf_own_identity(pdf_path).arxiv_ids
        return next(iter(ids)) if len(ids) == 1 else None

    def query_docs_for_arxiv_ids(
        self,
        arxiv_ids: list[str],
        ads_documents_callback: AdsDocumentsCallback | None = None,
    ) -> dict[str, dict[str, Any] | None]:
        """Resolve arXiv identifiers through ADS ``identifier:"arXiv:ID"``.

        An identifier maps to a document only if exactly one returned document
        carries that arXiv identifier among its own aliases.
        """
        resolved: dict[str, dict[str, Any] | None] = {i: None for i in arxiv_ids}
        if self.client is None:
            return resolved
        for chunk in self._chunked(sorted(resolved), self.MAX_BATCH_TERMS):
            query = " OR ".join(
                f'identifier:"arXiv:{escape_query_phrase(i)}"' for i in chunk
            )
            result = self.client.search.query(
                q=query, fl=self.SEARCH_FIELDS, rows=min(max(len(chunk) * 5, 10), 2000)
            )
            documents = list(result.docs)
            if ads_documents_callback is not None:
                ads_documents_callback(documents)
            for arxiv_id in chunk:
                owners = {
                    self._first_scalar(doc.get("bibcode")): doc
                    for doc in documents
                    if arxiv_id in target_identity(doc).arxiv_ids
                }
                owners.pop("", None)
                if len(owners) == 1:
                    resolved[arxiv_id] = next(iter(owners.values()))
                elif owners:
                    self.logger.info("Ambiguous ADS match for arXiv:%s", arxiv_id)
        return resolved

    def _extract_strategy_candidates(
        self,
        strategy: IdentifyStrategy,
        paths: list[Path],
        journal_map: Mapping[str, Any],
        progress_callback: Callable[[str, int, int], None] | None,
    ) -> dict[Path, Any]:
        """Extract candidates for one configured strategy."""
        extractors: dict[IdentifyStrategy, Callable[[Path], Any]] = {
            IdentifyStrategy.FIRST_PAGE_ARXIV: self.extract_arxiv_from_first_page,
            IdentifyStrategy.FILENAME_BIBCODE: lambda path: (
                self.extract_bibcode_from_filename(path.name)
            ),
            IdentifyStrategy.FILENAME_DOI: lambda path: self.extract_doi_from_filename(
                path.name
            ),
            IdentifyStrategy.PDF_METADATA_BIBCODE: self.extract_bibcode_from_metadata,
            IdentifyStrategy.PDF_METADATA_DOI: self.extract_doi_from_metadata,
            IdentifyStrategy.FIRST_PAGE_BIBCODE: self.extract_bibcode_from_first_page,
            IdentifyStrategy.FIRST_PAGE_DOI: self.extract_doi_from_text,
            IdentifyStrategy.PDF_METADATA_TITLE: self.extract_title_from_metadata,
            IdentifyStrategy.FIRST_PAGE_TITLE: self.extract_title_candidates,
        }
        if strategy is IdentifyStrategy.STRUCTURED_FILENAME:
            return {
                path: parsed
                for path in paths
                if (parsed := self.parse_filename(path.name, journal_map))
            }

        extractor = extractors[strategy]
        progress = None
        if progress_callback is not None:

            def progress(done: int, total: int) -> None:
                progress_callback(f"{strategy.value}_extract", done, total)

        return self._parallel_extract_values(
            paths,
            extractor,
            progress_callback=progress,
        )

    def _query_bibcode_candidates(
        self,
        candidates: Mapping[Path, str],
        candidate_cache: dict[tuple[str, Any], dict[str, Any] | None],
        progress_callback: Callable[[str, int, int], None] | None,
        ads_documents_callback: AdsDocumentsCallback | None,
        cached_candidate_lookup: CachedCandidateLookup | None,
        allow_network_lookup: bool,
    ) -> None:
        pending: dict[str, str] = {}
        for candidate in candidates.values():
            value = str(candidate).strip()
            key = value.casefold()
            if not value or ("bibcode", key) in candidate_cache:
                continue
            if cached_candidate_lookup is not None:
                cache_hit, doc = cached_candidate_lookup("bibcode", value)
                if cache_hit:
                    candidate_cache[("bibcode", key)] = doc
                    continue
            pending.setdefault(key, value)
        if not allow_network_lookup or self.client is None:
            for key in pending:
                candidate_cache[("bibcode", key)] = None
            return
        docs = self.query_docs_for_bibcodes(
            list(pending.values()),
            progress_callback=(
                (
                    lambda done, total: progress_callback(
                        "ads_bibcode_batches", done, total
                    )
                )
                if progress_callback is not None
                else None
            ),
            ads_documents_callback=ads_documents_callback,
        )
        for key, value in pending.items():
            candidate_cache[("bibcode", key)] = docs.get(value)

    def _query_doi_candidates(
        self,
        candidates: Mapping[Path, str],
        candidate_cache: dict[tuple[str, Any], dict[str, Any] | None],
        progress_callback: Callable[[str, int, int], None] | None,
        ads_documents_callback: AdsDocumentsCallback | None,
        cached_candidate_lookup: CachedCandidateLookup | None,
        allow_network_lookup: bool,
        providers: Sequence[DoiProvider] = DEFAULT_DOI_PROVIDERS,
    ) -> None:
        pending: set[str] = set()
        for candidate in candidates.values():
            try:
                normalized = _normalize_doi(str(candidate))
            except ValueError:
                continue
            if not normalized or ("doi", normalized) in candidate_cache:
                continue
            pending.add(normalized)

        # Allow cached ADS DOI hits even when allow_network_lookup is False,
        # but only if ADS is active in providers.
        if (
            not allow_network_lookup
            and DoiProvider.ADS in providers
            and cached_candidate_lookup is not None
        ):
            for normalized in list(pending):
                cache_hit, doc = cached_candidate_lookup("doi", normalized)
                if cache_hit and doc is not None:
                    candidate_cache[("doi", normalized)] = doc
                    pending.remove(normalized)

        if not pending or not allow_network_lookup:
            if not allow_network_lookup:
                for normalized in pending:
                    candidate_cache[("doi", normalized)] = None
            return

        remaining = set(pending)
        for provider in providers:
            if not remaining:
                break

            if provider is DoiProvider.ADS:
                if cached_candidate_lookup is not None:
                    for normalized in list(remaining):
                        cache_hit, doc = cached_candidate_lookup("doi", normalized)
                        if cache_hit and doc is not None:
                            candidate_cache[("doi", normalized)] = doc
                            remaining.remove(normalized)

                if not remaining or self.client is None:
                    continue

                try:
                    docs = self.query_docs_for_dois(
                        sorted(remaining),
                        progress_callback=(
                            (
                                lambda done, total: progress_callback(
                                    "ads_doi_batches", done, total
                                )
                            )
                            if progress_callback is not None
                            else None
                        ),
                        ads_documents_callback=ads_documents_callback,
                    )
                except AuthenticationError as exc:
                    if len(providers) > 1:
                        self.logger.warning(
                            "ADS authentication token unavailable; skipping ADS provider: %s",
                            exc,
                        )
                        continue
                    raise

                for normalized in list(remaining):
                    doc = docs.get(normalized)
                    if doc is not None:
                        candidate_cache[("doi", normalized)] = doc
                        remaining.remove(normalized)

            elif provider in {DoiProvider.CROSSREF, DoiProvider.DATACITE}:
                for normalized in list(remaining):
                    try:
                        doc = _fetch_external_doi(provider, normalized)
                    except ValueError:
                        continue
                    if doc is not None:
                        candidate_cache[("doi", normalized)] = doc
                        remaining.remove(normalized)

        for normalized in remaining:
            candidate_cache[("doi", normalized)] = None

    def _query_structured_candidates(
        self,
        candidates: Mapping[Path, Mapping[str, str]],
        candidate_cache: dict[tuple[str, Any], dict[str, Any] | None],
        ads_documents_callback: AdsDocumentsCallback | None,
        cached_candidate_lookup: CachedCandidateLookup | None,
        allow_network_lookup: bool,
    ) -> None:
        hints: dict[tuple[str, str, str, str, str], Mapping[str, str]] = {}
        for parsed in candidates.values():
            key = self._parsed_filename_hint_key(parsed)
            if ("structured", key) in candidate_cache:
                continue
            if cached_candidate_lookup is not None:
                cache_hit, doc = cached_candidate_lookup("structured", key)
                if cache_hit:
                    candidate_cache[("structured", key)] = doc
                    continue
            hints.setdefault(key, parsed)
        if not allow_network_lookup or self.client is None:
            for key in hints:
                candidate_cache[("structured", key)] = None
            return
        docs = self._query_docs_for_filename_hints(
            hints,
            ads_documents_callback=ads_documents_callback,
        )
        for key in hints:
            candidate_cache[("structured", key)] = docs.get(key)

    def _query_title_candidates(
        self,
        candidates: Mapping[Path, str | tuple[str, ...]],
        candidate_cache: dict[tuple[str, Any], dict[str, Any] | None],
        ads_documents_callback: AdsDocumentsCallback | None,
        cached_candidate_lookup: CachedCandidateLookup | None,
        allow_network_lookup: bool,
    ) -> None:
        titles: dict[str, str] = {}
        title_values = (
            title
            for value in candidates.values()
            for title in ((value,) if isinstance(value, str) else value)
        )
        for title in title_values:
            normalized = self._normalized_title(title)
            if not normalized or ("title", normalized) in candidate_cache:
                continue
            if cached_candidate_lookup is not None:
                cache_hit, doc = cached_candidate_lookup("title", normalized)
                if cache_hit:
                    candidate_cache[("title", normalized)] = doc
                    continue
            titles.setdefault(normalized, str(title).strip())
        if not allow_network_lookup or self.client is None:
            for normalized in titles:
                candidate_cache[("title", normalized)] = None
            return

        multiple_candidates = any(
            not isinstance(value, str) for value in candidates.values()
        )

        def lookup(keys: list[str]) -> None:
            query = " OR ".join(
                f'title:"{escape_query_phrase(titles[key])}"' for key in keys
            )
            try:
                if multiple_candidates:
                    count = self.client.search.query(q=query, fl=["bibcode"], rows=0)
                    if count.num_found > 2000:
                        if len(keys) > 1:
                            middle = len(keys) // 2
                            lookup(keys[:middle])
                            lookup(keys[middle:])
                        else:
                            candidate_cache[("title", keys[0])] = None
                        return
                    if count.num_found == 0:
                        for key in keys:
                            candidate_cache[("title", key)] = None
                        return
                response = self.client.search.query(
                    q=query,
                    fl=["bibcode", "title"]
                    if multiple_candidates
                    else self.SEARCH_FIELDS,
                    rows=2000,
                )
                result = AdsQueryResult(int(response.num_found), tuple(response.docs))
            except RequestTooLongError:
                if len(keys) > 1:
                    middle = len(keys) // 2
                    lookup(keys[:middle])
                    lookup(keys[middle:])
                    return
                key = keys[0]
                candidate_cache[("title", key)] = None
                for path, candidate in candidates.items():
                    values = (candidate,) if isinstance(candidate, str) else candidate
                    if any(self._normalized_title(value) == key for value in values):
                        self.logger.warning(
                            "ADS rejected an oversized title query for %s; "
                            "skipping this title candidate.",
                            path,
                        )
                return
            if result.num_found != len(result.documents):
                # A broad phrase must not hide a second exact title match.
                if len(keys) > 1:
                    middle = len(keys) // 2
                    lookup(keys[:middle])
                    lookup(keys[middle:])
                else:
                    candidate_cache[("title", keys[0])] = None
                return
            matches: dict[str, dict[str, dict[str, Any]]] = {key: {} for key in keys}
            for doc in result.documents:
                bibcode = self._first_scalar(doc.get("bibcode"))
                if not bibcode:
                    continue
                for title in self._as_list(doc.get("title")):
                    key = self._normalized_title(title)
                    if key in matches:
                        matches[key][bibcode] = doc
            for key, documents in matches.items():
                candidate_cache[("title", key)] = (
                    next(iter(documents.values())) if len(documents) == 1 else None
                )

        # Keep single-title metadata lookups independent for cache compatibility.
        chunks = self._chunked(
            list(titles), self.MAX_BATCH_TERMS if multiple_candidates else 1
        )
        for chunk in chunks:
            lookup(chunk)
        # Cache only exact matches, never thousands of unrelated heading hits.
        verified = {
            self._first_scalar(doc.get("bibcode")): doc
            for key in titles
            if (doc := candidate_cache.get(("title", key))) is not None
        }
        if verified:
            documents = (
                self.query_docs_for_bibcodes(list(verified))
                if multiple_candidates
                else verified
            )
            for key in titles:
                doc = candidate_cache.get(("title", key))
                if doc is not None:
                    full = documents.get(self._first_scalar(doc.get("bibcode")))
                    # A changing ADS record must not silently lose title validation.
                    candidate_cache[("title", key)] = (
                        full
                        if full
                        and any(
                            self._normalized_title(title) == key
                            for title in self._as_list(full.get("title"))
                        )
                        else None
                    )
            if ads_documents_callback is not None:
                accepted = {
                    self._first_scalar(doc.get("bibcode")): doc
                    for key in titles
                    if (doc := candidate_cache.get(("title", key))) is not None
                }
                if accepted:
                    ads_documents_callback(list(accepted.values()))

    def _candidate_cache_key(
        self,
        strategy: IdentifyStrategy,
        candidate: Any,
    ) -> tuple[str, Any] | None:
        if candidate is None:
            return None
        if strategy in {
            IdentifyStrategy.FILENAME_BIBCODE,
            IdentifyStrategy.PDF_METADATA_BIBCODE,
            IdentifyStrategy.FIRST_PAGE_BIBCODE,
        }:
            normalized = str(candidate).strip().casefold()
            return ("bibcode", normalized) if normalized else None
        if strategy in {
            IdentifyStrategy.FILENAME_DOI,
            IdentifyStrategy.PDF_METADATA_DOI,
            IdentifyStrategy.FIRST_PAGE_DOI,
        }:
            normalized = _normalize_doi(str(candidate))
            return ("doi", normalized) if normalized else None
        if strategy is IdentifyStrategy.FIRST_PAGE_ARXIV:
            return ("arxiv", str(candidate))
        if strategy is IdentifyStrategy.STRUCTURED_FILENAME:
            return ("structured", self._parsed_filename_hint_key(candidate))
        normalized = self._normalized_title(str(candidate))
        return ("title", normalized) if normalized else None

    def _resolve_strategy_candidates(
        self,
        strategy: IdentifyStrategy,
        paths: list[Path],
        candidates: Mapping[Path, Any],
        candidate_cache: dict[tuple[str, Any], dict[str, Any] | None],
        sha256_by_path: Mapping[Path, str] | None,
        progress_callback: Callable[[str, int, int], None] | None,
        ads_documents_callback: AdsDocumentsCallback | None,
        cached_candidate_lookup: CachedCandidateLookup | None,
        allow_network_lookup: bool,
        providers: Sequence[DoiProvider] = DEFAULT_DOI_PROVIDERS,
    ) -> tuple[dict[Path, dict[str, Any]], list[Path]]:
        ads_enabled = DoiProvider.ADS in providers
        if strategy is IdentifyStrategy.FIRST_PAGE_ARXIV:
            pending = sorted(
                {
                    str(value)
                    for value in candidates.values()
                    if ("arxiv", str(value)) not in candidate_cache
                }
            )
            docs = (
                self.query_docs_for_arxiv_ids(pending, ads_documents_callback)
                if pending and ads_enabled and allow_network_lookup
                else dict.fromkeys(pending)
            )
            for key, doc in docs.items():
                candidate_cache[("arxiv", key)] = doc
        elif strategy in {
            IdentifyStrategy.FILENAME_BIBCODE,
            IdentifyStrategy.PDF_METADATA_BIBCODE,
            IdentifyStrategy.FIRST_PAGE_BIBCODE,
        }:
            if ads_enabled:
                self._query_bibcode_candidates(
                    candidates,
                    candidate_cache,
                    progress_callback,
                    ads_documents_callback,
                    cached_candidate_lookup,
                    allow_network_lookup,
                )
        elif strategy in {
            IdentifyStrategy.FILENAME_DOI,
            IdentifyStrategy.PDF_METADATA_DOI,
            IdentifyStrategy.FIRST_PAGE_DOI,
        }:
            self._query_doi_candidates(
                candidates,
                candidate_cache,
                progress_callback,
                ads_documents_callback,
                cached_candidate_lookup,
                allow_network_lookup,
                providers=providers,
            )
        elif strategy is IdentifyStrategy.STRUCTURED_FILENAME:
            if ads_enabled:
                self._query_structured_candidates(
                    candidates,
                    candidate_cache,
                    ads_documents_callback,
                    cached_candidate_lookup,
                    allow_network_lookup,
                )
        else:
            if ads_enabled:
                self._query_title_candidates(
                    candidates,
                    candidate_cache,
                    ads_documents_callback,
                    cached_candidate_lookup,
                    allow_network_lookup,
                )
        resolved: dict[Path, dict[str, Any]] = {}
        unresolved: list[Path] = []
        for path in paths:
            candidate = candidates.get(path)
            if strategy is IdentifyStrategy.FIRST_PAGE_TITLE and candidate:
                values = (candidate,) if isinstance(candidate, str) else candidate
                matches = {}
                for title in values:
                    key = self._candidate_cache_key(strategy, title)
                    match = candidate_cache.get(key)
                    if match is not None:
                        matches[self._first_scalar(match.get("bibcode"))] = match
                doc = next(iter(matches.values())) if len(matches) == 1 else None
                if len(matches) > 1:
                    self.logger.warning("Conflicting ADS title matches for %s", path)
            else:
                key = self._candidate_cache_key(strategy, candidate)
                doc = candidate_cache.get(key) if key is not None else None
            if doc is None:
                unresolved.append(path)
                continue
            try:
                record = self.build_record(
                    doc,
                    path,
                    sha256=sha256_by_path.get(path) if sha256_by_path else None,
                )
            except IdentityValidationError as exc:
                self.logger.warning("Rejected identification of %s: %s", path.name, exc)
                if not isinstance(
                    self.identity_failures.get(path), IdentityConflictError
                ):
                    self.identity_failures[path] = exc
                unresolved.append(path)
                continue
            self.identity_failures.pop(path, None)
            record["resolution_strategy"] = strategy.value
            resolved[path] = record
        return resolved, unresolved

    def resolve(
        self,
        pdf_path: Path,
        journal_map: Mapping[str, Any] | None = None,
        ads_documents_callback: AdsDocumentsCallback | None = None,
        cached_candidate_lookup: CachedCandidateLookup | None = None,
        allow_network_lookup: bool = True,
        *,
        strategies: tuple[IdentifyStrategy, ...] = RECOMMENDED_IDENTIFY_STRATEGIES,
        providers: tuple[DoiProvider, ...] = DEFAULT_DOI_PROVIDERS,
    ) -> dict[str, Any] | None:
        """Resolve one PDF with the canonical ordered strategy engine."""
        path = Path(pdf_path)
        return self.resolve_many(
            [path],
            journal_map=journal_map,
            strategies=strategies,
            providers=providers,
            ads_documents_callback=ads_documents_callback,
            cached_candidate_lookup=cached_candidate_lookup,
            allow_network_lookup=allow_network_lookup,
        )[path]

    def resolve_many(
        self,
        pdf_paths: list[Path],
        journal_map: Mapping[str, Any] | None = None,
        sha256_by_path: Mapping[Path, str] | None = None,
        progress_callback: Callable[[str, int, int], None] | None = None,
        ads_documents_callback: AdsDocumentsCallback | None = None,
        cached_candidate_lookup: CachedCandidateLookup | None = None,
        allow_network_lookup: bool = True,
        *,
        strategies: tuple[IdentifyStrategy, ...] = RECOMMENDED_IDENTIFY_STRATEGIES,
        providers: tuple[DoiProvider, ...] = DEFAULT_DOI_PROVIDERS,
    ) -> dict[Path, dict[str, Any] | None]:
        """Resolve PDFs in configured stage order while batching equal lookups."""
        ordered_paths = [Path(path) for path in pdf_paths]
        results: dict[Path, dict[str, Any] | None] = {
            path: None for path in ordered_paths
        }
        if not ordered_paths:
            return results

        self.identity_failures.clear()
        pending = list(ordered_paths)
        candidate_cache: dict[tuple[str, Any], dict[str, Any] | None] = {}
        for strategy in strategies:
            if not pending:
                break
            candidates = self._extract_strategy_candidates(
                strategy,
                pending,
                journal_map or {},
                progress_callback,
            )
            resolved, pending = self._resolve_strategy_candidates(
                strategy,
                pending,
                candidates,
                candidate_cache,
                sha256_by_path,
                progress_callback,
                ads_documents_callback,
                cached_candidate_lookup,
                allow_network_lookup,
                providers=providers,
            )
            results.update(resolved)
            self.logger.info(
                "resolve_many stage=%s candidates=%d resolved=%d remaining=%d",
                strategy.value,
                len(candidates),
                len(resolved),
                len(pending),
            )

        for path in pending:
            self.logger.warning("Unable to resolve PDF: %s", path.name)
        return results
