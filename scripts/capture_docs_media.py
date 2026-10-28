"""Render documentation screenshots and the first-run video from sample data.

The script runs ADSfolio offscreen with temporary configuration and sample files.
It does not read or change the user's settings, token, or library catalog. All papers,
authors, journals, and bibcodes below are fictional. It drives the real
windows: the welcome page, the folder flow, and the dialogs.

Usage::

    uv run python scripts/capture_docs_media.py [--static DIR] [--no-video]

Outputs, relative to ``docs/_static``:

- ``app-overview.png`` (README, website) and ``welcome.png`` (website, docs)
- ``tutorial/*.png`` (dialog screenshots for the guides)
- ``tutorial/first-run.mp4`` and ``tutorial/workflows.mp4`` (captioned
  walkthroughs; need ``ffmpeg``)
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
VISIBLE_COLUMNS = (
    "title",
    "author",
    "author_count",
    "year",
    "pub",
    "citation_count",
    "status",
)
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
            "doi": ["10.5555/adsfolio-demo-comet"],
            "author_count": 2,
            "citation_count": 12,
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
            "doi": ["10.5555/adsfolio-demo-small-bodies"],
            "author_count": 2,
            "citation_count": 0,
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
            "author_count": 1,
            "_adsfolio_locked_fields": ["title", "author", "year", "pub"],
        },
    },
    {
        "path": "2023/Light scattering.pdf",
        "status": "unresolved",
        "metadata": {
            "title": ["Unidentified download"],
        },
    },
)
SAMPLE_RELEASE_NOTES = """## 9.9.0 (sample)

### New

- **Example feature.** Before: this sample entry did not exist. Now: it shows
  how release notes appear in **Software Update**.
"""


def _write_pdf(path: Path, title: str, doi: str | None = None) -> None:
    """Write a one-page PDF so the catalog sees a real, distinct file."""
    import fitz

    path.parent.mkdir(parents=True, exist_ok=True)
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), title)
    if doi is not None:
        page.insert_text((72, 100), f"doi:{doi}")
        document.set_metadata({"title": title, "subject": f"doi:{doi}"})
    document.save(path)
    document.close()


class Recorder:
    """Collect captioned frames and write PNG screenshots."""

    def __init__(self, static: Path) -> None:
        self.static = static
        self.videos: dict[str, list[tuple[Any, str]]] = {}

    def save(self, image: Any, name: str) -> None:
        """Save `image` under the static directory, or raise on failure."""
        from PySide6.QtCore import QBuffer, QIODevice

        target = self.static / name
        target.parent.mkdir(parents=True, exist_ok=True)
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        if not image.save(buffer, "PNG"):
            raise RuntimeError(f"Could not save {target}")
        data = bytes(buffer.data())
        buffer.close()
        if target.exists() and target.read_bytes() == data:
            logger.info("Unchanged %s", target)
        else:
            target.write_bytes(data)
            logger.info("Saved %s", target)

    def frame(self, image: Any, caption: str, video: str = "first-run") -> None:
        """Add one captioned frame to the named video."""
        self.videos.setdefault(video, []).append((image, caption))


def _check_size(pixmap: Any) -> Any:
    if (pixmap.width(), pixmap.height()) != SIZE:
        raise RuntimeError(f"Unexpected window size {pixmap.size()}")
    return pixmap


def _composite(
    window: Any,
    dialog: Any | None = None,
    highlight: Any = None,
    at: Any = None,
) -> Any:
    """Draw `dialog` over a dimmed `window`, or ring `highlight`.

    `dialog` is centered unless `at` gives its top-left point in `window`.
    """
    from PySide6.QtCore import QPoint, QRect, Qt
    from PySide6.QtGui import QColor, QPainter, QPen, QPixmap

    image = _check_size(window.grab()).toImage()
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    if dialog is not None:
        painter.fillRect(image.rect(), QColor(0, 0, 0, 70))
        pixmap = dialog if isinstance(dialog, QPixmap) else dialog.grab()
        if at is None:
            x = (image.width() - pixmap.width()) // 2
            y = max(60, (image.height() - pixmap.height()) // 3)
        else:
            x, y = at.x(), at.y()
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
            "citation_count",
            "publisher",
        ],
        citation_styles=[],
        column_widths={
            "title": 290,
            "author": 120,
            "author_count": 65,
            "year": 55,
            "pub": 150,
            "citation_count": 70,
            "status": 28,
        },
        splitter_sizes=[210, 840, 390],
        **overrides,
    )


def capture(recorder: Recorder) -> None:
    """Drive the first-run flow and the main dialogs, saving each view."""
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

    application = QApplication.instance() or QApplication([])
    from adsfolio.config import Config

    capture_home = Path(os.environ["ADSFOLIO_CAPTURE_HOME"])
    Config.get_config_dir = classmethod(lambda cls: capture_home / "config")
    Config.get_token_paths = classmethod(
        lambda cls: [capture_home / "config" / "token"]
    )

    from adsfolio.gui import main_window as main_window_module
    from adsfolio.gui.config import save_config
    from adsfolio.gui.main_window import MainWindow
    from adsfolio.gui.token_dialog import TokenDialog
    from adsfolio.gui.updates import InstallationKind, parse_release
    from adsfolio.pdf.catalog import PdfCatalog
    from adsfolio.pdf.files import sha256_file

    if not Config.get_config_dir().is_relative_to(capture_home):
        raise RuntimeError("The configuration directory is not isolated")
    library = capture_home / "Demo library"
    for paper in PAPERS:
        dois = paper["metadata"].get("doi", [])
        _write_pdf(
            library / paper["path"],
            paper["metadata"]["title"][0],
            dois[0] if dois else None,
        )
    catalog = PdfCatalog()
    try:
        for paper in PAPERS:
            path = library / paper["path"]
            facts = path.stat()
            catalog.observe_file(
                library,
                paper["path"],
                sha256=sha256_file(path),
                size_bytes=facts.st_size,
                mtime_ns=facts.st_mtime_ns,
                device=facts.st_dev,
                inode=facts.st_ino,
                status="unresolved",
            )
    finally:
        catalog.close()
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
    from adsfolio.pdf.identifier import PdfIdentifier

    for paper in PAPERS:
        metadata = paper["metadata"]
        if paper["status"] == "resolved":
            metadata = {
                **PdfIdentifier(None).build_record(metadata, root / paper["path"]),
                **metadata,
            }
        path = root / paper["path"]
        facts = path.stat()
        registry.upsert_file(
            root=root,
            relative_path=paper["path"],
            current_path=paper["path"],
            sha256=sha256_file(path),
            size_bytes=facts.st_size,
            mtime_ns=facts.st_mtime_ns,
            metadata=metadata,
            status=paper["status"],
        )
    window.refresh(root)
    application.processEvents()
    title = PAPERS[0]["metadata"]["title"][0]
    title_column = next(
        index
        for index, column in enumerate(window.model.columns)
        if column.key == "title"
    )
    row = next(
        row
        for row in range(window.model.rowCount())
        if window.model.index(row, title_column).data() == title
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
    _capture_workflows(application, recorder, window, root)
    window.close()


def _capture_workflows(
    application: Any, recorder: Recorder, window: Any, root: Path
) -> None:
    """Save the BibTeX export, folder watching, and CV Generator views."""
    from PySide6.QtCore import QObject, QPoint, Qt, Signal
    from PySide6.QtWidgets import QMenu, QTabWidget

    from adsfolio.config import Config
    from adsfolio.gui import main_window as main_window_module
    from adsfolio.gui.ads_library_export_dialog import ExportAdsLibraryDialog
    from adsfolio.gui.ads_library_jobs import AdsLibraryOperation
    from adsfolio.gui.cv_dialog import CVDialog
    from adsfolio.gui.dialogs import IdentifyDialog
    from adsfolio.library_sync import LibraryDestination
    from adsfolio.pdf.readiness import metadata_ready

    identify = IdentifyDialog(
        root,
        Config.load_identify_settings(),
        save_settings=lambda scope, settings: Config.save_identify_settings(settings),
    )
    identify.allow_ads.setChecked(True)
    identify.save_defaults()
    identify.close()
    identify = IdentifyDialog(root, Config.load_identify_settings())
    if not identify.allow_ads.isChecked():
        raise RuntimeError("Saved ADS fallback did not survive reopening Identify")
    identify.show()
    application.processEvents()
    recorder.save(identify.grab(), "tutorial/identify-settings.png")
    recorder.frame(
        _composite(window, identify, at=QPoint((SIZE[0] - identify.width()) // 2, 8)),
        "Enable ADS fallback and choose Save Defaults to keep it enabled",
        video="workflows",
    )
    identify.close()

    libraries = (
        LibraryDestination(
            id="demo-1",
            name="Comet dust paper (2026)",
            permission="owner",
            public=False,
            num_documents=42,
        ),
        LibraryDestination(
            id="demo-2",
            name="Group reading list",
            permission="read",
            public=False,
            num_documents=118,
        ),
    )

    class ListJob(QObject):
        succeeded = Signal(object)
        failed = Signal(object)
        finished = Signal()

        def __init__(self, request: Any, parent: Any = None) -> None:
            super().__init__(parent)
            self.request = request

        def start(self) -> None:
            if self.request.operation is AdsLibraryOperation.LIST:
                self.succeeded.emit(libraries)
            self.finished.emit()

        def cancel(self) -> None:
            pass

    export = ExportAdsLibraryDialog(window, job_factory=ListJob)
    export.show()
    application.processEvents()
    recorder.save(export.grab(), "tutorial/export-ads-library.png")
    recorder.frame(
        _composite(window, export),
        "Export an ADS library as BibTeX for Overleaf or LaTeX",
        video="workflows",
    )
    export.close()

    # The root context menu, with automatic processing turned on.
    entry = window.root_entries[str(root)]
    entry.auto_process_new_pdfs = True
    captured: list[QMenu] = []

    class CapturedMenu(QMenu):
        def exec(self, *_args: Any) -> None:
            captured.append(self)

    original_menu = main_window_module.QMenu
    main_window_module.QMenu = CapturedMenu
    try:
        item = window.sidebar.topLevelItem(0).child(0)
        rect = window.sidebar.visualItemRect(item)
        window._sidebar_menu(rect.center())
    finally:
        main_window_module.QMenu = original_menu
    menu = captured[-1]
    menu.adjustSize()
    menu.ensurePolished()
    recorder.save(menu.grab(), "tutorial/watch-menu.png")
    origin = window.sidebar.viewport().mapTo(window, rect.center() + QPoint(12, 6))
    recorder.frame(
        _composite(window, menu, at=origin),
        "Watch a folder: new PDFs are identified and renamed",
        video="workflows",
    )

    # Several selected papers: the menu acts on all of them.
    from PySide6.QtCore import QItemSelectionModel

    selection = window.table.selectionModel()
    selection.clearSelection()
    for row in range(window.model.rowCount()):
        if metadata_ready(window.model.record_at(row)):
            selection.select(
                window.model.index(row, 0),
                QItemSelectionModel.SelectionFlag.Select
                | QItemSelectionModel.SelectionFlag.Rows,
            )
    application.processEvents()
    main_window_module.QMenu = CapturedMenu
    try:
        point = window.table.visualRect(window.model.index(1, 0)).center()
        window._table_menu(point)
    finally:
        main_window_module.QMenu = original_menu
    table_menu = captured[-1]
    table_menu.adjustSize()
    recorder.save(table_menu.grab(), "tutorial/selection-menu.png")
    recorder.frame(
        _composite(
            window,
            table_menu,
            at=window.table.viewport().mapTo(window, point + QPoint(12, 6)),
        ),
        "Select several papers: each action covers all of them",
        video="workflows",
    )
    export = CVDialog(
        window,
        settings_path=Path(os.environ["ADSFOLIO_CAPTURE_HOME"]) / "cv-selection.yaml",
        records=window._selected_records(),
    )
    export.me_target.setText("Rivera, A.")
    export.preset.setCurrentIndex(export.preset.findData("markdown"))
    export._apply_preset()
    export.resize(1180, 640)
    export.show()
    application.processEvents()
    export._render_sample()
    application.processEvents()
    recorder.save(export.grab(), "tutorial/export-selected.png")
    recorder.frame(
        _composite(
            window,
            export.grab().scaled(
                1180, 540, Qt.KeepAspectRatio, Qt.SmoothTransformation
            ),
        ),
        "Export the selected papers as a list, without ADS",
        video="workflows",
    )
    export.hide()

    # Citation key styles, previewed on the selected papers.
    from adsfolio.citekeys import KeyPolicy, KeyStyle, scholar_key
    from adsfolio.gui.bibtex_export import CitationKeyDialog, CitationKeySettingsDialog
    from adsfolio.references import CitationKeyError, LocalReferences

    samples = [
        {**record["metadata"], "bibcode": record.get("bibcode")}
        for record in window._selected_records()[:3]
    ]
    keys = CitationKeySettingsDialog(window, KeyPolicy(other=KeyStyle.SCHOLAR), samples)
    keys.ads_style.setCurrentIndex(keys.ads_style.findData(KeyStyle.SCHOLAR))
    keys.show()
    application.processEvents()
    recorder.save(keys.grab(), "tutorial/citation-keys.png")
    keys.hide()

    # Export stops and asks when papers have no key.
    service = LocalReferences(
        window.registries[str(window.current_root)].catalog,
        KeyPolicy(ads=KeyStyle.MANUAL, other=KeyStyle.MANUAL),
    )
    try:
        service.export_bibtex(
            [
                int(record["document_id"])
                for record in window._selected_records()[:3]
                if record.get("document_id") is not None
            ]
        )
    except CitationKeyError as error:
        needed = CitationKeyDialog(window, error)
        fields = error.references[error.assignment.missing[0]]["fields"]
        needed.table.item(0, 3).setText(
            scholar_key(
                {
                    "author": fields.get("author", "").split(" and ")[:1],
                    "year": fields.get("year", ""),
                    "title": fields.get("title", ""),
                }
            )
        )
        needed.show()
        application.processEvents()
        recorder.save(needed.grab(), "tutorial/citation-keys-needed.png")
        needed.hide()
    else:
        raise RuntimeError("The sample papers should need citation keys.")

    # A fictional profile; the window fills its fields from it.
    from adsfolio.user_profile import UserProfile

    Config.save_user_profile(UserProfile(author_names=("Okafor, Nia",)))
    cv = CVDialog(
        window, settings_path=Path(os.environ["ADSFOLIO_CAPTURE_HOME"]) / "cv.yaml"
    )
    cv.format_choice.setCurrentIndex(cv.format_choice.count() - 1)
    cv.preset.setCurrentIndex(cv.preset.findData("html"))
    cv._apply_preset()
    cv.findChildren(QTabWidget)[0].setCurrentIndex(1)
    cv.resize(1180, 640)
    cv.show()
    application.processEvents()
    cv._render_sample()
    application.processEvents()
    recorder.save(cv.grab(), "tutorial/cv-generator.png")
    recorder.frame(
        _composite(
            window,
            cv.grab().scaled(1180, 540, Qt.KeepAspectRatio, Qt.SmoothTransformation),
        ),
        "Make your CV in LaTeX, HTML, Markdown, or any text format",
        video="workflows",
    )
    cv.hide()

    from adsfolio.gui.library import LibraryView
    from adsfolio.gui.notifications import Notice
    from adsfolio.pdf.files import sha256_file
    from adsfolio.pdf.identity import target_identity

    window._select_view(LibraryView.UNRESOLVED)
    candidate = "2025DEMO....3....3T"
    sample_pdf = root / "Photometry notes.pdf"
    window.notice_bar.enqueue(
        Notice(
            "demo-candidate",
            f"Photometry notes.pdf: Candidate {candidate} needs review. "
            "The PDF metadata has no explicitly labelled DOI. "
            "Compare the ADS entry with the PDF. Use This ID confirms the match.",
            severity="warning",
            reveal_path=sample_pdf,
            update_by_id=True,
            manual_entry=True,
            candidate_bibcode=candidate,
            candidate_sha256=sha256_file(sample_pdf),
            candidate_fingerprint=target_identity({"bibcode": candidate}).fingerprint,
            candidate_confirmable=True,
        )
    )
    window.notification_panel.show_for(window.notifications_button)
    application.processEvents()
    recorder.save(window.notification_panel.grab(), "tutorial/notifications.png")
    recorder.frame(
        _composite(
            window,
            window.notification_panel,
            at=QPoint((SIZE[0] - window.notification_panel.width()) // 2, 70),
        ),
        "Open Notifications to review a proposed ID without moving the library panes",
        video="workflows",
    )
    window.notification_panel.hide()
    window.notice_bar.dismiss("demo-candidate")


def write_video(frames: list[tuple[Any, str]], output: Path) -> None:
    """Encode the captioned frames with cross-fades into an H.264 MP4."""
    from PySide6.QtGui import QPainter

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg is required for the video; use --no-video")
    images = [_with_caption(image, caption) for image, caption in frames]
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
                str(Path(folder) / output.name),
            ],
            check=True,
        )
        data = (Path(folder) / output.name).read_bytes()
        if output.exists() and output.read_bytes() == data:
            logger.info("Unchanged %s", output)
            return
        output.write_bytes(data)
    logger.info("Saved %s (%d frames)", output, index)


def main() -> None:
    """Render the media with isolated configuration and sample files."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--static", type=Path, default=ROOT / "docs/_static")
    parser.add_argument("--no-video", action="store_true")
    args = parser.parse_args()
    if os.environ.get("ADSFOLIO_CAPTURE_HOME") is None:
        with tempfile.TemporaryDirectory(prefix="adsfolio-capture-") as capture_home:
            environment = {
                key: value
                for key, value in os.environ.items()
                if key not in {"ADS_API_TOKEN", "ADSFOLIO_SOURCE_LAUNCH"}
            }
            environment |= {
                "ADSFOLIO_CAPTURE_HOME": capture_home,
                "QT_QPA_PLATFORM": "offscreen",
            }
            command = [sys.executable, __file__, "--static", str(args.static.resolve())]
            if args.no_video:
                command.append("--no-video")
            raise SystemExit(
                subprocess.run(
                    command, env=environment, cwd=capture_home, check=False
                ).returncode
            )
    recorder = Recorder(args.static)
    capture(recorder)
    if not args.no_video:
        for name, frames in recorder.videos.items():
            write_video(frames, args.static / f"tutorial/{name}.mp4")


if __name__ == "__main__":
    main()
