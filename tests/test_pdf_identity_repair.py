import copy
import hashlib
import json
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import FreeText
from pypdf.generic import NameObject, StreamObject

from adsfolio.pdf.catalog import PdfCatalog
from adsfolio.pdf.identity import (
    IdentityValidationError,
    UnverifiedIdentityError,
    assert_mutation_allowed,
)
from adsfolio.pdf.identity_repair import (
    IdentityRepairError,
    RepairEntry,
    _acquire_lock,
    _release_lock,
    apply_repairs,
    build_plan,
    load_manifest,
    undo_repair,
)
from adsfolio.pdf.rename_settings import RenameSettings

SETTINGS = RenameSettings(
    template="{authors}_{bibcode}-{title}", max_authors=2, authors_when_truncated=1
)
TABLES = ("attachments", "file_locations", "documents", "content_versions")


def test_repair_lock_excludes_another_process_until_released(tmp_path: Path) -> None:
    code = textwrap.dedent(
        """\
        import sys
        from pathlib import Path
        from adsfolio.pdf.identity_repair import (
            IdentityRepairError, _acquire_lock, _release_lock,
        )

        try:
            lock = _acquire_lock(Path(sys.argv[1]))
        except IdentityRepairError:
            sys.exit(2)
        _release_lock(lock)
        """
    )

    def probe() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", code, str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    lock = _acquire_lock(tmp_path)
    try:
        blocked = probe()
        assert blocked.returncode == 2, blocked.stderr
    finally:
        _release_lock(lock)
    available = probe()
    assert available.returncode == 0, available.stderr


def _make_pdf(path: Path, doi: str, notes: int = 0) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(200, 200)
    writer.add_metadata({"/DOI": doi})
    for index in range(notes):
        writer.add_annotation(
            page_number=0,
            annotation=FreeText(text=f"note {index}", rect=(10, 10 + index, 100, 40)),
        )
    with path.open("wb") as handle:
        writer.write(handle)
    return path.read_bytes()


def _make_bad_xmp_pdf(path: Path, doi: str) -> bytes:
    """Write a PDF with a readable DocInfo DOI and an undecodable XMP stream."""
    writer = PdfWriter()
    writer.add_blank_page(200, 200)
    writer.add_metadata({"/DOI": doi})
    stream = StreamObject()
    stream._data = b"not flate data at all"
    stream[NameObject("/Type")] = NameObject("/Metadata")
    stream[NameObject("/Subtype")] = NameObject("/XML")
    stream[NameObject("/Filter")] = NameObject("/FlateDecode")
    writer._root_object[NameObject("/Metadata")] = writer._add_object(stream)
    with path.open("wb") as handle:
        writer.write(handle)
    return path.read_bytes()


def _doc(index: int) -> dict[str, Any]:
    bibcode = f"2021A&A...{600 + index}A..99T"
    return {
        "bibcode": bibcode,
        "doi": [f"10.1000/paper{index}"],
        "identifier": [bibcode, f"10.1000/paper{index}"],
        "title": [f"Correct Title {index}"],
        "author": ["Takahashi, J.", "Itoh, Y.", "Matsuo, T."],
        "author_count": 3,
        "year": "2021",
        "bibstem": ["A&A"],
        "volume": "653",
        "page": ["A99"],
        "pub": "Astronomy and Astrophysics",
        "abstract": f"Correct abstract {index}",
        "citation_count": 40 + index,
    }


def _wrong_metadata(index: int, sha256: str) -> dict[str, Any]:
    return {
        "bibcode": f"2000Wrong.{index:03d}W",
        "title": ["Wrong paper"],
        "author": ["Wrong, A."],
        "year": "2000",
        "abstract": "WRONG abstract",
        "citation_count": 999,
        "provider": "OldProvider",
        "arxiv_id": "0000.00000",
        "sha256": sha256,
        "my_note": f"keep me {index}",
        "_adsfolio_cite_key": "wrong2000",
        "resolution_strategy": "filename_doi",
        "original_bibcode": "1999Old.....1O",
        "arxiv_backup": "0000.00000",
        "upgrade_status": "upgraded",
        "upgraded_at": "2026-01-01T00:00:00+00:00",
        "planned_filename": "old-plan.pdf",
        "source": "legacy",
    }


def _dump(catalog: PdfCatalog) -> dict[str, list[tuple]]:
    conn = catalog._connection
    return {
        table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1")]
        for table in TABLES
    }


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Env:
    """A catalog with wrongly identified PDFs and one unrelated tracked PDF."""

    def __init__(self, tmp_path: Path, count: int = 1, bad_xmp: bool = False) -> None:
        self.root = (tmp_path / "library").resolve()
        self.root.mkdir()
        self.journals = tmp_path / "journals"
        self.catalog = PdfCatalog(tmp_path / "catalog.sqlite")
        self.entries: list[RepairEntry] = []
        self.original: dict[str, bytes] = {}
        for index in range(count):
            name = f"wrong_{index}.pdf"
            doi = f"10.1000/paper{index}"
            if bad_xmp:
                data = _make_bad_xmp_pdf(self.root / name, doi)
            else:
                data = _make_pdf(self.root / name, doi, notes=index + 1)
            self.original[name] = data
            self.entries.append(self._track(name, index, _wrong_metadata))
        self.unrelated = "other.pdf"
        _make_pdf(self.root / self.unrelated, "10.1000/other")
        self._observe(self.unrelated, {"bibcode": "2001Other.....1O", "title": ["T"]})

    def _observe(self, name: str, metadata: dict[str, Any]) -> None:
        path = self.root / name
        stat = path.stat()
        self.catalog.observe_file(
            self.root,
            name,
            sha256=_sha(path),
            size_bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            inode=stat.st_ino,
            device=stat.st_dev,
            metadata=metadata,
            status="resolved",
        )

    def _track(self, name: str, index: int, make: Any) -> RepairEntry:
        digest = _sha(self.root / name)
        self._observe(name, make(index, digest))
        record = self.catalog.get_file(self.root, name)
        assert record is not None
        before = {k: v for k, v in record["metadata"].items() if k != "my_note"}
        return RepairEntry(
            path=str(self.root / name),
            sha256=digest,
            location_id=record["location_id"],
            attachment_id=record["attachment_id"],
            document_id=record["document_id"],
            metadata_before=before,
            proposed_ads_record=_doc(index),
        )

    def apply(self) -> Path:
        return apply_repairs(
            self.catalog, self.root, self.entries, SETTINGS, self.journals
        )

    def journal_phase(self) -> str:
        (journal,) = self.journals.glob("repair-*.json")
        return json.loads(journal.read_text())["phase"]


def _annotation_count(path: Path) -> int:
    return len(PdfReader(path).pages[0].get("/Annots") or [])


def test_apply_then_undo_round_trip(tmp_path: Path) -> None:
    env = Env(tmp_path, count=2)
    before = _dump(env.catalog)
    plan = build_plan(env.catalog, env.root, env.entries, SETTINGS)
    old_names = [item["old_rel"] for item in plan["items"]]
    journal = env.apply()

    for index, item in enumerate(plan["items"]):
        new_path = env.root / item["new_rel"]
        assert item["new_rel"] != item["old_rel"]
        assert not (env.root / old_names[index]).exists()
        assert new_path.read_bytes() == env.original[old_names[index]]
        assert _annotation_count(new_path) == index + 1
        record = env.catalog.get_file(env.root, item["new_rel"])
        entry = env.entries[index]
        assert record is not None
        assert (record["location_id"], record["attachment_id"]) == (
            entry.location_id,
            entry.attachment_id,
        )
        assert record["document_id"] != entry.document_id
        meta = record["metadata"]
        assert meta["bibcode"] == _doc(index)["bibcode"]
        assert meta["abstract"] == f"Correct abstract {index}"
        assert meta["citation_count"] == 40 + index
        assert meta["author_count"] == 3
        assert not {"provider", "arxiv_id"} & set(meta)
        history = {
            "resolution_strategy",
            "original_bibcode",
            "arxiv_backup",
            "upgrade_status",
            "upgraded_at",
            "planned_filename",
            "source",
        }
        assert not history & set(meta)
        assert meta["_adsfolio_cite_key"] == "wrong2000"
        assert meta["my_note"] == f"keep me {index}"
        assert meta["_adsfolio_identity"]["status"] == "verified"
        assert meta["_adsfolio_identity"]["sha256"] == entry.sha256
        old_document = env.catalog.get_document(entry.document_id)
        assert old_document is not None
        assert old_document["bibcode"] == f"2000Wrong.{index:03d}W"
        assert old_document["metadata"]["abstract"] == "WRONG abstract"

    unrelated = env.catalog.get_file(env.root, env.unrelated)
    assert unrelated is not None and unrelated["bibcode"] == "2001Other.....1O"

    undo_repair(env.catalog, journal)
    assert _dump(env.catalog) == before
    for name, data in env.original.items():
        assert (env.root / name).read_bytes() == data
    assert env.journal_phase() == "undone"


def test_existing_target_is_kept_and_numbered_name_is_used(tmp_path: Path) -> None:
    env = Env(tmp_path)
    target = (
        env.root
        / build_plan(env.catalog, env.root, env.entries, SETTINGS)["items"][0][
            "new_rel"
        ]
    )
    published = _make_pdf(target, "10.1000/published")

    plan = build_plan(env.catalog, env.root, env.entries, SETTINGS)
    new_rel = plan["items"][0]["new_rel"]
    assert new_rel == f"{target.stem}_1.pdf"
    env.apply()

    assert target.read_bytes() == published
    assert (env.root / new_rel).read_bytes() == env.original["wrong_0.pdf"]
    assert env.catalog.get_file(env.root, target.name) is None


def test_manifest_round_trip_and_conflicting_identity_changes_nothing(
    tmp_path: Path,
) -> None:
    env = Env(tmp_path)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "path": e.path,
                    "sha256": e.sha256,
                    "location_id": e.location_id,
                    "attachment_id": e.attachment_id,
                    "document_id": e.document_id,
                    "metadata_before": e.metadata_before,
                    "proposed_ads_record": e.proposed_ads_record,
                }
                for e in env.entries
            ]
        )
    )
    assert load_manifest(manifest) == env.entries

    before = _dump(env.catalog)
    conflicting = copy.deepcopy(env.entries[0].proposed_ads_record)
    conflicting["doi"] = ["10.9999/other"]
    conflicting["identifier"] = ["10.9999/other"]
    bad_identity = RepairEntry(
        **{**env.entries[0].__dict__, "proposed_ads_record": conflicting}
    )
    wrong_hash = RepairEntry(**{**env.entries[0].__dict__, "sha256": "0" * 64})
    wrong_ids = RepairEntry(**{**env.entries[0].__dict__, "attachment_id": 99999})
    for entry, error in (
        (bad_identity, IdentityValidationError),
        (wrong_hash, IdentityRepairError),
        (wrong_ids, IdentityRepairError),
    ):
        with pytest.raises(error):
            apply_repairs(env.catalog, env.root, [entry], SETTINGS, env.journals)
    assert _dump(env.catalog) == before
    assert (env.root / "wrong_0.pdf").read_bytes() == env.original["wrong_0.pdf"]
    assert not list(env.journals.glob("repair-*"))


def test_locked_automatic_field_blocks_unless_authorized(tmp_path: Path) -> None:
    env = Env(tmp_path)
    record = env.catalog.get_file(env.root, "wrong_0.pdf")
    assert record is not None
    env.catalog.replace_file_metadata(
        env.root,
        "wrong_0.pdf",
        {**record["metadata"], "_adsfolio_locked_fields": ["abstract"]},
    )
    with pytest.raises(IdentityRepairError, match="locked"):
        build_plan(env.catalog, env.root, env.entries, SETTINGS)
    allowed = RepairEntry(
        **{**env.entries[0].__dict__, "authorized_locked_fields": ("abstract",)}
    )
    item = build_plan(env.catalog, env.root, [allowed], SETTINGS)["items"][0]
    assert item["metadata_after"]["_adsfolio_locked_fields"] == ["abstract"]


@pytest.mark.parametrize("method", ["replace_file_metadata", "move_file"])
def test_catalog_failure_rolls_back_every_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    env = Env(tmp_path, count=2)
    before = _dump(env.catalog)
    original = getattr(PdfCatalog, method)
    calls = {"count": 0}

    def flaky(self: PdfCatalog, *args: Any, **kwargs: Any) -> Any:
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("injected failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(PdfCatalog, method, flaky)
    with pytest.raises(IdentityRepairError, match="injected failure"):
        env.apply()
    monkeypatch.undo()

    assert _dump(env.catalog) == before
    for name, data in env.original.items():
        assert (env.root / name).read_bytes() == data
    assert sorted(p.name for p in env.root.iterdir()) == sorted(
        [*env.original, env.unrelated]
    )
    assert env.journal_phase() == "rolledback"


def test_file_move_failure_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = Env(tmp_path, count=2)
    before = _dump(env.catalog)
    import adsfolio.pdf.identity_repair as repair

    original = repair._move_no_overwrite
    calls = {"count": 0}

    def flaky(source: Path, destination: Path) -> None:
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("disk error")
        original(source, destination)

    monkeypatch.setattr(repair, "_move_no_overwrite", flaky)
    with pytest.raises(IdentityRepairError):
        env.apply()
    monkeypatch.undo()
    assert _dump(env.catalog) == before
    assert env.journal_phase() == "rolledback"


def test_undo_refuses_after_intervening_edit_or_collision(tmp_path: Path) -> None:
    env = Env(tmp_path)
    plan = build_plan(env.catalog, env.root, env.entries, SETTINGS)
    new_rel = plan["items"][0]["new_rel"]
    journal = env.apply()

    record = env.catalog.get_file(env.root, new_rel)
    assert record is not None
    env.catalog.replace_file_metadata(
        env.root, new_rel, {**record["metadata"], "my_note": "edited later"}
    )
    edited = _dump(env.catalog)
    with pytest.raises(IdentityRepairError, match="changed since the repair"):
        undo_repair(env.catalog, journal)
    assert _dump(env.catalog) == edited
    assert (env.root / new_rel).exists()

    env.catalog.replace_file_metadata(env.root, new_rel, record["metadata"])
    occupied = env.root / "wrong_0.pdf"
    occupied.write_bytes(b"someone else")
    with pytest.raises(IdentityRepairError, match="occupied"):
        undo_repair(env.catalog, journal)
    assert occupied.read_bytes() == b"someone else"
    assert (env.root / new_rel).read_bytes() == env.original["wrong_0.pdf"]


def test_preexisting_target_document_and_sibling_attachment_are_unchanged(
    tmp_path: Path,
) -> None:
    env = Env(tmp_path)
    sibling = "sibling.pdf"
    _make_pdf(env.root / sibling, "10.1000/paper0")
    env._observe(
        sibling,
        {
            "bibcode": _doc(0)["bibcode"],
            "doi": "10.1000/paper0",
            "title": ["Sibling title"],
            "abstract": "Sibling abstract",
            "citation_count": 5,
            "my_note": "sibling note",
        },
    )
    conn = env.catalog._connection

    def rows() -> tuple[Any, Any]:
        doc = conn.execute(
            "SELECT * FROM documents WHERE bibcode = ?", (_doc(0)["bibcode"],)
        ).fetchone()
        att = env.catalog.get_file(env.root, sibling)
        assert doc is not None and att is not None
        return tuple(doc), att["attachment_id"]

    document_before, sibling_id = rows()
    sibling_row = tuple(
        conn.execute("SELECT * FROM attachments WHERE id = ?", (sibling_id,)).fetchone()
    )
    before = _dump(env.catalog)

    journal = env.apply()

    assert rows()[0] == document_before
    assert (
        tuple(
            conn.execute(
                "SELECT * FROM attachments WHERE id = ?", (sibling_id,)
            ).fetchone()
        )
        == sibling_row
    )
    new_rel = build_plan_names(env)
    record = env.catalog.get_file(env.root, new_rel)
    assert record is not None
    assert record["document_id"] == document_before[0]
    assert record["metadata"]["abstract"] == "Correct abstract 0"
    assert record["metadata"]["citation_count"] == 40

    undo_repair(env.catalog, journal)
    assert _dump(env.catalog) == before


def build_plan_names(env: Env) -> str:
    (journal,) = env.journals.glob("repair-*.json")
    return json.loads(journal.read_text())["items"][0]["new_rel"]


def test_undo_restores_root_row_timestamp(tmp_path: Path) -> None:
    env = Env(tmp_path)
    conn = env.catalog._connection
    before = tuple(conn.execute("SELECT * FROM library_roots").fetchall()[0])
    journal = env.apply()
    undo_repair(env.catalog, journal)
    assert tuple(conn.execute("SELECT * FROM library_roots").fetchall()[0]) == before


def test_missing_hard_link_support_is_rejected_without_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = Env(tmp_path)
    before = _dump(env.catalog)

    def no_link(*args: Any, **kwargs: Any) -> None:
        raise OSError("links not supported")

    monkeypatch.setattr("os.link", no_link)
    with pytest.raises(IdentityRepairError, match="hard links"):
        env.apply()
    monkeypatch.undo()
    assert _dump(env.catalog) == before
    assert (env.root / "wrong_0.pdf").read_bytes() == env.original["wrong_0.pdf"]
    assert env.journal_phase() == "rolledback"


def _crossref_doc(index: int = 0) -> dict[str, Any]:
    return {
        "provider": "crossref",
        "doi": [f"10.1000/paper{index}"],
        "title": [f"Crossref Title {index}"],
        "author": ["Curie, Marie", "Curie, Pierre"],
        "year": "2021",
        "pub": "Some Journal",
        "volume": "7",
        "page": ["12"],
    }


def _with_record(entry: RepairEntry, record: dict[str, Any]) -> RepairEntry:
    return RepairEntry(**{**entry.__dict__, "proposed_ads_record": record})


def test_doi_provider_record_without_bibcode_round_trip(tmp_path: Path) -> None:
    env = Env(tmp_path)
    env.entries = [_with_record(env.entries[0], _crossref_doc())]
    before = _dump(env.catalog)
    plan = build_plan(env.catalog, env.root, env.entries, SETTINGS)
    item = plan["items"][0]
    assert item["after_bibcode"] == ""
    assert item["after_status"] == "unresolved"
    assert item["new_rel"] == "CurieM+CurieP_2021-Crossref_Title_0.pdf"

    journal = env.apply()
    record = env.catalog.get_file(env.root, item["new_rel"])
    assert record is not None
    meta = record["metadata"]
    assert record["status"] == "unresolved"
    assert meta["bibcode"] == "" and meta["title"] == ["Crossref Title 0"]
    assert meta["_adsfolio_external_metadata"]["provider"] == "crossref"
    assert meta["_adsfolio_identity"]["status"] == "verified"
    assert meta["_adsfolio_identity"]["sha256"] == env.entries[0].sha256
    assert not {"abstract", "citation_count"} & set(meta)
    assert meta["my_note"] == "keep me 0"
    document = env.catalog.get_document(record["document_id"])
    assert document is not None and document["bibcode"] is None
    assert record["document_id"] != env.entries[0].document_id
    old = env.catalog.get_document(env.entries[0].document_id)
    assert old is not None and old["bibcode"] == "2000Wrong.000W"

    undo_repair(env.catalog, journal)
    assert _dump(env.catalog) == before
    assert (env.root / "wrong_0.pdf").read_bytes() == env.original["wrong_0.pdf"]


def test_doi_provider_repair_keeps_existing_doi_document_and_sibling(
    tmp_path: Path,
) -> None:
    env = Env(tmp_path)
    env.entries = [_with_record(env.entries[0], _crossref_doc())]
    _make_pdf(env.root / "sibling.pdf", "10.1000/paper0")
    env._observe(
        "sibling.pdf",
        {
            "doi": "10.1000/paper0",
            "title": ["Sibling title"],
            "abstract": "Sibling abstract",
            "_adsfolio_external_metadata": {"provider": "crossref"},
            "status": "unresolved",
        },
    )
    sibling = env.catalog.get_file(env.root, "sibling.pdf")
    assert sibling is not None and sibling["document_id"] is not None
    conn = env.catalog._connection
    document_before = tuple(
        conn.execute(
            "SELECT * FROM documents WHERE id = ?", (sibling["document_id"],)
        ).fetchone()
    )
    before = _dump(env.catalog)

    journal = env.apply()

    plan_name = json.loads(journal.read_text())["items"][0]["new_rel"]
    record = env.catalog.get_file(env.root, plan_name)
    assert record is not None
    assert record["document_id"] == sibling["document_id"]
    assert (
        tuple(
            conn.execute(
                "SELECT * FROM documents WHERE id = ?", (sibling["document_id"],)
            ).fetchone()
        )
        == document_before
    )
    assert env.catalog.get_file(env.root, "sibling.pdf") == sibling

    undo_repair(env.catalog, journal)
    assert _dump(env.catalog) == before


def test_record_without_bibcode_needs_identifier_and_full_metadata(
    tmp_path: Path,
) -> None:
    env = Env(tmp_path)
    thin = _crossref_doc()
    thin.pop("title")
    for record in (thin, {**_crossref_doc(), "doi": []}):
        with pytest.raises(IdentityRepairError, match="without bibcode"):
            build_plan(
                env.catalog,
                env.root,
                [_with_record(env.entries[0], record)],
                SETTINGS,
            )


XMP_ERROR = "XMP metadata is not well-formed"


def _reviewed(entry: RepairEntry, **changes: Any) -> RepairEntry:
    fields = {
        "reviewed_parser_errors": (XMP_ERROR,),
        "audit_reference": "audit-1",
        **changes,
    }
    return RepairEntry(**{**entry.__dict__, **fields})


def test_reviewed_parser_error_repair_round_trip(tmp_path: Path) -> None:
    env = Env(tmp_path, bad_xmp=True)
    automatic_plan = build_plan(env.catalog, env.root, env.entries, SETTINGS)
    assert (
        automatic_plan["items"][0]["metadata_after"]["_adsfolio_identity"]["status"]
        == "verified"
    )
    env.entries = [_reviewed(env.entries[0])]
    before = _dump(env.catalog)
    plan = build_plan(env.catalog, env.root, env.entries, SETTINGS)
    new_rel = plan["items"][0]["new_rel"]
    journal = env.apply()

    record = env.catalog.get_file(env.root, new_rel)
    assert record is not None
    meta = record["metadata"]
    proof = meta["_adsfolio_identity"]
    assert proof["status"] == "reviewed_repair"
    assert proof["authority"] == "manual_audit_manifest"
    assert proof["audit_reference"] == "audit-1"
    assert proof["reviewed_parser_errors"] == [XMP_ERROR]
    assert proof["sha256"] == env.entries[0].sha256
    assert meta["bibcode"] == _doc(0)["bibcode"] and meta["my_note"] == "keep me 0"
    assert meta["abstract"] == "Correct abstract 0"
    assert "citation_count" in meta and "provider" not in meta
    assert (env.root / new_rel).read_bytes() == env.original["wrong_0.pdf"]
    # A reviewed-repair stamp alone cannot replace a provider record.
    with pytest.raises(UnverifiedIdentityError):
        assert_mutation_allowed(
            env.root / new_rel, meta, expected_sha256=env.entries[0].sha256
        )

    undo_repair(env.catalog, journal)
    assert _dump(env.catalog) == before
    assert (env.root / "wrong_0.pdf").read_bytes() == env.original["wrong_0.pdf"]


@pytest.mark.parametrize(
    "case", ["changed_errors", "no_audit_reference", "unknown_own_id", "wrong_hash"]
)
def test_reviewed_parser_error_refusals_change_nothing(
    tmp_path: Path, case: str
) -> None:
    env = Env(tmp_path, bad_xmp=True)
    entry = env.entries[0]
    if case == "changed_errors":
        entry = _reviewed(entry, reviewed_parser_errors=("something else",))
    elif case == "no_audit_reference":
        entry = _reviewed(entry, audit_reference=" ")
    elif case == "wrong_hash":
        entry = _reviewed(entry, sha256="0" * 64)
    else:
        other = {**_doc(0), "doi": ["10.9999/other"], "identifier": ["10.9999/other"]}
        entry = _with_record(_reviewed(entry), other)
    before = _dump(env.catalog)
    with pytest.raises(IdentityRepairError):
        apply_repairs(env.catalog, env.root, [entry], SETTINGS, env.journals)
    assert _dump(env.catalog) == before
    assert (env.root / "wrong_0.pdf").read_bytes() == env.original["wrong_0.pdf"]
    assert not list(env.journals.glob("repair-*"))
