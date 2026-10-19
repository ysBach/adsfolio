"""Read earlier application data without changing the source files."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

LEGACY_SIDECAR = ".adsapi-pdfs.json"
LEGACY_REGISTRY = ".adsapi-pdfs.sqlite"
_KEY_RENAMES = {
    f"_adsapi_{suffix}": f"_adsfolio_{suffix}"
    for suffix in (
        "locked_fields",
        "reference",
        "metrics",
        "external_metadata",
        "metadata_sha256",
        "metadata_stale",
        "author_text",
    )
}


def normalize_metadata(value: Any) -> Any:
    """Translate reserved metadata keys while preserving user text and values."""
    if isinstance(value, list):
        return [normalize_metadata(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        target = _KEY_RENAMES.get(key, key)
        converted = normalize_metadata(item)
        if target in result and result[target] != converted:
            raise ValueError(f"Conflicting metadata values for {target!r}")
        result[target] = converted
    return result


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


def migrate_catalog_keys(connection: sqlite3.Connection) -> None:
    """Translate reserved keys in a catalog transaction without changing IDs."""
    marker = "adsfolio_metadata_keys"
    if connection.execute(
        "SELECT 1 FROM catalog_meta WHERE key = ?", (marker,)
    ).fetchone():
        return
    with connection:
        for table, identity, column in (
            ("attachments", "id", "metadata_json"),
            ("documents", "id", "metadata_json"),
            ("ads_documents", "bibcode", "raw_json"),
        ):
            rows = connection.execute(
                f"SELECT {identity}, {column} FROM {table}"
            ).fetchall()
            for row_id, encoded in rows:
                original = json.loads(encoded)
                converted = normalize_metadata(original)
                if converted != original:
                    connection.execute(
                        f"UPDATE {table} SET {column} = ? WHERE {identity} = ?",
                        (json.dumps(converted, ensure_ascii=False), row_id),
                    )
        connection.execute(
            "INSERT INTO catalog_meta(key, value) VALUES (?, ?)", (marker, "1")
        )
