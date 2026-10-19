"""Enumerate PDF files in a library directory."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from pathlib import Path

__all__ = ["iter_pdf_paths", "sha256_file"]


def iter_pdf_paths(directory: Path, recursive: bool) -> Iterator[Path]:
    """Yield PDF files under `directory`, matching the suffix case-insensitively.

    A symbolic link is yielded only if its target is a regular file. Symbolic
    links to directories are ignored and never entered, and dangling links are
    not yielded. Paths are not resolved.

    Parameters
    ----------
    directory : Path
        Directory to scan.
    recursive : bool
        Also scan subdirectories.

    Yields
    ------
    Path
        Path of each PDF, built from `directory`.

    Raises
    ------
    OSError
        If `directory` or any subdirectory cannot be listed, or an entry's
        type cannot be determined (for example, permission denied). Callers
        must treat the error as an incomplete scan and must not conclude from
        it that unseen files are missing.

    Notes
    -----
    ``os.DirEntry.is_dir()`` and ``is_file()`` return `False` when the entry no
    longer exists, so a file removed mid-scan is silently not yielded. Other
    `OSError` values propagate.
    """
    pending = [Path(directory)]
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    if recursive:
                        pending.append(Path(entry.path))
                elif entry.name.lower().endswith(".pdf") and entry.is_file():
                    yield Path(entry.path)


def sha256_file(path: Path) -> str:
    """Return the SHA-256 hex digest of a file, read in 1 MiB chunks.

    Raises
    ------
    OSError
        If the file cannot be opened or read, including `FileNotFoundError`
        when it no longer exists.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
