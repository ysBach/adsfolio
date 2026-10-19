"""Tests for the ADS library destination and confirmation dialogs."""

from __future__ import annotations

import os
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from adsfolio.gui.ads_library_dialog import AdsLibraryDialog, CreateAdsLibraryDialog
from adsfolio.gui.ads_library_jobs import (
    AdsLibraryFailure,
    AdsLibraryOperation,
    AdsLibraryRequest,
)
from adsfolio.library_sync import AddDocumentsResult, LibraryDestination


@pytest.fixture(scope="module")
def application() -> QApplication:
    """Provide one Qt application for dialog tests."""
    return QApplication.instance() or QApplication([])


def _destination(
    library_id: str,
    permission: str,
    *,
    name: str | None = None,
    public: bool = False,
    num_documents: int = 0,
) -> LibraryDestination:
    return LibraryDestination(
        id=library_id,
        name=name or library_id,
        permission=permission,
        public=public,
        num_documents=num_documents,
    )


def _record(bibcode: str, *, status: str = "resolved", document_id: int = 1) -> dict:
    return {
        "status": status,
        "document_id": document_id,
        "metadata": {"bibcode": bibcode},
    }


class FakeJob(QObject):
    """Synchronous or manually completed one-shot job for dialog tests."""

    succeeded = Signal(object)
    failed = Signal(object)
    finished = Signal()

    def __init__(
        self,
        request: AdsLibraryRequest,
        parent: QObject | None = None,
        *,
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
        if self.harness.auto_finish:
            self.finish()

    def cancel(self) -> None:
        self.cancelled = True

    def finish(self) -> None:
        if not self.running:
            return
        outcome = self.harness.next_outcome(self.request)
        if isinstance(outcome, AdsLibraryFailure):
            self.failed.emit(outcome)
        else:
            self.succeeded.emit(outcome)
        self.running = False
        self.finished.emit()


class FakeJobHarness:
    """Create fake jobs and deliver configured terminal values."""

    def __init__(self, *, auto_finish: bool = True) -> None:
        self.auto_finish = auto_finish
        self.started: list[FakeJob] = []
        self.outcomes: dict[AdsLibraryOperation, deque[object]] = defaultdict(deque)

    def factory(
        self, request: AdsLibraryRequest, parent: QObject | None = None
    ) -> FakeJob:
        return FakeJob(request, parent, harness=self)

    def queue(self, operation: AdsLibraryOperation, *outcomes: object) -> None:
        self.outcomes[operation].extend(outcomes)

    def next_outcome(self, request: AdsLibraryRequest) -> object:
        values = self.outcomes[request.operation]
        if values:
            return values.popleft()
        if request.operation is AdsLibraryOperation.LIST:
            return (
                _destination("read", "read", public=True, num_documents=4),
                _destination("write", "write", name="Writable library"),
                _destination("mystery", "mystery"),
            )
        if request.operation is AdsLibraryOperation.LOAD_DOCUMENTS:
            return ()
        if request.operation is AdsLibraryOperation.CREATE:
            return _destination(
                "created",
                "owner",
                name=request.name,
                public=request.public,
            )
        return AddDocumentsResult(acknowledged=request.bibcodes)


def _dialog(
    harness: FakeJobHarness,
    *,
    selected: list[dict[str, Any]] | None = None,
    all_records: list[dict[str, Any]] | None = None,
) -> AdsLibraryDialog:
    return AdsLibraryDialog(
        Path("/tmp/local-pdf-library"),
        selected or [],
        all_records if all_records is not None else selected or [],
        job_factory=harness.factory,
    )


def _select_destination(dialog: AdsLibraryDialog, row: int = 1) -> None:
    dialog.destination_table.selectRow(row)
    dialog.destination_table.cellClicked.emit(row, 0)


def test_scope_defaults_and_recomputes_exact_counts_without_ads_request(
    application: QApplication,
) -> None:
    harness = FakeJobHarness()
    selected = [
        _record("B1"),
        _record("B1", document_id=2),
        _record("BAD", status="unresolved"),
    ]
    all_records = [
        _record("B1"),
        _record("B2", document_id=2),
        _record("B2", document_id=3),
    ]
    all_records.append({"status": "resolved", "document_id": 4, "metadata": {}})
    dialog = _dialog(harness, selected=selected, all_records=all_records)
    try:
        assert dialog.selected_scope.isEnabled()
        assert dialog.selected_scope.isChecked()
        assert dialog.source_count_label.text() == "3"
        assert dialog.eligible_count_label.text() == "1"
        assert dialog.ineligible_count_label.text() == "1"
        assert dialog.local_duplicate_count_label.text() == "1"

        requests_before = len(harness.started)
        dialog.all_scope.click()

        assert len(harness.started) == requests_before
        assert dialog.source_count_label.text() == "4"
        assert dialog.eligible_count_label.text() == "2"
        assert dialog.ineligible_count_label.text() == "1"
        assert dialog.local_duplicate_count_label.text() == "1"
        assert dialog.active_selection.bibcodes == ("B1", "B2")
    finally:
        dialog.close()


def test_empty_selected_scope_is_disabled_and_all_identified_selected(
    application: QApplication,
) -> None:
    dialog = _dialog(FakeJobHarness(), selected=[], all_records=[_record("B1")])
    try:
        assert not dialog.selected_scope.isEnabled()
        assert dialog.all_scope.isChecked()
        assert dialog.active_selection.bibcodes == ("B1",)
    finally:
        dialog.close()


def test_destinations_show_metadata_and_disable_non_writable_rows(
    application: QApplication,
) -> None:
    harness = FakeJobHarness()
    dialog = _dialog(harness, selected=[_record("B1")])
    try:
        table = dialog.destination_table
        assert table.rowCount() == 3
        assert table.item(0, 0).text() == "read"
        assert table.item(0, 1).text() == "4"
        assert table.item(0, 2).text() == "Public"
        assert table.item(0, 3).text() == "read"
        assert not table.item(0, 0).flags() & Qt.ItemFlag.ItemIsEnabled
        assert table.item(1, 0).text() == "Writable library"
        assert table.item(1, 3).text() == "write"
        assert table.item(1, 0).flags() & Qt.ItemFlag.ItemIsEnabled
        assert not table.item(2, 0).flags() & Qt.ItemFlag.ItemIsEnabled
    finally:
        dialog.close()


def test_refresh_starts_list_and_disables_conflicting_controls_until_finished(
    application: QApplication,
) -> None:
    harness = FakeJobHarness(auto_finish=False)
    dialog = _dialog(harness, selected=[_record("B1")])
    try:
        dialog.refresh_button.click()
        assert harness.started[-1].request.operation is AdsLibraryOperation.LIST
        assert not dialog.refresh_button.isEnabled()
        assert not dialog.create_button.isEnabled()
        assert not dialog.add_button.isEnabled()
        assert dialog.cancel_button.isEnabled()
        harness.started[-1].finish()
        assert dialog.refresh_button.isEnabled()
    finally:
        dialog.close()


def test_writable_destination_loads_documents_and_reports_exact_duplicates(
    application: QApplication,
) -> None:
    harness = FakeJobHarness()
    harness.queue(AdsLibraryOperation.LOAD_DOCUMENTS, ("B2", "REMOTE"))
    dialog = _dialog(
        harness,
        selected=[
            _record("B1"),
            _record("B2", document_id=2),
            _record("B3", document_id=3),
        ],
    )
    try:
        _select_destination(dialog)
        assert (
            harness.started[-1].request.operation is AdsLibraryOperation.LOAD_DOCUMENTS
        )
        assert harness.started[-1].request.library_id == "write"
        assert dialog.already_present_label.text() == "1"
        assert dialog.preflight_warning_label.text() == ""
    finally:
        dialog.close()


def test_preflight_failure_is_unknown_but_confirmation_remains_available(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = FakeJobHarness()
    harness.queue(
        AdsLibraryOperation.LOAD_DOCUMENTS,
        AdsLibraryFailure(kind="timeout", message="offline"),
    )
    dialog = _dialog(harness, selected=[_record("B1")])
    questions: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda _parent, _title, text, *_args, **_kwargs: (
            questions.append(text) or QMessageBox.No
        ),
    )
    try:
        _select_destination(dialog)
        assert dialog.already_present_label.text() == "Unknown"
        assert "warning" in dialog.preflight_warning_label.text().lower()
        assert dialog.add_button.isEnabled()
        dialog.add_button.click()
        assert len(questions) == 1
        assert "Unknown" in questions[0]
        assert "preflight" in questions[0].lower()
        assert not any(
            job.request.operation is AdsLibraryOperation.ADD for job in harness.started
        )
    finally:
        dialog.close()


def test_create_dialog_validates_name_and_returns_trimmed_values(
    application: QApplication,
) -> None:
    dialog = CreateAdsLibraryDialog()
    try:
        dialog.name_edit.setText("  New library  ")
        dialog.description_edit.setPlainText("  Description  ")
        dialog.public_checkbox.setChecked(True)
        assert dialog.values() == ("New library", "Description", True)

        dialog.name_edit.clear()
        dialog.accept()
        assert dialog.values() is None
        assert dialog.error_label.text()
    finally:
        dialog.close()


def test_create_is_empty_and_inserts_selects_without_add_request(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = FakeJobHarness()
    created = _destination("created", "owner", name="Created")
    harness.queue(AdsLibraryOperation.CREATE, created)
    dialog = _dialog(harness, selected=[_record("B1")])
    create = CreateAdsLibraryDialog(dialog)
    create.name_edit.setText(" Created ")
    create.description_edit.setPlainText("Description")
    create.accept()
    monkeypatch.setattr(
        CreateAdsLibraryDialog,
        "exec",
        lambda _self: QMessageBox.Yes,
    )
    try:
        dialog._create_library = lambda: None  # type: ignore[method-assign]
        dialog._submit_create_values(("Created", "Description", True))
        request = harness.started[-1].request
        assert request.operation is AdsLibraryOperation.CREATE
        assert request.bibcodes == ()
        assert request.name == "Created"
        assert dialog.destination_table.currentRow() >= 0
        assert not any(
            job.request.operation is AdsLibraryOperation.ADD for job in harness.started
        )
    finally:
        create.close()
        dialog.close()


def test_add_requires_affirmative_confirmation_and_includes_exact_summary(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = FakeJobHarness()
    harness.queue(AdsLibraryOperation.LOAD_DOCUMENTS, ("B2",))
    dialog = _dialog(
        harness,
        selected=[
            _record("B1"),
            _record("B2", document_id=2),
            _record("B1", document_id=3),
        ],
    )
    questions: list[str] = []
    answer = iter((QMessageBox.No, QMessageBox.Yes))
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda _parent, _title, text, *_args, **_kwargs: (
            questions.append(text) or next(answer)
        ),
    )
    try:
        _select_destination(dialog)
        dialog.add_button.click()
        assert not any(
            job.request.operation is AdsLibraryOperation.ADD for job in harness.started
        )
        dialog.add_button.click()
        add_job = harness.started[-1]
        assert add_job.request.operation is AdsLibraryOperation.ADD
        assert add_job.request.bibcodes == ("B1",)
        summary = questions[-1]
        for expected in (
            "Writable library",
            "write",
            "eligible unique",
            "already present",
            "ineligible",
            "local duplicate",
            "submit",
        ):
            assert expected.lower() in summary.lower()
    finally:
        dialog.close()


def test_result_summary_counts_only_selected_documents_already_present(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = FakeJobHarness()
    harness.queue(AdsLibraryOperation.LOAD_DOCUMENTS, ("REMOTE", "B2", "OTHER"))
    harness.queue(
        AdsLibraryOperation.ADD,
        AddDocumentsResult(acknowledged=("B1",), already_present=("B2",)),
    )
    dialog = _dialog(
        harness,
        selected=[_record("B1"), _record("B2", document_id=2)],
    )
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.Yes
    )
    try:
        _select_destination(dialog)
        dialog.add_button.click()
        assert "Already present: 1" in dialog.result_label.text()
    finally:
        dialog.close()


def test_result_summary_uses_terminal_already_present_after_unknown_preflight(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = FakeJobHarness()
    harness.queue(
        AdsLibraryOperation.LOAD_DOCUMENTS,
        AdsLibraryFailure(kind="timeout", message="preflight unavailable"),
    )
    harness.queue(
        AdsLibraryOperation.ADD,
        AddDocumentsResult(
            acknowledged=("B2",),
            already_present=("B1",),
            failed=("B3",),
            status_unknown=("B4",),
            unattempted=("B5",),
            cancelled=True,
        ),
    )
    dialog = _dialog(
        harness,
        selected=[_record(f"B{i}", document_id=i) for i in range(1, 6)],
    )
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.Yes
    )
    try:
        _select_destination(dialog)
        assert dialog.already_present_label.text() == "Unknown"

        dialog.add_button.click()

        assert dialog.result_label.text().splitlines() == [
            "Acknowledged: 1",
            "Already present: 1",
            "Failed: 1",
            "Status unknown: 1",
            "Unattempted: 1",
            "Local duplicate files: 0",
            "Ineligible files: 0",
            "Cancelled: yes",
        ]
    finally:
        dialog.close()


@pytest.mark.parametrize(
    "result",
    [
        AddDocumentsResult(acknowledged=("B1",)),
        AddDocumentsResult(
            acknowledged=("B1",),
            failed=("B2",),
            status_unknown=("B3",),
            unattempted=("B4",),
        ),
        AddDocumentsResult(
            acknowledged=("B1",),
            unattempted=("B2",),
            cancelled=True,
        ),
    ],
)
def test_result_displays_every_category_and_exposes_retry_for_remainder(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    result: AddDocumentsResult,
) -> None:
    harness = FakeJobHarness()
    harness.queue(AdsLibraryOperation.ADD, result)
    dialog = _dialog(harness, selected=[_record(f"B{i}") for i in range(1, 5)])
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.Yes
    )
    try:
        _select_destination(dialog)
        dialog.add_button.click()
        text = dialog.result_label.text().lower()
        for expected in (
            "acknowledged",
            "already present",
            "failed",
            "status unknown",
            "unattempted",
            "local duplicate",
            "ineligible",
        ):
            assert expected in text
        assert dialog.retry_button.isEnabled() is bool(
            result.failed or result.status_unknown or result.unattempted
        )
    finally:
        dialog.close()


def test_retry_uses_reconciliation_and_preserves_remainder_order(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = FakeJobHarness()
    harness.queue(
        AdsLibraryOperation.ADD,
        AddDocumentsResult(
            acknowledged=("B1",),
            failed=("B3",),
            status_unknown=("B2",),
            unattempted=("B4",),
        ),
    )
    harness.queue(AdsLibraryOperation.RETRY, AddDocumentsResult(acknowledged=("B2",)))
    dialog = _dialog(harness, selected=[_record(f"B{i}") for i in range(1, 5)])
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.Yes
    )
    try:
        _select_destination(dialog)
        dialog.add_button.click()
        dialog.retry_button.click()
        retry_job = harness.started[-1]
        assert retry_job.request.operation is AdsLibraryOperation.RETRY
        assert retry_job.request.bibcodes == ("B2", "B3", "B4")
    finally:
        dialog.close()


def test_failed_retry_reconciliation_requires_second_warning_before_raw_add(
    application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = FakeJobHarness()
    harness.queue(
        AdsLibraryOperation.ADD,
        AddDocumentsResult(status_unknown=("B1",), unattempted=("B2",)),
    )
    harness.queue(
        AdsLibraryOperation.RETRY,
        AddDocumentsResult(
            status_unknown=("B1", "B2"),
            error=RuntimeError("reconciliation unavailable"),
        ),
    )
    questions: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda _parent, _title, text, *_args, **_kwargs: (
            questions.append(text) or QMessageBox.Yes
        ),
    )
    dialog = _dialog(harness, selected=[_record("B1"), _record("B2")])
    try:
        _select_destination(dialog)
        dialog.add_button.click()
        dialog.retry_button.click()
        assert "reconciliation" in dialog.retry_warning_label.text().lower()
        dialog.retry_button.click()
        assert len(questions) == 2
        assert "raw" in questions[-1].lower()
        assert harness.started[-1].request.operation is AdsLibraryOperation.ADD
        assert harness.started[-1].request.bibcodes == ("B1", "B2")
    finally:
        dialog.close()


def test_close_requests_cancel_and_rejects_only_after_finished(
    application: QApplication,
) -> None:
    harness = FakeJobHarness(auto_finish=False)
    dialog = _dialog(harness, selected=[_record("B1")])
    try:
        dialog.refresh_button.click()
        job = harness.started[-1]
        assert dialog.close() is False
        assert job.cancelled
        assert dialog._active_job is job
        assert dialog.result() == 0
        job.finish()
        assert dialog.result() == QDialog.DialogCode.Rejected
    finally:
        dialog.close()
