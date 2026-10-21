"""Render documentation screenshots and the first-run video from sample data.

The script runs ADSfolio offscreen with a temporary home directory, so it never
reads or changes the user's settings, token, or library catalog. All papers,
authors, journals, and bibcodes below are fictional. It drives the real
windows: the welcome page, the folder flow, and the dialogs.

Usage::

    uv run python scripts/capture_docs_media.py [--static DIR] [--no-video]

Outputs, relative to ``docs/_static``:

- ``app-overview.png`` and ``welcome.png`` (README and website)
- ``tutorial/*.png`` (dialog screenshots for the guides)
- ``tutorial/first-run.mp4`` (captioned walkthrough; needs ``ffmpeg``)
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
SIZE = (1440, 700)
VIDEO_FPS = 10
HOLD_SECONDS = 3.0
FADE_SECONDS = 0.5
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
# Two papers sit in a subfolder so the folder flow asks about subfolders.
PAPERS = (
    {
        "path": "Comet dust.pdf",
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
        "path": "2023/Small bodies.pdf",
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
        "path": "Photometry notes.pdf",
        "status": "unresolved",
        "metadata": {
            "title": ["Notes on aperture photometry"],
            "author": ["Taylor, J."],
            "year": "2025",
            "pub": "Local reference",
        },
    },
    {
        "path": "2023/Light scattering.pdf",
        "status": "unresolved",
        "metadata": {
            "title": ["Light scattering from planetary surfaces"],
            "author": ["Kim, S."],
            "year": "2025",
            "pub": "Optics",
        },
    },
)
SAMPLE_RELEASE_NOTES = """## 9.9.0 (sample)

### New

- **Example feature.** Before: this sample entry did not exist. Now: it shows
  how release notes appear in **Software Update**.
"""


def _write_pdf(path: Path, title: str) -> None:
    """Write a one-page PDF so the catalog sees a real, distinct file."""
    import fitz

    path.parent.mkdir(parents=True, exist_ok=True)
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), title)
    document.save(path)
    document.close()


class Recorder:
    """Collect captioned frames and write PNG screenshots."""

    def __init__(self, static: Path) -> None:
        self.static = static
        self.frames: list[tuple[Any, str]] = []

    def save(self, image: Any, name: str) -> None:
        """Save `image` under the static directory, or raise on failure."""
        target = self.static / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not image.save(str(target)):
            raise RuntimeError(f"Could not save {target}")
        logger.info("Saved %s", target)

    def frame(self, image: Any, caption: str) -> None:
        """Add one video frame with a caption band."""
        self.frames.append((image, caption))


def _check_size(pixmap: Any) -> Any:
    if (pixmap.width(), pixmap.height()) != SIZE:
        raise RuntimeError(f"Unexpected window size {pixmap.size()}")
    return pixmap


def _composite(window: Any, dialog: Any | None = None, highlight: Any = None) -> Any:
    """Draw `dialog` centered over a dimmed `window`, or ring `highlight`."""
    from PySide6.QtCore import QPoint, QRect, Qt
    from PySide6.QtGui import QColor, QPainter, QPen

    image = _check_size(window.grab()).toImage()
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    if dialog is not None:
        painter.fillRect(image.rect(), QColor(0, 0, 0, 70))
        pixmap = dialog.grab()
        x = (image.width() - pixmap.width()) // 2
        y = max(60, (image.height() - pixmap.height()) // 3)
        painter.setPen(QPen(QColor(0, 0, 0, 90), 1))
        painter.drawRect(QRect(x - 1, y - 1, pixmap.width() + 1, pixmap.height() + 1))
        painter.drawPixmap(QPoint(x, y), pixmap)
    if highlight is not None:
        top_left = highlight.mapTo(window, QPoint(0, 0))
        ring = QRect(top_left, highlight.size()).adjusted(-6, -6, 6, 6)
        painter.setPen(QPen(QColor("#0a84ff"), 4))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(ring, 10, 10)
    painter.end()
    return image


def _with_caption(image: Any, caption: str) -> Any:
    """Return a copy of `image` with a caption band at the bottom."""
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtGui import QColor, QFont, QPainter

    framed = image.copy()
    painter = QPainter(framed)
    band = QRect(0, framed.height() - 64, framed.width(), 64)
    painter.fillRect(band, QColor(20, 20, 24, 215))
    font = QFont()
    font.setPixelSize(24)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor("white"))
    painter.drawText(band, Qt.AlignCenter, caption)
    painter.end()
    return framed


def _modal(application: Any, trigger: Callable[[], Any], handle: Callable) -> Any:
    """Run `trigger`, pass each modal dialog it opens to `handle`, return result.

    `handle` receives the dialog and must close it, for example with a button
    click or ``reject()``.
    """
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    seen: list[Any] = []

    def poll() -> None:
        dialog = QApplication.activeModalWidget()
        if dialog is not None and dialog not in seen:
            seen.append(dialog)
            application.processEvents()
            handle(dialog)

    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(50)
    try:
        return trigger()
    finally:
        timer.stop()


def _config(**overrides: Any) -> Any:
    from adsfolio.gui.config import DEFAULT_HIDDEN_DETAIL_ROWS, GuiConfig
    from adsfolio.gui.detail_pane import DEFAULT_ROW_ORDER
    from adsfolio.gui.library import BASE_COLUMNS

    return GuiConfig(
        check_updates_automatically=False,
        hidden_table_columns=[
            column.key for column in BASE_COLUMNS if column.key not in VISIBLE_COLUMNS
        ],
        detail_hidden_rows=[
            *DEFAULT_HIDDEN_DETAIL_ROWS,
            *(row for row in DEFAULT_ROW_ORDER if row not in VISIBLE_DETAIL_ROWS),
        ],
        citation_styles=[],
        column_widths={"pub": 170},
        splitter_sizes=[210, 840, 390],
        **overrides,
    )


def capture(recorder: Recorder) -> None:
    """Drive the first-run flow and the main dialogs, saving each view."""
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

    application = QApplication.instance() or QApplication([])
    from adsfolio.config import Config
    from adsfolio.gui import main_window as main_window_module
    from adsfolio.gui.config import save_config
    from adsfolio.gui.main_window import MainWindow
    from adsfolio.gui.token_dialog import TokenDialog
    from adsfolio.gui.updates import InstallationKind, parse_release

    home = Path(os.environ["HOME"])
    if not Config.get_config_dir().is_relative_to(home):
        raise RuntimeError("The configuration directory is not isolated")
    library = home / "Demo library"
    for paper in PAPERS:
        _write_pdf(library / paper["path"], paper["metadata"]["title"][0])
    save_config(_config())

    window = MainWindow()
    window.resize(*SIZE)
    window.show()
    application.processEvents()
    welcome = _composite(window)
    recorder.save(welcome, "welcome.png")
    recorder.frame(welcome, "A new installation opens with three setup steps")
    recorder.frame(
        _composite(window, highlight=window.welcome.add_folder_button),
        "1. Choose Add PDF Folder… and select your folder of papers",
    )

    def answer(dialog: Any) -> None:
        buttons = dialog.buttons() if isinstance(dialog, QMessageBox) else []
        include = next(
            (button for button in buttons if button.text() == "Include Subfolders"),
            None,
        )
        if include is not None:
            recorder.save(dialog.grab(), "tutorial/subfolder-question.png")
            recorder.frame(
                _composite(window, dialog),
                "2. Choose whether to include PDFs in subfolders",
            )
            include.click()
        else:
            recorder.save(dialog.grab(), "tutorial/identify-prompt.png")
            recorder.frame(
                _composite(window, dialog),
                "3. Choose OK to find publication details",
            )
            dialog.reject()

    original_picker = QFileDialog.getExistingDirectory
    QFileDialog.getExistingDirectory = staticmethod(lambda *args: str(library))
    try:
        _modal(application, window.welcome.add_folder_button.click, answer)
    finally:
        QFileDialog.getExistingDirectory = original_picker

    # Identification was cancelled in the walkthrough. Store the sample
    # results that a finished lookup would show.
    root = library.resolve()
    registry = window.registries[str(root)]
    for paper in PAPERS:
        registry.replace_file_metadata(root, paper["path"], paper["metadata"])
        registry.update_file_status(root, paper["path"], paper["status"])
    window.refresh(root)
    application.processEvents()
    title = PAPERS[0]["metadata"]["title"][0]
    row = next(
        row
        for row in range(window.model.rowCount())
        if window.model.index(row, 0).data() == title
    )
    window.table.setCurrentIndex(window.model.index(row, 0))
    application.processEvents()
    overview = _composite(window)
    recorder.save(overview, "app-overview.png")
    recorder.frame(overview, "4. Browse papers and edit their details")

    def keep(name: str, caption: str) -> Callable[[Any], None]:
        def handle(dialog: Any) -> None:
            recorder.save(dialog.grab(), f"tutorial/{name}.png")
            recorder.frame(_composite(window, dialog), caption)
            dialog.reject()

        return handle

    _modal(
        application,
        lambda: TokenDialog(window).exec(),
        keep("token-dialog", "Optional: add a NASA ADS token and test it"),
    )
    _modal(
        application,
        window._edit_user_profile,
        keep("profile-dialog", "Optional: add your ORCID iD for your own papers"),
    )

    # Show the desktop-download variant that most readers see; this script
    # itself runs from a Python installation.
    main_window_module.installation_kind = lambda: InstallationKind.DESKTOP
    checker = window.update_checker
    checker.latest = parse_release(
        {
            "tag_name": "v9.9.0",
            "draft": False,
            "prerelease": False,
            "assets": [{"name": "adsfolio-macos-arm64.dmg"}],
        }
    )
    checker.changelog = SAMPLE_RELEASE_NOTES
    checker.fetch_changelog = lambda: None
    checker.changed.emit()
    window._show_update_dialog()
    dialog = window._update_dialog
    dialog.resize(560, 470)
    application.processEvents()
    recorder.save(dialog.grab(), "tutorial/software-update.png")
    recorder.frame(
        _composite(window, dialog),
        "A blue dot on App Update means a new version is ready",
    )
    dialog.close()
    window.close()


def write_video(recorder: Recorder, output: Path) -> None:
    """Encode the captioned frames with cross-fades into an H.264 MP4."""
    from PySide6.QtGui import QPainter

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg is required for the video; use --no-video")
    images = [_with_caption(image, caption) for image, caption in recorder.frames]
    hold = round(HOLD_SECONDS * VIDEO_FPS)
    fade = round(FADE_SECONDS * VIDEO_FPS)
    with tempfile.TemporaryDirectory(prefix="adsfolio-frames-") as folder:
        index = 0

        def emit(image: Any) -> None:
            nonlocal index
            if not image.save(str(Path(folder) / f"frame_{index:05d}.png")):
                raise RuntimeError("Could not write a video frame")
            index += 1

        for position, image in enumerate(images):
            for _ in range(hold):
                emit(image)
            if position + 1 < len(images):
                following = images[position + 1]
                for step in range(1, fade + 1):
                    blended = image.copy()
                    painter = QPainter(blended)
                    painter.setOpacity(step / (fade + 1))
                    painter.drawImage(0, 0, following)
                    painter.end()
                    emit(blended)
        output.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                ffmpeg,
                "-loglevel",
                "error",
                "-y",
                "-framerate",
                str(VIDEO_FPS),
                "-i",
                str(Path(folder) / "frame_%05d.png"),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-crf",
                "26",
                "-movflags",
                "+faststart",
                str(output),
            ],
            check=True,
        )
    logger.info("Saved %s (%d frames)", output, index)


def main() -> None:
    """Re-run in an isolated home directory, then capture the media."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--static", type=Path, default=ROOT / "docs/_static")
    parser.add_argument("--no-video", action="store_true")
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
            command = [sys.executable, __file__, "--static", str(args.static.resolve())]
            if args.no_video:
                command.append("--no-video")
            raise SystemExit(
                subprocess.run(
                    command, env=environment, cwd=home, check=False
                ).returncode
            )
    recorder = Recorder(args.static)
    capture(recorder)
    if not args.no_video:
        write_video(recorder, args.static / "tutorial/first-run.mp4")


if __name__ == "__main__":
    main()
