"""Run one library's automatic identifier recheck outside the GUI thread."""

from __future__ import annotations

import logging
import os
from contextlib import ExitStack
from inspect import signature
from pathlib import Path
from threading import Event
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal, Slot

from ..client import ADSClient
from ..config import AuthenticationError, Config
from ..pdf.doi_providers import DoiProvider
from ..pdf.identify_settings import IdentifySettings
from ..pdf.registry import open_registry_state
from ..pdf.rename_settings import RenameSettings

logger = logging.getLogger(__name__)


def root_is_accessible(root: Path) -> bool:
    """Return whether a library directory can be read and traversed."""
    return root.is_dir() and os.access(root, os.R_OK | os.X_OK)


def has_external_providers(settings: IdentifySettings) -> bool:
    """Return whether the settings include a supported non-ADS provider."""
    return any(
        provider in settings.providers
        for provider in (DoiProvider.CROSSREF, DoiProvider.DATACITE)
    )


class _AdsTokenThread(QThread):
    """Check for a saved token without blocking the GUI."""

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self.cancel_requested = Event()
        self.available = False
        self.error: str | None = None

    def run(self) -> None:
        if self.cancel_requested.is_set():
            return
        try:
            Config.get_token(prompt=False)
            self.available = not self.cancel_requested.is_set()
        except AuthenticationError:
            pass
        except Exception as exc:
            logger.exception("Could not check the ADS token")
            self.error = str(exc) or type(exc).__name__


class AdsTokenCheck(QObject):
    """Report token availability after the worker stops."""

    finished = Signal(bool, object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread = _AdsTokenThread(self)
        self._thread.finished.connect(self._complete)
        self._started = False
        self._finished = False

    def start(self) -> None:
        """Start the token check once."""
        if not self._started:
            self._started = True
            self._thread.start()

    def cancel(self) -> None:
        """Ignore the result if the window is closing."""
        self._thread.cancel_requested.set()

    @property
    def running(self) -> bool:
        """Return whether the worker still needs to finish."""
        return self._started and not self._finished

    @Slot()
    def _complete(self) -> None:
        self._finished = True
        self.finished.emit(self._thread.available, self._thread.error)


class _AdsRecheckThread(QThread):
    """Own the catalog and optional ADS client for one library."""

    def __init__(
        self,
        root: Path,
        identify_settings: IdentifySettings,
        rename_settings: RenameSettings,
        recursive: bool,
        parent: QObject,
        *,
        ads_enabled: bool = True,
    ) -> None:
        super().__init__(parent)
        self.root = root
        self.identify_settings = identify_settings
        self.rename_settings = rename_settings
        self.recursive = recursive
        self.ads_enabled = (
            ads_enabled and DoiProvider.ADS in identify_settings.providers
        )
        self.cancel_requested = Event()
        self.result: Any | None = None
        self.error: str | None = None

    def run(self) -> None:
        """Run the backend and release its resources before completion."""
        try:
            if self.cancel_requested.is_set() or not root_is_accessible(self.root):
                return
            token = None
            if self.ads_enabled:
                try:
                    token = Config.get_token(prompt=False)
                except AuthenticationError:
                    pass
                except Exception as exc:
                    logger.exception("Could not check the ADS token")
                    detail = str(exc) or type(exc).__name__
                    self.error = f"Could not read the ADS token: {detail}"
            ads_enabled = bool(token)
            if not ads_enabled and not has_external_providers(self.identify_settings):
                return
            if self.cancel_requested.is_set():
                return
            if not root_is_accessible(self.root):
                return
            from ..pdf.ads_recheck import recheck_existing

            options = {
                "ads_enabled": ads_enabled,
                "apply": True,
                "cancelled": self.cancel_requested.is_set,
                "identify_settings": self.identify_settings,
                "recursive": self.recursive,
            }
            try:
                signature(recheck_existing).bind(
                    None, None, self.rename_settings, **options
                )
            except TypeError as exc:
                raise RuntimeError(
                    "The ADS recheck backend cannot apply this library's saved scope."
                ) from exc
            with ExitStack() as stack:
                state = open_registry_state(self.root)
                stack.callback(state.close)
                if self.cancel_requested.is_set():
                    return
                client = ADSClient(token=token) if ads_enabled else None
                if client is not None:
                    stack.callback(client.close)
                if self.cancel_requested.is_set():
                    return
                self.result = recheck_existing(
                    state,
                    client,
                    self.rename_settings,
                    **options,
                )
        except Exception as exc:
            logger.exception("Identifier recheck failed for %s", self.root)
            self.error = str(exc) or type(exc).__name__


class AdsRecheckJob(QObject):
    """Deliver one recheck result after its worker thread stops."""

    finished = Signal(object, object)

    def __init__(
        self,
        root: Path,
        identify_settings: IdentifySettings,
        rename_settings: RenameSettings,
        recursive: bool,
        parent: QObject | None = None,
        *,
        ads_enabled: bool = True,
    ) -> None:
        super().__init__(parent)
        self.root = root
        self._thread = _AdsRecheckThread(
            root,
            identify_settings,
            rename_settings,
            recursive,
            self,
            ads_enabled=ads_enabled,
        )
        self._thread.finished.connect(self._complete)
        self._started = False
        self._finished = False

    def start(self) -> None:
        """Start the worker once."""
        if not self._started:
            self._started = True
            self._thread.start()

    def cancel(self) -> None:
        """Request cancellation at the next backend cancellation check."""
        self._thread.cancel_requested.set()

    @property
    def running(self) -> bool:
        """Return whether the worker still needs to finish."""
        return self._started and not self._finished

    @Slot()
    def _complete(self) -> None:
        self._finished = True
        self.finished.emit(self._thread.result, self._thread.error)
