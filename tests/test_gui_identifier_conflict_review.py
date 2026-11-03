"""Review only conflicts returned by an explicit identifier lookup."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from pypdf import PdfWriter
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QInputDialog, QMessageBox
from test_gui_main_window_watch import _ads_refresh_job_factory, _window_factory

from adsfolio.config import Config
from adsfolio.gui.config import GuiConfig, RootEntry
from adsfolio.pdf.doi_providers import DoiProvider
from adsfolio.pdf.files import sha256_file
from adsfolio.pdf.identify_settings import IdentifySettings
from adsfolio.pdf.rename_settings import RenameSettings


@pytest.fixture(autouse=True)
def isolate_token_lookup(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv(Config.ENV_VAR, raising=False)
    monkeypatch.setattr(
        Config, "get_token_paths", classmethod(lambda cls: [tmp_path / "token"])
    )


@pytest.fixture
def lookup(monkeypatch, qtbot, tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    path = root / "paper.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_metadata({"/arXiv": "arXiv:2301.00001"})
    writer.write(path)
    original = path.read_bytes()
    window, controller, _saves = _window_factory(
        monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(root))])
    )
    window.table.setCurrentIndex(window.model.index(0, 0))
    window.watch_current_action.trigger()
    jobs = []
    _ads_refresh_job_factory(monkeypatch, jobs)
    prompts = []

    def enter_identifier(*args, **kwargs):
        prompts.append(args[2])
        return "10.1234/right", True

    monkeypatch.setattr(QInputDialog, "getText", enter_identifier)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *args, **kwargs: pytest.fail("Early question")
    )
    monkeypatch.setattr(QMessageBox, "exec", lambda *args: pytest.fail("Early review"))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[1:]))
    payload = {
        "identifier": "10.1234/right",
        "identifier_option": "--identifier",
        "title": "Retrieved paper",
        "author": "Example, A.",
        "identifiers": "DOI: 10.1234/right; Bibcode: 2024Test...1A",
        "conflict": "The PDF contains arXiv:2301.00001.",
        "pdf_sha256": sha256_file(path),
        "identity_fingerprint": "identity-fingerprint",
        "review_digest": "review-digest",
        "catalog_digest": "catalog-digest",
        "year": "2024",
    }
    return SimpleNamespace(
        root=root,
        path=path,
        original=original,
        window=window,
        controller=controller,
        jobs=jobs,
        prompts=prompts,
        warnings=warnings,
        payload=payload,
    )


def start_lookup(lookup) -> None:
    assert lookup.window._update_from_identifier("")
    assert len(lookup.jobs) == 1
    assert "--offer-conflict-review" in lookup.jobs[0].argv
    assert "--confirm-conflicting-identity" not in lookup.jobs[0].argv
    assert lookup.warnings == []


def send_conflict(job, payload) -> None:
    job.output.emit("PDF_IDENTITY_CONFLICT_JSON:" + json.dumps(payload))


def finish(job, code: int = 0) -> None:
    job.running = False
    job.finished.emit(code)


def argv_value(job, option: str) -> str:
    return job.argv[job.argv.index(option) + 1]


@pytest.mark.parametrize(
    ("answer", "exit_code"),
    [(QMessageBox.Yes, 0), (QMessageBox.Yes, 1), (QMessageBox.No, 0)],
)
def test_review_after_lookup_and_retry_bound_to_the_result(
    lookup, monkeypatch, answer, exit_code
) -> None:
    questions = []

    def review(box):
        questions.append(box.text())
        assert box.textFormat() == Qt.PlainText
        assert box.defaultButton() is box.button(QMessageBox.No)
        return answer

    monkeypatch.setattr(QMessageBox, "exec", review)
    start_lookup(lookup)
    assert "Conflicting PDF identifiers block" not in lookup.prompts[0]
    assert "Select the correct paper from the ADS matches." in lookup.prompts[0]
    assert "If no reference matches, enter a DOI or Bibcode." in lookup.prompts[0]
    assert questions == []
    first = lookup.jobs[0]
    send_conflict(first, lookup.payload)
    assert questions == []
    finish(first, exit_code)

    assert len(questions) == 1
    for key in ("conflict", "title", "author", "identifiers", "year"):
        assert lookup.payload[key] in questions[0]
    assert lookup.path.read_bytes() == lookup.original
    if answer == QMessageBox.No:
        assert len(lookup.jobs) == 1
        assert lookup.controller.resume_calls == [lookup.root.resolve()]
        return

    assert len(lookup.jobs) == 2
    retry = lookup.jobs[1]
    assert retry.working_directory == lookup.root.resolve()
    assert argv_value(retry, "--identifier") == lookup.payload["identifier"]
    assert argv_value(retry, "--relative-path") == "paper.pdf"
    assert "--confirm-conflicting-identity" in retry.argv
    assert "--offer-conflict-review" not in retry.argv
    for option, key in (
        ("--expected-sha256", "pdf_sha256"),
        ("--expected-identity", "identity_fingerprint"),
        ("--expected-review-digest", "review_digest"),
        ("--expected-catalog-digest", "catalog_digest"),
    ):
        assert argv_value(retry, option) == lookup.payload[key]
    assert lookup.controller.pause_calls == [lookup.root.resolve()] * 2
    finish(retry)
    assert lookup.controller.resume_calls == [lookup.root.resolve()] * 2
    assert len(questions) == 1
    assert lookup.path.read_bytes() == lookup.original


@pytest.mark.parametrize(
    "change", ["changed", "missing", "record_missing", "record_moved", "root_missing"]
)
def test_review_does_not_retry_when_the_target_changes(lookup, monkeypatch, change):
    removed_registry = None

    def review(box):
        nonlocal removed_registry
        registry = lookup.window.registries[str(lookup.root)]
        if change == "changed":
            lookup.path.write_bytes(lookup.original + b"\nchanged")
        elif change == "missing":
            lookup.path.unlink()
        elif change == "record_missing":
            monkeypatch.setattr(registry, "find_location_by_id", lambda _id: None)
        elif change == "record_moved":
            find = registry.find_location_by_id

            def moved(location_id):
                return {**find(location_id), "current_path": "moved.pdf"}

            monkeypatch.setattr(registry, "find_location_by_id", moved)
        else:
            removed_registry = lookup.window.registries.pop(str(lookup.root))
        return QMessageBox.Yes

    monkeypatch.setattr(QMessageBox, "exec", review)
    start_lookup(lookup)
    send_conflict(lookup.jobs[0], lookup.payload)
    finish(lookup.jobs[0])
    if removed_registry is not None:
        lookup.window.registries[str(lookup.root)] = removed_registry

    assert len(lookup.jobs) == 1
    assert len(lookup.warnings) == 1
    expected = "changed" if change == "changed" else "available"
    assert expected in lookup.warnings[0][1]
    assert lookup.controller.resume_calls == [lookup.root.resolve()]


@pytest.mark.parametrize(
    "field",
    [
        "identifier",
        "identifier_option",
        "identifiers",
        "conflict",
        "pdf_sha256",
        "identity_fingerprint",
        "review_digest",
        "catalog_digest",
    ],
)
def test_incomplete_conflict_payload_never_opens_a_review(lookup, field):
    start_lookup(lookup)
    incomplete = dict(lookup.payload)
    del incomplete[field]
    send_conflict(lookup.jobs[0], incomplete)
    finish(lookup.jobs[0])
    assert len(lookup.jobs) == 1
    assert lookup.window._conflict_review_payload is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("identifier_option", "--all"),
        ("title", []),
        ("author", 123),
        ("conflict", "   "),
        ("pdf_sha256", "not-a-sha256"),
        ("catalog_digest", ""),
        ("year", True),
    ],
)
def test_malformed_conflict_payload_never_opens_a_review(lookup, field, value):
    start_lookup(lookup)
    malformed = dict(lookup.payload)
    malformed[field] = value
    send_conflict(lookup.jobs[0], malformed)
    finish(lookup.jobs[0])
    assert len(lookup.jobs) == 1
    assert lookup.window._conflict_review_payload is None


@pytest.mark.parametrize("lines", ["invalid_json", "duplicate_key", "duplicate_line"])
def test_invalid_or_repeated_conflict_lines_are_rejected(lookup, lines):
    start_lookup(lookup)
    job = lookup.jobs[0]
    if lines == "invalid_json":
        job.output.emit("PDF_IDENTITY_CONFLICT_JSON:{")
    elif lines == "duplicate_key":
        encoded = json.dumps(lookup.payload)
        job.output.emit(
            'PDF_IDENTITY_CONFLICT_JSON:{"identifier":"duplicate",' + encoded[1:]
        )
    else:
        send_conflict(job, lookup.payload)
        send_conflict(job, lookup.payload)
    finish(job)
    assert len(lookup.jobs) == 1


@pytest.mark.parametrize("value", [None, "", "   "])
def test_review_displays_fallbacks_for_missing_title_and_author(
    lookup, monkeypatch, value
):
    questions = []
    monkeypatch.setattr(
        QMessageBox,
        "exec",
        lambda box: questions.append(box.text()) or QMessageBox.No,
    )
    start_lookup(lookup)
    payload = {**lookup.payload, "author": value}
    del payload["title"]
    send_conflict(lookup.jobs[0], payload)
    finish(lookup.jobs[0])
    assert len(questions) == 1
    assert "Unknown title" in questions[0]
    assert "Unknown author" in questions[0]
    assert payload["conflict"] in questions[0]
    assert payload["identifiers"] in questions[0]


@pytest.mark.parametrize("year", [None, 2024, "2024"])
def test_publication_year_is_optional_and_shown_when_supplied(
    lookup, monkeypatch, year
):
    questions = []
    monkeypatch.setattr(
        QMessageBox,
        "exec",
        lambda box: questions.append(box.text()) or QMessageBox.No,
    )
    start_lookup(lookup)
    payload = dict(lookup.payload)
    if year is None:
        del payload["year"]
    else:
        payload["year"] = year
    send_conflict(lookup.jobs[0], payload)
    finish(lookup.jobs[0])
    assert len(questions) == 1
    assert ("Year: 2024" in questions[0]) is (year is not None)


def test_selected_ambiguous_candidate_can_offer_a_conflict_review(lookup, monkeypatch):
    questions = []
    monkeypatch.setattr(
        QInputDialog, "getItem", lambda *args, **kwargs: (args[3][0], True)
    )
    monkeypatch.setattr(
        QMessageBox,
        "exec",
        lambda box: questions.append(box.text()) or QMessageBox.Yes,
    )
    start_lookup(lookup)
    candidate = {
        "bibcode": "2024Test...1A",
        "first_author": "Example, A.",
        "title": "Retrieved paper",
        "pdf_sha256": lookup.payload["pdf_sha256"],
        "identity_fingerprint": lookup.payload["identity_fingerprint"],
        "review_digest": lookup.payload["review_digest"],
    }
    lookup.jobs[0].output.emit("ADS_MATCH_CANDIDATES_JSON:" + json.dumps([candidate]))
    finish(lookup.jobs[0])
    assert len(lookup.jobs) == 2
    selected = lookup.jobs[1]
    assert "--offer-conflict-review" in selected.argv
    assert argv_value(selected, "--bibcode") == candidate["bibcode"]
    assert questions == []
    payload = {
        **lookup.payload,
        "identifier_option": "--bibcode",
        "identifier": candidate["bibcode"],
        "year": 2024,
    }
    send_conflict(selected, payload)
    finish(selected)
    assert len(questions) == 1
    assert "2024" in questions[0]
    assert len(lookup.jobs) == 3
    retry = lookup.jobs[2]
    assert argv_value(retry, "--bibcode") == candidate["bibcode"]
    assert argv_value(retry, "--expected-catalog-digest") == payload["catalog_digest"]
    assert "--confirm-conflicting-identity" in retry.argv
    finish(retry)
    assert lookup.controller.pause_calls == [lookup.root.resolve()] * 3
    assert lookup.controller.resume_calls == [lookup.root.resolve()] * 3
    assert lookup.path.read_bytes() == lookup.original


def test_cancelled_job_cannot_leak_a_conflict_to_the_next_job(lookup):
    start_lookup(lookup)
    first = lookup.jobs[0]
    send_conflict(first, lookup.payload)
    lookup.window._cancel_job()
    finish(first, -1)
    assert lookup.window._update_from_identifier("")
    assert len(lookup.jobs) == 2
    send_conflict(first, lookup.payload)
    finish(lookup.jobs[1])
    assert len(lookup.jobs) == 2
    assert lookup.path.read_bytes() == lookup.original


def test_other_ads_refreshes_cannot_offer_conflict_review(lookup):
    lookup.window._start_ads_refresh(
        "--bibcode", "2024Test...1A", "--relative-path", "paper.pdf", root=lookup.root
    )
    assert len(lookup.jobs) == 1
    assert "--offer-conflict-review" not in lookup.jobs[0].argv
    send_conflict(lookup.jobs[0], lookup.payload)
    finish(lookup.jobs[0])
    assert len(lookup.jobs) == 1


@pytest.mark.parametrize(
    ("catalog_hash", "has_notes"),
    [(False, False), (False, True), (True, False)],
    ids=["new-record", "unhashed-notes", "hashed-record"],
)
def test_gui_review_uses_the_cli_protocol_without_changing_pdf_contents(
    monkeypatch, qtbot, tmp_path: Path, catalog_hash: bool, has_notes: bool
):
    from adsfolio.pdf.cli import pdf
    from adsfolio.pdf.registry import open_registry_state

    root = tmp_path / "library"
    root.mkdir()
    source = root / "paper.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_metadata({"/DOI": "10.5555/wrong"})
    writer.write(source)
    original_hash = sha256_file(source)
    if catalog_hash or has_notes:
        state = open_registry_state(root)
        try:
            state.upsert_file(
                source.name,
                {
                    "current_filename": source.name,
                    "sha256": original_hash if catalog_hash else "",
                    "status": "unresolved",
                    **({"notes": "Keep this note"} if has_notes else {}),
                },
            )
        finally:
            state.close()
    document = {
        "doi": ["10.5555/correct"],
        "title": ["Correct paper"],
        "author": ["Roe, Alice"],
        "year": "2021",
    }
    Config.save_rename_settings(RenameSettings(template="{authors}_{year}-{title}"))
    monkeypatch.setattr(
        Config,
        "load_identify_settings",
        classmethod(lambda cls: IdentifySettings(providers=(DoiProvider.CROSSREF,))),
    )
    monkeypatch.setattr(
        "adsfolio.pdf.identifier.fetch_crossref_doi", lambda _doi: dict(document)
    )
    window, _controller, _saves = _window_factory(
        monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(root))])
    )
    window.table.setCurrentIndex(window.model.index(0, 0))
    registry = window.registries[str(root)]
    before = registry.find_file_by_relative_path(root, source.name)
    assert bool(before["sha256"]) is catalog_hash
    jobs = []
    _ads_refresh_job_factory(monkeypatch, jobs)
    monkeypatch.setattr(
        QInputDialog, "getText", lambda *args, **kwargs: ("10.5555/correct", True)
    )
    questions = []
    monkeypatch.setattr(
        QMessageBox, "exec", lambda box: questions.append(box.text()) or QMessageBox.Yes
    )
    assert window._update_from_identifier("")
    assert len(jobs) == 1
    assert questions == []

    def run_job(job):
        arguments = job.argv[job.argv.index("refresh-ads") :]
        result = CliRunner().invoke(pdf, arguments)
        for line in result.output.splitlines():
            job.output.emit(line)
        return result

    preview = run_job(jobs[0])
    assert preview.exit_code == 0, preview.output
    assert questions == []
    assert sha256_file(source) == original_hash
    current = window.registries[str(root)].find_file_by_relative_path(root, source.name)
    if has_notes:
        assert current["metadata"]["notes"] == "Keep this note"
        assert current["metadata"]["_adsfolio_metadata_stale"]
    else:
        assert current["metadata"] == before["metadata"]
    finish(jobs[0], preview.exit_code)
    assert len(questions) == 1
    assert "10.5555/wrong" in questions[0]
    assert "10.5555/correct" in questions[0]
    assert "2021" in questions[0]
    assert len(jobs) == 2

    retry = run_job(jobs[1])
    assert retry.exit_code == 0, retry.output
    finish(jobs[1], retry.exit_code)
    records = window.registries[str(root)].query_files()
    assert len(records) == 1
    assert records[0]["metadata"]["title"] == document["title"]
    if has_notes:
        assert records[0]["metadata"]["notes"] == "Keep this note"
        assert not records[0]["metadata"].get("_adsfolio_metadata_stale")
    assert records[0]["current_path"] != source.name
    assert sha256_file(root / records[0]["current_path"]) == original_hash
