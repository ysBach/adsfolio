"""Reviewed-parser-error exception of the identity repair (manifest only)."""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfWriter
from pypdf.generic import NameObject, StreamObject

from adsfolio.pdf.catalog import PdfCatalog
from adsfolio.pdf.identity import UnverifiedIdentityError, assert_mutation_allowed
from adsfolio.pdf.identity_repair import (
    IdentityRepairError,
    RepairEntry,
    apply_repairs,
    build_plan,
    undo_repair,
)
from adsfolio.pdf.rename_settings import RenameSettings

SETTINGS = RenameSettings(max_authors=2, authors_when_truncated=1)
XMP_ERROR = "XMP metadata is not well-formed"
DOI = "10.1000/reviewed"
TABLES = ("attachments", "file_locations", "documents", "content_versions")


def _bad_xmp_pdf(path: Path) -> bytes:
    """Write a PDF with a readable DocInfo DOI and an undecodable XMP stream."""
    writer = PdfWriter()
    writer.add_blank_page(200, 200)
    writer.add_metadata({"/DOI": DOI})
    stream = StreamObject()
    stream._data = b"not flate data at all"
    stream[NameObject("/Type")] = NameObject("/Metadata")
    stream[NameObject("/Subtype")] = NameObject("/XML")
    stream[NameObject("/Filter")] = NameObject("/FlateDecode")
    writer._root_object[NameObject("/Metadata")] = writer._add_object(stream)
    with path.open("wb") as handle:
        writer.write(handle)
    return path.read_bytes()


def _provider_doc(doi: str = DOI) -> dict[str, Any]:
    return {
        "provider": "crossref",
        "doi": [doi],
        "title": ["Reviewed Paper"],
        "author": ["Carrozzo, F.", "Other, A."],
        "year": "2019",
        "pub": "Icarus",
        "volume": "318",
        "page": ["1"],
    }


class Case:
    """A wrongly identified PDF whose XMP cannot be parsed."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = (tmp_path / "library").resolve()
        self.root.mkdir()
        self.journals = tmp_path / "journals"
        self.catalog = PdfCatalog(tmp_path / "catalog.sqlite")
        self.data = _bad_xmp_pdf(self.root / "wrong.pdf")
        digest = hashlib.sha256(self.data).hexdigest()
        stat = (self.root / "wrong.pdf").stat()
        self.catalog.observe_file(
            self.root,
            "wrong.pdf",
            sha256=digest,
            size_bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            inode=stat.st_ino,
            device=stat.st_dev,
            metadata={
                "bibcode": "2000Wrong.001W",
                "title": ["Wrong"],
                "abstract": "WRONG",
                "citation_count": 9,
                "sha256": digest,
                "my_note": "keep me",
            },
            status="resolved",
        )
        record = self.catalog.get_file(self.root, "wrong.pdf")
        assert record is not None
        self.entry = RepairEntry(
            path=str(self.root / "wrong.pdf"),
            sha256=digest,
            location_id=record["location_id"],
            attachment_id=record["attachment_id"],
            document_id=record["document_id"],
            metadata_before={
                k: v for k, v in record["metadata"].items() if k != "my_note"
            },
            proposed_ads_record=_provider_doc(),
            reviewed_parser_errors=(XMP_ERROR,),
            audit_reference="audit-1",
        )

    def entry_with(self, **changes: Any) -> RepairEntry:
        return RepairEntry(**{**self.entry.__dict__, **changes})

    def dump(self) -> dict[str, list[tuple]]:
        conn = self.catalog._connection
        return {
            t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1")]
            for t in TABLES
        }

    def apply(self, entry: RepairEntry) -> Path:
        return apply_repairs(self.catalog, self.root, [entry], SETTINGS, self.journals)


def test_matching_doi_with_malformed_xmp_needs_no_manual_audit(tmp_path: Path) -> None:
    case = Case(tmp_path)
    plain = case.entry_with(reviewed_parser_errors=(), audit_reference="")
    plan = build_plan(case.catalog, case.root, [plain], SETTINGS)
    new_rel = plan["items"][0]["new_rel"]
    case.apply(plain)
    record = case.catalog.get_file(case.root, new_rel)
    proof = record["metadata"]["_adsfolio_identity"]
    assert proof["status"] == "verified"
    assert proof["parser_warnings"] == [XMP_ERROR]
    assert (case.root / new_rel).read_bytes() == case.data


def test_reviewed_repair_applies_and_undoes_exactly(tmp_path: Path) -> None:
    case = Case(tmp_path)
    before = case.dump()
    new_rel = build_plan(case.catalog, case.root, [case.entry], SETTINGS)["items"][0][
        "new_rel"
    ]
    journal = case.apply(case.entry)

    record = case.catalog.get_file(case.root, new_rel)
    assert record is not None
    meta = record["metadata"]
    proof = meta["_adsfolio_identity"]
    assert proof["status"] == "reviewed_repair"
    assert proof["authority"] == "manual_audit_manifest"
    assert proof["audit_reference"] == "audit-1"
    assert proof["reviewed_parser_errors"] == [XMP_ERROR]
    assert proof["sha256"] == case.entry.sha256
    assert proof["own_doi"] == [DOI]
    assert meta["title"] == ["Reviewed Paper"] and meta["my_note"] == "keep me"
    assert not {"abstract", "citation_count"} & set(meta)
    assert meta["_adsfolio_external_metadata"]["provider"] == "crossref"
    assert (case.root / new_rel).read_bytes() == case.data

    # A reviewed-repair stamp alone cannot replace a provider record.
    with pytest.raises(UnverifiedIdentityError):
        assert_mutation_allowed(
            case.root / new_rel, meta, expected_sha256=case.entry.sha256
        )

    undo_repair(case.catalog, journal)
    assert case.dump() == before
    assert (case.root / "wrong.pdf").read_bytes() == case.data
    assert json.loads(journal.read_text())["phase"] == "undone"


@pytest.mark.parametrize(
    "case_name",
    ["changed_errors", "extra_error", "no_audit_reference", "conflicting_id", "hash"],
)
def test_reviewed_repair_refusals_change_nothing(
    tmp_path: Path, case_name: str
) -> None:
    case = Case(tmp_path)
    entry = case.entry
    if case_name == "changed_errors":
        entry = case.entry_with(reviewed_parser_errors=("another error",))
    elif case_name == "extra_error":
        entry = case.entry_with(reviewed_parser_errors=(XMP_ERROR, "extra"))
    elif case_name == "no_audit_reference":
        entry = case.entry_with(audit_reference="  ")
    elif case_name == "hash":
        entry = case.entry_with(sha256="0" * 64)
    else:
        entry = case.entry_with(proposed_ads_record=_provider_doc("10.9999/other"))
    before = case.dump()
    with pytest.raises(IdentityRepairError):
        case.apply(entry)
    assert case.dump() == before
    assert (case.root / "wrong.pdf").read_bytes() == case.data
    assert not list(case.journals.glob("repair-*"))
