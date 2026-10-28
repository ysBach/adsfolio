"""Replacement refuses unsafe destinations and leaves independent versions intact."""

from pathlib import Path

import pytest
from pdf_identity_fixtures import write_pdf
from test_pdf_replacement import Identifier, make_pdf, register

from adsfolio.config import Config
from adsfolio.pdf.registry import open_registry_state
from adsfolio.pdf.renamer import PdfRenamer
from adsfolio.pdf.tracked_move import move_tracked_file
from adsfolio.pdf.watcher import PdfWatcher


def test_dangling_symlink_cannot_be_overwritten(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    root = tmp_path / "library"
    root.mkdir()
    source, destination = root / "source.pdf", root / "target.pdf"
    write_pdf(source)
    destination.symlink_to(root / "missing.pdf")
    state = open_registry_state(root)
    state.upsert_file(source.name, {"current_filename": source.name})
    with pytest.raises(FileExistsError):
        move_tracked_file(state, source, destination)
    assert source.is_file() and destination.is_symlink()
    state.close()


def test_live_alias_in_another_root_is_not_an_absent_replacement(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    root, other = tmp_path / "library", tmp_path / "other"
    root.mkdir()
    other.mkdir()
    state, elsewhere = open_registry_state(root), open_registry_state(other)
    old = root / "old.pdf"
    make_pdf(old)
    original = register(state, old, notes="Keep with old content")
    alias = other / "alias.pdf"
    alias.write_bytes(old.read_bytes())
    register(elsewhere, alias)
    old.unlink()
    new = root / "new.pdf"
    make_pdf(new, revision="updated")
    PdfWatcher(Identifier(None), PdfRenamer()).process_new_pdf(
        new, state, scheme="new", apply=True
    )
    assert (
        state.registry.catalog.get_file(root, new.name)["attachment_id"]
        != original["attachment_id"]
    )
    assert alias.is_file()
    elsewhere.close()
    state.close()


def test_changed_one_of_two_live_aliases_gets_a_separate_attachment(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    root = tmp_path / "library"
    root.mkdir()
    state = open_registry_state(root)
    path = root / "paper.pdf"
    make_pdf(path)
    original = register(state, path, notes="Old bytes")
    alias = root / "alias.pdf"
    alias.write_bytes(path.read_bytes())
    register(state, alias)
    old_bytes = alias.read_bytes()
    make_pdf(path, revision="updated bytes")
    PdfWatcher(Identifier(None), PdfRenamer()).process_new_pdf(
        path, state, scheme="paper", apply=True
    )
    assert alias.read_bytes() == old_bytes
    assert (
        state.registry.catalog.get_file(root, alias.name)["attachment_id"]
        == original["attachment_id"]
    )
    assert (
        state.registry.catalog.get_file(root, path.name)["attachment_id"]
        != original["attachment_id"]
    )
    state.close()


def test_concurrent_arrivals_do_not_both_replace_one_absent_attachment(
    monkeypatch, tmp_path
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from adsfolio.pdf.catalog import PdfCatalog
    from adsfolio.pdf.identifier import PdfIdentifier

    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    root = tmp_path / "library"
    root.mkdir()
    state = open_registry_state(root)
    old = root / "old.pdf"
    make_pdf(old)
    original = register(state, old)
    database = state.registry.catalog.path
    old.unlink()
    arrivals = [root / "one.pdf", root / "two.pdf"]
    for path in arrivals:
        make_pdf(path, revision=path.name)
    state.close()
    barrier = Barrier(2)

    def run(path):
        catalog = PdfCatalog(database)
        metadata = PdfIdentifier(None).build_record(
            {
                "bibcode": "2026NatAs.tmp..145O",
                "doi": ["10.1038/example"],
                "title": ["Updated paper"],
            },
            path,
        )
        barrier.wait(timeout=10)
        try:
            return catalog.register_verified_replacement(root, path.name, metadata)
        finally:
            catalog.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, arrivals))
    accepted = [x for x in results if x is not None]
    assert len(accepted) == 1
    assert accepted[0].attachment_id == original["attachment_id"]
    assert all(path.is_file() for path in arrivals)


def test_changed_file_with_only_manual_old_identity_does_not_steal_notes(
    monkeypatch, tmp_path
):
    from adsfolio.pdf.files import sha256_file

    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    root = tmp_path / "library"
    root.mkdir()
    path = root / "paper.pdf"
    make_pdf(path)
    state = open_registry_state(root)
    state.upsert_file(
        path.name,
        {
            "current_filename": path.name,
            "sha256": sha256_file(path),
            "title": ["Manual old paper"],
            "notes": "Only for original",
        },
    )
    before = state.registry.catalog.get_file(root, path.name)
    make_pdf(path, revision="different new bytes")
    PdfWatcher(Identifier(None), PdfRenamer()).process_new_pdf(
        path, state, scheme="paper", apply=True
    )
    current = state.registry.catalog.get_file(root, path.name)
    assert current["attachment_id"] != before["attachment_id"]
    assert "notes" not in current["metadata"]
    state.close()
