"""Classify current PDF identification separately from saved ADS lookup status."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any

from .cli_common import is_resolved_record
from .identity import IDENTITY_KEY, target_identity
from .readiness import METADATA_SHA256_KEY, METADATA_STALE_KEY, metadata_ready


class ResolutionState(str, Enum):
    """Describe the current identification evidence for a PDF."""

    CHANGED = "changed"
    ADS_LINKED = "ads_linked"
    IDENTIFIED_EXTERNAL = "identified_external"
    LOCAL_METADATA = "local_metadata"
    UNRESOLVED = "unresolved"


IDENTIFICATION_GROUPS = frozenset(
    {"ads_linked", "identified_without_ads", "needs_identification"}
)


def verification_stale(record: Mapping[str, Any]) -> bool:
    """Return whether saved metadata refers to an earlier PDF revision."""
    raw_metadata = record.get("metadata")
    metadata = raw_metadata if isinstance(raw_metadata, Mapping) else {}
    expected_sha = (
        record.get(METADATA_SHA256_KEY)
        or metadata.get(METADATA_SHA256_KEY)
        or metadata.get("sha256")
    )
    return bool(
        record.get(METADATA_STALE_KEY)
        or metadata.get(METADATA_STALE_KEY)
        or (expected_sha and expected_sha != record.get("sha256"))
    )


def current_identity_proof(record: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Return accepted identity evidence bound to the current hash and identifiers.

    This function checks stored evidence. It does not read the PDF or retrieve
    provider metadata. File observation remains the catalog's responsibility.

    Parameters
    ----------
    record
        Catalog record or flattened metadata containing the current file digest.

    Returns
    -------
    Mapping or None
        Current stored proof, or None when the evidence is absent or invalid.
    """
    if verification_stale(record):
        return None
    raw_metadata = record.get("metadata")
    metadata = raw_metadata if isinstance(raw_metadata, Mapping) else record
    proof = metadata.get(IDENTITY_KEY)
    if (
        isinstance(proof, Mapping)
        and bool(record.get("sha256"))
        and proof.get("sha256") == record.get("sha256")
        and proof.get("identity") == target_identity(metadata).fingerprint
        and proof.get("status")
        in {"verified", "manual_confirmed", "filename_associated"}
    ):
        return proof
    return None


def resolution_state(record: Mapping[str, Any]) -> ResolutionState:
    """Classify current evidence without changing metadata or mutation permissions.

    A changed file takes priority over saved ADS links and external evidence.
    Usable metadata without identity evidence remains distinct from identification.
    """
    if verification_stale(record):
        return ResolutionState.CHANGED
    raw_metadata = record.get("metadata")
    metadata = raw_metadata if isinstance(raw_metadata, Mapping) else {}
    bibcode = record.get("bibcode") or metadata.get("bibcode")
    codes = bibcode if isinstance(bibcode, list) else [bibcode]
    has_bibcode = any(isinstance(code, str) and code.strip() for code in codes)
    if has_bibcode and is_resolved_record({**record, "bibcode": bibcode}):
        return ResolutionState.ADS_LINKED
    proof = current_identity_proof(record)
    if proof is not None and proof.get("status") in {"verified", "manual_confirmed"}:
        return ResolutionState.IDENTIFIED_EXTERNAL
    if metadata_ready(record):
        return ResolutionState.LOCAL_METADATA
    return ResolutionState.UNRESOLVED


def identification_group(record: Mapping[str, Any]) -> str:
    """Return the shared view group for filters, counts, and identification badges.

    Returns
    -------
    str
        One of `ads_linked`, `identified_without_ads`, or `needs_identification`.
    """
    state = resolution_state(record)
    if state is ResolutionState.ADS_LINKED:
        return "ads_linked"
    if state is ResolutionState.IDENTIFIED_EXTERNAL:
        return "identified_without_ads"
    return "needs_identification"
