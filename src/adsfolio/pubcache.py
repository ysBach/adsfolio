"""Persistent user-level cache for the user's ADS publication universe."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import platformdirs

from .data_migration import copy_data_directory

LOGGER = logging.getLogger(__name__)
CACHE_FILENAME = "publications.json"

__all__ = [
    "CACHE_FILENAME",
    "CachedPublications",
    "cache_path",
    "clear",
    "load_publications",
    "profile_matches",
    "save_publications",
]


@dataclass(frozen=True)
class CachedPublications:
    """Publication cache payload loaded from disk."""

    fetched_at: str
    profile: dict[str, Any]
    documents: list[dict[str, Any]]


def cache_path() -> Path:
    """Return the user-level publication cache path."""
    destination = Path(platformdirs.user_cache_dir("adsfolio"))
    copy_data_directory(Path(platformdirs.user_cache_dir("ads")), destination)
    return destination / CACHE_FILENAME


def _profile_payload(profile: Mapping[str, Any] | Any) -> dict[str, Any]:
    if hasattr(profile, "__dataclass_fields__"):
        profile = {
            name: getattr(profile, name) for name in profile.__dataclass_fields__
        }
    if not isinstance(profile, Mapping):
        raise TypeError("profile must be a mapping or dataclass instance")
    normalized = dict(profile)
    if "end_year" in normalized:
        normalized.pop("retire_year", None)
    return normalized


def save_publications(
    profile: Mapping[str, Any] | Any,
    documents: list[Mapping[str, Any]],
) -> Path:
    """Atomically save a publication cache.

    Parameters
    ----------
    profile : mapping or dataclass
        Snapshot of the setup profile used for the query.
    documents : list of mapping
        ADS document dictionaries.

    Returns
    -------
    pathlib.Path
        The written cache path.
    """
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "profile": _profile_payload(profile),
        "documents": [dict(document) for document in documents],
    }

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.replace(path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    return path


def load_publications() -> CachedPublications | None:
    """Load the cache, returning `None` for missing or invalid data."""
    path = cache_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        fetched_at = str(payload["fetched_at"])
        profile = payload["profile"]
        documents = payload["documents"]
        if not isinstance(profile, dict) or not isinstance(documents, list):
            raise ValueError("cache profile/documents have invalid types")
        normalized_documents = [
            dict(document) for document in documents if isinstance(document, dict)
        ]
    except FileNotFoundError:
        return None
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        LOGGER.warning("Ignoring unreadable publication cache %s: %s", path, exc)
        return None
    return CachedPublications(
        fetched_at=fetched_at,
        profile=dict(profile),
        documents=normalized_documents,
    )


def profile_matches(
    cached_profile: Mapping[str, Any], current_profile: Mapping[str, Any] | Any
) -> bool:
    """Return whether a cache profile snapshot equals the current profile."""
    return dict(cached_profile) == _profile_payload(current_profile)


def clear() -> bool:
    """Remove the publication cache and report whether it existed."""
    path = cache_path()
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True
