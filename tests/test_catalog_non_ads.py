"""Papers that ADS does not index are catalog documents too."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from adsfolio.config import Config
from adsfolio.pdf.arxiv import normalize_arxiv_id
from adsfolio.pdf.catalog import CATALOG_FILENAME, CATALOG_SCHEMA_VERSION, PdfCatalog

ROOT = Path("/library")


@pytest.fixture
def catalog(monkeypatch, tmp_path) -> PdfCatalog:
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    created = PdfCatalog(tmp_path / CATALOG_FILENAME)
    yield created
    created.close()


def _external(doi: str = "10.1234/Example.5", title: str = "Not on ADS") -> dict:
    """Metadata as Identify stores it after a Crossref lookup."""
    return {
        "bibcode": "",
        "doi": doi,
        "title": [title],
        "author": ["Roe, A."],
        "year": "2021",
        "status": "unresolved",
        "_adsfolio_external_metadata": {"provider": "crossref", "doi": doi},
    }


def _observe(catalog: PdfCatalog, name: str, metadata: dict, digest: str = "a"):
    return catalog.observe_file(
        ROOT,
        name,
        sha256=digest * 64,
        size_bytes=10,
        mtime_ns=20,
        metadata=metadata,
        status=metadata.get("status", "unresolved"),
    )


def test_crossref_metadata_makes_a_document_that_stays_unresolved(catalog):
    observation = _observe(catalog, "paper.pdf", _external())
    document = catalog.document_for_attachment(observation.attachment_id)
    assert document is not None
    assert document["bibcode"] is None
    record = catalog.get_file(ROOT, "paper.pdf")
    assert record["status"] == "unresolved"
    assert [item["id"] for item in catalog.list_documents()] == [document["id"]]


def test_same_doi_in_any_spelling_is_one_document(catalog):
    first = _observe(catalog, "a.pdf", _external("10.1234/Example.5"), "a")
    second = _observe(
        catalog, "b.pdf", _external("https://doi.org/10.1234/EXAMPLE.5"), "b"
    )
    assert (
        catalog.document_for_attachment(first.attachment_id)["id"]
        == catalog.document_for_attachment(second.attachment_id)["id"]
    )
    assert len(catalog.list_documents()) == 1


def test_doi_document_gains_its_bibcode_instead_of_a_duplicate(catalog):
    observation = _observe(catalog, "paper.pdf", _external())
    external_id = catalog.document_for_attachment(observation.attachment_id)["id"]
    catalog.upsert_ads_document(
        {
            "bibcode": "2021DEMO....1....1R",
            "doi": ["10.1234/example.5"],
            "title": ["Not on ADS"],
        },
        source="test",
    )
    documents = catalog.list_documents()
    assert [(item["id"], item["bibcode"]) for item in documents] == [
        (external_id, "2021DEMO....1....1R")
    ]


def test_arxiv_id_matches_across_versions(catalog):
    base = {
        "bibcode": "",
        "title": ["Preprint"],
        "author": ["Roe, A."],
        "year": "2024",
        "_adsfolio_external_metadata": {"provider": "arxiv"},
    }
    first = _observe(catalog, "a.pdf", {**base, "arxiv": "2401.12345v1"}, "a")
    second = _observe(catalog, "b.pdf", {**base, "arxiv": "arXiv:2401.12345v3"}, "b")
    assert (
        catalog.document_for_attachment(first.attachment_id)["id"]
        == catalog.document_for_attachment(second.attachment_id)["id"]
    )


def test_a_doi_found_in_pdf_text_alone_makes_no_document(catalog):
    observation = _observe(catalog, "paper.pdf", {"doi": "10.1234/x", "status": "new"})
    assert catalog.document_for_attachment(observation.attachment_id) is None
    assert catalog.list_documents() == []


def test_manual_details_for_a_non_ads_pdf_make_a_document(catalog):
    _observe(catalog, "paper.pdf", {"status": "new"})
    assert catalog.replace_file_metadata(
        ROOT,
        "paper.pdf",
        {
            "title": ["Field notes"],
            "author": ["Roe, A."],
            "year": "2020",
            "_adsfolio_locked_fields": ["title"],
        },
    )
    record = catalog.get_file(ROOT, "paper.pdf")
    assert catalog.document_for_attachment(record["attachment_id"]) is not None
    assert record["status"] != "resolved"


def test_version_2_catalog_gains_identity_columns(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    path = tmp_path / CATALOG_FILENAME
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE catalog_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO catalog_meta VALUES ('schema_version', '2');
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY,
            bibcode TEXT UNIQUE,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    connection.execute(
        "INSERT INTO documents (bibcode, metadata_json, created_at, updated_at) "
        "VALUES (?, ?, 't', 't')",
        (
            "2020DEMO....1....1A",
            json.dumps({"doi": ["10.5555/ABC"], "identifier": ["arXiv:2001.00001"]}),
        ),
    )
    connection.commit()
    connection.close()

    catalog = PdfCatalog(path)
    try:
        row = catalog._connection.execute(
            "SELECT doi, arxiv_id FROM documents"
        ).fetchone()
        assert (row["doi"], row["arxiv_id"]) == ("10.5555/abc", "2001.00001")
        assert catalog.schema_version == CATALOG_SCHEMA_VERSION == 3
    finally:
        catalog.close()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2401.12345v2", "2401.12345"),
        ("arXiv:2401.12345", "2401.12345"),
        ("10.48550/arXiv.2401.12345", "2401.12345"),
        ("astro-ph/0601001v1", "astro-ph/0601001"),
        ("2024arXiv240112345B", "2401.12345"),
        ("10.1234/not-arxiv", ""),
    ],
)
def test_normalize_arxiv_id(value, expected):
    assert normalize_arxiv_id(value) == expected


def test_canonical_doi_strips_urls_and_case():
    assert PdfCatalog.canonical_doi("https://doi.org/10.1234/AbC") == "10.1234/abc"
    assert PdfCatalog.canonical_doi(["doi:10.1/X"]) == "10.1/x"
