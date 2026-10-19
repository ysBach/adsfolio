"""Test-only frozen Qt launch and helper check; never shipped in app archives."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import fitz
from iso4 import abbreviate
from pypdf import PdfReader, PdfWriter
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from adsfolio.config import Config
from adsfolio.gui import app as desktop
from adsfolio.gui.jobs import CommandJob, cli_argv
from adsfolio.gui.token_dialog import TokenDialog

logger = logging.getLogger(__name__)


def main() -> int:
    """Exercise the bundled runtime with disposable state and no ADS requests."""
    logging.basicConfig(level=logging.INFO)
    with TemporaryDirectory(prefix="adsfolio-smoke-") as directory:
        root = Path(directory)
        Config.get_config_dir = classmethod(lambda cls: root / "config")
        Config.get_token_paths = classmethod(lambda cls: [root / "config" / "token"])
        os.environ.pop(Config.ENV_VAR, None)
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        sample = root / "Paper with spaces 한글.pdf"
        writer.write(sample)
        assert len(PdfReader(sample).pages) == 1
        with fitz.open(sample) as document:
            assert document.page_count == 1
        assert abbreviate("Journal of Testing") == "J. Test."

        checked = False
        lines: list[str] = []

        class StartupDialog(TokenDialog):
            def exec(self) -> int:
                QTimer.singleShot(0, self.reject)
                return super().exec()

        class Window(desktop.MainWindow):
            def __init__(self) -> None:
                super().__init__()
                self.check_job = CommandJob(cli_argv("--version"), parent=self)
                self.check_job.output.connect(lines.append)
                self.check_job.finished.connect(self.checked_helper)
                self.check_job.failed.connect(self.failed_helper)
                QTimer.singleShot(0, self.check_job.start)
                QTimer.singleShot(20000, self.timed_out)

            def checked_helper(self, code: int) -> None:
                nonlocal checked
                checked = (
                    self.isVisible()
                    and code == 0
                    and any(
                        ("ADSfolio, version" in line or "adsfolio, version" in line)
                        for line in lines
                    )
                )
                self.close()
                QApplication.instance().exit(0 if checked else 1)

            def failed_helper(self, message: str) -> None:
                logger.error("Bundled helper failed: %s", message)
                QApplication.instance().exit(1)

            def timed_out(self) -> None:
                logger.error("Qt/helper smoke check timed out")
                self.check_job.cancel()
                QApplication.instance().exit(1)

        desktop.TokenDialog = StartupDialog
        desktop.MainWindow = Window
        code = desktop.run()
        if code or not checked:
            logger.error("Frozen desktop check failed: %s", lines)
            return 1
        logger.info(
            "Frozen Qt window, first launch, PDF engines, ISO4 and CLI helper passed"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
