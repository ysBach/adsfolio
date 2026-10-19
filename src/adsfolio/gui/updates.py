"""Check GitHub for stable desktop releases without blocking the window."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote

from packaging.version import InvalidVersion, Version
from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from .. import __version__

REPOSITORY_URL = "https://github.com/ysBach/adsfolio"
RELEASES_URL = f"{REPOSITORY_URL}/releases/latest"
LATEST_RELEASE_API = "https://api.github.com/repos/ysBach/adsfolio/releases/latest"


@dataclass(frozen=True)
class Release:
    """A stable release and its download page."""

    version: Version
    url: str


def parse_release(payload: object) -> Release:
    """Read a stable release from a GitHub API response.

    Raises
    ------
    TypeError
        If the response is not a JSON object.
    ValueError
        If the response does not describe a published stable version.
    """
    if not isinstance(payload, dict):
        raise TypeError("GitHub returned an invalid release response.")
    if payload.get("draft") is not False or payload.get("prerelease") is not False:
        raise ValueError("GitHub did not return a published stable release.")
    tag = payload.get("tag_name")
    if not isinstance(tag, str) or not tag or tag != tag.strip():
        raise ValueError("GitHub returned an invalid release tag.")
    try:
        version = Version(tag)
    except InvalidVersion as exc:
        raise ValueError("GitHub returned an unsupported release version.") from exc
    if version.is_prerelease or version.is_devrelease:
        raise ValueError("GitHub did not return a stable release version.")
    return Release(version, f"{REPOSITORY_URL}/releases/tag/{quote(tag, safe='')}")


class UpdateChecker(QObject):
    """Keep one asynchronous release check and its latest result."""

    changed = Signal()
    finished = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.current_version = Version(__version__)
        self.latest: Release | None = None
        self.error: str | None = None
        self.checked_at: datetime | None = None
        self._network = QNetworkAccessManager(self)
        self._reply: QNetworkReply | None = None

    @property
    def busy(self) -> bool:
        """Return whether a request is still active."""
        return self._reply is not None

    @property
    def update_available(self) -> bool:
        """Return whether the checked release is newer than this installation."""
        return self.latest is not None and self.latest.version > self.current_version

    @property
    def status(self) -> str:
        """Describe the current check for the About window."""
        if self.busy:
            return "Checking GitHub for updates…"
        if self.error:
            return self.error
        if self.latest is None:
            return "Updates have not been checked in this session."
        if self.update_available:
            return f"ADSfolio {self.latest.version} is available."
        if self.current_version > self.latest.version:
            return (
                f"This version ({self.current_version}) is newer than the latest "
                f"stable release ({self.latest.version})."
            )
        return (
            f"This version matches the latest stable release ({self.latest.version})."
        )

    @Slot()
    def check(self) -> None:
        """Start a public GitHub lookup unless one is already running."""
        if self.busy:
            return
        request = QNetworkRequest(QUrl(LATEST_RELEASE_API))
        request.setRawHeader(b"Accept", b"application/vnd.github+json")
        request.setRawHeader(b"Accept-Language", b"en")
        request.setRawHeader(b"User-Agent", f"ADSfolio/{__version__}".encode())
        request.setAttribute(
            QNetworkRequest.RedirectPolicyAttribute,
            QNetworkRequest.SameOriginRedirectPolicy,
        )
        request.setAttribute(
            QNetworkRequest.CacheLoadControlAttribute, QNetworkRequest.AlwaysNetwork
        )
        request.setTransferTimeout(15_000)
        self.latest = None
        self.error = None
        self.checked_at = None
        self._reply = self._network.get(request)
        self._reply.finished.connect(self._complete)
        self.changed.emit()

    def cancel(self) -> None:
        """Abort an active check and discard its pending result."""
        reply = self._reply
        if reply is not None:
            self._reply = None
            reply.finished.disconnect(self._complete)
            reply.abort()
            reply.deleteLater()
            self.error = "Update check cancelled."
            self.changed.emit()

    @Slot()
    def _complete(self) -> None:
        reply = self._reply
        if reply is None:
            return
        self._reply = None
        status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        try:
            if status in {403, 429}:
                self.error = "GitHub limited update checks. Try again later or view the releases page."
            elif status == 404:
                self.error = "No published release was found. View the releases page to check manually."
            elif reply.error() != QNetworkReply.NoError:
                self.error = f"Could not check for updates: {reply.errorString()}"
            elif status != 200:
                self.error = f"GitHub returned HTTP {status}. Try again later."
            else:
                self.latest = parse_release(json.loads(bytes(reply.readAll())))
                self.checked_at = datetime.now().astimezone()
        except (TypeError, ValueError) as exc:
            self.error = f"Could not read the release information: {exc}"
        finally:
            reply.deleteLater()
        self.changed.emit()
        self.finished.emit()
