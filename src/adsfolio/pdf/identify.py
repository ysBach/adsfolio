"""Cached PDF identification workflow."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

import click

from .. import pubcache
from ..config import Config
from .cli_common import is_resolved_record
from .cli_common import iter_pdf_paths as _iter_pdf_paths
from .cli_common import unique_destination
from .identifier import (
    CachedCandidateLookup,
    PdfIdentifier,
    extract_first_page_text_pdf,
)
from .identify_settings import IdentifyStrategy
from .prefetch import PublicationProfile, load_profile, query_publications
from .registry import open_registry_state
from .tracked_move import move_tracked_file
from .upgrade_utils import _normalize_doi

LOGGER = logging.getLogger(__name__)

__all__ = [
    "IdentifyWorkflowHelpers",
    "cached_ads_record",
    "build_cached_candidate_lookup",
    "print_cache_status",
    "run_identify",
]


@dataclass(frozen=True)
class IdentifyWorkflowHelpers:
    """Callbacks retained by the PDF CLI while its commands share them."""

    parallel_map_paths: Callable[..., dict[Path, Any]]
    to_sidecar_key: Callable[[Path, Path], str]
    format_elapsed: Callable[[float], str]
    profile_sync_payload: Callable[[Any], dict[str, Any]]
    estimate_ads_queries_for_targets: Callable[..., dict[str, Any]]
    should_prompt_ads_permission: Callable[[], bool]
    confirm_ads_queries: Callable[..., tuple[str, int]]
    progress_reporter_factory: Callable[[], Any]
    build_progress: Callable[..., Any | None]
    parallel_extract_first_page_previews: Callable[..., dict[Path, str]]
    write_unresolved_csv: Callable[..., Path | None]
    print_unresolved_ai_prompt: Callable[[Path], None]
    resolve_from_ai_csv: Callable[..., dict[Path, dict[str, Any]]]
    ads_block_risk_threshold: int


def print_cache_status(cached: pubcache.CachedPublications | None) -> None:
    """Print cache age and document count for an identify run."""
    if cached is None:
        click.echo("Publication cache: missing (run `adsfolio cache refresh`).")
        return
    try:
        fetched = datetime.fromisoformat(cached.fetched_at)
        age = datetime.now(timezone.utc) - fetched.astimezone(timezone.utc)
        age_text = str(max(age.total_seconds(), 0)).split(".", 1)[0] + " seconds"
    except (TypeError, ValueError):
        age_text = "unknown age"
    click.echo(f"Publication cache: {len(cached.documents)} documents, age {age_text}.")


def cached_ads_record(
    registry: Any,
    identifier: Any,
    path: Path,
    doi: str = "",
    bibcode: str = "",
    sha256: str = "",
) -> tuple[bool, dict[str, Any] | None]:
    """Return cache-hit status and a unique folder-cache identify record."""
    if bibcode:
        document = registry.find_ads_document_by_bibcode(bibcode)
        if document is not None:
            return True, identifier.build_record(
                document,
                path,
                sha256=sha256 or None,
            )

    candidates = registry.find_ads_documents_by_doi(doi) if doi else []
    if not candidates:
        return False, None
    if len(candidates) != 1:
        return True, None
    return True, identifier.build_record(candidates[0], path, sha256=sha256 or None)


def build_cached_candidate_lookup(
    identifier: PdfIdentifier,
    registry: Any,
    documents: list[dict[str, Any]],
) -> CachedCandidateLookup:
    """Build a unique cached-document lookup for every strategy candidate kind."""
    bibcodes: dict[str, dict[str, Any]] = {}
    dois: dict[str, dict[str, dict[str, Any]]] = {}
    structured: dict[tuple[str, str, str, str, str], dict[str, dict[str, Any]]] = {}
    titles: dict[str, dict[str, dict[str, Any]]] = {}

    for document in documents:
        bibcode = identifier._first_scalar(document.get("bibcode")).strip()
        if not bibcode:
            continue
        bibcodes[bibcode.casefold()] = document
        for doi in identifier._as_list(document.get("doi")):
            normalized_doi = _normalize_doi(str(doi))
            if normalized_doi:
                dois.setdefault(normalized_doi, {})[bibcode] = document
        structured_key = identifier._doc_filename_hint_key(document)
        if any(structured_key):
            structured.setdefault(structured_key, {})[bibcode] = document
        for title in identifier._as_list(document.get("title")):
            normalized_title = identifier._normalized_title(str(title))
            if normalized_title:
                titles.setdefault(normalized_title, {})[bibcode] = document

    def unique_document(
        bucket: dict[str, dict[str, Any]] | None,
    ) -> tuple[bool, dict[str, Any] | None]:
        if not bucket:
            return False, None
        values = list(bucket.values())
        return True, values[0] if len(values) == 1 else None

    def lookup(kind: str, candidate: Any) -> tuple[bool, dict[str, Any] | None]:
        if kind == "bibcode":
            value = str(candidate).strip()
            document = registry.find_ads_document_by_bibcode(value)
            if document is not None:
                return True, document
            document = bibcodes.get(value.casefold())
            return (True, document) if document is not None else (False, None)
        if kind == "doi":
            normalized = _normalize_doi(str(candidate))
            registry_documents = registry.find_ads_documents_by_doi(normalized)
            if registry_documents:
                by_bibcode = {
                    identifier._first_scalar(document.get("bibcode")): document
                    for document in registry_documents
                    if identifier._first_scalar(document.get("bibcode"))
                }
                return unique_document(by_bibcode)
            return unique_document(dois.get(normalized))
        if kind == "structured":
            return unique_document(structured.get(candidate))
        if kind == "title":
            return unique_document(titles.get(str(candidate)))
        raise ValueError(f"Unknown cached candidate kind: {kind!r}")

    return lookup


def run_identify(
    client: Any,
    directory: Path,
    *,
    helpers: IdentifyWorkflowHelpers,
    recursive: bool,
    debut_year: int | None,
    end_year: int | None,
    refresh_cache: bool,
    output_unresolved: Path | None,
    skip_unresolved_directory: bool,
    apply: bool,
    verbose_parser: bool,
    strategies: tuple[IdentifyStrategy, ...],
    allow_ads: bool,
    ai_csv: Path | None,
    verbose: int,
    profile: PublicationProfile | None = None,
    prompt_for_ai_csv: bool = True,
) -> None:
    """Identify PDFs and persist their ADS metadata."""
    identifier = PdfIdentifier(
        client,
        suppress_parser_warnings=not verbose_parser,
    )
    profile = profile or load_profile()
    if (debut_year is not None or end_year is not None) and hasattr(
        profile, "__dataclass_fields__"
    ):
        profile = replace(
            profile,
            debut_year=(debut_year if debut_year is not None else profile.debut_year),
            end_year=end_year if end_year is not None else profile.end_year,
            retire_year=end_year if end_year is not None else profile.end_year,
        )
    cached = None if refresh_cache else pubcache.load_publications()
    if refresh_cache:
        if not profile.has_identity:
            click.secho(
                "Cannot refresh publication cache without a setup identity.",
                fg="yellow",
            )
        else:
            documents = query_publications(identifier.client, profile)
            pubcache.save_publications(profile, documents)
            cached = pubcache.load_publications()
            click.echo(f"Refreshed publication cache: {len(documents)} documents.")
    print_cache_status(cached)
    if cached is not None and not pubcache.profile_matches(cached.profile, profile):
        click.secho(
            "Publication cache profile differs from the current setup; "
            "use --refresh-cache or `adsfolio cache refresh`.",
            fg="yellow",
        )
    sidecar_directory = directory.resolve()

    report_unresolved_moves = verbose > 0
    resolved = 0
    unresolved = 0
    sidecar_dirty = False
    pending_paths: list[Path] = []
    pending_sha256: dict[Path, str] = {}
    pending_original_names: dict[Path, str] = {}
    unresolved_paths: list[Path] = []
    unresolved_first_page_text_cache: dict[Path, str] = {}
    first_page_preview_cache: dict[Path, str] = {}
    scan_started = perf_counter()
    scanned_paths = list(_iter_pdf_paths(directory, recursive))
    if skip_unresolved_directory:
        scan_root = directory.resolve()
        unresolved_directory = (
            output_unresolved.resolve()
            if output_unresolved is not None
            else (scan_root / "unresolved").resolve()
        )
        try:
            scan_root.relative_to(unresolved_directory)
        except ValueError:
            pass
        else:
            raise click.UsageError(
                "Cannot skip the unresolved directory because it contains the "
                "Identify scan root."
            )
        included_paths: list[Path] = []
        skipped_paths = 0
        for path in scanned_paths:
            try:
                path.resolve().relative_to(unresolved_directory)
            except ValueError:
                included_paths.append(path)
            else:
                skipped_paths += 1
        scanned_paths = included_paths
        click.echo(
            f"Skipped unresolved directory: {skipped_paths} PDFs "
            f"({unresolved_directory})"
        )
    scanned_count = len(scanned_paths)
    if scanned_paths:
        click.echo(f"Scan phase: hashing {scanned_count} PDFs...")
    sidecar = open_registry_state(sidecar_directory)

    def _safe_compute_sha(pdf_path: Path) -> str | None:
        try:
            return identifier.compute_sha256(pdf_path)
        except OSError as exc:
            LOGGER.warning("Skipping unreadable PDF %s: %s", pdf_path, exc)
            return None

    scanned_sha: dict[Path, str] = {}
    paths_to_hash: list[Path] = []
    for pdf_path in scanned_paths:
        relative_path = helpers.to_sidecar_key(pdf_path, sidecar_directory)
        existing = sidecar.registry.find_file_by_current_path(
            sidecar_directory, relative_path
        )
        if (
            existing is None
            or existing.get("missing_at") is not None
            or not existing.get("sha256")
        ):
            paths_to_hash.append(pdf_path)
            continue
        try:
            stat = pdf_path.stat()
        except OSError:
            paths_to_hash.append(pdf_path)
            continue
        facts_match = (
            stat.st_size == existing.get("size_bytes")
            and stat.st_mtime_ns == existing.get("mtime_ns")
            and (
                existing.get("device") is None or existing.get("device") == stat.st_dev
            )
            and (existing.get("inode") is None or existing.get("inode") == stat.st_ino)
        )
        if facts_match:
            scanned_sha[pdf_path] = str(existing["sha256"])
        else:
            paths_to_hash.append(pdf_path)
    if paths_to_hash:
        scanned_sha.update(
            helpers.parallel_map_paths(
                paths_to_hash,
                _safe_compute_sha,
                desc="Scan/hash PDFs",
                unit="pdf",
            )
        )

    for pdf_path in scanned_paths:
        sha256 = scanned_sha.get(pdf_path)
        if not sha256:
            continue

        sidecar_key = helpers.to_sidecar_key(pdf_path, sidecar_directory)
        catalog_record = sidecar.registry.find_file_by_current_path(
            sidecar_directory, sidecar_key
        )
        existing = sidecar.find_by_current_filename(sidecar_key)
        if catalog_record is not None and catalog_record.get("missing_at") is not None:
            previous_sha = str(existing[1].get("sha256", ""))
            metadata = (
                {**existing[1], "current_filename": sidecar_key, "sha256": sha256}
                if previous_sha == sha256
                else {
                    "current_filename": sidecar_key,
                    "sha256": sha256,
                    "status": "unresolved",
                }
            )
            record = sidecar.upsert_file(
                sidecar_key,
                metadata,
            )
            existing = (sidecar_key, record)
            sidecar_dirty = True
        elif existing is not None and existing[1].get("sha256") != sha256:
            record = sidecar.upsert_file(
                sidecar_key,
                {
                    "current_filename": sidecar_key,
                    "sha256": sha256,
                    "status": "unresolved",
                },
            )
            existing = (sidecar_key, record)
            sidecar_dirty = True
        elif existing is None:
            existing = sidecar.find_by_sha256(sha256)
            if existing is not None:
                original_name, record = existing
                previous_path = sidecar_directory / str(
                    record.get("current_filename", original_name)
                )
                if previous_path.exists():
                    record = sidecar.upsert_file(
                        sidecar_key,
                        {**record, "current_filename": sidecar_key},
                    )
                    existing = (sidecar_key, record)
                    sidecar_dirty = True
        if existing is not None:
            original_name, record = existing
            if is_resolved_record(record):
                if record.get("current_filename") != sidecar_key:
                    sidecar.set_current_filename(original_name, sidecar_key)
                    sidecar_dirty = True
                continue
            pending_original_names[pdf_path] = original_name
            if record.get("current_filename") != sidecar_key:
                sidecar.set_current_filename(original_name, sidecar_key)
                sidecar_dirty = True
        else:
            sidecar.upsert_file(
                sidecar_key,
                {
                    "current_filename": sidecar_key,
                    "sha256": sha256,
                    "status": "unresolved",
                },
            )
            sidecar_dirty = True

        pending_paths.append(pdf_path)
        pending_sha256[pdf_path] = sha256
    click.echo(
        (
            f"Scan phase completed in {helpers.format_elapsed(perf_counter() - scan_started)} "
            f"({scanned_count} PDFs discovered)."
        )
    )

    if pending_paths:
        click.echo(f"Pending PDFs requiring identification: {len(pending_paths)}")
        click.echo(
            "Identify plan: ordered evidence strategies -> cached/ADS lookup -> "
            "AI CSV import -> sidecar update"
        )
        click.echo(
            "Identification priority: "
            + " -> ".join(strategy.value for strategy in strategies)
        )

    if profile.has_identity:
        profile_payload = helpers.profile_sync_payload(profile)
        if sidecar.set_identify_profile(profile_payload):
            sidecar_dirty = True
            click.echo("Synced identify profile to sidecar file.")

    if pending_paths and not profile.has_identity:
        setup_path = Config.get_setup_path()
        click.secho(
            (
                "No usable identify profile found "
                f"({setup_path}). Run `adsfolio set` and provide at least one of: "
                "ORCID, --query-name, or --library-id."
            ),
            fg="yellow",
        )
    elif pending_paths and cached is not None:
        click.echo(
            f"Loaded {len(cached.documents)} profile publications as lookup cache."
        )

    query_targets = list(pending_paths)
    analysis: dict[str, Any] | None = None
    if query_targets:
        analysis = helpers.estimate_ads_queries_for_targets(
            identifier,
            query_targets,
            strategies=strategies,
        )
        first_page_preview_cache = {
            path: text
            for path, text in analysis.get("first_page_preview_by_path", {}).items()
            if isinstance(path, Path)
        }

    allow_network_lookup = False
    if (
        query_targets
        and helpers.should_prompt_ads_permission()
        and analysis is not None
    ):
        decision, estimated = helpers.confirm_ads_queries(
            analysis, interactive=not allow_ads
        )
        allow_network_lookup = decision != "none"
        if not allow_network_lookup and estimated < helpers.ads_block_risk_threshold:
            click.echo("Skipped ADS network queries at user request; checking caches.")
    elif query_targets and analysis is not None and not allow_ads:
        click.secho(
            (
                "Skipping ADS network queries because stdin is non-interactive; "
                "cached candidates will still be checked. "
                "Re-run with --allow-ads to proceed without a prompt."
            ),
            fg="yellow",
        )
    elif query_targets and analysis is not None and allow_ads:
        decision, _ = helpers.confirm_ads_queries(analysis, interactive=False)
        allow_network_lookup = decision != "none"

    resolved_map: dict[Path, dict[str, Any] | None] = {}
    if query_targets:
        click.echo(
            (
                f"Step 2/3: running ordered identification for "
                f"{len(query_targets)} PDFs..."
            )
        )
        fallback_started = perf_counter()
        fallback_progress = helpers.progress_reporter_factory()
        ads_documents_callback = (
            partial(
                sidecar.registry.upsert_ads_documents,
                source="identify:fallback",
            )
            if hasattr(sidecar, "registry")
            else None
        )
        cached_candidate_lookup = build_cached_candidate_lookup(
            identifier,
            sidecar.registry,
            list(cached.documents) if cached is not None else [],
        )
        try:
            resolved_map.update(
                identifier.resolve_many(
                    query_targets,
                    strategies=strategies,
                    sha256_by_path=pending_sha256,
                    progress_callback=fallback_progress,
                    ads_documents_callback=ads_documents_callback,
                    cached_candidate_lookup=cached_candidate_lookup,
                    allow_network_lookup=allow_network_lookup,
                )
            )
        finally:
            fallback_progress.close()
        click.echo(
            (
                "Step 2/3: ADS fallback identification completed in "
                f"{helpers.format_elapsed(perf_counter() - fallback_started)}."
            )
        )
    apply_started = perf_counter()
    apply_progress = helpers.build_progress(
        total=len(pending_paths),
        desc="Apply results",
        unit="pdf",
    )
    for pdf_path in pending_paths:
        record = resolved_map.get(pdf_path)
        if record is None:
            unresolved += 1
            unresolved_path = pdf_path
            if output_unresolved is not None and pdf_path.parent != output_unresolved:
                target = unique_destination(output_unresolved, pdf_path.name)
                try:
                    target.resolve().relative_to(sidecar_directory)
                except ValueError:
                    if apply:
                        output_unresolved.mkdir(parents=True, exist_ok=True)
                        pdf_path.rename(target)
                        unresolved_path = target
                        if report_unresolved_moves:
                            click.echo(f"Moved unresolved: {pdf_path.name} -> {target}")
                    elif report_unresolved_moves:
                        click.echo(
                            f"[dry-run] move unresolved {pdf_path} -> "
                            f"{output_unresolved}"
                        )
                else:
                    if apply:
                        unresolved_path = move_tracked_file(sidecar, pdf_path, target)
                        if report_unresolved_moves:
                            click.echo(f"Moved unresolved: {pdf_path.name} -> {target}")
                    elif report_unresolved_moves:
                        click.echo(
                            f"[dry-run] move unresolved {pdf_path} -> "
                            f"{output_unresolved}"
                        )
            unresolved_paths.append(unresolved_path)
            preview_text = first_page_preview_cache.get(pdf_path, "")
            if preview_text:
                unresolved_first_page_text_cache[unresolved_path] = preview_text
            if apply_progress is not None:
                apply_progress.update(1)
            continue
        resolved += 1
        sidecar_key = helpers.to_sidecar_key(pdf_path, sidecar_directory)
        record["current_filename"] = sidecar_key
        sidecar.upsert_file(
            pending_original_names.get(pdf_path, sidecar_key),
            record,
        )
        sidecar_dirty = True
        if apply_progress is not None:
            apply_progress.update(1)
    if apply_progress is not None:
        apply_progress.close()
    if pending_paths:
        click.echo(
            (
                f"Step 2/3: result application completed in "
                f"{helpers.format_elapsed(perf_counter() - apply_started)}."
            )
        )

    if sidecar_dirty:
        click.echo("Step 3/3: writing updated sidecar file...")
        sidecar_save_started = perf_counter()
        sidecar.save()
        click.echo(
            (
                "Step 3/3: sidecar write completed in "
                f"{helpers.format_elapsed(perf_counter() - sidecar_save_started)}."
            )
        )

    unresolved_first_page_texts: dict[Path, str] = {}
    if unresolved_paths:
        unresolved_first_page_texts.update(unresolved_first_page_text_cache)
        extract_targets = [
            path for path in unresolved_paths if path not in unresolved_first_page_texts
        ]
        click.echo("Preparing first-page previews for unresolved.csv...")
        if extract_targets:
            try:
                unresolved_first_page_texts.update(
                    helpers.parallel_extract_first_page_previews(
                        extract_targets,
                        suppress_parser_warnings=identifier.suppress_parser_warnings,
                        desc="Unresolved first-page text",
                        unit="pdf",
                    )
                )
            except PermissionError:
                LOGGER.warning(
                    "Parallel first-page extraction is unavailable; using one process."
                )
                unresolved_first_page_texts.update(
                    helpers.parallel_map_paths(
                        extract_targets,
                        lambda path: extract_first_page_text_pdf(
                            path,
                            suppress_parser_warnings=identifier.suppress_parser_warnings,
                        ),
                        desc="Unresolved first-page text",
                        unit="pdf",
                    )
                )

    unresolved_csv = helpers.write_unresolved_csv(
        directory,
        unresolved_paths,
        first_page_text_by_path=unresolved_first_page_texts,
    )
    if unresolved_csv is not None:
        helpers.print_unresolved_ai_prompt(unresolved_csv)
        ai_csv_path = ai_csv
        if (
            ai_csv_path is None
            and prompt_for_ai_csv
            and helpers.should_prompt_ads_permission()
        ):
            provided = click.prompt(
                "Path to AI-enriched CSV (press Enter to skip)",
                default="",
                show_default=False,
            ).strip()
            if provided:
                ai_csv_path = Path(provided).expanduser()
        elif ai_csv_path is None and prompt_for_ai_csv:
            click.echo(
                "Non-interactive mode: re-run with --ai-csv <path> to apply AI hints."
            )

        if ai_csv_path is not None:
            if not ai_csv_path.exists():
                click.secho(f"AI CSV not found: {ai_csv_path}", fg="yellow")
            else:
                click.echo(f"Applying AI-enriched CSV: {ai_csv_path}")
                ai_resolved = helpers.resolve_from_ai_csv(
                    identifier,
                    directory,
                    unresolved_paths,
                    ai_csv_path,
                )
                if ai_resolved:
                    for path, record in ai_resolved.items():
                        sidecar_key = helpers.to_sidecar_key(path, sidecar_directory)
                        record["current_filename"] = sidecar_key
                        sidecar.upsert_file(sidecar_key, record)
                    sidecar.save()
                    sidecar_dirty = True
                    unresolved_paths = [
                        path for path in unresolved_paths if path not in ai_resolved
                    ]
                    resolved += len(ai_resolved)
                    unresolved = max(0, unresolved - len(ai_resolved))
                    click.secho(
                        f"AI CSV resolved additional PDFs: {len(ai_resolved)}",
                        fg="green",
                    )
                    unresolved_csv = helpers.write_unresolved_csv(
                        directory,
                        unresolved_paths,
                        first_page_text_by_path=unresolved_first_page_texts,
                    )
                    if unresolved_csv is not None:
                        click.echo(
                            f"Updated unresolved CSV after AI import: {unresolved_csv}"
                        )
                else:
                    click.secho(
                        "AI CSV did not produce additional resolvable ADS matches.",
                        fg="yellow",
                    )

    click.echo(f"Scanned directory: {directory}")
    click.echo(f"Resolved PDFs: {resolved}")
    click.echo(f"Unresolved PDFs: {unresolved}")
    if output_unresolved and not apply:
        click.echo("Use --apply to actually move unresolved files.")
