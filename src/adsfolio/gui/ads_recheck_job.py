"""Run one library's automatic identifier recheck outside the GUI process.

The recheck parses PDFs with Python code. In a thread it competed with the
window for the Python interpreter lock, and scrolling lagged. A child process
removes that contention.
"""

from __future__ import annotations

import logging
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal, Slot

from ..config import AuthenticationError, Config
from ..pdf.identify_settings import IdentifySettings
from ..pdf.recheck_worker import build_request
from ..pdf.rename_settings import RenameSettings
from .jobs import JsonWorkerJob

logger = logging.getLogger(__name__)


class _AdsTokenThread(QThread):
    """Check for a saved token without blocking the GUI."""

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self.cancel_requested = Event()
        self.available = False
        self.error: str | None = None

    def run(self) -> None:
        if self.cancel_requested.is_set():
            return
        try:
            Config.get_token(prompt=False)
            self.available = not self.cancel_requested.is_set()
        except AuthenticationError:
            pass
        except Exception as exc:
            logger.exception("Could not check the ADS token")
            self.error = str(exc) or type(exc).__name__


class AdsTokenCheck(QObject):
    """Report token availability after the worker stops."""

    finished = Signal(bool, object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread = _AdsTokenThread(self)
        self._thread.finished.connect(self._complete)
        self._started = False
        self._finished = False

    def start(self) -> None:
        """Start the token check once."""
        if not self._started:
            self._started = True
            self._thread.start()

    def cancel(self) -> None:
        """Ignore the result if the window is closing."""
        self._thread.cancel_requested.set()

    @property
    def running(self) -> bool:
        """Return whether the worker still needs to finish."""
        return self._started and not self._finished

    @Slot()
    def _complete(self) -> None:
        self._finished = True
        self.finished.emit(self._thread.available, self._thread.error)


class AdsRecheckJob(JsonWorkerJob):
    """Run one library recheck in a child process and deliver its result.

    `finished` carries ``(result, error)``. ``result`` is `None` when the check
    did not run. Otherwise it has the `RecheckResult` attributes.
    """

    command = "recheck-worker"

    def __init__(
        self,
        root: Path,
        identify_settings: IdentifySettings,
        rename_settings: RenameSettings,
        recursive: bool,
        parent: QObject | None = None,
        *,
        ads_enabled: bool = True,
        retry_remembered: bool = False,
    ) -> None:
        super().__init__(parent)
        self.root = root
        self.request = build_request(
            root,
            identify_settings,
            rename_settings,
            recursive,
            ads_enabled=ads_enabled,
            retry_remembered=retry_remembered,
            config_dir=Config.get_config_dir(),
            cancel_path=self.cancel_path,
            result_path=self.result_path,
        )

    def _deliver(self, payload: dict[str, Any] | None, error: str | None) -> None:
        result: Any = None
        if payload is not None:
            if isinstance(payload.get("result"), dict):
                result = SimpleNamespace(**payload["result"])
            error = payload.get("error")
        self.finished.emit(result, error)
