"""Desktop token setup using the existing per-user configuration."""

from __future__ import annotations

import os

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from ..config import Config
from .osint import open_url


class TokenDialog(QDialog):
    """Save an ADS token without terminal input or a network request.

    Parameters
    ----------
    parent : `QWidget`, optional
        Parent window. Cancel leaves the existing token unchanged.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("NASA ADS Token")
        layout = QVBoxLayout(self)
        instructions = QLabel(
            'Get your token from <a href="https://scixplorer.org/user/settings/token">'
            "ADS / SciXplorer settings</a>. Paste it below to enable ADS queries.<br>"
            "Cancel to browse local records offline.",
            self,
        )
        instructions.linkActivated.connect(open_url)
        instructions.setWordWrap(True)
        layout.addWidget(instructions)
        self.token = QLineEdit(self)
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setAccessibleName("NASA ADS token")
        self.token.setPlaceholderText("Paste your API token")
        layout.addWidget(self.token)
        storage = QLabel(
            "Saved in your existing per-user token file (not a keychain). "
            "Saving does not verify the token with ADS.",
            self,
        )
        storage.setWordWrap(True)
        layout.addWidget(storage)
        self.environment_notice = QLabel(
            "ADS_API_TOKEN is set in your environment and takes precedence "
            "over a token saved here.",
            self,
        )
        self.environment_notice.setWordWrap(True)
        self.environment_notice.setVisible(
            bool(os.environ.get(Config.ENV_VAR, "").strip())
        )
        layout.addWidget(self.environment_notice)
        self.error = QLabel(self)
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self) -> None:
        """Save a nonempty token, retaining the dialog when saving fails."""
        token = self.token.text().strip()
        if not token:
            self.error.setText("Paste a token, or choose Cancel to continue offline.")
            self.token.setFocus()
            return
        try:
            Config.save_token(token)
        except OSError:
            self.error.setText(
                "Could not save the token. Check permissions for your user "
                "configuration directory."
            )
            return
        self.token.clear()
        super().accept()
