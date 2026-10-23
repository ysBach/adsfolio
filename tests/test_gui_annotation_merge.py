"""GUI tests for the annotation merge dialog."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fitz")
pytest.importorskip("PySide6")

import fitz
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

from adsfolio.config import Config
from adsfolio.gui.annotation_merge import (
    AnnotationMergeDialog,
    PdfPageView,
)
from adsfolio.gui.main_window import MainWindow


def _pdf(path: Path, rotation: int = 0) -> Path:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "alpha beta gamma delta", fontsize=12)
    if rotation:
        page.set_rotation(rotation)
    doc.save(str(path))
    doc.close()
    return path


def _annotated_source(tmp_path: Path) -> Path:
    path = _pdf(tmp_path / "s.pdf")
    doc = fitz.open(str(path))
    page = doc[0]
    h = page.add_highlight_annot(page.search_for("beta gamma", quads=True))
    h.set_info(content="why")
    h.update()
    page.add_text_annot((300, 300), "note").update()
    page.add_freetext_annot(fitz.Rect(300, 100, 400, 150), "free")
    out = tmp_path / "source.pdf"
    doc.save(str(out))
    doc.close()
    return out


@pytest.fixture
def application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _dialog(tmp_path, application) -> AnnotationMergeDialog:
    return AnnotationMergeDialog(
        _pdf(tmp_path / "target.pdf"), _annotated_source(tmp_path)
    )


def test_dialog_lists_every_annotation_and_requires_waiver(
    application, tmp_path: Path
) -> None:
    dialog = _dialog(tmp_path, application)
    subtypes = [i.subtype for i in dialog.items]

    assert {"Highlight", "Text", "FreeText", "Popup"} <= set(subtypes)
    free = next(i for i in dialog.items if i.subtype == "FreeText")
    dialog.item_list.setCurrentRow(dialog.visible_items.index(free))
    assert dialog.waive_button.isEnabled()
    assert "not placed" not in dialog.item_state(free)
    assert dialog.waive_current(confirmed=False) is False
    assert free.item_id not in dialog.waived
    assert dialog.waive_current(confirmed=True) is True
    assert dialog.item_state(free) == "waived"
    dialog.close()


def test_exact_suggestion_must_be_accepted_by_the_user(
    application, tmp_path: Path
) -> None:
    dialog = _dialog(tmp_path, application)
    highlight = next(i for i in dialog.items if i.subtype == "Highlight")
    dialog.item_list.setCurrentRow(dialog.visible_items.index(highlight))

    assert dialog.suggest_button.isEnabled()
    assert highlight.item_id not in dialog.placements
    assert dialog.accept_suggestion() is None
    assert highlight.item_id in dialog.placements
    dialog.close()


def test_partial_save_asks_confirmation_and_never_claims_completion(
    application, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dialog = _dialog(tmp_path, application)
    shown: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "information", lambda *a: shown.append(a[2]) or QMessageBox.Ok
    )
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.No)
    output = tmp_path / "merged.pdf"

    assert dialog.save_copy(output) is None
    assert not output.exists()

    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    report = dialog.save_copy(output)
    assert report is not None and not report.complete
    assert output.exists()
    assert "partial merge" in shown[0]
    dialog.close()


def test_existing_output_is_refused_and_left_intact(
    application, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dialog = _dialog(tmp_path, application)
    existing = tmp_path / "exists.pdf"
    existing.write_bytes(b"keep")
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    warned: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a: warned.append(a[2]) or QMessageBox.Ok
    )

    assert dialog.save_copy(existing) is None
    assert existing.read_bytes() == b"keep"
    assert "already exists" in warned[0]
    dialog.close()


def test_rotated_page_view_maps_clicks_to_unrotated_coordinates(
    application, tmp_path: Path
) -> None:
    path = _pdf(tmp_path / "r.pdf", rotation=90)
    view = PdfPageView()
    rect = view.show_page(path, 0)
    doc = fitz.open(str(path))
    page = doc[0]
    word = next(w for w in page.get_text("words") if w[4] == "beta")
    to_view = page.rotation_matrix
    shown = fitz.Rect(*word[:4]) * to_view
    doc.close()
    centre = ((shown.x0 + shown.x1) / 2, (shown.y0 + shown.y1) / 2)

    got: list[tuple[float, float]] = []
    view.point_clicked.connect(lambda x, y: got.append((x, y)))
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    point = QPointF(centre[0] * 1.4, centre[1] * 1.4)
    for kind in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
        event = QMouseEvent(
            kind, point, point, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier
        )
        (
            view.mousePressEvent
            if kind == QEvent.MouseButtonPress
            else view.mouseReleaseEvent
        )(event)

    assert rect[2] < rect[3]  # unrotated frame is portrait
    assert got[0][0] == pytest.approx((word[0] + word[2]) / 2, abs=1e-3)
    assert got[0][1] == pytest.approx((word[1] + word[3]) / 2, abs=1e-3)


def test_main_window_has_merge_action_and_survives_missing_pymupdf(
    application, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    window = MainWindow()
    assert window.merge_annotations_action.text() == "Merge Annotations…"
    window.close()


def test_missing_pymupdf_shows_install_advice_without_crashing(
    application, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from adsfolio.pdf.annotation_merge import MissingDependencyError

    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    window = MainWindow()
    _pdf(tmp_path / "t.pdf")
    _pdf(tmp_path / "s.pdf")
    picks = iter([(str(tmp_path / "t.pdf"), ""), (str(tmp_path / "s.pdf"), "")])
    monkeypatch.setattr(
        "adsfolio.gui.main_window.QFileDialog.getOpenFileName",
        lambda *a, **k: next(picks),
    )

    def missing(*_a, **_k):
        raise MissingDependencyError("needs PyMuPDF: pip install adsfolio[full]")

    monkeypatch.setattr(
        "adsfolio.gui.annotation_merge.AnnotationMergeDialog.__init__", missing
    )
    warned: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a: warned.append(a[2]) or QMessageBox.Ok
    )

    window._merge_annotations()

    assert "adsfolio[full]" in warned[0]
    window.close()


def test_controls_and_pdf_panels_are_in_a_resizable_splitter(
    application, tmp_path: Path
) -> None:
    dialog = _dialog(tmp_path, application)
    dialog.show()
    application.processEvents()

    left, right = dialog.splitter.sizes()
    assert dialog.splitter.widget(0).minimumWidth() == 300
    assert left < right / 2
    dialog.splitter.setSizes([500, 900])
    application.processEvents()
    assert dialog.splitter.sizes()[0] > left
    headings = [
        w.toolTip() for w in dialog.findChildren(QLabel) if w.toolTip().endswith(".pdf")
    ]
    assert str(dialog.source) in headings and str(dialog.target) in headings
    dialog.close()


def test_source_hyperlinks_require_confirmation_and_leave_reader_marks_visible(
    application, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _annotated_source(tmp_path)
    with fitz.open(source) as document:
        document[0].insert_link(
            {
                "kind": fitz.LINK_URI,
                "from": fitz.Rect(72, 82, 120, 103),
                "uri": "https://example.org",
            }
        )
        document.saveIncr()
    dialog = AnnotationMergeDialog(_pdf(tmp_path / "target.pdf"), source)
    links = [item for item in dialog.items if item.subtype == "Link"]
    assert len(links) == 1
    assert dialog.exclude_source_links(confirmed=False) == 0
    assert links[0].item_id not in dialog.waived
    asked = []

    def confirm(*args) -> int:
        asked.append(args[2])
        return QMessageBox.Yes

    monkeypatch.setattr(QMessageBox, "question", confirm)

    dialog.exclude_links_button.click()

    assert len(asked) == 1
    assert "All hyperlinks already in the target PDF are retained" in asked[0]
    assert links[0].item_id in dialog.waived
    assert not any(item.subtype == "Link" for item in dialog.visible_items)
    assert any(item.subtype == "Highlight" for item in dialog.visible_items)
    assert dialog.exclude_source_links(confirmed=True) == 0
    dialog.close()


def test_save_button_prompts_for_output_and_emits_saved_path(
    application, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dialog = _dialog(tmp_path, application)
    output = tmp_path / "saved-from-button.pdf"
    saved = []
    prompts = []
    dialog.copy_saved.connect(saved.append)

    def choose_output(*args, **kwargs) -> tuple[str, str]:
        prompts.append(args[1])
        return str(output), "PDF (*.pdf)"

    monkeypatch.setattr(
        "adsfolio.gui.annotation_merge.QFileDialog.getSaveFileName", choose_output
    )
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: QMessageBox.Ok)

    dialog.save_button.click()

    assert prompts == ["Save merged copy"]
    assert saved == [str(output)]
    assert dialog.report is not None and dialog.report.verified
    assert output.is_file()
    dialog.close()


def test_fit_width_centers_matches_without_horizontal_hunting(
    application, tmp_path: Path
) -> None:
    dialog = _dialog(tmp_path, application)
    dialog.show()
    application.processEvents()
    for view, scroll in (
        (dialog.source_view, dialog.source_scroll),
        (dialog.target_view, dialog.target_scroll),
    ):
        assert view.width() <= scroll.viewport().width()
        assert scroll.horizontalScrollBar().maximum() == 0
        assert view._focus is not None
    dialog.close()


def test_accept_and_next_navigates_to_the_next_unresolved_annotation(
    application, tmp_path: Path
) -> None:
    dialog = _dialog(tmp_path, application)
    first = dialog.current
    assert first is not None
    assert first.item_id not in dialog.placements
    dialog.next_button.click()
    assert first.item_id in dialog.placements
    assert dialog.current != first
    assert dialog.current.item_id not in dialog.placements
    dialog.close()


def test_repeated_matches_are_reviewable_and_search_changes_candidate_page(
    application, tmp_path: Path
) -> None:
    target = tmp_path / "repeated.pdf"
    with fitz.open() as document:
        for _ in range(2):
            document.new_page().insert_text((72, 100), "alpha beta gamma delta")
        document.save(target)
    dialog = AnnotationMergeDialog(target, _annotated_source(tmp_path))
    assert dialog.candidates.count() == 2
    dialog.candidates.setCurrentIndex(1)
    assert dialog.target_spin.value() == 2
    assert not dialog.placements
    assert dialog.accept_suggestion() is None
    assert dialog.placements[dialog.current.item_id].page == 1
    dialog.close()


def test_background_lookup_keeps_a_manual_placement_and_user_navigation(
    application, tmp_path: Path
) -> None:
    from types import SimpleNamespace

    from adsfolio.pdf.annotation_matching import TextMatch

    dialog = _dialog(tmp_path, application)
    item = dialog.current
    assert item is not None
    candidate = dialog.suggestions[item.item_id]
    assert candidate is not None
    dialog._match_job = SimpleNamespace(
        items=[item], cancelled=SimpleNamespace(set=lambda: None)
    )
    dialog.matches[item.item_id] = []
    dialog._manual_navigation = True
    dialog._sentences_ready(
        {item.item_id: [TextMatch(candidate, "new candidate", "Closest wording")]}
    )
    assert dialog._manual_navigation
    assert not dialog.placements
    dialog.close()


def test_background_wording_search_works_offline(
    application, tmp_path: Path, qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket

    from PySide6.QtCore import QThreadPool

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Annotation review attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    target = tmp_path / "edited.pdf"
    with fitz.open() as document:
        document.new_page().insert_text((72, 100), "alpha beta revised gamma delta")
        document.save(target)
    dialog = AnnotationMergeDialog(target, _annotated_source(tmp_path))
    dialog.show()
    qtbot.waitUntil(
        lambda: "Local wording candidates found" in dialog.match_status.text(),
        timeout=5000,
    )
    assert dialog.matches[dialog.current.item_id]
    assert not dialog.placements
    assert dialog.next_button.isEnabled()
    assert "word edit" in dialog.target_excerpt.toPlainText()
    dialog.close()
    QThreadPool.globalInstance().waitForDone(5000)


def test_enter_in_find_does_not_accept_an_annotation(
    application, tmp_path: Path, qtbot
) -> None:
    from PySide6.QtCore import Qt

    dialog = _dialog(tmp_path, application)
    dialog.show()
    dialog.search.setText("beta")
    dialog.search.setFocus()
    qtbot.keyClick(dialog.search, Qt.Key_Return)
    assert not dialog.placements
    assert "beta" in dialog.target_excerpt.toPlainText()
    dialog.close()


def test_note_and_shape_waivers_remain_explicit(application, tmp_path: Path) -> None:
    dialog = _dialog(tmp_path, application)
    note = next(item for item in dialog.visible_items if item.subtype == "Text")
    dialog.item_list.setCurrentRow(dialog.visible_items.index(note))
    assert dialog.waive_current(confirmed=False) is False
    assert dialog.waive_current(confirmed=True) is True
    assert note.item_id in dialog.waived
    dialog.close()


def test_fit_width_keeps_rotated_mouse_coordinates_correct(
    application, tmp_path: Path
) -> None:
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QScrollArea

    path = _pdf(tmp_path / "rotated.pdf", rotation=90)
    view, scroll = PdfPageView(), QScrollArea()
    scroll.resize(430, 500)
    scroll.setWidget(view)
    view.attach_scroll(scroll)
    view.show_page(path, 0)
    scroll.show()
    application.processEvents()
    with fitz.open(path) as document:
        word = next(w for w in document[0].get_text("words") if w[4] == "beta")
        point = (
            fitz.Point((word[0] + word[2]) / 2, (word[1] + word[3]) / 2)
            * document[0].rotation_matrix
        )
    mouse = QPointF(point.x * view.zoom, point.y * view.zoom)
    received = []
    view.point_clicked.connect(lambda x, y: received.append((x, y)))
    view.mousePressEvent(
        QMouseEvent(
            QEvent.MouseButtonPress,
            mouse,
            mouse,
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )
    )
    view.mouseReleaseEvent(
        QMouseEvent(
            QEvent.MouseButtonRelease,
            mouse,
            mouse,
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )
    )
    assert view.zoom != 1.4
    assert received == [
        pytest.approx(((word[0] + word[2]) / 2, (word[1] + word[3]) / 2))
    ]
    scroll.close()


def test_accepted_candidate_stays_visible_and_can_be_revised(
    application, tmp_path: Path
) -> None:
    target = tmp_path / "choices.pdf"
    with fitz.open() as document:
        for text in ("alpha beta gamma delta", "alpha beta gamma delta"):
            document.new_page().insert_text((72, 100), text)
        document.save(target)
    dialog = AnnotationMergeDialog(target, _annotated_source(tmp_path))
    dialog.candidates.setCurrentIndex(1)
    assert dialog.accept_suggestion() is None
    assert dialog.target_spin.value() == 2
    assert dialog.candidates.currentIndex() == 1
    assert "Accepted target placement" in dialog.target_excerpt.toPlainText()
    assert not dialog.candidates.isEnabled()
    dialog.undo_button.click()
    assert not dialog.placements
    assert dialog.candidates.isEnabled()
    assert dialog.suggest_button.isEnabled()
    dialog.close()
