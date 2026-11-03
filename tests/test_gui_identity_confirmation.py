"""The user's identifier choice does not need a second warning."""

from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfWriter
from PySide6.QtWidgets import QInputDialog, QMessageBox
from pytestqt.qtbot import QtBot
from test_gui_main_window_watch import _window_factory

from adsfolio.gui.config import GuiConfig, RootEntry
from adsfolio.pdf.files import sha256_file
from adsfolio.pdf.identity import target_identity


@pytest.mark.parametrize("owned", [False, True])
def test_update_by_id_accepts_the_users_identifier_without_an_extra_warning(
    monkeypatch: pytest.MonkeyPatch,
    qtbot: QtBot,
    tmp_path: Path,
    owned: bool,
) -> None:
    """Keep validation enabled when the PDF has identifiers of its own."""
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
    started: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        QMessageBox, "question", lambda *args: pytest.fail("Unnecessary warning")
    )
    monkeypatch.setattr(
        window, "_start_ads_refresh", lambda *args, **kwargs: started.append(args)
    )

    window._update_from_identifier("")

    assert len(started) == 1
    assert started[0][:2] == ("--identifier", "arXiv:2401.00001")
    assert ("--confirm-identity" in started[0]) is not owned
    assert path.read_bytes() == before


@pytest.mark.parametrize("change", ["changed", "missing"])
def test_reference_candidate_is_bound_to_the_reviewed_pdf(
    monkeypatch: pytest.MonkeyPatch, qtbot: QtBot, tmp_path: Path, change: str
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    path = root / "paper.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=400)
    writer.write(path)
    window, _controller, _saves = _window_factory(
        monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(root))])
    )
    document = {"bibcode": "2024Test...1A", "title": ["Candidate"]}
    candidate = {
        "bibcode": document["bibcode"],
        "first_author": "Author",
        "title": "Candidate",
        "pdf_sha256": sha256_file(path),
        "identity_fingerprint": target_identity(document).fingerprint,
    }
    warnings = []
    started = []

    def choose(*args, **kwargs):
        if change == "changed":
            path.write_bytes(b"changed after the lookup")
        else:
            path.unlink()
        return args[3][0], True

    monkeypatch.setattr(QInputDialog, "getItem", choose)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    monkeypatch.setattr(window, "_start_ads_refresh", lambda *a, **k: started.append(a))

    window._choose_ambiguous_ads_match([candidate], path.name, 1, root=root)

    assert not started
    assert warnings and ("changed" in warnings[0][2] or "no longer" in warnings[0][2])


@pytest.mark.parametrize("owned", [False, True])
def test_choosing_an_ads_candidate_needs_no_second_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    qtbot: QtBot,
    tmp_path: Path,
    owned: bool,
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
    started: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        QMessageBox, "question", lambda *args: pytest.fail("Unnecessary warning")
    )
    monkeypatch.setattr(
        window, "_start_ads_refresh", lambda *args, **kwargs: started.append(args)
    )

    window._choose_ambiguous_ads_match(candidates, "ambig.pdf", 1, root=root)

    assert len(started) == 1
    assert started[0][:4] == (
        "--bibcode",
        "2024Test...1A",
        "--relative-path",
        "ambig.pdf",
    )
    assert ("--confirm-identity" in started[0]) is not owned
