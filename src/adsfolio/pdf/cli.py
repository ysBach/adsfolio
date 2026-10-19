"""Click command group for PDF identification and organization."""

import csv
import json
import logging
import math
import os
import re
import shlex
import signal
import sys
import threading
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Literal

import click

from ..config import ADSError, Config
from .cli_common import get_client as _get_client
from .cli_common import handle_unresolved as _handle_unresolved
from .cli_common import iter_pdf_paths as _iter_pdf_paths
from .cli_common import validate_author_format as _validate_author_format
from .cli_common import validate_char_policy as _validate_char_policy
from .cli_upgrade import upgrade_command
from .identifier import (
    PdfIdentifier,
    extract_first_page_text_pdf,
)
from .identify import IdentifyWorkflowHelpers, run_identify
from .identify_settings import IdentifySettings, IdentifyStrategy
from .obsidian import ObsidianNoteWriter
from .registry import PdfRegistry, merge_ads_metadata, open_registry_state
from .renamer import DEFAULT_SCHEME, PdfRenamer
from .sidecar import Sidecar
from .upgrade import PdfUpgrader
from .upgrade_utils import _normalize_doi
from .watcher import PdfWatcher

LOGGER = logging.getLogger(__name__)
ADS_BLOCK_RISK_THRESHOLD = 1000
REFRESH_PROGRESS_UPDATE_COUNT = 50
MAX_ANALYSIS_WORKERS = 24
PROCESS_PARSE_THRESHOLD = 96
UNRESOLVED_FIRST_PAGE_MAX_CHARS = 1200
NON_OCR_MIN_ALNUM_CHARS = 40
IDENTIFY_PROGRESS_STAGES: dict[str, tuple[str, str]] = {
    "metadata_doi_extract": ("Fallback metadata DOI extraction", "pdf"),
    "text_doi_extract": ("Fallback text DOI extraction", "pdf"),
    "ads_bibcode_batches": ("ADS bibcode batches", "batch"),
    "ads_doi_batches": ("ADS DOI batches", "batch"),
    "title_text_extract": ("Fallback text-title extraction", "pdf"),
    "title_metadata_extract": ("Fallback metadata-title extraction", "pdf"),
    "title_fallback": ("ADS title fallback", "pdf"),
    "bibcode_batches": ("AI CSV ADS bibcode batches", "batch"),
    "doi_batches_ai": ("AI CSV ADS DOI batches", "batch"),
}

__all__ = ["pdf"]

_JOURNAL_COORDINATES = re.compile(
    r"^(?P<bibstem>[A-Za-z][A-Za-z0-9.&]{0,15})\s*[-:/\s]\s*"
    r"(?P<volume>\d+)\s*[-:/\s]\s*(?P<page>[A-Za-z]?\d+)$"
)


@click.group()
def pdf() -> None:
    """Identify, rename, and watch PDF files with ADS metadata."""


def load_library_identify_settings(directory: Path) -> IdentifySettings | None:
    """Load reusable settings attached to one library root."""
    state = open_registry_state(directory)
    try:
        return state.identify_settings
    finally:
        state.close()


def _citation_metrics_payload(metrics: Any) -> dict[str, int | None]:
    """Normalize ADS total, refereed, and non-refereed citation counts."""
    citation_stats = getattr(metrics, "citation_stats", None) or {}
    refereed_stats = getattr(metrics, "citation_stats_refereed", None) or {}
    total = citation_stats.get("total number of citations")
    refereed = refereed_stats.get("total number of citations")
    try:
        total_value = int(total) if total is not None else None
    except (TypeError, ValueError):
        total_value = None
    try:
        refereed_value = int(refereed) if refereed is not None else None
    except (TypeError, ValueError):
        refereed_value = None
    non_refereed = (
        total_value - refereed_value
        if total_value is not None and refereed_value is not None
        else None
    )
    return {
        "all_citations": total_value,
        "refereed_citations": refereed_value,
        "non_refereed_citations": non_refereed,
    }


def _ads_match_candidates(
    documents: tuple[dict[str, Any], ...],
) -> list[dict[str, str]]:
    """Build concise, selectable descriptions for ambiguous ADS documents."""
    candidates: list[dict[str, str]] = []
    for document in documents:
        bibcode = str(document.get("bibcode") or "").strip()
        if not bibcode:
            continue
        authors = document.get("author")
        first_author = (
            str(authors[0]).strip()
            if isinstance(authors, list) and authors
            else str(authors or "").strip()
        )
        titles = document.get("title")
        title = (
            str(titles[0]).strip()
            if isinstance(titles, list) and titles
            else str(titles or "").strip()
        )
        candidates.append(
            {
                "bibcode": bibcode,
                "first_author": first_author,
                "title": title,
            }
        )
    return candidates


def _report_ambiguous_ads_matches(documents: tuple[dict[str, Any], ...]) -> None:
    """Print user-readable and GUI-readable candidate metadata."""
    candidates = _ads_match_candidates(documents)
    click.secho("Multiple ADS documents match this identifier:", fg="yellow")
    for candidate in candidates:
        click.echo("- {bibcode} | {first_author} | {title}".format(**candidate))
    click.echo(f"ADS_MATCH_CANDIDATES_JSON:{json.dumps(candidates)}")


def _parse_ads_identifier(value: str) -> tuple[str, str]:
    """Classify a Bibcode, DOI, or journal-volume-page identifier."""
    candidate = re.sub(r"^doi\s*:\s*", "", value.strip(), flags=re.IGNORECASE)
    if candidate.lower().startswith("10.") and "/" in candidate:
        return "doi", _normalize_doi(candidate)
    coordinates = _JOURNAL_COORDINATES.fullmatch(candidate)
    if coordinates is not None:
        groups = coordinates.groupdict()
        return (
            "query",
            (
                f'bibstem:"{groups["bibstem"]}" '
                f'volume:"{groups["volume"]}" page:"{groups["page"]}"'
            ),
        )
    return "bibcode", candidate


def _record_bibcodes(directory: Path) -> list[str]:
    """Return distinct resolved bibcodes tracked by one registry."""
    state = open_registry_state(directory)
    try:
        return list(
            dict.fromkeys(
                str(
                    record.get("bibcode") or record["metadata"].get("bibcode") or ""
                ).strip()
                for record in state.registry.iter_files()
                if str(
                    record.get("bibcode") or record["metadata"].get("bibcode") or ""
                ).strip()
            )
        )
    finally:
        state.close()


def _refresh_file_metadata(
    registry: PdfRegistry,
    root: Path,
    relative_path: str,
    document: dict[str, Any],
) -> bool:
    """Apply one ADS document to an existing file, respecting locked fields."""
    record = registry.find_file_by_relative_path(root, relative_path)
    if record is None:
        return False
    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    if not registry.replace_file_metadata(
        root,
        relative_path,
        merge_ads_metadata(metadata, document),
    ):
        return False
    return registry.update_file_status(root, relative_path, "resolved")


def _refresh_bibcode_metadata(
    registry: PdfRegistry,
    root: Path,
    bibcode: str,
    document: dict[str, Any],
) -> int:
    """Apply one ADS document to all matching file records in `root`."""
    return sum(
        _refresh_file_metadata(registry, root, str(record["relative_path"]), document)
        for record in registry.find_files_by_bibcode(root, bibcode)
    )


def _clear_invalid_bibcode(
    registry: PdfRegistry,
    root: Path,
    bibcode: str,
    relative_path: str | None = None,
) -> int:
    """Clear an ADS-missing Bibcode while retaining local file tracking."""
    records = (
        [registry.find_file_by_relative_path(root, relative_path)]
        if relative_path is not None
        else registry.find_files_by_bibcode(root, bibcode)
    )
    cleared = 0
    for record in records:
        if (
            record is None
            or str(record.get("bibcode") or "").lower() != bibcode.lower()
        ):
            continue
        metadata = dict(record.get("metadata") or {})
        metadata["bibcode"] = ""
        if registry.replace_file_metadata(root, str(record["relative_path"]), metadata):
            registry.update_file_status(
                root, str(record["relative_path"]), "unresolved"
            )
            cleared += 1
    return cleared


@pdf.command("refresh-ads")
@click.argument(
    "directory", type=click.Path(exists=True, file_okay=False, path_type=Path)
)
@click.option("--bibcode", "bibcodes", multiple=True, help="Refresh one bibcode.")
@click.option(
    "--identifier",
    "identifier_input",
    help="Refresh by Bibcode, DOI, or journal-volume-page coordinates.",
)
@click.option(
    "--all",
    "refresh_all",
    is_flag=True,
    help="Refresh every resolved bibcode tracked by the registry.",
)
@click.option(
    "--relative-path",
    help="Update this one file record from the requested ADS bibcode.",
)
@click.option(
    "--citation-metrics",
    is_flag=True,
    help="Fetch per-paper all/refereed/non-refereed citation metrics.",
)
@click.pass_context
def refresh_ads_command(
    ctx: click.Context,
    directory: Path,
    bibcodes: tuple[str, ...],
    identifier_input: str | None,
    refresh_all: bool,
    relative_path: str | None,
    citation_metrics: bool,
) -> None:
    """Refresh ADS metadata, abstracts, and citation metrics."""
    requested = [str(bibcode).strip() for bibcode in bibcodes if str(bibcode).strip()]
    if identifier_input is not None and (requested or refresh_all):
        raise click.UsageError(
            "--identifier cannot be combined with --bibcode or --all."
        )
    if identifier_input is not None and relative_path is None:
        raise click.UsageError("--identifier requires --relative-path.")
    if refresh_all:
        requested.extend(_record_bibcodes(directory))
    requested = list(dict.fromkeys(requested))
    if not requested and identifier_input is None:
        raise click.UsageError("Pass --bibcode, --identifier, or --all.")
    if (
        relative_path is not None
        and identifier_input is None
        and (refresh_all or len(requested) != 1)
    ):
        raise click.UsageError(
            "--relative-path requires exactly one identifier and cannot be used with --all."
        )

    client = _get_client(ctx)
    state = open_registry_state(directory)
    try:
        pdf_identifier = PdfIdentifier(client)
        if identifier_input is not None:
            identifier_kind, identifier_value = _parse_ads_identifier(identifier_input)

            def cache_documents(values: list[dict[str, Any]]) -> None:
                state.registry.upsert_ads_documents(values, source="refresh-ads")

            callback = cache_documents
            if identifier_kind == "doi":
                documents = pdf_identifier.query_docs_for_dois(
                    [identifier_value],
                    ads_documents_callback=callback,
                )
            elif identifier_kind == "query":
                result = pdf_identifier.query_documents(
                    identifier_value,
                    ads_documents_callback=callback,
                )
                if result.num_found > 1:
                    _report_ambiguous_ads_matches(result.documents)
                    return
                documents = {
                    identifier_value: (
                        result.documents[0]
                        if result.num_found == 1 and len(result.documents) == 1
                        else None
                    )
                }
            else:
                documents = pdf_identifier.query_docs_for_bibcodes(
                    [identifier_value],
                    ads_documents_callback=callback,
                )
            requested = [identifier_value]
        else:
            documents = pdf_identifier.query_docs_for_bibcodes(
                requested,
                ads_documents_callback=lambda values: (
                    state.registry.upsert_ads_documents(
                        values,
                        source="refresh-ads",
                    )
                ),
            )
        missing_documents = [
            bibcode for bibcode, document in documents.items() if document is None
        ]
        refreshed = 0
        progress_step = max(
            1,
            math.ceil(len(requested) / REFRESH_PROGRESS_UPDATE_COUNT),
        )
        for index, bibcode in enumerate(requested, start=1):
            if bibcode in missing_documents:
                if identifier_input is None:
                    _clear_invalid_bibcode(state.registry, directory, bibcode)
                elif identifier_kind == "bibcode":
                    _clear_invalid_bibcode(
                        state.registry,
                        directory,
                        identifier_value,
                        relative_path,
                    )
                if index % progress_step == 0 or index == len(requested):
                    click.echo(f"Refresh ADS: {index}/{len(requested)}")
                continue
            refreshed += 1
            if citation_metrics:
                metrics = client.metrics.citations([bibcode])
                state.registry.set_ads_document_metrics(
                    bibcode,
                    _citation_metrics_payload(metrics),
                )
            if relative_path is not None:
                document = documents.get(bibcode)
                if document is not None and not _refresh_file_metadata(
                    state.registry,
                    directory,
                    relative_path,
                    document,
                ):
                    raise click.UsageError(
                        f"No tracked file found at relative path: {relative_path}"
                    )
            elif document := documents.get(bibcode):
                actual_bibcode = str(document.get("bibcode") or bibcode)
                _refresh_bibcode_metadata(
                    state.registry, directory, actual_bibcode, document
                )
            if index % progress_step == 0 or index == len(requested):
                click.echo(f"Refresh ADS: {index}/{len(requested)}")
        detail = " and citation metrics" if citation_metrics else ""
        click.echo(f"Refreshed ADS metadata{detail} for {refreshed} bibcode(s).")
        if missing_documents:
            click.secho(
                f"No ADS document found for {len(missing_documents)} bibcode(s).",
                fg="yellow",
            )
    finally:
        state.close()


def _analysis_worker_count(task_count: int) -> int:
    if task_count <= 1:
        return 1
    cpu_count = os.cpu_count() or 1
    return min(MAX_ANALYSIS_WORKERS, max(4, cpu_count * 2), task_count)


def _parallel_map_paths(
    paths: list[Path],
    worker: Callable[[Path], Any],
    desc: str,
    unit: str,
) -> dict[Path, Any]:
    if not paths:
        return {}

    total = len(paths)
    progress = _build_progress(total=total, desc=desc, unit=unit)
    started = perf_counter()
    text_step = max(1, total // 10)
    if progress is None:
        click.echo(f"{desc}: 0/{total}")
    results: dict[Path, Any] = {}
    worker_count = _analysis_worker_count(total)

    if worker_count == 1:
        completed = 0
        for path in paths:
            results[path] = worker(path)
            completed += 1
            if progress is not None:
                progress.update(1)
            elif completed % text_step == 0 or completed == total:
                click.echo(
                    (
                        f"{desc}: {completed}/{total} "
                        f"({(completed / total) * 100:.1f}%, "
                        f"elapsed {_format_elapsed(perf_counter() - started)}, "
                        f"{completed / max(perf_counter() - started, 1e-9):.1f}/s)"
                    )
                )
        if progress is not None:
            progress.close()
        return results

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {executor.submit(worker, path): path for path in paths}
        completed = 0
        for future in as_completed(futures):
            path = futures[future]
            try:
                results[path] = future.result()
            except Exception as exc:
                LOGGER.debug("Parallel analysis failed for %s: %s", path, exc)
                results[path] = None
            completed += 1
            if progress is not None:
                progress.update(1)
            elif completed % text_step == 0 or completed == total:
                click.echo(
                    (
                        f"{desc}: {completed}/{total} "
                        f"({(completed / total) * 100:.1f}%, "
                        f"elapsed {_format_elapsed(perf_counter() - started)}, "
                        f"{completed / max(perf_counter() - started, 1e-9):.1f}/s)"
                    )
                )
    if progress is not None:
        progress.close()
    return results


def _parallel_extract_first_page_previews(
    paths: list[Path],
    suppress_parser_warnings: bool,
    desc: str,
    unit: str,
    max_chars: int = UNRESOLVED_FIRST_PAGE_MAX_CHARS,
) -> dict[Path, str]:
    if not paths:
        return {}
    if len(paths) < PROCESS_PARSE_THRESHOLD:
        return _parallel_map_paths(
            paths,
            lambda pdf_path: extract_first_page_text_pdf(
                pdf_path,
                suppress_parser_warnings=suppress_parser_warnings,
                max_chars=max_chars,
            ),
            desc=desc,
            unit=unit,
        )

    total = len(paths)
    started = perf_counter()
    progress = _build_progress(total=total, desc=desc, unit=unit)
    text_step = max(1, total // 10)
    if progress is None:
        click.echo(f"{desc}: 0/{total}")

    worker_count = _analysis_worker_count(total)
    click.echo(f"{desc}: using {worker_count} worker processes.")
    results: dict[Path, str] = {}
    with ProcessPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(
                extract_first_page_text_pdf,
                path,
                suppress_parser_warnings,
                max_chars,
            ): path
            for path in paths
        }
        completed = 0
        for future in as_completed(futures):
            path = futures[future]
            try:
                text = future.result()
            except Exception as exc:
                LOGGER.debug("First-page text extraction failed for %s: %s", path, exc)
                text = ""
            results[path] = text
            completed += 1
            if progress is not None:
                progress.update(1)
            elif completed % text_step == 0 or completed == total:
                click.echo(
                    (
                        f"{desc}: {completed}/{total} "
                        f"({(completed / total) * 100:.1f}%, "
                        f"elapsed {_format_elapsed(perf_counter() - started)}, "
                        f"{completed / max(perf_counter() - started, 1e-9):.1f}/s)"
                    )
                )
    if progress is not None:
        progress.close()
    return results


def _estimate_ads_queries_for_targets(
    identifier: PdfIdentifier,
    pdf_paths: list[Path],
    strategies: tuple[IdentifyStrategy, ...],
) -> dict[str, Any]:
    """Estimate network work for only the enabled ordered strategies."""
    started = perf_counter()
    enabled = set(strategies)
    filename_dois: dict[Path, str] = {}
    metadata_dois: dict[Path, str] = {}
    text_dois: dict[Path, str] = {}
    filename_bibcodes: dict[Path, str] = {}
    metadata_bibcodes: dict[Path, str] = {}
    text_bibcodes: dict[Path, str] = {}

    if IdentifyStrategy.FILENAME_DOI in enabled:
        filename_dois = {
            path: doi
            for path in pdf_paths
            if (doi := identifier.extract_doi_from_filename(path.name))
        }
    if IdentifyStrategy.FILENAME_BIBCODE in enabled:
        filename_bibcodes = {
            path: bibcode
            for path in pdf_paths
            if (bibcode := identifier.extract_bibcode_from_filename(path.name))
        }
    if IdentifyStrategy.PDF_METADATA_DOI in enabled:
        metadata_dois = _parallel_map_paths(
            pdf_paths,
            identifier.extract_doi_from_metadata,
            desc="Analyze PDF metadata DOI parse",
            unit="pdf",
        )
    if IdentifyStrategy.PDF_METADATA_BIBCODE in enabled:
        metadata_bibcodes = _parallel_map_paths(
            pdf_paths,
            identifier.extract_bibcode_from_metadata,
            desc="Analyze PDF metadata bibcode parse",
            unit="pdf",
        )
    if IdentifyStrategy.FIRST_PAGE_DOI in enabled:
        text_dois = _parallel_map_paths(
            pdf_paths,
            identifier.extract_doi_from_text,
            desc="Analyze first-page DOI parse",
            unit="pdf",
        )

    first_page_strategies = {
        IdentifyStrategy.FIRST_PAGE_BIBCODE,
        IdentifyStrategy.FIRST_PAGE_DOI,
        IdentifyStrategy.FIRST_PAGE_TITLE,
    }
    first_page_preview_by_path = (
        _parallel_extract_first_page_previews(
            pdf_paths,
            suppress_parser_warnings=identifier.suppress_parser_warnings,
            desc="Analyze first-page preview",
            unit="pdf",
        )
        if enabled & first_page_strategies
        else {}
    )
    if IdentifyStrategy.FIRST_PAGE_BIBCODE in enabled:
        text_bibcodes = {
            path: bibcode
            for path, text in first_page_preview_by_path.items()
            if (bibcode := identifier.extract_bibcode_from_text(text))
        }

    doi_values = {
        normalized
        for values in (filename_dois, metadata_dois, text_dois)
        for value in values.values()
        if (normalized := _normalize_doi(value))
    }
    bibcode_values = {
        str(value).strip().casefold()
        for values in (filename_bibcodes, metadata_bibcodes, text_bibcodes)
        for value in values.values()
        if str(value).strip()
    }
    doi_paths = set(filename_dois) | set(metadata_dois) | set(text_dois)
    bibcode_paths = set(filename_bibcodes) | set(metadata_bibcodes) | set(text_bibcodes)
    structured_candidates = (
        sum(bool(identifier.parse_filename(path.name)) for path in pdf_paths)
        if IdentifyStrategy.STRUCTURED_FILENAME in enabled
        else 0
    )
    doi_batch_queries = math.ceil(len(doi_values) / identifier.MAX_BATCH_TERMS)
    bibcode_batch_queries = math.ceil(len(bibcode_values) / identifier.MAX_BATCH_TERMS)
    structured_queries = math.ceil(structured_candidates / identifier.MAX_BATCH_TERMS)
    title_strategy_count = sum(
        strategy in enabled
        for strategy in (
            IdentifyStrategy.PDF_METADATA_TITLE,
            IdentifyStrategy.FIRST_PAGE_TITLE,
        )
    )
    title_fallback_queries = title_strategy_count * len(pdf_paths)
    estimated = (
        doi_batch_queries
        + bibcode_batch_queries
        + structured_queries
        + title_fallback_queries
    )
    click.echo(
        "Identification analysis completed in "
        f"{_format_elapsed(perf_counter() - started)} "
        f"(enabled stages={len(strategies)}, estimated ADS queries={estimated})."
    )
    return {
        "pending_files": len(pdf_paths),
        "metadata_doi_files": len(metadata_dois),
        "text_doi_files": len(text_dois),
        "filename_doi_files": len(filename_dois),
        "no_doi_files": len(pdf_paths) - len(doi_paths),
        "doi_candidate_files": len(doi_paths),
        "unique_dois": len(doi_values),
        "doi_batch_queries": doi_batch_queries,
        "metadata_bibcode_files": len(metadata_bibcodes),
        "text_bibcode_files": len(text_bibcodes),
        "filename_bibcode_files": len(filename_bibcodes),
        "no_bibcode_files": len(pdf_paths) - len(bibcode_paths),
        "bibcode_candidate_files": len(bibcode_paths),
        "unique_bibcodes": len(bibcode_values),
        "bibcode_batch_queries": bibcode_batch_queries,
        "structured_filename_files": structured_candidates,
        "structured_filename_queries": structured_queries,
        "title_fallback_queries": title_fallback_queries,
        "estimated_ads_queries": estimated,
        "first_page_preview_by_path": first_page_preview_by_path,
    }


def _confirm_ads_queries(
    analysis: dict[str, Any],
    interactive: bool = True,
) -> tuple[Literal["all", "none"], int]:
    pending_count = int(analysis.get("pending_files", 0))
    estimated = int(analysis.get("estimated_ads_queries", 0))
    doi_batches = int(analysis.get("doi_batch_queries", 0))
    bibcode_batches = int(analysis.get("bibcode_batch_queries", 0))
    structured_queries = int(analysis.get("structured_filename_queries", 0))
    title_fallback_queries = int(analysis.get("title_fallback_queries", 0))
    doi_candidate_files = int(analysis.get("doi_candidate_files", 0))
    bibcode_candidate_files = int(analysis.get("bibcode_candidate_files", 0))
    no_doi_files = int(analysis.get("no_doi_files", 0))
    no_bibcode_files = int(analysis.get("no_bibcode_files", 0))
    unique_dois = int(analysis.get("unique_dois", 0))
    unique_bibcodes = int(analysis.get("unique_bibcodes", 0))
    click.secho(
        (
            f"About to run ADS queries for {pending_count} pending PDFs "
            f"(estimated up to {estimated} queries: DOI batches {doi_batches} + "
            f"bibcode batches {bibcode_batches} + structured filename "
            f"{structured_queries} + title fallback {title_fallback_queries})."
        ),
        fg="yellow",
    )
    click.secho(
        ("If you exceed ~1000 requests/24h, ADS may block you for ~24 hrs."),
        fg="yellow",
    )
    click.secho(
        (
            f"DOI-bearing PDFs: {doi_candidate_files} "
            f"(metadata/text/filename, unique DOI count: {unique_dois}). "
            f"Bibcode-bearing PDFs: {bibcode_candidate_files} "
            f"(metadata/text/filename, unique bibcode count: {unique_bibcodes}). "
            f"PDFs with no DOI: {no_doi_files}. PDFs with no bibcode: {no_bibcode_files}."
        ),
        fg="yellow",
    )
    if estimated <= 0:
        click.secho(
            "No ADS fallback queries available for unresolved files.", fg="yellow"
        )
        return "none", estimated

    if estimated >= ADS_BLOCK_RISK_THRESHOLD:
        click.secho(
            (
                f"Estimated ADS query volume (~{estimated}) is at or above the "
                "high-risk threshold."
            ),
            fg="red",
        )
        click.secho("Skipping ADS queries automatically.", fg="red")
        return "none", estimated

    if not interactive:
        click.secho("Proceeding with ADS queries (--allow-ads).", fg="yellow")
        return "all", estimated

    allowed = click.confirm(
        f"Proceed with {estimated} ADS queries?",
        default=False,
    )
    if allowed:
        return "all", estimated
    return "none", estimated


def _should_prompt_ads_permission() -> bool:
    return sys.stdin.isatty()


def _identify_workflow_helpers() -> IdentifyWorkflowHelpers:
    """Build the CLI-specific callbacks used by the identify workflow."""
    return IdentifyWorkflowHelpers(
        parallel_map_paths=_parallel_map_paths,
        to_sidecar_key=_to_sidecar_key,
        format_elapsed=_format_elapsed,
        profile_sync_payload=_profile_sync_payload,
        estimate_ads_queries_for_targets=_estimate_ads_queries_for_targets,
        should_prompt_ads_permission=_should_prompt_ads_permission,
        confirm_ads_queries=_confirm_ads_queries,
        progress_reporter_factory=_IdentifyProgressReporter,
        build_progress=_build_progress,
        parallel_extract_first_page_previews=_parallel_extract_first_page_previews,
        write_unresolved_csv=_write_unresolved_csv,
        print_unresolved_ai_prompt=_print_unresolved_ai_prompt,
        resolve_from_ai_csv=_resolve_from_ai_csv,
        ads_block_risk_threshold=ADS_BLOCK_RISK_THRESHOLD,
    )


def _build_progress(total: int | None, desc: str, unit: str) -> Any | None:
    if not sys.stderr.isatty():
        return None
    try:
        from tqdm.auto import tqdm
    except ImportError:
        return None
    return tqdm(
        total=total,
        desc=desc,
        unit=unit,
        leave=False,
        dynamic_ncols=True,
    )


class _IdentifyProgressReporter:
    """Render live identify progress via tqdm or periodic textual updates."""

    def __init__(self) -> None:
        self._bars: dict[str, Any | None] = {}
        self._counts: dict[str, int] = {}
        self._steps: dict[str, int] = {}
        self._started: dict[str, float] = {}

    def _meta(self, stage: str) -> tuple[str, str]:
        return IDENTIFY_PROGRESS_STAGES.get(stage, (stage.replace("_", " "), "item"))

    def _close_stage(self, stage: str, total: int) -> None:
        bar = self._bars.pop(stage, None)
        started = self._started.pop(stage, perf_counter())
        if bar is not None:
            bar.close()
        self._counts.pop(stage, None)
        self._steps.pop(stage, None)
        desc, _ = self._meta(stage)
        click.echo(
            (
                f"{desc} completed in "
                f"{_format_elapsed(perf_counter() - started)} "
                f"({total}/{total})."
            )
        )

    def __call__(self, stage: str, done: int, total: int) -> None:
        if total <= 0:
            return
        if stage not in self._bars:
            desc, unit = self._meta(stage)
            self._bars[stage] = _build_progress(total=total, desc=desc, unit=unit)
            self._counts[stage] = 0
            self._steps[stage] = max(1, total // 10)
            self._started[stage] = perf_counter()
            if self._bars[stage] is None:
                click.echo(f"{desc}: 0/{total}")

        bar = self._bars[stage]
        previous = self._counts.get(stage, 0)
        done = min(done, total)

        if bar is not None:
            delta = done - previous
            if delta > 0:
                bar.update(delta)
                self._counts[stage] = done
        else:
            step = self._steps.get(stage, 1)
            if done >= previous + step or done == total:
                desc, _ = self._meta(stage)
                elapsed = perf_counter() - self._started.get(stage, perf_counter())
                click.echo(
                    (
                        f"{desc}: {done}/{total} ({(done / total) * 100:.1f}%, "
                        f"elapsed {_format_elapsed(elapsed)}, "
                        f"{done / max(elapsed, 1e-9):.1f}/s)"
                    )
                )
                self._counts[stage] = done

        if done >= total:
            self._close_stage(stage, total)

    def close(self) -> None:
        for stage in list(self._bars.keys()):
            total = max(1, self._counts.get(stage, 0))
            self._close_stage(stage, total)


def _format_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, remainder = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)}m {remainder:.1f}s"
    hours, minutes = divmod(int(minutes), 60)
    return f"{hours}h {minutes}m {remainder:.1f}s"


def _profile_sync_payload(profile: Any) -> dict[str, Any]:
    payload = {
        "orcid": str(getattr(profile, "orcid", "")).strip(),
        "query_names": list(getattr(profile, "query_names", [])),
        "debut_year": getattr(profile, "debut_year", None),
        "library_ids": list(getattr(profile, "library_ids", [])),
    }
    year_key = "end_year" if hasattr(profile, "end_year") else "retire_year"
    payload[year_key] = getattr(
        profile, year_key, getattr(profile, "retire_year", None)
    )
    return payload


def _to_scan_relative(path: Path, directory: Path) -> str:
    try:
        return str(path.relative_to(directory))
    except ValueError:
        return str(path)


def _to_sidecar_key(path: Path, sidecar_directory: Path) -> str:
    """Return stable sidecar key/current_filename relative to sidecar directory."""
    return _to_scan_relative(path, sidecar_directory)


def _alnum_count(text: str) -> int:
    return sum(1 for char in text if char.isalnum())


def _is_likely_non_ocr(
    text: str, min_alnum_chars: int = NON_OCR_MIN_ALNUM_CHARS
) -> bool:
    return _alnum_count(text) < min_alnum_chars


def _write_unresolved_csv(
    directory: Path,
    unresolved_paths: list[Path],
    first_page_text_by_path: dict[Path, str] | None = None,
) -> Path | None:
    if not unresolved_paths:
        return None

    csv_path = directory / "unresolved.csv"
    try:
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["filename", "relative_path", "first_page_text"])
            for path in sorted(unresolved_paths, key=str):
                writer.writerow(
                    [
                        path.name,
                        _to_scan_relative(path, directory),
                        (first_page_text_by_path or {}).get(path, ""),
                    ]
                )
    except OSError as exc:
        LOGGER.warning("Failed writing unresolved CSV %s: %s", csv_path, exc)
        click.secho(
            f"Could not write unresolved CSV ({csv_path}): {exc}",
            fg="yellow",
        )
        return None
    return csv_path


def _render_ocr_shell_script(ocr_input_dir: Path) -> str:
    """Build a reusable OCR shell script for routed non-OCR PDFs."""
    srcdir = str(ocr_input_dir)
    outdir = f"{srcdir.rstrip('/').rstrip(os.sep)}_ocred"
    return f"""#!/usr/bin/env bash
set -euo pipefail

srcdir={shlex.quote(srcdir)}
outdir={shlex.quote(outdir)}
mkdir -p "$outdir"

find "$srcdir" -name "*.pdf" -print0 | while IFS= read -r -d '' f; do
  rel="${{f#"$srcdir"/}}"
  out="$outdir/$rel"
  tmp="${{out%.pdf}}.ocr.tmp.pdf"

  mkdir -p "$(dirname "$out")"

  ocrmypdf \\
    --redo-ocr --output-type pdf --continue-on-soft-render-error \\
    "$f" "$tmp" && mv "$tmp" "$out"
done
"""


def _print_unresolved_ai_prompt(csv_path: Path) -> None:
    click.secho(f"Generated unresolved filename list: {csv_path}", fg="yellow")
    click.secho(
        "Use any AI tool you prefer. Attach unresolved.csv and paste this prompt:",
        fg="yellow",
    )
    click.echo(
        """Analyze the attached CSV with columns including `relative_path` and `first_page_text`.

Goal:
1) Extract ADS bibcode (19 chars: YYYYJJJJJVVVVMPPPPA).
- YYYY: Year of publication
- JJJJJ: A standard abbreviation for the journal (e.g. ApJ, AJ, MNRAS, Sci, PASP, etc.). A list of abbreviations is available.
- VVVV: The volume number (for a serial) or an abbreviation that specifies what type of publication it is (e.g. conf for conference proceedings, meet for Meeting proceedings, book for a book, coll for colloquium proceedings, proc for any other type of proceedings).
- M: Qualifier for publication:
  - E: Electronic Abstract (usually a counter, not a page number)
  - L: Letter
  - P: Pink page
- Q-Z: Unduplicating character for identical codes
- PPPP: Page number. Note that for page numbers greater than 9999, the page number is continued in the m column.
- A: The first letter of the last name of the first author.
2) If bibcode cannot be determined, extract as much metadata as possible.

Priority:
- If a valid 19-character bibcode appears in filename or text -> use it.
- Otherwise, parse filename aggressively (patterns like AuthorYearJournal_Vol_Page) to extract:
  year, bibstem (journal), volume, page, and first-author initial, then construct bibcode.
- If still not possible -> leave bibcode blank.

Also extract (even if bibcode fails):
year, bibstem, vol, page, title, doi (lowercase, no "doi:"), arxiv, author1-author4.

Rules:
- Do not hallucinate missing values.
- Prefer filename patterns for structured fields.
- Use text mainly for title/doi/arxiv/authors.
- If ambiguous, choose the most consistent interpretation and explain briefly.

Confidence:
- 0.95+ exact bibcode found
- ~0.85 constructed from strong pattern
- lower if partial/uncertain

Output CSV with columns:
relative_path, ads_bibcode, year, bibstem, vol, page, title, doi, arxiv, author1, author2, author3, author4, confidence, parse_notes"""
    )


def _normalize_ai_row_key(row: dict[str, str], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = str(row.get(key, "")).strip()
        if value:
            return value
    return ""


def _read_ai_csv_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return [{str(k): str(v) for k, v in row.items()} for row in reader]


def _resolve_from_ai_csv(
    identifier: PdfIdentifier,
    directory: Path,
    unresolved_paths: list[Path],
    ai_csv_path: Path,
) -> dict[Path, dict[str, Any]]:
    rows = _read_ai_csv_rows(ai_csv_path)
    if not rows:
        return {}

    by_relative: dict[str, Path] = {}
    by_absolute: dict[str, Path] = {}
    by_filename: dict[str, list[Path]] = {}
    for path in unresolved_paths:
        by_relative[_to_scan_relative(path, directory)] = path
        by_absolute[str(path)] = path
        by_filename.setdefault(path.name, []).append(path)

    bibcode_by_path: dict[Path, str] = {}
    doi_by_path: dict[Path, str] = {}
    for row in rows:
        path_token = _normalize_ai_row_key(
            row,
            ("relative_path", "path", "file", "filename"),
        )
        if not path_token:
            continue

        target = by_relative.get(path_token)
        if target is None:
            target = by_absolute.get(path_token)
        if target is None:
            bucket = by_filename.get(path_token, [])
            if len(bucket) == 1:
                target = bucket[0]
        if target is None:
            continue

        bibcode = _normalize_ai_row_key(row, ("ads_bibcode", "bibcode"))
        if bibcode:
            bibcode_by_path[target] = bibcode
        doi = _normalize_doi(_normalize_ai_row_key(row, ("doi",)))
        if doi:
            doi_by_path[target] = doi

    resolved: dict[Path, dict[str, Any]] = {}
    if bibcode_by_path:
        docs_by_bibcode = identifier.query_docs_for_bibcodes(
            list(set(bibcode_by_path.values())),
        )
        for path, bibcode in bibcode_by_path.items():
            doc = docs_by_bibcode.get(str(bibcode).strip())
            if doc is None:
                continue
            resolved[path] = identifier.build_record(doc, path)

    remaining_for_doi = {
        path: doi for path, doi in doi_by_path.items() if path not in resolved
    }
    if remaining_for_doi:
        docs_by_doi = identifier.query_docs_for_dois(
            list(set(remaining_for_doi.values()))
        )
        for path, doi in remaining_for_doi.items():
            doc = docs_by_doi.get(doi)
            if doc is None:
                continue
            resolved[path] = identifier.build_record(doc, path)
    return resolved


@pdf.command("ocr")
@click.argument(
    "directory",
    required=False,
    default=".",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "-o",
    "--output",
    "output_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("ocr_required"),
    show_default=True,
    help="Directory where likely non-OCR PDFs are routed.",
)
@click.option(
    "--recursive/--no-recursive",
    default=True,
    show_default=True,
    help="Scan subdirectories recursively.",
)
@click.option(
    "-m",
    "--make-ocr-sh",
    is_flag=True,
    help="Write ocrmypdf.sh in the current directory.",
)
@click.option("--apply", is_flag=True, help="Apply file moves. Default is dry-run.")
@click.option(
    "--verbose-parser",
    is_flag=True,
    help="Show low-level PDF parser warnings while extracting first-page text.",
)
@click.option(
    "--verbose",
    "-v",
    count=True,
    help="Increase verbosity (print per-file move/dry-run lines).",
)
@click.pass_context
def ocr_command(
    ctx: click.Context,
    directory: Path,
    output_dir: Path,
    recursive: bool,
    make_ocr_sh: bool,
    apply: bool,
    verbose_parser: bool,
    verbose: int,
) -> None:
    """Route likely non-OCR PDFs into a review directory."""
    try:
        identifier = PdfIdentifier(
            _get_client(ctx),
            suppress_parser_warnings=not verbose_parser,
        )
        report_moves = verbose > 0
        pdf_paths = list(_iter_pdf_paths(directory, recursive))
        if not pdf_paths:
            click.echo("No PDF files found.")
            return

        click.echo(f"OCR precheck: scanning {len(pdf_paths)} PDFs...")
        started = perf_counter()
        previews = _parallel_extract_first_page_previews(
            pdf_paths,
            suppress_parser_warnings=identifier.suppress_parser_warnings,
            desc="OCR precheck first-page text",
            unit="pdf",
        )
        candidates = [
            path for path in pdf_paths if _is_likely_non_ocr(previews.get(path, ""))
        ]

        moved = 0
        for path in candidates:
            _handle_unresolved(
                path,
                output_dir,
                apply,
                report=report_moves,
                label="ocr-required",
            )
            moved += 1

        click.echo(
            (
                "OCR precheck completed in "
                f"{_format_elapsed(perf_counter() - started)} "
                f"(likely non-OCR={len(candidates)}, total scanned={len(pdf_paths)})."
            )
        )
        if apply:
            click.echo(f"Routed likely non-OCR PDFs to: {output_dir}")
        else:
            click.echo("Dry-run only. Re-run with --apply to move files.")

        script_content = _render_ocr_shell_script(output_dir)
        click.secho("You may run OCR with a command like this:", fg="yellow")
        click.echo(script_content.rstrip())
        if make_ocr_sh:
            script_path = Path.cwd() / "ocrmypdf.sh"
            try:
                script_path.write_text(script_content, encoding="utf-8")
                script_path.chmod(0o755)
                click.secho(
                    (
                        f"I wrote OCR batch script: {script_path}\n"
                        "Run it with: bash ocrmypdf.sh"
                    ),
                    fg="yellow",
                )
            except OSError as exc:
                click.secho(
                    f"Could not write OCR script ({script_path}): {exc}",
                    fg="yellow",
                )
        click.secho(
            (
                "Note: OCR routing is optional and only needed when your collection "
                "contains non-OCR PDFs."
            ),
            fg="yellow",
        )
        click.secho(
            (
                "If files have **annotations**, carefully verify OCR outputs before replacing "
                "or reusing them."
            ),
            fg="yellow",
        )
        click.secho(
            (
                "If OCR is done, move the non-OCR documents *out of the directory* "
                "(outside of where you are doing --recursive.)"
            ),
            fg="red",
        )
    except ADSError as exc:
        click.secho(f"Error: {exc}", fg="red", err=True)
        sys.exit(1)


@pdf.command("identify")
@click.argument(
    "directory", type=click.Path(exists=True, file_okay=False, path_type=Path)
)
@click.option(
    "--settings-scope",
    type=click.Choice(["global", "library"]),
    default="global",
    show_default=True,
)
@click.option("--recursive/--no-recursive", default=None)
@click.option(
    "--debut-year",
    type=int,
    help="Earliest year to fetch when refreshing the publication prefetch cache.",
)
@click.option(
    "--end-year",
    type=int,
    help="Latest year to fetch when refreshing the publication prefetch cache.",
)
@click.option("--clear-debut-year", is_flag=True)
@click.option("--clear-end-year", is_flag=True)
@click.option(
    "--refresh-cache",
    is_flag=True,
    help="Refresh the user publication cache before matching.",
)
@click.option(
    "--output-unresolved",
    type=click.Path(file_okay=False, path_type=Path),
    help="Move unresolved PDFs into this directory.",
)
@click.option("--clear-output-unresolved", is_flag=True)
@click.option(
    "--skip-unresolved-directory/--include-unresolved-directory",
    default=None,
    help=(
        "Exclude the unresolved destination before hashing or ADS work. "
        "Without --output-unresolved, the destination is DIRECTORY/unresolved."
    ),
)
@click.option(
    "--apply",
    is_flag=True,
    help="Apply unresolved-file moves (otherwise dry-run for moves).",
)
@click.option(
    "--verbose-parser",
    is_flag=True,
    help="Show low-level PDF parser warnings (useful for debugging broken PDFs).",
)
@click.option(
    "--strategy",
    "strategy_ids",
    multiple=True,
    type=click.Choice([strategy.value for strategy in IdentifyStrategy]),
    help="Enabled identification strategy; repeat in priority order.",
)
@click.option(
    "--allow-ads",
    is_flag=True,
    help="Allow ADS fallback queries without interactive confirmation.",
)
@click.option(
    "--ai-csv",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to AI-enriched unresolved CSV for additional batch identification.",
)
@click.option(
    "--verbose",
    "-v",
    count=True,
    help=(
        "Increase verbosity (use -v to print per-file unresolved move/dry-run lines)."
    ),
)
@click.option(
    "--quiet",
    is_flag=True,
    help="Disable reusable verbose logging for this run.",
)
@click.pass_context
def identify_command(
    ctx: click.Context,
    directory: Path,
    settings_scope: str,
    recursive: bool | None,
    debut_year: int | None,
    end_year: int | None,
    clear_debut_year: bool,
    clear_end_year: bool,
    refresh_cache: bool,
    output_unresolved: Path | None,
    clear_output_unresolved: bool,
    skip_unresolved_directory: bool | None,
    apply: bool,
    verbose_parser: bool,
    strategy_ids: tuple[str, ...],
    allow_ads: bool,
    ai_csv: Path | None,
    verbose: int,
    quiet: bool,
) -> None:
    """Scan PDF files, resolve metadata, and write sidecar."""
    try:
        if clear_debut_year and debut_year is not None:
            raise click.UsageError(
                "--debut-year cannot be combined with --clear-debut-year"
            )
        if clear_end_year and end_year is not None:
            raise click.UsageError(
                "--end-year cannot be combined with --clear-end-year"
            )
        if clear_output_unresolved and output_unresolved is not None:
            raise click.UsageError(
                "--output-unresolved cannot be combined with --clear-output-unresolved"
            )
        if quiet and verbose:
            raise click.UsageError("--quiet cannot be combined with --verbose")
        settings = (
            load_library_identify_settings(directory)
            if settings_scope == "library"
            else None
        ) or Config.load_identify_settings()
        overrides: dict[str, Any] = {}
        if recursive is not None:
            overrides["recursive"] = recursive
        if strategy_ids:
            overrides["strategies"] = tuple(
                IdentifyStrategy(strategy_id) for strategy_id in strategy_ids
            )
        if output_unresolved is not None or clear_output_unresolved:
            overrides["output_unresolved"] = (
                None if clear_output_unresolved else str(output_unresolved)
            )
        if skip_unresolved_directory is not None:
            overrides["skip_unresolved_directory"] = skip_unresolved_directory
        try:
            settings = settings.with_overrides(**overrides)
        except ValueError as exc:
            raise click.UsageError(str(exc)) from exc
        resolved_output = (
            Path(settings.output_unresolved)
            if settings.output_unresolved is not None
            else None
        )
        if resolved_output is not None and not resolved_output.is_absolute():
            resolved_output = directory / resolved_output
        profile = Config.load_user_profile().publication_profile()
        effective_debut_year = (
            None
            if clear_debut_year
            else debut_year
            if debut_year is not None
            else profile.debut_year
        )
        effective_end_year = (
            None
            if clear_end_year
            else end_year
            if end_year is not None
            else profile.end_year
        )
        if (
            effective_debut_year is not None
            and effective_end_year is not None
            and effective_debut_year > effective_end_year
        ):
            raise click.UsageError(
                "first publication year cannot be greater than last publication year"
            )
        profile = replace(
            profile,
            debut_year=effective_debut_year,
            end_year=effective_end_year,
            retire_year=effective_end_year,
        )
        run_identify(
            _get_client(ctx),
            directory,
            helpers=_identify_workflow_helpers(),
            recursive=settings.recursive,
            debut_year=profile.debut_year,
            end_year=profile.end_year,
            refresh_cache=refresh_cache,
            output_unresolved=resolved_output,
            skip_unresolved_directory=settings.skip_unresolved_directory,
            apply=apply,
            verbose_parser=verbose_parser,
            strategies=settings.strategies,
            allow_ads=allow_ads,
            ai_csv=ai_csv,
            verbose=0 if quiet else int(settings.verbose) + verbose,
            profile=profile,
        )
    except ADSError as exc:
        click.secho(f"Error: {exc}", fg="red", err=True)
        sys.exit(1)


@pdf.command("rename")
@click.argument(
    "directory", type=click.Path(exists=True, file_okay=False, path_type=Path)
)
@click.option(
    "--scheme", default=DEFAULT_SCHEME, show_default=True, help="Rename template."
)
@click.option(
    "--author-format",
    default="surname_initials",
    callback=_validate_author_format,
    show_default=True,
    help="Author style: surname, surname_initials, last_comma_first, ads_custom:FMT",
)
@click.option(
    "--char-policy",
    default="keep",
    callback=_validate_char_policy,
    show_default=True,
    help=(
        "Filename handling for non-ASCII characters: "
        "ascii transliterates (Hanuš->Hanus), keep preserves Unicode."
    ),
)
@click.option(
    "--max-authors",
    default=3,
    type=click.IntRange(1, 99),
    show_default=True,
)
@click.option(
    "--authors-when-truncated",
    default=1,
    type=click.IntRange(1, 99),
    show_default=True,
    help="Leading authors retained when --max-authors truncates the list.",
)
@click.option(
    "--author-separator",
    default="+",
    show_default=True,
    help="Text placed between rendered authors.",
)
@click.option(
    "--author-truncation-suffix",
    default="+",
    show_default=True,
    help="Text appended to {authors} only when the list is truncated.",
)
@click.option(
    "--output-unresolved",
    type=click.Path(file_okay=False, path_type=Path),
    help="Move unresolved PDFs here during --allow-ads (relative paths use DIRECTORY).",
)
@click.option(
    "--allow-ads",
    is_flag=True,
    help="Identify pending PDFs recursively through ADS before renaming.",
)
@click.option("--apply", is_flag=True, help="Apply renames. Default is dry-run.")
@click.pass_context
def rename_command(
    ctx: click.Context,
    directory: Path,
    scheme: str,
    author_format: str,
    char_policy: str,
    max_authors: int,
    authors_when_truncated: int,
    author_separator: str,
    author_truncation_suffix: str,
    output_unresolved: Path | None,
    allow_ads: bool,
    apply: bool,
) -> None:
    """Rename resolved PDFs using sidecar metadata and a naming scheme."""
    try:
        if authors_when_truncated > max_authors:
            raise click.UsageError("authors_when_truncated cannot exceed max_authors")
        client = _get_client(ctx)
        resolved_output_unresolved = output_unresolved
        if output_unresolved is not None and not output_unresolved.is_absolute():
            resolved_output_unresolved = directory / output_unresolved
        if allow_ads:
            click.echo("Identifying pending PDFs before rename...")
            identify_settings = (
                load_library_identify_settings(directory)
                or Config.load_identify_settings()
            )
            run_identify(
                client,
                directory,
                helpers=_identify_workflow_helpers(),
                recursive=True,
                debut_year=None,
                end_year=None,
                refresh_cache=False,
                output_unresolved=resolved_output_unresolved,
                skip_unresolved_directory=False,
                apply=apply,
                verbose_parser=False,
                strategies=identify_settings.strategies,
                allow_ads=True,
                ai_csv=None,
                verbose=0,
                prompt_for_ai_csv=False,
            )

        sidecar = open_registry_state(directory)
        renamer = PdfRenamer(client=client)
        unresolved_directory = (
            resolved_output_unresolved
            if resolved_output_unresolved is not None
            else directory / "unresolved"
        ).resolve()
        rename_options: dict[str, Any] = {
            "sidecar": sidecar,
            "template": scheme,
            "dry_run": not apply,
            "author_format": author_format,
            "char_policy": char_policy,
            "max_authors": max_authors,
            "authors_when_truncated": authors_when_truncated,
            "author_separator": author_separator,
            "author_truncation_suffix": author_truncation_suffix,
        }
        if unresolved_directory.is_dir():
            rename_options.update(
                move_resolved_from=unresolved_directory,
                move_resolved_to=sidecar.directory,
            )
        plans = renamer.batch_rename(**rename_options)
        if not plans:
            click.echo("No files to rename.")
            return

        for old_name, new_name in plans:
            click.echo(f"{old_name} -> {new_name}")
        if not apply:
            click.echo("Dry-run only. Re-run with --apply to perform renames.")
    except ADSError as exc:
        click.secho(f"Error: {exc}", fg="red", err=True)
        sys.exit(1)
    except ValueError as exc:
        click.secho(f"Error: {exc}", fg="red", err=True)
        sys.exit(1)


@pdf.command("obsidian")
@click.argument(
    "directory", type=click.Path(exists=True, file_okay=False, path_type=Path)
)
@click.option(
    "--notes-dir",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="Where to write .md notes. Defaults to DIRECTORY (colocated with the PDFs).",
)
@click.option(
    "--force", is_flag=True, help="Overwrite existing notes. Default skips them."
)
@click.option(
    "--apply",
    is_flag=True,
    help="Write notes and fetch/cache missing abstracts. Default is dry-run.",
)
@click.pass_context
def obsidian_command(
    ctx: click.Context,
    directory: Path,
    notes_dir: Path | None,
    force: bool,
    apply: bool,
) -> None:
    """Generate Obsidian-friendly Markdown notes for PDFs tracked in the sidecar."""
    try:
        sidecar = open_registry_state(directory)
        client = _get_client(ctx)
        writer = ObsidianNoteWriter(client=client)
        plans = writer.generate(
            directory=directory,
            state=sidecar,
            notes_dir=notes_dir,
            apply=apply,
            force=force,
        )
        if not plans:
            click.echo("No tracked files.")
            return

        for plan in plans:
            click.echo(
                f"{plan.original_filename} -> {plan.note_filename} [{plan.action}]"
            )
        if not apply:
            click.echo("Dry-run only. Re-run with --apply to write notes.")
    except ADSError as exc:
        click.secho(f"Error: {exc}", fg="red", err=True)
        sys.exit(1)
    except ValueError as exc:
        click.secho(f"Error: {exc}", fg="red", err=True)
        sys.exit(1)


@pdf.command("export")
@click.argument(
    "directory", type=click.Path(exists=True, file_okay=False, path_type=Path)
)
@click.option(
    "-o",
    "output",
    required=True,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Destination JSON snapshot.",
)
def export_command(directory: Path, output: Path) -> None:
    """Export the SQLite registry as a human-readable JSON snapshot."""
    state = open_registry_state(directory)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(state.registry.export_payload(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    click.echo(f"Exported PDF registry: {output}")


@pdf.command("watch")
@click.argument(
    "directory", type=click.Path(exists=True, file_okay=False, path_type=Path)
)
@click.option(
    "--scheme", default=DEFAULT_SCHEME, show_default=True, help="Rename template."
)
@click.option(
    "--use-watchdog", is_flag=True, help="Use watchdog backend instead of polling."
)
@click.option("--recursive", is_flag=True, help="Watch subdirectories recursively.")
@click.option("--interval", default=10.0, type=float, show_default=True)
@click.option(
    "--output-unresolved",
    type=click.Path(file_okay=False, path_type=Path),
    help="Move unresolved PDFs into this directory.",
)
@click.option(
    "--author-format",
    default="surname_initials",
    callback=_validate_author_format,
    show_default=True,
    help="Author style: surname, surname_initials, last_comma_first, ads_custom:FMT",
)
@click.option(
    "--char-policy",
    default="keep",
    callback=_validate_char_policy,
    show_default=True,
    help=(
        "Filename handling for non-ASCII characters: "
        "ascii transliterates (Hanuš->Hanus), keep preserves Unicode."
    ),
)
@click.option(
    "--max-authors",
    default=3,
    type=click.IntRange(1, 99),
    show_default=True,
)
@click.option(
    "--authors-when-truncated",
    default=1,
    type=click.IntRange(1, 99),
    show_default=True,
    help="Leading authors retained when --max-authors truncates the list.",
)
@click.option(
    "--author-separator",
    default="+",
    show_default=True,
    help="Text placed between rendered authors.",
)
@click.option(
    "--author-truncation-suffix",
    default="+",
    show_default=True,
    help="Text appended to {authors} only when the list is truncated.",
)
@click.option("--apply", is_flag=True, help="Apply renames/moves. Default is dry-run.")
@click.option(
    "--auto-upgrade",
    is_flag=True,
    help="Periodically upgrade arXiv entries when published versions appear.",
)
@click.option(
    "--upgrade-interval",
    default=24.0,
    type=float,
    show_default=True,
    help="Hours between auto-upgrade checks.",
)
@click.option(
    "--overwrite-arxiv-with-official",
    is_flag=True,
    help="During upgrade, overwrite tracked arXiv PDF with official PDF.",
)
@click.option(
    "--arxiv-backup-if-overwritten",
    type=click.Path(file_okay=False, path_type=Path),
    help="Backup directory for overwritten arXiv PDFs.",
)
@click.option(
    "--skip-annotation-port",
    is_flag=True,
    help="Skip annotation transfer when upgrading PDFs.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Force upgrade checks even for entries already marked as published.",
)
@click.pass_context
def watch_command(
    ctx: click.Context,
    directory: Path,
    scheme: str,
    use_watchdog: bool,
    recursive: bool,
    interval: float,
    output_unresolved: Path | None,
    author_format: str,
    char_policy: str,
    max_authors: int,
    authors_when_truncated: int,
    author_separator: str,
    author_truncation_suffix: str,
    apply: bool,
    auto_upgrade: bool,
    upgrade_interval: float,
    overwrite_arxiv_with_official: bool,
    arxiv_backup_if_overwritten: Path | None,
    skip_annotation_port: bool,
    force: bool,
) -> None:
    """Watch directory for incoming PDFs and process continuously."""
    sidecar = None
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    stop_event = threading.Event()

    def request_stop(signum: int, frame: object) -> None:
        del signum, frame
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    try:
        if authors_when_truncated > max_authors:
            raise click.UsageError("authors_when_truncated cannot exceed max_authors")
        directory = directory.resolve()
        if output_unresolved is not None:
            output_unresolved = (
                output_unresolved.resolve()
                if output_unresolved.is_absolute()
                else (directory / output_unresolved).resolve()
            )
            try:
                output_unresolved.relative_to(directory)
            except ValueError as exc:
                raise click.UsageError(
                    "--output-unresolved must be inside the watched directory."
                ) from exc
        client = _get_client(ctx)
        sidecar = open_registry_state(directory)
        identifier = PdfIdentifier(client)
        renamer = PdfRenamer(client=client)
        upgrader = PdfUpgrader(client=client)
        watcher = PdfWatcher(identifier=identifier, renamer=renamer, upgrader=upgrader)

        def _callback(pdf_path: Path) -> None:
            record = watcher.process_new_pdf(
                pdf_path=pdf_path,
                sidecar=sidecar,
                scheme=scheme,
                author_format=author_format,
                char_policy=char_policy,
                max_authors=max_authors,
                authors_when_truncated=authors_when_truncated,
                author_separator=author_separator,
                author_truncation_suffix=author_truncation_suffix,
                output_unresolved=output_unresolved,
                apply=apply,
            )
            if record is not None:
                destination = record.get("current_filename", pdf_path.name)
                if apply:
                    click.secho(
                        f"Resolved {pdf_path.name} -> {record.get('bibcode', '')} "
                        f"(renamed: {destination})",
                        fg="green",
                    )
                else:
                    destination = record.get("planned_filename", destination)
                    click.secho(
                        f"[DRY-RUN] Would rename: {pdf_path.name} -> {destination} "
                        f"({record.get('bibcode', '')})",
                        fg="green",
                    )
            else:
                click.secho(f"Unresolved {pdf_path.name}", fg="yellow")

        click.echo(f"Watching directory: {directory}")
        if not apply:
            click.echo("Dry-run mode active. Use --apply to rename/move files.")
        if auto_upgrade:
            click.echo(f"Auto-upgrade enabled (every {upgrade_interval:.1f}h).")

        upgrade_kwargs = {
            "apply": apply,
            "overwrite_arxiv_with_official": overwrite_arxiv_with_official,
            "arxiv_backup_if_overwritten": arxiv_backup_if_overwritten,
            "skip_annotation_port": skip_annotation_port,
            "force": force,
        }

        if use_watchdog:
            watcher.watchdog_loop(
                directory=directory,
                sidecar=sidecar,
                recursive=recursive,
                callback=_callback,
                reconciliation_interval=interval,
                stop_event=stop_event,
                auto_upgrade=auto_upgrade,
                upgrade_interval_hours=upgrade_interval,
                upgrade_kwargs=upgrade_kwargs,
            )
        else:
            watcher.poll_loop(
                directory=directory,
                sidecar=sidecar,
                interval=interval,
                recursive=recursive,
                callback=_callback,
                stop_event=stop_event,
                auto_upgrade=auto_upgrade,
                upgrade_interval_hours=upgrade_interval,
                upgrade_kwargs=upgrade_kwargs,
            )
    except KeyboardInterrupt:
        click.echo("Stopped.")
    except (ADSError, RuntimeError) as exc:
        click.secho(f"Error: {exc}", fg="red", err=True)
        sys.exit(1)
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        if sidecar is not None:
            sidecar.close()


@pdf.command("push-library")
@click.argument(
    "directory", type=click.Path(exists=True, file_okay=False, path_type=Path)
)
@click.argument("library_id")
@click.option("--apply", is_flag=True, help="Apply remote library update.")
@click.pass_context
def push_library_command(
    ctx: click.Context,
    directory: Path,
    library_id: str,
    apply: bool,
) -> None:
    """Push resolved bibcodes from sidecar to an ADS library."""
    try:
        sidecar = open_registry_state(directory)
        bibcodes = sorted(
            {
                str(record.get("bibcode", "")).strip()
                for record in sidecar.files.values()
                if str(record.get("bibcode", "")).strip()
            }
        )
        if not bibcodes:
            click.echo("No resolved bibcodes found in sidecar.")
            return

        click.echo(f"Prepared {len(bibcodes)} bibcodes for library {library_id}.")
        if not apply:
            click.echo("Dry-run only. Re-run with --apply to update library.")
            return

        client = _get_client(ctx)
        response = client.libraries.add_documents(library_id, bibcodes)
        click.secho(f"Library update response: {response}", fg="green")
    except ADSError as exc:
        click.secho(f"Error: {exc}", fg="red", err=True)
        sys.exit(1)


@pdf.command("status")
@click.argument(
    "directory", type=click.Path(exists=True, file_okay=False, path_type=Path)
)
@click.option("--recursive", is_flag=True, help="Include subdirectories.")
def status_command(directory: Path, recursive: bool) -> None:
    """Show catalog identification and duplication status."""
    sidecars: dict[Path, Sidecar] = {}

    def _get_sidecar(path: Path) -> Sidecar:
        parent = Path(path)
        cached = sidecars.get(parent)
        if cached is not None:
            return cached
        created = open_registry_state(parent)
        sidecars[parent] = created
        return created

    root_sidecar = _get_sidecar(directory)
    pdf_paths = list(_iter_pdf_paths(directory, recursive))

    if recursive:
        for pdf_path in pdf_paths:
            _get_sidecar(pdf_path.parent)
        tracked_count = sum(len(sidecar.files) for sidecar in sidecars.values())
        duplicate_count = sum(
            len(sidecar.duplicates_by_sha256()) for sidecar in sidecars.values()
        )
    else:
        tracked_count = len(root_sidecar.files)
        duplicate_count = len(root_sidecar.duplicates_by_sha256())

    unresolved = 0
    for pdf_path in pdf_paths:
        sidecar = _get_sidecar(pdf_path.parent if recursive else directory)
        if sidecar.find_by_current_filename(pdf_path.name) is None:
            unresolved += 1

    click.echo(f"Directory: {directory}")
    click.echo(f"Identified (catalog locations): {tracked_count}")
    click.echo(f"Unresolved PDFs on disk: {unresolved}")
    click.echo(f"Duplicate SHA-256 groups: {duplicate_count}")


pdf.add_command(upgrade_command)
