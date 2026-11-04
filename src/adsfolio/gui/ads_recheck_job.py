"""Run one library's automatic identifier recheck outside the GUI process.

The recheck parses PDFs with Python code. In a thread it competed with the
window for the Python interpreter lock, and scrolling lagged. A child process
removes that contention.
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QThread, Signal, Slot

from ..config import AuthenticationError, Config
from ..pdf.identify_settings import IdentifySettings
from ..pdf.recheck_worker import build_request
from ..pdf.rename_settings import RenameSettings
from .jobs import cli_argv

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


class AdsRecheckJob(QObject):
    """Run one library recheck in a child process and deliver its result.

    `finished` carries ``(result, error)``. ``result`` is `None` when the check
    did not run. Otherwise it has the `RecheckResult` attributes.
    """

    finished = Signal(object, object)

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
        self._directory = Path(tempfile.mkdtemp(prefix="adsfolio-recheck-"))
        self.request_path = self._directory / "request.json"
        self.cancel_path = self._directory / "cancel"
        self.result_path = self._directory / "result.json"
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
        self._process: QProcess | None = None
        self._last_output = ""
        self._started = False
        self._finished = False

    def start(self) -> None:
        """Write the request and start the child process once."""
        if self._started:
            return
        self._started = True
        self.request_path.write_text(json.dumps(self.request), encoding="utf-8")
        self._launch(cli_argv("pdf", "recheck-worker", str(self.request_path)))

    def _launch(self, argv: list[str]) -> None:
        """Start the child process. Tests replace this with an in-process runner."""
        process = QProcess(self)
        process.setProcessChannelMode(QProcess.MergedChannels)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONUNBUFFERED", "1")
        environment.insert("PYTHONIOENCODING", "utf-8")
        process.setProcessEnvironment(environment)
        process.readyReadStandardOutput.connect(self._read_output)
        process.finished.connect(lambda code, _status: self._complete(code))
        process.errorOccurred.connect(self._process_error)
        self._process = process
        process.start(argv[0], argv[1:])
        process.closeWriteChannel()

    def cancel(self) -> None:
        """Ask the child to stop at its next check. It saves its progress first."""
        try:
            self.cancel_path.touch()
        except OSError:
            logger.exception("Could not request recheck cancellation")

    @property
    def running(self) -> bool:
        """Return whether the worker still needs to finish."""
        return self._started and not self._finished

    @Slot()
    def _read_output(self) -> None:
        if self._process is None:
            return
        text = bytes(self._process.readAllStandardOutput()).decode(errors="replace")
        for line in text.splitlines():
            if line.strip():
                self._last_output = line
                logger.info("Recheck worker: %s", line)

    def _process_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.FailedToStart and self._process is not None:
            self._last_output = self._process.errorString()
            self._complete(-1)

    def _complete(self, exit_code: int) -> None:
        """Read the result file, remove temporary files, and report once."""
        if self._finished:
            return
        self._read_output()
        self._finished = True
        result: Any = None
        error: str | None = None
        try:
            payload = json.loads(self.result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            detail = f": {self._last_output}" if self._last_output else ""
            error = f"The recheck process stopped with exit code {exit_code}{detail}"
        else:
            if isinstance(payload.get("result"), dict):
                result = SimpleNamespace(**payload["result"])
            error = payload.get("error")
        shutil.rmtree(self._directory, ignore_errors=True)
        self.finished.emit(result, error)
