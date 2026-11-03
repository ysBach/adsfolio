"""Upgrade a verified external PDF to an ADS record when available."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from pypdf import PdfWriter

from adsfolio.config import ADSError, Config
from adsfolio.pdf.cli import pdf
from adsfolio.pdf.doi_providers import DoiProvider
from adsfolio.pdf.files import sha256_file
from adsfolio.pdf.identifier import PdfIdentifier
from adsfolio.pdf.identify_settings import IdentifySettings
from adsfolio.pdf.registry import open_registry_state
from adsfolio.pdf.rename_settings import RenameSettings

DOI = "10.1234/verified-example"
ADS_DOC = {
    "bibcode": "2024ApJ...900..100E",
    "doi": [DOI],
    "title": ["An Example Paper"],
    "author": ["Example, A."],
    "year": "2024",
}
EXTERNAL_DOC = {key: value for key, value in ADS_DOC.items() if key != "bibcode"}
EXTERNAL_DOC["provider"] = "crossref"


class _Search:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.queries = []

    def query(self, *, q, fl, rows, start=0):
        self.queries.append(q)
        if self.fail:
            raise ADSError("ADS unavailable")
        return SimpleNamespace(num_found=1, docs=[ADS_DOC], start=start)


@pytest.fixture
def identified_pdf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    monkeypatch.setattr(
        "adsfolio.pubcache.cache_path",
        lambda: tmp_path / "config" / "publications.json",
    )
    root = tmp_path / "library"
    root.mkdir()
    path = root / "paper.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=400)
    writer.add_metadata({"/DOI": DOI, "/Title": "An Example Paper"})
    writer.write(path)
    metadata = PdfIdentifier(None).build_record(EXTERNAL_DOC, path)
    metadata["notes"] = "Keep the user's notes"
    metadata["_adsfolio_locked_fields"] = ["title"]
    state = open_registry_state(root)
    state.upsert_file(path.name, metadata)
    original = state.registry.catalog.get_file(root, path.name)
    state.close()
    return root, path, original


def test_explicit_identify_upgrades_verified_external_record_to_ads(
    identified_pdf, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, path, original = identified_pdf
    search = _Search()
    monkeypatch.setattr(
        "adsfolio.pdf.cli._get_client", lambda ctx: SimpleNamespace(search=search)
    )
    digest = sha256_file(path)

    result = CliRunner().invoke(
        pdf,
        ["identify", str(root), "--file", str(path), "--allow-ads"],
    )

    assert result.exit_code == 0, result.output
    assert search.queries
    state = open_registry_state(root)
    try:
        saved = state.registry.catalog.get_file(root, path.name)
        assert saved["attachment_id"] == original["attachment_id"]
        assert saved["status"] == "resolved"
        assert saved["bibcode"] == ADS_DOC["bibcode"]
        assert saved["metadata"]["notes"] == "Keep the user's notes"
        assert saved["metadata"]["_adsfolio_locked_fields"] == ["title"]
        assert saved["metadata"]["_adsfolio_identity"]["sha256"] == digest
    finally:
        state.close()
    assert sha256_file(path) == digest


@pytest.mark.parametrize("outcome", ["matching", "missing", "wrong"])
def test_identify_rechecks_unverified_saved_external_metadata(
    identified_pdf, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    root, path, _original = identified_pdf
    state = open_registry_state(root)
    try:
        saved = state.registry.find_file_by_relative_path(root, path.name)
        metadata = dict(saved["metadata"])
        metadata["_adsfolio_identity"] = {
            **metadata["_adsfolio_identity"],
            "status": "reviewed_repair",
            "authority": "manual_audit_manifest",
            "audit_reference": "Keep the audit evidence",
        }
        assert state.registry.replace_file_metadata(root, path.name, metadata)
        before = state.registry.find_file_by_relative_path(root, path.name)
    finally:
        state.close()
    digest = sha256_file(path)

    def lookup(doi: str):
        assert doi == DOI
        if outcome == "missing":
            return None
        if outcome == "wrong":
            return {**EXTERNAL_DOC, "doi": ["10.1234/different"]}
        return dict(EXTERNAL_DOC)

    monkeypatch.setattr("adsfolio.pdf.identifier.fetch_crossref_doi", lookup)
    result = CliRunner().invoke(
        pdf,
        [
            "identify",
            str(root),
            "--file",
            str(path),
            "--provider",
            "crossref",
            "--strategy",
            "pdf_metadata_doi",
            "--no-allow-ads",
            "--output-unresolved",
            str(root / "unresolved"),
            "--apply",
        ],
    )
    assert result.exit_code == 0, result.output
    state = open_registry_state(root)
    try:
        after = state.registry.find_file_by_relative_path(root, path.name)
        assert after["metadata"]["notes"] == "Keep the user's notes"
        assert after["metadata"]["_adsfolio_locked_fields"] == ["title"]
        if outcome == "matching":
            assert after["metadata"]["_adsfolio_identity"]["status"] == "verified"
            assert (
                after["metadata"]["_adsfolio_identity_history"][-1]["audit_reference"]
                == "Keep the audit evidence"
            )
        else:
            assert after["metadata"] == before["metadata"]
    finally:
        state.close()
    assert path.exists()
    assert sha256_file(path) == digest


def test_external_revalidation_preserves_concurrent_catalog_edits(
    identified_pdf, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, path, _original = identified_pdf
    state = open_registry_state(root)
    try:
        saved = state.registry.find_file_by_relative_path(root, path.name)
        metadata = dict(saved["metadata"])
        metadata["_adsfolio_identity"] = {
            **metadata["_adsfolio_identity"],
            "status": "reviewed_repair",
            "authority": "manual_audit_manifest",
        }
        assert state.registry.replace_file_metadata(root, path.name, metadata)
    finally:
        state.close()

    def lookup(doi: str):
        concurrent = open_registry_state(root)
        try:
            saved = concurrent.registry.find_file_by_relative_path(root, path.name)
            changed = {**saved["metadata"], "notes": "Changed during lookup"}
            assert concurrent.registry.replace_file_metadata(root, path.name, changed)
        finally:
            concurrent.close()
        return dict(EXTERNAL_DOC)

    monkeypatch.setattr("adsfolio.pdf.identifier.fetch_crossref_doi", lookup)
    result = CliRunner().invoke(
        pdf,
        [
            "identify",
            str(root),
            "--file",
            str(path),
            "--provider",
            "crossref",
            "--strategy",
            "pdf_metadata_doi",
            "--no-allow-ads",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "catalog changed" in result.output.lower()
    state = open_registry_state(root)
    try:
        saved = state.registry.find_file_by_relative_path(root, path.name)
        assert saved["metadata"]["notes"] == "Changed during lookup"
        assert saved["metadata"]["_adsfolio_identity"]["status"] == "reviewed_repair"
    finally:
        state.close()


@pytest.mark.parametrize("ads_available", [True, False])
@pytest.mark.parametrize("apply", [True, False])
def test_background_recheck_verifies_saved_external_metadata(
    identified_pdf, monkeypatch: pytest.MonkeyPatch, ads_available: bool, apply: bool
) -> None:
    from adsfolio.pdf.ads_recheck import recheck_existing

    root, path, _original = identified_pdf
    digest = sha256_file(path)
    state = open_registry_state(root)
    try:
        saved = state.registry.find_file_by_relative_path(root, path.name)
        metadata = dict(saved["metadata"])
        metadata["_adsfolio_identity"] = {
            **metadata["_adsfolio_identity"],
            "status": "reviewed_repair",
            "authority": "manual_audit_manifest",
        }
        assert state.registry.replace_file_metadata(root, path.name, metadata)
        before = state.registry.find_file_by_relative_path(root, path.name)

        class Search:
            def query(self, **kwargs):
                if not ads_available:
                    raise ADSError("ADS is unavailable")
                return SimpleNamespace(num_found=0, docs=[])

        monkeypatch.setattr(
            "adsfolio.pdf.identifier.fetch_crossref_doi", lambda doi: dict(EXTERNAL_DOC)
        )
        settings = IdentifySettings(providers=(DoiProvider.ADS, DoiProvider.CROSSREF))
        result = recheck_existing(
            state,
            SimpleNamespace(search=Search()),
            RenameSettings(),
            identify_settings=settings,
            apply=apply,
        )
        after = state.registry.find_file_by_relative_path(root, path.name)
        if apply:
            assert result.updated == 1
            assert after["metadata"]["_adsfolio_identity"]["status"] == "verified"
            assert after["metadata"]["notes"] == "Keep the user's notes"
        else:
            assert after["metadata"] == before["metadata"]
    finally:
        state.close()
    assert path.exists()
    assert sha256_file(path) == digest


@pytest.mark.parametrize("ads_enabled", [True, False])
def test_background_recheck_identifies_empty_record_from_pdf_metadata_doi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ads_enabled: bool
) -> None:
    from adsfolio.pdf.ads_recheck import recheck_existing

    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    root = tmp_path / "library"
    root.mkdir()
    path = root / "paper.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=400)
    writer.add_metadata({"/DOI": DOI, "/Title": "An Example Paper"})
    writer.write(path)
    digest = sha256_file(path)
    state = open_registry_state(root)
    try:
        state.upsert_file(
            path.name,
            {"current_filename": path.name, "sha256": digest, "status": "unresolved"},
        )

        class Search:
            def query(self, **kwargs):
                return SimpleNamespace(num_found=0, docs=[])

        monkeypatch.setattr(
            "adsfolio.pdf.identifier.fetch_crossref_doi", lambda doi: dict(EXTERNAL_DOC)
        )
        settings = IdentifySettings(
            providers=(DoiProvider.ADS, DoiProvider.CROSSREF)
            if ads_enabled
            else (DoiProvider.CROSSREF,)
        )
        result = recheck_existing(
            state,
            SimpleNamespace(search=Search()) if ads_enabled else None,
            RenameSettings(),
            identify_settings=settings,
            ads_enabled=ads_enabled,
        )
        after = state.registry.find_file_by_relative_path(root, path.name)
        assert result.updated == 1
        assert after["metadata"]["doi"] == DOI
        assert after["metadata"]["_adsfolio_identity"]["status"] == "verified"
    finally:
        state.close()
    assert path.exists()
    assert sha256_file(path) == digest


def test_background_identification_honors_external_provider_priority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from adsfolio.pdf.ads_recheck import recheck_existing
    from adsfolio.pdf.identify_settings import IdentifyStrategy

    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    root = tmp_path / "library"
    root.mkdir()
    path = root / "paper.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=400)
    writer.add_metadata({"/DOI": DOI, "/Title": "An Example Paper"})
    writer.write(path)
    calls = []

    class Search:
        def query(self, **kwargs):
            calls.append("ads")
            return SimpleNamespace(num_found=1, docs=[ADS_DOC])

    def crossref(doi):
        calls.append("crossref")
        return dict(EXTERNAL_DOC)

    monkeypatch.setattr("adsfolio.pdf.identifier.fetch_crossref_doi", crossref)
    state = open_registry_state(root)
    try:
        state.upsert_file(
            path.name, {"sha256": sha256_file(path), "status": "unresolved"}
        )
        settings = IdentifySettings(
            providers=(DoiProvider.CROSSREF, DoiProvider.ADS),
            strategies=(IdentifyStrategy.PDF_METADATA_DOI,),
        )
        result = recheck_existing(
            state,
            SimpleNamespace(search=Search()),
            RenameSettings(),
            identify_settings=settings,
        )
        after = state.registry.find_file_by_relative_path(root, path.name)
        assert result.updated == 1
        assert calls[0] == "crossref"
        assert not after["bibcode"]
        assert (
            after["metadata"]["_adsfolio_external_metadata"]["provider"] == "crossref"
        )
    finally:
        state.close()


def test_background_recheck_tries_next_provider_after_rejected_ads_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import fitz

    from adsfolio.pdf.ads_recheck import recheck_existing
    from adsfolio.pdf.identify_settings import IdentifyStrategy

    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    root = tmp_path / "library"
    root.mkdir()
    path = root / "paper.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((40, 100), f"An Example Paper\nDOI: {DOI}")
    document.set_metadata({"title": "An Example Paper", "subject": f"DOI: {DOI}"})
    document.save(path)
    document.close()
    digest = sha256_file(path)
    calls = []

    class Search:
        def query(self, **kwargs):
            calls.append("ads")
            bad = {**ADS_DOC, "title": ["A Different Paper"]}
            return SimpleNamespace(num_found=1, docs=[bad])

    def crossref(doi):
        calls.append("crossref")
        return dict(EXTERNAL_DOC)

    monkeypatch.setattr("adsfolio.pdf.identifier.fetch_crossref_doi", crossref)
    state = open_registry_state(root)
    try:
        state.upsert_file(path.name, {"sha256": digest, "status": "unresolved"})
        settings = IdentifySettings(
            providers=(DoiProvider.ADS, DoiProvider.CROSSREF),
            strategies=(IdentifyStrategy.PDF_METADATA_DOI,),
        )
        result = recheck_existing(
            state,
            SimpleNamespace(search=Search()),
            RenameSettings(),
            identify_settings=settings,
        )
        after = state.registry.find_file_by_relative_path(root, path.name)
        assert result.updated == 1, result.failures
        assert calls == ["ads", "crossref"]
        assert after["metadata"]["_adsfolio_identity"]["status"] == "verified"
        assert (
            after["metadata"]["_adsfolio_external_metadata"]["provider"] == "crossref"
        )
        assert sha256_file(path) == digest
    finally:
        state.close()


def test_ads_upgrade_is_not_short_circuited_by_earlier_external_provider(
    identified_pdf, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, path, original = identified_pdf
    search = _Search()
    monkeypatch.setattr(
        "adsfolio.pdf.cli._get_client", lambda ctx: SimpleNamespace(search=search)
    )

    def unexpected_crossref(doi):
        pytest.fail(f"External record recheck queried Crossref again: {doi}")

    monkeypatch.setattr(
        "adsfolio.pdf.identifier.fetch_crossref_doi", unexpected_crossref
    )
    result = CliRunner().invoke(
        pdf,
        [
            "identify",
            str(root),
            "--file",
            str(path),
            "--allow-ads",
            "--provider",
            "crossref",
            "--provider",
            "ads",
        ],
    )

    assert result.exit_code == 0, result.output
    assert search.queries
    state = open_registry_state(root)
    try:
        saved = state.registry.catalog.get_file(root, path.name)
        assert saved["attachment_id"] == original["attachment_id"]
        assert saved["bibcode"] == ADS_DOC["bibcode"]
        assert saved["metadata"]["notes"] == "Keep the user's notes"
    finally:
        state.close()


def test_failed_ads_upgrade_keeps_existing_external_record(
    identified_pdf, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, path, original = identified_pdf
    search = _Search(fail=True)
    monkeypatch.setattr(
        "adsfolio.pdf.cli._get_client", lambda ctx: SimpleNamespace(search=search)
    )
    monkeypatch.setattr(
        "adsfolio.pdf.identifier.fetch_crossref_doi", lambda doi: dict(EXTERNAL_DOC)
    )
    digest = sha256_file(path)

    result = CliRunner().invoke(
        pdf,
        ["identify", str(root), "--file", str(path), "--allow-ads"],
    )

    assert result.exit_code == 0, result.output
    assert search.queries
    state = open_registry_state(root)
    try:
        saved = state.registry.catalog.get_file(root, path.name)
        assert saved["attachment_id"] == original["attachment_id"]
        assert saved["metadata"] == original["metadata"]
        assert saved["status"] == original["status"]
    finally:
        state.close()
    assert sha256_file(path) == digest


def test_cached_ads_upgrade_does_not_need_network_permission(
    identified_pdf, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, path, original = identified_pdf
    state = open_registry_state(root)
    state.registry.upsert_ads_documents([ADS_DOC], source="test")
    state.close()
    search = _Search(fail=True)
    monkeypatch.setattr(
        "adsfolio.pdf.cli._get_client", lambda ctx: SimpleNamespace(search=search)
    )

    result = CliRunner().invoke(
        pdf,
        ["identify", str(root), "--file", str(path), "--no-allow-ads"],
    )

    assert result.exit_code == 0, result.output
    assert not search.queries
    state = open_registry_state(root)
    try:
        saved = state.registry.catalog.get_file(root, path.name)
        assert saved["attachment_id"] == original["attachment_id"]
        assert saved["bibcode"] == ADS_DOC["bibcode"]
        assert saved["status"] == "resolved"
        assert saved["metadata"]["notes"] == "Keep the user's notes"
    finally:
        state.close()
