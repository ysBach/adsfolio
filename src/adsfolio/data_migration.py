"""Read earlier application data without changing the source files."""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path


def copy_data_directory(source: Path, destination: Path) -> None:
    """Copy an earlier data directory once, using SQLite snapshots for databases.

    Existing destinations are never merged or replaced. The source is retained.
    Close the earlier application before migration to keep its JSON settings and
    database snapshot consistent with each other.
    """
    if destination.exists() or not source.is_dir():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=".adsfolio-migration-", dir=destination.parent)
    )
    try:
        for original in source.rglob("*"):
            relative = original.relative_to(source)
            target = staging / relative
            if original.is_symlink():
                raise ValueError(f"Cannot migrate symbolic link: {original}")
            if original.is_dir():
                target.mkdir(exist_ok=True)
            elif original.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                if original.name.endswith(("-wal", "-shm", "-journal")):
                    continue
                with original.open("rb") as stream:
                    is_database = stream.read(16) == b"SQLite format 3\x00"
                if is_database:
                    with closing(
                        sqlite3.connect(
                            original.resolve().as_uri() + "?mode=ro", uri=True
                        )
                    ) as old:
                        with closing(sqlite3.connect(target)) as new:
                            old.backup(new)
                    shutil.copystat(original, target)
                else:
                    shutil.copy2(original, target)
        # A non-empty destination created by another process is never replaced.
        if destination.exists():
            return
        os.rename(staging, destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
