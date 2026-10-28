"""Edition identity and user-confirmed annotation merge workflows."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest
from PySide6.QtCore import QItemSelectionModel
from PySide6.QtWidgets import QDialog, QMenu
from test_gui_main_window_watch import _event, _window_factory

from adsfolio.config import Config
from adsfolio.gui.config import GuiConfig, RootEntry
from adsfolio.gui.edition_merge import (
    EditionMergeDialog,
    EditionMergeRequest,
    edition_candidates,
    edition_identifiers,
)
from adsfolio.gui.notifications import NoticeAction
from adsfolio.pdf.identifier import PdfIdentifier
from adsfolio.pdf.registry import CentralPdfRegistryState, merge_ads_metadata
from adsfolio.pdf.watch_session import WatchOutcome, WatchState


@pytest.fixture(autouse=True)
def isolate_config(monkeypatch, tmp_path: Path) -> None:
    """Keep edition tests away from the real catalog."""
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )


def make_pdf(path: Path) -> None:
    """Create a small real PDF for an edition review."""
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "A paper with annotations")
        document.save(path)


def record(path: str, bibcode: str, **metadata: object) -> dict:
    """Describe one PDF in the catalog."""
    return {
        "current_path": path,
        "relative_path": path,
        "bibcode": bibcode,
        "metadata": {"bibcode": bibcode, "title": ["Same title"], **metadata},
    }


def test_edition_matching_uses_ads_aliases_and_version_free_arxiv_identifiers() -> None:
    preprint = record(
        "preprint.pdf", "2026arXiv260627535B", doi=["10.48550/arXiv.2606.27535"]
    )
    published = record(
        "published.pdf", "2026ApJ..1009L..41B", identifier=["arXiv:2606.27535v2"]
    )

    candidates, linked = edition_candidates(published, [preprint, published])

    assert candidates == (preprint,)
    assert linked
    alias = record(
        "alias.pdf", "2026ApJ..1009L..41B", alternate_bibcode=["2026arXiv260627535B"]
    )
    assert edition_identifiers(alias) & edition_identifiers(preprint)


def test_same_title_does_not_claim_an_identifier_link() -> None:
    preprint = record("preprint.pdf", "2026arXiv260627535B")
    published = record("published.pdf", "2026ApJ..1009L..41B")
    unrelated = record("another.pdf", "2025ApJ...999...10A")

    records = [published, unrelated, preprint]
    for arrival in (published, preprint):
        assert edition_candidates(arrival, records) == ((), False)


def test_several_exact_matches_require_source_choice_and_exclude_others(
    qtbot, tmp_path: Path
) -> None:
    arrival = record("new.pdf", "2015Natur.525..500D", doi="10.1038/nature14869")
    first = record("first.pdf", "2015arXiv150100001D", doi="10.1038/nature14869")
    second = record(
        "second.pdf", "2015Natur.525..500D", identifier=["10.1038/nature14869"]
    )
    unrelated = record("unrelated.pdf", "2026arXiv260627535B")
    missing = record("missing.pdf", "2015arXiv150100003D", doi="10.1038/nature14869")
    missing["missing_at"] = "2026-01-01"

    candidates, linked = edition_candidates(
        arrival, [arrival, first, unrelated, second, missing]
    )

    assert candidates == (first, second)
    assert linked
    for name in ("new.pdf", "first.pdf", "second.pdf"):
        make_pdf(tmp_path / name)
    dialog = EditionMergeDialog(
        EditionMergeRequest(
            root=tmp_path,
            records=(arrival, *candidates),
            target=tmp_path / "new.pdf",
            choose_source=True,
            linked=linked,
        )
    )
    qtbot.addWidget(dialog)
    assert dialog.source is None
    assert not dialog.review_button.isEnabled()
    dialog.source_combo.setCurrentIndex(
        dialog.source_combo.findData(str(tmp_path / "second.pdf"))
    )
    assert dialog.review_button.isEnabled()


def test_identification_preserves_ads_edition_identifiers(tmp_path: Path) -> None:
    path = tmp_path / "published.pdf"
    from pdf_identity_fixtures import write_pdf

    write_pdf(path, arxiv="2606.27535")
    identifiers = ["arXiv:2606.27535", "10.3847/2041-8213/aea84e"]
    aliases = ["2026arXiv260627535B"]
    document = {
        "bibcode": "2026ApJ..1009L..41B",
        "identifier": identifiers,
        "alternate_bibcode": aliases,
    }

    result = PdfIdentifier().build_record(document, path)

    assert result["identifier"] == identifiers
    assert result["alternate_bibcode"] == aliases
    assert {"identifier", "alternate_bibcode"} <= set(PdfIdentifier.SEARCH_FIELDS)


def test_unlinked_edition_dialog_requires_source_choice_and_allows_swapping(
    qtbot, tmp_path: Path
) -> None:
    for name in ("preprint.pdf", "published.pdf"):
        make_pdf(tmp_path / name)
    request = EditionMergeRequest(
        root=tmp_path,
        records=(
            record("published.pdf", "2026ApJ..1009L..41B"),
            record("preprint.pdf", "2026arXiv260627535B"),
        ),
        target=tmp_path / "published.pdf",
        choose_source=True,
    )
    dialog = EditionMergeDialog(request)
    qtbot.addWidget(dialog)
    assert "no shared identifier in the available metadata" in dialog.explanation.text()
    assert dialog.target == tmp_path / "published.pdf"
    assert dialog.source is None
    assert not dialog.review_button.isEnabled()

    dialog.source_combo.setCurrentIndex(
        dialog.source_combo.findData(str(tmp_path / "preprint.pdf"))
    )

    assert dialog.review_button.isEnabled()
    dialog.target_combo.setCurrentIndex(
        dialog.target_combo.findData(str(tmp_path / "preprint.pdf"))
    )
    assert dialog.source_combo.findData(str(tmp_path / "preprint.pdf")) == -1
    assert not dialog.review_button.isEnabled()


@pytest.mark.parametrize("multiple", [False, True])
@pytest.mark.parametrize("accept", [False, True])
def test_watched_published_arrival_prompts_for_merge_after_pause(
    monkeypatch, qtbot, tmp_path: Path, accept: bool, multiple: bool
) -> None:
    root, other = tmp_path / "library", tmp_path / "other"
    root.mkdir()
    other.mkdir()
    for name in ("preprint.pdf", "new-0.pdf"):
        make_pdf(root / name)
    window, controller, _saves = _window_factory(
        monkeypatch,
        qtbot,
        GuiConfig(roots=[RootEntry(str(root)), RootEntry(str(other))]),
    )
    window._select_root(root)
    registry = window.registries[str(root)]
    state = CentralPdfRegistryState(registry)
    state.upsert_file(
        "preprint.pdf", record("preprint.pdf", "2026arXiv260627535B")["metadata"]
    )
    if multiple:
        make_pdf(root / "preprint-copy.pdf")
        state.upsert_file(
            "preprint-copy.pdf",
            record("preprint-copy.pdf", "2026arXiv260627535B")["metadata"],
        )
    published = record(
        "new-0.pdf",
        "2026ApJ..1009L..41B",
        identifier=["arXiv:2606.27535"],
    )
    state.upsert_file("new-0.pdf", published["metadata"])
    state.save()
    window.watch_current_action.trigger()
    window.show()
    controller.auto_ack_pause = False
    event = replace(
        _event(root, controller.session_id(root)), state=WatchState.RESOLVED
    )
    reviewed, opened = [], []

    def choose(dialog) -> int:
        opened.append(dialog.request)
        assert controller.pause_calls == [root.resolve()]
        assert dialog.target == root / "new-0.pdf"
        assert dialog.request.choose_source is multiple
        if multiple:
            assert dialog.source is None
            assert not dialog.review_button.isEnabled()
            dialog.source_combo.setCurrentIndex(
                dialog.source_combo.findData(str(root / "preprint.pdf"))
            )
        assert dialog.source == root / "preprint.pdf"
        assert dialog.review_button.isEnabled()
        return QDialog.Accepted if accept else QDialog.Rejected

    monkeypatch.setattr(EditionMergeDialog, "exec", choose)
    monkeypatch.setattr(
        window,
        "_review_annotation_pair",
        lambda target, source: reviewed.append((target, source)),
    )
    controller.outcome.emit(WatchOutcome(event, destination=root / "new-0.pdf"))
    assert window.notice_bar._current().action is NoticeAction.MERGE_EDITIONS
    # A notice click can review the arrival even if another library is selected.
    window._select_root(other)
    window.notice_bar.action_button.click()
    assert opened == []

    controller.paused.emit(controller.session_id(root))

    assert len(opened) == 1
    assert opened[0].linked
    assert reviewed == ([(root / "new-0.pdf", root / "preprint.pdf")] if accept else [])
    assert controller.resume_calls == [root.resolve()]
    assert window.current_root == other.resolve()
    assert window._edition_merge_target is None
    assert window.notice_bar.queued_keys() == (window._watch_notice_key(event),)
    assert controller.process_calls == []
    assert controller.retry_calls == []


def test_two_selected_pdfs_offer_merge_without_file_pickers(
    monkeypatch, qtbot, tmp_path: Path
) -> None:
    for name in ("preprint.pdf", "published.pdf"):
        make_pdf(tmp_path / name)
    window, _controller, _saves = _window_factory(
        monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(tmp_path))])
    )
    registry = window.registries[str(tmp_path)]
    for name, bibcode in (
        ("preprint.pdf", "2026arXiv260627535B"),
        ("published.pdf", "2026ApJ..1009L..41B"),
    ):
        registry.replace_file_metadata(tmp_path, name, {"bibcode": bibcode})
    window.refresh()
    window.show()
    qtbot.waitUntil(window.isVisible)
    selection = window.table.selectionModel()
    selection.clearSelection()
    for row in range(2):
        selection.select(
            window.model.index(row, 0),
            QItemSelectionModel.Select | QItemSelectionModel.Rows,
        )
    captured = []

    class NonBlockingMenu(QMenu):
        def exec(self, *_args) -> None:
            captured.extend(self.actions())

    monkeypatch.setattr("adsfolio.gui.main_window.QMenu", NonBlockingMenu)
    point = window.table.visualRect(window.model.index(0, 0)).center()
    window._table_menu(point)
    action = next(
        action for action in captured if action.text() == "Merge Selected PDFs…"
    )
    assert action.isEnabled()
    reviewed = []

    def choose(dialog) -> int:
        assert dialog.target == tmp_path / "published.pdf"
        assert dialog.source == tmp_path / "preprint.pdf"
        return QDialog.Accepted

    monkeypatch.setattr(EditionMergeDialog, "exec", choose)
    monkeypatch.setattr(
        window,
        "_review_annotation_pair",
        lambda target, source: reviewed.append((target, source)),
    )

    action.trigger()

    assert reviewed == [(tmp_path / "published.pdf", tmp_path / "preprint.pdf")]


@pytest.mark.parametrize("ambiguous", [False, True])
@pytest.mark.parametrize("field", ["alternate_bibcode", "identifier"])
def test_ads_bibcode_lookup_accepts_only_unique_aliases(
    ambiguous: bool, field: str
) -> None:
    old = "2026arXiv260627535B"
    alias = old if field == "alternate_bibcode" else "arXiv:2606.27535v2"
    published = {"bibcode": "2026ApJ..1009L..41B", field: [alias]}
    documents = [published]
    if ambiguous:
        documents.append({"bibcode": "2026ApJ..1009...42C", field: [alias]})
    client = SimpleNamespace(
        search=SimpleNamespace(query=lambda **kwargs: SimpleNamespace(docs=documents))
    )

    result = PdfIdentifier(client).query_docs_for_bibcodes([old])

    assert result[old] == (None if ambiguous else published)


def test_watch_arrival_automatically_opens_review_and_dismiss_stops_future_prompt(
    monkeypatch, qtbot, tmp_path: Path
) -> None:
    make_pdf(tmp_path / "preprint.pdf")
    make_pdf(tmp_path / "new-0.pdf")
    window, controller, _saves = _window_factory(
        monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(tmp_path))])
    )
    registry = window.registries[str(tmp_path)]
    registry.replace_file_metadata(
        tmp_path, "preprint.pdf", {"bibcode": "2026arXiv260627535B"}
    )
    registry.replace_file_metadata(
        tmp_path,
        "new-0.pdf",
        {"bibcode": "2026ApJ..1009L..41B", "identifier": ["arXiv:2606.27535"]},
    )
    window.watch_current_action.trigger()
    window.show()
    event = replace(
        _event(tmp_path, controller.session_id(tmp_path)), state=WatchState.RESOLVED
    )
    opened = []

    def cancel(dialog) -> int:
        opened.append(dialog.request)
        return QDialog.Rejected

    monkeypatch.setattr(EditionMergeDialog, "exec", cancel)
    controller.outcome.emit(WatchOutcome(event, destination=event.path))
    qtbot.waitUntil(lambda: len(opened) == 1)
    assert opened[0].linked
    assert controller.pause_calls == [tmp_path.resolve()]
    assert controller.resume_calls == [tmp_path.resolve()]
    assert window.notice_bar._current().action is NoticeAction.MERGE_EDITIONS
    window.notice_bar.dismiss_button.click()
    window._review_next_edition_merge()
    assert len(opened) == 1
    assert window.notice_bar.queued_keys() == ()


def test_merge_output_does_not_prompt_for_another_merge(
    monkeypatch, qtbot, tmp_path: Path
) -> None:
    make_pdf(tmp_path / "preprint.pdf")
    make_pdf(tmp_path / "new-0.pdf")
    window, controller, _saves = _window_factory(
        monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(tmp_path))])
    )
    registry = window.registries[str(tmp_path)]
    registry.replace_file_metadata(
        tmp_path, "preprint.pdf", {"bibcode": "2026arXiv260627535B"}
    )
    registry.replace_file_metadata(
        tmp_path,
        "new-0.pdf",
        {"bibcode": "2026ApJ..1009L..41B", "identifier": ["arXiv:2606.27535"]},
    )
    window.watch_current_action.trigger()
    event = replace(
        _event(tmp_path, controller.session_id(tmp_path)), state=WatchState.RESOLVED
    )
    window._merge_output_paths.add(event.path.resolve())

    controller.outcome.emit(WatchOutcome(event, destination=event.path))

    assert window.notice_bar._current().severity == "success"
    assert window._pending_edition_merges == {}


def test_ads_refresh_retains_version_identifiers_and_locked_fields() -> None:
    original = {"title": ["My title"], "_adsfolio_locked_fields": ["title"]}
    incoming = {
        "title": ["ADS title"],
        "identifier": ["arXiv:2606.27535"],
        "alternate_bibcode": ["2026arXiv260627535B"],
    }

    merged = merge_ads_metadata(original, incoming)

    assert merged["title"] == ["My title"]
    assert merged["identifier"] == incoming["identifier"]
    assert merged["alternate_bibcode"] == incoming["alternate_bibcode"]
    assert original == {"title": ["My title"], "_adsfolio_locked_fields": ["title"]}


@pytest.mark.parametrize("state", [WatchState.RESOLVED, WatchState.RENAMED])
def test_unrelated_preprint_does_not_prompt_for_watched_published_arrival(
    monkeypatch, qtbot, tmp_path: Path, state: WatchState
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    for name in ("preprint.pdf", "new-0.pdf"):
        make_pdf(root / name)
    window, controller, _saves = _window_factory(
        monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(root))])
    )
    registry = window.registries[str(root)]
    registry.replace_file_metadata(
        root, "preprint.pdf", {"bibcode": "2026arXiv260627535B"}
    )
    registry.replace_file_metadata(
        root,
        "new-0.pdf",
        {
            "bibcode": "2015Natur.525..500D",
            "doi": "10.1038/nature14869",
            "identifier": ["2015Natur.525..500D", "10.1038/nature14869"],
        },
    )
    window.watch_current_action.trigger()
    event = replace(_event(root, controller.session_id(root)), state=state)

    controller.outcome.emit(WatchOutcome(event, destination=event.path))

    assert window.notice_bar._current().action is None
    assert window.notice_bar._current().severity == "success"
    assert window._pending_edition_merges == {}
    assert window._edition_merge_target is None
    assert controller.pause_calls == []
