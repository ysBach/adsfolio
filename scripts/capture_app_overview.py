"""Render the README application screenshots from illustrative sample data.

The script runs ADSfolio offscreen with a temporary home directory, so it never
reads or changes the user's settings, token, or library catalog. All papers,
authors, journals, and bibcodes below are fictional.

Usage::

    uv run python scripts/capture_app_overview.py [--output PATH] [--welcome PATH]

The first image shows a sample library. The second shows the welcome page
that a new installation opens with.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
SIZE = (1440, 700)
VISIBLE_COLUMNS = ("title", "author", "year", "pub", "status")
VISIBLE_DETAIL_ROWS = (
    "title",
    "author",
    "year",
    "pub",
    "abstract",
    "status",
    "links",
    "filename",
)

# Fictional records. "DEMO" is not an ADS journal code, so no bibcode is real.
PAPERS = (
    {
        "filename": "Comet dust.pdf",
        "status": "resolved",
        "metadata": {
            "title": ["Dust in comet atmospheres"],
            "author": ["Rivera, A.", "Chen, M."],
            "year": "2024",
            "pub": "Astronomy research",
            "bibcode": "2024DEMO....1....1R",
            "abstract": "Illustrative sample data. Select a paper to inspect "
            "metadata. Identify checks publication records. Rename shows a "
            "preview before files change.",
        },
    },
    {
        "filename": "Small bodies.pdf",
        "status": "resolved",
        "metadata": {
            "title": ["Optical observations of small bodies"],
            "author": ["Morgan, L.", "Patel, R."],
            "year": "2023",
            "pub": "Planetary science",
            "bibcode": "2023DEMO....2....2M",
        },
    },
    {
        "filename": "Photometry notes.pdf",
        "status": "unresolved",
        "metadata": {
            "title": ["Notes on aperture photometry"],
            "author": ["Taylor, J."],
            "year": "2025",
            "pub": "Local reference",
        },
    },
    {
        "filename": "Light scattering.pdf",
        "status": "unresolved",
        "metadata": {
            "title": ["Light scattering from planetary surfaces"],
            "author": ["Kim, S."],
            "year": "2025",
            "pub": "Optics",
        },
    },
)


def _write_pdf(path: Path, title: str) -> None:
    """Write a one-page PDF so the catalog sees a real, distinct file."""
    import fitz

    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), title)
    document.save(path)
    document.close()


def _save(window: object, output: Path) -> None:
    """Save the window at the README size, or raise on any mismatch."""
    pixmap = window.grab()
    if (pixmap.width(), pixmap.height()) != SIZE:
        raise RuntimeError(f"Unexpected screenshot size {pixmap.size()}")
    if not pixmap.save(str(output)):
        raise RuntimeError(f"Could not save {output}")
    logger.info("Saved %s", output)


def capture(output: Path, welcome: Path) -> None:
    """Save the welcome page, then the sample library, from a temporary home."""
    from PySide6.QtWidgets import QApplication

    application = QApplication.instance() or QApplication([])
    from adsfolio.config import Config
    from adsfolio.gui.config import (
        DEFAULT_HIDDEN_DETAIL_ROWS,
        GuiConfig,
        RootEntry,
        save_config,
    )
    from adsfolio.gui.detail_pane import DEFAULT_ROW_ORDER
    from adsfolio.gui.library import BASE_COLUMNS
    from adsfolio.gui.main_window import MainWindow

    home = Path(os.environ["HOME"])
    if not Config.get_config_dir().is_relative_to(home):
        raise RuntimeError("The configuration directory is not isolated")
    save_config(GuiConfig(check_updates_automatically=False))
    first_run = MainWindow()
    first_run.resize(*SIZE)
    first_run.show()
    application.processEvents()
    _save(first_run, welcome)
    first_run.close()

    library = home / "Demo library"
    library.mkdir()
    for paper in PAPERS:
        _write_pdf(library / paper["filename"], paper["metadata"]["title"][0])
    save_config(
        GuiConfig(
            roots=[RootEntry(str(library), label="Demo library")],
            check_updates_automatically=False,
            hidden_table_columns=[
                column.key
                for column in BASE_COLUMNS
                if column.key not in VISIBLE_COLUMNS
            ],
            detail_hidden_rows=[
                *DEFAULT_HIDDEN_DETAIL_ROWS,
                *(row for row in DEFAULT_ROW_ORDER if row not in VISIBLE_DETAIL_ROWS),
            ],
            citation_styles=[],
            column_widths={"pub": 170},
            splitter_sizes=[210, 840, 390],
        )
    )

    window = MainWindow()
    registry = window.registries[str(library.resolve())]
    for paper in PAPERS:
        registry.replace_file_metadata(
            library.resolve(), paper["filename"], paper["metadata"]
        )
        registry.update_file_status(
            library.resolve(), paper["filename"], paper["status"]
        )
    window.resize(*SIZE)
    window.show()
    window.refresh(library.resolve())
    application.processEvents()
    window.table.setCurrentIndex(window.model.index(0, 0))
    application.processEvents()
    _save(window, output)
    window.close()


def main() -> None:
    """Re-run in an isolated home directory, then capture the window."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--output", type=Path, default=ROOT / "docs/_static/app-overview.png"
    )
    parser.add_argument(
        "--welcome", type=Path, default=ROOT / "docs/_static/welcome.png"
    )
    args = parser.parse_args()
    if os.environ.get("ADSFOLIO_CAPTURE_HOME") is None:
        with tempfile.TemporaryDirectory(prefix="adsfolio-capture-") as home:
            environment = {
                key: value
                for key, value in os.environ.items()
                if key not in {"ADS_API_TOKEN", "ADSFOLIO_SOURCE_LAUNCH"}
            }
            environment |= {
                "HOME": home,
                "ADSFOLIO_CAPTURE_HOME": home,
                "QT_QPA_PLATFORM": "offscreen",
            }
            command = [
                sys.executable,
                __file__,
                "--output",
                str(args.output.resolve()),
                "--welcome",
                str(args.welcome.resolve()),
            ]
            raise SystemExit(
                subprocess.run(
                    command, env=environment, cwd=home, check=False
                ).returncode
            )
    capture(args.output, args.welcome)


if __name__ == "__main__":
    main()
