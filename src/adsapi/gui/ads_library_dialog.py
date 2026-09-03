"""Dialogs for selecting and populating an ADS library."""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..library_sync import (
    AddDocumentsResult,
    BibcodeSelection,
    LibraryDestination,
    collect_identified_bibcodes,
)
from .ads_library_jobs import (
    AdsLibraryFailure,
    AdsLibraryJob,
    AdsLibraryOperation,
    AdsLibraryRequest,
)

__all__ = ["AdsLibraryDialog", "CreateAdsLibraryDialog"]


class CreateAdsLibraryDialog(QDialog):
    """Collect the values needed to create an empty ADS library."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create ADS Library")

        form = QFormLayout(self)
        self.name_edit = QLineEdit(self)
        self.name_edit.setPlaceholderText("Library name")
        self.description_edit = QPlainTextEdit(self)
        self.description_edit.setPlaceholderText("Optional description")
        self.description_edit.setMaximumHeight(96)
        self.public_checkbox = QRadioButton("Public", self)
        self.private_checkbox = QRadioButton("Private", self)
        self.private_checkbox.setChecked(True)
        visibility = QHBoxLayout()
        visibility.addWidget(self.public_checkbox)
        visibility.addWidget(self.private_checkbox)
        visibility.addStretch(1)
        self.error_label = QLabel(self)
        self.error_label.setStyleSheet("color: #b00020;")
        self.error_label.setWordWrap(True)

        form.addRow("Name", self.name_edit)
        form.addRow("Description", self.description_edit)
        form.addRow("Visibility", visibility)
        form.addRow(self.error_label)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            self,
        )
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        form.addRow(self.button_box)

    def values(self) -> tuple[str, str, bool] | None:
        """Return trimmed creation values when the name is valid."""
        name = self.name_edit.text().strip()
        if not name:
            return None
        return (
            name,
            self.description_edit.toPlainText().strip(),
            self.public_checkbox.isChecked(),
        )

    def accept(self) -> None:
        """Accept only a non-empty trimmed library name."""
        if self.values() is None:
            self.error_label.setText("Library name must be non-empty.")
            self.name_edit.setFocus()
            return
        self.error_label.clear()
        super().accept()


class AdsLibraryDialog(QDialog):
    """Choose an ADS destination and add a snapshot of local Bibcodes."""

    def __init__(
        self,
        root: Path,
        selected_records: Sequence[Mapping[str, object]],
        all_records: Sequence[Mapping[str, object]],
        job_factory: Callable[..., AdsLibraryJob] = AdsLibraryJob,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.root = root
        self._job_factory = job_factory
        self._selected_records = tuple(
            copy.deepcopy(record) for record in selected_records
        )
        self._all_records = tuple(copy.deepcopy(record) for record in all_records)
        self.selected_selection = collect_identified_bibcodes(self._selected_records)
        self.all_selection = collect_identified_bibcodes(self._all_records)

        self._destinations: list[LibraryDestination] = []
        self._destination: LibraryDestination | None = None
        self._destination_documents: tuple[str, ...] | None = None
        self._active_job: QObject | None = None
        self._active_request: AdsLibraryRequest | None = None
        self._cancel_requested = False
        self._close_requested = False
        self._result: AddDocumentsResult | None = None
        self._retry_bibcodes: tuple[str, ...] = ()
        self._retry_requires_raw_confirmation = False

        self.setWindowTitle("Add to ADS Library")
        self.resize(760, 620)
        self._build_ui()
        self._connect_ui()
        self._set_scope_from_snapshots()
        self._update_controls()
        self._start_job(AdsLibraryRequest(AdsLibraryOperation.LIST))

    @property
    def active_selection(self) -> BibcodeSelection:
        """Return the immutable selection represented by the active scope."""
        return self._active_selection()

    @property
    def destination_documents(self) -> tuple[str, ...] | None:
        """Return complete preflight documents, or `None` when unknown."""
        return self._destination_documents

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        source_box = QWidget(self)
        source_layout = QHBoxLayout(source_box)
        source_layout.setContentsMargins(0, 0, 0, 0)
        source_layout.addWidget(QLabel("Source", source_box))
        self.selected_scope = QRadioButton("Selected rows", source_box)
        self.all_scope = QRadioButton(
            "All identified files in this local library",
            source_box,
        )
        source_layout.addWidget(self.selected_scope)
        source_layout.addWidget(self.all_scope)
        source_layout.addStretch(1)
        layout.addWidget(source_box)

        counts = QFormLayout()
        self.source_count_label = QLabel(self)
        self.eligible_count_label = QLabel(self)
        self.ineligible_count_label = QLabel(self)
        self.local_duplicate_count_label = QLabel(self)
        self.already_present_label = QLabel("Unknown", self)
        counts.addRow("Source files", self.source_count_label)
        counts.addRow("Eligible unique Bibcodes", self.eligible_count_label)
        counts.addRow("Ineligible files", self.ineligible_count_label)
        counts.addRow("Local duplicate Bibcodes", self.local_duplicate_count_label)
        counts.addRow("Already present", self.already_present_label)
        layout.addLayout(counts)

        destination_heading = QHBoxLayout()
        destination_heading.addWidget(QLabel("Remote ADS library", self))
        destination_heading.addStretch(1)
        self.refresh_button = QPushButton("Refresh", self)
        self.create_button = QPushButton("Create Library…", self)
        destination_heading.addWidget(self.refresh_button)
        destination_heading.addWidget(self.create_button)
        layout.addLayout(destination_heading)

        self.destination_table = QTableWidget(0, 4, self)
        self.destination_table.setHorizontalHeaderLabels(
            ["Name", "Documents", "Visibility", "Permission"]
        )
        self.destination_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.destination_table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )
        self.destination_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.destination_table.setMinimumHeight(180)
        layout.addWidget(self.destination_table, 1)

        self.preflight_warning_label = QLabel(self)
        self.preflight_warning_label.setWordWrap(True)
        self.preflight_warning_label.setStyleSheet("color: #8a4b08;")
        layout.addWidget(self.preflight_warning_label)

        self.status_label = QLabel(self)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.result_label = QLabel(self)
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)
        self.retry_warning_label = QLabel(self)
        self.retry_warning_label.setWordWrap(True)
        self.retry_warning_label.setStyleSheet("color: #8a4b08;")
        layout.addWidget(self.retry_warning_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = QPushButton("Cancel", self)
        self.retry_button = QPushButton("Retry Remaining", self)
        self.add_button = QPushButton("Add to ADS Library", self)
        buttons.addWidget(self.retry_button)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.add_button)
        layout.addLayout(buttons)

    def _connect_ui(self) -> None:
        self.selected_scope.toggled.connect(self._scope_changed)
        self.all_scope.toggled.connect(self._scope_changed)
        self.destination_table.itemSelectionChanged.connect(
            self._destination_selection_changed
        )
        self.refresh_button.clicked.connect(self._refresh_destinations)
        self.create_button.clicked.connect(self._create_library)
        self.cancel_button.clicked.connect(self._cancel_active_job)
        self.add_button.clicked.connect(self._add_documents)
        self.retry_button.clicked.connect(self._retry_remaining)

    def _set_scope_from_snapshots(self) -> None:
        self.selected_scope.setEnabled(bool(self._selected_records))
        if self._selected_records:
            self.selected_scope.setChecked(True)
        else:
            self.all_scope.setChecked(True)
        self._recompute_counts()

    def _scope_changed(self, checked: bool) -> None:
        if not checked:
            return
        self._result = None
        self._retry_bibcodes = ()
        self._retry_requires_raw_confirmation = False
        self.result_label.clear()
        self.retry_warning_label.clear()
        self._recompute_counts()
        self._update_controls()

    def _active_selection(self) -> BibcodeSelection:
        return (
            self.selected_selection
            if self.selected_scope.isChecked()
            else self.all_selection
        )

    def _recompute_counts(self) -> None:
        selection = self._active_selection()
        self.source_count_label.setText(str(selection.source_files))
        self.eligible_count_label.setText(str(len(selection.bibcodes)))
        self.ineligible_count_label.setText(str(selection.ineligible_files))
        self.local_duplicate_count_label.setText(str(selection.local_duplicate_files))
        self._update_preflight_display()

    def _update_preflight_display(self) -> None:
        if self._destination_documents is None:
            self.already_present_label.setText("Unknown")
            if self._destination is not None:
                self.preflight_warning_label.setText(
                    "Warning: remote duplicate preflight is unknown; all eligible "
                    "Bibcodes will be submitted after confirmation."
                )
            else:
                self.preflight_warning_label.clear()
            return
        remote = set(self._destination_documents)
        count = sum(bibcode in remote for bibcode in self._active_selection().bibcodes)
        self.already_present_label.setText(str(count))
        self.preflight_warning_label.clear()

    def _populate_destinations(
        self, destinations: Sequence[LibraryDestination]
    ) -> None:
        self._destinations = list(destinations)
        self.destination_table.setRowCount(0)
        for row, destination in enumerate(self._destinations):
            self.destination_table.insertRow(row)
            values = (
                destination.name,
                str(destination.num_documents),
                "Public" if destination.public else "Private",
                destination.permission,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if not destination.writable:
                    item.setFlags(
                        item.flags()
                        & ~Qt.ItemFlag.ItemIsEnabled
                        & ~Qt.ItemFlag.ItemIsSelectable
                    )
                self.destination_table.setItem(row, column, item)
        self._destination = None
        self._destination_documents = None
        self._update_preflight_display()

    @Slot()
    def _destination_selection_changed(self) -> None:
        if self._active_job is not None or self._close_requested:
            return
        row = self.destination_table.currentRow()
        if not 0 <= row < len(self._destinations):
            return
        destination = self._destinations[row]
        if not destination.writable:
            return
        self._destination = destination
        self._destination_documents = None
        self._result = None
        self.result_label.clear()
        self.retry_warning_label.clear()
        self._update_preflight_display()
        self._start_job(
            AdsLibraryRequest(
                AdsLibraryOperation.LOAD_DOCUMENTS,
                library_id=destination.id,
            )
        )

    def _refresh_destinations(self) -> None:
        if self._active_job is not None:
            return
        self._destination = None
        self._destination_documents = None
        self._start_job(AdsLibraryRequest(AdsLibraryOperation.LIST))

    def _create_library(self) -> None:
        if self._active_job is not None:
            return
        dialog = CreateAdsLibraryDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        if values is not None:
            self._submit_create_values(values)

    def _submit_create_values(self, values: tuple[str, str, bool]) -> None:
        name, description, public = values
        self._start_job(
            AdsLibraryRequest(
                AdsLibraryOperation.CREATE,
                name=name,
                description=description,
                public=public,
            )
        )

    def _start_job(self, request: AdsLibraryRequest) -> None:
        if self._active_job is not None:
            return
        job = self._job_factory(request, parent=self)
        self._active_job = job
        self._active_request = request
        self._cancel_requested = False
        job.succeeded.connect(self._job_succeeded)
        job.failed.connect(self._job_failed)
        job.finished.connect(self._job_finished)
        self._set_status_for_request(request)
        self._update_controls()
        job.start()

    def _set_status_for_request(self, request: AdsLibraryRequest) -> None:
        messages = {
            AdsLibraryOperation.LIST: "Loading ADS libraries…",
            AdsLibraryOperation.LOAD_DOCUMENTS: "Checking remote documents…",
            AdsLibraryOperation.CREATE: "Creating empty ADS library…",
            AdsLibraryOperation.ADD: "Adding documents to ADS library…",
            AdsLibraryOperation.RETRY: "Reconciling remote documents before retry…",
        }
        self.status_label.setText(messages[request.operation])

    def _update_controls(self) -> None:
        busy = self._active_job is not None
        destination_writable = (
            self._destination is not None and self._destination.writable
        )
        has_bibcodes = bool(self._active_selection().bibcodes)
        self.selected_scope.setEnabled(bool(self._selected_records) and not busy)
        self.all_scope.setEnabled(not busy)
        self.destination_table.setEnabled(not busy)
        self.refresh_button.setEnabled(not busy)
        self.create_button.setEnabled(not busy)
        self.add_button.setEnabled(not busy and destination_writable and has_bibcodes)
        self.retry_button.setEnabled(
            not busy and bool(self._retry_bibcodes) and destination_writable
        )
        self.cancel_button.setEnabled(busy and not self._cancel_requested)

    def _cancel_active_job(self) -> None:
        if self._active_job is None or self._cancel_requested:
            return
        self._cancel_requested = True
        cancel = getattr(self._active_job, "cancel", None)
        if callable(cancel):
            cancel()
        if (
            self._active_request is not None
            and self._active_request.operation is AdsLibraryOperation.ADD
        ):
            self.status_label.setText(
                "Cancellation requested. An in-flight request may finish; "
                "cancellation takes effect before the next batch."
            )
        else:
            self.status_label.setText(
                "Cancellation requested; waiting for the operation to finish…"
            )
        self._update_controls()

    @Slot(object)
    def _job_succeeded(self, result: object) -> None:
        request = self._active_request
        if request is None:
            return
        if request.operation is AdsLibraryOperation.LIST:
            self._populate_destinations(result if isinstance(result, tuple) else ())
            self.status_label.setText("ADS libraries loaded.")
        elif request.operation is AdsLibraryOperation.LOAD_DOCUMENTS:
            if isinstance(result, tuple) and all(
                isinstance(item, str) for item in result
            ):
                self._destination_documents = tuple(result)
                self._update_preflight_display()
                self.status_label.setText("Remote duplicate preflight complete.")
            else:
                self._destination_documents = None
                self._set_preflight_unknown(
                    "Remote duplicate preflight returned no complete result."
                )
        elif request.operation is AdsLibraryOperation.CREATE:
            if isinstance(result, LibraryDestination):
                self._insert_created_destination(result)
                self.status_label.setText("Empty ADS library created and selected.")
        elif request.operation is AdsLibraryOperation.ADD:
            if isinstance(result, AddDocumentsResult):
                self._show_add_result(result)
        elif request.operation is AdsLibraryOperation.RETRY:
            if isinstance(result, AddDocumentsResult):
                if result.error is not None:
                    self._show_retry_reconciliation_failure(result)
                else:
                    self._show_add_result(result)

    @Slot(object)
    def _job_failed(self, failure: object) -> None:
        request = self._active_request
        if request is None:
            return
        message = (
            failure.message if isinstance(failure, AdsLibraryFailure) else str(failure)
        )
        if request.operation is AdsLibraryOperation.LIST:
            self.status_label.setText(f"Could not load ADS libraries: {message}")
        elif request.operation is AdsLibraryOperation.LOAD_DOCUMENTS:
            self._destination_documents = None
            self._set_preflight_unknown(
                f"Remote duplicate preflight failed ({message}); confirmation remains available."
            )
        elif request.operation is AdsLibraryOperation.RETRY:
            self._show_retry_reconciliation_failure(
                AddDocumentsResult(
                    status_unknown=self._retry_bibcodes,
                    error=RuntimeError(message),
                )
            )
        else:
            self.status_label.setText(f"ADS library operation failed: {message}")

    def _set_preflight_unknown(self, message: str) -> None:
        self.already_present_label.setText("Unknown")
        self.preflight_warning_label.setText(f"Warning: {message}")
        self._update_controls()

    def _insert_created_destination(self, destination: LibraryDestination) -> None:
        self._destinations.append(destination)
        previous_block = self.destination_table.blockSignals(True)
        try:
            self._populate_destinations(self._destinations)
            row = next(
                index
                for index, item in enumerate(self._destinations)
                if item.id == destination.id
            )
            self.destination_table.selectRow(row)
            self._destination = destination
            self._destination_documents = None
        finally:
            self.destination_table.blockSignals(previous_block)
        self._update_preflight_display()

    def _to_submit(self) -> tuple[str, ...]:
        selection = self._active_selection()
        if self._destination_documents is None:
            return selection.bibcodes
        present = set(self._destination_documents)
        return tuple(
            bibcode for bibcode in selection.bibcodes if bibcode not in present
        )

    def _already_present_count(self) -> int | None:
        if self._destination_documents is None:
            return None
        remote = set(self._destination_documents)
        return sum(bibcode in remote for bibcode in self._active_selection().bibcodes)

    def _confirmation_text(self, to_submit: Sequence[str]) -> str:
        destination = self._destination
        assert destination is not None
        already_present = (
            "Unknown"
            if self._destination_documents is None
            else self.already_present_label.text()
        )
        warning = (
            "\nWarning: duplicate preflight is unknown, so all eligible Bibcodes "
            "will be submitted."
            if self._destination_documents is None
            else ""
        )
        selection = self._active_selection()
        return (
            f"Destination: {destination.name}\n"
            f"Permission: {destination.permission}\n"
            f"Eligible unique Bibcodes: {len(selection.bibcodes)}\n"
            f"Already present: {already_present}\n"
            f"Ineligible files: {selection.ineligible_files}\n"
            f"Local duplicate files: {selection.local_duplicate_files}\n"
            f"Submit: {len(to_submit)} Bibcodes?{warning}"
        )

    def _add_documents(self) -> None:
        if self._active_job is not None or self._destination is None:
            return
        to_submit = self._to_submit()
        if not to_submit:
            self.result_label.setText(
                "Everything is already present in the selected ADS library."
            )
            self._update_controls()
            return
        if (
            QMessageBox.question(
                self,
                "Confirm adding documents",
                self._confirmation_text(to_submit),
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self._retry_bibcodes = ()
        self._retry_requires_raw_confirmation = False
        self._start_job(
            AdsLibraryRequest(
                AdsLibraryOperation.ADD,
                library_id=self._destination.id,
                bibcodes=to_submit,
            )
        )

    def _show_add_result(self, result: AddDocumentsResult) -> None:
        self._result = result
        self._retry_requires_raw_confirmation = False
        source_order = self._active_selection().bibcodes
        remainder = {
            *result.failed,
            *result.status_unknown,
            *result.unattempted,
        }
        self._retry_bibcodes = tuple(
            bibcode for bibcode in source_order if bibcode in remainder
        )
        already_present = self._already_present_count()
        already_present_text = (
            str(already_present) if already_present is not None else "Unknown"
        )
        self.result_label.setText(
            "\n".join(
                (
                    f"Acknowledged: {len(result.acknowledged)}",
                    f"Already present: {already_present_text}",
                    f"Failed: {len(result.failed)}",
                    f"Status unknown: {len(result.status_unknown)}",
                    f"Unattempted: {len(result.unattempted)}",
                    f"Local duplicate files: {self._active_selection().local_duplicate_files}",
                    f"Ineligible files: {self._active_selection().ineligible_files}",
                    f"Cancelled: {'yes' if result.cancelled else 'no'}",
                )
            )
        )
        self.retry_warning_label.clear()
        self.status_label.setText("ADS library add operation complete.")
        self._update_controls()

    def _retry_remaining(self) -> None:
        if (
            self._active_job is not None
            or not self._retry_bibcodes
            or self._destination is None
        ):
            return
        if self._retry_requires_raw_confirmation:
            question = (
                "Remote reconciliation failed. A raw retry may resubmit Bibcodes "
                "with an uncertain server outcome. Submit the remaining Bibcodes "
                "again?"
            )
            if (
                QMessageBox.question(self, "Confirm raw retry", question)
                != QMessageBox.StandardButton.Yes
            ):
                return
            self._retry_requires_raw_confirmation = False
            self._start_job(
                AdsLibraryRequest(
                    AdsLibraryOperation.ADD,
                    library_id=self._destination.id,
                    bibcodes=self._retry_bibcodes,
                )
            )
            return
        self._start_job(
            AdsLibraryRequest(
                AdsLibraryOperation.RETRY,
                library_id=self._destination.id,
                bibcodes=self._retry_bibcodes,
            )
        )

    def _show_retry_reconciliation_failure(self, result: AddDocumentsResult) -> None:
        self._retry_requires_raw_confirmation = True
        self.retry_warning_label.setText(
            "Warning: retry reconciliation failed. A raw retry may resubmit the "
            "uncertain remainder and requires a second explicit confirmation."
        )
        self.result_label.setText(
            "\n".join(
                (
                    f"Acknowledged: {len(result.acknowledged)}",
                    f"Already present: {len(result.already_present)}",
                    f"Failed: {len(result.failed)}",
                    f"Status unknown: {len(result.status_unknown)}",
                    f"Unattempted: {len(result.unattempted)}",
                    f"Local duplicate files: {self._active_selection().local_duplicate_files}",
                    f"Ineligible files: {self._active_selection().ineligible_files}",
                )
            )
        )
        self.status_label.setText(
            "Retry reconciliation failed; review the warning before retrying."
        )
        self._update_controls()

    def _job_finished(self) -> None:
        self._active_job = None
        self._active_request = None
        self._cancel_requested = False
        self._update_controls()
        if self._close_requested:
            self._close_requested = False
            self.reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Request normal job cancellation before allowing the dialog to close."""
        if self._active_job is not None:
            self._close_requested = True
            self._cancel_active_job()
            event.ignore()
            return
        super().closeEvent(event)
