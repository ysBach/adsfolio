"""Filesystem moves kept consistent with the PDF catalog."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

__all__ = [
    "TrackedMoveRollbackError",
    "move_tracked_file",
    "resolved_file_directory",
]


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


def resolved_file_directory(
    root: Path, source_path: Path, unresolved_directory: Path | None = None
) -> Path:
    """Choose the directory for a resolved PDF within one catalog root.

    Parameters
    ----------
    root : `Path`
        Library root owning the file's catalog location.
    source_path : `Path`
        Current PDF path.
    unresolved_directory : `Path`, optional
        Configured unresolved directory, relative to `root` when not absolute.
        Defaults to ``<root>/unresolved``.

    Returns
    -------
    Path
        The library root for files below its unresolved subtree; otherwise
        the source parent. An external directory or the root itself is not
        treated as an unresolved subtree because its owning library is ambiguous.
    """
    root = Path(root).expanduser().resolve()
    source = Path(source_path).expanduser().resolve()
    unresolved = (
        Path(unresolved_directory).expanduser()
        if unresolved_directory is not None
        else root / "unresolved"
    )
    if not unresolved.is_absolute():
        unresolved = root / unresolved
    unresolved = unresolved.resolve()
    if root in unresolved.parents and unresolved in source.parents:
        return root
    return source.parent


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
    raw_destination = Path(destination_path).expanduser()
    destination = raw_destination.parent.resolve() / raw_destination.name
    source_relative = _relative_to_root(root, source)
    destination_relative = _relative_to_root(root, destination)

    if source == destination:
        return destination
    if not source.is_file():
        raise FileNotFoundError(f"Tracked source file does not exist: {source}")
    try:
        destination.lstat()
    except FileNotFoundError:
        pass
    else:
        raise FileExistsError(f"Tracked move destination already exists: {destination}")

    registry = getattr(state, "registry", None)
    if registry is not None:
        occupied = registry.catalog.get_file(root, destination_relative)
        if occupied is not None and occupied["missing_at"] is None:
            # A deleted PDF must not reserve its old name forever. Only an
            # actual missing path can release the row; access errors propagate.
            try:
                destination.lstat()
            except FileNotFoundError:
                with registry.catalog._connection:
                    registry.catalog._connection.execute(
                        "UPDATE file_locations SET missing_at = ? WHERE id = ?",
                        (registry.catalog._utc_timestamp(), occupied["location_id"]),
                    )
            else:
                raise ValueError(
                    f"Catalog location already exists: {destination_relative}"
                )
    tracked_entry = state.find_by_current_filename(source_relative)
    if tracked_entry is None:
        raise KeyError(f"Source is not tracked in the catalog: {source_relative}")
    original_key, _record = tracked_entry

    destination.parent.mkdir(parents=True, exist_ok=True)
    # Both paths are inside one library. Hard links provide an atomic
    # no-replace operation; a racing destination must never be overwritten.
    os.link(source, destination)
    try:
        source.unlink()
    except OSError:
        destination.unlink()
        raise
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
            except Exception as state_rollback_error:  # noqa: BLE001 - rollback must continue
                rollback_errors.append(state_rollback_error)
        try:
            os.link(destination, source)
            destination.unlink()
        except Exception as rollback_error:  # noqa: BLE001 - report incomplete rollback
            rollback_errors.append(rollback_error)
        if rollback_errors:
            raise TrackedMoveRollbackError(
                "Catalog update failed and rollback was incomplete: "
                f"catalog={catalog_error!r}; rollback={rollback_errors!r}"
            ) from rollback_errors[-1]
        raise
    return destination
