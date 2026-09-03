import json
from pathlib import Path

import pytest

from adsapi.config import Config
from adsapi.pdf.catalog import CATALOG_FILENAME, CATALOG_SCHEMA_VERSION
from adsapi.pdf.identify_settings import IdentifySettings
from adsapi.pdf.registry import (
    PdfRegistry,
    merge_ads_metadata,
    migrate_sidecar,
    open_registry_state,
)
from adsapi.pdf.rename_settings import RenameSettings


@pytest.fixture(autouse=True)
def _use_test_catalog(monkeypatch, tmp_path):
    """Keep each registry test's central catalog isolated."""
    monkeypatch.setattr(
        Config,
        "get_config_dir",
        classmethod(lambda cls: tmp_path / "config"),
    )


def test_registry_initializes_schema(tmp_path: Path) -> None:
    registry = PdfRegistry(tmp_path)

    assert registry.path == tmp_path / "config" / CATALOG_FILENAME
    assert registry.schema_version == CATALOG_SCHEMA_VERSION
    assert registry.count_files() == 0


def test_registry_state_save_uses_only_sqlite_for_fresh_registry(
    tmp_path: Path,
) -> None:
    state = open_registry_state(tmp_path)

    state.save()

    assert (tmp_path / "config" / CATALOG_FILENAME).exists()
    assert not (tmp_path / ".adsapi-pdfs.json").exists()


def test_registry_upserts_and_finds_file_records(tmp_path: Path) -> None:
    registry = PdfRegistry(tmp_path)
    file_id = registry.upsert_file(
        root=tmp_path,
        relative_path="paper.pdf",
        current_path="paper.pdf",
        sha256="a" * 64,
        size_bytes=123,
        mtime_ns=456,
        status="new",
    )

    by_sha = registry.find_file_by_sha256("a" * 64)
    by_path = registry.find_file_by_relative_path(tmp_path, "paper.pdf")

    assert file_id > 0
    assert by_sha is not None
    assert by_sha["relative_path"] == "paper.pdf"
    assert by_path is not None
    assert by_path["sha256"] == "a" * 64
    assert registry.find_file_by_id(file_id) is not None


def test_registry_discards_legacy_current_filename_metadata(tmp_path: Path) -> None:
    """The canonical registry path is not duplicated in file metadata."""
    registry = PdfRegistry(tmp_path)
    registry.upsert_file(
        root=tmp_path,
        relative_path="original.pdf",
        current_path="renamed.pdf",
        sha256="a" * 64,
        size_bytes=1,
        mtime_ns=1,
        status="resolved",
        metadata={"current_filename": "renamed.pdf", "bibcode": "B1"},
    )

    record = registry.find_file_by_relative_path(tmp_path, "renamed.pdf")

    assert record is not None
    assert record["current_path"] == "renamed.pdf"
    assert "current_filename" not in record["metadata"]
    assert "current_filename" not in registry.export_payload()["files"]["renamed.pdf"]


def test_registry_reconciles_file_facts_without_removing_missing_records(
    tmp_path: Path,
) -> None:
    """Reconciliation updates filesystem facts and reports stale rows safely."""
    tracked = tmp_path / "tracked.pdf"
    tracked.write_bytes(b"PDF")
    registry = PdfRegistry(tmp_path)
    registry.upsert_file(
        root=tmp_path,
        relative_path="tracked.pdf",
        current_path="tracked.pdf",
        sha256="a" * 64,
        size_bytes=0,
        mtime_ns=0,
        status="resolved",
        metadata={"bibcode": "B1"},
    )
    registry.upsert_file(
        root=tmp_path,
        relative_path="missing.pdf",
        current_path="missing.pdf",
        sha256="b" * 64,
        size_bytes=0,
        mtime_ns=0,
        status="resolved",
        metadata={"bibcode": "B2"},
    )

    result = registry.reconcile_file_facts(tmp_path)
    refreshed = registry.find_file_by_relative_path(tmp_path, "tracked.pdf")

    assert result.scanned == 2
    assert result.updated == 1
    assert result.missing == 1
    assert refreshed is not None
    assert refreshed["size_bytes"] == tracked.stat().st_size
    assert registry.find_file_by_relative_path(tmp_path, "missing.pdf") is not None


def test_registry_reconcile_hashes_changed_content(tmp_path: Path) -> None:
    tracked = tmp_path / "tracked.pdf"
    tracked.write_bytes(b"first")
    registry = PdfRegistry(tmp_path)
    registry.upsert_file(
        root=tmp_path,
        relative_path="tracked.pdf",
        current_path="tracked.pdf",
        sha256="old-digest",
        size_bytes=0,
        mtime_ns=0,
        status="unresolved",
    )

    tracked.write_bytes(b"second")
    result = registry.reconcile_file_facts(tmp_path)
    record = registry.find_file_by_relative_path(tmp_path, "tracked.pdf")

    assert result.updated == 1
    assert record is not None
    assert record["sha256"] != "old-digest"
    assert len(registry.catalog.content_versions(record["attachment_id"])) == 2


def test_registry_caches_ads_documents(tmp_path: Path) -> None:
    registry = PdfRegistry(tmp_path)
    doc = {
        "bibcode": "2020ApJ...900..100S",
        "doi": ["10.1234/example"],
        "title": ["Example Paper"],
        "author": ["Smith, Jane"],
        "year": "2020",
        "bibstem": ["ApJ"],
        "volume": "900",
        "page": ["100"],
        "pub": "Astrophysical Journal",
    }

    registry.upsert_ads_document(doc, source="library:LIB123")

    by_bibcode = registry.find_ads_document_by_bibcode("2020ApJ...900..100S")
    by_doi = registry.find_ads_documents_by_doi("10.1234/example")

    assert by_bibcode is not None
    assert by_bibcode["title"] == ["Example Paper"]
    assert len(by_doi) == 1
    assert by_doi[0]["bibcode"] == "2020ApJ...900..100S"


def test_registry_caches_ads_document_metrics(tmp_path: Path) -> None:
    registry = PdfRegistry(tmp_path)
    registry.upsert_ads_document({"bibcode": "B1"}, source="test")

    assert registry.set_ads_document_metrics(
        "B1",
        {
            "all_citations": 8,
            "refereed_citations": 5,
            "non_refereed_citations": 3,
        },
    )
    document = registry.find_ads_document_by_bibcode("B1")

    assert document is not None
    assert document["metrics"]["refereed_citations"] == 5


def test_registry_replaces_metadata_without_changing_file_identity(
    tmp_path: Path,
) -> None:
    registry = PdfRegistry(tmp_path)
    registry.upsert_file(
        root=tmp_path,
        relative_path="original.pdf",
        current_path="renamed.pdf",
        sha256="a" * 64,
        size_bytes=123,
        mtime_ns=456,
        status="resolved",
        metadata={
            "bibcode": "OLD",
            "doi": ["10.0/old"],
            "title": ["Old title"],
        },
    )

    assert registry.replace_file_metadata(
        tmp_path,
        "renamed.pdf",
        {"bibcode": "NEW", "title": ["New title"]},
    )

    record = registry.find_file_by_relative_path(tmp_path, "renamed.pdf")
    assert record is not None
    assert record["current_path"] == "renamed.pdf"
    assert record["sha256"] == "a" * 64
    assert record["size_bytes"] == 123
    assert record["metadata"]["bibcode"] == "NEW"
    assert record["metadata"]["title"] == ["New title"]

    assert registry.replace_file_metadata(
        tmp_path,
        "renamed.pdf",
        {"bibcode": "", "doi": [], "title": []},
    )
    cleared = registry.find_file_by_relative_path(tmp_path, "renamed.pdf")
    assert cleared is not None
    assert cleared["metadata"]["bibcode"] == ""
    assert cleared["metadata"]["doi"] == []
    assert cleared["metadata"]["title"] == []

    assert registry.replace_file_metadata(
        tmp_path,
        "renamed.pdf",
        {"status": "unresolved"},
    )
    status_changed = registry.find_file_by_relative_path(tmp_path, "renamed.pdf")
    assert status_changed is not None
    assert status_changed["status"] == "unresolved"


def test_registry_sorts_resolution_marker_with_attention_first(tmp_path: Path) -> None:
    """Marker sorting puts records needing review before resolved records."""
    registry = PdfRegistry(tmp_path)
    for relative_path, status, metadata in (
        ("resolved.pdf", "resolved", {"bibcode": "RESOLVED"}),
        ("missing-bibcode.pdf", "resolved", {}),
        ("unresolved.pdf", "unresolved", {"bibcode": "UNRESOLVED"}),
    ):
        registry.upsert_file(
            root=tmp_path,
            relative_path=relative_path,
            current_path=relative_path,
            sha256=relative_path,
            size_bytes=1,
            mtime_ns=1,
            status=status,
            metadata=metadata,
        )

    records = registry.query_files(order_by="status")

    assert [record["relative_path"] for record in records] == [
        "missing-bibcode.pdf",
        "unresolved.pdf",
        "resolved.pdf",
    ]
    registry.close()


def test_merge_ads_metadata_preserves_locked_fields() -> None:
    merged = merge_ads_metadata(
        {
            "title": ["My corrected title"],
            "doi": ["10.0/old"],
            "_adsapi_locked_fields": ["title"],
        },
        {
            "bibcode": "2022StGal...5....4B",
            "title": ["ADS title"],
            "doi": ["10.0/new"],
            "abstract": "ADS abstract",
        },
    )

    assert merged["title"] == ["My corrected title"]
    assert merged["doi"] == ["10.0/new"]
    assert merged["abstract"] == "ADS abstract"
    assert merged["_adsapi_locked_fields"] == ["title"]


def test_registry_status_rename_iteration_and_batch_ads_lookup(tmp_path: Path) -> None:
    registry = PdfRegistry(tmp_path)
    registry.upsert_file(
        root=tmp_path,
        relative_path="paper.pdf",
        current_path="paper.pdf",
        sha256="a" * 64,
        size_bytes=1,
        mtime_ns=2,
        status="resolved",
        metadata={"bibcode": "B1", "title": ["Paper"]},
    )

    assert registry.update_file_status(tmp_path, "paper.pdf", "unresolved")
    assert registry.record_rename(tmp_path, "paper.pdf", "renamed.pdf")
    assert list(registry.iter_files(status="unresolved"))[0]["current_path"] == (
        "renamed.pdf"
    )
    registry.upsert_ads_document(
        {"bibcode": "B1", "doi": ["10/example"]}, source="fallback"
    )
    assert registry.find_ads_documents_by_bibcodes(["B1"])["b1"]["bibcode"] == "B1"
    assert registry.find_ads_documents_by_dois(["10/example"])["10/example"]


def test_registry_state_upsert_preserves_locked_metadata_fields(tmp_path: Path) -> None:
    state = open_registry_state(tmp_path)
    state.upsert_file(
        "paper.pdf",
        {
            "current_filename": "paper.pdf",
            "sha256": "a" * 64,
            "title": ["Manual title"],
            "_adsapi_locked_fields": ["title"],
        },
    )

    state.upsert_file(
        "paper.pdf",
        {
            "current_filename": "paper.pdf",
            "sha256": "a" * 64,
            "title": ["Identify title"],
            "doi": ["10.0/new"],
        },
    )

    record = state.get_file("paper.pdf")
    assert record is not None
    assert record["title"] == ["Manual title"]
    assert record["doi"] == ["10.0/new"]


def test_registry_migrates_sidecar_and_exports_lossless_metadata(
    tmp_path: Path,
) -> None:
    sidecar_path = tmp_path / ".adsapi-pdfs.json"
    payload = {
        "version": 2,
        "identify_profile": {"orcid": "0000", "end_year": 2026},
        "files": {
            "original.pdf": {
                "current_filename": "renamed.pdf",
                "sha256": "a" * 64,
                "bibcode": "B1",
                "title": ["Paper"],
            }
        },
    }
    sidecar_path.write_text(json.dumps(payload), encoding="utf-8")

    state = open_registry_state(tmp_path)
    assert sidecar_path.exists()
    assert not (tmp_path / ".adsapi-pdfs.json.migrated").exists()
    assert state.get_file("renamed.pdf")["bibcode"] == "B1"
    assert state.identify_profile is not None
    assert state.identify_profile["orcid"] == "0000"
    assert state.identify_profile["end_year"] == 2026
    exported = state.registry.export_payload()
    assert exported["files"]["renamed.pdf"]["current_path"] == "renamed.pdf"
    assert exported["files"]["renamed.pdf"]["bibcode"] == "B1"


def test_migrate_sidecar_is_lossless_for_existing_registry(tmp_path: Path) -> None:
    sidecar_path = tmp_path / ".adsapi-pdfs.json"
    sidecar_path.write_text(
        json.dumps({"files": {"paper.pdf": {"bibcode": "B1"}}}),
        encoding="utf-8",
    )
    registry = PdfRegistry(tmp_path)
    assert migrate_sidecar(sidecar_path, registry) == 0
    assert sidecar_path.exists()
    assert registry.find_file_by_relative_path(tmp_path, "paper.pdf") is not None


def test_open_registry_migrates_sidecar_when_registry_already_exists(
    tmp_path: Path,
) -> None:
    registry = PdfRegistry(tmp_path)
    registry.close()
    sidecar_path = tmp_path / ".adsapi-pdfs.json"
    sidecar_path.write_text(
        json.dumps({"files": {"paper.pdf": {"bibcode": "B1"}}}),
        encoding="utf-8",
    )

    state = open_registry_state(tmp_path)

    assert state.get_file("paper.pdf")["bibcode"] == "B1"
    assert sidecar_path.exists()
    assert not (tmp_path / ".adsapi-pdfs.json.migrated").exists()


def test_open_registry_preserves_existing_migrated_sidecar(tmp_path: Path) -> None:
    migrated_path = tmp_path / ".adsapi-pdfs.json.migrated"
    migrated_path.write_text("previous migration", encoding="utf-8")
    sidecar_path = tmp_path / ".adsapi-pdfs.json"
    current_payload = json.dumps(
        {"files": {"paper.pdf": {"bibcode": "current migration"}}}
    )
    sidecar_path.write_text(current_payload, encoding="utf-8")

    state = open_registry_state(tmp_path)

    assert state.get_file("paper.pdf")["bibcode"] == "current migration"
    assert sidecar_path.read_text(encoding="utf-8") == current_payload
    assert migrated_path.read_text(encoding="utf-8") == "previous migration"


def test_state_single_record_lookups_do_not_scan_all_files(tmp_path: Path) -> None:
    state = open_registry_state(tmp_path)
    state.upsert_file(
        "renamed.pdf",
        {"current_filename": "renamed.pdf", "sha256": "a" * 64, "bibcode": "B1"},
    )

    def _fail_scan(*args, **kwargs):
        raise AssertionError("single-record lookup must not iterate all files")

    state.registry.iter_files = _fail_scan

    found_path, by_sha = state.find_by_sha256("a" * 64)
    assert found_path == "renamed.pdf"
    assert by_sha["bibcode"] == "B1"
    assert by_sha["current_filename"] == "renamed.pdf"
    assert by_sha["status"] == "resolved"

    by_path = state.get_file("renamed.pdf")
    assert by_path["bibcode"] == "B1"

    original, by_current = state.find_by_current_filename("renamed.pdf")
    assert original == "renamed.pdf"
    assert by_current["bibcode"] == "B1"
    assert state.find_by_current_filename("missing.pdf") is None

    upserted = state.upsert_file(
        "renamed.pdf",
        {"current_filename": "renamed.pdf", "sha256": "a" * 64, "bibcode": "B2"},
    )
    assert upserted["bibcode"] == "B2"


def test_state_upsert_returns_requested_location_for_duplicate_sha(
    tmp_path: Path,
) -> None:
    state = open_registry_state(tmp_path)
    state.upsert_file(
        "archived-copy.pdf",
        {"current_filename": "archived-copy.pdf", "sha256": "a" * 64},
    )

    upserted = state.upsert_file(
        "current.pdf",
        {"current_filename": "current.pdf", "sha256": "a" * 64},
    )

    assert upserted["current_filename"] == "current.pdf"


def test_registry_state_identify_settings_are_root_specific(tmp_path: Path) -> None:
    state = open_registry_state(tmp_path)
    state.registry.catalog.set_meta(
        "identify_settings",
        json.dumps(IdentifySettings(recursive=True).to_mapping()),
    )

    assert state.identify_settings is None
    expected = IdentifySettings(output_unresolved="pending")
    assert state.set_identify_settings(expected)
    assert state.identify_settings == expected
    assert state.set_identify_settings(None)
    assert state.identify_settings is None


def test_registry_state_does_not_treat_legacy_identity_as_identify_behavior(
    tmp_path: Path,
) -> None:
    state = open_registry_state(tmp_path)
    state.registry.set_meta(
        "identify_profile",
        json.dumps({"orcid": "0000", "retire_year": 2030}),
    )

    assert state.identify_settings is None


def _insert_query_records(registry: PdfRegistry, root: Path) -> None:
    records = [
        {
            "relative_path": "resolved.pdf",
            "sha256": "a" * 64,
            "status": "resolved",
            "metadata": {
                "bibcode": "2024ApJ...1..001A",
                "doi": "10.1234/resolved",
                "title": ["Thermal Paper"],
                "author": ["Bach, Yoonsoo"],
                "year": "2024",
                "source": "published",
                "pub": "ApJ",
            },
        },
        {
            "relative_path": "nested/unresolved.pdf",
            "sha256": "b" * 64,
            "status": "unresolved",
            "metadata": {
                "title": ["Unmatched Spectrum"],
                "author": ["Kim, Ada"],
                "year": "2023",
                "source": "arxiv",
                "pub": "A&A",
            },
        },
        {
            "relative_path": "duplicate.pdf",
            "sha256": "a" * 64,
            "status": "new",
            "metadata": {
                "title": ["Duplicate Copy"],
                "author": ["Lee, Pat"],
                "year": "2022",
            },
        },
        {
            "relative_path": "empty-digest.pdf",
            "sha256": "",
            "status": "new",
            "metadata": {"title": ["No Digest"]},
        },
    ]
    for record in records:
        registry.upsert_file(
            root=root,
            relative_path=record["relative_path"],
            current_path=record["relative_path"],
            sha256=record["sha256"],
            size_bytes=10,
            mtime_ns=20,
            status=record["status"],
            metadata=record["metadata"],
        )


def test_registry_query_files_filters_searches_and_counts(tmp_path: Path) -> None:
    registry = PdfRegistry(tmp_path)
    _insert_query_records(registry, tmp_path)

    assert len(registry.query_files(resolved=True)) == 2
    assert len(registry.query_files(resolved=False)) == 2
    assert len(registry.query_files(source="arxiv")) == 1
    assert {
        record["relative_path"]
        for record in registry.query_files(search="10.1234/resolved")
    } == {"resolved.pdf", "duplicate.pdf"}
    assert {
        record["relative_path"] for record in registry.query_files(search="thermal")
    } == {"resolved.pdf", "duplicate.pdf"}
    assert {
        record["relative_path"] for record in registry.query_files(search="Bach")
    } == {"resolved.pdf", "duplicate.pdf"}
    assert registry.query_files(search="nested/unresolved")[0]["relative_path"] == (
        "nested/unresolved.pdf"
    )
    assert len(registry.query_files(duplicates_only=True)) == 2
    assert registry.count_files_matching(duplicates_only=True) == len(
        registry.query_files(duplicates_only=True)
    )


def test_registry_sorts_derived_arxiv_identifiers(tmp_path: Path) -> None:
    registry = PdfRegistry(tmp_path)
    registry.upsert_file(
        root=tmp_path,
        relative_path="late.pdf",
        current_path="late.pdf",
        sha256="late",
        size_bytes=1,
        mtime_ns=1,
        status="resolved",
        metadata={"doi": "10.48550/arXiv.2606.12345"},
    )
    registry.upsert_file(
        root=tmp_path,
        relative_path="early.pdf",
        current_path="early.pdf",
        sha256="early",
        size_bytes=1,
        mtime_ns=1,
        status="resolved",
        metadata={"doi": "10.48550/arXiv.2501.00001"},
    )

    assert [
        record["current_path"] for record in registry.query_files(order_by="arxiv")
    ] == [
        "early.pdf",
        "late.pdf",
    ]
    registry.close()


def test_registry_sorts_location_first_seen_at_newest_first(tmp_path: Path) -> None:
    registry = PdfRegistry(tmp_path)
    registry.upsert_file(
        root=tmp_path,
        relative_path="earlier.pdf",
        current_path="earlier.pdf",
        sha256="earlier",
        size_bytes=1,
        mtime_ns=1,
        status="new",
    )
    registry.upsert_file(
        root=tmp_path,
        relative_path="later.pdf",
        current_path="later.pdf",
        sha256="later",
        size_bytes=1,
        mtime_ns=1,
        status="new",
    )
    earlier = registry.find_file_by_relative_path(tmp_path, "earlier.pdf")
    later = registry.find_file_by_relative_path(tmp_path, "later.pdf")
    assert earlier is not None
    assert later is not None
    with registry.catalog._connection:
        registry.catalog._connection.executemany(
            "UPDATE file_locations SET first_seen_at = ? WHERE id = ?",
            [
                ("2026-08-29T01:00:00+00:00", earlier["location_id"]),
                ("2026-08-30T01:00:00+00:00", later["location_id"]),
            ],
        )

    records = registry.query_files(order_by="first_seen_at", descending=True)

    assert [record["current_path"] for record in records] == [
        "later.pdf",
        "earlier.pdf",
    ]
    registry.close()


def test_registry_query_files_sorting_and_stable_paging(tmp_path: Path) -> None:
    registry = PdfRegistry(tmp_path)
    _insert_query_records(registry, tmp_path)

    ascending = registry.query_files(order_by="year")
    descending = registry.query_files(order_by="year", descending=True)
    ascending_years = [record["year"] for record in ascending]
    assert ascending_years == ["2023", "2024", "2024", None]
    assert ascending_years[-1] is None
    assert descending[0]["year"] == "2024"

    first_page = registry.query_files(limit=2, offset=0)
    second_page = registry.query_files(limit=2, offset=2)
    assert {record["location_id"] for record in first_page}.isdisjoint(
        {record["location_id"] for record in second_page}
    )
    assert len(first_page) + len(second_page) == 4


def test_registry_query_files_rejects_untrusted_order_by(tmp_path: Path) -> None:
    registry = PdfRegistry(tmp_path)

    with pytest.raises(ValueError, match="Unsortable column"):
        registry.query_files(order_by="; DROP TABLE files")


def test_registry_merges_flat_metadata_and_deletes_selected_records(
    tmp_path: Path,
) -> None:
    registry = PdfRegistry(tmp_path)
    file_id = registry.upsert_file(
        root=tmp_path,
        relative_path="paper.pdf",
        current_path="paper.pdf",
        sha256="a" * 64,
        size_bytes=1,
        mtime_ns=2,
        status="resolved",
        metadata={"bibcode": "B1"},
    )
    assert registry.replace_file_metadata(
        tmp_path,
        "paper.pdf",
        {"bibcode": "B1", "author": ["Bach, Yoonsoo"]},
    )
    record = registry.find_file_by_relative_path(tmp_path, "paper.pdf")

    assert record is not None
    assert record["metadata"]["author"] == ["Bach, Yoonsoo"]
    assert registry.delete_files_by_ids([file_id]) == 1
    assert registry.count_files() == 0


def test_registry_roundtrips_root_rename_settings(tmp_path: Path) -> None:
    state = open_registry_state(tmp_path / "library")
    expected = RenameSettings(max_authors=5, authors_when_truncated=2)
    assert state.set_rename_settings(expected)
    assert state.rename_settings == expected
    assert state.set_rename_settings(expected) is False
    assert state.set_rename_settings(None) is True
    assert state.rename_settings is None
    state.close()


def test_registry_state_rename_settings_are_root_specific(tmp_path: Path) -> None:
    state = open_registry_state(tmp_path)
    state.registry.catalog.set_meta(
        "rename_settings",
        json.dumps(RenameSettings(template="{bibcode}").to_mapping()),
    )
    assert state.rename_settings is None


def test_registry_state_rename_settings_malformed_json_returns_none(
    tmp_path: Path,
) -> None:
    state = open_registry_state(tmp_path)
    state.registry.catalog.set_meta(
        f"root:{state.directory}:rename_settings",
        "{invalid-json",
    )
    assert state.rename_settings is None
