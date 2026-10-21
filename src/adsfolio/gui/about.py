"""Show application information and the latest release check."""

from __future__ import annotations

import platform
from pathlib import Path

from PySide6.QtCore import Qt, qVersion
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from .osint import open_url
from .updates import (
    HOMEBREW_COMMAND,
    RELEASES_URL,
    REPOSITORY_URL,
    InstallationKind,
    UpdateChecker,
    installation_kind,
)


class AboutDialog(QDialog):
    """Show the version, copyright, installation type, and update controls."""

    def __init__(
        self,
        checker: UpdateChecker,
        automatic_action: QAction,
        parent: QWidget | None = None,
        *,
        browser_command: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.checker = checker
        self.browser_command = browser_command
        self.setWindowTitle("About ADSfolio")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        heading = QHBoxLayout()
        icon = QLabel(self)
        icon.setPixmap(QApplication.windowIcon().pixmap(64, 64))
        heading.addWidget(icon)
        self.version_label = QLabel(f"ADSfolio {__version__}", self)
        font = self.version_label.font()
        font.setPointSize(font.pointSize() + 5)
        font.setBold(True)
        self.version_label.setFont(font)
        self.version_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        heading.addWidget(self.version_label, 1)
        layout.addLayout(heading)
        description = QLabel("Organize research PDFs and publication metadata.", self)
        description.setWordWrap(True)
        layout.addWidget(description)
        copyright_label = QLabel("Copyright © 2026 @ysBach", self)
        copyright_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(copyright_label)

        kind = installation_kind()
        self.installation = kind.value
        if kind is InstallationKind.SOURCE:
            installation_text = (
                "Local source launcher. Restart ADSfolio to load source edits. "
                "Update this checkout to use newer source code. "
                "The version label does not track individual source edits. "
                "A desktop installer or Homebrew upgrade replaces this source launcher."
            )
        elif kind is InstallationKind.HOMEBREW:
            installation_text = f"Homebrew application. Update with {HOMEBREW_COMMAND}."
        elif kind is InstallationKind.DESKTOP:
            installation_text = (
                "Desktop application. Install a release for your operating system."
            )
        else:
            installation_text = (
                "Python installation. Update the package or source checkout, "
                "then restart ADSfolio."
            )
        self.installation_label = QLabel(installation_text, self)
        self.installation_label.setWordWrap(True)
        layout.addWidget(self.installation_label)
        if kind is InstallationKind.SOURCE:
            source_path = QLabel(str(Path(__file__).resolve().parents[3]), self)
            source_path.setWordWrap(True)
            source_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
            layout.addWidget(source_path)

        links = QLabel(
            '<a href="https://ysbach.github.io/adsfolio/">Website</a> · '
            '<a href="https://ysbach.github.io/adsfolio/docs/">Help</a> · '
            f'<a href="{REPOSITORY_URL}/issues">Report an issue</a> · '
            f'<a href="{REPOSITORY_URL}/blob/main/LICENSE">BSD 3-Clause License</a>',
            self,
        )
        links.setWordWrap(True)
        links.setTextInteractionFlags(Qt.TextBrowserInteraction)
        links.linkActivated.connect(self._open_link)
        layout.addWidget(links)
        third_party = QLabel(
            "Dependencies retain their own licenses. Bundles include Qt/PySide6 "
            "(LGPL) and PyMuPDF (AGPL). "
            '<a href="https://ysbach.github.io/adsfolio/docs/desktop.html">License details</a>.',
            self,
        )
        third_party.setWordWrap(True)
        third_party.setTextInteractionFlags(Qt.TextBrowserInteraction)
        third_party.linkActivated.connect(self._open_link)
        layout.addWidget(third_party)

        self.update_status = QLabel(self)
        self.update_status.setWordWrap(True)
        self.update_status.setTextFormat(Qt.PlainText)
        self.update_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.update_status)
        self.checked_label = QLabel(self)
        layout.addWidget(self.checked_label)
        updates = QHBoxLayout()
        self.check_button = QPushButton("Check for Updates", self)
        self.check_button.clicked.connect(checker.check)
        updates.addWidget(self.check_button)
        self.release_button = QPushButton("View Releases", self)
        self.release_button.clicked.connect(self._view_release)
        updates.addWidget(self.release_button)
        updates.addStretch()
        layout.addLayout(updates)
        self.automatic_checkbox = QCheckBox(
            "Check for updates when ADSfolio starts", self
        )
        self.automatic_checkbox.setChecked(automatic_action.isChecked())
        self.automatic_checkbox.toggled.connect(automatic_action.setChecked)
        automatic_action.toggled.connect(self.automatic_checkbox.setChecked)
        layout.addWidget(self.automatic_checkbox)
        privacy = QLabel(
            "GitHub receives your IP address and app version. "
            "No PDFs, library metadata, or ADS credentials are sent. "
            "Updates are not installed automatically.",
            self,
        )
        privacy.setWordWrap(True)
        layout.addWidget(privacy)
        self.link_error = QLabel(self)
        self.link_error.setWordWrap(True)
        self.link_error.setTextFormat(Qt.PlainText)
        self.link_error.hide()
        layout.addWidget(self.link_error)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        copy_button = buttons.addButton("Copy App Details", QDialogButtonBox.ActionRole)
        copy_button.clicked.connect(self._copy_details)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        checker.changed.connect(self._update_status)
        self._update_status()

    def _update_status(self) -> None:
        self.update_status.setText(self.checker.status)
        self.check_button.setEnabled(not self.checker.busy)
        self.release_button.setText(
            "View Update" if self.checker.update_available else "View Releases"
        )
        checked = self.checker.checked_at
        self.checked_label.setText(
            f"Last checked: {checked:%Y-%m-%d %H:%M %Z}" if checked else ""
        )
        self.checked_label.setVisible(checked is not None)

    def _view_release(self) -> None:
        release = self.checker.latest
        self._open_link(release.url if release else RELEASES_URL)

    def _open_link(self, url: str) -> None:
        if open_url(url, application=self.browser_command):
            self.link_error.hide()
        else:
            self.link_error.setText(
                f"Could not open the browser. Open this address: {url}"
            )
            self.link_error.show()

    def _copy_details(self) -> None:
        details = (
            f"ADSfolio {__version__}\n"
            f"Installation: {self.installation}\n"
            f"System: {platform.system()} {platform.release()} ({platform.machine()})\n"
            f"Python: {platform.python_version()}\nQt: {qVersion()}\n"
            f"Updates: {self.checker.status}"
        )
        QApplication.clipboard().setText(details)
