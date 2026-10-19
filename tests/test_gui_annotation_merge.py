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
    dialog.item_list.setCurrentRow(dialog.items.index(free))
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
    dialog.item_list.setCurrentRow(dialog.items.index(highlight))

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
