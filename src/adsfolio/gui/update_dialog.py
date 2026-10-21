"""Show available releases, their changes, and the install path for this copy."""

from __future__ import annotations

import platform
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QStandardPaths, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QStyleFactory,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .icons import RoundIconButton
from .osint import open_path, open_url, reveal_in_file_manager
from .updates import (
    HOMEBREW_COMMAND,
    RELEASES_URL,
    InstallationKind,
    UpdateChecker,
    UpdateDownloader,
    asset_name,
)

# macOS system blue; the dot marks an available update in light and dark themes.
UPDATE_DOT_COLOR = QColor("#0a84ff")


def quiet_progress_bar(parent: QWidget) -> QProgressBar:
    """Create a progress bar that stops drawing once hidden.

    The native macOS style animates indeterminate bars with a shared AppKit
    indicator that keeps redrawing after the bar is hidden. Fusion animates
    only while the bar is visible.
    """
    bar = QProgressBar(parent)
    style = QStyleFactory.create("Fusion")
    style.setParent(bar)
    bar.setStyle(style)
    return bar


class UpdateButton(RoundIconButton):
    """Round download button that draws a blue dot while an update is available."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("download", "App Update", parent)
        self._dot_visible = False

    @property
    def dot_visible(self) -> bool:
        """Return whether the update dot is drawn."""
        return self._dot_visible

    def set_dot_visible(self, visible: bool) -> None:
        """Show or hide the update dot."""
        if visible != self._dot_visible:
            self._dot_visible = visible
            self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        if not self._dot_visible:
            return
        radius = self.DIAMETER / 8
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(UPDATE_DOT_COLOR)
        # Place the dot on the circle's upper-right edge, at 45 degrees.
        center = self.DIAMETER / 2
        offset = center * 0.70710678
        painter.drawEllipse(QPointF(center + offset, center - offset), radius, radius)
        painter.end()


class UpdateDialog(QDialog):
    """Compare versions, show release notes, and install, skip, or cancel.

    Parameters
    ----------
    checker : `UpdateChecker`
        Shared release check.
    downloader : `UpdateDownloader`
        Shared asset download.
    kind : `InstallationKind`
        How this copy is installed. Only desktop downloads offer **Install**.
    parent : `QWidget`, optional
        Parent window.
    browser_command : `str`, optional
        Configured browser for release links.
    download_directory : `Path`, optional
        Destination for verified downloads. Defaults to the user's
        Downloads folder.
    """

    skip_requested = Signal(str)

    def __init__(
        self,
        checker: UpdateChecker,
        downloader: UpdateDownloader,
        kind: InstallationKind,
        parent: QWidget | None = None,
        *,
        browser_command: str | None = None,
        download_directory: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.checker = checker
        self.downloader = downloader
        self.kind = kind
        self.browser_command = browser_command
        self.download_directory = download_directory or Path(
            QStandardPaths.writableLocation(QStandardPaths.DownloadLocation)
            or Path.home()
        )
        self.skipped_version: str | None = None
        self.setWindowTitle("Software Update")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        self.headline = QLabel(self)
        font = self.headline.font()
        font.setPointSize(font.pointSize() + 3)
        font.setBold(True)
        self.headline.setFont(font)
        self.headline.setWordWrap(True)
        layout.addWidget(self.headline)

        versions = QFormLayout()
        self.installed_label = QLabel(str(checker.current_version), self)
        self.available_label = QLabel(self)
        for label in (self.installed_label, self.available_label):
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        versions.addRow("Installed version:", self.installed_label)
        versions.addRow("Available version:", self.available_label)
        layout.addLayout(versions)

        self.notes_title = QLabel("What's new", self)
        notes_font = self.notes_title.font()
        notes_font.setBold(True)
        self.notes_title.setFont(notes_font)
        layout.addWidget(self.notes_title)
        self.notes = QTextBrowser(self)
        self.notes.setOpenLinks(False)
        self.notes.anchorClicked.connect(lambda url: self._open_link(url.toString()))
        self.notes.setMinimumHeight(200)
        layout.addWidget(self.notes, 1)

        self.install_note = QLabel(self)
        self.install_note.setWordWrap(True)
        self.install_note.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.install_note)
        self.progress = quiet_progress_bar(self)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.download_status = QLabel(self)
        self.download_status.setWordWrap(True)
        self.download_status.setTextFormat(Qt.PlainText)
        self.download_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.download_status.hide()
        layout.addWidget(self.download_status)

        buttons = QHBoxLayout()
        self.skip_button = QPushButton("Skip This Version", self)
        self.skip_button.clicked.connect(self._skip)
        buttons.addWidget(self.skip_button)
        self.check_button = QPushButton("Check Again", self)
        self.check_button.clicked.connect(checker.check)
        buttons.addWidget(self.check_button)
        self.release_button = QPushButton("View Release", self)
        self.release_button.clicked.connect(self._view_release)
        buttons.addWidget(self.release_button)
        buttons.addStretch()
        self.cancel_button = QPushButton("Cancel", self)
        self.cancel_button.clicked.connect(self._cancel)
        buttons.addWidget(self.cancel_button)
        self.install_button = QPushButton("Install", self)
        self.install_button.clicked.connect(self._install)
        buttons.addWidget(self.install_button)
        layout.addLayout(buttons)

        checker.changed.connect(self.sync_state)
        downloader.changed.connect(self.sync_state)
        downloader.progress.connect(self._show_progress)
        downloader.completed.connect(self._open_download)
        self.sync_state()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.checker.fetch_changelog()

    def _asset(self) -> str | None:
        release = self.checker.latest
        name = asset_name(sys.platform, platform.machine())
        if release is None or name is None or name not in release.assets:
            return None
        return name

    def sync_state(self) -> None:
        """Show the current check, release notes, and download state."""
        checker = self.checker
        release = checker.latest
        available = checker.update_available and release is not None
        if available:
            self.headline.setText(f"ADSfolio {release.version} is available")
        elif checker.busy:
            self.headline.setText("Checking for updates…")
        elif checker.error:
            self.headline.setText("Could not check for updates")
        elif release is not None:
            self.headline.setText("ADSfolio is up to date")
        else:
            self.headline.setText("Updates have not been checked yet")
        self.available_label.setText(
            str(release.version)
            if release is not None
            else (checker.error or "Unknown")
        )
        skipped = available and str(release.version) == self.skipped_version
        if skipped:
            self.available_label.setText(f"{release.version} (skipped)")

        self.notes_title.setVisible(available)
        self.notes.setVisible(available)
        if available:
            if checker.changelog:
                self.notes.setMarkdown(checker.changelog)
            elif checker.changelog_busy:
                self.notes.setPlainText("Loading release notes…")
            else:
                self.notes.setPlainText(
                    checker.changelog_error
                    or "No release notes were published for this version."
                )
        if not available:
            self.install_note.setText(checker.status if release else "")
        else:
            self.install_note.setText(self._install_text())
        self.install_note.setVisible(bool(self.install_note.text()))

        downloading = self.downloader.busy
        done = (
            available
            and self.downloader.path is not None
            and self.downloader.release == release
        )
        self.skip_button.setVisible(available and not skipped)
        self.skip_button.setEnabled(not downloading)
        self.check_button.setVisible(not available)
        self.check_button.setEnabled(not checker.busy)
        self.release_button.setVisible(
            not available or self.kind is not InstallationKind.HOMEBREW
        )
        self.release_button.setText("View Release" if release else "View Releases")
        install_visible = available and self.kind in {
            InstallationKind.DESKTOP,
            InstallationKind.HOMEBREW,
        }
        self.install_button.setVisible(install_visible)
        if self.kind is InstallationKind.HOMEBREW:
            self.install_button.setText("Copy Command")
            self.install_button.setEnabled(True)
        else:
            self.install_button.setText("Open Download" if done else "Install")
            self.install_button.setEnabled(
                not downloading and (done or self._asset() is not None)
            )
        if install_visible:
            self.install_button.setDefault(True)
        self.cancel_button.setText("Cancel" if available else "Close")
        self.progress.setVisible(downloading)
        status = self.downloader.error
        if done:
            status = (
                f"Verified download saved to {self.downloader.path}. "
                "Quit ADSfolio before replacing the application."
            )
        elif downloading:
            status = "Downloading and verifying…"
        self.download_status.setText(status or "")
        self.download_status.setVisible(available and bool(status))

    def _install_text(self) -> str:
        if self.kind is InstallationKind.HOMEBREW:
            return (
                "ADSfolio was installed with Homebrew. Quit ADSfolio, then run "
                f"this command in Terminal:\n{HOMEBREW_COMMAND}"
            )
        if self.kind is InstallationKind.SOURCE:
            return (
                "This copy runs from a local source checkout. Update the checkout, "
                "run uv sync --all-extras, then restart ADSfolio. Installing a "
                "release would replace this source launcher."
            )
        if self.kind is InstallationKind.PYTHON:
            return (
                "This copy runs from a Python installation. Update the package or "
                "checkout, then restart ADSfolio."
            )
        if self._asset() is None:
            return "This release has no download for this system. View the release page instead."
        target = (
            "opens the disk image"
            if sys.platform == "darwin"
            else "shows the archive in your file manager"
        )
        return (
            "Install downloads the release from GitHub, verifies it against the "
            f"release checksums, and {target}. Nothing is replaced automatically."
        )

    def _show_progress(self, received: int, total: int) -> None:
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(received)
        else:
            self.progress.setRange(0, 0)

    def _install(self) -> None:
        if self.kind is InstallationKind.HOMEBREW:
            QApplication.clipboard().setText(HOMEBREW_COMMAND)
            self.download_status.setText("Command copied.")
            self.download_status.show()
            return
        release = self.checker.latest
        if self.downloader.path is not None and self.downloader.release == release:
            self._open_download(self.downloader.path)
            return
        name = self._asset()
        if release is None or name is None:
            return
        self.progress.setRange(0, 0)
        self.downloader.start(release, name, self.download_directory)

    def _open_download(self, path: Path) -> None:
        opened = (
            open_path(path)
            if sys.platform == "darwin"
            else reveal_in_file_manager(path)
        )
        if not opened:
            self.download_status.setText(f"Open this file to install: {path}")
            self.download_status.show()

    def _skip(self) -> None:
        release = self.checker.latest
        if release is not None:
            self.skipped_version = str(release.version)
            self.skip_requested.emit(self.skipped_version)
        self.close()

    def _cancel(self) -> None:
        if self.downloader.busy:
            self.downloader.cancel()
            return
        self.close()

    def _view_release(self) -> None:
        release = self.checker.latest
        self._open_link(release.url if release else RELEASES_URL)

    def _open_link(self, url: str) -> None:
        if not open_url(QUrl(url).toString(), application=self.browser_command):
            self.download_status.setText(
                f"Could not open the browser. Open this address: {url}"
            )
            self.download_status.show()
