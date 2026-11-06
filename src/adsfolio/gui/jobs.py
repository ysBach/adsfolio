"""Qt subprocess helpers for running the existing adsfolio CLI."""

from __future__ import annotations

import json
import logging
import shutil
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

try:  # Keep the base package importable without the optional GUI extra.
    from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal
except ImportError:  # pragma: no cover - only used without PySide6 installed
    QProcess = None  # type: ignore[assignment,misc]
    QProcessEnvironment = None  # type: ignore[assignment,misc]
    QTimer = None  # type: ignore[assignment,misc]

    class QObject:  # type: ignore[no-redef]
        """Fallback base used only to allow headless module imports."""

        def __init__(self, parent: Any = None) -> None:
            del parent

    class _MissingSignal:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def connect(self, *_args: Any, **_kwargs: Any) -> None:
            """Accept headless signal connections without performing work."""

        def emit(self, *_args: Any, **_kwargs: Any) -> None:
            """Accept headless signal emission without performing work."""

    def Signal(*_args: Any, **_kwargs: Any) -> Any:  # type: ignore[misc]
        """Return a placeholder signal for a missing optional dependency."""
        return _MissingSignal()


class CommandJob(QObject):
    """Run an adsfolio CLI subcommand and stream complete output lines.

    Parameters
    ----------
    argv : list[str]
        Full argv, including the executable at index zero.
    parent : `QObject`, optional
        Qt parent object.
    working_directory : `Path` or `str`, optional
        Directory in which to run the command.  The GUI passes the selected
        library root so relative CLI paths have the same semantics as the CLI.
    follow_up_argv : sequence of sequences of `str`, optional
        Commands to run in order after successful completion. A failed or
        cancelled command stops the sequence.
    root : `Path` or `str`, optional
        Backward-compatible alias for `working_directory` used by the dialogs.
    """

    output = Signal(str)
    finished = Signal(int)
    failed = Signal(str)

    def __init__(
        self,
        argv: list[str],
        parent: QObject | None = None,
        *,
        working_directory: Path | str | None = None,
        root: Path | str | None = None,
        follow_up_argv: Sequence[Sequence[str]] = (),
    ) -> None:
        super().__init__(parent)
        if not argv:
            raise ValueError("CommandJob requires a non-empty argv")
        if working_directory is not None and root is not None:
            raise ValueError("Specify only one of working_directory or root")
        self.argv = [str(argument) for argument in argv]
        if any(not command for command in follow_up_argv):
            raise ValueError("Follow-up commands must not be empty")
        self._follow_up_argv = [list(map(str, command)) for command in follow_up_argv]
        self._pending_commands: list[list[str]] = []
        self._cancel_requested = False
        self.working_directory = (
            str(working_directory if working_directory is not None else root)
            if working_directory is not None or root is not None
            else None
        )
        self._buffer = ""
        self._start_failed = False
        self._kill_timer: Any = None
        if QProcess is None:  # pragma: no cover - requires no PySide6 install
            self._process = None
            return
        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.MergedChannels)
        self._process.readyReadStandardOutput.connect(self._read_output)
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_error)

    def start(self) -> None:
        """Start the subprocess and return immediately."""
        if self._process is None:
            raise RuntimeError(
                "PySide6 is required for CommandJob; "
                "install with `pip install 'adsfolio[gui]'`."
            )
        if self.running:
            return
        self._buffer = ""
        self._start_failed = False
        self._cancel_requested = False
        self._pending_commands = list(self._follow_up_argv)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONUNBUFFERED", "1")
        environment.insert("PYTHONIOENCODING", "utf-8")
        self._process.setProcessEnvironment(environment)
        if self.working_directory is not None:
            self._process.setWorkingDirectory(self.working_directory)
        # No stdin is connected.  The CLI detects the closed pipe and skips
        # interactive ADS permission prompts.
        self._process.start(self.argv[0], self.argv[1:])
        self._process.closeWriteChannel()

    def cancel(self) -> None:
        """Request termination, escalating to a kill after three seconds."""
        if self._process is None or not self.running:
            return
        self._cancel_requested = True
        self._pending_commands.clear()
        self._process.terminate()
        self._kill_timer = QTimer.singleShot(3000, self._kill_if_running)

    @property
    def running(self) -> bool:
        """Whether the underlying process is still running."""
        return (
            self._process is not None and self._process.state() != QProcess.NotRunning
        )

    def _read_output(self) -> None:
        if self._process is None:
            return
        payload = bytes(self._process.readAllStandardOutput())
        if payload:
            self._buffer += payload.decode(errors="replace")
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self.output.emit(line.removesuffix("\r"))

    def _on_finished(self, exit_code: int, exit_status: Any) -> None:
        self._read_output()
        if self._buffer:
            self.output.emit(self._buffer.removesuffix("\r"))
            self._buffer = ""
        if exit_status == QProcess.CrashExit and exit_code == 0:
            exit_code = -1
        if exit_code == 0 and not self._cancel_requested and self._pending_commands:
            command = self._pending_commands.pop(0)
            self._process.start(command[0], command[1:])
            self._process.closeWriteChannel()
            return
        self._pending_commands.clear()
        self.finished.emit(-1 if self._cancel_requested else int(exit_code))

    def _on_error(self, error: Any) -> None:
        if self._process is None:
            return
        if error == QProcess.FailedToStart:
            self._start_failed = True
            self.failed.emit(self._process.errorString())

    def _kill_if_running(self) -> None:
        if self._process is not None and self.running:
            self._process.kill()


logger = logging.getLogger(__name__)


class JsonWorkerJob(QObject):
    """Run a hidden ``adsfolio pdf`` worker command in a child process.

    The job writes ``request.json`` to a private temporary directory, starts
    ``adsfolio pdf COMMAND REQUEST``, and reads ``result.json`` after the child
    exits. A child process keeps Python work off the window's interpreter lock,
    so the window stays responsive. `finished` carries ``(payload, error)``.

    Subclasses set `command` and fill `request` after this initializer.
    """

    finished = Signal(object, object)
    command = ""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._directory = Path(tempfile.mkdtemp(prefix=f"adsfolio-{self.command}-"))
        self.request_path = self._directory / "request.json"
        self.cancel_path = self._directory / "cancel"
        self.result_path = self._directory / "result.json"
        self.request: dict[str, Any] = {}
        self._process: Any = None
        self._last_output = ""
        self._started = False
        self._finished = False

    def start(self) -> None:
        """Write the request and start the child process once."""
        if self._started:
            return
        self._started = True
        self.request_path.write_text(json.dumps(self.request), encoding="utf-8")
        self._launch(cli_argv("pdf", self.command, str(self.request_path)))

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
        """Ask the child to stop at its next check by creating the cancel file."""
        try:
            self.cancel_path.touch()
        except OSError:
            logger.exception("Could not request worker cancellation")

    @property
    def running(self) -> bool:
        """Return whether the worker still needs to finish."""
        return self._started and not self._finished

    def _read_output(self) -> None:
        if self._process is None:
            return
        text = bytes(self._process.readAllStandardOutput()).decode(errors="replace")
        for line in text.splitlines():
            if line.strip():
                self._last_output = line
                logger.info("%s: %s", self.command, line)

    def _process_error(self, error: Any) -> None:
        if error == QProcess.FailedToStart and self._process is not None:
            self._last_output = self._process.errorString()
            self._complete(-1)

    def _complete(self, exit_code: int) -> None:
        """Read the result file, remove temporary files, and report once."""
        if self._finished:
            return
        self._read_output()
        self._finished = True
        payload: dict[str, Any] | None = None
        error: str | None = None
        try:
            loaded = json.loads(self.result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            detail = f": {self._last_output}" if self._last_output else ""
            error = f"The worker process stopped with exit code {exit_code}{detail}"
        else:
            payload = loaded if isinstance(loaded, dict) else None
        shutil.rmtree(self._directory, ignore_errors=True)
        self._deliver(payload, error)

    def _deliver(self, payload: dict[str, Any] | None, error: str | None) -> None:
        """Emit the result. Subclasses convert the payload first."""
        self.finished.emit(payload, error)


def cli_argv(*args: str) -> list[str]:
    """Build CLI arguments for an installed package or a desktop bundle.

    Parameters
    ----------
    *args : `str`
        CLI arguments, passed unchanged without shell interpretation.

    Returns
    -------
    list[str]
        Command using the bundled console helper when frozen, otherwise the
        current Python interpreter. The helper shares the GUI's dependencies
        and provides output pipes even when the GUI has no console on Windows.
    """
    if getattr(sys, "frozen", False):
        helper = "adsfolio-cli.exe" if sys.platform == "win32" else "adsfolio-cli"
        return [str(Path(sys.executable).with_name(helper)), *args]
    return [sys.executable, "-m", "adsfolio.cli", *args]


__all__ = ["CommandJob", "JsonWorkerJob", "cli_argv"]
