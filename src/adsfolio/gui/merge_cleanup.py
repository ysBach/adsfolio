"""Offer reversible original-file cleanup after a verified PDF merge."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QFile, Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..pdf.annotation_merge import MergeInput, MergeReport
from ..pdf.files import sha256_file


def cleanup_problem(report: MergeReport, originals: list[MergeInput]) -> str | None:
    """Check the saved output and original contents before moving any files."""
    if not report.complete or not report.output_sha256 or not report.input_files:
        return "Cleanup needs a complete, verified merge. Resolve the remaining annotations and save a new merged copy."
    output = Path(report.output_path)
    try:
        if (
            output.is_symlink()
            or not output.is_file()
            or sha256_file(output) != report.output_sha256
        ):
            return "The merged PDF is missing or changed. Keep the originals and save a new verified merged copy."
        allowed = set(report.input_files)
        for original in originals:
            path = Path(original.path)
            if original not in allowed:
                return f"This file was not an input to this merge: {path}"
            if path.is_symlink() or not path.is_file():
                return f"This original is missing or is a symbolic link: {path}. Keep it and refresh the list."
            if path.resolve() == output.resolve() or path.samefile(output):
                return f"The merged result cannot be moved to Trash: {path}"
            if sha256_file(path) != original.sha256:
                return f"This original changed after the merge: {path}. Keep it and merge its latest annotations first."
    except OSError as exc:
        return f"Could not check the files: {exc}. Keep the originals and try again."
    return None


class MergeCleanupDialog(QDialog):
    """Let the user select original PDFs to move to the system Trash."""

    def __init__(self, report: MergeReport, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.report = report
        self.trashed_paths: list[Path] = []
        self.originals = tuple(dict.fromkeys(report.input_files))
        self.setWindowTitle("Keep the merged PDF and remove originals")
        self.resize(880, 440)
        layout = QVBoxLayout(self)
        heading = QLabel(f"Keep this verified merged PDF:\n{report.output_path}", self)
        heading.setWordWrap(True)
        heading.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(heading)
        description = QLabel(
            "Select original PDFs to move to Trash. You can restore them from Trash. Unchecked files stay in place.",
            self,
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        omissions = [
            f"{len(report.waived)} annotation(s) were explicitly waived in this merge and are not copied into the merged PDF.",
            "Only annotations were copied from the other editions. Their pages, bookmarks, attachments, and form data were not transferred. The canonical PDF supplies the retained pages and existing content.",
        ]
        if len(self.originals) > 2:
            omissions.append("Earlier merge steps may also have waived annotations.")
        self.omissions = QLabel("\n\n".join(omissions), self)
        self.omissions.setWordWrap(True)
        layout.addWidget(self.omissions)
        self.file_list = QListWidget(self)
        self.file_list.setWordWrap(True)
        for original in self.originals:
            item = QListWidgetItem(original.path, self.file_list)
            item.setToolTip(original.path)
            if Path(original.path).exists():
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked)
            else:
                item.setText(original.path + "\nAlready absent; nothing to remove.")
                item.setFlags(Qt.NoItemFlags)
        layout.addWidget(self.file_list, 1)
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.keep_button = QPushButton("Keep originals / Close", self)
        self.keep_button.setDefault(True)
        self.trash_button = QPushButton("Move selected originals to Trash", self)
        self.trash_button.setAutoDefault(False)
        row.addWidget(self.keep_button)
        row.addWidget(self.trash_button)
        layout.addLayout(row)
        self.keep_button.clicked.connect(self.reject)
        self.trash_button.clicked.connect(self.move_selected_to_trash)
        self.file_list.itemChanged.connect(self._selection_changed)
        self._selection_changed()

    def _selection_changed(self) -> None:
        count = sum(
            self.file_list.item(row).checkState() == Qt.Checked
            for row in range(self.file_list.count())
        )
        self.trash_button.setText(f"Move {count} original PDF(s) to Trash")
        self.trash_button.setEnabled(count > 0 and self.report.complete)

    def move_selected_to_trash(self) -> None:
        """Move only checked, unchanged inputs after checking the merged result."""
        selected = [
            original
            for row, original in enumerate(self.originals)
            if self.file_list.item(row).checkState() == Qt.Checked
        ]
        if not selected:
            return
        problem = cleanup_problem(self.report, selected)
        if problem:
            self.status.setText(problem)
            return
        failures = []
        for original in selected:
            # Recheck the output before each move, including after a failed move.
            problem = cleanup_problem(self.report, [original])
            if problem:
                failures.append(problem)
                break
            path = Path(original.path)
            try:
                result = QFile.moveToTrash(str(path))
                success = result[0] if isinstance(result, tuple) else result
            except (OSError, RuntimeError) as exc:
                success = False
                failures.append(f"{path}: {exc}")
            if success:
                self.trashed_paths.append(path)
                row = self.originals.index(original)
                item = self.file_list.item(row)
                item.setCheckState(Qt.Unchecked)
                item.setFlags(Qt.NoItemFlags)
                item.setText(str(path) + "\nMoved to Trash.")
            elif not any(str(path) in message for message in failures):
                failures.append(
                    f"Could not move {path} to Trash. Check file permissions or Trash availability, then retry. No permanent-delete fallback is used."
                )
        self._selection_changed()
        if self.trashed_paths:
            self.keep_button.setText("Keep remaining / Close")
        self.status.setText(
            f"Moved {len(self.trashed_paths)} original PDF(s) to Trash.\n"
            + "\n".join(failures)
        )
        if not failures:
            self.accept()
