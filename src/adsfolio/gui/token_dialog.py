"""Desktop token setup using the existing per-user configuration."""

from __future__ import annotations

import os

from PySide6.QtCore import QUrl, QUrlQuery, Slot
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..config import AuthenticationError, Config
from .osint import open_url

TEST_QUERY_URL = f"{Config.API_BASE}/search/query"


class TokenDialog(QDialog):
    """Save an ADS token and optionally test it with one ADS search.

    Saving makes no network request. **Test Token** sends one search request
    to ADS with the pasted token, or with the active token when the field is
    empty.

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
            "Saved in your per-user token file, readable only by you (not a "
            "keychain). Saving does not check the token. Test Token sends one "
            "search request to ADS.",
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
        self.source_label = QLabel(self)
        self.source_label.setWordWrap(True)
        layout.addWidget(self.source_label)
        self._show_source()
        self.test_status = QLabel(self)
        self.test_status.setWordWrap(True)
        layout.addWidget(self.test_status)
        self.error = QLabel(self)
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        self.test_button = buttons.addButton(
            "Test Token", QDialogButtonBox.ButtonRole.ActionRole
        )
        self.test_button.clicked.connect(self.test_token)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._network = QNetworkAccessManager(self)
        self._reply: QNetworkReply | None = None

    def _show_source(self) -> None:
        source = Config.token_source()
        self.source_label.setText(
            f"Active token: from {source}."
            if source
            else "No token is saved. ADS lookups need a token."
        )

    @Slot()
    def test_token(self) -> None:
        """Send one ADS search with the pasted or active token."""
        if self._reply is not None:
            return
        token = self.token.text().strip()
        if not token:
            try:
                token = Config.get_token(prompt=False)
            except AuthenticationError:
                self.test_status.setText("Paste a token to test it.")
                return
        url = QUrl(TEST_QUERY_URL)
        query = QUrlQuery()
        for key, value in (("q", "year:2000"), ("rows", "1"), ("fl", "bibcode")):
            query.addQueryItem(key, value)
        url.setQuery(query)
        request = QNetworkRequest(url)
        request.setRawHeader(b"Authorization", f"Bearer {token}".encode())
        request.setRawHeader(b"User-Agent", f"ADSfolio/{__version__}".encode())
        # The token must never follow a redirect to another host.
        request.setAttribute(
            QNetworkRequest.RedirectPolicyAttribute,
            QNetworkRequest.SameOriginRedirectPolicy,
        )
        request.setTransferTimeout(15_000)
        self.test_button.setEnabled(False)
        self.test_status.setText("Testing the token with ADS…")
        self._reply = self._network.get(request)
        self._reply.finished.connect(self._test_finished)

    @Slot()
    def _test_finished(self) -> None:
        reply = self._reply
        if reply is None:
            return
        self._reply = None
        status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        if status == 200:
            message = "ADS accepted the token."
        elif status in {401, 403}:
            message = "ADS rejected the token. Copy a new token from ADS settings."
        elif status == 429:
            message = "ADS rate limit reached. Try again later."
        elif reply.error() != QNetworkReply.NoError:
            message = f"Could not reach ADS: {reply.errorString()}"
        else:
            message = f"ADS returned HTTP {status}. Try again later."
        reply.deleteLater()
        self.test_status.setText(message)
        self.test_button.setEnabled(True)

    def done(self, result: int) -> None:
        """Abort a running token test when the dialog closes."""
        reply = self._reply
        if reply is not None:
            self._reply = None
            reply.finished.disconnect(self._test_finished)
            reply.abort()
            reply.deleteLater()
            self.test_button.setEnabled(True)
        super().done(result)

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
