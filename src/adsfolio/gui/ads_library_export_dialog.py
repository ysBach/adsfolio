"""Dialog for exporting a complete ADS library."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtCore import QIODevice, QSaveFile, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..api.export import ExportAPI
from ..library_sync import LibraryDestination, LibraryExportResult
from .ads_library_jobs import (
    AdsLibraryFailure,
    AdsLibraryJob,
    AdsLibraryOperation,
    AdsLibraryRequest,
)

__all__ = ["ExportAdsLibraryDialog"]


_FORMAT_LABELS = {
    "bibtex": "BibTeX",
    "bibtexabs": "BibTeX with abstracts",
    "ris": "RIS",
    "endnote": "EndNote",
    "ads": "ADS tagged",
    "procite": "ProCite",
    "refworks": "RefWorks",
    "medlars": "MEDLARS",
    "aastex": "AASTeX",
    "mnras": "MNRAS",
    "icarus": "Icarus",
    "soph": "Solar Physics",
}

_WINDOWS_RESERVED_STEMS = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def _safe_library_stem(name: str) -> str:
    """Make a library name safe as a cross-platform filename stem."""
    stem = re.sub(
        r"[^\w .()\-]",
        "_",
        name,
        flags=re.UNICODE,
    ).strip(" .")
    if not stem:
        return "ads-library"
    if stem.split(".", 1)[0].upper() in _WINDOWS_RESERVED_STEMS:
        return f"ADS-{stem}"
    return stem


class ExportAdsLibraryDialog(QDialog):
    """Select an accessible ADS library and save its complete export."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        job_factory: Callable[..., AdsLibraryJob] = AdsLibraryJob,
    ) -> None:
        super().__init__(parent)
        self._job_factory = job_factory
        self._destinations: list[LibraryDestination] = []
        self._active_job: AdsLibraryJob | None = None
        self._active_request: AdsLibraryRequest | None = None
        self._cancel_requested = False
        self._close_requested = False
        self._pending_save: tuple[LibraryExportResult, AdsLibraryRequest] | None = None

        self.setWindowTitle("Export ADS Library")
        self.resize(520, 260)
        self._build_ui()
        self._connect_ui()
        self._populate_formats()
        self._update_controls()
        self._start_job(AdsLibraryRequest(AdsLibraryOperation.LIST))

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.library_combo = QComboBox(self)
        self.library_combo.setMinimumContentsLength(28)
        form.addRow("ADS library", self.library_combo)

        self.format_combo = QComboBox(self)
        form.addRow("Bibliography style", self.format_combo)
        layout.addLayout(form)

        self.status_label = QLabel(self)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.result_label = QLabel(self)
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)
        layout.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = QPushButton("Cancel", self)
        self.close_button = QPushButton("Close", self)
        self.export_button = QPushButton("Export", self)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.close_button)
        buttons.addWidget(self.export_button)
        layout.addLayout(buttons)

    def _connect_ui(self) -> None:
        self.library_combo.currentIndexChanged.connect(self._selection_changed)
        self.format_combo.currentIndexChanged.connect(self._selection_changed)
        self.cancel_button.clicked.connect(self._cancel_active_job)
        self.close_button.clicked.connect(self.reject)
        self.export_button.clicked.connect(self._export)

    def _populate_formats(self) -> None:
        self.format_combo.clear()
        for format_key in ExportAPI.BIBLIOGRAPHY_FORMATS:
            self.format_combo.addItem(
                _FORMAT_LABELS.get(format_key, format_key),
                format_key,
            )
        bibtex_index = self.format_combo.findData("bibtex")
        if bibtex_index >= 0:
            self.format_combo.setCurrentIndex(bibtex_index)

    def _populate_destinations(self, result: object) -> None:
        if not isinstance(result, Sequence) or isinstance(result, (str, bytes)):
            self._set_error("ADS returned an invalid library list.")
            return
        destinations = tuple(
            destination
            for destination in result
            if isinstance(destination, LibraryDestination)
        )
        if len(destinations) != len(result):
            self._set_error("ADS returned an invalid library list.")
            return

        self._destinations = list(destinations)
        self.library_combo.blockSignals(True)
        try:
            self.library_combo.clear()
            for destination in self._destinations:
                self.library_combo.addItem(
                    (
                        f"{destination.name} ({destination.num_documents} documents; "
                        f"{destination.permission})"
                    ),
                    destination.id,
                )
        finally:
            self.library_combo.blockSignals(False)
        if self._destinations:
            self.library_combo.setCurrentIndex(0)
            self.status_label.setText("ADS libraries loaded.")
        else:
            self.status_label.setText("No accessible ADS libraries were found.")
        self._update_controls()

    def _selected_destination(self) -> LibraryDestination | None:
        library_id = self.library_combo.currentData()
        return next(
            (
                destination
                for destination in self._destinations
                if destination.id == library_id
            ),
            None,
        )

    @Slot(int)
    def _selection_changed(self, _index: int) -> None:
        if self._active_job is None:
            self.result_label.clear()

    def _start_job(self, request: AdsLibraryRequest) -> None:
        if self._active_job is not None:
            return
        job = self._job_factory(request, parent=self)
        self._active_job = job
        self._active_request = request
        self._cancel_requested = False
        self._pending_save = None
        self.status_label.setStyleSheet("")
        job.succeeded.connect(self._job_succeeded)
        job.failed.connect(self._job_failed)
        job.finished.connect(self._job_finished)
        if request.operation is AdsLibraryOperation.LIST:
            self.status_label.setText("Loading ADS libraries…")
        else:
            self.status_label.setText("Exporting ADS library…")
        self._update_controls()
        job.start()

    def _export(self) -> None:
        if self._active_job is not None:
            return
        destination = self._selected_destination()
        format_key = self.format_combo.currentData()
        if destination is None or not isinstance(format_key, str):
            return
        self.result_label.clear()
        self._start_job(
            AdsLibraryRequest(
                AdsLibraryOperation.EXPORT,
                library_id=destination.id,
                name=destination.name,
                export_format=format_key,
            )
        )

    def _update_controls(self) -> None:
        busy = self._active_job is not None
        has_destination = self._selected_destination() is not None
        self.library_combo.setEnabled(not busy)
        self.format_combo.setEnabled(not busy)
        self.export_button.setEnabled(not busy and has_destination)
        self.cancel_button.setEnabled(busy and not self._cancel_requested)

    def _cancel_active_job(self) -> None:
        if self._active_job is None or self._cancel_requested:
            return
        self._cancel_requested = True
        cancel = getattr(self._active_job, "cancel", None)
        if callable(cancel):
            cancel()
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
            self._populate_destinations(result)
            return
        if request.operation is not AdsLibraryOperation.EXPORT:
            return
        if not isinstance(result, LibraryExportResult):
            self._set_error("ADS returned an invalid export result.")
            return
        if self._cancel_requested or result.cancelled:
            self.status_label.setText(
                "ADS library export cancelled; no file was written."
            )
            return
        if not result.bibcodes:
            self.result_label.setText(
                "The selected ADS library is empty; no file was written."
            )
            self.status_label.setText("ADS library export complete.")
            return
        self._pending_save = (result, request)
        self.status_label.setText("ADS library export received; preparing save…")

    @Slot(object)
    def _job_failed(self, failure: object) -> None:
        message = (
            failure.message if isinstance(failure, AdsLibraryFailure) else str(failure)
        )
        request = self._active_request
        if request is not None and request.operation is AdsLibraryOperation.LIST:
            self._set_error(f"Could not load ADS libraries: {message}")
        else:
            self._set_error(f"ADS library export failed: {message}")

    def _job_finished(self) -> None:
        was_cancelled = self._cancel_requested
        close_requested = self._close_requested
        pending_save = self._pending_save
        self._active_job = None
        self._active_request = None
        self._cancel_requested = False
        self._pending_save = None
        if was_cancelled and not close_requested:
            self.status_label.setText("ADS library operation cancelled.")
        self._update_controls()
        if close_requested:
            self._close_requested = False
            super().reject()
            return
        if pending_save is not None and not was_cancelled:
            self._save_result(*pending_save)

    def _save_result(
        self,
        result: LibraryExportResult,
        request: AdsLibraryRequest,
    ) -> None:
        format_key = request.export_format
        suffix = {
            "bibtex": ".bib",
            "bibtexabs": ".bib",
            "ris": ".ris",
            "aastex": ".tex",
            "mnras": ".tex",
            "icarus": ".tex",
            "soph": ".tex",
        }.get(format_key, ".txt")
        filename = f"{_safe_library_stem(request.name)}{suffix}"
        default_path = Path.home() / filename
        file_dialog = QFileDialog(
            self,
            "Save ADS library export",
            str(default_path),
            f"{_FORMAT_LABELS.get(format_key, format_key)} (*{suffix});;All files (*)",
        )
        file_dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        file_dialog.setDefaultSuffix(suffix.removeprefix("."))
        if file_dialog.exec() != QDialog.DialogCode.Accepted:
            self.status_label.setText("Export cancelled; no file was written.")
            return
        selected = file_dialog.selectedFiles()
        if not selected:
            self.status_label.setText("Export cancelled; no file was written.")
            return
        path = Path(selected[0]).expanduser()
        payload = result.text.encode("utf-8")
        save_file = QSaveFile(str(path))
        if not save_file.open(QIODevice.OpenModeFlag.WriteOnly):
            self._set_error(
                f"Could not open {path} for writing: {save_file.errorString()}"
            )
            return
        if save_file.write(payload) != len(payload):
            save_file.cancelWriting()
            self._set_error(f"Could not write the ADS export to {path}.")
            return
        if not save_file.commit():
            self._set_error(
                f"Could not commit the ADS export to {path}: {save_file.errorString()}"
            )
            return
        self.result_label.setText(f"Exported {len(result.bibcodes)} records to {path}.")
        self.status_label.setText("ADS library export complete.")

    def _set_error(self, message: str) -> None:
        self.status_label.setStyleSheet("color: #b00020;")
        self.status_label.setText(message)

    def reject(self) -> None:
        """Defer rejection until a running ADS operation has finished."""
        if self._active_job is not None:
            self._close_requested = True
            self._cancel_active_job()
            return
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Request cancellation before allowing a busy dialog to close."""
        if self._active_job is not None:
            self._close_requested = True
            self._cancel_active_job()
            event.ignore()
            return
        super().closeEvent(event)
