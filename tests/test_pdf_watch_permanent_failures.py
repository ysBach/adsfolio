"""A PDF that fails the same way on every start is not processed again."""

from pathlib import Path

import pytest
from pypdf import PdfWriter

from adsfolio.config import ADSError, Config
from adsfolio.pdf.files import sha256_file
from adsfolio.pdf.identifier import PdfIdentifier
from adsfolio.pdf.identify_settings import IdentifySettings
from adsfolio.pdf.registry import open_registry_state
from adsfolio.pdf.rename_settings import RenameSettings
from adsfolio.pdf.renamer import MissingTemplateFieldsError, PdfRenamer
from adsfolio.pdf.watch_session import ArrivalIdentity, WatchEvent, WatchState
from adsfolio.pdf.watcher import PdfWatcher

PENDING = "_adsfolio_watch_pending"


def _pdf(path: Path, *, user_password: str | None = None) -> Path:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    if user_password is not None:
        writer.encrypt(user_password=user_password, owner_password="owner")
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def test_owner_password_pdf_is_readable_but_open_password_pdf_is_not(tmp_path):
    """Journal PDFs often limit printing with an owner password only."""
    identifier = PdfIdentifier(client=None)
    owner_only = _pdf(tmp_path / "owner.pdf", user_password="")
    open_password = _pdf(tmp_path / "locked.pdf", user_password="secret")

    assert identifier.pdf_is_readable(owner_only)
    assert not identifier.pdf_is_readable(open_password)


@pytest.fixture
def pending_arrival(monkeypatch, tmp_path):
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    root = tmp_path / "library"
    root.mkdir()
    source = _pdf(root / "download.pdf")
    state = open_registry_state(root)
    stat = source.stat()
    state.registry.upsert_file(
        root=root,
        relative_path=source.name,
        current_path=source.name,
        sha256=sha256_file(source),
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        status="unresolved",
        metadata={PENDING: True},
    )
    event = WatchEvent(
        root=root,
        session_id="test",
        identity=ArrivalIdentity(stat.st_dev, stat.st_ino, None, 0),
        path=source,
        state=WatchState.DETECTED,
    )
    yield root, source, state, event
    state.close()


def _process(monkeypatch, arrival, error: Exception):
    root, source, state, event = arrival

    def fail(self, **_kwargs):
        raise error

    monkeypatch.setattr(PdfWatcher, "process_new_pdf", fail)
    outcome = PdfWatcher(PdfIdentifier(client=None), PdfRenamer()).process_event(
        event, state, RenameSettings(template="{bibcode}"), IdentifySettings()
    )
    record = state.registry.find_file_by_current_path(root, source.name)
    return outcome, bool(record["metadata"].get(PENDING))


def test_template_failure_clears_the_mark_so_restart_skips_it(
    monkeypatch, pending_arrival
):
    outcome, pending = _process(
        monkeypatch, pending_arrival, MissingTemplateFieldsError(["bibcode"])
    )

    assert outcome.event.state is WatchState.FAILED
    assert "bibcode" in outcome.message
    assert not pending


def test_network_failure_keeps_the_mark_so_restart_retries_it(
    monkeypatch, pending_arrival
):
    outcome, pending = _process(monkeypatch, pending_arrival, ADSError("timeout"))

    assert outcome.event.state is WatchState.FAILED
    assert pending
