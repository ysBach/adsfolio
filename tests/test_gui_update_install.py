"""Check release notes, verified downloads, skipping, and the update button."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from packaging.version import Version
from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QApplication

from adsfolio.gui.config import load_config
from adsfolio.gui.main_window import MainWindow
from adsfolio.gui.update_dialog import UpdateDialog
from adsfolio.gui.updates import (
    CHANGELOG_URL,
    REPOSITORY_URL,
    InstallationKind,
    UpdateChecker,
    UpdateDownloader,
    asset_name,
    changelog_between,
    download_filename,
    installation_kind,
    parse_release,
    parse_sha256sums,
)

ASSET = "adsfolio-macos-arm64.dmg"
CHANGELOG = """# Changelog

## Unreleased

- Not released.

## 9.1.0 (2030-02-01)

- Newest change.

## 9.0.0 (2030-01-01)

- Middle change.

## 0.0.1 (2020-01-01)

- Old change.
"""


def release_payload(tag: str = "v9.1.0") -> dict[str, object]:
    return {
        "tag_name": tag,
        "draft": False,
        "prerelease": False,
        "assets": [{"name": ASSET}, {"name": "SHA256SUMS.txt"}, {"size": 1}],
    }


class Reply(QObject):
    """Fake reply that can stream chunks before finishing."""

    finished = Signal()
    readyRead = Signal()
    downloadProgress = Signal("qint64", "qint64")

    def __init__(self, body: bytes = b"", status: int | None = 200) -> None:
        super().__init__()
        self.pending = body
        self.http_status = status
        self.aborted = False

    def attribute(self, attribute: QNetworkRequest.Attribute) -> int | None:
        return self.http_status

    def error(self) -> QNetworkReply.NetworkError:
        return QNetworkReply.NoError

    def errorString(self) -> str:
        return "Unavailable"

    def readAll(self) -> bytes:
        data, self.pending = self.pending, b""
        return data

    def stream(self, chunk: bytes) -> None:
        self.pending += chunk
        self.readyRead.emit()

    def abort(self) -> None:
        self.aborted = True


class Network:
    """Return replies in request order and record each request."""

    def __init__(self, *replies: Reply) -> None:
        self.replies = list(replies)
        self.requests: list[QNetworkRequest] = []

    def get(self, request: QNetworkRequest) -> Reply:
        self.requests.append(request)
        return self.replies.pop(0)


@pytest.fixture(autouse=True)
def no_homebrew(monkeypatch) -> None:
    monkeypatch.setattr("adsfolio.gui.updates._CASKROOMS", ())


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        ("darwin", "arm64", "adsfolio-macos-arm64.dmg"),
        ("darwin", "x86_64", "adsfolio-macos-x86_64.dmg"),
        ("win32", "AMD64", "adsfolio-windows-x86_64.zip"),
        ("linux", "x86_64", "adsfolio-linux-x86_64.tar.gz"),
        ("linux", "aarch64", None),
        ("freebsd14", "amd64", None),
    ],
)
def test_asset_name_matches_published_platforms(
    system: str, machine: str, expected: str | None
) -> None:
    assert asset_name(system, machine) == expected


def test_checksums_parse_text_and_binary_modes() -> None:
    digest = "A" * 64
    text = f"{digest}  first.zip\n{'b' * 64} *second.dmg\nnot a checksum line\n"
    assert parse_sha256sums(text) == {"first.zip": "a" * 64, "second.dmg": "b" * 64}


def test_changelog_keeps_versions_after_installed_through_available() -> None:
    notes = changelog_between(CHANGELOG, Version("0.0.1"), Version("9.1.0"))
    assert "Newest change." in notes
    assert "Middle change." in notes
    assert "Old change." not in notes
    assert "Not released." not in notes
    assert changelog_between(CHANGELOG, Version("9.0.0"), Version("9.0.0")) == ""


def test_release_downloads_come_from_the_project_repository() -> None:
    release = parse_release(
        {**release_payload(), "html_url": "https://example.com/untrusted"}
    )
    assert release.assets == (ASSET, "SHA256SUMS.txt")
    assert release.download_url(ASSET) == (
        f"{REPOSITORY_URL}/releases/download/v9.1.0/{ASSET}"
    )
    assert download_filename(release, ASSET) == "adsfolio-9.1.0-macos-arm64.dmg"


def test_installation_kind_prefers_source_launcher_over_homebrew(
    tmp_path: Path,
) -> None:
    source = {"ADSFOLIO_SOURCE_LAUNCH": "1"}
    cask = (tmp_path,)
    assert (
        installation_kind(source, frozen=False, platform="darwin", caskrooms=cask)
        is InstallationKind.SOURCE
    )
    assert (
        installation_kind({}, frozen=True, platform="darwin", caskrooms=cask)
        is InstallationKind.HOMEBREW
    )
    assert (
        installation_kind({}, frozen=True, platform="darwin", caskrooms=())
        is InstallationKind.DESKTOP
    )
    assert installation_kind({}, frozen=False) is InstallationKind.PYTHON


def checker_with_update(qtbot) -> UpdateChecker:
    checker = UpdateChecker()
    checker.current_version = Version("0.0.1")
    checker.latest = parse_release(release_payload())
    return checker


def test_changelog_loads_once_for_the_available_release(qtbot) -> None:
    checker = checker_with_update(qtbot)
    reply = Reply(CHANGELOG.encode())
    network = Network(reply)
    checker._network = network
    checker.fetch_changelog()
    checker.fetch_changelog()
    assert checker.changelog_busy
    assert len(network.requests) == 1
    assert network.requests[0].url().toString() == CHANGELOG_URL.format(tag="v9.1.0")
    reply.finished.emit()
    assert "Newest change." in (checker.changelog or "")
    checker.fetch_changelog()
    assert len(network.requests) == 1


def test_changelog_failure_is_reported_without_notes(qtbot) -> None:
    checker = checker_with_update(qtbot)
    reply = Reply(status=404)
    checker._network = Network(reply)
    checker.fetch_changelog()
    reply.finished.emit()
    assert checker.changelog is None
    assert "unavailable" in (checker.changelog_error or "")


def start_download(
    qtbot, tmp_path: Path, payload: bytes, listed: bytes | None = None
) -> tuple[UpdateDownloader, Reply, Reply, Network]:
    downloader = UpdateDownloader()
    digest = hashlib.sha256(payload if listed is None else listed).hexdigest()
    sums = Reply(f"{digest}  {ASSET}\n".encode())
    asset = Reply()
    network = Network(sums, asset)
    downloader._network = network
    downloader.start(parse_release(release_payload()), ASSET, tmp_path)
    sums.finished.emit()
    return downloader, sums, asset, network


def test_verified_download_streams_to_a_versioned_file(qtbot, tmp_path: Path) -> None:
    payload = b"disk image " * 1000
    downloader, _sums, asset, network = start_download(qtbot, tmp_path, payload)
    completed: list[Path] = []
    downloader.completed.connect(completed.append)
    for request in network.requests:
        assert request.attribute(QNetworkRequest.RedirectPolicyAttribute) == (
            QNetworkRequest.NoLessSafeRedirectPolicy
        )
        assert not request.hasRawHeader("Authorization")
    assert network.requests[0].url().toString().endswith("/v9.1.0/SHA256SUMS.txt")
    progress: list[tuple[int, int]] = []
    downloader.progress.connect(
        lambda received, total: progress.append((received, total))
    )
    asset.stream(payload[:500])
    asset.downloadProgress.emit(500, 2**40)
    asset.stream(payload[500:])
    asset.finished.emit()
    assert progress == [(500, 2**40)]
    target = tmp_path / "adsfolio-9.1.0-macos-arm64.dmg"
    assert completed == [target]
    assert downloader.path == target
    assert target.read_bytes() == payload
    assert not target.with_name(target.name + ".part").exists()
    assert not downloader.busy


def test_checksum_mismatch_removes_the_partial_file(qtbot, tmp_path: Path) -> None:
    downloader, _sums, asset, _network = start_download(
        qtbot, tmp_path, b"tampered", listed=b"original"
    )
    asset.stream(b"tampered")
    asset.finished.emit()
    assert downloader.path is None
    assert "did not match" in (downloader.error or "")
    assert list(tmp_path.iterdir()) == []


def test_cancel_aborts_and_removes_the_partial_file(qtbot, tmp_path: Path) -> None:
    downloader, _sums, asset, _network = start_download(qtbot, tmp_path, b"data")
    asset.stream(b"da")
    downloader.cancel()
    assert asset.aborted
    assert not downloader.busy
    assert downloader.error == "Download cancelled."
    assert list(tmp_path.iterdir()) == []


def test_missing_checksum_stops_before_downloading(qtbot, tmp_path: Path) -> None:
    downloader = UpdateDownloader()
    network = Network(Reply(f"{'a' * 64}  other.zip\n".encode()))
    downloader._network = network
    downloader.start(parse_release(release_payload()), ASSET, tmp_path)
    network_reply_count = len(network.requests)
    downloader._reply.finished.emit()
    assert len(network.requests) == network_reply_count == 1
    assert "no checksum" in (downloader.error or "")


def make_dialog(qtbot, kind: InstallationKind, tmp_path: Path) -> UpdateDialog:
    checker = checker_with_update(qtbot)
    checker._network = Network(Reply(CHANGELOG.encode()))
    dialog = UpdateDialog(
        checker, UpdateDownloader(), kind, download_directory=tmp_path
    )
    qtbot.addWidget(dialog)
    return dialog


def test_desktop_dialog_shows_versions_notes_and_installs(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("adsfolio.gui.update_dialog.sys.platform", "darwin")
    monkeypatch.setattr("adsfolio.gui.update_dialog.platform.machine", lambda: "arm64")
    dialog = make_dialog(qtbot, InstallationKind.DESKTOP, tmp_path)
    dialog.show()
    dialog.checker._changelog_reply.finished.emit()
    assert dialog.headline.text() == "ADSfolio 9.1.0 is available"
    assert dialog.available_label.text() == "9.1.0"
    assert "Newest change." in dialog.notes.toPlainText()
    assert dialog.install_button.isVisible() and dialog.install_button.isEnabled()
    assert dialog.skip_button.isVisible()
    assert dialog.cancel_button.text() == "Cancel"
    started = []
    monkeypatch.setattr(dialog.downloader, "start", lambda *args: started.append(args))
    dialog.install_button.click()
    assert started == [(dialog.checker.latest, ASSET, tmp_path)]


def test_install_is_unavailable_without_a_matching_asset(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("adsfolio.gui.update_dialog.sys.platform", "linux")
    monkeypatch.setattr("adsfolio.gui.update_dialog.platform.machine", lambda: "x86_64")
    dialog = make_dialog(qtbot, InstallationKind.DESKTOP, tmp_path)
    dialog.show()
    assert not dialog.install_button.isEnabled()
    assert "no download for this system" in dialog.install_note.text()


def test_homebrew_dialog_copies_the_upgrade_command(qtbot, tmp_path: Path) -> None:
    dialog = make_dialog(qtbot, InstallationKind.HOMEBREW, tmp_path)
    dialog.show()
    assert dialog.install_button.text() == "Copy Command"
    dialog.install_button.click()
    assert QApplication.clipboard().text() == "brew upgrade --cask adsfolio"


@pytest.mark.parametrize("kind", [InstallationKind.SOURCE, InstallationKind.PYTHON])
def test_source_and_python_copies_explain_instead_of_installing(
    qtbot, tmp_path: Path, kind: InstallationKind
) -> None:
    dialog = make_dialog(qtbot, kind, tmp_path)
    dialog.show()
    assert not dialog.install_button.isVisible()
    assert dialog.release_button.isVisible()
    assert "restart ADSfolio" in dialog.install_note.text()


@pytest.fixture
def window(qtbot) -> MainWindow:
    widget = MainWindow()
    qtbot.addWidget(widget)
    return widget


def finish_startup_check(window: MainWindow) -> None:
    reply = Reply(json.dumps(release_payload()).encode())
    window.update_checker._network = Network(reply, Reply(CHANGELOG.encode()))
    window.check_updates_on_start()
    reply.finished.emit()


def test_update_button_dot_marks_an_unskipped_update(window: MainWindow) -> None:
    assert not window.update_button.dot_visible
    finish_startup_check(window)
    assert window.update_button.dot_visible
    assert "9.1.0" in window.update_button.toolTip()
    assert window.notice_bar.queued_keys() == ("application-update",)


def test_skip_persists_and_hides_dot_and_notice(window: MainWindow) -> None:
    finish_startup_check(window)
    window.update_button.click()
    dialog = window._update_dialog
    assert dialog is not None and dialog.isVisible()
    dialog.skip_button.click()
    assert not dialog.isVisible()
    assert not window.update_button.dot_visible
    assert window.notice_bar.queued_keys() == ()
    assert load_config().skipped_update_version == "9.1.0"
    finish_startup_check(window)
    assert not window.update_button.dot_visible
    assert window.notice_bar.queued_keys() == ()
    window.update_button.click()
    assert dialog.available_label.text() == "9.1.0 (skipped)"
    assert not dialog.skip_button.isVisible()


def test_update_button_checks_when_no_result_exists(window: MainWindow) -> None:
    network = Network(Reply(json.dumps(release_payload()).encode()))
    window.update_checker._network = network
    window.update_button.click()
    assert window.update_checker.busy
    assert len(network.requests) == 1


def test_download_for_an_older_release_is_not_offered(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("adsfolio.gui.update_dialog.sys.platform", "darwin")
    monkeypatch.setattr("adsfolio.gui.update_dialog.platform.machine", lambda: "arm64")
    dialog = make_dialog(qtbot, InstallationKind.DESKTOP, tmp_path)
    dialog.downloader._release = parse_release(release_payload("v9.0.0"))
    dialog.downloader.path = tmp_path / "old.dmg"
    dialog.show()
    assert dialog.install_button.text() == "Install"
    dialog.downloader._release = dialog.checker.latest
    dialog.sync_state()
    assert dialog.install_button.text() == "Open Download"
