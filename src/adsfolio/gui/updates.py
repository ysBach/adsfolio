"""Check GitHub for stable desktop releases without blocking the window."""

from __future__ import annotations

import enum
import hashlib
import json
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote

from packaging.version import InvalidVersion, Version
from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from .. import __version__

REPOSITORY_URL = "https://github.com/ysBach/adsfolio"
RELEASES_URL = f"{REPOSITORY_URL}/releases/latest"
LATEST_RELEASE_API = "https://api.github.com/repos/ysBach/adsfolio/releases/latest"
CHANGELOG_URL = "https://raw.githubusercontent.com/ysBach/adsfolio/{tag}/CHANGELOG.md"
CHECKSUMS_ASSET = "SHA256SUMS.txt"
HOMEBREW_COMMAND = "brew upgrade --cask adsfolio"
_TRANSFER_TIMEOUT_MS = 15_000
_CHECKSUM_LINE = re.compile(r"^([0-9a-fA-F]{64}) [ *](\S.*)$")
_CASKROOMS = (
    Path("/opt/homebrew/Caskroom/adsfolio"),
    Path("/usr/local/Caskroom/adsfolio"),
)


@dataclass(frozen=True)
class Release:
    """A stable release, its download page, and its published asset names."""

    version: Version
    url: str
    tag: str = ""
    assets: tuple[str, ...] = ()

    def download_url(self, name: str) -> str:
        """Return the project download URL for one asset of this release."""
        return (
            f"{REPOSITORY_URL}/releases/download/"
            f"{quote(self.tag, safe='')}/{quote(name, safe='')}"
        )


class InstallationKind(enum.Enum):
    """How this copy of ADSfolio was installed, which decides how to update."""

    SOURCE = "Local source launcher"
    HOMEBREW = "Homebrew application"
    DESKTOP = "Desktop application"
    PYTHON = "Python installation"


def installation_kind(
    environ: Mapping[str, str] | None = None,
    *,
    frozen: bool | None = None,
    platform: str | None = None,
    caskrooms: tuple[Path, ...] | None = None,
) -> InstallationKind:
    """Classify the running installation.

    The source launcher is checked first: a machine can have both a Homebrew
    cask record and a source launcher that replaced the cask's application.
    """
    environ = os.environ if environ is None else environ
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    platform = sys.platform if platform is None else platform
    caskrooms = _CASKROOMS if caskrooms is None else caskrooms
    if environ.get("ADSFOLIO_SOURCE_LAUNCH") == "1" and not frozen:
        return InstallationKind.SOURCE
    if not frozen:
        return InstallationKind.PYTHON
    if platform == "darwin" and any(path.is_dir() for path in caskrooms):
        return InstallationKind.HOMEBREW
    return InstallationKind.DESKTOP


def asset_name(platform: str, machine: str) -> str | None:
    """Return the release asset that installs on a platform, or `None`.

    Parameters
    ----------
    platform : `str`
        A `sys.platform` value.
    machine : `str`
        A ``platform.machine()`` value of the running process.
    """
    machine = machine.lower()
    intel = machine in {"x86_64", "amd64"}
    if platform == "darwin":
        if machine in {"arm64", "aarch64"}:
            return "adsfolio-macos-arm64.dmg"
        return "adsfolio-macos-x86_64.dmg" if intel else None
    if platform.startswith("win") and intel:
        return "adsfolio-windows-x86_64.zip"
    if platform.startswith("linux") and intel:
        return "adsfolio-linux-x86_64.tar.gz"
    return None


def parse_sha256sums(text: str) -> dict[str, str]:
    """Map file names to lowercase SHA-256 digests from ``sha256sum`` output."""
    digests: dict[str, str] = {}
    for line in text.splitlines():
        match = _CHECKSUM_LINE.match(line.strip())
        if match:
            digests[match.group(2)] = match.group(1).lower()
    return digests


def changelog_between(markdown: str, current: Version, latest: Version) -> str:
    """Return changelog sections newer than `current` up to `latest`.

    Sections start at ``## <version>`` headings. Sections whose heading does
    not begin with a version, such as ``## Unreleased``, are skipped.
    """
    kept: list[str] = []
    include = False
    for line in markdown.splitlines():
        if line.startswith("## "):
            words = line[3:].split()
            try:
                version = Version(words[0]) if words else None
            except InvalidVersion:
                version = None
            include = version is not None and current < version <= latest
        elif line.startswith("# "):
            include = False
        if include:
            kept.append(line)
    return "\n".join(kept).strip()


def download_filename(release: Release, name: str) -> str:
    """Insert the release version into an unversioned asset file name."""
    return f"adsfolio-{release.version}-{name.removeprefix('adsfolio-')}"


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
    raw_assets = payload.get("assets")
    assets = tuple(
        item["name"]
        for item in (raw_assets if isinstance(raw_assets, list) else ())
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    )
    return Release(
        version,
        f"{REPOSITORY_URL}/releases/tag/{quote(tag, safe='')}",
        tag,
        assets,
    )


def _request(url: str, policy: QNetworkRequest.RedirectPolicy) -> QNetworkRequest:
    request = QNetworkRequest(QUrl(url))
    request.setRawHeader(b"User-Agent", f"ADSfolio/{__version__}".encode())
    request.setAttribute(QNetworkRequest.RedirectPolicyAttribute, policy)
    request.setAttribute(
        QNetworkRequest.CacheLoadControlAttribute, QNetworkRequest.AlwaysNetwork
    )
    request.setTransferTimeout(_TRANSFER_TIMEOUT_MS)
    return request


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
        self.changelog: str | None = None
        self.changelog_error: str | None = None
        self._network = QNetworkAccessManager(self)
        self._reply: QNetworkReply | None = None
        self._changelog_reply: QNetworkReply | None = None

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
        request = _request(LATEST_RELEASE_API, QNetworkRequest.SameOriginRedirectPolicy)
        request.setRawHeader(b"Accept", b"application/vnd.github+json")
        request.setRawHeader(b"Accept-Language", b"en")
        self.latest = None
        self._cancel_changelog()
        self.changelog = None
        self.changelog_error = None
        self.error = None
        self.checked_at = None
        self._reply = self._network.get(request)
        self._reply.finished.connect(self._complete)
        self.changed.emit()

    @property
    def changelog_busy(self) -> bool:
        """Return whether the release notes are still loading."""
        return self._changelog_reply is not None

    def fetch_changelog(self) -> None:
        """Load the notes between this version and the available release.

        The notes come from ``CHANGELOG.md`` at the release tag, because the
        GitHub release body contains only generated comparison links.
        """
        release = self.latest
        if (
            release is None
            or not self.update_available
            or self.changelog_busy
            or self.changelog is not None
        ):
            return
        self.changelog_error = None
        self._changelog_reply = self._network.get(
            _request(
                CHANGELOG_URL.format(tag=quote(release.tag, safe="")),
                QNetworkRequest.SameOriginRedirectPolicy,
            )
        )
        self._changelog_reply.finished.connect(self._complete_changelog)
        self.changed.emit()

    def _cancel_changelog(self) -> None:
        reply = self._changelog_reply
        if reply is not None:
            self._changelog_reply = None
            reply.finished.disconnect(self._complete_changelog)
            reply.abort()
            reply.deleteLater()

    @Slot()
    def _complete_changelog(self) -> None:
        reply = self._changelog_reply
        release = self.latest
        if reply is None:
            return
        self._changelog_reply = None
        status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        try:
            if reply.error() != QNetworkReply.NoError or status != 200:
                self.changelog_error = (
                    "Release notes are unavailable. View the release page instead."
                )
            elif release is not None:
                text = bytes(reply.readAll()).decode("utf-8", errors="replace")
                self.changelog = changelog_between(
                    text, self.current_version, release.version
                )
        finally:
            reply.deleteLater()
        self.changed.emit()

    def cancel(self) -> None:
        """Abort an active check and discard its pending result."""
        self._cancel_changelog()
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


class UpdateDownloader(QObject):
    """Download one release asset and verify it against the release checksums.

    The checksum file is fetched first. The asset streams to ``<name>.part``
    while it is hashed, and it is renamed only after the SHA-256 digest
    matches. The checksums come from the same release, so the check detects
    corrupted or truncated downloads; it does not authenticate the publisher.
    """

    changed = Signal()
    # Byte counts can exceed 32 bits, so they travel as Python integers.
    progress = Signal(object, object)
    completed = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.error: str | None = None
        self.path: Path | None = None
        self._network = QNetworkAccessManager(self)
        self._reply: QNetworkReply | None = None
        self._release: Release | None = None
        self._name = ""
        self._target: Path | None = None
        self._expected = ""
        self._file: BinaryIO | None = None
        self._digest = hashlib.sha256()

    @property
    def release(self) -> Release | None:
        """Return the release of the current or last download."""
        return self._release

    @property
    def busy(self) -> bool:
        """Return whether a checksum or asset request is active."""
        return self._reply is not None

    def start(self, release: Release, name: str, directory: Path) -> None:
        """Download asset `name` of `release` into `directory`."""
        if self.busy:
            return
        self.error = None
        self.path = None
        self._release = release
        self._name = name
        self._target = Path(directory) / download_filename(release, name)
        self._get(release.download_url(CHECKSUMS_ASSET), self._checksums_done)
        self.changed.emit()

    def cancel(self) -> None:
        """Abort the download and remove its partial file."""
        if not self.busy:
            return
        self._finish("Download cancelled.")

    def _get(self, url: str, done: object) -> None:
        # Release downloads redirect from github.com to a GitHub content host.
        reply = self._network.get(
            _request(url, QNetworkRequest.NoLessSafeRedirectPolicy)
        )
        self._reply = reply
        reply.finished.connect(done)

    def _failure(self, reply: QNetworkReply) -> str | None:
        status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        if reply.error() != QNetworkReply.NoError:
            return f"Download failed: {reply.errorString()}"
        if status != 200:
            return f"Download failed: GitHub returned HTTP {status}."
        return None

    @Slot()
    def _checksums_done(self) -> None:
        reply = self._reply
        if reply is None or self._release is None or self._target is None:
            return
        self._reply = None
        reply.deleteLater()
        failure = self._failure(reply)
        if failure:
            self._finish(failure)
            return
        digests = parse_sha256sums(bytes(reply.readAll()).decode(errors="replace"))
        expected = digests.get(self._name)
        if expected is None:
            self._finish(f"The release has no checksum for {self._name}.")
            return
        self._expected = expected
        self._digest = hashlib.sha256()
        try:
            self._target.parent.mkdir(parents=True, exist_ok=True)
            self._file = self._partial_path().open("wb")
        except OSError as exc:
            self._finish(f"Could not save the download: {exc}")
            return
        self._get(self._release.download_url(self._name), self._asset_done)
        assert self._reply is not None
        self._reply.readyRead.connect(self._write_available)
        self._reply.downloadProgress.connect(self._forward_progress)

    @Slot("qint64", "qint64")
    def _forward_progress(self, received: int, total: int) -> None:
        self.progress.emit(received, total)

    def _partial_path(self) -> Path:
        assert self._target is not None
        return self._target.with_name(f"{self._target.name}.part")

    @Slot()
    def _write_available(self) -> None:
        reply = self._reply
        if reply is None or self._file is None:
            return
        chunk = bytes(reply.readAll())
        if not chunk:
            return
        try:
            self._file.write(chunk)
        except OSError as exc:
            self._finish(f"Could not save the download: {exc}")
            return
        self._digest.update(chunk)

    @Slot()
    def _asset_done(self) -> None:
        reply = self._reply
        if reply is None:
            return
        self._write_available()
        if self._reply is None:
            return
        self._reply = None
        reply.deleteLater()
        failure = self._failure(reply)
        if failure:
            self._finish(failure)
            return
        if self._digest.hexdigest() != self._expected:
            self._finish("The download did not match the release checksum.")
            return
        assert self._file is not None and self._target is not None
        try:
            self._file.close()
            self._file = None
            os.replace(self._partial_path(), self._target)
        except OSError as exc:
            self._finish(f"Could not save the download: {exc}")
            return
        self.path = self._target
        self.changed.emit()
        self.completed.emit(self._target)

    def _finish(self, error: str) -> None:
        """Stop with `error`, closing and removing any partial file."""
        reply = self._reply
        self._reply = None
        if reply is not None:
            for signal in (reply.finished, reply.readyRead, reply.downloadProgress):
                try:
                    signal.disconnect()
                except (RuntimeError, TypeError):
                    pass
            reply.abort()
            reply.deleteLater()
        if self._file is not None:
            try:
                self._file.close()
            except OSError:
                pass
            self._file = None
        if self._target is not None:
            try:
                self._partial_path().unlink(missing_ok=True)
            except OSError:
                pass
        self.error = error
        self.changed.emit()
