"""BibTeX export uses the chosen key styles and stops on missing or shared keys."""

from __future__ import annotations

from pathlib import Path

import pytest

from adsfolio.citekeys import KeyPolicy, KeyStyle
from adsfolio.config import Config
from adsfolio.pdf.catalog import CATALOG_FILENAME, PdfCatalog
from adsfolio.references import CitationKeyError, LocalReferences

ROOT = Path("/library")


@pytest.fixture
def catalog(monkeypatch, tmp_path) -> PdfCatalog:
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    created = PdfCatalog(tmp_path / CATALOG_FILENAME)
    yield created
    created.close()


def _add(catalog: PdfCatalog, name: str, metadata: dict, digest: str) -> int:
    observation = catalog.observe_file(
        ROOT,
        name,
        sha256=digest * 64,
        size_bytes=10,
        mtime_ns=20,
        metadata=metadata,
        status=metadata.get("status", "unresolved"),
    )
    return catalog.document_for_attachment(observation.attachment_id)["id"]


def _crossref(doi: str, title: str, author: str = "Roe, Alice") -> dict:
    return {
        "bibcode": "",
        "doi": [doi],
        "title": [title],
        "author": [author],
        "year": "2021",
        "pub": "Journal of Examples",
        "_adsfolio_external_metadata": {"provider": "crossref", "doi": doi},
    }


def _ads(bibcode: str, title: str) -> dict:
    return {
        "bibcode": bibcode,
        "doi": ["10.3847/example"],
        "title": [title],
        "author": ["Kim, Sun"],
        "year": "2020",
        "pub": "The Astrophysical Journal",
        "status": "resolved",
    }


def test_default_keys_are_bibcode_for_ads_and_doi_for_other(catalog):
    ads = _add(catalog, "a.pdf", _ads("2020ApJ...900....1K", "Stars"), "a")
    other = _add(catalog, "b.pdf", _crossref("10.5555/Not.ADS", "Outside"), "b")
    text = LocalReferences(catalog).export_bibtex([ads, other])
    assert "{2020ApJ...900....1K," in text
    assert "{10.5555/not.ads," in text.lower()


def test_google_scholar_style_for_papers_outside_ads(catalog):
    other = _add(catalog, "b.pdf", _crossref("10.5555/x", "On the Theory of Dust"), "b")
    policy = KeyPolicy(other=KeyStyle.SCHOLAR)
    text = LocalReferences(catalog, policy).export_bibtex([other])
    assert "{roe2021theory," in text


def test_shared_keys_stop_the_export_until_the_user_types_one(catalog):
    first = _add(catalog, "a.pdf", _crossref("10.5555/a", "Dust"), "a")
    second = _add(catalog, "b.pdf", _crossref("10.5555/b", "Dust grains"), "b")
    library = LocalReferences(catalog, KeyPolicy(other=KeyStyle.SCHOLAR))
    with pytest.raises(CitationKeyError) as caught:
        library.export_bibtex([first, second])
    assert caught.value.assignment.clashes == {"roe2021dust": [first, second]}
    library.set_cite_key(second, "roe2021grains")
    text = library.export_bibtex([first, second])
    assert "{roe2021dust," in text and "{roe2021grains," in text


def test_manual_style_asks_for_every_key(catalog):
    other = _add(catalog, "b.pdf", _crossref("10.5555/x", "Dust"), "b")
    library = LocalReferences(catalog, KeyPolicy(other=KeyStyle.MANUAL))
    with pytest.raises(CitationKeyError) as caught:
        library.export_bibtex([other])
    assert caught.value.assignment.missing == [other]
    library.set_cite_key(other, "MyDust")
    assert "{MyDust," in library.export_bibtex([other])


def test_typed_key_survives_a_metadata_refresh(catalog):
    other = _add(catalog, "b.pdf", _crossref("10.5555/x", "Dust"), "b")
    library = LocalReferences(catalog)
    library.set_cite_key(other, "MyDust")
    again = _add(catalog, "b.pdf", _crossref("10.5555/x", "Dust, revised"), "b")
    assert again == other
    assert library.get(other)["cite_key"] == "MyDust"


def test_typed_key_with_bibtex_delimiters_is_rejected(catalog):
    other = _add(catalog, "b.pdf", _crossref("10.5555/x", "Dust"), "b")
    with pytest.raises(ValueError, match="Citation key"):
        LocalReferences(catalog).set_cite_key(other, "bad key")


def test_imported_bibtex_key_wins_over_the_style(catalog):
    library = LocalReferences(catalog, KeyPolicy(other=KeyStyle.SCHOLAR))
    [saved] = library.import_bibtex(
        "@book{Knuth84, title={The TeXbook}, author={Knuth, Donald}, year={1984}}"
    )
    assert "{Knuth84," in library.export_bibtex([saved["id"]])


def test_cli_export_lists_papers_without_keys_then_set_key_fixes_it(
    catalog, monkeypatch
):
    from click.testing import CliRunner

    from adsfolio.cli.commands import main

    other = _add(catalog, "b.pdf", _crossref("10.5555/x", "Dust"), "b")
    monkeypatch.setattr(
        Config,
        "load_key_policy",
        classmethod(lambda cls: KeyPolicy(other=KeyStyle.MANUAL)),
    )
    path = str(catalog.path)
    runner = CliRunner()
    failed = runner.invoke(main, ["reference", "--catalog", path, "export", "--all"])
    assert failed.exit_code != 0
    assert f"{other}: 2021 Outside" not in failed.output
    assert f"{other}: 2021 Dust  (no key)" in failed.output
    assert "reference set-key ID KEY" in failed.output
    fixed = runner.invoke(
        main, ["reference", "--catalog", path, "set-key", str(other), "MyDust"]
    )
    assert fixed.exit_code == 0, fixed.output
    exported = runner.invoke(main, ["reference", "--catalog", path, "export", "--all"])
    assert exported.exit_code == 0, exported.output
    assert "{MyDust," in exported.output
