"""Run one library's automatic identifier recheck in a separate process.

The desktop app writes a JSON request file and starts the hidden
``adsfolio pdf recheck-worker REQUEST`` command. The worker writes a JSON
result file next to the request and exits. A separate process keeps PDF
parsing off the window's Python interpreter, so scrolling stays smooth.

The app asks the worker to stop by creating the request's cancel file. The
worker checks for that file between steps, saves its progress, and exits.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from inspect import signature
from pathlib import Path
from typing import Any

from ..client import ADSClient
from ..config import AuthenticationError, Config
from .doi_providers import DoiProvider
from .identify_settings import IdentifySettings
from .registry import open_registry_state
from .rename_settings import RenameSettings

logger = logging.getLogger(__name__)

REQUEST_VERSION = 1
# RecheckResult fields that the worker sends back to the app.
RESULT_FIELDS = (
    "checked",
    "updated",
    "renamed",
    "failures",
    "cancelled",
    "planned_updates",
    "planned_renames",
    "activities",
    "remembered_skipped",
    "remembered_outdated",
)


def root_is_accessible(root: Path) -> bool:
    """Return whether a library directory can be read and traversed."""
    return root.is_dir() and os.access(root, os.R_OK | os.X_OK)


def has_external_providers(settings: IdentifySettings) -> bool:
    """Return whether the settings include a supported non-ADS provider."""
    return any(
        provider in settings.providers
        for provider in (DoiProvider.CROSSREF, DoiProvider.DATACITE)
    )


def build_request(
    root: Path,
    identify_settings: IdentifySettings,
    rename_settings: RenameSettings,
    recursive: bool,
    *,
    ads_enabled: bool,
    retry_remembered: bool,
    config_dir: Path,
    cancel_path: Path,
    result_path: Path,
) -> dict[str, Any]:
    """Return a JSON-serializable request for one library recheck.

    Parameters
    ----------
    root
        Library root to check.
    identify_settings, rename_settings
        Effective settings for this library.
    recursive
        Whether the check includes subdirectories.
    ads_enabled
        Whether the worker may contact ADS when a token exists.
    retry_remembered
        Check again the PDFs whose earlier check failed.
    config_dir
        The app's configuration directory. The worker uses the same catalog.
    cancel_path
        File whose existence asks the worker to stop.
    result_path
        File that receives the result.
    """
    return {
        "version": REQUEST_VERSION,
        "root": str(root),
        "identify_settings": identify_settings.to_mapping(),
        "rename_settings": rename_settings.to_mapping(),
        "recursive": recursive,
        "ads_enabled": ads_enabled,
        "retry_remembered": retry_remembered,
        "config_dir": str(config_dir),
        "cancel_path": str(cancel_path),
        "result_path": str(result_path),
    }


def run_request(
    request: Mapping[str, Any], cancelled: Callable[[], bool]
) -> dict[str, Any]:
    """Run one recheck and return a JSON-serializable result payload.

    Parameters
    ----------
    request
        Request from `build_request`.
    cancelled
        Cooperative cancellation check.

    Returns
    -------
    dict
        ``{"result": ..., "error": ...}``. ``result`` is `None` when the
        check did not run, for example after an early cancel or without a
        usable provider.
    """
    payload: dict[str, Any] = {"result": None, "error": None}
    try:
        if request.get("version") != REQUEST_VERSION:
            raise ValueError(f"Unsupported recheck request: {request.get('version')}")
        root = Path(request["root"])
        identify_settings = IdentifySettings.from_mapping(request["identify_settings"])
        rename_settings = RenameSettings.from_mapping(request["rename_settings"])
        if cancelled() or not root_is_accessible(root):
            return payload
        token = None
        if request["ads_enabled"] and DoiProvider.ADS in identify_settings.providers:
            try:
                token = Config.get_token(prompt=False)
            except AuthenticationError:
                pass
            except Exception as exc:
                logger.exception("Could not check the ADS token")
                detail = str(exc) or type(exc).__name__
                payload["error"] = f"Could not read the ADS token: {detail}"
        ads_enabled = bool(token)
        if not ads_enabled and not has_external_providers(identify_settings):
            return payload
        if cancelled() or not root_is_accessible(root):
            return payload
        from .ads_recheck import recheck_existing

        options: dict[str, Any] = {
            "ads_enabled": ads_enabled,
            "apply": True,
            "cancelled": cancelled,
            "identify_settings": identify_settings,
            "recursive": bool(request["recursive"]),
        }
        if request.get("retry_remembered"):
            options["retry_remembered"] = True
        try:
            signature(recheck_existing).bind(None, None, rename_settings, **options)
        except TypeError as exc:
            raise RuntimeError(
                "The ADS recheck backend cannot apply this library's saved scope."
            ) from exc
        with ExitStack() as stack:
            state = open_registry_state(root)
            stack.callback(state.close)
            if cancelled():
                return payload
            client = ADSClient(token=token) if ads_enabled else None
            if client is not None:
                stack.callback(client.close)
            if cancelled():
                return payload
            result = recheck_existing(state, client, rename_settings, **options)
        payload["result"] = {
            name: getattr(result, name)
            for name in RESULT_FIELDS
            if hasattr(result, name)
        }
    except Exception as exc:
        logger.exception("Identifier recheck failed for %s", request.get("root"))
        payload["error"] = str(exc) or type(exc).__name__
    return payload


def write_result(path: Path, payload: Mapping[str, Any]) -> None:
    """Write a result file atomically, so the app never reads a partial file."""
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(payload, default=str), encoding="utf-8")
    os.replace(temporary, path)


def main(request_path: Path) -> int:
    """Run the request in this process and write its result file.

    Returns
    -------
    int
        ``0`` when the result file exists. The result reports check failures.
    """
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    # Use the app's catalog and token, even when this process resolves another
    # default configuration directory.
    Config.config_dir_override = Path(request["config_dir"])
    cancel_path = Path(request["cancel_path"])
    payload = run_request(request, cancel_path.exists)
    write_result(Path(request["result_path"]), payload)
    return 0


__all__ = [
    "RESULT_FIELDS",
    "build_request",
    "has_external_providers",
    "main",
    "root_is_accessible",
    "run_request",
    "write_result",
]
