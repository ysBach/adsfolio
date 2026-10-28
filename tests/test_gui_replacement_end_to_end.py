"""Native filesystem events replace a PDF without a manual identification step."""

from pathlib import Path

import pytest
import requests
from test_pdf_replacement import DOC, make_pdf

from adsfolio.config import Config
from adsfolio.gui.watch import LibraryWatchController
from adsfolio.pdf.files import sha256_file
from adsfolio.pdf.identifier import PdfIdentifier
from adsfolio.pdf.identify_settings import IdentifySettings
from adsfolio.pdf.registry import open_registry_state
from adsfolio.pdf.rename_settings import RenameSettings
from adsfolio.pdf.watch_session import WatchState


def test_native_delete_add_and_atomic_replace_update_original_attachment(
    monkeypatch, qtbot, tmp_path: Path
) -> None:
    pytest.importorskip("watchdog")
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    root = tmp_path / "library"
    root.mkdir()
    path = root / "paper.pdf"
    make_pdf(path)
    state = open_registry_state(root)
    state.registry.upsert_ads_documents([DOC], source="test")
    metadata = PdfIdentifier(None).build_record(DOC, path)
    metadata["notes"] = "Reader note"
    state.upsert_file(path.name, metadata)
    original = state.registry.catalog.get_file(root, path.name)
    state.close()

    def no_network(*args, **kwargs):
        raise AssertionError("The exact DOI in the catalog must resolve locally")

    monkeypatch.setattr(requests.Session, "request", no_network)
    controller = LibraryWatchController()
    checking = open_registry_state(root)
    outcomes = []
    errors = []
    controller.outcome.connect(outcomes.append)
    controller.failed.connect(errors.append)
    controller.detected.connect(
        lambda event: controller.process(
            event, IdentifySettings(), RenameSettings(template="paper")
        )
    )
    with qtbot.waitSignal(controller.started, timeout=10000):
        controller.start_root(root, recursive=False)
    try:
        assert not errors
        path.unlink()
        arrival = root / "download.pdf"
        make_pdf(arrival, revision="new download")
        first_digest = sha256_file(arrival)
        qtbot.waitUntil(
            lambda: any(x.event.state is WatchState.RENAMED for x in outcomes),
            timeout=10000,
        )
        assert path.is_file() and not arrival.exists()
        assert sha256_file(path) == first_digest
        row = checking.registry.catalog.get_file(root, path.name)
        assert row["attachment_id"] == original["attachment_id"]
        assert row["metadata"]["notes"] == "Reader note"
        qtbot.wait(250)
        before = len(outcomes)
        temporary = root / "atomic.tmp"
        make_pdf(temporary, revision="atomic new edition")
        next_digest = sha256_file(temporary)
        temporary.replace(path)
        qtbot.waitUntil(
            lambda: (
                len(outcomes) > before
                and any(x.event.state is WatchState.RESOLVED for x in outcomes[before:])
            ),
            timeout=10000,
        )
        current = checking.registry.catalog.get_file(root, path.name)
        assert current["attachment_id"] == original["attachment_id"]
        assert current["sha256"] == next_digest
        assert current["metadata"]["notes"] == "Reader note"
        assert (
            len(checking.registry.catalog.content_versions(original["attachment_id"]))
            == 3
        )
        assert len(checking.registry.catalog.query_files(root=root)) == 1
        assert not errors
    finally:
        assert controller.stop_all()
        checking.close()
