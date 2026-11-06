"""BibTeX export uses ADS's own entry, saved once on this computer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from adsfolio.api.export import ExportAPI
from adsfolio.config import Config
from adsfolio.pdf.catalog import (
    CATALOG_FILENAME,
    CITE_KEY_METADATA_KEY,
    LOCKED_METADATA_FIELDS_KEY,
    PdfCatalog,
)
from adsfolio.references import LocalReferences, ads_bibtex_fingerprint

ROOT = Path("/library")
# Fictional thesis in the layout of the ADS export service.
THESIS = "2005PhDT........42Q"
THESIS_ENTRY = f"""@PHDTHESIS{{{THESIS},
       author = {{{{Quill}}, Avery}},
        title = "{{Fictional ices of small moons}}",
     keywords = {{Ices, Moons}},
       school = {{Fictional University}},
         year = 2005,
        month = jan,
       adsurl = {{https://ui.adsabs.harvard.edu/abs/{THESIS}}},
      adsnote = {{Provided by the SAO/NASA Astrophysics Data System}}
}}"""


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
        status="resolved",
    )
    return catalog.document_for_attachment(observation.attachment_id)["id"]


def _thesis(**extra: Any) -> dict:
    return {
        "bibcode": THESIS,
        "title": ["Fictional ices of small moons"],
        "author": ["Quill, Avery"],
        "year": "2005",
        "pub": "Ph.D. Thesis",
        **extra,
    }


def _saved(catalog: PdfCatalog, document_id: int) -> LocalReferences:
    """Save the ADS entry as a completed fetch would."""
    references = LocalReferences(catalog)
    requests = references.ads_bibtex_requests([document_id])
    assert references.store_ads_bibtex({THESIS: THESIS_ENTRY}, requests) == 1
    return references


def test_export_uses_the_saved_ads_entry(catalog: PdfCatalog) -> None:
    document_id = _add(catalog, "thesis.pdf", _thesis(), "a")
    references = _saved(catalog, document_id)

    text = references.export_bibtex([document_id], exclude_fields=("adsnote",))

    assert text.startswith(f"@PHDTHESIS{{{THESIS},")
    assert "school = {Fictional University}" in text
    assert "month = jan," in text
    assert "keywords = {Ices, Moons}" in text
    assert "adsnote" not in text
    # A later export reads the saved entry and needs no ADS request.
    assert references.ads_bibtex_requests([document_id]) == {}


def test_export_puts_the_chosen_key_in_the_ads_entry(catalog: PdfCatalog) -> None:
    document_id = _add(
        catalog, "thesis.pdf", _thesis(**{CITE_KEY_METADATA_KEY: "quill2005"}), "a"
    )
    text = _saved(catalog, document_id).export_bibtex([document_id])
    assert text.startswith("@PHDTHESIS{quill2005,")


def test_no_ads_entry_is_saved_and_not_requested_again(catalog: PdfCatalog) -> None:
    document_id = _add(catalog, "thesis.pdf", _thesis(), "a")
    references = LocalReferences(catalog)
    requests = references.ads_bibtex_requests([document_id])
    assert references.store_ads_bibtex({}, requests) == 0
    assert references.ads_bibtex_requests([document_id]) == {}
    # Without an ADS entry, export keeps the local fields.
    assert references.export_bibtex([document_id]).startswith("@article{")


def test_changed_ads_metadata_needs_a_new_entry(catalog: PdfCatalog) -> None:
    document_id = _add(catalog, "thesis.pdf", _thesis(), "a")
    references = LocalReferences(catalog)
    catalog.store_ads_bibtex({THESIS: (THESIS_ENTRY, "older metadata")})

    assert references.ads_bibtex_requests([document_id]) == {
        THESIS: ads_bibtex_fingerprint(catalog.get_document(document_id)["metadata"])
    }
    assert references.export_bibtex([document_id]).startswith("@article{")


@pytest.mark.parametrize(
    "extra",
    [
        {LOCKED_METADATA_FIELDS_KEY: ["title"]},
        {CITE_KEY_METADATA_KEY: "quill2005", LOCKED_METADATA_FIELDS_KEY: ["year"]},
    ],
)
def test_locked_fields_keep_the_local_entry(catalog: PdfCatalog, extra: dict) -> None:
    """Your locked values stay in the export."""
    document_id = _add(catalog, "thesis.pdf", _thesis(**extra), "a")
    references = LocalReferences(catalog)
    assert references.ads_bibtex_requests([document_id]) == {}
    catalog.store_ads_bibtex(
        {THESIS: (THESIS_ENTRY, ads_bibtex_fingerprint(_thesis(**extra)))}
    )
    assert "school" not in references.export_bibtex([document_id])


def test_mixed_export_keeps_order_and_layout(catalog: PdfCatalog) -> None:
    thesis_id = _add(catalog, "thesis.pdf", _thesis(), "a")
    other_id = _add(
        catalog,
        "other.pdf",
        {
            "bibcode": "",
            "doi": ["10.5555/fictional"],
            "title": ["A fictional local paper"],
            "author": ["Roe, Alice"],
            "year": "2021",
            "pub": "Journal of Examples",
            "_adsfolio_external_metadata": {
                "provider": "crossref",
                "doi": "10.5555/fictional",
            },
        },
        "b",
    )
    references = _saved(catalog, thesis_id)

    text = references.export_bibtex([other_id, thesis_id])

    first, second = text.split("\n\n@", 1)
    assert first.startswith("@article{") and "A fictional local paper" in first
    assert second.startswith(f"PHDTHESIS{{{THESIS},")
    assert text.endswith("}\n")


class _ExportClient:
    """Model the ADS export service with individual entries."""

    def __init__(self, known: set[str]) -> None:
        self.known = known
        self.calls: list[list[str]] = []

    def post(self, endpoint: str, data: dict[str, Any]) -> dict[str, Any]:
        assert endpoint == "/export/bibtexabs"
        assert data["outputformat"] == 2
        self.calls.append(list(data["bibcode"]))
        docs = [
            {"bibcode": code, "reference": f"@ARTICLE{{{code},\n title = {{T}}\n}}\n"}
            for code in data["bibcode"]
            if code in self.known
        ]
        return {"num_docs": len(docs), "docs": docs}


def test_export_entries_skips_unknown_bibcodes_and_reports_progress(
    monkeypatch,
) -> None:
    monkeypatch.setattr(ExportAPI, "MAX_RECORDS_PER_REQUEST", 2)
    client = _ExportClient({"2020A", "2020C"})
    progress: list[tuple[int, int]] = []

    entries = ExportAPI(client).export_entries(
        ["2020A", "2020B", "2020C"], progress=lambda *args: progress.append(args)
    )

    assert client.calls == [["2020A", "2020B"], ["2020C"]]
    assert set(entries) == {"2020A", "2020C"}
    assert entries["2020A"].startswith("@ARTICLE{2020A,")
    assert progress == [(2, 3), (3, 3)]
