"""Search folding is exact, and search documents are rebuilt only when needed."""

from __future__ import annotations

import unicodedata

import pytest

from adsfolio.config import Config
from adsfolio.pdf import registry as registry_module
from adsfolio.pdf.registry import PdfRegistry
from adsfolio.pdf.search import fold_text


def _reference_fold(text: str) -> str:
    """The definition: NFKD, then drop combining marks, then casefold."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.casefold()


@pytest.mark.parametrize(
    "text",
    [
        "Plain ASCII Title, 2021",
        "Dorémont",
        "Hanuš Ďurech",
        "Ångström ÅÉÎÕÜ ß",
        "ﬁ ligature Ⅻ ①",
        "ȩ́ combining marks",
        "日本語のタイトル",
        "",
    ],
)
def test_fold_text_matches_its_definition(text):
    assert fold_text(text) == _reference_fold(text)


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    monkeypatch.setattr(registry_module, "_SEARCH_DOCUMENTS", {})
    root = tmp_path / "library"
    root.mkdir()
    registry = PdfRegistry(root)
    for name, title in (("a.pdf", "Comet dust"), ("b.pdf", "Asteroid régolith")):
        registry.upsert_file(
            root=root,
            relative_path=name,
            current_path=name,
            sha256=name,
            size_bytes=1,
            mtime_ns=1,
            status="resolved",
            metadata={"title": [title]},
        )
    yield root, registry
    registry.close()


def _count_builds(monkeypatch) -> list[str]:
    built: list[str] = []
    original = registry_module.record_search_document

    def counted(relative_path, metadata_json, sha256):
        built.append(relative_path)
        return original(relative_path, metadata_json, sha256)

    monkeypatch.setattr(registry_module, "record_search_document", counted)
    return built


def test_catalog_change_rebuilds_only_the_changed_document(library, monkeypatch):
    root, registry = library
    built = _count_builds(monkeypatch)
    assert {key[1] for key in registry.search_location_keys("regolith")}
    assert sorted(built) == ["a.pdf", "b.pdf"]

    built.clear()
    registry.replace_file_metadata(root, "a.pdf", {"title": ["Comet regolith"]})

    keys = registry.search_location_keys("regolith")
    assert built == ["a.pdf"]
    assert len(keys) == 2


def test_refreshed_registry_reuses_unchanged_documents(library, monkeypatch):
    root, registry = library
    registry.search_location_keys("comet")
    built = _count_builds(monkeypatch)

    fresh = PdfRegistry(root)
    try:
        keys = fresh.search_location_keys("comet")
    finally:
        fresh.close()

    assert built == []
    assert len(keys) == 1


def test_folded_document_folds_only_the_fields_that_are_read(monkeypatch):
    from adsfolio.pdf import search

    folded = []
    original = search.fold_text
    monkeypatch.setattr(
        search, "fold_text", lambda text: folded.append(text) or original(text)
    )
    document = search.fold_document({"title": "Régolith", "author": "Ďurech, J."})

    assert document.get("title") == "regolith"
    assert document.get("title") == "regolith"
    assert document.get("year", "") == ""
    assert folded == ["Régolith"]
    assert search.document_matches(document, "author:durech")
