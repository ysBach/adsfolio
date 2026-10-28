"""Metadata readiness verification for catalog records."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

METADATA_STALE_KEY = "_adsfolio_metadata_stale"
WATCH_PENDING_KEY = "_adsfolio_watch_pending"
METADATA_SHA256_KEY = "_adsfolio_metadata_sha256"
EXTERNAL_METADATA_KEY = "_adsfolio_external_metadata"
REFERENCE_METADATA_KEY = "_adsfolio_reference"
LOCKED_FIELDS_KEY = "_adsfolio_locked_fields"


def _get_field(record: Mapping[str, Any], metadata: Mapping[str, Any], key: str) -> Any:
    """Retrieve field value from top-level record with fallback to metadata."""
    if key in record:
        return record[key]
    return metadata.get(key)


def _is_valid_text_or_sequence(val: Any) -> bool:
    """Check if value is a non-empty string or a sequence with non-empty strings."""
    if isinstance(val, str):
        return bool(val.strip())
    if isinstance(val, Sequence) and not isinstance(val, (bytes, bytearray)):
        return any(isinstance(elem, str) and bool(elem.strip()) for elem in val)
    return False


def _is_valid_year(val: Any) -> bool:
    """Check if value is a valid year (integer or digit string, rejecting boolean)."""
    if isinstance(val, bool):
        return False
    if isinstance(val, int):
        return True
    if isinstance(val, str):
        cleaned = val.strip()
        return bool(cleaned and cleaned.isdigit())
    return False


def stored_bibcode_matches_file(record: Mapping[str, Any]) -> bool:
    """Return whether a saved bibcode belongs to the current catalog digest."""
    metadata = record.get("metadata")
    if not isinstance(metadata, Mapping):
        return False
    if not (record.get("bibcode") or metadata.get("bibcode")):
        return False
    if record.get(METADATA_STALE_KEY) or metadata.get(METADATA_STALE_KEY):
        return False
    expected = metadata.get(METADATA_SHA256_KEY) or metadata.get("sha256")
    return bool(expected and expected == record.get("sha256"))


def metadata_ready(record: Mapping[str, Any]) -> bool:
    """Return whether current metadata can name this PDF.

    Parameters
    ----------
    record : Mapping[str, Any]
        Catalog or sidecar record mapping, supporting top-level overrides and
        nested metadata mapping.

    Returns
    -------
    bool
        True if the record contains non-stale fields required for default
        renaming (title, author, year), matches its file digest if tracked,
        and has resolved status or verified external / manual provenance.
    """
    raw_metadata = record.get("metadata")
    metadata: Mapping[str, Any] = (
        raw_metadata if isinstance(raw_metadata, Mapping) else {}
    )

    if bool(_get_field(record, metadata, METADATA_STALE_KEY)):
        return False

    expected_sha = _get_field(record, metadata, METADATA_SHA256_KEY)
    file_sha = _get_field(record, metadata, "sha256")
    if expected_sha is not None and (not file_sha or expected_sha != file_sha):
        return False

    title = _get_field(record, metadata, "title")
    if not _is_valid_text_or_sequence(title):
        return False

    author = _get_field(record, metadata, "author")
    if not _is_valid_text_or_sequence(author):
        return False

    year = _get_field(record, metadata, "year")
    if not _is_valid_year(year):
        return False

    status = str(_get_field(record, metadata, "status") or "").strip().lower()
    if status == "resolved":
        return True

    has_external = bool(_get_field(record, metadata, EXTERNAL_METADATA_KEY))
    if has_external and expected_sha is not None and expected_sha == file_sha:
        return True

    has_reference = bool(_get_field(record, metadata, REFERENCE_METADATA_KEY))
    if has_reference:
        return True

    return bool(_get_field(record, metadata, LOCKED_FIELDS_KEY))
