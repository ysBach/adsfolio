"""Explicit review is required before assigning an unreadable PDF identity."""

from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfWriter
from PySide6.QtWidgets import QInputDialog, QMessageBox
from pytestqt.qtbot import QtBot
from test_gui_main_window_watch import _window_factory

from adsfolio.gui.config import GuiConfig, RootEntry


@pytest.mark.parametrize("owned", [False, True])
@pytest.mark.parametrize("confirm", [False, True])
def test_update_by_id_requires_confirmation_only_without_owned_identifier(
    monkeypatch: pytest.MonkeyPatch,
    qtbot: QtBot,
    tmp_path: Path,
    owned: bool,
    confirm: bool,
) -> None:
    """Only explicit consent adds the single-file manual-confirmation flag."""
    root = tmp_path / "library"
    root.mkdir()
    path = root / "paper.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=400)
    if owned:
        writer.add_metadata({"/arXiv": "arXiv:2401.00001"})
    with path.open("wb") as stream:
        writer.write(stream)
    before = path.read_bytes()
    window, _controller, _saves = _window_factory(
        monkeypatch,
        qtbot,
        GuiConfig(roots=[RootEntry(str(root))]),
    )
    window.table.setCurrentIndex(window.model.index(0, 0))
    monkeypatch.setattr(
        QInputDialog, "getText", lambda *args, **kwargs: ("arXiv:2401.00001", True)
    )
    questions: list[str] = []
    started: list[tuple[str, ...]] = []

    def answer(_parent, _title, text, *_args):
        questions.append(text)
        return QMessageBox.Yes if confirm else QMessageBox.No

    monkeypatch.setattr(QMessageBox, "question", answer)
    monkeypatch.setattr(
        window, "_start_ads_refresh", lambda *args, **kwargs: started.append(args)
    )

    window._update_from_identifier("")

    assert bool(questions) is not owned
    if owned or confirm:
        assert len(started) == 1
        assert started[0][:2] == ("--identifier", "arXiv:2401.00001")
        assert ("--confirm-identity" in started[0]) is not owned
    else:
        assert started == []
    if questions:
        assert "paper.pdf" in questions[0]
        assert "arXiv:2401.00001" in questions[0]
    assert path.read_bytes() == before


@pytest.mark.parametrize("owned", [False, True])
@pytest.mark.parametrize("confirm", [False, True])
def test_choose_ambiguous_ads_candidate_prompts_and_binds_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    qtbot: QtBot,
    tmp_path: Path,
    owned: bool,
    confirm: bool,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    path = root / "ambig.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=400)
    if owned:
        writer.add_metadata({"/arXiv": "arXiv:2401.00001"})
    with path.open("wb") as stream:
        writer.write(stream)
    window, _controller, _saves = _window_factory(
        monkeypatch,
        qtbot,
        GuiConfig(roots=[RootEntry(str(root))]),
    )
    candidates = [
        {
            "bibcode": "2024Test...1A",
            "first_author": "Author A",
            "title": "Title A",
        }
    ]
    monkeypatch.setattr(
        QInputDialog,
        "getItem",
        lambda *args, **kwargs: (args[3][0], True),
    )
    questions: list[str] = []
    started: list[tuple[str, ...]] = []

    def answer(_parent, _title, text, *_args):
        questions.append(text)
        return QMessageBox.Yes if confirm else QMessageBox.No

    monkeypatch.setattr(QMessageBox, "question", answer)
    monkeypatch.setattr(
        window, "_start_ads_refresh", lambda *args, **kwargs: started.append(args)
    )

    window._choose_ambiguous_ads_match(candidates, "ambig.pdf", 1, root=root)

    assert bool(questions) is not owned
    if owned or confirm:
        assert len(started) == 1
        assert started[0][:4] == (
            "--bibcode",
            "2024Test...1A",
            "--relative-path",
            "ambig.pdf",
        )
        assert ("--confirm-identity" in started[0]) is not owned
    else:
        assert started == []
    if questions:
        assert "ambig.pdf" in questions[0]
        assert "2024Test...1A" in questions[0]
        assert "trusted metadata fields" in questions[0]
