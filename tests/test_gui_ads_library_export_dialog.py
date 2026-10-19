"""Tests for exporting complete ADS libraries from the GUI."""

from __future__ import annotations

import os
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, ClassVar

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QDialog

from adsfolio.api.export import ExportAPI
from adsfolio.gui.ads_library_export_dialog import ExportAdsLibraryDialog
from adsfolio.gui.ads_library_jobs import (
    AdsLibraryFailure,
    AdsLibraryOperation,
    AdsLibraryRequest,
)
from adsfolio.library_sync import LibraryDestination, LibraryExportResult


@pytest.fixture(scope="module")
def application() -> QApplication:
    """Provide one Qt application for dialog tests."""
    return QApplication.instance() or QApplication([])


def _destination(
    library_id: str,
    permission: str,
    *,
    name: str | None = None,
    num_documents: int = 0,
) -> LibraryDestination:
    return LibraryDestination(
        id=library_id,
        name=name or library_id,
        permission=permission,
        public=True,
        num_documents=num_documents,
    )


class FakeJob(QObject):
    """Manually completed job exposing the production job signals."""

    succeeded = Signal(object)
    failed = Signal(object)
    finished = Signal()

    def __init__(
        self,
        request: AdsLibraryRequest,
        parent: QObject | None,
        harness: FakeJobHarness,
    ) -> None:
        super().__init__(parent)
        self.request = request
        self.harness = harness
        self.running = False
        self.cancelled = False

    def start(self) -> None:
        self.running = True
        self.harness.started.append(self)

    def cancel(self) -> None:
        self.cancelled = True

    def succeed(self, result: object) -> None:
        self.succeeded.emit(result)

    def fail(self, failure: object) -> None:
        self.failed.emit(failure)

    def finish(self) -> None:
        self.running = False
        self.finished.emit()


class FakeJobHarness:
    """Create fake jobs and retain their requests for assertions."""

    def __init__(self) -> None:
        self.started: list[FakeJob] = []
        self.outcomes: dict[AdsLibraryOperation, deque[object]] = defaultdict(deque)

    def factory(
        self,
        request: AdsLibraryRequest,
        parent: QObject | None = None,
    ) -> FakeJob:
        return FakeJob(request, parent, self)

    def list(self, *destinations: LibraryDestination) -> None:
        job = self.started[-1]
        assert job.request.operation is AdsLibraryOperation.LIST
        job.succeed(destinations)
        job.finish()


def _dialog(
    application: QApplication,
    qtbot: Any,
    harness: FakeJobHarness,
) -> ExportAdsLibraryDialog:
    dialog = ExportAdsLibraryDialog(job_factory=harness.factory)
    qtbot.addWidget(dialog)
    return dialog


def _load_one_library(
    dialog: ExportAdsLibraryDialog,
    harness: FakeJobHarness,
    *,
    name: str = "Read-only / research",
) -> LibraryDestination:
    destination = _destination("lib-read", "read", name=name, num_documents=2)
    harness.list(destination)
    assert dialog.library_combo.count() == 1
    return destination


def test_lists_read_only_libraries_and_defaults_to_bibtex(
    application: QApplication,
    qtbot: Any,
) -> None:
    """Remote export does not require a local root or write permission."""
    harness = FakeJobHarness()
    dialog = _dialog(application, qtbot, harness)
    try:
        writable = _destination("lib-write", "write", num_documents=3)
        read_only = _destination("lib-read", "read", num_documents=2)
        harness.list(read_only, writable)

        assert dialog.library_combo.count() == 2
        assert "lib-read" in dialog.library_combo.itemText(0)
        assert "read" in dialog.library_combo.itemText(0)
        assert dialog.format_combo.currentData() == "bibtex"
        assert (
            tuple(
                dialog.format_combo.itemData(index)
                for index in range(dialog.format_combo.count())
            )
            == ExportAPI.BIBLIOGRAPHY_FORMATS
        )
        assert dialog.export_button.isEnabled()
    finally:
        dialog.close()


class FakeSaveDialog:
    """Capture native save-dialog parameters without opening a modal window."""

    class AcceptMode:
        AcceptSave = object()

    instances: ClassVar[list[FakeSaveDialog]] = []
    selected_path: Path
    result_code: int = QDialog.DialogCode.Accepted

    def __init__(self, *args: object) -> None:
        self.args = args
        self.accept_mode: object | None = None
        self.default_suffix: str | None = None
        self.instances.append(self)

    def setAcceptMode(self, mode: object) -> None:
        self.accept_mode = mode

    def setDefaultSuffix(self, suffix: str) -> None:
        self.default_suffix = suffix

    def exec(self) -> int:
        return self.result_code

    def selectedFiles(self) -> list[str]:
        return [str(self.selected_path)]


@pytest.mark.parametrize(
    ("format_key", "suffix"),
    [("bibtex", ".bib"), ("ris", ".ris"), ("aastex", ".tex"), ("ads", ".txt")],
)
def test_export_uses_matching_suffix_and_atomic_save(
    application: QApplication,
    qtbot: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    format_key: str,
    suffix: str,
) -> None:
    """A successful non-empty export opens a suffixed atomic output file."""
    import adsfolio.gui.ads_library_export_dialog as export_module

    harness = FakeJobHarness()
    dialog = _dialog(application, qtbot, harness)
    try:
        destination = _load_one_library(dialog, harness, name="My / Papers")
        dialog.format_combo.setCurrentIndex(dialog.format_combo.findData(format_key))
        output = tmp_path / f"chosen-name{suffix}"
        FakeSaveDialog.instances.clear()
        FakeSaveDialog.selected_path = output
        monkeypatch.setattr(export_module, "QFileDialog", FakeSaveDialog)

        dialog.export_button.click()
        export_job = harness.started[-1]
        assert export_job.request == AdsLibraryRequest(
            AdsLibraryOperation.EXPORT,
            library_id=destination.id,
            name=destination.name,
            export_format=format_key,
        )
        export_job.succeed(LibraryExportResult("export text\n", ("B1",)))
        export_job.finish()

        assert output.read_text(encoding="utf-8") == "export text\n"
        assert FakeSaveDialog.instances[-1].default_suffix == suffix.removeprefix(".")
        assert (
            FakeSaveDialog.instances[-1].accept_mode
            is FakeSaveDialog.AcceptMode.AcceptSave
        )
    finally:
        dialog.close()


def test_empty_export_does_not_open_save_dialog(
    application: QApplication,
    qtbot: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty remote library is reported without creating an output file."""
    import adsfolio.gui.ads_library_export_dialog as export_module

    harness = FakeJobHarness()
    dialog = _dialog(application, qtbot, harness)
    try:
        _load_one_library(dialog, harness)
        opened: list[object] = []
        monkeypatch.setattr(
            export_module,
            "QFileDialog",
            lambda *args, **kwargs: opened.append((args, kwargs)),
        )
        dialog.export_button.click()
        harness.started[-1].succeed(LibraryExportResult("", ()))
        harness.started[-1].finish()

        assert opened == []
        assert "empty" in dialog.result_label.text()
    finally:
        dialog.close()


def test_save_dialog_cancel_never_writes(
    application: QApplication,
    qtbot: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Cancelling the native save dialog leaves the destination untouched."""
    import adsfolio.gui.ads_library_export_dialog as export_module

    harness = FakeJobHarness()
    dialog = _dialog(application, qtbot, harness)
    try:
        _load_one_library(dialog, harness)
        output = tmp_path / "cancelled.bib"
        output.write_text("existing", encoding="utf-8")
        FakeSaveDialog.instances.clear()
        FakeSaveDialog.selected_path = output
        FakeSaveDialog.result_code = QDialog.DialogCode.Rejected
        monkeypatch.setattr(export_module, "QFileDialog", FakeSaveDialog)

        dialog.export_button.click()
        harness.started[-1].succeed(LibraryExportResult("replacement", ("B1",)))
        harness.started[-1].finish()

        assert output.read_text(encoding="utf-8") == "existing"
        assert dialog.status_label.text() == "Export cancelled; no file was written."
    finally:
        FakeSaveDialog.result_code = QDialog.DialogCode.Accepted
        dialog.close()


@pytest.mark.parametrize(
    ("failure_kind", "write_result"),
    [("commit", None), ("write", -1)],
)
def test_save_failure_preserves_existing_bytes_and_shows_error(
    application: QApplication,
    qtbot: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_kind: str,
    write_result: int | None,
) -> None:
    """QSaveFile failures never replace an existing output file."""
    import adsfolio.gui.ads_library_export_dialog as export_module

    class FailingSaveFile:
        def __init__(self, _path: str) -> None:
            self.cancelled = False

        def open(self, _mode: object) -> bool:
            return True

        def write(self, payload: bytes) -> int:
            return write_result if write_result is not None else len(payload)

        def cancelWriting(self) -> None:
            self.cancelled = True

        def commit(self) -> bool:
            return False

        def errorString(self) -> str:
            return f"simulated {failure_kind} failure"

    harness = FakeJobHarness()
    dialog = _dialog(application, qtbot, harness)
    try:
        _load_one_library(dialog, harness)
        output = tmp_path / "existing.bib"
        output.write_text("existing", encoding="utf-8")
        FakeSaveDialog.instances.clear()
        FakeSaveDialog.selected_path = output
        FakeSaveDialog.result_code = QDialog.DialogCode.Accepted
        monkeypatch.setattr(export_module, "QFileDialog", FakeSaveDialog)
        monkeypatch.setattr(export_module, "QSaveFile", FailingSaveFile)

        dialog.export_button.click()
        harness.started[-1].succeed(LibraryExportResult("replacement", ("B1",)))
        harness.started[-1].finish()

        assert output.read_text(encoding="utf-8") == "existing"
        assert failure_kind in dialog.status_label.text()
    finally:
        dialog.close()


def test_cancelled_export_never_saves_result(
    application: QApplication,
    qtbot: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A cancellation request suppresses saving even if the worker finishes with text."""
    import adsfolio.gui.ads_library_export_dialog as export_module

    harness = FakeJobHarness()
    dialog = _dialog(application, qtbot, harness)
    try:
        _load_one_library(dialog, harness)
        opened: list[object] = []
        monkeypatch.setattr(
            export_module,
            "QFileDialog",
            lambda *args, **kwargs: opened.append((args, kwargs)),
        )
        output = tmp_path / "never-created"
        dialog.export_button.click()
        export_job = harness.started[-1]
        dialog.cancel_button.click()
        assert export_job.cancelled
        export_job.succeed(LibraryExportResult("text", ("B1",)))
        export_job.finish()

        assert opened == []
        assert not output.exists()
    finally:
        dialog.close()


def test_failure_is_visible_and_close_waits_for_worker(
    application: QApplication,
    qtbot: Any,
) -> None:
    """Errors remain visible and closing waits for the active job to finish."""
    harness = FakeJobHarness()
    dialog = _dialog(application, qtbot, harness)
    try:
        _load_one_library(dialog, harness)
        dialog.export_button.click()
        export_job = harness.started[-1]
        export_job.fail(AdsLibraryFailure(kind="connection", message="offline"))
        assert "offline" in dialog.status_label.text()

        dialog.show()
        dialog.reject()
        assert export_job.cancelled
        assert dialog.result() == 0
        export_job.finish()
        assert dialog.result() == QDialog.DialogCode.Rejected
    finally:
        if dialog.result() == 0:
            dialog.close()
