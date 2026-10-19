import csv
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
import requests
from click.testing import CliRunner

from adsfolio.cli.commands import main
from adsfolio.config import ADSError, Config, RateLimitError
from adsfolio.library_sync import AdsLibraryService
from adsfolio.pdf.catalog import CATALOG_FILENAME
from adsfolio.pdf.cli import (
    _estimate_ads_queries_for_targets,
    _parse_ads_identifier,
    pdf,
)
from adsfolio.pdf.cli_common import handle_unresolved
from adsfolio.pdf.doi_providers import DEFAULT_DOI_PROVIDERS
from adsfolio.pdf.files import sha256_file
from adsfolio.pdf.identifier import PdfIdentifier
from adsfolio.pdf.identify import build_cached_candidate_lookup
from adsfolio.pdf.identify_settings import (
    RECOMMENDED_IDENTIFY_STRATEGIES,
    IdentifySettings,
    IdentifyStrategy,
)
from adsfolio.pdf.prefetch import PublicationProfile
from adsfolio.pdf.registry import PdfRegistry, PdfRegistryState, open_registry_state
from adsfolio.pdf.upgrade import UpgradeResult
from adsfolio.pdf.watcher import PdfWatcher
from adsfolio.user_profile import UserProfile


@pytest.fixture(autouse=True)
def _use_test_catalog(monkeypatch, tmp_path):
    """Keep CLI catalog state inside the test sandbox."""
    monkeypatch.setattr(
        Config,
        "get_config_dir",
        classmethod(lambda cls: tmp_path / "config"),
    )
    monkeypatch.setattr(
        "adsfolio.pubcache.cache_path",
        lambda: tmp_path / "config" / "publications.json",
    )


def _metadata(**overrides):
    base = {
        "bibcode": "2024ApJ...900..100S",
        "doi": "10.1234/example",
        "sha256": "sha-default",
        "title": ["Paper Title"],
        "author": ["Smith, John"],
        "year": "2024",
        "bibstem": ["ApJ"],
        "volume": "900",
        "page": ["100"],
        "pub": "The Astrophysical Journal",
        "current_filename": "paper.pdf",
        "identified_at": "2025-04-02T12:00:00+00:00",
    }
    base.update(overrides)
    return base


def _estimate_ads_ready(paths):
    normalized = [Path(path) for path in paths]
    dois = {
        path: f"10.1234/{index:04d}" for index, path in enumerate(normalized, start=1)
    }
    return {
        "pending_files": len(normalized),
        "metadata_doi_files": len(normalized),
        "text_doi_files": 0,
        "no_doi_files": 0,
        "doi_candidate_files": len(normalized),
        "unique_dois": len(dois),
        "doi_batch_queries": 1 if dois else 0,
        "estimated_ads_queries": 1 if dois else 0,
    }


def test_main_registers_pdf_group():
    assert "pdf" in main.commands
    assert "adsquery" not in pdf.commands


def test_refresh_ads_progress_is_bounded(tmp_path, monkeypatch):
    bibcodes = [f"B{index:03d}" for index in range(101)]

    monkeypatch.setattr("adsfolio.pdf.cli._get_client", lambda ctx: object())
    monkeypatch.setattr(
        "adsfolio.pdf.cli.PdfIdentifier.query_docs_for_bibcodes",
        lambda self, requested, **kwargs: {
            bibcode: {"bibcode": bibcode} for bibcode in requested
        },
    )

    runner = CliRunner()
    args = ["refresh-ads", str(tmp_path)]
    args.extend(item for bibcode in bibcodes for item in ("--bibcode", bibcode))
    result = runner.invoke(pdf, args)

    assert result.exit_code == 0
    progress_lines = [
        line for line in result.output.splitlines() if line.startswith("Refresh ADS: ")
    ]
    assert len(progress_lines) <= 51
    assert progress_lines[-1] == "Refresh ADS: 101/101"
    assert "Refresh ADS: 1/101" not in progress_lines


def test_identify_command_delegates_to_identify_workflow(tmp_path, monkeypatch):
    captured = {}

    def fake_run_identify(client, directory, *, helpers, **options):
        captured.update(
            client=client,
            directory=directory,
            helpers=helpers,
            options=options,
        )

    monkeypatch.setattr("adsfolio.pdf.cli.run_identify", fake_run_identify)

    result = CliRunner().invoke(
        pdf,
        [
            "identify",
            str(tmp_path),
            "--recursive",
            "--debut-year",
            "2004",
            "--end-year",
            "2026",
            "--allow-ads",
        ],
    )

    assert result.exit_code == 0
    assert captured["directory"] == tmp_path
    assert callable(captured["helpers"].parallel_map_paths)
    assert captured["options"] == {
        "recursive": True,
        "debut_year": 2004,
        "end_year": 2026,
        "refresh_cache": False,
        "output_unresolved": None,
        "skip_unresolved_directory": False,
        "apply": False,
        "verbose_parser": False,
        "strategies": RECOMMENDED_IDENTIFY_STRATEGIES,
        "providers": DEFAULT_DOI_PROVIDERS,
        "allow_ads": True,
        "ai_csv": None,
        "verbose": 0,
        "profile": PublicationProfile(debut_year=2004, end_year=2026),
    }


def test_identify_command_inherits_library_settings_and_allows_clears(
    tmp_path, monkeypatch
):
    captured = {}
    saved = IdentifySettings(
        recursive=True,
        strategies=(IdentifyStrategy.FIRST_PAGE_DOI,),
        output_unresolved="pending",
        skip_unresolved_directory=True,
        verbose=True,
    )
    monkeypatch.setattr(
        "adsfolio.pdf.cli.load_library_identify_settings",
        lambda directory: saved,
    )
    monkeypatch.setattr(
        Config,
        "load_user_profile",
        lambda: UserProfile(
            first_publication_year=2010,
            last_publication_year=2030,
        ),
    )
    monkeypatch.setattr(
        "adsfolio.pdf.cli.run_identify",
        lambda client, directory, *, helpers, **options: captured.update(options),
    )

    result = CliRunner().invoke(
        pdf,
        [
            "identify",
            str(tmp_path),
            "--settings-scope",
            "library",
            "--no-recursive",
            "--strategy",
            "filename_doi",
            "--clear-debut-year",
            "--clear-output-unresolved",
        ],
    )

    assert result.exit_code == 0
    assert captured["recursive"] is False
    assert captured["debut_year"] is None
    assert captured["end_year"] == 2030
    assert captured["strategies"] == (IdentifyStrategy.FILENAME_DOI,)
    assert captured["output_unresolved"] is None
    assert captured["skip_unresolved_directory"] is True
    assert captured["verbose"] == 1
    assert captured["refresh_cache"] is False
    assert captured["allow_ads"] is False
    assert captured["apply"] is False


def test_identify_command_quiet_overrides_reusable_verbose(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        Config,
        "load_identify_settings",
        classmethod(lambda cls: IdentifySettings(verbose=True)),
    )
    monkeypatch.setattr(
        "adsfolio.pdf.cli.run_identify",
        lambda client, directory, *, helpers, **options: captured.update(options),
    )

    result = CliRunner().invoke(pdf, ["identify", str(tmp_path), "--quiet"])

    assert result.exit_code == 0
    assert captured["verbose"] == 0


def test_identify_command_rejects_quiet_with_verbose(tmp_path):
    result = CliRunner().invoke(
        pdf, ["identify", str(tmp_path), "--quiet", "--verbose"]
    )

    assert result.exit_code == 2
    assert "--quiet cannot be combined with --verbose" in result.output


def test_identify_command_reports_reversed_year_override_as_usage_error(tmp_path):
    result = CliRunner().invoke(
        pdf,
        [
            "identify",
            str(tmp_path),
            "--debut-year",
            "2030",
            "--end-year",
            "2020",
        ],
    )

    assert result.exit_code == 2
    assert (
        "first publication year cannot be greater than last publication year"
        in result.output
    )
    assert "Traceback" not in result.output


def test_identify_creates_registry_record(tmp_path, monkeypatch):
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF")

    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.compute_sha256",
        lambda self, path: f"sha-{path.name}",
    )
    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.resolve_many",
        lambda self, paths, journal_map=None, **kwargs: {
            Path(path): _metadata(
                sha256=f"sha-{Path(path).name}",
                current_filename=Path(path).name,
            )
            for path in paths
        },
    )
    monkeypatch.setattr(
        "adsfolio.pdf.cli._estimate_ads_queries_for_targets",
        lambda identifier, pdf_paths, strategies=None: _estimate_ads_ready(pdf_paths),
    )

    runner = CliRunner()
    result = runner.invoke(pdf, ["identify", str(tmp_path), "--allow-ads"])
    assert result.exit_code == 0
    assert "Resolved PDFs: 1" in result.output

    state = open_registry_state(tmp_path)
    try:
        assert state.get_file("paper.pdf") is not None
    finally:
        state.close()


def test_identify_unresolved_move_is_dry_run_without_apply(tmp_path, monkeypatch):
    pdf_path = tmp_path / "unresolved.pdf"
    pdf_path.write_bytes(b"%PDF")
    output_dir = tmp_path / "unresolved-bin"

    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.compute_sha256",
        lambda self, path: "sha-unresolved",
    )
    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.resolve_many",
        lambda self, paths, journal_map=None, **kwargs: {
            Path(path): None for path in paths
        },
    )

    runner = CliRunner()
    result = runner.invoke(
        pdf,
        [
            "identify",
            str(tmp_path),
            "--output-unresolved",
            str(output_dir),
        ],
    )
    assert result.exit_code == 0
    assert "[dry-run] move unresolved" not in result.output
    assert pdf_path.exists()


def test_identify_persists_unresolved_pdf_before_enrichment(tmp_path, monkeypatch):
    pdf_path = tmp_path / "unresolved.pdf"
    pdf_path.write_bytes(b"%PDF")

    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.compute_sha256",
        lambda self, path: "sha-unresolved",
    )
    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.resolve_many",
        lambda self, paths, journal_map=None, **kwargs: {
            Path(path): None for path in paths
        },
    )

    result = CliRunner().invoke(pdf, ["identify", str(tmp_path)])

    assert result.exit_code == 0
    state = open_registry_state(tmp_path)
    try:
        record = state.get_file("unresolved.pdf")
        assert record is not None
        assert record["status"] == "unresolved"
        assert record["sha256"] == "sha-unresolved"
    finally:
        state.close()


def test_unresolved_move_is_noop_when_file_is_already_in_output_directory(tmp_path):
    output_dir = tmp_path / "unresolved"
    output_dir.mkdir()
    pdf_path = output_dir / "already-unresolved.pdf"
    pdf_path.write_bytes(b"%PDF")

    result = handle_unresolved(pdf_path, output_dir, apply=True)

    assert result == pdf_path
    assert list(output_dir.glob("*.pdf")) == [pdf_path]


def test_identify_verbose_unresolved_move_shows_per_file_logs(tmp_path, monkeypatch):
    pdf_path = tmp_path / "unresolved.pdf"
    pdf_path.write_bytes(b"%PDF")
    output_dir = tmp_path / "unresolved-bin"

    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.compute_sha256",
        lambda self, path: "sha-unresolved",
    )
    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.resolve_many",
        lambda self, paths, journal_map=None, **kwargs: {
            Path(path): None for path in paths
        },
    )

    runner = CliRunner()
    result = runner.invoke(
        pdf,
        [
            "identify",
            str(tmp_path),
            "--output-unresolved",
            str(output_dir),
            "-v",
        ],
    )
    assert result.exit_code == 0
    assert "[dry-run] move unresolved" in result.output


def test_identify_writes_unresolved_csv_and_ai_prompt(tmp_path, monkeypatch):
    pdf_path = tmp_path / "unresolved.pdf"
    pdf_path.write_bytes(b"%PDF")

    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.compute_sha256",
        lambda self, path: "sha-unresolved",
    )
    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.resolve_many",
        lambda self, paths, journal_map=None, **kwargs: {
            Path(path): None for path in paths
        },
    )

    runner = CliRunner()
    result = runner.invoke(pdf, ["identify", str(tmp_path)])
    assert result.exit_code == 0
    assert "Generated unresolved filename list:" in result.output
    assert "Use any AI tool you prefer." in result.output
    assert "Analyze the attached CSV with columns including" in result.output
    assert "Goal:" in result.output
    assert "Output CSV with columns:" in result.output

    unresolved_csv = tmp_path / "unresolved.csv"
    assert unresolved_csv.exists()
    with unresolved_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    assert set(rows[0].keys()) == {"filename", "relative_path", "first_page_text"}
    assert rows[0]["filename"] == "unresolved.pdf"
    assert rows[0]["relative_path"] == "unresolved.pdf"


def test_identify_passes_cached_lookup_to_ordered_resolver(tmp_path, monkeypatch):
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF")
    observed = {}

    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.compute_sha256",
        lambda self, path: f"sha-{path.name}",
    )

    def fake_resolve_many(self, paths, journal_map=None, **kwargs):
        observed.update(kwargs)
        return {
            Path(path): _metadata(
                sha256=f"sha-{Path(path).name}",
                current_filename=Path(path).name,
            )
            for path in paths
        }

    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.resolve_many", fake_resolve_many
    )

    result = CliRunner().invoke(pdf, ["identify", str(tmp_path)])

    assert result.exit_code == 0
    assert observed["strategies"] == RECOMMENDED_IDENTIFY_STRATEGIES
    assert callable(observed["cached_candidate_lookup"])
    assert observed["allow_network_lookup"] is False
    assert "Resolved PDFs: 1" in result.output


def test_identify_prompts_before_ads_queries(tmp_path, monkeypatch):
    for idx in range(51):
        (tmp_path / f"paper_{idx:03d}.pdf").write_bytes(b"%PDF")

    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.compute_sha256",
        lambda self, path: f"sha-{path.name}",
    )
    observed = []
    monkeypatch.setattr("adsfolio.pdf.cli._should_prompt_ads_permission", lambda: True)
    monkeypatch.setattr(
        "adsfolio.pdf.cli._estimate_ads_queries_for_targets",
        lambda identifier, pdf_paths, strategies=None: _estimate_ads_ready(pdf_paths),
    )

    def fake_resolve_many(self, paths, journal_map=None, **kwargs):
        observed.append(kwargs)
        return {Path(path): None for path in paths}

    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.resolve_many", fake_resolve_many
    )

    result = CliRunner().invoke(pdf, ["identify", str(tmp_path)], input="n\n\n")

    assert result.exit_code == 0
    assert "About to run ADS queries for 51 pending PDFs" in result.output
    assert "Proceed with 1 ADS queries?" in result.output
    assert (
        "Skipped ADS network queries at user request; checking caches." in result.output
    )
    assert observed[0]["allow_network_lookup"] is False
    assert "Unresolved PDFs: 51" in result.output


def test_identify_auto_skips_ads_prompt_when_estimate_is_too_high(
    tmp_path, monkeypatch
):
    for idx in range(251):
        (tmp_path / f"paper_{idx:03d}.pdf").write_bytes(b"%PDF")

    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.compute_sha256",
        lambda self, path: f"sha-{path.name}",
    )
    observed = []
    monkeypatch.setattr("adsfolio.pdf.cli._should_prompt_ads_permission", lambda: True)
    monkeypatch.setattr(
        "adsfolio.pdf.cli._estimate_ads_queries_for_targets",
        lambda identifier, pdf_paths, strategies=None: {
            "pending_files": len(pdf_paths),
            "estimated_ads_queries": 1004,
        },
    )

    def fake_resolve_many(self, paths, journal_map=None, **kwargs):
        observed.append(kwargs)
        return {Path(path): None for path in paths}

    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.resolve_many", fake_resolve_many
    )

    result = CliRunner().invoke(pdf, ["identify", str(tmp_path)], input="y\n")

    assert result.exit_code == 0
    assert (
        "Estimated ADS query volume (~1004) is at or above the high-risk threshold."
        in result.output
    )
    assert "Proceed with ADS queries?" not in result.output
    assert observed[0]["allow_network_lookup"] is False
    assert "Unresolved PDFs: 251" in result.output


def test_identify_high_risk_estimate_skips_ads_queries(tmp_path, monkeypatch):
    for idx in range(40):
        (tmp_path / f"paper_{idx:03d}.pdf").write_bytes(b"%PDF")

    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.compute_sha256",
        lambda self, path: f"sha-{path.name}",
    )
    monkeypatch.setattr("adsfolio.pdf.cli._should_prompt_ads_permission", lambda: True)
    monkeypatch.setattr(
        "adsfolio.pdf.cli._estimate_ads_queries_for_targets",
        lambda identifier, pdf_paths, strategies=None: {
            "pending_files": len(pdf_paths),
            "estimated_ads_queries": 1004,
        },
    )
    observed = []

    def fake_resolve_many(self, paths, journal_map=None, **kwargs):
        observed.append(kwargs)
        return {Path(path): None for path in paths}

    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.resolve_many", fake_resolve_many
    )

    result = CliRunner().invoke(pdf, ["identify", str(tmp_path)], input="\n")

    assert result.exit_code == 0
    assert "Skipping ADS queries automatically." in result.output
    assert observed[0]["allow_network_lookup"] is False


def test_identify_syncs_profile_to_registry_when_no_files_resolved(
    tmp_path, monkeypatch
):
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF")

    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.compute_sha256",
        lambda self, path: f"sha-{path.name}",
    )
    monkeypatch.setattr("adsfolio.pdf.cli._should_prompt_ads_permission", lambda: False)
    monkeypatch.setattr(
        "adsfolio.config.Config.load_user_profile",
        lambda: UserProfile(
            orcid="0000-0002-1825-0097",
            author_names=("Bach, Y. P.",),
            first_publication_year=2010,
            last_publication_year=2030,
            ads_library_ids=("LIB123",),
        ),
    )

    runner = CliRunner()
    result = runner.invoke(pdf, ["identify", str(tmp_path), "--allow-ads"])
    assert result.exit_code == 0
    assert "Synced identify profile to sidecar file." in result.output

    state = open_registry_state(tmp_path)
    try:
        assert state.identify_profile == {
            "orcid": "0000-0002-1825-0097",
            "query_names": ["Bach, Y. P."],
            "debut_year": 2010,
            "end_year": 2030,
            "library_ids": ["LIB123"],
        }
    finally:
        state.close()


def test_identify_verbose_parser_disables_parser_suppression(tmp_path, monkeypatch):
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF")
    observed = {}

    class StubIdentifier:
        MAX_BATCH_TERMS = 25

        def __init__(
            self,
            client,
            logger=None,
            suppress_parser_warnings=True,
        ):
            observed["suppress_parser_warnings"] = suppress_parser_warnings
            self.suppress_parser_warnings = suppress_parser_warnings

        def compute_sha256(self, path):
            return f"sha-{path.name}"

        def parse_filename(self, filename):
            return {}

        def extract_doi_from_metadata(self, path):
            return None

        def extract_doi_from_text(self, path):
            return None

        def extract_doi_candidates(self, path):
            return None, None

        def extract_bibcode_from_text(self, text):
            return None

        def extract_bibcode_from_pdf(self, path):
            return None

        def extract_bibcode_from_metadata(self, path):
            return None

        def extract_doi_from_filename(self, filename):
            return None

        def extract_bibcode_from_filename(self, filename):
            return None

        def resolve_many(self, paths, journal_map=None, **kwargs):
            return {Path(path): None for path in paths}

    monkeypatch.setattr("adsfolio.pdf.identify.PdfIdentifier", StubIdentifier)

    runner = CliRunner()
    result = runner.invoke(pdf, ["identify", str(tmp_path), "--verbose-parser"])
    assert result.exit_code == 0
    assert observed["suppress_parser_warnings"] is False


def test_identify_recursive_uses_registry_matches(tmp_path, monkeypatch):
    subdir = tmp_path / "nested"
    subdir.mkdir()
    known_pdf = subdir / "known.pdf"
    new_pdf = tmp_path / "new.pdf"
    known_pdf.write_bytes(b"%PDF")
    new_pdf.write_bytes(b"%PDF")

    state = open_registry_state(tmp_path)
    state.upsert_file(
        "nested/known-original.pdf",
        _metadata(
            sha256="sha-known",
            current_filename="nested/known.pdf",
            bibcode="2021A&A...654A.113B",
        ),
    )
    state.save()

    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.compute_sha256",
        lambda self, path: "sha-known" if Path(path).name == "known.pdf" else "sha-new",
    )

    resolve_calls = []

    def fake_resolve_many(self, paths, journal_map=None, **kwargs):
        normalized = [Path(path) for path in paths]
        resolve_calls.append(normalized)
        return {
            path: _metadata(
                sha256="sha-new",
                current_filename=path.name,
            )
            for path in normalized
        }

    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.resolve_many", fake_resolve_many
    )
    monkeypatch.setattr(
        "adsfolio.pdf.cli._estimate_ads_queries_for_targets",
        lambda identifier, pdf_paths, strategies=None: _estimate_ads_ready(pdf_paths),
    )

    runner = CliRunner()
    result = runner.invoke(
        pdf, ["identify", str(tmp_path), "--recursive", "--allow-ads"]
    )
    assert result.exit_code == 0
    assert resolve_calls == [[new_pdf]]
    assert "Resolved PDFs: 1" in result.output

    persisted = open_registry_state(tmp_path)
    try:
        assert persisted.get_file("new.pdf") is not None
        assert persisted.get_file("nested/known.pdf") is not None
    finally:
        persisted.close()


def test_identify_recursive_persists_filename_updates_in_registry(
    tmp_path, monkeypatch
):
    subdir = tmp_path / "nested"
    subdir.mkdir()
    renamed_pdf = subdir / "renamed.pdf"
    renamed_pdf.write_bytes(b"%PDF")

    state = open_registry_state(tmp_path)
    state.upsert_file(
        "nested/known-original.pdf",
        _metadata(
            sha256="sha-known",
            current_filename="nested/old-name.pdf",
            bibcode="2021A&A...654A.113B",
        ),
    )
    state.save()

    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.compute_sha256",
        lambda self, path: "sha-known",
    )

    resolve_calls = []
    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.resolve_many",
        lambda self, paths, journal_map=None, **kwargs: (
            resolve_calls.append(list(paths)) or {}
        ),
    )

    runner = CliRunner()
    result = runner.invoke(pdf, ["identify", str(tmp_path), "--recursive"])
    assert result.exit_code == 0
    assert resolve_calls == []

    persisted = open_registry_state(tmp_path)
    try:
        record = persisted.get_file("nested/renamed.pdf")
        assert record is not None
        assert record["current_filename"] == "nested/renamed.pdf"
    finally:
        persisted.close()


def test_identify_prefers_exact_catalog_path_for_duplicate_sha(tmp_path, monkeypatch):
    archived_pdf = tmp_path / "archived-copy.pdf"
    current_pdf = tmp_path / "current.pdf"
    archived_pdf.write_bytes(b"%PDF")
    current_pdf.write_bytes(b"%PDF")

    state = open_registry_state(tmp_path)
    state.upsert_file(
        "archived-copy.pdf",
        _metadata(
            sha256="sha-shared",
            current_filename="archived-copy.pdf",
            bibcode="2021A&A...654A.113B",
        ),
    )
    state.upsert_file(
        "current.pdf",
        _metadata(
            sha256="sha-shared",
            current_filename="current.pdf",
            bibcode="2021A&A...654A.113B",
        ),
    )
    state.save()

    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.compute_sha256",
        lambda self, path: "sha-shared",
    )

    result = CliRunner().invoke(pdf, ["identify", str(tmp_path)])

    assert result.exit_code == 0
    persisted = open_registry_state(tmp_path)
    try:
        archived_record = persisted.get_file("archived-copy.pdf")
        current_record = persisted.get_file("current.pdf")
        assert archived_record is not None
        assert current_record is not None
        assert archived_record["sha256"] == "sha-shared"
        assert current_record["sha256"] == "sha-shared"
    finally:
        persisted.close()


def test_identify_registers_new_duplicate_path_without_moving_existing_path(
    tmp_path, monkeypatch
):
    archived_pdf = tmp_path / "archived-copy.pdf"
    current_pdf = tmp_path / "current.pdf"
    archived_pdf.write_bytes(b"%PDF")
    current_pdf.write_bytes(b"%PDF")

    state = open_registry_state(tmp_path)
    state.upsert_file(
        "archived-copy.pdf",
        _metadata(
            sha256="sha-shared",
            current_filename="archived-copy.pdf",
            bibcode="2021A&A...654A.113B",
        ),
    )
    state.save()

    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.compute_sha256",
        lambda self, path: "sha-shared",
    )

    result = CliRunner().invoke(pdf, ["identify", str(tmp_path)])

    assert result.exit_code == 0
    persisted = open_registry_state(tmp_path)
    try:
        assert set(persisted.files) == {"archived-copy.pdf", "current.pdf"}
    finally:
        persisted.close()


def test_identify_reactivates_returned_catalog_path(tmp_path, monkeypatch):
    current_pdf = tmp_path / "current.pdf"
    current_pdf.write_bytes(b"%PDF")

    state = open_registry_state(tmp_path)
    state.upsert_file(
        "current.pdf",
        _metadata(
            sha256="sha-current",
            current_filename="current.pdf",
            bibcode="2021A&A...654A.113B",
        ),
    )
    state.registry.catalog.mark_missing_locations(tmp_path, set())
    state.save()

    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.compute_sha256",
        lambda self, path: "sha-current",
    )

    result = CliRunner().invoke(pdf, ["identify", str(tmp_path)])

    assert result.exit_code == 0
    persisted = open_registry_state(tmp_path)
    try:
        assert set(persisted.files) == {"current.pdf"}
    finally:
        persisted.close()


def test_identify_rehashes_replacement_at_missing_catalog_path(tmp_path, monkeypatch):
    current_pdf = tmp_path / "current.pdf"
    current_pdf.write_bytes(b"%OLD")
    original_stat = current_pdf.stat()

    state = open_registry_state(tmp_path)
    state.upsert_file(
        "current.pdf",
        _metadata(
            sha256=sha256_file(current_pdf),
            current_filename="current.pdf",
            bibcode="2021A&A...654A.113B",
        ),
    )
    state.registry.catalog.mark_missing_locations(tmp_path, set())
    state.save()

    current_pdf.write_bytes(b"%NEW")
    current_pdf.touch()
    current_pdf.chmod(original_stat.st_mode)
    os.utime(
        current_pdf,
        ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
    )
    hash_calls = []

    def fake_compute_sha(self, path):
        hash_calls.append(Path(path))
        return "sha-new"

    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.compute_sha256",
        fake_compute_sha,
    )

    result = CliRunner().invoke(pdf, ["identify", str(tmp_path)])

    assert result.exit_code == 0
    assert hash_calls == [current_pdf]
    persisted = open_registry_state(tmp_path)
    try:
        record = persisted.get_file("current.pdf")
        assert record is not None
        assert record["sha256"] == "sha-new"
        assert record["status"] == "unresolved"
    finally:
        persisted.close()


def test_identify_rate_limit_is_reported_as_operation_failure(tmp_path, monkeypatch):
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF")

    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.compute_sha256",
        lambda self, path: f"sha-{path.name}",
    )
    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.resolve_many",
        lambda self, paths, journal_map=None, **kwargs: (_ for _ in ()).throw(
            RateLimitError("Rate limit exceeded. Try again later.")
        ),
    )
    monkeypatch.setattr(
        "adsfolio.pdf.cli._estimate_ads_queries_for_targets",
        lambda identifier, pdf_paths, strategies=None: _estimate_ads_ready(pdf_paths),
    )

    result = CliRunner().invoke(pdf, ["identify", str(tmp_path), "--allow-ads"])

    assert result.exit_code == 1
    assert "Rate limit exceeded. Try again later." in result.output
    assert "Unresolved PDFs: 1" not in result.output


def test_identify_cache_refresh_rate_limit_is_operation_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("adsfolio.pdf.cli._get_client", lambda ctx: object())
    monkeypatch.setattr(
        Config,
        "load_user_profile",
        classmethod(lambda cls: UserProfile(orcid="0000-0000-0000-0000")),
    )
    monkeypatch.setattr(
        "adsfolio.pdf.identify.query_publications",
        lambda client, profile: (_ for _ in ()).throw(
            RateLimitError("Rate limit exceeded during refresh.")
        ),
    )

    result = CliRunner().invoke(pdf, ["identify", str(tmp_path), "--refresh-cache"])

    assert result.exit_code == 1
    assert "Rate limit exceeded during refresh." in result.output


def test_identify_repeated_strategy_options_preserve_order(tmp_path, monkeypatch):
    for idx in range(3):
        (tmp_path / f"paper_{idx:03d}.pdf").write_bytes(b"%PDF")

    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.compute_sha256",
        lambda self, path: f"sha-{path.name}",
    )
    observed = {}

    def fake_resolve_many(self, paths, journal_map=None, **kwargs):
        observed["strategies"] = kwargs["strategies"]
        observed["count"] = len(paths)
        return {Path(path): None for path in paths}

    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.resolve_many", fake_resolve_many
    )

    result = CliRunner().invoke(
        pdf,
        [
            "identify",
            str(tmp_path),
            "--strategy",
            "first_page_doi",
            "--strategy",
            "filename_bibcode",
            "--allow-ads",
        ],
    )

    assert result.exit_code == 0
    assert observed["strategies"] == (
        IdentifyStrategy.FIRST_PAGE_DOI,
        IdentifyStrategy.FILENAME_BIBCODE,
    )
    assert observed["count"] == 3


def test_rename_passes_character_and_author_join_policies_to_renamer(
    tmp_path, monkeypatch
):
    source = tmp_path / "old.pdf"
    source.write_bytes(b"%PDF")
    sidecar = open_registry_state(tmp_path)
    sidecar.upsert_file(
        "orig.pdf",
        _metadata(
            current_filename="old.pdf",
            bibcode="2021A&A...654A.113B",
            sha256=sha256_file(source),
        ),
    )
    sidecar.save()

    observed = {}

    def fake_batch_rename(
        self,
        sidecar,
        template,
        dry_run,
        author_format,
        max_authors,
        authors_when_truncated,
        char_policy,
        author_separator,
        author_truncation_suffix,
    ):
        observed["char_policy"] = char_policy
        observed["authors_when_truncated"] = authors_when_truncated
        observed["author_separator"] = author_separator
        observed["author_truncation_suffix"] = author_truncation_suffix
        return []

    monkeypatch.setattr("adsfolio.pdf.cli.PdfRenamer.batch_rename", fake_batch_rename)

    runner = CliRunner()
    result = runner.invoke(
        pdf,
        [
            "rename",
            str(tmp_path),
            "--char-policy",
            "keep",
            "--max-authors",
            "2",
            "--authors-when-truncated",
            "2",
            "--author-separator",
            "~",
            "--author-truncation-suffix",
            "++",
        ],
    )
    assert result.exit_code == 0
    assert observed["char_policy"] == "keep"
    assert observed["authors_when_truncated"] == 2
    assert observed["author_separator"] == "~"
    assert observed["author_truncation_suffix"] == "++"


def test_rename_rejects_retained_count_above_max_authors(tmp_path):
    result = CliRunner().invoke(
        pdf,
        [
            "rename",
            str(tmp_path),
            "--max-authors",
            "1",
            "--authors-when-truncated",
            "2",
        ],
    )

    assert result.exit_code == 2
    assert "authors_when_truncated cannot exceed max_authors" in result.output


def test_watch_rejects_retained_count_above_max_authors(tmp_path):
    result = CliRunner().invoke(
        pdf,
        [
            "watch",
            str(tmp_path),
            "--max-authors",
            "1",
            "--authors-when-truncated",
            "2",
        ],
    )

    assert result.exit_code == 2
    assert "authors_when_truncated cannot exceed max_authors" in result.output


def test_rename_dry_run_then_apply(tmp_path):
    source = tmp_path / "old.pdf"
    source.write_bytes(b"%PDF")
    sidecar = open_registry_state(tmp_path)
    sidecar.upsert_file(
        "orig.pdf",
        _metadata(
            current_filename="old.pdf",
            bibcode="2021A&A...654A.113B",
            sha256=sha256_file(source),
        ),
    )
    sidecar.save()

    runner = CliRunner()
    dry = runner.invoke(pdf, ["rename", str(tmp_path), "--scheme", "{bibcode}"])
    assert dry.exit_code == 0
    assert "old.pdf -> 2021A&A...654A.113B.pdf" in dry.output
    assert source.exists()

    apply = runner.invoke(
        pdf,
        ["rename", str(tmp_path), "--scheme", "{bibcode}", "--apply"],
    )
    assert apply.exit_code == 0
    assert not source.exists()
    assert (tmp_path / "2021A&A...654A.113B.pdf").exists()


def test_obsidian_dry_run_then_apply(tmp_path, monkeypatch):
    from adsfolio.api.search import SearchResult

    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF")
    sidecar = open_registry_state(tmp_path)
    sidecar.upsert_file("orig.pdf", _metadata(current_filename="paper.pdf"))
    sidecar.save()

    class _SearchStub:
        def query(self, q, fl=None, rows=10, start=0, sort=None, fq=None):
            return SearchResult(
                num_found=1,
                start=0,
                docs=[
                    {
                        "bibcode": ["2024ApJ...900..100S"],
                        "abstract": "Fetched abstract.",
                    }
                ],
            )

    class _ClientStub:
        search = _SearchStub()

    monkeypatch.setattr("adsfolio.pdf.cli._get_client", lambda ctx: _ClientStub())

    runner = CliRunner()
    dry = runner.invoke(pdf, ["obsidian", str(tmp_path)])
    assert dry.exit_code == 0
    assert "paper.pdf -> paper.md [create]" in dry.output
    assert "Dry-run only" in dry.output
    assert not (tmp_path / "paper.md").exists()

    apply = runner.invoke(pdf, ["obsidian", str(tmp_path), "--apply"])
    assert apply.exit_code == 0
    note_path = tmp_path / "paper.md"
    assert note_path.exists()
    content = note_path.read_text(encoding="utf-8")
    assert "Fetched abstract." in content
    assert f"[Open PDF]({pdf_path.resolve().as_uri()})" in content

    persisted = open_registry_state(tmp_path)
    try:
        assert persisted.get_file("paper.pdf")["abstract"] == "Fetched abstract."
    finally:
        persisted.close()


def test_push_library_apply_calls_add_documents(tmp_path, monkeypatch):
    sidecar = open_registry_state(tmp_path)
    sidecar.upsert_file("paper.pdf", _metadata())
    sidecar.save()

    class StubLibraries:
        def __init__(self):
            self.calls = []

        def add_documents(self, library_id, bibcodes):
            self.calls.append((library_id, bibcodes))
            return {"number_added": len(bibcodes)}

        def iter_libraries(self):
            yield SimpleNamespace(
                id="LIB123",
                name="Papers",
                permission="owner",
                public=False,
                num_documents=0,
            )

    class StubClient:
        instances: ClassVar[list[Any]] = []

        def __init__(self, token=None):
            self.libraries = StubLibraries()
            self.closed = False
            StubClient.instances.append(self)

        def close(self):
            self.closed = True

    monkeypatch.setattr("adsfolio.pdf.cli_common.ADSClient", StubClient)

    runner = CliRunner()
    result = runner.invoke(
        pdf,
        ["push-library", str(tmp_path), "LIB123", "--apply"],
    )
    assert result.exit_code == 0
    assert "ADS acknowledged 1 bibcodes" in result.output
    assert StubClient.instances[0].libraries.calls == [
        ("LIB123", ["2024ApJ...900..100S"])
    ]
    assert StubClient.instances[0].closed


class _FakeLibraries:
    """Record ADS library writes and optionally fail one add request."""

    def __init__(self, permission="owner", fail_call=None, error=None):
        self.permission = permission
        self.fail_call = fail_call
        self.error = error
        self.calls = []

    def iter_libraries(self):
        yield SimpleNamespace(
            id="LIB123",
            name="Papers",
            permission=self.permission,
            public=False,
            num_documents=0,
        )

    def add_documents(self, library_id, bibcodes):
        self.calls.append((library_id, list(bibcodes)))
        if len(self.calls) == self.fail_call:
            raise self.error
        return {"number_added": len(bibcodes)}


class _FakeLibraryClient:
    def __init__(self, libraries):
        self.libraries = libraries
        self.closed = False

    def close(self):
        self.closed = True


def _catalog_resolved_papers(root, count):
    """Catalog resolved PDFs with distinct identities and return their Bibcodes."""
    bibcodes = [f"2024Test.{index:05d}" for index in range(count)]
    state = open_registry_state(root)
    try:
        for index, bibcode in enumerate(bibcodes):
            name = f"paper{index:05d}.pdf"
            state.upsert_file(
                name,
                _metadata(
                    bibcode=bibcode,
                    doi=f"10.1234/{index}",
                    sha256=f"sha-{index}",
                    current_filename=name,
                ),
            )
        state.save()
    finally:
        state.close()
    return bibcodes


def test_push_library_dry_run_submits_nothing_and_skips_unresolved(
    tmp_path, monkeypatch
):
    _catalog_resolved_papers(tmp_path, 2)
    state = open_registry_state(tmp_path)
    state.upsert_file(
        "candidate.pdf",
        _metadata(
            bibcode="2024Cand.00001",
            doi="10.1234/candidate",
            sha256="sha-candidate",
            current_filename="candidate.pdf",
            status="unresolved",
        ),
    )
    state.save()
    state.close()

    def forbid_client(ctx):
        raise AssertionError("dry-run must not create an ADS client")

    monkeypatch.setattr("adsfolio.pdf.cli._get_client", forbid_client)

    result = CliRunner().invoke(pdf, ["push-library", str(tmp_path), "LIB123"])

    assert result.exit_code == 0, result.output
    assert "Prepared 2 bibcodes for library LIB123." in result.output
    assert "Skipped 1 records without a resolved ADS Bibcode." in result.output
    assert "Dry-run only" in result.output


def test_push_library_apply_batches_over_request_limit(tmp_path, monkeypatch):
    limit = AdsLibraryService.MAX_DOCUMENTS_PER_REQUEST
    bibcodes = _catalog_resolved_papers(tmp_path, limit + 1)
    client = _FakeLibraryClient(_FakeLibraries())
    monkeypatch.setattr("adsfolio.pdf.cli._get_client", lambda ctx: client)

    result = CliRunner().invoke(
        pdf, ["push-library", str(tmp_path), "LIB123", "--apply"]
    )

    assert result.exit_code == 0, result.output
    calls = client.libraries.calls
    assert [len(batch) for _, batch in calls] == [limit, 1]
    assert [code for _, batch in calls for code in batch] == sorted(bibcodes)
    assert f"ADS acknowledged {limit + 1} bibcodes" in result.output
    assert client.closed


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (ADSError("quota exceeded"), "rejected 3, status unknown 0"),
        (requests.ConnectionError("reset"), "rejected 0, status unknown 3"),
    ],
)
def test_push_library_later_batch_failure_reports_partial_progress(
    tmp_path, monkeypatch, error, outcome
):
    limit = AdsLibraryService.MAX_DOCUMENTS_PER_REQUEST
    _catalog_resolved_papers(tmp_path, limit + 3)
    client = _FakeLibraryClient(_FakeLibraries(fail_call=2, error=error))
    monkeypatch.setattr("adsfolio.pdf.cli._get_client", lambda ctx: client)

    result = CliRunner().invoke(
        pdf, ["push-library", str(tmp_path), "LIB123", "--apply"]
    )

    assert result.exit_code == 1
    assert len(client.libraries.calls) == 2
    assert f"Acknowledged {limit}, {outcome}, not attempted 0" in result.output
    assert "Re-run this command" in result.output
    assert client.closed


def test_push_library_refuses_read_only_library(tmp_path, monkeypatch):
    _catalog_resolved_papers(tmp_path, 1)
    client = _FakeLibraryClient(_FakeLibraries(permission="read"))
    monkeypatch.setattr("adsfolio.pdf.cli._get_client", lambda ctx: client)

    result = CliRunner().invoke(
        pdf, ["push-library", str(tmp_path), "LIB123", "--apply"]
    )

    assert result.exit_code == 1
    assert "not writable" in result.output
    assert "No bibcodes were submitted." in result.output
    assert client.libraries.calls == []
    assert client.closed


def test_status_reports_identified_unresolved_and_duplicates(tmp_path):
    tracked = tmp_path / "tracked.pdf"
    untracked = tmp_path / "untracked.pdf"
    tracked.write_bytes(b"%PDF")
    untracked.write_bytes(b"%PDF")

    sidecar = open_registry_state(tmp_path)
    sidecar.upsert_file(
        "orig1.pdf",
        _metadata(current_filename="tracked.pdf", sha256="same"),
    )
    sidecar.upsert_file(
        "orig2.pdf",
        _metadata(
            bibcode="2022ApJ...901..101A",
            current_filename="missing.pdf",
            sha256="same",
        ),
    )
    sidecar.save()

    runner = CliRunner()
    result = runner.invoke(pdf, ["status", str(tmp_path)])
    assert result.exit_code == 0
    assert "Identified (catalog locations): 2" in result.output
    assert "Unresolved PDFs on disk: 1" in result.output
    assert "Duplicate SHA-256 groups: 1" in result.output


def test_status_recursive_uses_registries_per_directory(tmp_path):
    subdir = tmp_path / "nested"
    subdir.mkdir()

    root_tracked = tmp_path / "root-tracked.pdf"
    nested_tracked = subdir / "nested-tracked.pdf"
    nested_untracked = subdir / "nested-untracked.pdf"
    root_tracked.write_bytes(b"%PDF")
    nested_tracked.write_bytes(b"%PDF")
    nested_untracked.write_bytes(b"%PDF")

    root_sidecar = open_registry_state(tmp_path)
    root_sidecar.upsert_file(
        "root-orig.pdf",
        _metadata(current_filename="root-tracked.pdf", sha256="sha-root"),
    )
    root_sidecar.save()

    nested_sidecar = open_registry_state(subdir)
    nested_sidecar.upsert_file(
        "nested-orig.pdf",
        _metadata(
            bibcode="2022ApJ...901..101A",
            current_filename="nested-tracked.pdf",
            sha256="sha-nested",
        ),
    )
    nested_sidecar.save()

    runner = CliRunner()
    result = runner.invoke(pdf, ["status", str(tmp_path), "--recursive"])
    assert result.exit_code == 0
    assert "Identified (catalog locations): 2" in result.output
    assert "Unresolved PDFs on disk: 1" in result.output


def test_identify_non_interactive_disables_network_but_checks_cache(
    tmp_path, monkeypatch
):
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF")

    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.compute_sha256",
        lambda self, path: f"sha-{path.name}",
    )
    monkeypatch.setattr("adsfolio.pdf.cli._should_prompt_ads_permission", lambda: False)
    observed = []

    def fake_resolve_many(self, paths, journal_map=None, **kwargs):
        observed.append(kwargs)
        return {Path(path): None for path in paths}

    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.resolve_many", fake_resolve_many
    )

    result = CliRunner().invoke(pdf, ["identify", str(tmp_path)])

    assert result.exit_code == 0
    assert (
        "Skipping ADS network queries because stdin is non-interactive" in result.output
    )
    assert observed[0]["allow_network_lookup"] is False


def test_identify_default_quiet_unresolved_moves_suppresses_per_file_logs(
    tmp_path, monkeypatch
):
    pdf_path = tmp_path / "unresolved.pdf"
    pdf_path.write_bytes(b"%PDF")
    output_dir = tmp_path / "unresolved-bin"

    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.compute_sha256",
        lambda self, path: "sha-unresolved",
    )
    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.resolve_many",
        lambda self, paths, journal_map=None, **kwargs: {
            Path(path): None for path in paths
        },
    )

    runner = CliRunner()
    result = runner.invoke(
        pdf,
        [
            "identify",
            str(tmp_path),
            "--output-unresolved",
            str(output_dir),
            "--apply",
        ],
    )
    assert result.exit_code == 0
    assert "Moved unresolved:" not in result.output


def test_identify_verbose_unresolved_moves_prints_per_file_logs(tmp_path, monkeypatch):
    pdf_path = tmp_path / "unresolved.pdf"
    pdf_path.write_bytes(b"%PDF")
    output_dir = tmp_path / "unresolved-bin"

    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.compute_sha256",
        lambda self, path: "sha-unresolved",
    )
    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.resolve_many",
        lambda self, paths, journal_map=None, **kwargs: {
            Path(path): None for path in paths
        },
    )

    runner = CliRunner()
    result = runner.invoke(
        pdf,
        [
            "identify",
            str(tmp_path),
            "--output-unresolved",
            str(output_dir),
            "--apply",
            "-v",
        ],
    )
    assert result.exit_code == 0
    assert "Moved unresolved:" in result.output


def test_pdf_ocr_routes_non_ocr_files(tmp_path, monkeypatch):
    scan_only = tmp_path / "scan.pdf"
    text_pdf = tmp_path / "text.pdf"
    scan_only.write_bytes(b"%PDF")
    text_pdf.write_bytes(b"%PDF")
    non_ocr_dir = tmp_path / "non_ocred"

    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.compute_sha256",
        lambda self, path: f"sha-{Path(path).name}",
    )
    monkeypatch.setattr(
        "adsfolio.pdf.cli._parallel_extract_first_page_previews",
        lambda paths, suppress_parser_warnings, desc, unit, max_chars=1200: {
            Path(path): ""
            if Path(path).name == "scan.pdf"
            else (
                "This page has enough alphabetic text to pass the non OCR detection "
                "threshold for routing."
            )
            for path in paths
        },
    )

    runner = CliRunner()
    result = runner.invoke(
        pdf,
        [
            "ocr",
            str(tmp_path),
            "--output",
            str(non_ocr_dir),
            "--apply",
        ],
    )
    assert result.exit_code == 0
    assert "likely non-OCR=1" in result.output
    assert "You may run OCR with a command like this:" in result.output
    assert f"srcdir={non_ocr_dir}" in result.output
    assert f"outdir={non_ocr_dir}_ocred" in result.output
    assert (non_ocr_dir / "scan.pdf").exists()
    assert text_pdf.exists()


def test_pdf_ocr_make_ocr_sh_writes_script_with_output_dir(tmp_path, monkeypatch):
    pdf_path = tmp_path / "scan.pdf"
    pdf_path.write_bytes(b"%PDF")
    non_ocr_dir = tmp_path / "ocr_required"
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(
        "adsfolio.pdf.cli._parallel_extract_first_page_previews",
        lambda paths, suppress_parser_warnings, desc, unit, max_chars=1200: {
            Path(path): "" for path in paths
        },
    )

    runner = CliRunner()
    result = runner.invoke(
        pdf,
        [
            "ocr",
            ".",
            "--output",
            str(non_ocr_dir),
            "--make-ocr-sh",
        ],
    )
    assert result.exit_code == 0
    assert "Run it with: bash ocrmypdf.sh" in result.output
    script_path = tmp_path / "ocrmypdf.sh"
    assert script_path.exists()
    script_body = script_path.read_text(encoding="utf-8")
    assert f"srcdir={non_ocr_dir}" in script_body
    assert f"outdir={non_ocr_dir}_ocred" in script_body


def test_identify_can_import_ai_csv_resolutions(tmp_path, monkeypatch):
    pdf_path = tmp_path / "unresolved.pdf"
    pdf_path.write_bytes(b"%PDF")
    ai_csv = tmp_path / "ai.csv"
    ai_csv.write_text("relative_path,ads_bibcode\nunresolved.pdf,2024ApJ...900..100S\n")

    monkeypatch.setattr(
        "adsfolio.pdf.cli.PdfIdentifier.compute_sha256",
        lambda self, path: "sha-unresolved",
    )
    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.resolve_many",
        lambda self, paths, journal_map=None, **kwargs: {
            Path(path): None for path in paths
        },
    )
    monkeypatch.setattr(
        "adsfolio.pdf.cli._estimate_ads_queries_for_targets",
        lambda identifier, pdf_paths, strategies=None: {
            "pending_files": len(pdf_paths),
            "metadata_doi_files": 0,
            "text_doi_files": 0,
            "no_doi_files": len(pdf_paths),
            "doi_candidate_files": 0,
            "unique_dois": 0,
            "doi_batch_queries": 0,
            "estimated_ads_queries": 0,
        },
    )
    monkeypatch.setattr("adsfolio.pdf.cli._should_prompt_ads_permission", lambda: True)
    monkeypatch.setattr(
        "adsfolio.pdf.cli._resolve_from_ai_csv",
        lambda identifier, directory, unresolved_paths, ai_csv_path: {
            unresolved_paths[0]: _metadata(
                bibcode="2024ApJ...900..100S",
                sha256="sha-unresolved",
                current_filename="unresolved.pdf",
            )
        },
    )

    runner = CliRunner()
    result = runner.invoke(
        pdf,
        ["identify", str(tmp_path)],
        input=f"{ai_csv}\n",
    )
    assert result.exit_code == 0
    assert "Applying AI-enriched CSV:" in result.output
    assert "AI CSV resolved additional PDFs: 1" in result.output
    assert "Resolved PDFs: 1" in result.output
    assert "Unresolved PDFs: 0" in result.output

    sidecar = open_registry_state(tmp_path)
    record = sidecar.get_file("unresolved.pdf")
    assert record is not None
    assert record["bibcode"] == "2024ApJ...900..100S"


def test_identify_retries_registry_records_marked_unresolved(tmp_path, monkeypatch):
    pdf_path = tmp_path / "retry.pdf"
    pdf_path.write_bytes(b"%PDF")

    sidecar = open_registry_state(tmp_path)
    sidecar.upsert_file(
        "retry.pdf",
        {
            "current_filename": "retry.pdf",
            "sha256": "sha-retry",
            "status": "unresolved",
        },
    )
    sidecar.save()

    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.compute_sha256",
        lambda self, path: "sha-retry",
    )
    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.resolve_many",
        lambda self, paths, **kwargs: {
            Path(path): _metadata(
                bibcode="2024ApJ...900..100S",
                sha256="sha-retry",
                current_filename=Path(path).name,
            )
            for path in paths
        },
    )

    runner = CliRunner()
    result = runner.invoke(pdf, ["identify", str(tmp_path), "--allow-ads"])
    assert result.exit_code == 0
    assert "Resolved PDFs: 1" in result.output

    state = open_registry_state(tmp_path)
    assert state.get_file("retry.pdf")["bibcode"] == "2024ApJ...900..100S"
    assert (
        list(state.registry.iter_files())
        and len(list(state.registry.iter_files())) == 1
    )


def test_identify_can_skip_default_unresolved_directory_before_hashing(
    tmp_path, monkeypatch
):
    candidate = tmp_path / "candidate.pdf"
    candidate.write_bytes(b"%PDF")
    unresolved_dir = tmp_path / "unresolved"
    unresolved_dir.mkdir()
    old_unresolved = unresolved_dir / "old.pdf"
    old_unresolved.write_bytes(b"%PDF")
    hash_calls = []

    def fake_compute_sha(self, path):
        hash_calls.append(Path(path))
        return f"sha-{Path(path).name}"

    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.compute_sha256",
        fake_compute_sha,
    )
    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.resolve_many",
        lambda self, paths, journal_map=None, **kwargs: {
            Path(path): _metadata(
                sha256=f"sha-{Path(path).name}",
                current_filename=Path(path).name,
            )
            for path in paths
        },
    )
    monkeypatch.setattr(
        "adsfolio.pdf.cli._estimate_ads_queries_for_targets",
        lambda identifier, pdf_paths, strategies=None: _estimate_ads_ready(pdf_paths),
    )

    result = CliRunner().invoke(
        pdf,
        [
            "identify",
            str(tmp_path),
            "--recursive",
            "--skip-unresolved-directory",
            "--allow-ads",
        ],
    )

    assert result.exit_code == 0
    assert f"Skipped unresolved directory: 1 PDFs ({unresolved_dir})" in result.output
    assert hash_calls == [candidate]
    assert old_unresolved.exists()


def test_identify_rejects_skipping_when_unresolved_destination_is_scan_root(
    tmp_path,
):
    (tmp_path / "paper.pdf").write_bytes(b"%PDF")

    result = CliRunner().invoke(
        pdf,
        [
            "identify",
            str(tmp_path),
            "--recursive",
            "--output-unresolved",
            str(tmp_path),
            "--skip-unresolved-directory",
        ],
    )

    assert result.exit_code == 2
    assert "because it contains the Identify scan root" in result.output


def test_identify_rejects_skipping_unresolved_ancestor_of_scan_root(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    (library / "paper.pdf").write_bytes(b"%PDF")

    result = CliRunner().invoke(
        pdf,
        [
            "identify",
            str(library),
            "--recursive",
            "--output-unresolved",
            str(tmp_path),
            "--skip-unresolved-directory",
        ],
    )

    assert result.exit_code == 2
    assert "because it contains the Identify scan root" in result.output


def test_rename_allow_ads_identifies_pending_pdfs(tmp_path, monkeypatch):
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF")
    captured = {}

    def fake_run_identify(client, directory, *, helpers, **options):
        captured.update(
            client=client, directory=directory, helpers=helpers, options=options
        )

    monkeypatch.setattr("adsfolio.pdf.cli.run_identify", fake_run_identify)

    runner = CliRunner()
    result = runner.invoke(pdf, ["rename", str(tmp_path), "--allow-ads"])

    assert result.exit_code == 0
    assert "Identifying pending PDFs before rename..." in result.output
    assert captured["directory"] == tmp_path
    assert captured["options"] == {
        "recursive": True,
        "debut_year": None,
        "end_year": None,
        "refresh_cache": False,
        "output_unresolved": None,
        "skip_unresolved_directory": False,
        "apply": False,
        "verbose_parser": False,
        "strategies": RECOMMENDED_IDENTIFY_STRATEGIES,
        "providers": DEFAULT_DOI_PROVIDERS,
        "allow_ads": True,
        "ai_csv": None,
        "verbose": 0,
        "prompt_for_ai_csv": False,
    }


def test_rename_allow_ads_routes_unresolved_relative_to_directory(
    tmp_path, monkeypatch
):
    captured = {}

    def fake_run_identify(client, directory, *, helpers, **options):
        captured.update(directory=directory, options=options)

    monkeypatch.setattr("adsfolio.pdf.cli.run_identify", fake_run_identify)

    runner = CliRunner()
    result = runner.invoke(
        pdf,
        [
            "rename",
            str(tmp_path),
            "--allow-ads",
            "--output-unresolved",
            "unresolved",
            "--apply",
        ],
    )

    assert result.exit_code == 0
    assert captured["directory"] == tmp_path
    assert captured["options"]["output_unresolved"] == tmp_path / "unresolved"
    assert captured["options"]["apply"] is True


def test_identify_passes_saved_strategy_policy_to_resolver(tmp_path, monkeypatch):
    pdf_path = tmp_path / "mystery.pdf"
    pdf_path.write_bytes(b"%PDF")
    saved = IdentifySettings(strategies=(IdentifyStrategy.FIRST_PAGE_BIBCODE,))
    monkeypatch.setattr(Config, "load_identify_settings", lambda: saved)
    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.compute_sha256",
        lambda self, path: "sha-mystery",
    )
    observed = {}

    def fake_resolve_many(self, paths, journal_map=None, **kwargs):
        observed.update(kwargs)
        return {Path(path): None for path in paths}

    monkeypatch.setattr(
        "adsfolio.pdf.identify.PdfIdentifier.resolve_many", fake_resolve_many
    )

    result = CliRunner().invoke(pdf, ["identify", str(tmp_path), "--allow-ads"])

    assert result.exit_code == 0
    assert observed["strategies"] == (IdentifyStrategy.FIRST_PAGE_BIBCODE,)


def test_estimator_does_not_run_disabled_strategy_extractors(tmp_path):
    pdf_path = tmp_path / "2024ApJ...900..100S.pdf"
    pdf_path.write_bytes(b"%PDF")

    class Identifier:
        MAX_BATCH_TERMS = 25
        suppress_parser_warnings = True

        def extract_bibcode_from_filename(self, filename):
            return "2024ApJ...900..100S"

        def extract_doi_from_metadata(self, path):
            raise AssertionError("disabled metadata DOI strategy ran")

        def extract_bibcode_from_metadata(self, path):
            raise AssertionError("disabled metadata Bibcode strategy ran")

        def parse_filename(self, filename):
            raise AssertionError("disabled structured filename strategy ran")

    analysis = _estimate_ads_queries_for_targets(
        Identifier(),
        [pdf_path],
        (IdentifyStrategy.FILENAME_BIBCODE,),
    )

    assert analysis["filename_bibcode_files"] == 1
    assert analysis["metadata_bibcode_files"] == 0
    assert analysis["metadata_doi_files"] == 0
    assert analysis["estimated_ads_queries"] == 1


def test_cached_candidate_lookup_preserves_ambiguous_doi_as_a_miss(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        Config,
        "get_config_dir",
        classmethod(lambda cls: tmp_path / "config"),
    )
    registry = PdfRegistry(tmp_path)
    documents = [
        {
            "bibcode": "2024ApJ...900..100A",
            "doi": ["10.1234/ambiguous"],
            "title": ["Candidate A"],
        },
        {
            "bibcode": "2024ApJ...900..100B",
            "doi": ["10.1234/ambiguous"],
            "title": ["Candidate B"],
        },
    ]
    registry.upsert_ads_documents(documents, source="test")
    identifier = PdfIdentifier(SimpleNamespace())
    lookup = build_cached_candidate_lookup(identifier, registry, [])

    try:
        cache_hit, document = lookup("doi", "10.1234/ambiguous")
        assert cache_hit is True
        assert document is None
    finally:
        registry.close()


def test_watch_command_uses_polling_backend(tmp_path, monkeypatch):
    new_pdf = tmp_path / "new.pdf"
    new_pdf.write_bytes(b"%PDF")

    monkeypatch.setattr(
        PdfWatcher,
        "process_new_pdf",
        lambda self, **kwargs: {"bibcode": "2024ApJ...900..100S"},
    )

    def fake_poll_loop(
        self, directory, sidecar, interval, recursive, callback, **kwargs
    ):
        callback(new_pdf)

    monkeypatch.setattr(PdfWatcher, "poll_loop", fake_poll_loop)

    runner = CliRunner()
    result = runner.invoke(pdf, ["watch", str(tmp_path)])
    assert result.exit_code == 0
    assert "Watching directory" in result.output
    assert "[DRY-RUN] Would rename: new.pdf" in result.output
    assert "2024ApJ...900..100S" in result.output


def test_watch_command_passes_character_and_author_join_policies_to_processor(
    tmp_path, monkeypatch
):
    observed = {}
    new_pdf = tmp_path / "new.pdf"
    new_pdf.write_bytes(b"%PDF")

    def fake_process(self, **kwargs):
        observed["char_policy"] = kwargs.get("char_policy")
        observed["authors_when_truncated"] = kwargs.get("authors_when_truncated")
        observed["author_separator"] = kwargs.get("author_separator")
        observed["author_truncation_suffix"] = kwargs.get("author_truncation_suffix")
        return {"bibcode": "2024ApJ...900..100S"}

    def fake_poll_loop(
        self, directory, sidecar, interval, recursive, callback, **kwargs
    ):
        callback(new_pdf)

    monkeypatch.setattr(PdfWatcher, "process_new_pdf", fake_process)
    monkeypatch.setattr(PdfWatcher, "poll_loop", fake_poll_loop)

    runner = CliRunner()
    result = runner.invoke(
        pdf,
        [
            "watch",
            str(tmp_path),
            "--char-policy",
            "keep",
            "--max-authors",
            "2",
            "--authors-when-truncated",
            "2",
            "--author-separator",
            "~",
            "--author-truncation-suffix",
            "++",
        ],
    )
    assert result.exit_code == 0
    assert observed["char_policy"] == "keep"
    assert observed["authors_when_truncated"] == 2
    assert observed["author_separator"] == "~"
    assert observed["author_truncation_suffix"] == "++"


def test_watch_command_defaults_to_unicode_and_normalizes_unresolved_path(
    tmp_path, monkeypatch
):
    observed = {}
    new_pdf = tmp_path / "new.pdf"
    new_pdf.write_bytes(b"%PDF")

    def fake_process(self, **kwargs):
        observed.update(kwargs)
        return {"bibcode": "2024ApJ...900..100S"}

    def fake_poll_loop(
        self, directory, sidecar, interval, recursive, callback, **kwargs
    ):
        callback(new_pdf)

    monkeypatch.setattr(PdfWatcher, "process_new_pdf", fake_process)
    monkeypatch.setattr(PdfWatcher, "poll_loop", fake_poll_loop)

    result = CliRunner().invoke(
        pdf,
        ["watch", str(tmp_path), "--output-unresolved", "unresolved"],
    )

    assert result.exit_code == 0
    assert observed["char_policy"] == "keep"
    assert observed["output_unresolved"] == (tmp_path / "unresolved").resolve()


def test_watch_command_rejects_unresolved_path_outside_root(tmp_path):
    result = CliRunner().invoke(
        pdf,
        ["watch", str(tmp_path), "--output-unresolved", "../outside"],
    )

    assert result.exit_code == 2
    assert "must be inside the watched directory" in result.output


def test_watch_command_forwards_watchdog_reconciliation_interval(tmp_path, monkeypatch):
    observed = {}

    def fake_watchdog_loop(self, **kwargs):
        observed.update(kwargs)

    monkeypatch.setattr(PdfWatcher, "watchdog_loop", fake_watchdog_loop)

    result = CliRunner().invoke(
        pdf,
        ["watch", str(tmp_path), "--use-watchdog", "--interval", "17"],
    )

    assert result.exit_code == 0
    assert observed["reconciliation_interval"] == 17.0
    assert observed["stop_event"] is not None


def test_watch_command_passes_auto_upgrade_options(tmp_path, monkeypatch):
    called_kwargs = {}

    monkeypatch.setattr(
        PdfWatcher,
        "process_new_pdf",
        lambda self, **kwargs: {"bibcode": "2024ApJ...900..100S"},
    )

    def fake_poll_loop(
        self, directory, sidecar, interval, recursive, callback, **kwargs
    ):
        called_kwargs.update(kwargs)
        callback(tmp_path / "new.pdf")

    monkeypatch.setattr(PdfWatcher, "poll_loop", fake_poll_loop)
    (tmp_path / "new.pdf").write_bytes(b"%PDF")

    runner = CliRunner()
    result = runner.invoke(
        pdf,
        [
            "watch",
            str(tmp_path),
            "--auto-upgrade",
            "--upgrade-interval",
            "12",
            "--overwrite-arxiv-with-official",
            "--skip-annotation-port",
            "--force",
        ],
    )
    assert result.exit_code == 0
    assert called_kwargs["auto_upgrade"] is True
    assert called_kwargs["upgrade_interval_hours"] == 12.0
    assert called_kwargs["upgrade_kwargs"]["overwrite_arxiv_with_official"] is True
    assert called_kwargs["upgrade_kwargs"]["skip_annotation_port"] is True
    assert called_kwargs["upgrade_kwargs"]["force"] is True


def test_upgrade_command_reports_results(tmp_path, monkeypatch):
    sidecar = open_registry_state(tmp_path)
    sidecar.upsert_file(
        "paper.pdf",
        _metadata(
            bibcode="2024arXiv240112345B",
            doi="10.48550/arXiv.2401.12345",
            bibstem=["arXiv"],
            current_filename="paper.pdf",
        ),
    )
    sidecar.save()

    monkeypatch.setattr(
        "adsfolio.pdf.cli_upgrade.PdfUpgrader.batch_upgrade",
        lambda self, **kwargs: [
            UpgradeResult(
                original_filename="paper.pdf",
                current_filename="paper.pdf",
                old_bibcode="2024arXiv240112345B",
                new_bibcode="2025ApJ...123..456A",
                status="dry-run",
                message="Upgrade preview only.",
            )
        ],
    )

    runner = CliRunner()
    result = runner.invoke(pdf, ["upgrade", str(tmp_path)])
    assert result.exit_code == 0
    assert "paper.pdf: dry-run" in result.output
    assert "Dry-run only" in result.output


def test_upgrade_command_uses_registry_without_creating_sidecar(tmp_path, monkeypatch):
    captured = {}

    def batch_upgrade(self, **kwargs):
        captured["state"] = kwargs["sidecar"]
        return []

    monkeypatch.setattr(
        "adsfolio.pdf.cli_upgrade.PdfUpgrader.batch_upgrade", batch_upgrade
    )

    runner = CliRunner()
    result = runner.invoke(pdf, ["upgrade", str(tmp_path)])

    assert result.exit_code == 0
    assert isinstance(captured["state"], PdfRegistryState)
    assert (tmp_path / "config" / CATALOG_FILENAME).exists()


def test_parse_ads_identifier_normalizes_doi_resolver_urls() -> None:
    assert _parse_ads_identifier("https://doi.org/10.1364/JOSA.24.000206") == (
        "doi",
        "10.1364/josa.24.000206",
    )
    assert _parse_ads_identifier("https://doi.org/10.1234/a(b)") == (
        "doi",
        "10.1234/a(b)",
    )
    assert _parse_ads_identifier("https://dx.doi.org/10.1234/a%28b%29?loc=1#f") == (
        "doi",
        "10.1234/a(b)",
    )
    assert _parse_ads_identifier(
        "http://dx.doi.org/10.1002/%28SICI%291097-0258%2819980815%2917?loc=en#frag"
    ) == (
        "doi",
        "10.1002/(sici)1097-0258(19980815)17",
    )
    assert _parse_ads_identifier(
        "DOI: https://doi.org/10.1051/0004-6361/201834764"
    ) == (
        "doi",
        "10.1051/0004-6361/201834764",
    )
    assert _parse_ads_identifier("DOI: 10.1234/a(b).") == (
        "doi",
        "10.1234/a(b).",
    )
    assert _parse_ads_identifier("https://doi.org/10.1000/ab(c)") == (
        "doi",
        "10.1000/ab(c)",
    )
    assert _parse_ads_identifier("10.1000/ab(c)") == (
        "doi",
        "10.1000/ab(c)",
    )
    assert _parse_ads_identifier("https://example.org/10.1364/JOSA.24.000206") == (
        "bibcode",
        "https://example.org/10.1364/JOSA.24.000206",
    )
    assert _parse_ads_identifier("https://[") == ("bibcode", "https://[")


def test_refresh_ads_cli_resolves_doi_resolver_url_with_closing_paren(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    pdf_path = library / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%test\n")
    state = open_registry_state(library)
    try:
        state.upsert_file(
            "paper.pdf",
            {
                "current_filename": "paper.pdf",
                "sha256": "sha-paren",
                "status": "unresolved",
            },
        )
        state.save()
    finally:
        state.close()

    queries: list[str] = []

    class FakeSearch:
        def query(self, *, q: str, fl: list[str], rows: int):
            del fl, rows
            queries.append(q)
            return SimpleNamespace(
                num_found=1,
                docs=[
                    {
                        "bibcode": "1998StatM..17.1661B",
                        "doi": ["10.1234/a(b)"],
                        "title": ["Parenthesis DOI Paper"],
                        "author": ["Bach, Yoonsoo P."],
                        "year": "1998",
                        "bibstem": ["StatM"],
                        "volume": "17",
                        "page": ["1661"],
                        "pub": "Statistics in Medicine",
                    }
                ],
            )

    monkeypatch.setattr(
        "adsfolio.pdf.cli._get_client",
        lambda _ctx: SimpleNamespace(search=FakeSearch()),
    )
    runner = CliRunner()
    result = runner.invoke(
        pdf,
        [
            "refresh-ads",
            str(library),
            "--identifier",
            "https://doi.org/10.1234/a(b)",
            "--relative-path",
            "paper.pdf",
        ],
    )
    assert result.exit_code == 0, result.output
    assert queries == ['doi:"10.1234/a(b)"']

    verify_state = open_registry_state(library)
    try:
        records = list(verify_state.registry.iter_files())
        assert len(records) == 1
        assert records[0]["status"] == "resolved"
        assert records[0]["bibcode"] == "1998StatM..17.1661B"
        assert records[0]["metadata"]["title"] == ["Parenthesis DOI Paper"]
    finally:
        verify_state.close()


def test_external_identify_then_rename_without_ads_token(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    source = root / "10.1234_example.pdf"
    source.write_bytes(b"%PDF-1.4 external publication")
    monkeypatch.setattr(
        "adsfolio.pdf.cli._get_client",
        lambda ctx: pytest.fail(
            "External lookup and local rename must not create an ADS client"
        ),
    )
    monkeypatch.setattr(
        "adsfolio.pdf.identifier.fetch_crossref_doi",
        lambda doi: {
            "doi": [doi],
            "title": ["External Paper"],
            "author": ["Curie, Marie"],
            "year": "2024",
        },
    )
    result = CliRunner().invoke(
        pdf,
        [
            "identify",
            str(root),
            "--provider",
            "crossref",
            "--strategy",
            "filename_doi",
            "--apply",
        ],
    )
    assert result.exit_code == 0, result.output
    state = open_registry_state(root)
    before = state.registry.find_file_by_current_path(root, source.name)
    assert before["metadata"]["_adsfolio_external_metadata"]["provider"] == "crossref"
    identity = before["id"]
    state.close()
    result = CliRunner().invoke(pdf, ["rename", str(root), "--apply"])
    assert result.exit_code == 0, result.output
    renamed = list(root.glob("*.pdf"))
    assert len(renamed) == 1
    assert renamed[0].name == "CurieM_2024-External_Paper.pdf"
    state = open_registry_state(root)
    after = state.registry.find_file_by_current_path(root, renamed[0].name)
    assert after["id"] == identity
    assert after["metadata"]["title"] == ["External Paper"]
    state.close()


def test_external_lookup_failure_does_not_move_pdf(tmp_path, monkeypatch):
    from adsfolio.pdf.doi_providers import ProviderNetworkError

    root = tmp_path / "library"
    root.mkdir()
    source = root / "10.1234_example.pdf"
    content = b"%PDF-1.4 preserved on failure"
    source.write_bytes(content)

    def fail_lookup(doi):
        raise ProviderNetworkError(f"Crossref timeout for DOI {doi}")

    monkeypatch.setattr("adsfolio.pdf.identifier.fetch_crossref_doi", fail_lookup)
    result = CliRunner().invoke(
        pdf,
        [
            "identify",
            str(root),
            "--provider",
            "crossref",
            "--strategy",
            "filename_doi",
            "--output-unresolved",
            str(root / "unresolved"),
            "--apply",
        ],
    )
    assert result.exit_code == 1
    assert "Crossref timeout" in result.output
    assert source.read_bytes() == content
    assert not (root / "unresolved" / source.name).exists()
