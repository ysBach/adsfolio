"""Compare copies of one paper and move an unwanted copy to the Trash.

The window lists each copy with its size, dates, and annotation count. The
user chooses which copy to move to the system Trash, the older or the newer
one. A copy moves only after a confirmation and only if its bytes did not
change after the window opened. The Trash keeps it for a restore.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QFile, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..pdf.files import sha256_file

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DuplicateCopy:
    """One PDF copy in a duplicate group.

    Parameters
    ----------
    root : Path
        Library root of the copy.
    relative_path : str
        Path inside `root`.
    sha256 : str or None
        Catalog digest of the copy. `None` when the catalog has no digest.
    first_seen_at : str or None
        When ADSfolio first saw the copy, as an ISO 8601 timestamp.
    document_id : int or None
        Catalog paper record of the copy.
    """

    root: Path
    relative_path: str
    sha256: str | None = None
    first_seen_at: str | None = None
    document_id: int | None = None

    @property
    def path(self) -> Path:
        """Return the absolute path of the copy."""
        return self.root / self.relative_path


def annotation_count(path: Path) -> int | None:
    """Return the number of annotations in a PDF, or `None` if unreadable."""
    try:
        import fitz

        with fitz.open(path) as document:
            return sum(
                sum(1 for _annotation in page.annots() or ()) for page in document
            )
    except Exception as exc:  # noqa: BLE001 -- PyMuPDF raises many types
        logger.debug("Cannot count annotations in %s: %s", path, exc)
        return None


def _local_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")


def _added(text: str | None) -> str:
    if not text:
        return ""
    try:
        return datetime.fromisoformat(text).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return text


def _size(size: int) -> str:
    return f"{size / 1_000_000:.2f} MB" if size >= 100_000 else f"{size:,} bytes"


class DuplicateReviewDialog(QDialog):
    """Show copies of one paper and move the copy that the user chooses to Trash.

    Parameters
    ----------
    parent : QWidget or None
        Parent widget.
    copies : Sequence[DuplicateCopy]
        Two or more copies.
    message : str
        Text above the list that says why the copies are shown.

    Signals
    -------
    trashed(DuplicateCopy)
        A copy moved to the Trash.
    show_in_table()
        The user asked to select the copies in the library table.
    """

    trashed = Signal(object)
    show_in_table = Signal()

    COLUMNS = ("Filename", "Folder", "Size", "Modified", "Added", "Annotations", "")

    def __init__(
        self,
        parent: QWidget | None,
        copies: Sequence[DuplicateCopy],
        message: str,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Duplicate PDFs")
        self.copies: list[DuplicateCopy] = list(copies)
        self._facts: dict[Path, tuple[int, int]] = {}
        layout = QVBoxLayout(self)
        intro = QLabel(message, self)
        intro.setWordWrap(True)
        layout.addWidget(intro)
        hint = QLabel(
            "Choose the copy to remove. Move to Trash asks first, and you can "
            "restore the file from the Trash.",
            self,
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.table = QTableWidget(0, len(self.COLUMNS), self)
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, len(self.COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        layout.addWidget(self.table, 1)
        self.status = QLabel("", self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        show = QPushButton("Show in Table", self)
        show.setToolTip("Select these PDFs in the library table")
        show.clicked.connect(self._show_in_table)
        buttons.addWidget(show)
        buttons.addStretch(1)
        keep = QPushButton("Keep All", self)
        keep.setToolTip("Close this window and keep every copy")
        keep.setDefault(True)
        keep.clicked.connect(self.accept)
        buttons.addWidget(keep)
        layout.addLayout(buttons)
        self._fill()
        self.resize(900, 140 + 34 * len(self.copies))

    def _fill(self) -> None:
        self.table.setRowCount(len(self.copies))
        for row, copy in enumerate(self.copies):
            path = copy.path
            try:
                stat = path.stat()
            except OSError:
                stat = None
            if stat is not None:
                self._facts[path] = (stat.st_size, stat.st_mtime_ns)
            count = annotation_count(path) if stat is not None else None
            folder = str(Path(copy.relative_path).parent)
            values = (
                path.name,
                str(copy.root.name if folder == "." else Path(copy.root.name, folder)),
                _size(stat.st_size) if stat else "missing",
                _local_time(stat.st_mtime) if stat else "",
                _added(copy.first_seen_at),
                "unknown" if count is None else str(count),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setToolTip(str(path))
                self.table.setItem(row, column, item)
            actions = QWidget(self.table)
            row_layout = QHBoxLayout(actions)
            row_layout.setContentsMargins(2, 0, 2, 0)
            open_button = QPushButton("Open", actions)
            open_button.clicked.connect(lambda _checked=False, p=path: self._open(p))
            trash = QPushButton("Move to Trash…", actions)
            trash.setToolTip(f"Move {path.name} to the Trash")
            trash.setEnabled(stat is not None and len(self.copies) > 1)
            trash.clicked.connect(lambda _checked=False, c=copy: self.move_to_trash(c))
            row_layout.addWidget(open_button)
            row_layout.addWidget(trash)
            self.table.setCellWidget(row, len(self.COLUMNS) - 1, actions)

    def _open(self, path: Path) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _show_in_table(self) -> None:
        self.show_in_table.emit()
        self.accept()

    def _unchanged(self, copy: DuplicateCopy) -> bool:
        """Return whether the copy still has the bytes that the window shows."""
        path = copy.path
        try:
            if copy.sha256:
                return sha256_file(path) == copy.sha256
            stat = path.stat()
        except OSError:
            return False
        return self._facts.get(path) == (stat.st_size, stat.st_mtime_ns)

    def move_to_trash(self, copy: DuplicateCopy) -> bool:
        """Ask, check, and move one copy to the system Trash.

        Returns
        -------
        bool
            `True` when the copy moved to the Trash.
        """
        path = copy.path
        answer = QMessageBox.question(
            self,
            "Move to Trash",
            f"Move this PDF to the Trash?\n\n{path}\n\n"
            "You can restore it from the Trash.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        if not self._unchanged(copy):
            QMessageBox.warning(
                self,
                "PDF changed",
                f"{path.name} changed after this window opened. Nothing moved. "
                "Close the window and review the PDFs again.",
            )
            return False
        try:
            result = QFile.moveToTrash(str(path))
            moved = result[0] if isinstance(result, tuple) else bool(result)
        except (OSError, RuntimeError) as exc:
            logger.warning("Cannot move %s to Trash: %s", path, exc)
            moved = False
        if not moved or path.exists():
            QMessageBox.warning(
                self,
                "Move to Trash failed",
                f"{path.name} could not be moved to the Trash. The file is unchanged.",
            )
            return False
        logger.info("Moved duplicate PDF to Trash: %s", path)
        self.copies.remove(copy)
        self.trashed.emit(copy)
        self._fill()
        self.status.setText(
            f"Moved {path.name} to the Trash."
            + (" One copy remains." if len(self.copies) == 1 else "")
        )
        return True


__all__ = ["DuplicateCopy", "DuplicateReviewDialog", "annotation_count"]
