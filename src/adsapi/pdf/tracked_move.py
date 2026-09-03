"""Filesystem moves kept consistent with the PDF catalog."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Mapping, Protocol

__all__ = ["TrackedMoveRollbackError", "move_tracked_file"]


class TrackedMoveRollbackError(RuntimeError):
    """Report both catalog and filesystem rollback failures."""


class TrackedFileState(Protocol):
    """State operations required for a catalog-consistent file move."""

    directory: Path

    def find_by_current_filename(
        self, filename: str
    ) -> tuple[str, Mapping[str, Any]] | None: ...

    def set_current_filename(
        self, original_filename: str, current_filename: str
    ) -> bool: ...

    def save(self) -> Path: ...


def _relative_to_root(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(
            f"Tracked move destination is outside catalog root: {path}"
        ) from exc


def move_tracked_file(
    state: TrackedFileState, source_path: Path, destination_path: Path
) -> Path:
    """Move one tracked file and update its root-relative catalog location.

    The filesystem move is rolled back if the catalog update fails.
    """
    root = Path(state.directory).resolve()
    source = Path(source_path).resolve()
    destination = Path(destination_path).resolve()
    source_relative = _relative_to_root(root, source)
    destination_relative = _relative_to_root(root, destination)

    if source == destination:
        return destination
    if not source.is_file():
        raise FileNotFoundError(f"Tracked source file does not exist: {source}")
    if destination.exists():
        raise FileExistsError(f"Tracked move destination already exists: {destination}")

    registry = getattr(state, "registry", None)
    if registry is not None:
        active_paths = {
            str(record["current_path"])
            for record in registry.catalog.query_files(root=root)
        }
        if destination_relative in active_paths:
            raise ValueError(f"Catalog location already exists: {destination_relative}")
    tracked_entry = state.find_by_current_filename(source_relative)
    if tracked_entry is None:
        raise KeyError(f"Source is not tracked in the catalog: {source_relative}")
    original_key, _record = tracked_entry

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(destination))
    state_updated = False
    try:
        updated = state.set_current_filename(original_key, destination_relative)
        if not updated:
            raise RuntimeError(
                f"Catalog did not update tracked path: {source_relative}"
            )
        state_updated = True
        if registry is None:
            state.save()
    except Exception as catalog_error:
        rollback_errors: list[Exception] = []
        if state_updated:
            try:
                rollback_key = (
                    destination_relative if registry is not None else original_key
                )
                state.set_current_filename(rollback_key, source_relative)
                if registry is None:
                    state.save()
            except Exception as state_rollback_error:
                rollback_errors.append(state_rollback_error)
        try:
            shutil.move(str(destination), str(source))
        except Exception as rollback_error:
            rollback_errors.append(rollback_error)
        if rollback_errors:
            raise TrackedMoveRollbackError(
                "Catalog update failed and rollback was incomplete: "
                f"catalog={catalog_error!r}; rollback={rollback_errors!r}"
            ) from rollback_errors[-1]
        raise
    return destination
