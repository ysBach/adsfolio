"""Isolate tests from user configuration, user cache, and the network.

The fixtures also wait for started Qt threads before Qt deletes their owners.
"""

import sys
import weakref
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

import platformdirs
import pytest
import requests


@pytest.fixture(scope="session", autouse=True)
def isolated_session_fallback_directories():
    """Keep late Qt teardown away from real user settings and caches."""
    with TemporaryDirectory(prefix="adsfolio-pytest-session-") as directory:
        base = Path(directory)
        platformdirs.user_config_dir = lambda appname, *args, **kwargs: str(
            base / "config" / appname
        )
        platformdirs.user_cache_dir = lambda appname, *args, **kwargs: str(
            base / "cache" / appname
        )

        def deny_network(*args, **kwargs):
            raise requests.ConnectionError("External network is disabled during tests")

        requests.Session.request = deny_network
        yield


@pytest.fixture(autouse=True)
def isolated_application_directories(monkeypatch, tmp_path: Path) -> Iterator[None]:
    """Give every test its own platform configuration and cache directories."""
    _drain_qt_events()
    monkeypatch.setattr(
        platformdirs,
        "user_config_dir",
        lambda appname, *args, **kwargs: str(tmp_path / "config" / appname),
    )
    monkeypatch.setattr(
        platformdirs,
        "user_cache_dir",
        lambda appname, *args, **kwargs: str(tmp_path / "cache" / appname),
    )
    yield
    _drain_qt_events()


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers",
        "recheck_process: run automatic rechecks in a real child process",
    )
    config.addinivalue_line(
        "markers",
        "recheck_offer: show the question that starts an identifier recheck",
    )
    config.addinivalue_line(
        "markers",
        "rename_preview_process: build rename previews in a real child process",
    )
    config.addinivalue_line(
        "markers",
        "ads_bibtex_fetch: let BibTeX export ask ADS for its own entries",
    )


@pytest.fixture(autouse=True)
def no_ads_bibtex_fetch(request, monkeypatch) -> None:
    """Keep BibTeX export from asking ADS in tests that do not need it.

    A developer token on the test computer would otherwise send requests.
    Mark a test with ``ads_bibtex_fetch`` to use the real fetch code.
    """
    if request.node.get_closest_marker("ads_bibtex_fetch"):
        return
    try:
        from adsfolio.gui import bibtex_export, main_window
        from adsfolio.gui.export_dialog import ExportPapersDialog
    except ImportError:
        return
    monkeypatch.setattr(ExportPapersDialog, "_fetch_ads_bibtex", lambda self: None)
    monkeypatch.setattr(bibtex_export, "fetch_ads_bibtex_now", lambda *args: "")
    monkeypatch.setattr(main_window, "fetch_ads_bibtex_now", lambda *args: "")


@pytest.fixture(autouse=True)
def in_process_rename_preview(request, monkeypatch) -> None:
    """Build rename previews synchronously so test fakes apply.

    The runner uses the same request and result files as the child process.
    Mark a test with ``rename_preview_process`` to start the real process.
    """
    if request.node.get_closest_marker("rename_preview_process"):
        return
    try:
        from adsfolio.gui.dialogs import RenamePreviewJob
        from adsfolio.pdf import recheck_worker, rename_preview
    except ImportError:
        return

    def launch(self, argv) -> None:
        del argv
        payload = rename_preview.run_request(self.request)
        recheck_worker.write_result(self.result_path, payload)
        self._complete(0)

    monkeypatch.setattr(RenamePreviewJob, "_launch", launch)


@pytest.fixture(autouse=True)
def no_recheck_offer(request, monkeypatch) -> None:
    """Keep the start question for rechecks out of tests that do not need it.

    The question takes keyboard focus, so shortcut tests would fail. Mark a
    test with ``recheck_offer`` to show it.
    """
    if request.node.get_closest_marker("recheck_offer"):
        return
    try:
        from adsfolio.gui.main_window import MainWindow
    except ImportError:
        return
    monkeypatch.setattr(MainWindow, "_offer_ads_recheck", lambda self: None)


@pytest.fixture(autouse=True)
def in_process_recheck_worker(request, monkeypatch) -> None:
    """Run recheck workers in a thread so test fakes and directories apply.

    The thread uses the same request and result files as the child process.
    Mark a test with ``recheck_process`` to start the real child process.
    """
    if request.node.get_closest_marker("recheck_process"):
        return
    try:
        from PySide6.QtCore import QThread

        from adsfolio.gui import ads_recheck_job
        from adsfolio.pdf import recheck_worker
    except ImportError:
        return

    class WorkerThread(QThread):
        def __init__(self, job) -> None:
            super().__init__(job)
            self.job = job

        def run(self) -> None:
            payload = recheck_worker.run_request(
                self.job.request, self.job.cancel_path.exists
            )
            recheck_worker.write_result(self.job.result_path, payload)

    def launch(self, argv) -> None:
        del argv
        self._thread = WorkerThread(self)
        self._thread.finished.connect(lambda: self._complete(0))
        self._thread.start()

    monkeypatch.setattr(ads_recheck_job.AdsRecheckJob, "_launch", launch)


# Qt threads that tests started. Teardown waits for them before it deletes
# their owners: Qt aborts the process when a running QThread is destroyed.
_STARTED_QT_THREADS: weakref.WeakSet = weakref.WeakSet()
# Longest wait for one thread. A cancelled worker stops at its next check.
_QT_THREAD_WAIT_MS = 30_000


def _track_qt_thread_starts(qt) -> None:
    """Record every started QThread once per test session."""
    if getattr(qt.QThread.start, "_adsfolio_tracked", False):
        return
    original = qt.QThread.start

    def start(self, *args, **kwargs):
        _STARTED_QT_THREADS.add(self)
        return original(self, *args, **kwargs)

    start._adsfolio_tracked = True
    qt.QThread.start = start


def _wait_for_qt_threads() -> None:
    """Let started workers finish so that deferred deletes cannot abort."""
    for thread in list(_STARTED_QT_THREADS):
        try:
            running = thread.isRunning()
        except RuntimeError:
            # Qt already deleted this thread.
            continue
        if running and not thread.wait(_QT_THREAD_WAIT_MS):
            raise RuntimeError(
                f"{type(thread).__name__} did not stop within "
                f"{_QT_THREAD_WAIT_MS} ms after the test."
            )


def _drain_qt_events() -> None:
    """Complete queued Qt cleanup before a test changes configuration routing."""
    qt = sys.modules.get("PySide6.QtCore")
    if qt is None:
        return
    _track_qt_thread_starts(qt)
    application = qt.QCoreApplication.instance()
    if application is not None:
        application.processEvents()
        _wait_for_qt_threads()
        qt.QCoreApplication.sendPostedEvents(None, qt.QEvent.Type.DeferredDelete)
