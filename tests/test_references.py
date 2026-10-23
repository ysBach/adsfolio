"""Reference intake, identity protection, and complete offline export."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from bibtexparser.bparser import BibTexParser

from adsfolio.pdf.catalog import LOCKED_METADATA_FIELDS_KEY, PdfCatalog
from adsfolio.references import LocalReferences, parse_bibtex


@pytest.fixture
def references(tmp_path: Path) -> Iterator[LocalReferences]:
    catalog = PdfCatalog(tmp_path / "catalog.sqlite")
    yield LocalReferences(catalog)
    catalog.close()


def test_standalone_references_persist_without_ads_or_pdf(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite"
    catalog = PdfCatalog(path)
    service = LocalReferences(catalog)
    first = service.add(cite_key="Book", entry_type="book", fields={"title": "Book"})
    second = service.add(
        cite_key="Web", fields={"title": "Web", "url": "https://example.org"}
    )
    catalog.close()
    reopened = PdfCatalog(path)
    records = LocalReferences(reopened).list()
    assert [item["id"] for item in records] == [first["id"], second["id"]]
    assert all(
        item["bibcode"] is None and item["attachments"] == [] for item in records
    )
    reopened.close()


def test_get_returns_only_selected_reference_with_missing_attachments(
    references: LocalReferences, tmp_path: Path
) -> None:
    first = references.add(cite_key="First", fields={"title": "First"})
    second = references.add(cite_key="Second", fields={"title": "Second"})
    for reference in (first, second):
        path = tmp_path / f"{reference['cite_key']}.pdf"
        path.write_bytes(f"%PDF-{reference['cite_key']}".encode())
        references.attach_pdf(reference["id"], path)
    references.catalog.mark_missing_locations(tmp_path, {"Second.pdf"})

    selected = references.get(first["id"])

    assert selected["cite_key"] == "First"
    assert [item["current_path"] for item in selected["attachments"]] == ["First.pdf"]
    assert selected["attachments"][0]["missing_at"] is not None
    assert references.catalog.query_files(document_id=first["id"]) == []
    with pytest.raises(ValueError, match="Unknown reference ID"):
        references.get(-1)


def test_get_does_not_decode_unrelated_document_metadata(
    references: LocalReferences,
) -> None:
    selected = references.add(cite_key="Selected", fields={"title": "Selected"})
    unrelated = references.add(cite_key="Unrelated", fields={"title": "Unrelated"})
    with references.catalog._connection:
        references.catalog._connection.execute(
            "UPDATE documents SET metadata_json = ? WHERE id = ?",
            ("invalid JSON", unrelated["id"]),
        )

    assert references.get(selected["id"])["fields"] == {"title": "Selected"}


def test_import_preserves_bibtex_semantics_and_adds_crossref_parent(
    references: LocalReferences,
) -> None:
    source = r"""@string{press = "Example" # " Press"}
@preamble{"\newcommand{\custom}{Hello}"}
@book{Parent, title={A {Nested} Book}, publisher=press, year=2024}
@inbook{Child, title="A chapter", crossref={Parent},
author={{Research and Development Group}}, custom={\textbf{Value}}, month=jan}
"""
    preview = parse_bibtex(source)
    assert references.list() == []
    imported = references.import_bibtex(source, source="google-scholar")
    output = references.export_bibtex([imported[1]["id"]])
    parsed = BibTexParser(ignore_nonstandard_types=False).parse(output)
    assert [entry["ID"] for entry in parsed.entries] == ["Child", "Parent"]
    assert parsed.entries_dict["Parent"]["publisher"] == "Example Press"
    assert parsed.entries_dict["Parent"]["title"] == "A {Nested} Book"
    assert parsed.entries_dict["Child"]["custom"] == r"\textbf{Value}"
    assert parsed.entries_dict["Child"]["author"] == "{Research and Development Group}"
    assert parsed.preambles == [r"\newcommand{\custom}{Hello}"]
    assert imported[0]["fields"] == preview[0]["fields"]
    assert imported[0]["source"] == "google-scholar"
    assert imported[0]["source_id"] == imported[1]["source_id"]
    assert references.original_bibtex(imported[0]["id"]) == source


@pytest.mark.parametrize(
    "text",
    [
        "@book{ok,title={Fine}}\n@book{bad,title={Unclosed}",
        "@book{same,title={One}}\n@article{same,title={Two}}",
        "No entries here",
        "@book{unknown,title=undefinedmacro}",
    ],
)
def test_invalid_import_is_atomic(references: LocalReferences, text: str) -> None:
    with pytest.raises(ValueError):
        references.import_bibtex(text)
    assert references.list() == []


@pytest.mark.parametrize(
    "duplicate",
    [
        "@book{Original,title={Duplicate}}",
        "@book{Other,title={Duplicate},doi={https://doi.org/10.1000/ABC}}",
    ],
)
def test_conflict_in_last_entry_rolls_back_whole_import(
    references: LocalReferences, duplicate: str
) -> None:
    original = references.add(
        cite_key="Original", fields={"title": "Original", "doi": "10.1000/abc"}
    )
    with pytest.raises(ValueError, match="already exists"):
        references.import_bibtex("@book{New,title={New}}\n" + duplicate)
    assert [item["id"] for item in references.list()] == [original["id"]]


def test_existing_duplicate_dois_do_not_block_unrelated_reference(
    references: LocalReferences, tmp_path: Path
) -> None:
    for index in range(2):
        references.catalog.observe_file(
            tmp_path,
            f"{index}.pdf",
            sha256=None,
            size_bytes=1,
            mtime_ns=1,
            metadata={"bibcode": f"ads-{index}", "doi": ["10.1000/same"]},
        )
    added = references.add(cite_key="Independent", fields={"title": "Independent"})
    assert references.get(added["id"])["cite_key"] == "Independent"


def test_manual_fields_and_key_survive_ads_refresh_and_pdf_rescan(
    references: LocalReferences, tmp_path: Path
) -> None:
    reference = references.add(cite_key="Local", fields={"title": "Original"})
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\nA PDF fixture\n")
    attachment = references.attach_pdf(reference["id"], pdf)
    references.update(
        reference["id"],
        cite_key="EditedKey",
        fields={"title": "User title", "author": "Smith, A."},
    )
    references.catalog.observe_file(
        tmp_path,
        pdf.name,
        sha256=attachment["sha256"],
        size_bytes=pdf.stat().st_size,
        mtime_ns=pdf.stat().st_mtime_ns,
        metadata={
            "bibcode": "new-ads",
            "title": ["ADS title"],
            LOCKED_METADATA_FIELDS_KEY: [],
        },
        status="resolved",
    )
    refreshed = references.catalog.get_file(tmp_path, pdf.name)
    assert refreshed["document_id"] == reference["id"]
    assert refreshed["status"] == "unresolved"
    assert refreshed["metadata"]["title"] == ["User title"]
    assert "bibcode" in refreshed["metadata"][LOCKED_METADATA_FIELDS_KEY]
    assert references.get(reference["id"])["cite_key"] == "EditedKey"


def test_attach_refuses_to_replace_existing_document(
    references: LocalReferences, tmp_path: Path
) -> None:
    first = references.add(cite_key="One", fields={"title": "One"})
    second = references.add(cite_key="Two", fields={"title": "Two"})
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    references.attach_pdf(first["id"], pdf)
    with pytest.raises(ValueError, match="another reference"):
        references.attach_pdf(second["id"], pdf)
    assert references.catalog.get_file(tmp_path, pdf.name)["document_id"] == first["id"]


def test_mixed_export_uses_ads_metadata_without_network(
    references: LocalReferences, tmp_path: Path
) -> None:
    local = references.add(cite_key="Manual", fields={"title": "Local"})
    observation = references.catalog.observe_file(
        tmp_path,
        "ads.pdf",
        sha256=None,
        size_bytes=1,
        mtime_ns=1,
        metadata={
            "bibcode": "2024Test...........A",
            "title": ["ADS title"],
            "author": ["Smith, A.", "Jones, B."],
            "pub": "Journal",
            "year": "2024",
        },
    )
    ads = references.catalog.document_for_attachment(observation.attachment_id)
    parsed = BibTexParser().parse(references.export_bibtex([local["id"], ads["id"]]))
    assert set(parsed.entries_dict) == {"Manual", "2024Test...........A"}
    assert (
        parsed.entries_dict["2024Test...........A"]["author"]
        == "Smith, A. and Jones, B."
    )


def test_export_preflights_drafts_unknown_ids_and_crossrefs(
    references: LocalReferences,
) -> None:
    draft = references.add(cite_key="Draft", fields={})
    with pytest.raises(ValueError, match="no bibliography fields"):
        references.export_bibtex([draft["id"]])
    with pytest.raises(ValueError, match="Unknown reference"):
        references.export_bibtex([999])
    child = references.add(cite_key="Child", fields={"crossref": "Missing"})
    with pytest.raises(ValueError, match="Missing crossref"):
        references.export_bibtex([child["id"]])
    parent = references.add(cite_key="Missing", fields={"crossref": "Child"})
    with pytest.raises(ValueError, match="Cyclic crossref"):
        references.export_bibtex([parent["id"]])


@pytest.mark.parametrize(
    ("key", "fields"),
    [
        ("bad key", {"title": "Good"}),
        ("Good", {"title": "Unclosed {brace"}),
        ("Good", {"title": "x}, injected={oops"}),
        ("Good", {"not a field": "x"}),
    ],
)
def test_manual_input_rejects_invalid_bibtex(
    references: LocalReferences, key: str, fields: dict[str, str]
) -> None:
    with pytest.raises(ValueError):
        references.add(cite_key=key, fields=fields)
    assert references.list() == []


def test_edit_preserves_unknown_fields_and_rejects_duplicate_key(
    references: LocalReferences,
) -> None:
    first, second = references.import_bibtex(
        "@book{One,title={One},custom={Data}}\n@book{Two,title={Two}}"
    )
    edited = references.update(first["id"], cite_key="Renamed")
    assert edited["fields"]["custom"] == "Data"
    with pytest.raises(ValueError, match="already exists"):
        references.update(first["id"], cite_key=second["cite_key"])
    assert references.get(first["id"])["cite_key"] == "Renamed"


def test_preamble_macros_are_stored_and_exported_as_text(
    references: LocalReferences,
) -> None:
    imported = references.import_bibtex(
        '@string{pre="abc"}\n@preamble{pre # " def"}\n@article{a,title={T}}'
    )
    result = BibTexParser().parse(references.export_bibtex([imported[0]["id"]]))
    assert result.preambles == ["abc def"]
    with pytest.raises(ValueError, match="Invalid BibTeX"):
        parse_bibtex("@preamble{missing}\n@article{a,title={T}}")


def test_ads_text_is_escaped_but_imported_tex_is_preserved(
    references: LocalReferences, tmp_path: Path
) -> None:
    observed = references.catalog.observe_file(
        tmp_path,
        "ads.pdf",
        sha256=None,
        size_bytes=1,
        mtime_ns=1,
        metadata={
            "bibcode": "ads",
            "title": ["A & B: 50% efficiency with H<SUB>2</SUB>O"],
            "pub": "A&A",
            "doi": ["10.1234/under_score"],
        },
    )
    document = references.catalog.document_for_attachment(observed.attachment_id)
    imported = references.import_bibtex(r"@book{tex,title={Already \& {Protected}}}")[0]
    result = (
        BibTexParser()
        .parse(references.export_bibtex([document["id"], imported["id"]]))
        .entries_dict
    )
    assert result["ads"]["title"] == r"A \& B: 50\% efficiency with H$_{2}$O"
    assert result["ads"]["journal"] == r"A\&A"
    assert result["ads"]["doi"] == "10.1234/under_score"
    assert result["tex"]["title"] == r"Already \& {Protected}"


def test_export_preserves_unrelated_selection_order(
    references: LocalReferences,
) -> None:
    imported = references.import_bibtex(
        "@book{B,title={B}}\n@book{A,title={A}}\n@book{C,title={C}}"
    )
    ids = [item["id"] for item in imported]
    result = BibTexParser().parse(references.export_bibtex(ids))
    assert [item["ID"] for item in result.entries] == ["B", "A", "C"]


def test_author_projection_respects_corporate_braces(
    references: LocalReferences, tmp_path: Path
) -> None:
    ref = references.add(
        cite_key="Names",
        fields={"author": "Doe, Jane and {Research and Development} and Roe, John"},
    )
    pdf = tmp_path / "authors.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    attachment = references.attach_pdf(ref["id"], pdf)
    assert attachment["metadata"]["author"] == [
        "Doe, Jane",
        "{Research and Development}",
        "Roe, John",
    ]


def test_pdf_metadata_editor_preserves_reference_link_and_protection(
    references: LocalReferences, tmp_path: Path
) -> None:
    ref = references.add(cite_key="Managed", fields={"title": "Managed"})
    pdf = tmp_path / "managed.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    attachment = references.attach_pdf(ref["id"], pdf)
    metadata = attachment["metadata"]
    assert references.catalog.replace_file_metadata(
        tmp_path, pdf.name, {**metadata, "pdf_note": "Allowed"}
    )
    assert references.catalog.get_file(tmp_path, pdf.name)["document_id"] == ref["id"]
    assert references.catalog.get_file(tmp_path, pdf.name)["status"] == "unresolved"
    assert references.catalog.update_file_status(tmp_path, pdf.name, "resolved")
    assert references.catalog.get_file(tmp_path, pdf.name)["status"] == "unresolved"
    with pytest.raises(ValueError, match="Local References"):
        references.catalog.replace_file_metadata(
            tmp_path, pdf.name, {**metadata, "title": ["Changed"]}
        )
    with pytest.raises(ValueError, match="Local References"):
        references.catalog.replace_file_metadata(
            tmp_path, pdf.name, {**metadata, LOCKED_METADATA_FIELDS_KEY: []}
        )


def test_pdf_specific_locks_remain_editable_for_local_reference(
    references: LocalReferences, tmp_path: Path
) -> None:
    ref = references.add(cite_key="Notes", fields={"title": "Local reference"})
    pdf = tmp_path / "notes.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    attachment = references.attach_pdf(ref["id"], pdf)
    metadata = {
        **attachment["metadata"],
        "pdf_note": "Original note",
        LOCKED_METADATA_FIELDS_KEY: [
            *attachment["metadata"][LOCKED_METADATA_FIELDS_KEY],
            "pdf_note",
        ],
    }
    assert references.catalog.replace_file_metadata(tmp_path, pdf.name, metadata)
    metadata["pdf_note"] = "Corrected note"
    metadata[LOCKED_METADATA_FIELDS_KEY].remove("pdf_note")
    assert references.catalog.replace_file_metadata(tmp_path, pdf.name, metadata)
    record = references.catalog.get_file(tmp_path, pdf.name)
    assert record["metadata"]["pdf_note"] == "Corrected note"
    assert record["document_id"] == ref["id"]
    assert record["status"] == "unresolved"


def test_export_detects_key_collision_created_by_later_ads_identification(
    references: LocalReferences, tmp_path: Path
) -> None:
    manual = references.add(cite_key="ads-key", fields={"title": "Manual"})
    observed = references.catalog.observe_file(
        tmp_path,
        "ads.pdf",
        sha256=None,
        size_bytes=1,
        mtime_ns=1,
        metadata={"bibcode": "ads-key", "title": ["Other"]},
    )
    ads = references.catalog.document_for_attachment(observed.attachment_id)
    with pytest.raises(ValueError, match="duplicate citation keys"):
        references.export_bibtex([manual["id"], ads["id"]])


@pytest.mark.parametrize(
    ("abstract", "expected"),
    [
        ("A lone } in text", r"A lone \textbraceright{} in text"),
        ("A lone { in text", r"A lone \textbraceleft{} in text"),
        (
            "2012 {DR}<sub>30</sub>} data",
            r"2012 \textbraceleft{}DR\textbraceright{}$_{30}$\textbraceright{} data",
        ),
    ],
)
def test_export_preserves_provider_literal_braces_without_changing_metadata(
    references: LocalReferences, tmp_path: Path, abstract: str, expected: str
) -> None:
    observed = references.catalog.observe_file(
        tmp_path,
        "braces.pdf",
        sha256=None,
        size_bytes=1,
        mtime_ns=1,
        metadata={
            "bibcode": "BracePaper",
            "title": ["Provider braces"],
            "abstract": abstract,
        },
    )
    document = references.catalog.document_for_attachment(observed.attachment_id)

    output = references.export_bibtex([document["id"]])

    result = BibTexParser().parse(output).entries_dict
    assert result["BracePaper"]["abstract"] == expected
    assert references.catalog.get_document(document["id"]) == document


def test_export_field_error_identifies_paper_and_corrective_action(
    references: LocalReferences, tmp_path: Path
) -> None:
    observed = references.catalog.observe_file(
        tmp_path,
        "bad-math.pdf",
        sha256=None,
        size_bytes=1,
        mtime_ns=1,
        metadata={
            "bibcode": "BadMathPaper",
            "title": ["A paper to review"],
            "abstract": "$x^{2$",
        },
    )
    document = references.catalog.document_for_attachment(observed.attachment_id)

    with pytest.raises(ValueError) as caught:
        references.export_bibtex([document["id"]])

    message = str(caught.value)
    assert "A paper to review" in message
    assert "BadMathPaper" in message
    assert "abstract" in message
    assert "paper's details" in message
    assert "export again" in message
    assert isinstance(caught.value.__cause__, ValueError)
    assert references.catalog.get_document(document["id"]) == document
