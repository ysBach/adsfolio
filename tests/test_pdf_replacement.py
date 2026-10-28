"""A verified replacement updates one absent attachment without losing history."""

from pathlib import Path

import pytest
from pypdf import PdfWriter

from adsfolio.config import Config
from adsfolio.pdf.catalog import LOCKED_METADATA_FIELDS_KEY
from adsfolio.pdf.identifier import PdfIdentifier
from adsfolio.pdf.registry import open_registry_state
from adsfolio.pdf.renamer import PdfRenamer
from adsfolio.pdf.watcher import PdfWatcher

DOC = {
    "bibcode": "2026NatAs.tmp..145O",
    "doi": ["10.1038/example"],
    "title": ["Updated paper"],
    "author": ["Opitom, C."],
    "year": "2026",
}


def make_pdf(
    path: Path, *, doi: str = "10.1038/example", revision: str = "old"
) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=300)
    writer.add_metadata({"/DOI": doi, "/Title": revision})
    writer.write(path)


@pytest.fixture
def library(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    root = tmp_path / "library"
    root.mkdir()
    state = open_registry_state(root)
    state.registry.upsert_ads_documents([DOC], source="test")
    yield root, state
    state.close()


def register(state, path: Path, doc: dict = DOC, **extra):
    record = PdfIdentifier(None).build_record(doc, path)
    record.update(extra)
    state.upsert_file(path.name, {**record, "current_filename": path.name})
    return state.registry.catalog.get_file(path.parent, path.name)


class Identifier(PdfIdentifier):
    def resolve_many(self, paths, **kwargs):
        return {p: self.build_record(DOC, p) for p in paths}


def test_deleted_old_pdf_replaced_under_download_name(library):
    root, state = library
    old = root / "Opitom.pdf"
    make_pdf(old)
    original = register(
        state, old, notes="Keep my notes", **{LOCKED_METADATA_FIELDS_KEY: ["title"]}
    )
    history = state.registry.catalog.content_versions(original["attachment_id"])
    old.unlink()
    new = root / "download.pdf"
    make_pdf(new, revision="updated")
    new_bytes = new.read_bytes()
    watcher = PdfWatcher(Identifier(None), PdfRenamer())
    result = watcher.process_new_pdf(new, state, scheme="Opitom", apply=True)
    assert result is not None
    current = state.registry.catalog.get_file(root, "Opitom.pdf")
    assert current["attachment_id"] == original["attachment_id"]
    assert current["metadata"]["notes"] == "Keep my notes"
    assert current["metadata"][LOCKED_METADATA_FIELDS_KEY] == ["title"]
    assert old.read_bytes() == new_bytes and not new.exists()
    versions = state.registry.catalog.content_versions(current["attachment_id"])
    assert {x["sha256"] for x in history} < {x["sha256"] for x in versions}
    assert len(state.registry.catalog.query_files(root=root)) == 1


def test_existing_stale_destination_row_does_not_block_replacement(library):
    root, state = library
    old = root / "Opitom.pdf"
    make_pdf(old)
    original = register(state, old)
    old.unlink()
    new = root / "download.pdf"
    make_pdf(new, revision="updated")
    # The GUI may have identified the arrival before a complete rescan.
    register(state, new)
    result = PdfWatcher(Identifier(None), PdfRenamer()).process_new_pdf(
        new, state, scheme="Opitom", apply=True
    )
    assert result is not None
    current = state.registry.catalog.get_file(root, "Opitom.pdf")
    assert current["attachment_id"] == original["attachment_id"]
    assert len(state.registry.catalog.query_files(root=root)) == 1


def test_replacement_at_same_path_preserves_history_and_user_metadata(library):
    root, state = library
    path = root / "paper.pdf"
    make_pdf(path)
    original = register(
        state, path, notes="Keep", **{LOCKED_METADATA_FIELDS_KEY: ["title"]}
    )
    make_pdf(path, revision="new bytes")
    PdfWatcher(Identifier(None), PdfRenamer()).process_new_pdf(
        path, state, scheme="paper", apply=True
    )
    current = state.registry.catalog.get_file(root, "paper.pdf")
    assert current["attachment_id"] == original["attachment_id"]
    assert current["metadata"]["notes"] == "Keep"
    assert len(state.registry.catalog.content_versions(current["attachment_id"])) == 2
    assert not current["metadata"].get("_adsfolio_metadata_stale")


def test_replacement_does_not_absorb_an_existing_other_edition(library):
    root, state = library
    old = root / "old.pdf"
    make_pdf(old)
    original = register(state, old, notes="Edition-specific note")
    new = root / "new.pdf"
    make_pdf(new, revision="another edition")
    PdfWatcher(Identifier(None), PdfRenamer()).process_new_pdf(
        new, state, scheme="new", apply=True
    )
    assert (
        state.registry.catalog.get_file(root, "new.pdf")["attachment_id"]
        != original["attachment_id"]
    )
    assert old.exists() and new.exists()
    assert len(state.registry.catalog.query_files(root=root)) == 2


def test_two_absent_editions_do_not_select_one_arbitrarily(library):
    root, state = library
    attachments = set()
    for name in ["a.pdf", "b.pdf"]:
        path = root / name
        make_pdf(path, revision=name)
        row = register(state, path, notes=name)
        attachments.add(row["attachment_id"])
    for name in ["a.pdf", "b.pdf"]:
        (root / name).unlink()
    new = root / "new.pdf"
    make_pdf(new, revision="updated")
    PdfWatcher(Identifier(None), PdfRenamer()).process_new_pdf(
        new, state, scheme="new", apply=True
    )
    row = state.registry.catalog.get_file(root, "new.pdf")
    assert row["attachment_id"] not in attachments
    assert "notes" not in row["metadata"]


def test_cached_exact_doi_identifies_replacement_without_network(library):
    root, state = library
    path = root / "download.pdf"
    make_pdf(path, revision="download")
    result = PdfWatcher(PdfIdentifier(None), PdfRenamer()).process_new_pdf(
        path, state, scheme="identified", apply=True
    )
    assert result is not None and result["bibcode"] == DOC["bibcode"]
    assert (root / "identified.pdf").exists()


def test_same_path_new_paper_does_not_inherit_old_notes_or_locks(library):
    root, state = library
    path = root / "paper.pdf"
    make_pdf(path)
    original = register(
        state, path, notes="Only old paper", **{LOCKED_METADATA_FIELDS_KEY: ["title"]}
    )
    different = {
        **DOC,
        "bibcode": "2025ApJ...900..100A",
        "doi": ["10.1038/different"],
        "title": ["Different paper"],
    }
    make_pdf(path, doi="10.1038/different", revision="different paper")
    register(state, path, doc=different)
    current = state.registry.catalog.get_file(root, "paper.pdf")
    assert current["attachment_id"] != original["attachment_id"]
    assert "notes" not in current["metadata"]
    assert not current["metadata"].get(LOCKED_METADATA_FIELDS_KEY)
    assert current["bibcode"] == different["bibcode"]
    assert len(state.registry.catalog.content_versions(original["attachment_id"])) == 1


def test_reused_download_name_does_not_disqualify_absent_old_attachment(library):
    root, state = library
    download = root / "download.pdf"
    make_pdf(download)
    original = register(state, download, notes="Keep history")
    renamed = root / "old-name.pdf"
    state.registry.catalog.mark_missing_locations(root, set())
    missing_at = state.registry.catalog.get_file(root, download.name)["missing_at"]
    download.rename(renamed)
    register(state, renamed)
    renamed.unlink()
    make_pdf(download, revision="updated download")
    PdfWatcher(Identifier(None), PdfRenamer()).process_new_pdf(
        download, state, scheme="final", apply=True
    )
    row = state.registry.catalog.get_file(root, "final.pdf")
    assert row["attachment_id"] == original["attachment_id"]
    assert row["metadata"]["notes"] == "Keep history"
    historical = state.registry.catalog._connection.execute(
        "SELECT missing_at FROM file_locations WHERE id = ?", (original["location_id"],)
    ).fetchone()
    assert historical[0] == missing_at


def test_absorbing_fresh_arrival_does_not_make_future_hash_lookup_ambiguous(library):
    root, state = library
    old = root / "old.pdf"
    make_pdf(old)
    original = register(state, old)
    old.unlink()
    new = root / "new.pdf"
    make_pdf(new, revision="new bytes")
    # Pre-observe an arrival, as discovery can do before watcher processing.
    stat = new.stat()
    from adsfolio.pdf.files import sha256_file

    state.registry.catalog.observe_file(
        root,
        new.name,
        sha256=sha256_file(new),
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
    )
    PdfWatcher(Identifier(None), PdfRenamer()).process_new_pdf(
        new, state, scheme="new", apply=True
    )
    digest = sha256_file(new)
    rows = state.registry.catalog._connection.execute(
        "SELECT DISTINCT attachment_id FROM content_versions WHERE sha256 = ?",
        (digest,),
    ).fetchall()
    assert [r[0] for r in rows] == [original["attachment_id"]]
    assert not state.registry.catalog._connection.execute(
        "SELECT 1 FROM file_locations f JOIN content_versions v ON v.id=f.current_version_id WHERE f.attachment_id != v.attachment_id"
    ).fetchone()


def test_deleted_same_name_returns_as_new_version_after_missing_scan(library):
    root, state = library
    path = root / "paper.pdf"
    make_pdf(path)
    original = register(state, path, notes="Reader note")
    path.unlink()
    state.registry.catalog.mark_missing_locations(root, set())
    make_pdf(path, revision="replacement after deletion")
    PdfWatcher(Identifier(None), PdfRenamer()).process_new_pdf(
        path, state, scheme="paper", apply=True
    )
    row = state.registry.catalog.get_file(root, path.name)
    assert row["attachment_id"] == original["attachment_id"]
    assert row["metadata"]["notes"] == "Reader note"
    assert len(state.registry.catalog.content_versions(original["attachment_id"])) == 2
