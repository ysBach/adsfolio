"""Tests for explicit, reversible cleanup after PDF merging."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QMessageBox

from adsfolio.gui.annotation_merge import AnnotationMergeDialog
from adsfolio.gui.merge_cleanup import MergeCleanupDialog, cleanup_problem
from adsfolio.pdf.annotation_merge import (
    MergeInput,
    MergeReport,
    build_merged_copy,
)

fitz = pytest.importorskip("fitz")


def _pdf(path: Path, text: str = "A paper with annotations") -> Path:
    with fitz.open() as document:
        document.new_page().insert_text((72, 72), text)
        document.save(path)
    return path


def _report(tmp_path: Path) -> MergeReport:
    target, source = (
        _pdf(tmp_path / "target.pdf"),
        _pdf(tmp_path / "source.pdf", "A second edition"),
    )
    return build_merged_copy(target, source, [], {}, set(), tmp_path / "merged.pdf")


def _fake_trash(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[Path]:
    trash = tmp_path / "fake-trash"
    trash.mkdir()
    moved = []

    def move(path: str) -> tuple[bool, str]:
        original = Path(path)
        destination = trash / original.name
        shutil.move(original, destination)
        moved.append(original)
        return True, str(destination)

    monkeypatch.setattr("adsfolio.gui.merge_cleanup.QFile.moveToTrash", move)
    return moved


def test_checked_originals_go_to_trash_and_only_the_merged_pdf_remains(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _report(tmp_path)
    merged_bytes = Path(report.output_path).read_bytes()
    moved = _fake_trash(monkeypatch, tmp_path)
    dialog = MergeCleanupDialog(report)
    qtbot.addWidget(dialog)
    dialog.trash_button.click()
    assert set(moved) == {Path(item.path) for item in report.input_files}
    assert dialog.result() == QDialog.Accepted
    assert Path(report.output_path).read_bytes() == merged_bytes
    assert list(tmp_path.glob("*.pdf")) == [Path(report.output_path)]
    assert all((tmp_path / "fake-trash" / path.name).is_file() for path in moved)


def test_keep_and_unchecked_files_are_not_moved(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _report(tmp_path)
    moved = _fake_trash(monkeypatch, tmp_path)
    dialog = MergeCleanupDialog(report)
    qtbot.addWidget(dialog)
    dialog.keep_button.click()
    assert moved == []
    assert all(Path(item.path).exists() for item in report.input_files)
    dialog = MergeCleanupDialog(report)
    qtbot.addWidget(dialog)
    dialog.file_list.item(0).setCheckState(Qt.Unchecked)
    dialog.trash_button.click()
    assert moved == [Path(report.input_files[1].path)]
    assert Path(report.input_files[0].path).exists()


@pytest.mark.parametrize(
    "change",
    [
        "partial",
        "unverified",
        "output-changed",
        "output-missing",
        "input-changed",
        "unknown-input",
    ],
)
def test_unsafe_cleanup_is_refused_before_any_move(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    report = _report(tmp_path)
    moved = _fake_trash(monkeypatch, tmp_path)
    if change == "partial":
        report.unresolved.append("unplaced")
    elif change == "unverified":
        report.verified = False
    elif change == "output-changed":
        Path(report.output_path).write_bytes(b"changed")
    elif change == "output-missing":
        Path(report.output_path).unlink()
    elif change == "input-changed":
        Path(report.input_files[0].path).write_bytes(b"changed after merge")
    else:
        other = _pdf(tmp_path / "other.pdf")
        assert cleanup_problem(
            report,
            [MergeInput(str(other), hashlib.sha256(other.read_bytes()).hexdigest())],
        )
        return
    dialog = MergeCleanupDialog(report)
    qtbot.addWidget(dialog)
    dialog.move_selected_to_trash()
    assert moved == []
    assert dialog.status.text()


@pytest.mark.parametrize("alias", ["same-path", "hard-link", "symlink"])
def test_merged_output_is_protected_from_aliases(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, alias: str
) -> None:
    report = _report(tmp_path)
    moved = _fake_trash(monkeypatch, tmp_path)
    output = Path(report.output_path)
    if alias == "same-path":
        original = output
    else:
        original = tmp_path / "alias.pdf"
        if alias == "hard-link":
            os.link(output, original)
        else:
            original.symlink_to(output)
    report.input_files = (MergeInput(str(original), report.output_sha256),)
    dialog = MergeCleanupDialog(report)
    qtbot.addWidget(dialog)
    dialog.trash_button.click()
    assert moved == []
    assert output.exists()
    assert dialog.status.text()


def test_partial_trash_failure_names_files_and_can_be_retried(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _report(tmp_path)
    original_move = _fake_trash(monkeypatch, tmp_path)
    from adsfolio.gui.merge_cleanup import QFile

    move = QFile.moveToTrash
    failing = report.input_files[1].path
    monkeypatch.setattr(
        QFile,
        "moveToTrash",
        lambda path: (False, "") if path == failing else move(path),
    )
    dialog = MergeCleanupDialog(report)
    qtbot.addWidget(dialog)
    dialog.trash_button.click()
    assert dialog.trashed_paths == [Path(report.input_files[0].path)]
    assert failing in dialog.status.text()
    assert Path(failing).exists()
    assert Path(report.output_path).exists()
    monkeypatch.setattr(QFile, "moveToTrash", move)
    dialog.trash_button.click()
    assert len(original_move) == 2
    assert dialog.result() == QDialog.Accepted


def test_complete_save_offers_cleanup_and_partial_save_does_not(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, source = _pdf(tmp_path / "target.pdf"), _pdf(tmp_path / "source.pdf")
    dialog = AnnotationMergeDialog(target, source)
    qtbot.addWidget(dialog)
    presented = []

    def choose_keep(message):
        presented.extend(button.text() for button in message.buttons())
        return 0

    monkeypatch.setattr(QMessageBox, "exec", choose_keep)
    report = dialog.save_copy(tmp_path / "merged.pdf")
    assert report is not None and report.complete
    assert {"Keep originals", "Remove original PDFs…"} <= set(presented)
    assert not dialog.cleanup_button.isHidden()
    assert target.exists() and source.exists()
    report.unresolved.append("still-open")
    presented.clear()
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: shown.append(args[2]))
    dialog._show_saved_result()
    assert shown and presented == []


def test_cleanup_signal_closes_review_after_originals_are_removed(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _report(tmp_path)
    dialog = AnnotationMergeDialog(
        Path(report.input_files[0].path), Path(report.input_files[1].path)
    )
    qtbot.addWidget(dialog)
    dialog.report = report
    paths = []
    dialog.originals_trashed.connect(paths.append)
    _fake_trash(monkeypatch, tmp_path)
    monkeypatch.setattr(
        MergeCleanupDialog, "exec", lambda cleanup: cleanup.move_selected_to_trash()
    )
    dialog.cleanup_originals()
    assert paths == [tuple(Path(original.path) for original in report.input_files)]
    assert dialog.result() == QDialog.Accepted


def test_three_originals_and_intermediate_can_be_removed_after_sequential_merges(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_gui_main_window_watch import _window_factory

    from adsfolio.config import Config
    from adsfolio.gui.config import GuiConfig, RootEntry

    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    root = tmp_path / "library"
    root.mkdir()
    first = _report(root)
    window, _, _ = _window_factory(
        monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(root))])
    )
    window._remember_annotation_merge(first)
    # Reconstruct the window to exercise catalog-backed merge history.
    window.close()
    window, _, _ = _window_factory(
        monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(root))])
    )
    third = _pdf(root / "third.pdf", "Third edition")
    final = build_merged_copy(
        Path(first.output_path), third, [], {}, set(), root / "final.pdf"
    )
    window._remember_annotation_merge(final)
    expected = {root / "target.pdf", root / "source.pdf", root / "merged.pdf", third}
    assert {Path(original.path) for original in final.input_files} == expected
    moved = _fake_trash(monkeypatch, tmp_path)
    cleanup = MergeCleanupDialog(final)
    qtbot.addWidget(cleanup)
    cleanup.trash_button.click()
    assert set(moved) == expected
    window._record_trashed_merge_originals(tuple(moved))
    registry = window.registries[str(root)]
    assert [record["current_path"] for record in registry.iter_files()] == ["final.pdf"]
    assert registry.find_file_by_current_path(root, "target.pdf")["missing_at"]
    assert list(root.glob("*.pdf")) == [Path(final.output_path)]
    window.close()


def test_incomplete_previous_merge_does_not_offer_its_unresolved_originals(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_gui_main_window_watch import _window_factory

    from adsfolio.config import Config
    from adsfolio.gui.config import GuiConfig, RootEntry

    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    root = tmp_path / "library"
    root.mkdir()
    first = _report(root)
    first.unresolved.append("annotation-not-transferred")
    window, _, _ = _window_factory(
        monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(root))])
    )
    window._remember_annotation_merge(first)
    third = _pdf(root / "third.pdf", "Third edition")
    final = build_merged_copy(
        Path(first.output_path), third, [], {}, set(), root / "final.pdf"
    )
    window._remember_annotation_merge(final)
    assert {Path(original.path) for original in final.input_files} == {
        Path(first.output_path),
        third,
    }
    window.close()


def test_catalog_keeps_canonical_metadata_in_the_final_result(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_gui_main_window_watch import _window_factory

    from adsfolio.config import Config
    from adsfolio.gui.config import GuiConfig, RootEntry

    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    window, _, _ = _window_factory(
        monkeypatch, qtbot, GuiConfig(roots=[RootEntry(str(tmp_path))])
    )
    report = _report(tmp_path)
    registry = window.registries[str(tmp_path)]
    registry.refresh_local_files()
    stat = (tmp_path / "target.pdf").stat()
    registry.upsert_file(
        root=tmp_path,
        relative_path="target.pdf",
        current_path="target.pdf",
        sha256=report.input_files[0].sha256,
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        status="resolved",
        metadata={
            "title": ["Canonical title"],
            "custom_note": "keep this",
            "_adsfolio_metadata_sha256": report.input_files[0].sha256,
            "bibcode": "2026ApJ..1009L..41B",
        },
    )
    window._remember_annotation_merge(report)
    record = window.registries[str(tmp_path)].find_file_by_current_path(
        tmp_path, "merged.pdf"
    )
    assert record["metadata"]["custom_note"] == "keep this"
    assert record["metadata"]["title"] == ["Canonical title"]
    assert record["metadata"]["bibcode"] == "2026ApJ..1009L..41B"
    assert record["metadata"]["_adsfolio_metadata_sha256"] == report.output_sha256
    window.close()


def test_trash_binding_boolean_return_is_supported(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _report(tmp_path)
    moved = _fake_trash(monkeypatch, tmp_path)
    from adsfolio.gui.merge_cleanup import QFile

    move = QFile.moveToTrash
    monkeypatch.setattr(QFile, "moveToTrash", lambda path: move(path)[0])
    dialog = MergeCleanupDialog(report)
    qtbot.addWidget(dialog)
    dialog.trash_button.click()
    assert len(moved) == 2
    assert dialog.result() == QDialog.Accepted


def test_cleanup_discloses_waivers_and_uncopied_edition_content(
    qtbot, tmp_path: Path
) -> None:
    report = _report(tmp_path)
    report.waived.extend(["excluded-link", "excluded-note"])
    extra = _pdf(tmp_path / "earlier-original.pdf")
    report.input_files += (
        MergeInput(str(extra), hashlib.sha256(extra.read_bytes()).hexdigest()),
    )
    dialog = MergeCleanupDialog(report)
    qtbot.addWidget(dialog)
    text = dialog.omissions.text()
    assert "2 annotation(s) were explicitly waived" in text
    assert "pages, bookmarks, attachments, and form data were not transferred" in text
    assert "Earlier merge steps may also have waived annotations" in text


def test_output_changed_during_cleanup_stops_remaining_moves(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _report(tmp_path)
    moved = _fake_trash(monkeypatch, tmp_path)
    from adsfolio.gui.merge_cleanup import QFile

    move = QFile.moveToTrash

    def move_then_change_output(path: str) -> tuple[bool, str]:
        result = move(path)
        Path(report.output_path).write_bytes(b"external edit during cleanup")
        return result

    monkeypatch.setattr(QFile, "moveToTrash", move_then_change_output)
    dialog = MergeCleanupDialog(report)
    qtbot.addWidget(dialog)
    dialog.trash_button.click()
    assert len(moved) == 1
    assert Path(report.input_files[1].path).exists()
    assert "merged PDF is missing or changed" in dialog.status.text()
