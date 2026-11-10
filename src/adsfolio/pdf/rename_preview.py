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
import os
import sqlite3
import stat
import unicodedata
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..config import Config
from .catalog import PdfCatalog
from .files import sha256_file
from .identity import IDENTITY_KEY
from .recheck_worker import write_result
from .registry import open_registry_state
from .renamer import PdfRenamer, _recover_stale_record, verify_record_identity
from .tracked_move import move_tracked_file

logger = logging.getLogger(__name__)

REQUEST_VERSION = 1


def _catalog_identity(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return stable catalog fields that identify one tracked PDF record."""
    return {
        "location_id": record.get("location_id"),
        "attachment_id": record.get("attachment_id"),
        "document_id": record.get("document_id"),
        "current_version_id": record.get("version_id"),
        "current_path": record.get("current_path"),
        "sha256": record.get("sha256"),
        "status": record.get("status"),
        "missing_at": record.get("missing_at"),
        "metadata": record.get("metadata"),
    }


def _path_without_symlink(root: Path, relative: str) -> Path:
    """Return an existing root-relative path without symlink components."""
    path = root
    parts = Path(relative).parts
    for index, part in enumerate(parts):
        path = path / part
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ValueError(f"Planned path contains a symbolic link: {relative}")
        if index < len(parts) - 1 and not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"Planned path parent is not a directory: {relative}")
    return path


def _normalize_move_directory(
    root: Path, value: str | Path, *, must_exist: bool
) -> Path:
    """Normalize a move directory and reject symlinks and paths outside root."""
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    try:
        raw_relative = path.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"Rename move directory is outside the library: {path}"
        ) from exc
    current = root
    for part in raw_relative.parts:
        if part == "..":
            current = current.parent
            if current != root and root not in current.parents:
                raise ValueError(
                    f"Rename move directory is outside the library: {path}"
                )
            continue
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            if must_exist:
                raise ValueError(f"Rename target directory does not exist: {path}")
            break
        if stat.S_ISLNK(info.st_mode):
            raise ValueError(f"Rename move directory contains a symbolic link: {path}")
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"Rename move path is not a directory: {current}")
    normalized = Path(os.path.abspath(path))
    try:
        relative = normalized.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"Rename move directory is outside the library: {path}"
        ) from exc
    if not relative.parts:
        if must_exist and not root.is_dir():
            raise ValueError(f"Rename target directory does not exist: {root}")
        return root
    if must_exist and not normalized.is_dir():
        raise ValueError(f"Rename target directory does not exist: {normalized}")
    return normalized


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


def build_apply_request(
    root: Path,
    actions: list[Mapping[str, Any]],
    *,
    config_dir: Path,
    result_path: Path,
) -> dict[str, Any]:
    """Return a request to apply an immutable, previously reviewed action list."""
    return {
        "version": REQUEST_VERSION,
        "operation": "apply",
        "root": str(root),
        "actions": [dict(action) for action in actions],
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
        root = Path(request["root"]).expanduser().resolve()
        options = dict(request["options"])
        if options.get("move_resolved_from") is not None:
            options["move_resolved_from"] = _normalize_move_directory(
                root, options["move_resolved_from"], must_exist=False
            )
        if options.get("move_resolved_to") is not None:
            options["move_resolved_to"] = _normalize_move_directory(
                root, options["move_resolved_to"], must_exist=True
            )
        state = open_registry_state(root)
        try:
            renamer = PdfRenamer()
            try:
                actions: list[dict[str, Any]] = []
                plans = renamer.batch_rename(
                    state, dry_run=True, _action_sink=actions, **options
                )
            except ValueError as exc:
                payload["invalid"] = str(exc)
                return payload
        finally:
            state.close()
        safe_actions = []
        unsafe_sources = set()
        for action in actions:
            source_rel = str(action["source"])
            target_rel = str(action["target"])
            try:
                source_path = _path_without_symlink(root, source_rel)
                if not stat.S_ISREG(source_path.lstat().st_mode):
                    raise ValueError("Source is not a regular file.")
                target_parent_rel = Path(target_rel).parent.as_posix()
                target_parent = (
                    root
                    if target_parent_rel == "."
                    else _path_without_symlink(root, target_parent_rel)
                )
                if not stat.S_ISDIR(target_parent.lstat().st_mode):
                    raise ValueError(
                        f"Destination parent is not a directory: {target_parent_rel}"
                    )
            except (OSError, ValueError) as exc:
                unsafe_sources.add(source_rel)
                renamer.skipped[source_rel] = (
                    f"Planned path is unsafe or unavailable: {exc}"
                )
                continue
            safe_actions.append(action)
        actions = safe_actions
        plans = [plan for plan in plans if str(plan[0]) not in unsafe_sources]
        payload["plans"] = [[str(old), str(new)] for old, new in plans]
        payload["actions"] = [
            {**action, "catalog": _catalog_identity(action["catalog"])}
            for action in actions
        ]
        payload["skipped"] = {str(k): str(v) for k, v in renamer.skipped.items()}
        payload["identity_refused"] = {
            str(k): str(v) for k, v in renamer.identity_refused.items()
        }
    except Exception as exc:
        logger.exception("Rename preview failed for %s", request.get("root"))
        payload["error"] = str(exc) or type(exc).__name__
    return payload


def apply_plan(request: Mapping[str, Any]) -> dict[str, Any]:
    """Preflight and apply only the exact actions in a confirmed preview."""
    payload: dict[str, Any] = {"applied": [], "conflicts": [], "error": None}
    state = None
    try:
        if request.get("version") != REQUEST_VERSION:
            raise ValueError(f"Unsupported preview request: {request.get('version')}")
        root = Path(request["root"]).expanduser().resolve()
        actions = request.get("actions")
        if not isinstance(actions, list) or not actions:
            raise ValueError("A fresh nonempty preview is required.")
        state = open_registry_state(root)
        catalog = state.registry.catalog
        prepared = []
        normalized_actions = []
        seen_sources: set[str] = set()
        seen_targets: set[str] = set()

        for action in actions:
            source_rel = PdfCatalog._normalize_relative_path(str(action["source"]))
            target_rel = PdfCatalog._normalize_relative_path(str(action["target"]))
            source_key = unicodedata.normalize("NFC", source_rel).casefold()
            target_key = unicodedata.normalize("NFC", target_rel).casefold()
            if source_key in seen_sources:
                raise ValueError("The preview contains duplicate source paths.")
            if target_key in seen_targets:
                raise ValueError(
                    "The preview contains duplicate targets after Unicode "
                    "normalization and case folding."
                )
            seen_sources.add(source_key)
            seen_targets.add(target_key)
            normalized_actions.append((action, source_rel, target_rel))

        # Validate every action before changing a catalog record or moving a file.
        for action, source_rel, target_rel in normalized_actions:
            source = _path_without_symlink(root, source_rel)
            target_parent_rel = Path(target_rel).parent.as_posix()
            target_parent = (
                root
                if target_parent_rel == "."
                else _path_without_symlink(root, target_parent_rel)
            )
            if not stat.S_ISDIR(target_parent.lstat().st_mode):
                raise ValueError(
                    f"Destination parent is not a directory: {target_parent_rel}"
                )
            target = target_parent / Path(target_rel).name
            try:
                source_stat = source.lstat()
                if not stat.S_ISREG(source_stat.st_mode):
                    raise ValueError("The source is not a regular PDF file.")
                digest = sha256_file(source)
            except OSError as exc:
                raise ValueError(
                    f"Cannot read planned source {source_rel}: {exc}"
                ) from exc
            if digest != action.get("sha256"):
                raise ValueError(f"The PDF changed after preview: {source_rel}")
            record = catalog.get_file(root, source_rel)
            if (
                record is None
                or record.get("missing_at") is not None
                or _catalog_identity(record) != action.get("catalog")
            ):
                raise ValueError(
                    f"The catalog record changed after preview: {source_rel}"
                )
            try:
                target.lstat()
            except FileNotFoundError:
                pass
            else:
                raise ValueError(f"Destination exists: {target_rel}")
            target_record = catalog.get_file(root, target_rel)
            if target_record is not None and target_record.get("missing_at") is None:
                raise ValueError(f"Catalog destination is occupied: {target_rel}")
            metadata = state._record_view(record)
            recovered = None
            if action.get("recovery"):
                recovered = _recover_stale_record(
                    state,
                    source_rel,
                    apply=False,
                    expected_record=record,
                    expected_sha256=digest,
                )
                if recovered is None:
                    raise ValueError(
                        f"The stale record cannot be recovered: {source_rel}"
                    )
            else:
                verify_record_identity(source, metadata, sha256=digest, state=state)
            # Keep the file identity stable through preflight.
            after = source.lstat()
            if (
                source_stat.st_dev,
                source_stat.st_ino,
                source_stat.st_size,
                source_stat.st_mtime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ):
                raise ValueError(f"The PDF changed during preflight: {source_rel}")
            prepared.append(
                {
                    "source_rel": source_rel,
                    "target_rel": target_rel,
                    "source": source,
                    "target": target,
                    "record": record,
                    "digest": digest,
                    "recovered": recovered,
                }
            )

        # Commit safe stale-record recovery only after all actions pass preflight.
        for item in prepared:
            if item["recovered"] is None:
                continue
            committed = _recover_stale_record(
                state,
                item["source_rel"],
                apply=True,
                expected_record=item["record"],
                expected_sha256=item["digest"],
            )
            if committed is None or {
                key: value for key, value in committed.items() if key != IDENTITY_KEY
            } != {
                key: value
                for key, value in item["recovered"].items()
                if key != IDENTITY_KEY
            }:
                raise ValueError(
                    f"The catalog changed during recovery: {item['source_rel']}"
                )
            current = catalog.get_file(root, item["source_rel"])
            if current is None or state._record_view(current) != committed:
                raise ValueError(
                    f"The catalog changed during recovery: {item['source_rel']}"
                )
            item["move_catalog"] = _catalog_identity(current)

        for item in prepared:
            source_rel = item["source_rel"]
            target_rel = item["target_rel"]
            source = _path_without_symlink(root, source_rel)
            target_parent_rel = Path(target_rel).parent.as_posix()
            target_parent = (
                root
                if target_parent_rel == "."
                else _path_without_symlink(root, target_parent_rel)
            )
            if not stat.S_ISDIR(target_parent.lstat().st_mode):
                raise ValueError(
                    f"Destination parent is not a directory: {target_parent_rel}"
                )
            target = target_parent / Path(target_rel).name
            current = catalog.get_file(root, source_rel)
            expected = item.get("move_catalog", _catalog_identity(item["record"]))
            if current is None or _catalog_identity(current) != expected:
                raise ValueError(f"The catalog changed before move: {source_rel}")
            if sha256_file(source) != item["digest"]:
                raise ValueError(f"The PDF changed before move: {source_rel}")
            move_tracked_file(state, source, target)
            payload["applied"].append([source_rel, target_rel])
    except (OSError, KeyError, TypeError, ValueError, sqlite3.Error) as exc:
        payload["error"] = str(exc) or type(exc).__name__
        payload["conflicts"].append(payload["error"])
    finally:
        if state is not None:
            state.close()
    return payload


def main(request_path: Path) -> int:
    """Run the request in this process and write its result file."""
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    Config.config_dir_override = Path(request["config_dir"])
    result = (
        apply_plan(request)
        if request.get("operation") == "apply"
        else run_request(request)
    )
    write_result(Path(request["result_path"]), result)
    for source, target in result.get("applied", []):
        logger.info("Renamed %s to %s", source, target)
    if result.get("error"):
        logger.error("Rename Apply stopped: %s", result["error"])
        return 1
    return 0


__all__ = ["apply_plan", "build_apply_request", "build_request", "main", "run_request"]
