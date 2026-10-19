"""Application entry point for the ADSfolio desktop GUI."""

from __future__ import annotations

import importlib.resources as ir
import logging
import os
import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .. import __version__
from .main_window import MainWindow


def load_app_icon() -> QIcon:
    """Load the application window icon from packaged assets."""
    traversable = ir.files("adsfolio.gui").joinpath("assets", "adsfolio.png")
    with ir.as_file(traversable) as icon_path:
        if icon_path.is_file():
            return QIcon(str(icon_path))
    return QIcon()


def run() -> int:
    """Launch the ADSfolio desktop application and return its exit code."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    app = QApplication(sys.argv)
    app.setApplicationVersion(__version__)
    app.setApplicationName("adsfolio")
    app.setApplicationDisplayName("ADSfolio")
    app.setOrganizationName("adsfolio")

    icon = load_app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    window = MainWindow()
    if os.environ.get("ADSFOLIO_SOURCE_LAUNCH") == "1":
        window.setWindowTitle("ADSfolio (Development)")
    window.show()
    QTimer.singleShot(0, window.check_updates_on_start)
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
