"""Build a rename preview in a separate process for the desktop app.

The preview checks the identity of every tracked PDF. That reads and parses
each PDF, so it runs in the hidden ``adsfolio pdf rename-preview REQUEST``
command instead of the window's process. The worker writes a JSON result file
next to the request. A preview only plans renames, so the app may stop the
worker at any time.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..config import Config
from .recheck_worker import write_result
from .registry import open_registry_state
from .renamer import PdfRenamer

logger = logging.getLogger(__name__)

REQUEST_VERSION = 1


def build_request(
    root: Path,
    options: Mapping[str, Any],
    *,
    config_dir: Path,
    result_path: Path,
) -> dict[str, Any]:
    """Return a JSON-serializable rename preview request.

    Parameters
    ----------
    root
        Library root to preview.
    options
        Keyword arguments for `PdfRenamer.batch_rename`. Paths must be strings
        or `None`.
    config_dir
        The app's configuration directory. The worker uses the same catalog.
    result_path
        File that receives the result.
    """
    return {
        "version": REQUEST_VERSION,
        "root": str(root),
        "options": dict(options),
        "config_dir": str(config_dir),
        "result_path": str(result_path),
    }


def run_request(request: Mapping[str, Any]) -> dict[str, Any]:
    """Plan renames without changing files and return a result payload.

    Returns
    -------
    dict
        ``plans`` holds ``[current, target]`` pairs. ``skipped`` and
        ``identity_refused`` map filenames to reasons. ``invalid`` holds the
        message of a template or settings error. ``error`` holds any other
        failure.
    """
    payload: dict[str, Any] = {
        "plans": [],
        "skipped": {},
        "identity_refused": {},
        "invalid": None,
        "error": None,
    }
    try:
        if request.get("version") != REQUEST_VERSION:
            raise ValueError(f"Unsupported preview request: {request.get('version')}")
        options = dict(request["options"])
        for name in ("move_resolved_from", "move_resolved_to"):
            if options.get(name) is not None:
                options[name] = Path(options[name])
        state = open_registry_state(Path(request["root"]))
        try:
            renamer = PdfRenamer()
            try:
                plans = renamer.batch_rename(state, dry_run=True, **options)
            except ValueError as exc:
                payload["invalid"] = str(exc)
                return payload
        finally:
            state.close()
        payload["plans"] = [[str(old), str(new)] for old, new in plans]
        payload["skipped"] = {str(k): str(v) for k, v in renamer.skipped.items()}
        payload["identity_refused"] = {
            str(k): str(v) for k, v in renamer.identity_refused.items()
        }
    except Exception as exc:
        logger.exception("Rename preview failed for %s", request.get("root"))
        payload["error"] = str(exc) or type(exc).__name__
    return payload


def main(request_path: Path) -> int:
    """Run the request in this process and write its result file."""
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    Config.config_dir_override = Path(request["config_dir"])
    write_result(Path(request["result_path"]), run_request(request))
    return 0


__all__ = ["build_request", "main", "run_request"]
