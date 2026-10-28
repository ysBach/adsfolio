"""Apply a reviewed candidate directly while preserving the PDF and its guards."""

from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from pypdf import PdfWriter
from PySide6.QtWidgets import QInputDialog, QMessageBox
from test_gui_main_window_watch import _window_factory

from adsfolio.config import Config
from adsfolio.gui.config import GuiConfig, RootEntry
from adsfolio.pdf.cli import pdf
from adsfolio.pdf.files import sha256_file
from adsfolio.pdf.identity import IDENTITY_KEY, target_identity
from adsfolio.pdf.watch_session import (
    ArrivalIdentity,
    WatchEvent,
    WatchOutcome,
    WatchState,
)

DOC = {
    "bibcode": "2013A&A...552A.137R",
    "doi": ["10.5555/example"],
    "title": ["Example candidate"],
    "author": ["Example, A."],
    "year": "2013",
}


def candidate_window(monkeypatch, qtbot, tmp_path, *, conflict=False):
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    monkeypatch.setattr(
        "adsfolio.pubcache.cache_path", lambda: tmp_path / "publications.json"
    )
    root = tmp_path / "library"
    root.mkdir()
    path = root / "paper.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=400)
    if conflict:
        writer.add_metadata({"/DOI": "10.5555/another-paper"})
    with path.open("wb") as stream:
        writer.write(stream)
    window, controller, _ = _window_factory(
        monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(root))])
    )
    window.watch_current_action.trigger()
    stat = path.stat()
    event = WatchEvent(
        root=root,
        session_id=controller.session_id(root),
        identity=ArrivalIdentity(stat.st_dev, stat.st_ino, None, 0),
        path=path,
        state=WatchState.FAILED,
    )
    controller.outcome.emit(
        WatchOutcome(
            event,
            message="Candidate needs review",
            candidate_bibcode=DOC["bibcode"],
            candidate_sha256=sha256_file(path),
            candidate_fingerprint=target_identity(DOC).fingerprint,
            candidate_confirmable=True,
        )
    )
    window.show()
    window.notifications_button.click()
    assert window.notice_bar.use_id_button.isVisible()
    return window, controller, root, path, event


@pytest.mark.parametrize(
    "result_kind", ["success", "changed_pdf", "changed_candidate", "conflict"]
)
def test_use_this_id_runs_update_and_rename_without_another_dialog(
    monkeypatch, qtbot, tmp_path, result_kind
):
    window, controller, root, path, event = candidate_window(
        monkeypatch, qtbot, tmp_path, conflict=result_kind == "conflict"
    )
    original = path.read_bytes()
    notice_key = window._watch_notice_key(event)
    started = []

    def unexpected_dialog(*args, **kwargs):
        pytest.fail("Use This ID must not ask for the same ID or consent again.")

    monkeypatch.setattr(QInputDialog, "getText", unexpected_dialog)
    monkeypatch.setattr(QMessageBox, "question", unexpected_dialog)

    def start(*args, **kwargs):
        started.append((args, kwargs))
        window._refresh_selection_id = kwargs["selection_id"]
        window._ads_refresh_root = kwargs["root"]
        window._pending_ads_refresh = (
            kwargs["root"],
            args,
            kwargs["selection_id"],
            kwargs["ambiguous_relative_path"],
        )

    monkeypatch.setattr(window, "_start_ads_refresh", start)
    window.notice_bar.use_id_button.click()
    assert len(started) == 1
    args, kwargs = started[0]
    assert args[:4] == ("--bibcode", DOC["bibcode"], "--relative-path", "paper.pdf")
    assert "--confirm-identity" in args
    assert args[args.index("--expected-sha256") + 1] == sha256_file(path)
    assert (
        args[args.index("--expected-identity") + 1] == target_identity(DOC).fingerprint
    )
    assert kwargs["root"] == root
    assert notice_key in window.notice_bar.queued_keys()

    class Search:
        def query(self, **_kwargs):
            document = dict(DOC)
            if result_kind == "changed_candidate":
                document["doi"] = ["10.5555/changed-record"]
            if result_kind == "changed_pdf":
                path.write_bytes(original + b"\n%external modification\n")
            return SimpleNamespace(docs=[document], num_found=1)

    monkeypatch.setattr(
        "adsfolio.pdf.cli._get_client", lambda _ctx: SimpleNamespace(search=Search())
    )
    result = CliRunner().invoke(pdf, ["refresh-ads", str(root), *args])
    window._pending_ads_refresh = None
    window._job_finished(result.exit_code)
    assert controller.retry_calls == controller.process_calls == []
    saved = window.registries[str(root)].find_location_by_id(kwargs["selection_id"])
    if result_kind == "success":
        assert result.exit_code == 0, result.output
        assert saved["bibcode"] == DOC["bibcode"]
        assert saved["status"] == "resolved"
        assert saved["metadata"][IDENTITY_KEY]["status"] == "manual_confirmed"
        assert (root / saved["current_path"]).read_bytes() == original
        assert saved["current_path"] != path.name
        assert notice_key not in window.notice_bar.queued_keys()
    else:
        assert result.exit_code != 0, result.output
        assert not saved.get("bibcode")
        assert notice_key in window.notice_bar.queued_keys()
        assert path.is_file()
        expected = (
            original + b"\n%external modification\n"
            if result_kind == "changed_pdf"
            else original
        )
        assert path.read_bytes() == expected
        if result_kind == "changed_candidate":
            assert "ADS candidate changed after review" in result.output


@pytest.mark.parametrize("condition", ["changed", "obsolete", "busy"])
def test_use_this_id_does_not_start_for_a_stale_or_busy_notice(
    monkeypatch, qtbot, tmp_path, condition
):
    window, controller, root, path, event = candidate_window(
        monkeypatch, qtbot, tmp_path
    )
    started, warnings = [], []
    monkeypatch.setattr(
        window, "_start_ads_refresh", lambda *a, **kw: started.append(a)
    )
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warnings.append(a))
    if condition == "changed":
        path.write_bytes(path.read_bytes() + b"\n%changed\n")
    elif condition == "obsolete":
        controller.active[root] = "new-session"
    else:
        window.job = SimpleNamespace(running=False)
    before = path.read_bytes()
    window.notice_bar.use_id_button.click()
    assert started == []
    assert path.read_bytes() == before
    assert window._watch_notice_key(event) in window.notice_bar.queued_keys()
    assert bool(warnings) is (condition == "changed")
    window.job = None
