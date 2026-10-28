"""Resolved PDFs leave unresolved while retaining consistent catalog locations."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner
from pdf_identity_fixtures import write_pdf

from adsfolio.cli.commands import main
from adsfolio.config import ADSError, Config
from adsfolio.pdf.files import sha256_file
from adsfolio.pdf.identifier import PdfIdentifier
from adsfolio.pdf.identify_settings import IdentifySettings
from adsfolio.pdf.registry import LOCKED_METADATA_FIELDS_KEY, open_registry_state
from adsfolio.pdf.rename_settings import RenameSettings
from adsfolio.pdf.tracked_move import resolved_file_directory


@pytest.fixture(autouse=True)
def isolated_catalog(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    monkeypatch.setattr(
        "adsfolio.pubcache.cache_path", lambda: tmp_path / "cache" / "publications.json"
    )
    monkeypatch.setattr(
        "adsfolio.pdf.cli._estimate_ads_queries_for_targets",
        lambda *args, **kwargs: {},
    )


@pytest.mark.parametrize("apply", [False, True])
@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("unresolved_name", ["unresolved", "pending-review"])
def test_identify_moves_new_and_cached_matches(
    monkeypatch, tmp_path, apply, cached, unresolved_name
) -> None:
    root = tmp_path / "library"
    source = root / unresolved_name / "nested" / "Paper 한글.pdf"
    source.parent.mkdir(parents=True)
    write_pdf(source, doi="10.1234/relocation")
    record = PdfIdentifier(None).build_record(
        {"bibcode": "2024ApJ...900..100S", "doi": ["10.1234/relocation"]}, source
    )
    if cached:
        state = open_registry_state(root)
        state.upsert_file(record["current_filename"], record)
        state.close()

    calls = []

    def resolve(self, paths, **kwargs):
        calls.extend(paths)
        return {path: dict(record) for path in paths}

    monkeypatch.setattr(PdfIdentifier, "resolve_many", resolve)
    args = ["pdf", "identify", str(root), "--recursive"]
    if unresolved_name != "unresolved":
        args.extend(["--output-unresolved", unresolved_name])
    if apply:
        args.append("--apply")
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    assert bool(calls) != cached
    destination = root / source.name
    assert destination.exists() == apply
    assert source.exists() != apply
    state = open_registry_state(root)
    try:
        paths = [
            row["current_path"] for row in state.registry.catalog.query_files(root=root)
        ]
        assert paths == [
            destination.name if apply else source.relative_to(root).as_posix()
        ]
    finally:
        state.close()
    assert (
        "Moved resolved PDF" if apply else "[dry-run] move resolved PDF"
    ) in result.output


def test_identify_collision_keeps_both_files_and_resolved_catalog(
    monkeypatch, tmp_path
) -> None:
    root = tmp_path / "library"
    source = root / "unresolved" / "paper.pdf"
    source.parent.mkdir(parents=True)
    source_content = write_pdf(source, doi="10.1234/relocation")
    target = root / source.name
    target.write_bytes(b"existing")
    monkeypatch.setattr(
        PdfIdentifier,
        "resolve_many",
        lambda self, paths, **kwargs: {
            path: PdfIdentifier(None).build_record(
                {"bibcode": "B1", "doi": ["10.1234/relocation"]}, path
            )
            if path == source
            else None
            for path in paths
        },
    )
    result = CliRunner().invoke(
        main, ["pdf", "identify", str(root), "--recursive", "--apply"]
    )
    assert result.exit_code == 0, result.output
    assert "destination exists" in result.output
    assert source.read_bytes() == source_content
    assert target.read_bytes() == b"existing"
    state = open_registry_state(root)
    try:
        assert state.get_file("unresolved/paper.pdf")["bibcode"] == "B1"
    finally:
        state.close()


def test_ai_csv_resolved_file_moves_out_in_same_run(monkeypatch, tmp_path) -> None:
    root = tmp_path / "library"
    source = root / "unresolved" / "paper.pdf"
    source.parent.mkdir(parents=True)
    write_pdf(source, doi="10.1234/relocation")
    csv = tmp_path / "hints.csv"
    csv.write_text("filename,bibcode\npaper.pdf,B1\n")
    monkeypatch.setattr(
        PdfIdentifier,
        "resolve_many",
        lambda self, paths, **kwargs: {path: None for path in paths},
    )
    monkeypatch.setattr(
        "adsfolio.pdf.cli._resolve_from_ai_csv",
        lambda identifier, directory, paths, csv_path: {
            path: PdfIdentifier(None).build_record(
                {"bibcode": "B1", "doi": ["10.1234/relocation"]}, path
            )
            for path in paths
        },
    )
    monkeypatch.setattr(
        "adsfolio.pdf.cli._parallel_extract_first_page_previews",
        lambda *args, **kwargs: {},
    )
    result = CliRunner().invoke(
        main,
        ["pdf", "identify", str(root), "--recursive", "--apply", "--ai-csv", str(csv)],
    )
    assert result.exit_code == 0, result.output
    assert not source.exists()
    assert (root / "paper.pdf").exists()
    state = open_registry_state(root)
    try:
        assert state.get_file("paper.pdf")["bibcode"] == "B1"
    finally:
        state.close()


@pytest.mark.parametrize("unresolved", [".", "..", "../external"])
def test_relocation_never_infers_another_library_root(tmp_path, unresolved) -> None:
    root = tmp_path / "library"
    source = root / "nested" / "paper.pdf"
    assert resolved_file_directory(root, source, Path(unresolved)) == source.parent


@pytest.mark.parametrize("scope", ["default", "global", "library"])
@pytest.mark.parametrize("mode", ["identifier", "bibcode", "all"])
@pytest.mark.parametrize("collision", [False, True])
def test_ads_refresh_returns_identified_file_to_root(
    monkeypatch, tmp_path, scope, mode, collision
) -> None:
    root = tmp_path / "library"
    unresolved_name = "unresolved" if scope == "default" else "pending-review"
    source = root / unresolved_name / "nested" / "Paper 한글.pdf"
    source.parent.mkdir(parents=True)
    source_content = write_pdf(source, doi="10.1234/relocation")
    relative_path = source.relative_to(root).as_posix()
    identify_settings = IdentifySettings(output_unresolved=unresolved_name)
    rename_settings = RenameSettings(template="{title}-{bibcode}")
    if scope == "global":
        Config.save_identify_settings(identify_settings)
        Config.save_rename_settings(rename_settings)
    state = open_registry_state(root)
    try:
        if scope == "library":
            state.set_identify_settings(identify_settings)
            state.set_rename_settings(rename_settings)
        state.upsert_file(
            relative_path,
            {
                "current_filename": relative_path,
                "sha256": sha256_file(source),
                "bibcode": "B1" if mode != "identifier" else "",
                "status": "resolved" if mode == "all" else "unresolved",
            },
        )
        location_id = state.registry.find_file_by_relative_path(root, relative_path)[
            "location_id"
        ]
    finally:
        state.close()
    monkeypatch.setattr("adsfolio.pdf.cli._get_client", lambda ctx: object())
    monkeypatch.setattr(
        PdfIdentifier,
        "query_docs_for_bibcodes",
        lambda self, requested, **kwargs: {
            bibcode: {
                "bibcode": bibcode,
                "doi": ["10.1234/relocation"],
                "title": ["Identified"],
                "author": ["Smith, Jane"],
                "year": "2024",
            }
            for bibcode in requested
        },
    )
    args = ["pdf", "refresh-ads", str(root)]
    if mode == "all":
        args.append("--all")
    else:
        args.extend([f"--{mode}", "B1", "--relative-path", relative_path])
    destination_name = (
        "Identified-B1.pdf"
        if scope in {"global", "library"}
        else "SmithJ_B1-Identified.pdf"
    )
    destination = root / destination_name
    if collision:
        destination.write_bytes(b"existing")
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    if collision:
        assert source.read_bytes() == source_content
        assert destination.read_bytes() == b"existing"
    else:
        assert not source.exists()
        assert destination.read_bytes() == source_content
    state = open_registry_state(root)
    try:
        record = state.registry.find_location_by_id(location_id)
        assert record["current_path"] == (
            relative_path if collision else destination.name
        )
        assert record["status"] == "resolved"
        assert record["bibcode"] == "B1"
        assert record["metadata"]["title"] == ["Identified"]
    finally:
        state.close()
    assert (
        "destination exists" if collision else "Renamed resolved PDF"
    ) in result.output


def test_ads_refresh_global_scope_overrides_saved_library_rename(
    monkeypatch, tmp_path
) -> None:
    root = tmp_path / "library"
    source = root / "unresolved" / "paper.pdf"
    source.parent.mkdir(parents=True)
    source_content = write_pdf(source, doi="10.1234/relocation")
    relative_path = source.relative_to(root).as_posix()
    Config.save_rename_settings(RenameSettings(template="{year}-{bibcode}"))
    state = open_registry_state(root)
    try:
        state.set_rename_settings(RenameSettings(template="{title}"))
        state.upsert_file(
            relative_path,
            {
                "current_filename": relative_path,
                "sha256": sha256_file(source),
                "status": "unresolved",
            },
        )
    finally:
        state.close()
    monkeypatch.setattr("adsfolio.pdf.cli._get_client", lambda ctx: object())
    monkeypatch.setattr(
        PdfIdentifier,
        "query_docs_for_bibcodes",
        lambda self, requested, **kwargs: {
            bibcode: {
                "bibcode": bibcode,
                "doi": ["10.1234/relocation"],
                "title": ["Global title"],
                "author": ["Smith, Jane"],
                "year": "2024",
            }
            for bibcode in requested
        },
    )

    result = CliRunner().invoke(
        main,
        [
            "pdf",
            "refresh-ads",
            str(root),
            "--bibcode",
            "B1",
            "--relative-path",
            relative_path,
            "--rename-scope",
            "global",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (root / "2024-B1.pdf").read_bytes() == source_content
    assert not source.exists()
    assert not (root / "Global_title.pdf").exists()
    state = open_registry_state(root)
    try:
        record = state.get_file("2024-B1.pdf")
        assert record is not None
        assert record["status"] == "resolved"
        assert record["bibcode"] == "B1"
    finally:
        state.close()


def test_ads_refresh_renames_existing_library_file_in_place(
    monkeypatch, tmp_path
) -> None:
    root = tmp_path / "library"
    source = root / "papers" / "paper.pdf"
    source.parent.mkdir(parents=True)
    source_content = write_pdf(source, doi="10.1234/relocation")
    relative_path = source.relative_to(root).as_posix()
    Config.save_rename_settings(RenameSettings(template="{bibcode}-{title}"))
    state = open_registry_state(root)
    try:
        state.upsert_file(
            relative_path,
            {
                "current_filename": relative_path,
                "sha256": sha256_file(source),
                "bibcode": "B1",
                "status": "resolved",
            },
        )
    finally:
        state.close()
    monkeypatch.setattr("adsfolio.pdf.cli._get_client", lambda ctx: object())
    monkeypatch.setattr(
        PdfIdentifier,
        "query_docs_for_bibcodes",
        lambda self, requested, **kwargs: {
            bibcode: {
                "bibcode": bibcode,
                "doi": ["10.1234/relocation"],
                "title": ["Library title"],
                "author": ["Smith, Jane"],
                "year": "2024",
            }
            for bibcode in requested
        },
    )

    result = CliRunner().invoke(
        main,
        [
            "pdf",
            "refresh-ads",
            str(root),
            "--bibcode",
            "B1",
            "--relative-path",
            relative_path,
        ],
    )

    assert result.exit_code == 0, result.output
    target = root / "papers" / "B1-Library_title.pdf"
    assert target.read_bytes() == source_content
    assert not source.exists()
    assert not (root / "B1-Library_title.pdf").exists()
    state = open_registry_state(root)
    try:
        record = state.get_file("papers/B1-Library_title.pdf")
        assert record is not None
        assert record["status"] == "resolved"
    finally:
        state.close()


def test_ads_refresh_uses_locked_title_for_renamed_filename(
    monkeypatch, tmp_path
) -> None:
    root = tmp_path / "library"
    source = root / "unresolved" / "paper.pdf"
    source.parent.mkdir(parents=True)
    source_content = write_pdf(source, doi="10.1234/relocation")
    relative_path = source.relative_to(root).as_posix()
    locked_title = ["Local locked title"]
    Config.save_rename_settings(RenameSettings(template="{title}-{bibcode}"))
    state = open_registry_state(root)
    try:
        state.upsert_file(
            relative_path,
            {
                "current_filename": relative_path,
                "sha256": sha256_file(source),
                "title": locked_title,
                LOCKED_METADATA_FIELDS_KEY: ["title"],
                "status": "unresolved",
            },
        )
    finally:
        state.close()
    monkeypatch.setattr("adsfolio.pdf.cli._get_client", lambda ctx: object())
    monkeypatch.setattr(
        PdfIdentifier,
        "query_docs_for_bibcodes",
        lambda self, requested, **kwargs: {
            bibcode: {
                "bibcode": bibcode,
                "doi": ["10.1234/relocation"],
                "title": ["ADS replacement title"],
                "author": ["Smith, Jane"],
                "year": "2024",
            }
            for bibcode in requested
        },
    )

    result = CliRunner().invoke(
        main,
        [
            "pdf",
            "refresh-ads",
            str(root),
            "--bibcode",
            "B1",
            "--relative-path",
            relative_path,
        ],
    )

    assert result.exit_code == 0, result.output
    target = root / "Local_locked_title-B1.pdf"
    assert target.read_bytes() == source_content
    assert not source.exists()
    state = open_registry_state(root)
    try:
        record = state.get_file(target.name)
        assert record is not None
        assert record["title"] == locked_title
        catalog_record = state.registry.find_file_by_relative_path(root, target.name)
        assert catalog_record is not None
        assert catalog_record["metadata"]["title"] == locked_title
        assert catalog_record["metadata"][LOCKED_METADATA_FIELDS_KEY] == ["title"]
        assert record["status"] == "resolved"
    finally:
        state.close()


def test_ads_refresh_invalid_template_keeps_refreshed_metadata_and_source(
    monkeypatch, tmp_path
) -> None:
    root = tmp_path / "library"
    source = root / "unresolved" / "paper.pdf"
    source.parent.mkdir(parents=True)
    source_content = write_pdf(source, doi="10.1234/relocation")
    relative_path = source.relative_to(root).as_posix()
    Config.save_rename_settings(RenameSettings(template="{unknown_field}"))
    state = open_registry_state(root)
    try:
        state.upsert_file(
            relative_path,
            {
                "current_filename": relative_path,
                "sha256": sha256_file(source),
                "status": "unresolved",
            },
        )
    finally:
        state.close()
    monkeypatch.setattr("adsfolio.pdf.cli._get_client", lambda ctx: object())
    monkeypatch.setattr(
        PdfIdentifier,
        "query_docs_for_bibcodes",
        lambda self, requested, **kwargs: {
            bibcode: {
                "bibcode": bibcode,
                "doi": ["10.1234/relocation"],
                "title": ["Refreshed title"],
                "author": ["Smith, Jane"],
                "year": "2024",
            }
            for bibcode in requested
        },
    )

    result = CliRunner().invoke(
        main,
        [
            "pdf",
            "refresh-ads",
            str(root),
            "--bibcode",
            "B1",
            "--relative-path",
            relative_path,
        ],
    )

    assert result.exit_code == 0, result.output
    assert "ADS metadata updated; could not rename" in result.output
    assert "Unknown template key" in result.output
    assert source.read_bytes() == source_content
    state = open_registry_state(root)
    try:
        record = state.get_file(relative_path)
        assert record is not None
        assert record["current_filename"] == relative_path
        assert record["status"] == "resolved"
        assert record["bibcode"] == "B1"
        assert record["title"] == ["Refreshed title"]
    finally:
        state.close()


def test_ads_refresh_missing_pdf_refuses_update_and_preserves_catalog_path(
    monkeypatch, tmp_path
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    relative_path = "unresolved/missing.pdf"
    Config.save_rename_settings(RenameSettings(template="{bibcode}"))
    state = open_registry_state(root)
    try:
        state.upsert_file(
            relative_path,
            {
                "current_filename": relative_path,
                "sha256": "missing-sha",
                "status": "unresolved",
            },
        )
    finally:
        state.close()
    monkeypatch.setattr("adsfolio.pdf.cli._get_client", lambda ctx: object())
    monkeypatch.setattr(
        PdfIdentifier,
        "query_docs_for_bibcodes",
        lambda self, requested, **kwargs: {
            bibcode: {
                "bibcode": bibcode,
                "doi": ["10.1234/relocation"],
                "title": ["Refreshed missing title"],
                "author": ["Smith, Jane"],
                "year": "2024",
            }
            for bibcode in requested
        },
    )

    result = CliRunner().invoke(
        main,
        [
            "pdf",
            "refresh-ads",
            str(root),
            "--bibcode",
            "B1",
            "--relative-path",
            relative_path,
        ],
    )

    assert result.exit_code != 0
    assert "Metadata update refused" in result.output
    state = open_registry_state(root)
    try:
        record = state.get_file(relative_path)
        assert record is not None
        assert record["current_filename"] == relative_path
        assert record["status"] == "unresolved"
        assert not record.get("bibcode")
        assert not record.get("title")
    finally:
        state.close()


def test_ads_refresh_missing_document_keeps_filename_and_file(
    monkeypatch, tmp_path
) -> None:
    root = tmp_path / "library"
    source = root / "unresolved" / "paper.pdf"
    source.parent.mkdir(parents=True)
    source_content = write_pdf(source, doi="10.1234/relocation")
    relative_path = source.relative_to(root).as_posix()
    state = open_registry_state(root)
    try:
        state.upsert_file(
            relative_path,
            {
                "current_filename": relative_path,
                "sha256": sha256_file(source),
                "bibcode": "B1",
                "status": "resolved",
            },
        )
    finally:
        state.close()
    monkeypatch.setattr("adsfolio.pdf.cli._get_client", lambda ctx: object())
    monkeypatch.setattr(
        PdfIdentifier,
        "query_docs_for_bibcodes",
        lambda self, requested, **kwargs: {bibcode: None for bibcode in requested},
    )

    result = CliRunner().invoke(
        main,
        ["pdf", "refresh-ads", str(root), "--bibcode", "B1"],
    )

    assert result.exit_code == 0, result.output
    assert source.read_bytes() == source_content
    assert not (root / "B1.pdf").exists()
    state = open_registry_state(root)
    try:
        record = state.get_file(relative_path)
        assert record is not None
        assert record["current_filename"] == relative_path
        assert record["status"] == "resolved"
        assert record["bibcode"] == "B1"
    finally:
        state.close()


def test_ads_refresh_metrics_error_leaves_successfully_moved_pdf(
    monkeypatch, tmp_path
) -> None:
    root = tmp_path / "library"
    source = root / "unresolved" / "paper.pdf"
    source.parent.mkdir(parents=True)
    source_content = write_pdf(source, doi="10.1234/relocation")
    relative_path = source.relative_to(root).as_posix()
    Config.save_rename_settings(RenameSettings(template="{bibcode}"))
    state = open_registry_state(root)
    try:
        state.upsert_file(
            relative_path,
            {
                "current_filename": relative_path,
                "sha256": sha256_file(source),
                "bibcode": "B1",
                "status": "resolved",
            },
        )
    finally:
        state.close()

    class FailingMetrics:
        def citations(self, bibcodes) -> None:
            raise ADSError("citation metrics unavailable")

    client = type("Client", (), {"metrics": FailingMetrics()})()
    monkeypatch.setattr("adsfolio.pdf.cli._get_client", lambda ctx: client)
    monkeypatch.setattr(
        PdfIdentifier,
        "query_docs_for_bibcodes",
        lambda self, requested, **kwargs: {
            bibcode: {
                "bibcode": bibcode,
                "doi": ["10.1234/relocation"],
                "title": ["Refreshed title"],
                "author": ["Smith, Jane"],
                "year": "2024",
            }
            for bibcode in requested
        },
    )

    result = CliRunner().invoke(
        main,
        [
            "pdf",
            "refresh-ads",
            str(root),
            "--bibcode",
            "B1",
            "--relative-path",
            relative_path,
            "--citation-metrics",
        ],
    )

    assert result.exit_code != 0
    assert isinstance(result.exception, ADSError)
    assert "Renamed resolved PDF" in result.output
    target = root / "B1.pdf"
    assert target.read_bytes() == source_content
    assert not source.exists()
    state = open_registry_state(root)
    try:
        assert state.get_file(relative_path) is None
        record = state.get_file(target.name)
        assert record is not None
        assert record["status"] == "resolved"
        assert record["bibcode"] == "B1"
        assert record["title"] == ["Refreshed title"]
    finally:
        state.close()


def test_ads_refresh_lowercase_bibcode_batch_moves_original_tracked_file(
    monkeypatch, tmp_path
) -> None:
    root = tmp_path / "library"
    source = root / "unresolved" / "nested" / "paper.pdf"
    source.parent.mkdir(parents=True)
    source_content = write_pdf(source, doi="10.1234/relocation")
    relative_path = source.relative_to(root).as_posix()
    Config.save_rename_settings(RenameSettings(template="{bibcode}"))
    state = open_registry_state(root)
    try:
        state.upsert_file(
            relative_path,
            {
                "current_filename": relative_path,
                "sha256": sha256_file(source),
                "bibcode": "B1",
                "status": "resolved",
            },
        )
    finally:
        state.close()

    requested_values = []

    def query_docs(self, requested, **kwargs):
        requested_values.extend(requested)
        return {
            requested[0]: {
                "bibcode": "B1",
                "doi": ["10.1234/relocation"],
                "title": ["Canonical title"],
                "author": ["Smith, Jane"],
                "year": "2024",
            }
        }

    monkeypatch.setattr("adsfolio.pdf.cli._get_client", lambda ctx: object())
    monkeypatch.setattr(PdfIdentifier, "query_docs_for_bibcodes", query_docs)

    result = CliRunner().invoke(
        main,
        ["pdf", "refresh-ads", str(root), "--bibcode", "b1"],
    )

    assert result.exit_code == 0, result.output
    assert requested_values == ["b1"]
    target = root / "B1.pdf"
    assert target.read_bytes() == source_content
    assert not source.exists()
    state = open_registry_state(root)
    try:
        assert state.get_file(relative_path) is None
        record = state.get_file(target.name)
        assert record is not None
        assert record["status"] == "resolved"
        assert record["bibcode"] == "B1"
        assert record["title"] == ["Canonical title"]
    finally:
        state.close()


def test_ads_refresh_custom_author_export_error_skips_rename_with_metadata(
    monkeypatch, tmp_path
) -> None:
    root = tmp_path / "library"
    source = root / "unresolved" / "paper.pdf"
    source.parent.mkdir(parents=True)
    source_content = write_pdf(source, doi="10.1234/relocation")
    relative_path = source.relative_to(root).as_posix()
    Config.save_rename_settings(
        RenameSettings(
            template="{authors}-{bibcode}",
            author_format="ads_custom:%3.1m",
        )
    )
    state = open_registry_state(root)
    try:
        state.upsert_file(
            relative_path,
            {
                "current_filename": relative_path,
                "sha256": sha256_file(source),
                "status": "unresolved",
            },
        )
    finally:
        state.close()

    class FailingExport:
        def custom(self, bibcodes, *, format_string) -> None:
            raise ADSError("author export unavailable")

    client = type("Client", (), {"export": FailingExport()})()
    monkeypatch.setattr("adsfolio.pdf.cli._get_client", lambda ctx: client)
    monkeypatch.setattr(
        PdfIdentifier,
        "query_docs_for_bibcodes",
        lambda self, requested, **kwargs: {
            bibcode: {
                "bibcode": bibcode,
                "doi": ["10.1234/relocation"],
                "title": ["Refreshed custom title"],
                "author": ["Smith, Jane"],
                "year": "2024",
            }
            for bibcode in requested
        },
    )

    result = CliRunner().invoke(
        main,
        [
            "pdf",
            "refresh-ads",
            str(root),
            "--bibcode",
            "B1",
            "--relative-path",
            relative_path,
        ],
    )

    assert result.exit_code == 0, result.output
    assert "ADS metadata updated; could not rename" in result.output
    assert "author export unavailable" in result.output
    assert source.read_bytes() == source_content
    assert not (root / "B1.pdf").exists()
    state = open_registry_state(root)
    try:
        record = state.get_file(relative_path)
        assert record is not None
        assert record["current_filename"] == relative_path
        assert record["status"] == "resolved"
        assert record["bibcode"] == "B1"
        assert record["title"] == ["Refreshed custom title"]
    finally:
        state.close()


@pytest.mark.parametrize("scope", ["global", "library"])
@pytest.mark.parametrize("apply", [False, True])
def test_rename_uses_saved_unresolved_destination(
    monkeypatch, tmp_path, scope, apply
) -> None:
    root = tmp_path / "library"
    source = root / "pending-review" / "nested" / "paper.pdf"
    source.parent.mkdir(parents=True)
    write_pdf(source, doi="10.1234/relocation")
    settings = IdentifySettings(output_unresolved="pending-review")
    if scope == "global":
        Config.save_identify_settings(settings)
    state = open_registry_state(root)
    try:
        state.registry.upsert_ads_documents(
            [{"bibcode": "B1", "doi": ["10.1234/relocation"]}], source="test"
        )
        if scope == "library":
            state.set_identify_settings(settings)
        relative_path = source.relative_to(root).as_posix()
        state.upsert_file(
            relative_path,
            {
                "bibcode": "B1",
                "sha256": sha256_file(source),
                "current_filename": relative_path,
            },
        )
    finally:
        state.close()
    monkeypatch.setattr("adsfolio.pdf.cli._get_client", lambda ctx: object())
    args = ["pdf", "rename", str(root), "--scheme", "{bibcode}"]
    if apply:
        args.append("--apply")
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    target = root / "B1.pdf"
    assert target.exists() == apply
    assert source.exists() != apply
    state = open_registry_state(root)
    try:
        assert (
            state.get_file(target.name if apply else relative_path)["bibcode"] == "B1"
        )
    finally:
        state.close()
