"""Selected table rows export as BibTeX, asking for missing or shared keys."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QInputDialog,
    QMessageBox,
)

from adsfolio.citekeys import KeyPolicy, KeyStyle
from adsfolio.config import Config
from adsfolio.gui.bibtex_export import CitationKeyDialog
from adsfolio.gui.library import FileQuery
from adsfolio.gui.main_window import MainWindow
from adsfolio.references import CitationKeyError, LocalReferences


def _crossref(doi: str, title: str) -> dict:
    return {
        "bibcode": "",
        "doi": [doi],
        "title": [title],
        "author": ["Roe, Alice"],
        "year": "2021",
        "pub": "Journal of Examples",
        "_adsfolio_external_metadata": {"provider": "crossref", "doi": doi},
    }


@pytest.fixture
def window(qtbot: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    Config.save_key_policy(KeyPolicy(other=KeyStyle.SCHOLAR))
    root = tmp_path / "pdfs"
    root.mkdir()
    created = MainWindow()
    qtbot.addWidget(created)
    created._register_root(root)
    created._select_root(root)
    catalog = created.registries[str(root)].catalog
    for name, doi, title, digest in (
        ("a.pdf", "10.5555/a", "Dust", "a"),
        ("b.pdf", "10.5555/b", "Dust grains", "b"),
    ):
        (root / name).write_bytes(b"%PDF-1.4\n" + digest.encode())
        catalog.observe_file(
            root,
            name,
            sha256=digest * 64,
            size_bytes=10,
            mtime_ns=20,
            metadata=_crossref(doi, title),
            status="unresolved",
        )
    created.refresh()
    yield created
    created.close()


def _records(window: MainWindow) -> list[dict]:
    catalog = window.registries[str(window.current_root)].catalog
    return [catalog.get_file(window.current_root, name) for name in ("a.pdf", "b.pdf")]


def test_shared_scholar_keys_ask_once_then_copy(window, monkeypatch):
    asked: list[dict[int, str]] = []

    def answer(dialog: CitationKeyDialog) -> int:
        asked.append(dialog.keys())
        last_row = dialog.table.rowCount() - 1
        dialog.table.item(last_row, 3).setText("roe2021grains")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(CitationKeyDialog, "exec", answer)
    records = _records(window)
    window._copy_records_bibtex(records)
    assert len(asked) == 1
    assert sorted(asked[0].values()) == ["roe2021dust", "roe2021dust"]
    text = QApplication.clipboard().text()
    assert "{roe2021dust," in text and "{roe2021grains," in text
    # The typed key is saved, so the next export asks nothing.
    window._copy_records_bibtex(records)
    assert len(asked) == 1


def test_cancel_leaves_the_clipboard_unchanged(window, monkeypatch):
    QApplication.clipboard().setText("before")
    monkeypatch.setattr(
        CitationKeyDialog, "exec", lambda dialog: QDialog.DialogCode.Rejected
    )
    window._copy_records_bibtex(_records(window))
    assert QApplication.clipboard().text() == "before"


def test_key_dialog_saves_only_complete_unique_valid_keys(window):
    catalog = window.registries[str(window.current_root)].catalog
    service = LocalReferences(catalog, KeyPolicy(other=KeyStyle.MANUAL))
    ids = [record["document_id"] for record in _records(window)]
    with pytest.raises(CitationKeyError) as caught:
        service.export_bibtex(ids)
    dialog = CitationKeyDialog(None, caught.value)
    save = dialog.buttons.button(QDialogButtonBox.StandardButton.Save)
    assert not save.isEnabled()
    dialog.table.item(0, 3).setText("Same")
    dialog.table.item(1, 3).setText("Same")
    assert not save.isEnabled()
    dialog.table.item(1, 3).setText("has space")
    assert not save.isEnabled()
    dialog.table.item(1, 3).setText("Other")
    assert save.isEnabled()
    dialog.close()


def test_rows_without_paper_details_are_reported(window):
    window._copy_records_bibtex([{"relative_path": "x.pdf", "document_id": None}])
    assert "Identify them first" in window.status.currentMessage()


def test_key_settings_dialog_previews_and_returns_the_policy(qtbot):
    from adsfolio.gui.bibtex_export import CitationKeySettingsDialog

    dialog = CitationKeySettingsDialog(None, KeyPolicy())
    qtbot.addWidget(dialog)
    assert dialog.other_style.findData(KeyStyle.BIBCODE) == -1
    assert "2020ApJ...900....1K" in dialog.preview.text()
    assert "10.5555/example.2021" in dialog.preview.text()
    assert not dialog.template.isEnabled()
    dialog.other_style.setCurrentIndex(dialog.other_style.findData(KeyStyle.SCHOLAR))
    assert "roe2021theory" in dialog.preview.text()
    dialog.ads_style.setCurrentIndex(dialog.ads_style.findData(KeyStyle.TEMPLATE))
    assert dialog.template.isEnabled()
    assert "KimS2020" in dialog.preview.text()
    dialog.template.setText("{no_such_field}")
    save = dialog.buttons.button(QDialogButtonBox.StandardButton.Save)
    assert not save.isEnabled()
    dialog.template.setText("{year}{first_author_initials}")
    assert save.isEnabled()
    assert dialog.policy() == KeyPolicy(
        ads=KeyStyle.TEMPLATE,
        other=KeyStyle.SCHOLAR,
        template="{year}{first_author_initials}",
    )


def test_key_settings_action_saves_the_policy(window, monkeypatch):
    from adsfolio.gui.bibtex_export import CitationKeySettingsDialog

    def choose(dialog: CitationKeySettingsDialog) -> int:
        dialog.other_style.setCurrentIndex(dialog.other_style.findData(KeyStyle.MANUAL))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(CitationKeySettingsDialog, "exec", choose)
    window.citation_keys_action.trigger()
    assert Config.load_key_policy().other is KeyStyle.MANUAL


@pytest.mark.parametrize("all_papers", [False, True])
@pytest.mark.parametrize("limited_view", ["filtered", "paged"])
def test_export_as_bibtex_respects_scope_and_uses_all_current_rows(
    window, monkeypatch, tmp_path, all_papers, limited_view
) -> None:
    """All exports include hidden rows; selection exports contain only selected PDFs."""
    catalog = window.registries[str(window.current_root)].catalog
    service = LocalReferences(catalog)
    records = _records(window)
    for record, key in zip(records, ("first-paper", "second-paper")):
        service.set_cite_key(record["document_id"], key)
    other = tmp_path / "other"
    other.mkdir()
    catalog.observe_file(
        other,
        "outside.pdf",
        sha256="c" * 64,
        size_bytes=1,
        mtime_ns=1,
        metadata=_crossref("10.5555/outside", "Outside this library"),
    )
    if limited_view == "filtered":
        window.model.set_query(FileQuery(search="Dust grains"))
    else:
        monkeypatch.setattr("adsfolio.gui.models.PAGE_SIZE", 1)
        window.model.set_query(FileQuery(order_by="relative_path"))
    assert window.model.rowCount() == 1
    window.table.setCurrentIndex(window.model.index(0, 0))
    selected_id = window._selected_records()[0]["document_id"]
    target = tmp_path / "export.bib"
    choices = []

    def choose_format(
        parent, title, label, items, current, editable
    ) -> tuple[str, bool]:
        choices.append((title, tuple(items), editable))
        return "BibTeX (*.bib)", True

    monkeypatch.setattr(QInputDialog, "getItem", choose_format)
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *args: (str(target), "BibTeX (*.bib)")
    )
    action = window.export_all_action if all_papers else window.export_selected_action
    assert action.isEnabled()

    action.trigger()

    text = target.read_text()
    expected = (
        {record["document_id"] for record in records} if all_papers else {selected_id}
    )
    for record, key in zip(records, ("first-paper", "second-paper")):
        assert (f"{{{key}," in text) == (record["document_id"] in expected)
    assert "Outside this library" not in text
    assert choices == [
        (
            "Export All As" if all_papers else "Export Selection As",
            ("BibTeX (*.bib)", "Publication list (LaTeX, HTML, Markdown, text)"),
            False,
        )
    ]


def test_export_all_is_available_without_a_selection_and_can_export_a_list(
    window, monkeypatch
) -> None:
    """The all action can pass the whole library to the existing list formatter."""
    window.table.clearSelection()
    window._sync_export_action()
    assert window.export_selected_action.text() == "Export Selection As…"
    assert window.export_all_action.text() == "Export All As…"
    assert not window.export_selected_action.isEnabled()
    assert window.export_all_action.isEnabled()
    opened = []

    class Dialog:
        def __init__(self, parent, **kwargs) -> None:
            opened.extend(kwargs["records"])

        def exec(self) -> int:
            return QDialog.Accepted

    monkeypatch.setattr("adsfolio.gui.main_window.CVDialog", Dialog)
    monkeypatch.setattr(
        QInputDialog,
        "getItem",
        lambda *args: (
            "Publication list (LaTeX, HTML, Markdown, text)",
            True,
        ),
    )

    window.export_all_action.trigger()

    assert {row["document_id"] for row in opened} == {
        row["document_id"] for row in _records(window)
    }


def test_cancelling_export_format_does_not_ask_for_keys_or_a_filename(
    window, monkeypatch
) -> None:
    """Cancel the format choice before BibTeX key edits or file writes begin."""
    window.table.setCurrentIndex(window.model.index(0, 0))
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args: ("", False))

    def unexpected_dialog(*args) -> None:
        pytest.fail("A cancelled export must not open another dialog")

    monkeypatch.setattr(CitationKeyDialog, "exec", unexpected_dialog)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", unexpected_dialog)

    window.export_selected_action.trigger()


@pytest.mark.parametrize("valid", [True, False])
def test_export_all_handles_abstract_braces_and_reports_invalid_math(
    window, monkeypatch, tmp_path, valid
) -> None:
    """Export literal braces; identify an unexportable paper before touching a file."""
    catalog = window.registries[str(window.current_root)].catalog
    service = LocalReferences(catalog)
    records = _records(window)
    for record, key in zip(records, ("first-paper", "second-paper")):
        service.set_cite_key(record["document_id"], key)
    document = catalog.get_document(records[0]["document_id"])
    document["metadata"]["abstract"] = (
        "Text } in an ADS abstract" if valid else "$x^{2$"
    )
    catalog.save_reference_documents([document])
    saved = catalog.get_document(document["id"])
    target = tmp_path / "existing.bib"
    target.write_text("Existing bibliography")
    warnings = []
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args: ("BibTeX (*.bib)", True))
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *args: (str(target), "BibTeX (*.bib)")
    )
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))

    window.export_all_action.trigger()

    if valid:
        assert warnings == []
        text = target.read_text()
        assert "{first-paper," in text and "{second-paper," in text
        assert r"Text \textbraceright{} in an ADS abstract" in text
    else:
        assert len(warnings) == 1
        assert all(
            part in warnings[0]
            for part in (
                "Dust",
                "first-paper",
                "abstract",
                "paper's details",
                "export again",
            )
        )
        assert target.read_text() == "Existing bibliography"
    assert catalog.get_document(document["id"]) == saved
