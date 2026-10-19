"""Run a local library refresh without blocking the GUI."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, Slot

from ..pdf.registry import LibraryRefresh, PdfRegistry


class _RefreshThread(QThread):
    """Own the catalog connection used for one filesystem scan."""

    def __init__(self, root: Path, recursive: bool, parent: QObject) -> None:
        super().__init__(parent)
        self.root = root
        self.recursive = recursive
        self.result: LibraryRefresh | None = None
        self.error: str | None = None

    def run(self) -> None:
        """Reconcile the library and close its thread-owned catalog."""
        registry: PdfRegistry | None = None
        try:
            registry = PdfRegistry(self.root)
            self.result = registry.refresh_local_files(
                recursive=self.recursive, cancelled=self.isInterruptionRequested
            )
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.error = str(exc)
        finally:
            if registry is not None:
                registry.close()


class LibraryRefreshJob(QObject):
    """Expose a local scan through the GUI's existing job lifecycle."""

    output = Signal(str)
    failed = Signal(str)
    finished = Signal(int)
    refreshed = Signal(object)

    def __init__(
        self, root: Path, recursive: bool, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self.root = root
        self._thread = _RefreshThread(root, recursive, self)
        self._thread.finished.connect(self._complete)
        self._started = False
        self._finished = False

    def start(self) -> None:
        """Start the scan once."""
        if not self._started:
            self._started = True
            self._thread.start()

    def cancel(self) -> None:
        """Stop at the next file or directory boundary."""
        self._thread.requestInterruption()

    @property
    def running(self) -> bool:
        """Return whether the scan is still pending completion."""
        return self._started and not self._finished

    @Slot()
    def _complete(self) -> None:
        """Deliver the result only after the worker has released SQLite."""
        self._finished = True
        if self._thread.result is None:
            self.output.emit(
                f"Local refresh failed: {self._thread.error or 'No result'}"
            )
            self.finished.emit(1)
        else:
            self.refreshed.emit(self._thread.result)
            self.finished.emit(0)
