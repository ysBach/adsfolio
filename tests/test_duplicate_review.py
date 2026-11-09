"""Duplicates list same files and same papers, and a copy can move to Trash."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QFile
from PySide6.QtWidgets import QMessageBox

from adsfolio.gui.duplicate_review import DuplicateCopy, DuplicateReviewDialog
from adsfolio.pdf.files import sha256_file
from adsfolio.pdf.registry import (
    DUPLICATE_SAME_FILE,
    DUPLICATE_SAME_PAPER,
    duplicate_kinds,
    mark_duplicates,
    order_duplicate_groups,
    pdf_edition,
)


def test_duplicate_kinds_prefers_same_file_then_same_paper() -> None:
    kinds = duplicate_kinds(
        [
            ("aaa", 1, "published"),  # same bytes as the next copy
            ("aaa", 1, "published"),
            ("bbb", 1, ""),  # same paper, other bytes, edition unknown
            ("ccc", 2, "published"),  # single copy of paper 2
            ("", None, ""),  # no digest and no paper record
            ("ddd", None, ""),
        ]
    )
    assert kinds == [
        DUPLICATE_SAME_FILE,
        DUPLICATE_SAME_FILE,
        DUPLICATE_SAME_PAPER,
        "",
        "",
        "",
    ]


def test_preprint_and_published_editions_are_not_duplicates() -> None:
    """ADS can link both editions to one record. They stay separate here."""
    assert duplicate_kinds([("p", 3, "preprint"), ("j", 3, "published")]) == ["", ""]
    assert duplicate_kinds(
        [("p", 3, "preprint"), ("j", 3, "published"), ("k", 3, "published")]
    ) == ["", DUPLICATE_SAME_PAPER, DUPLICATE_SAME_PAPER]


def test_pdf_edition_reads_the_identifiers_that_the_pdf_states() -> None:
    def record(**proof):
        return {"metadata": {"_adsfolio_identity": proof}}

    assert pdf_edition(record(own_doi=["10.1051/0004-6361/201731937"])) == "published"
    assert pdf_edition(record(own_arxiv=["1711.09809"], own_doi=[])) == "preprint"
    assert pdf_edition(record(own_doi=["10.48550/arxiv.1711.09809"])) == "preprint"
    assert pdf_edition(record(own_arxiv=[], own_doi=[])) == ""
    assert pdf_edition({"metadata": {}}) == ""


def test_mark_duplicates_keeps_only_duplicates_with_their_kind() -> None:
    records = [
        {"sha256": "old", "document_id": 7, "current_path": "Doe_2026.pdf"},
        {"sha256": "new", "document_id": 7, "current_path": "download.pdf"},
        {"sha256": "other", "document_id": 8, "current_path": "other.pdf"},
    ]
    marked = mark_duplicates(records)
    assert [record["current_path"] for record in marked] == [
        "Doe_2026.pdf",
        "download.pdf",
    ]
    assert {record["duplicate_kind"] for record in marked} == {DUPLICATE_SAME_PAPER}


@pytest.fixture
def copies(tmp_path: Path) -> list[DuplicateCopy]:
    root = tmp_path / "library"
    root.mkdir()
    result = []
    for name, text in (("Doe_2026.pdf", b"%PDF old"), ("download.pdf", b"%PDF new")):
        (root / name).write_bytes(text)
        result.append(
            DuplicateCopy(
                root=root,
                relative_path=name,
                sha256=sha256_file(root / name),
                first_seen_at="2026-11-01T10:00:00+00:00",
                document_id=7,
            )
        )
    return result


@pytest.fixture
def fake_trash(monkeypatch, tmp_path: Path) -> Path:
    """Move files to a test folder instead of the user's Trash."""
    trash = tmp_path / "Trash"
    trash.mkdir()

    def move(path: str):
        shutil.move(path, trash / Path(path).name)
        return True, str(trash / Path(path).name)

    monkeypatch.setattr(QFile, "moveToTrash", staticmethod(move))
    return trash


def _answer(monkeypatch, button) -> None:
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: button))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))


def test_user_can_move_the_older_copy_to_trash(
    qtbot, monkeypatch, copies, fake_trash
) -> None:
    _answer(monkeypatch, QMessageBox.StandardButton.Yes)
    dialog = DuplicateReviewDialog(None, copies, "Two copies of one paper.")
    qtbot.addWidget(dialog)
    moved = []
    dialog.trashed.connect(moved.append)

    assert dialog.move_to_trash(copies[0])

    assert moved == [copies[0]]
    assert (fake_trash / "Doe_2026.pdf").exists()
    assert not copies[0].path.exists()
    assert copies[1].path.exists()
    assert dialog.copies == [copies[1]]
    assert "One copy remains" in dialog.status.text()


def test_cancel_keeps_both_copies(qtbot, monkeypatch, copies, fake_trash) -> None:
    _answer(monkeypatch, QMessageBox.StandardButton.Cancel)
    dialog = DuplicateReviewDialog(None, copies, "Two copies of one paper.")
    qtbot.addWidget(dialog)

    assert not dialog.move_to_trash(copies[1])

    assert all(copy.path.exists() for copy in copies)
    assert list(fake_trash.iterdir()) == []


def test_changed_copy_does_not_move(qtbot, monkeypatch, copies, fake_trash) -> None:
    _answer(monkeypatch, QMessageBox.StandardButton.Yes)
    dialog = DuplicateReviewDialog(None, copies, "Two copies of one paper.")
    qtbot.addWidget(dialog)
    copies[1].path.write_bytes(b"%PDF new with a fresh annotation")

    assert not dialog.move_to_trash(copies[1])

    assert copies[1].path.exists()
    assert list(fake_trash.iterdir()) == []


def test_copies_of_one_paper_share_a_group_and_stay_together() -> None:
    records = [
        {"sha256": "a1", "document_id": 1, "current_path": "x.pdf"},
        {"sha256": "b1", "document_id": 2, "current_path": "y.pdf"},
        {"sha256": "a2", "document_id": 1, "current_path": "unresolved/x.pdf"},
        {"sha256": "b1", "document_id": 2, "current_path": "y copy.pdf"},
        {"sha256": "a3", "document_id": 1, "current_path": "x2.pdf"},
    ]
    marked = mark_duplicates(records)
    assert {record["duplicate_group"] for record in marked} == {"paper:1", "paper:2"}

    order_duplicate_groups(marked)

    assert [record["current_path"] for record in marked] == [
        "x.pdf",
        "unresolved/x.pdf",
        "x2.pdf",
        "y.pdf",
        "y copy.pdf",
    ]
    assert [record["duplicate_group_number"] for record in marked] == [1, 1, 1, 2, 2]


def test_each_edition_of_a_shared_record_forms_its_own_group() -> None:
    def record(path, sha, **proof):
        return {
            "sha256": sha,
            "document_id": 4,
            "current_path": path,
            "metadata": {"_adsfolio_identity": proof},
        }

    marked = mark_duplicates(
        [
            record("pre1.pdf", "p1", own_arxiv=["1711.09809"]),
            record("pre2.pdf", "p2", own_arxiv=["1711.09809"]),
            record("pub1.pdf", "j1", own_doi=["10.1051/0004-6361/201731937"]),
            record("pub2.pdf", "j2", own_doi=["10.1051/0004-6361/201731937"]),
        ]
    )
    groups = {record["current_path"]: record["duplicate_group"] for record in marked}
    assert groups["pre1.pdf"] == groups["pre2.pdf"] != groups["pub1.pdf"]
    assert groups["pub1.pdf"] == groups["pub2.pdf"]


def test_merge_button_appears_only_when_offered(qtbot, copies) -> None:
    from PySide6.QtWidgets import QPushButton

    plain = DuplicateReviewDialog(None, copies, "Same bytes.")
    qtbot.addWidget(plain)
    assert "Merge Annotations…" not in {
        b.text() for b in plain.findChildren(QPushButton)
    }

    dialog = DuplicateReviewDialog(None, copies, "Two copies.", can_merge=True)
    qtbot.addWidget(dialog)
    merge = next(
        b for b in dialog.findChildren(QPushButton) if b.text() == "Merge Annotations…"
    )
    merge.click()
    assert dialog.merge_chosen
