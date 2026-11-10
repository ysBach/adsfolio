"""Discover saved PDF roots outside the GUI thread."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ..pdf.registry import PdfRegistry


class StartupDiscoveryThread(QThread):
    """Initialize one saved root with a worker-owned catalog connection."""

    succeeded = Signal(str, bool, str)
    failed = Signal(str)

    def __init__(self, root: Path, recursive: bool, parent=None) -> None:
        super().__init__(parent)
        self.root = root
        self.recursive = recursive

    def run(self) -> None:
        """Discover the root, then close the worker-owned registry."""
        registry: PdfRegistry | None = None
        root_text: str | None = None
        catalog_path: str | None = None
        available = False
        error: str | None = None
        try:
            if self.isInterruptionRequested():
                return
            root = self.root.expanduser().resolve()
            root_text = str(root)
            registry = PdfRegistry(root)
            catalog_path = str(registry.path)
            available = root.is_dir()
            if self.isInterruptionRequested():
                return
            if available:
                registry.catalog.discover_root(
                    root,
                    recursive=self.recursive,
                    cancelled=self.isInterruptionRequested,
                )
        except (OSError, ValueError, sqlite3.Error, InterruptedError) as exc:
            if not self.isInterruptionRequested():
                error = str(exc)
        except Exception as exc:  # noqa: BLE001 - report every root-local worker failure
            if not self.isInterruptionRequested():
                error = str(exc)
        finally:
            if registry is not None:
                registry.close()
        if self.isInterruptionRequested():
            return
        if error is not None:
            self.failed.emit(error)
        elif root_text is not None and catalog_path is not None:
            self.succeeded.emit(root_text, available, catalog_path)
