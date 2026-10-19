"""Link saved bibcodes without replacing metadata or changing PDF paths."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

import pytest
from click.testing import CliRunner

from adsfolio.config import Config
from adsfolio.pdf.cli import _clear_invalid_bibcode, _refresh_file_metadata, pdf
from adsfolio.pdf.files import sha256_file
from adsfolio.pdf.identifier import PdfIdentifier
from adsfolio.pdf.readiness import METADATA_SHA256_KEY, METADATA_STALE_KEY
from adsfolio.pdf.registry import (
    LOCKED_METADATA_FIELDS_KEY,
    PdfRegistry,
    open_registry_state,
)

BIBCODE = "2017MNRAS.469S.443B"


def register(
    root: Path, name: str = "paper.pdf", *, stale: bool = False, locked: bool = True
) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.write_bytes(b"%PDF paper content")
    digest = sha256_file(path)
    metadata = {
        "bibcode": BIBCODE,
        "sha256": digest,
        "status": "unresolved",
        "title": ["My title"],
        "author": ["My author"],
        "year": "2017",
        "notes": "Keep this note",
        "identified_at": "2026-01-01T00:00:00Z",
        LOCKED_METADATA_FIELDS_KEY: ["title"] if locked else [],
    }
    if stale:
        metadata[METADATA_STALE_KEY] = True
    with closing(open_registry_state(root)) as state:
        return state.upsert_file(name, metadata)


@pytest.fixture(autouse=True)
def configured(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    monkeypatch.setattr(
        "adsfolio.pubcache.cache_path", lambda: tmp_path / "publications.json"
    )


def test_metadata_only_bulk_refresh_includes_pending_bibcodes_without_renaming(
    monkeypatch, tmp_path: Path
) -> None:
    root = tmp_path / "library"
    register(root)
    with closing(open_registry_state(root)) as state:
        before = state.registry.find_file_by_current_path(root, "paper.pdf")
        versions = state.registry.catalog.content_versions(before["attachment_id"])
    requests = []
    monkeypatch.setattr("adsfolio.pdf.cli._get_client", lambda ctx: object())

    def query(self, bibcodes, **kwargs):
        requests.append(bibcodes)
        return {
            BIBCODE: {
                "bibcode": BIBCODE,
                "title": ["ADS title"],
                "author": ["ADS author"],
                "abstract": "ADS abstract",
            }
        }

    monkeypatch.setattr(PdfIdentifier, "query_docs_for_bibcodes", query)
    result = CliRunner().invoke(pdf, ["refresh-ads", str(root), "--all", "--no-rename"])
    assert result.exit_code == 0, result.output
    assert requests == [[BIBCODE]]
    assert list(root.glob("*.pdf")) == [root / "paper.pdf"]
    with closing(open_registry_state(root)) as state:
        after = state.registry.find_file_by_current_path(root, "paper.pdf")
        assert after["status"] == "resolved"
        assert after["document_id"] is not None
        for key in (
            "title",
            "author",
            "notes",
            "identified_at",
            LOCKED_METADATA_FIELDS_KEY,
        ):
            assert after["metadata"][key] == before["metadata"][key]
        assert after["metadata"]["abstract"] == "ADS abstract"
        assert after["location_id"] == before["location_id"]
        assert (
            state.registry.catalog.content_versions(after["attachment_id"]) == versions
        )
        assert METADATA_SHA256_KEY not in after["metadata"]


@pytest.mark.parametrize("status,locked", [("unresolved", False), ("resolved", True)])
def test_missing_lookup_keeps_pending_or_locked_bibcode(
    tmp_path: Path, status: str, locked: bool
) -> None:
    root = tmp_path / "library"
    register(root)
    registry = PdfRegistry(root)
    metadata = registry.find_file_by_current_path(root, "paper.pdf")["metadata"]
    metadata[LOCKED_METADATA_FIELDS_KEY] = ["bibcode"] if locked else []
    metadata.pop("status", None)
    registry.replace_file_metadata(root, "paper.pdf", metadata)
    registry.update_file_status(root, "paper.pdf", status)
    assert _clear_invalid_bibcode(registry, root, BIBCODE) == 0
    assert registry.find_file_by_current_path(root, "paper.pdf")["bibcode"] == BIBCODE
    registry.close()


@pytest.mark.parametrize("mismatch", [False, True])
def test_metadata_only_refresh_does_not_validate_stale_or_wrong_digest(
    tmp_path: Path, mismatch: bool
) -> None:
    root = tmp_path / "library"
    register(root, stale=not mismatch)
    registry = PdfRegistry(root)
    before = registry.find_file_by_current_path(root, "paper.pdf")
    if mismatch:
        metadata = before["metadata"]
        metadata[METADATA_SHA256_KEY] = "different"
        registry.replace_file_metadata(root, "paper.pdf", metadata)
    assert not _refresh_file_metadata(
        registry, root, "paper.pdf", {"bibcode": BIBCODE}, preserve_existing=True
    )
    assert (
        registry.find_file_by_current_path(root, "paper.pdf")["status"] == "unresolved"
    )
    registry.close()


def test_pending_lookup_uses_cache_without_ads_token(
    monkeypatch, tmp_path: Path
) -> None:
    root = tmp_path / "library"
    register(root)
    with closing(open_registry_state(root)) as state:
        state.registry.upsert_ads_documents(
            [{"bibcode": BIBCODE, "abstract": "Cached abstract"}], source="test"
        )
    monkeypatch.setattr(
        "adsfolio.pdf.cli._get_client",
        lambda ctx: pytest.fail("Cached lookup needs no token"),
    )
    result = CliRunner().invoke(
        pdf, ["refresh-ads", str(root), "--all", "--no-rename", "--pending-only"]
    )
    assert result.exit_code == 0, result.output
    with closing(open_registry_state(root)) as state:
        assert state.get_file("paper.pdf")["status"] == "resolved"
        assert state.get_file("paper.pdf")["abstract"] == "Cached abstract"


def test_pending_lookup_preserves_bibcode_when_ads_has_no_match(
    monkeypatch, tmp_path: Path
) -> None:
    root = tmp_path / "library"
    register(root)
    monkeypatch.setattr("adsfolio.pdf.cli._get_client", lambda ctx: object())
    monkeypatch.setattr(
        PdfIdentifier,
        "query_docs_for_bibcodes",
        lambda self, codes, **kwargs: {code: None for code in codes},
    )
    result = CliRunner().invoke(
        pdf, ["refresh-ads", str(root), "--all", "--no-rename", "--pending-only"]
    )
    assert result.exit_code == 0, result.output
    with closing(open_registry_state(root)) as state:
        assert state.get_file("paper.pdf")["status"] == "unresolved"
        assert state.get_file("paper.pdf")["bibcode"] == BIBCODE
    assert list(root.glob("*.pdf")) == [root / "paper.pdf"]


def test_pending_lookup_skips_mismatched_metadata_digest_without_stale_flag(
    monkeypatch, tmp_path: Path
) -> None:
    root = tmp_path / "library"
    register(root)
    with closing(open_registry_state(root)) as state:
        metadata = state.get_file("paper.pdf")
        metadata["sha256"] = "old digest"
        state.registry.replace_file_metadata(root, "paper.pdf", metadata)
        state.registry.update_file_status(root, "paper.pdf", "unresolved")
    monkeypatch.setattr(
        "adsfolio.pdf.cli._get_client",
        lambda ctx: pytest.fail("Mismatched metadata must not be linked"),
    )
    result = CliRunner().invoke(
        pdf, ["refresh-ads", str(root), "--all", "--no-rename", "--pending-only"]
    )
    assert result.exit_code == 0, result.output
    assert "No pending bibcodes" in result.output
    with closing(open_registry_state(root)) as state:
        assert state.get_file("paper.pdf")["status"] == "unresolved"
