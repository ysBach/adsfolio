"""Recheck tracked PDF identifiers with enabled providers."""

from __future__ import annotations

import copy
import json
import os
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import ADSError, Config
from .doi_providers import DoiProvider, ProviderError
from .identifier import PdfIdentifier
from .identify import _lookup_verified_external, _save_revalidated_external
from .identify_settings import IdentifySettings, IdentifyStrategy
from .identity import (
    IDENTITY_KEY,
    IdentityValidationError,
    UnverifiedIdentityError,
    assert_mutation_allowed,
    extract_pdf_own_identity,
    target_identity,
)
from .readiness import (
    EXTERNAL_METADATA_KEY,
    LOCKED_FIELDS_KEY,
    METADATA_SHA256_KEY,
    METADATA_STALE_KEY,
    PREVIOUS_RESOLUTION_KEY,
    REFERENCE_METADATA_KEY,
    WATCH_PENDING_KEY,
)
from .registry import CentralPdfRegistryState, merge_ads_metadata
from .rename_settings import RenameSettings
from .renamer import PdfRenamer, verify_record_identity
from .upgrade_utils import _normalize_doi
from .watcher import WatchIdentifyPolicy

if TYPE_CHECKING:
    from ..client import ADSClient

__all__ = ["RecheckResult", "recheck_existing"]
PENDING_RENAMES_KEY = "_adsfolio_ads_recheck_pending_renames"
PREVIOUS_IDENTITIES_KEY = "_adsfolio_ads_recheck_previous_identities"


@dataclass
class RecheckResult:
    """Summarize one recheck of a library root.

    Parameters
    ----------
    checked
        Number of eligible attachments examined.
    updated
        Number of attachments with updated metadata.
    renamed
        Number of PDF locations renamed.
    failures
        File-specific or ADS errors that need attention.
    activities
        File-specific identity updates and rename outcomes.
    cancelled
        Whether cancellation stopped the operation.
    planned_updates, planned_renames
        Counts of checked changes in a preview or applied run.
    """

    checked: int = 0
    updated: int = 0
    renamed: int = 0
    failures: list[str] = field(default_factory=list)
    cancelled: bool = False
    planned_updates: int = 0
    planned_renames: int = 0
    activities: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _LookupItem:
    record: dict[str, Any]
    bibcode: str
    bibcodes: frozenset[str]
    dois: frozenset[str]
    arxiv_ids: frozenset[str]
    filename_bibcode: str | None = field(default=None, kw_only=True)


@dataclass(frozen=True)
class _Snapshot(_LookupItem):
    document: dict[str, Any] | None
    locations: tuple[dict[str, Any], ...]
    path: Path
    digest: str


class _CancelledSearch(ADSError):
    """Stop a batched ADS search at a request boundary."""


class _GuardedSearch:
    """Check cancellation and quota before every ADS request."""

    def __init__(
        self,
        search: Any,
        cancelled: Callable[[], bool] | None,
        transaction_active: Callable[[], bool],
    ) -> None:
        self._search = search
        self._cancelled = cancelled
        self._transaction_active = transaction_active

    def query(self, **kwargs: Any) -> Any:
        if _stop_requested(self._cancelled):
            raise _CancelledSearch("The ADS recheck was cancelled.")
        if self._transaction_active():
            raise ADSError("ADS requests cannot run inside a catalog transaction.")
        if not Config.check_rate_limit():
            raise ADSError("ADS quota is low. Retry after the quota resets.")
        return self._search.query(**kwargs)


class _GuardedClient:
    def __init__(
        self,
        client: ADSClient,
        cancelled: Callable[[], bool] | None,
        transaction_active: Callable[[], bool],
    ) -> None:
        self.search = _GuardedSearch(client.search, cancelled, transaction_active)
        self.export = _GuardedExport(client, cancelled, transaction_active)


class _GuardedExport:
    """Guard custom author exports and reuse identical results in one run."""

    def __init__(
        self,
        client: ADSClient,
        cancelled: Callable[[], bool] | None,
        transaction_active: Callable[[], bool],
    ) -> None:
        self._client = client
        self._cancelled = cancelled
        self._transaction_active = transaction_active
        self._cache: dict[tuple[tuple[str, ...], str], str] = {}

    def custom(self, bibcodes: list[str], *, format_string: str) -> str:
        if _stop_requested(self._cancelled):
            raise _CancelledSearch("The ADS recheck was cancelled.")
        key = (tuple(bibcodes), format_string)
        if key in self._cache:
            return self._cache[key]
        if self._transaction_active():
            raise ADSError("ADS requests cannot run inside a catalog transaction.")
        if not Config.check_rate_limit():
            raise ADSError("ADS quota is low. Retry after the quota resets.")
        export = getattr(self._client, "export", None)
        if export is None:
            raise ValueError("ADS custom author export is unavailable.")
        result = export.custom(bibcodes, format_string=format_string)
        self._cache[key] = result
        return result


def _stop_requested(cancelled: Callable[[], bool] | None) -> bool:
    return bool(cancelled is not None and cancelled())


def _locked(metadata: Mapping[str, Any], name: str) -> bool:
    fields = metadata.get(LOCKED_FIELDS_KEY, [])
    return isinstance(fields, (list, tuple, set)) and name in fields


def _first(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value or "").strip()


def _bibcodes(metadata: Mapping[str, Any]) -> frozenset[str]:
    codes = set()
    for field_name in ("bibcode", "alternate_bibcode", "identifier"):
        raw = metadata.get(field_name)
        for value in raw if isinstance(raw, (list, tuple)) else [raw]:
            text = str(value or "").strip()
            if text and target_identity({field_name: text}).bibcodes:
                codes.add(text)
    return frozenset(codes)


def _safe_path(root: Path, relative: str) -> Path:
    path = root / relative
    resolved = path.resolve()
    if path.is_symlink() or not resolved.is_relative_to(root) or path != resolved:
        raise ValueError("The PDF path is a symbolic link or lies outside its root.")
    return path


def _snapshot(
    state: CentralPdfRegistryState,
    record: dict[str, Any],
    *,
    allow_stale: bool = False,
) -> _Snapshot:
    """Check saved identifiers and capture current file and catalog state."""
    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        raise TypeError("The saved metadata is invalid.")
    stale = bool(record.get(METADATA_STALE_KEY) or metadata.get(METADATA_STALE_KEY))
    if stale and not allow_stale:
        raise ValueError("The saved metadata is stale.")
    if REFERENCE_METADATA_KEY in metadata:
        raise ValueError("Edit this managed reference in Local References.")
    proof = metadata.get(IDENTITY_KEY)
    target = target_identity(metadata)
    bibcode = _first(metadata.get("bibcode"))
    external = metadata.get(EXTERNAL_METADATA_KEY)
    if isinstance(external, Mapping):
        source_doi = _first(external.get("doi"))
        if source_doi and target_identity({"doi": source_doi}).claims_outside(target):
            raise ValueError("The saved provider DOI conflicts with the record.")
    root = Path(record["root"]).resolve()
    path = _safe_path(root, str(record["current_path"]))
    evidence = extract_pdf_own_identity(path)
    if evidence.blocking_errors:
        raise ValueError("The current PDF identity cannot be read.")
    digest = evidence.sha256
    saved_hashes = [record.get("sha256")]
    if not stale:
        saved_hashes.extend((metadata.get("sha256"), metadata.get(METADATA_SHA256_KEY)))
    for saved in saved_hashes:
        if saved and saved != digest:
            raise ValueError(
                "The saved PDF hash does not match the current file. "
                "Refresh the library, then retry."
            )
    if isinstance(proof, Mapping) and not stale:
        if proof.get("sha256") and proof["sha256"] != digest:
            raise ValueError("The saved identity proof has a stale PDF hash.")
        if proof.get("identity") and proof["identity"] != target.fingerprint:
            raise ValueError(
                "The saved identity proof does not match the saved identifiers."
            )
        if proof.get("status") in {"verified", "filename_associated"}:
            checked = assert_mutation_allowed(path, metadata, expected_sha256=digest)
            if checked.get("status") != proof.get("status"):
                raise ValueError("The saved identity proof is no longer valid.")
    if (target.dois and not evidence.dois.issubset(target.dois)) or (
        target.arxiv_ids and not evidence.arxiv_ids.issubset(target.arxiv_ids)
    ):
        raise ValueError("The PDF contradicts the saved identifiers.")
    catalog = state.registry.catalog
    document_id = record.get("document_id")
    document = catalog.get_document(document_id) if document_id is not None else None
    locations = tuple(catalog.query_files(attachment_id=record["attachment_id"]))
    if not locations:
        raise ValueError("The attachment has no current location.")
    for location in locations:
        if location["sha256"] and location["sha256"] != digest:
            raise ValueError("Another location has different or unknown PDF contents.")
        location_root = Path(location["root"]).resolve()
        location_path = _safe_path(location_root, str(location["current_path"]))
        other = extract_pdf_own_identity(location_path)
        if other.sha256 != digest or other.blocking_errors:
            raise ValueError(
                "Another location has different or unreadable PDF contents."
            )
    return _Snapshot(
        record=copy.deepcopy(record),
        document=copy.deepcopy(document),
        locations=copy.deepcopy(locations),
        path=path,
        digest=digest,
        bibcode=bibcode,
        bibcodes=_bibcodes(metadata),
        dois=target.dois,
        arxiv_ids=target.arxiv_ids,
    )


def _eligible(record: Mapping[str, Any]) -> bool:
    metadata = record.get("metadata")
    if not isinstance(metadata, Mapping):
        return False
    identity = target_identity(metadata)
    return bool(identity.bibcodes or identity.dois or identity.arxiv_ids)


def _saved_proof_current(record: Mapping[str, Any]) -> bool:
    """Return whether a non-stale stored proof names this record and digest."""
    metadata = record.get("metadata")
    if not isinstance(metadata, Mapping):
        return False
    proof = metadata.get(IDENTITY_KEY)
    return bool(
        not record.get(METADATA_STALE_KEY)
        and not metadata.get(METADATA_STALE_KEY)
        and isinstance(proof, Mapping)
        and proof.get("status")
        in {"verified", "manual_confirmed", "filename_associated"}
        and record.get("sha256")
        and proof.get("sha256") == record.get("sha256")
        and proof.get("identity") == target_identity(metadata).fingerprint
    )


def _search(
    identifier: PdfIdentifier,
    snapshots: list[_LookupItem],
    result: RecheckResult,
    cancelled: Callable[[], bool] | None,
) -> dict[tuple[str, str], list[dict[str, Any]]] | None:
    """Use deduplicated batches and retain raw responses only in memory."""
    unique_codes: dict[str, str] = {}
    for item in snapshots:
        for code in sorted(item.bibcodes):
            unique_codes.setdefault(code.casefold(), code)
    bibcodes = list(unique_codes.values())
    dois = sorted({doi for item in snapshots for doi in item.dois})
    arxiv_ids = sorted(
        {value for item in snapshots if not item.dois for value in item.arxiv_ids}
    )
    matches: dict[tuple[str, str], list[dict[str, Any]]] = {}
    searches = (
        ("bibcode", bibcodes, identifier.query_docs_for_bibcodes),
        ("doi", dois, identifier.query_docs_for_dois),
        ("arxiv", arxiv_ids, identifier.query_docs_for_arxiv_ids),
    )
    for kind, values, query in searches:
        for start in range(0, len(values), identifier.MAX_BATCH_TERMS):
            if _stop_requested(cancelled):
                result.cancelled = True
                return None
            batch = values[start : start + identifier.MAX_BATCH_TERMS]

            def collect(
                documents: list[dict[str, Any]],
                *,
                batch: list[str] = batch,
                kind: str = kind,
            ) -> None:
                for document in documents:
                    identity = target_identity(document)
                    raw_dois = document.get("doi")
                    doi_values = (
                        raw_dois if isinstance(raw_dois, (list, tuple)) else [raw_dois]
                    )
                    doc_dois = {
                        _normalize_doi(str(value or "")) for value in doi_values
                    }
                    for value in batch:
                        key = value.casefold()
                        if kind == "bibcode":
                            arxiv = target_identity({"bibcode": value}).arxiv_ids
                            matched = key in identity.bibcodes or bool(
                                arxiv & identity.arxiv_ids
                            )
                        elif kind == "doi":
                            matched = key in doc_dois
                        else:
                            matched = key in identity.arxiv_ids
                        if matched:
                            matches.setdefault((kind, key), []).append(document)

            try:
                query(batch, ads_documents_callback=collect)
            except _CancelledSearch:
                result.cancelled = True
                return None
            except (ADSError, OSError, ValueError) as exc:
                result.failures.append(f"ADS recheck stopped: {exc}")
                return None
    return matches


def _candidate(
    snapshot: _LookupItem, documents: dict[tuple[str, str], list[dict[str, Any]]]
) -> dict[str, Any] | None:
    """Return one ADS record across all saved identifiers, or refuse ambiguity."""
    matches: dict[str, dict[str, Any]] = {}
    keys = [("bibcode", code.casefold()) for code in snapshot.bibcodes]
    keys.extend(("doi", doi) for doi in snapshot.dois)
    if not snapshot.dois:
        keys.extend(("arxiv", value) for value in snapshot.arxiv_ids)
    without_bibcode = []
    for identifier_key in keys:
        for document in documents.get(identifier_key, []):
            bibcode = _first(document.get("bibcode"))
            if not bibcode:
                without_bibcode.append(document)
                continue
            identity = target_identity(document)
            key = bibcode.casefold()
            previous = matches.get(key)
            if previous is not None and target_identity(previous) != identity:
                raise ValueError("ADS returned inconsistent versions of one candidate.")
            matches[key] = document
    saved_code = snapshot.bibcode.casefold()
    if snapshot.record.get("status") == "resolved" and saved_code in matches:
        owners = {
            code
            for code, document in matches.items()
            if saved_code in target_identity(document).bibcodes
        }
        unknown_owner = any(
            saved_code in target_identity(document).bibcodes
            for document in without_bibcode
        )
        if owners == {saved_code} and not unknown_owner:
            return matches[saved_code]
    if without_bibcode:
        raise ValueError("ADS returned a matching record without a bibcode.")
    if len(matches) > 1:
        raise ValueError("ADS returned ambiguous matches.")
    return next(iter(matches.values()), None)


def _recheck_external(
    state: CentralPdfRegistryState,
    lookup: _LookupItem,
    settings: IdentifySettings,
    result: RecheckResult,
    *,
    apply: bool,
    cancelled: Callable[[], bool] | None,
) -> bool:
    """Identify or revalidate one record through enabled DOI providers."""
    if not lookup.dois or not any(
        provider is not DoiProvider.ADS for provider in settings.providers
    ):
        return False
    if _saved_proof_current(lookup.record):
        return False
    name = str(lookup.record["current_path"])
    current = state.registry.catalog.get_file(state.directory, name)
    if current != lookup.record:
        raise ValueError("The catalog changed during the lookup.")
    snapshot = _snapshot(state, current)
    if _eligible(current):
        try:
            verify_record_identity(
                snapshot.path, current, sha256=snapshot.digest, state=state
            )
        except UnverifiedIdentityError:
            pass
        else:
            return False
    candidate = _lookup_verified_external(
        snapshot.path, snapshot.digest, sorted(lookup.dois), settings.providers
    )
    if candidate is None:
        return False
    if _stop_requested(cancelled):
        raise InterruptedError("Identification was cancelled.")
    if not _unchanged(state, snapshot):
        raise ValueError("The catalog changed during the lookup.")
    _save_revalidated_external(
        state,
        snapshot.path,
        snapshot.record,
        snapshot.document,
        candidate,
        snapshot.digest,
        apply=apply,
    )
    result.planned_updates += 1
    if apply:
        result.updated += 1
        result.activities.append(f"{name}: identified with a DOI provider.")
    return True


def _proposed_metadata(
    original: Mapping[str, Any],
    document: Mapping[str, Any],
    path: Path,
    digest: str,
    *,
    allow_missing_primary: bool = False,
    filename_bibcode: str | None = None,
) -> dict[str, Any]:
    """Preserve saved fields and prove the new bibcode and previous aliases."""
    old = target_identity(original)
    authority = target_identity(document)
    if old.claims_outside(authority):
        without_primary = dict(original)
        without_primary.pop("bibcode", None)
        if not allow_missing_primary or target_identity(without_primary).claims_outside(
            authority
        ):
            raise ValueError("The ADS record does not confirm every saved identifier.")
    fresh = assert_mutation_allowed(
        path,
        document,
        expected_sha256=digest,
        provider_document=document,
        filename_bibcode=filename_bibcode,
        allow_filename_association=filename_bibcode is not None,
    )
    if fresh.get("status") not in {"verified", "filename_associated"}:
        raise ValueError("The ADS candidate has no valid identity proof.")
    bibcode = _first(document.get("bibcode"))
    previous_bibcode = _first(original.get("bibcode"))
    if not bibcode:
        raise ValueError("The ADS candidate has no bibcode.")
    if _locked(original, "bibcode") and previous_bibcode != bibcode:
        raise ValueError("The bibcode is locked.")
    merged = merge_ads_metadata(original, document)
    merged["bibcode"] = bibcode
    if (
        previous_bibcode
        and previous_bibcode.casefold() != bibcode.casefold()
        and previous_bibcode.casefold() not in authority.bibcodes
    ):
        history = merged.get(PREVIOUS_IDENTITIES_KEY, [])
        if not isinstance(history, list):
            raise ValueError("The saved identity history is invalid.")
        merged[PREVIOUS_IDENTITIES_KEY] = [
            *copy.deepcopy(history),
            {
                "bibcode": previous_bibcode,
                "proof": copy.deepcopy(original.get(IDENTITY_KEY)),
            },
        ]
    current = target_identity(merged)
    missing_dois = old.dois - current.dois
    missing_arxiv = old.arxiv_ids - current.arxiv_ids
    missing_codes = old.bibcodes - current.bibcodes
    if allow_missing_primary and previous_bibcode.casefold() not in authority.bibcodes:
        missing_codes -= {previous_bibcode.casefold()}

    def append_values(name: str, values: list[str]) -> None:
        if not values:
            return
        if _locked(original, name):
            raise ValueError(
                f"The {name} field is locked and cannot retain prior identifiers."
            )
        present = merged.get(name)
        items = (
            list(present)
            if isinstance(present, (list, tuple))
            else ([present] if present else [])
        )
        existing = {str(item).casefold() for item in items}
        items.extend(value for value in values if value.casefold() not in existing)
        merged[name] = items

    if not _locked(original, "identifier"):
        code_names = {code.casefold(): code for code in _bibcodes(original)}
        append_values(
            "identifier",
            sorted(missing_dois)
            + [f"arXiv:{value}" for value in sorted(missing_arxiv)]
            + [code_names.get(value, value) for value in sorted(missing_codes)],
        )
    else:
        append_values("doi", sorted(missing_dois))
        append_values("arxiv", [f"arXiv:{value}" for value in sorted(missing_arxiv)])
        append_values("alternate_bibcode", sorted(missing_codes))
    if "status" in merged:
        merged["status"] = "resolved"
    merged[METADATA_SHA256_KEY] = digest
    merged["sha256"] = digest
    merged[IDENTITY_KEY] = assert_mutation_allowed(
        path,
        merged,
        expected_sha256=digest,
        provider_document=document,
        filename_bibcode=filename_bibcode,
        allow_filename_association=filename_bibcode is not None,
    )
    if merged[IDENTITY_KEY].get("status") not in {"verified", "filename_associated"}:
        raise ValueError("The merged metadata has no valid identity proof.")
    previous = original.get(IDENTITY_KEY)
    if isinstance(previous, Mapping) and previous != merged[IDENTITY_KEY]:
        history = merged.get("_adsfolio_identity_history", [])
        if not isinstance(history, list):
            raise ValueError("The saved identity history is invalid.")
        merged["_adsfolio_identity_history"] = [
            *history,
            copy.deepcopy(previous),
        ]
    merged.pop(METADATA_STALE_KEY, None)
    merged.pop(PREVIOUS_RESOLUTION_KEY, None)
    merged[WATCH_PENDING_KEY] = False
    return merged


def _same_verified_doi(snapshot: _Snapshot, document: Mapping[str, Any]) -> bool:
    """Check an old verified association against the PDF's own DOI and ADS."""
    proof = snapshot.record["metadata"].get(IDENTITY_KEY)
    if (
        not isinstance(proof, Mapping)
        or proof.get("status") != "verified"
        or proof.get("sha256") != snapshot.digest
        or proof.get("identity")
        != target_identity(snapshot.record["metadata"]).fingerprint
    ):
        return False
    evidence = extract_pdf_own_identity(snapshot.path)
    if evidence.blocking_errors or evidence.sha256 != snapshot.digest:
        return False
    old = target_identity(snapshot.record["metadata"])
    authority = target_identity(document)
    return bool(evidence.dois & old.dois & authority.dois)


def _unchanged(state: CentralPdfRegistryState, snapshot: _Snapshot) -> bool:
    catalog = state.registry.catalog
    record = catalog.get_file(
        Path(snapshot.record["root"]), snapshot.record["current_path"]
    )
    document = (
        catalog.get_document(snapshot.document["id"])
        if snapshot.document is not None
        else None
    )
    locations = tuple(
        catalog.query_files(attachment_id=snapshot.record["attachment_id"])
    )
    return (
        record == snapshot.record
        and document == snapshot.document
        and locations == snapshot.locations
    )


def _with_pending(metadata: dict[str, Any], snapshot: _Snapshot) -> dict[str, Any]:
    """Record every location that needs the new name in its own root."""
    pending = metadata.get(PENDING_RENAMES_KEY)
    old_ids = pending.get("locations", []) if isinstance(pending, Mapping) else []
    if not isinstance(old_ids, list) or any(
        not isinstance(value, int) for value in old_ids
    ):
        raise ValueError("The saved rename queue is invalid.")
    locations = sorted(
        set(old_ids) | {item["location_id"] for item in snapshot.locations}
    )
    updated = copy.deepcopy(metadata)
    updated[PENDING_RENAMES_KEY] = {
        "locations": locations,
        "bibcode": _first(metadata.get("bibcode")),
        "sha256": snapshot.digest,
        "identity": target_identity(metadata).fingerprint,
    }
    return updated


def _commit(
    state: CentralPdfRegistryState,
    snapshot: _Snapshot,
    document: dict[str, Any],
    metadata: dict[str, Any],
    cancelled: Callable[[], bool] | None,
    in_scope: Callable[[Mapping[str, Any]], bool],
    *,
    queue_rename: bool = True,
    filename_based: bool = False,
) -> list[int]:
    """Update one document and all affected attachments in one transaction."""
    catalog = state.registry.catalog
    conn = catalog._connection
    if conn.in_transaction:
        raise ValueError("The catalog already has an active transaction.")
    conn.execute("BEGIN IMMEDIATE")
    try:
        if _stop_requested(cancelled):
            raise InterruptedError("The recheck was cancelled before the update.")
        if not _unchanged(state, snapshot):
            raise ValueError("The catalog changed during the ADS lookup.")
        digest = snapshot.digest
        for location in snapshot.locations:
            root = Path(location["root"]).resolve()
            path = _safe_path(root, str(location["current_path"]))
            evidence = extract_pdf_own_identity(path)
            if evidence.sha256 != digest or evidence.blocking_errors:
                raise ValueError("The PDF changed during the ADS lookup.")
        assert_mutation_allowed(
            snapshot.path, metadata, expected_sha256=digest, provider_document=document
        )
        code = _first(document.get("bibcode"))
        old_doc_code = (
            _first(snapshot.document.get("bibcode")) if snapshot.document else ""
        )
        if old_doc_code and old_doc_code.casefold() not in {
            snapshot.bibcode.casefold(),
            code.casefold(),
        }:
            raise ValueError("The canonical document has a conflicting bibcode.")
        doc_id = snapshot.document["id"] if snapshot.document is not None else None
        existing = conn.execute(
            "SELECT id FROM documents WHERE lower(bibcode) = lower(?)",
            (code,),
        ).fetchone()
        if (
            existing is not None
            and doc_id is not None
            and int(existing["id"]) != doc_id
        ):
            raise ValueError("The ADS bibcode belongs to another canonical document.")
        updates: list[tuple[_Snapshot, dict[str, Any]]] = [(snapshot, metadata)]
        if snapshot.document is not None and old_doc_code.casefold() != code.casefold():
            if REFERENCE_METADATA_KEY in snapshot.document["metadata"]:
                raise ValueError("The canonical document is a managed local reference.")
            siblings = conn.execute(
                "SELECT id FROM attachments WHERE document_id = ? AND id != ?",
                (doc_id, snapshot.record["attachment_id"]),
            ).fetchall()
            if filename_based and siblings:
                raise ValueError(
                    "The filename association would change another attachment."
                )
            for sibling in siblings:
                if _stop_requested(cancelled):
                    raise InterruptedError(
                        "The recheck was cancelled before the update."
                    )
                locations = catalog.query_files(attachment_id=int(sibling["id"]))
                if not locations:
                    raise ValueError(
                        "A shared attachment has no current PDF to verify."
                    )
                if any(
                    Path(location["root"]).resolve() != Path(state.directory).resolve()
                    for location in locations
                ):
                    raise ValueError(
                        "A shared attachment belongs to another root with unknown Identify scope."
                    )
                other = _snapshot(state, locations[0])
                fresh = _proposed_metadata(
                    other.record["metadata"],
                    document,
                    other.path,
                    other.digest,
                    allow_missing_primary=_same_verified_doi(other, document),
                )
                if not (
                    other.bibcode.casefold() == code.casefold()
                    and other.record["status"] == "resolved"
                ):
                    updates.append((other, fresh))
            doc_metadata = _proposed_metadata(
                snapshot.document["metadata"],
                document,
                snapshot.path,
                digest,
                allow_missing_primary=_same_verified_doi(snapshot, document),
                filename_bibcode=(
                    metadata.get(IDENTITY_KEY, {}).get("source_bibcode")
                    if filename_based
                    else None
                ),
            )
        for item, _ in updates:
            for location in item.locations:
                if Path(location["root"]).resolve() == Path(
                    state.directory
                ).resolve() and not in_scope(location):
                    raise ValueError(
                        "A shared PDF location is excluded by this root's settings."
                    )
        now = catalog._utc_timestamp()
        if snapshot.document is not None and old_doc_code.casefold() != code.casefold():
            conn.execute(
                "UPDATE documents SET bibcode = ?, metadata_json = ?, updated_at = ? WHERE id = ?",
                (code, json.dumps(doc_metadata, sort_keys=True), now, doc_id),
            )
            catalog._set_document_identity(doc_id, doc_metadata)
        elif snapshot.document is not None:
            if target_identity(snapshot.document["metadata"]).claims_outside(
                target_identity(document)
            ):
                raise ValueError("The canonical document has conflicting identifiers.")
        elif existing is not None:
            doc_id = int(existing["id"])
            existing_doc = catalog.get_document(doc_id)
            if existing_doc is None or target_identity(
                existing_doc["metadata"]
            ).claims_outside(target_identity(document)):
                raise ValueError(
                    "The existing ADS document has conflicting identifiers."
                )
        else:
            cursor = conn.execute(
                "INSERT INTO documents (bibcode, metadata_json, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (code, json.dumps(metadata, sort_keys=True), now, now),
            )
            doc_id = int(cursor.lastrowid)
            catalog._set_document_identity(doc_id, metadata)
        updated_ids = []
        for item, item_metadata in updates:
            for location in item.locations:
                version_id = location["version_id"]
                if version_id is None:
                    cursor = conn.execute(
                        "INSERT INTO content_versions (attachment_id, sha256, size_bytes, observed_at) VALUES (?, ?, ?, ?)",
                        (
                            item.record["attachment_id"],
                            item.digest,
                            location["size_bytes"],
                            now,
                        ),
                    )
                    conn.execute(
                        "UPDATE file_locations SET current_version_id = ? WHERE id = ? AND current_version_id IS NULL",
                        (cursor.lastrowid, location["location_id"]),
                    )
                elif not location["sha256"]:
                    conn.execute(
                        "UPDATE content_versions SET sha256 = ? WHERE id = ? AND sha256 IS NULL",
                        (item.digest, version_id),
                    )
            if queue_rename:
                item_metadata = _with_pending(item_metadata, item)
            cursor = conn.execute(
                "UPDATE attachments SET document_id = ?, status = 'resolved', metadata_json = ?, updated_at = ? "
                "WHERE id = ? AND document_id IS ?",
                (
                    doc_id,
                    json.dumps(item_metadata, sort_keys=True),
                    now,
                    item.record["attachment_id"],
                    item.record["document_id"],
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("The attachment changed during the update.")
            updated_ids.append(item.record["attachment_id"])
        if _stop_requested(cancelled):
            raise InterruptedError("The recheck was cancelled before the update.")
        for item, _ in updates:
            for location in item.locations:
                root = Path(location["root"]).resolve()
                path = _safe_path(root, str(location["current_path"]))
                evidence = extract_pdf_own_identity(path)
                if evidence.sha256 != item.digest or evidence.blocking_errors:
                    raise ValueError("The PDF changed during the ADS update.")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return updated_ids


def _rename(
    state: CentralPdfRegistryState,
    record: dict[str, Any],
    settings: RenameSettings,
    *,
    client: _GuardedClient,
    apply: bool,
    cancelled: Callable[[], bool] | None,
) -> bool:
    """Rename one location with an exclusive link and guarded catalog update."""
    root = Path(state.directory).resolve()
    catalog = state.registry.catalog
    conn = catalog._connection
    if conn.in_transaction:
        raise ValueError("The catalog already has an active transaction.")
    source = _safe_path(root, str(record["current_path"]))
    metadata = CentralPdfRegistryState._record_view(record)
    renamer = PdfRenamer(client=client)
    target = renamer.rename_tracked_file(
        state, source, metadata, dry_run=True, **settings.to_mapping()
    )
    moving = target != source
    if not apply:
        if not moving:
            return False
        try:
            target.lstat()
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError(f"The destination exists: {target.name}")
        occupied = state.registry.catalog.get_file(
            root, target.relative_to(root).as_posix()
        )
        if occupied is not None and occupied["missing_at"] is None:
            raise FileExistsError(f"The destination is tracked: {target.name}")
        return True
    relative_target = target.relative_to(root).as_posix()
    conn.execute("BEGIN IMMEDIATE")
    linked = False
    unlinked = False
    try:
        if _stop_requested(cancelled):
            raise InterruptedError("The rename was cancelled.")
        if catalog.get_file(root, record["current_path"]) != record:
            raise ValueError("The catalog changed before rename.")
        if moving:
            occupied = catalog.get_file(root, relative_target)
            if occupied is not None and occupied["missing_at"] is None:
                raise FileExistsError(f"The destination is tracked: {target.name}")
        proof = assert_mutation_allowed(
            source, metadata, expected_sha256=record["sha256"]
        )
        if proof.get("status") != "verified":
            raise ValueError("The PDF identity is no longer verified.")
        if moving:
            before = source.stat()
            os.link(source, target, follow_symlinks=False)
            linked = True
            linked_stat = target.stat()
            current = source.stat()

            def facts(stat: os.stat_result) -> tuple[int, int, int, int]:
                return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)

            if facts(before) != facts(current) or facts(before) != facts(linked_stat):
                raise ValueError("The PDF changed before rename.")
            assert_mutation_allowed(target, metadata, expected_sha256=record["sha256"])
            if facts(before) != facts(source.stat()):
                raise ValueError("The PDF changed before rename.")
            source.unlink()
            unlinked = True
            cursor = conn.execute(
                "UPDATE file_locations SET relative_path = ?, last_seen_at = ? "
                "WHERE id = ? AND attachment_id = ? AND relative_path = ? "
                "AND current_version_id = ? AND missing_at IS NULL",
                (
                    relative_target,
                    catalog._utc_timestamp(),
                    record["location_id"],
                    record["attachment_id"],
                    record["current_path"],
                    record["version_id"],
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("The catalog location changed during rename.")
        saved = copy.deepcopy(record["metadata"])
        pending = saved.get(PENDING_RENAMES_KEY)
        if not isinstance(pending, dict) or record["location_id"] not in pending.get(
            "locations", []
        ):
            raise ValueError("The pending rename changed before completion.")
        remaining = [
            value for value in pending["locations"] if value != record["location_id"]
        ]
        if remaining:
            pending["locations"] = remaining
        else:
            saved.pop(PENDING_RENAMES_KEY)
        conn.execute(
            "UPDATE attachments SET metadata_json = ?, updated_at = ? WHERE id = ?",
            (
                json.dumps(saved, sort_keys=True),
                catalog._utc_timestamp(),
                record["attachment_id"],
            ),
        )
        conn.commit()
        return moving
    except BaseException as exc:
        conn.rollback()
        if linked:
            try:
                target_stat = target.stat()
                if (target_stat.st_dev, target_stat.st_ino) != (
                    before.st_dev,
                    before.st_ino,
                ):
                    raise OSError("The rename destination changed during rollback.")
                if unlinked:
                    os.link(target, source, follow_symlinks=False)
                target.unlink()
            except OSError as rollback_error:
                raise RuntimeError(
                    f"Rename failed and filesystem rollback was incomplete: {rollback_error}"
                ) from exc
        raise


def _process_pending(
    state: CentralPdfRegistryState,
    settings: RenameSettings,
    result: RecheckResult,
    in_scope: Callable[[Mapping[str, Any]], bool],
    *,
    client: _GuardedClient,
    apply: bool,
    cancelled: Callable[[], bool] | None,
    attachment_ids: set[int] | None = None,
) -> None:
    """Finish pending renames in this root with this root's settings."""
    catalog = state.registry.catalog
    records = catalog.query_files(root=state.directory)
    for initial in records:
        if (
            attachment_ids is not None
            and initial["attachment_id"] not in attachment_ids
        ):
            continue
        if not in_scope(initial):
            continue
        pending = initial["metadata"].get(PENDING_RENAMES_KEY)
        if pending is None:
            continue
        if not isinstance(pending, Mapping) or not isinstance(
            pending.get("locations"), list
        ):
            result.failures.append(
                f"{initial['current_path']}: pending rename data is invalid."
            )
            continue
        if initial["location_id"] not in pending["locations"]:
            continue
        if _stop_requested(cancelled):
            result.cancelled = True
            return
        current = catalog.query_files(
            root=state.directory, location_id=initial["location_id"]
        )
        if not current:
            result.failures.append(
                f"{initial['current_path']}: pending location disappeared."
            )
            continue
        record = current[0]
        pending = record["metadata"].get(PENDING_RENAMES_KEY)
        if not isinstance(pending, Mapping) or (
            pending.get("sha256") != record["sha256"]
            or pending.get("identity")
            != target_identity(record["metadata"]).fingerprint
            or str(pending.get("bibcode") or "").casefold()
            != _first(record["bibcode"]).casefold()
        ):
            result.failures.append(
                f"{record['current_path']}: pending rename identity changed."
            )
            continue
        try:
            if _rename(
                state, record, settings, client=client, apply=apply, cancelled=cancelled
            ):
                result.planned_renames += 1
                if apply:
                    result.renamed += 1
                    moved = catalog.query_files(
                        root=state.directory, location_id=record["location_id"]
                    )
                    if moved:
                        result.activities.append(
                            f"{record['current_path']}: renamed to {moved[0]['current_path']}."
                        )
            elif apply:
                result.activities.append(
                    f"{record['current_path']}: filename already matches."
                )
        except InterruptedError:
            result.cancelled = True
            return
        except _CancelledSearch:
            result.cancelled = True
            return
        except (
            ADSError,
            IdentityValidationError,
            OSError,
            ValueError,
            sqlite3.Error,
            RuntimeError,
        ) as exc:
            result.failures.append(
                f"{record['current_path']}: pending rename failed: {exc}"
            )


def recheck_existing(
    state: CentralPdfRegistryState,
    client: ADSClient | None,
    rename_settings: RenameSettings,
    *,
    ads_enabled: bool = True,
    apply: bool = True,
    cancelled: Callable[[], bool] | None = None,
    identify_settings: IdentifySettings | None = None,
    recursive: bool | None = None,
) -> RecheckResult:
    """Recheck tracked identifiers with the enabled providers.

    Parameters
    ----------
    state
        Open catalog state for the library root. The caller owns its connection.
    client
        ADS client used for batched searches, or `None` without an ADS client.
    rename_settings
        Effective saved Rename settings for this root.
    ads_enabled
        Whether this operation may contact ADS.
    apply
        Apply verified updates and renames. False checks and plans without writes.
    cancelled
        Optional cooperative cancellation check.
    identify_settings
        Effective Identify settings for this root. The default uses saved settings.
    recursive
        Whether to include subdirectories. The default uses Identify settings.

    Returns
    -------
    RecheckResult
        Counts and reasons for skipped updates or renames.

    Notes
    -----
    The caller controls scheduling and provider permission. A rename failure
    retains the verified metadata update and pending location. Each root must
    call this function with its own settings to finish its pending locations.
    PDF bytes are not modified. Filesystem writers are not locked, so checks
    cannot eliminate every external filesystem race.
    """
    result = RecheckResult()
    if _stop_requested(cancelled):
        result.cancelled = True
        return result
    connection = state.registry.catalog._connection
    if connection.in_transaction:
        result.failures.append("The catalog already has an active transaction.")
        return result
    settings = (
        identify_settings or state.identify_settings or Config.load_identify_settings()
    )
    run_ads = (
        ads_enabled and DoiProvider.ADS in settings.providers and client is not None
    )
    if ads_enabled and DoiProvider.ADS in settings.providers and client is None:
        result.failures.append("ADS client is unavailable.")
    if not run_ads and not any(
        provider is not DoiProvider.ADS for provider in settings.providers
    ):
        return result
    try:
        policy = WatchIdentifyPolicy.from_settings(state.directory, settings)
    except ValueError as exc:
        result.failures.append(f"Invalid Identify scope: {exc}")
        return result
    recurse = settings.recursive if recursive is None else recursive

    def in_scope(record: Mapping[str, Any]) -> bool:
        relative = Path(str(record["current_path"]))
        if not recurse and len(relative.parts) > 1:
            return False
        return not policy.excludes(Path(state.directory) / relative)

    guarded_client = (
        _GuardedClient(client, cancelled, lambda: connection.in_transaction)
        if run_ads
        else None
    )
    if guarded_client is not None:
        _process_pending(
            state,
            rename_settings,
            result,
            in_scope,
            client=guarded_client,
            apply=apply,
            cancelled=cancelled,
        )
    if result.cancelled:
        return result
    lookups: list[_LookupItem] = []
    seen: set[int] = set()
    identifier = PdfIdentifier(guarded_client)
    for record in state.registry.iter_files():
        if _stop_requested(cancelled):
            result.cancelled = True
            return result
        attachment_id = int(record["attachment_id"])
        if attachment_id in seen or not in_scope(record):
            continue
        filename_bibcode = None
        if (
            run_ads
            and record.get("status") != "resolved"
            and IdentifyStrategy.FILENAME_BIBCODE in settings.strategies
        ):
            filename_bibcode = identifier.extract_bibcode_from_filename(
                Path(str(record["current_path"])).name
            )
        metadata = record["metadata"]
        identity = target_identity(metadata)
        metadata_doi = None
        if (
            not _eligible(record)
            and IdentifyStrategy.PDF_METADATA_DOI in settings.strategies
        ):
            try:
                path = _safe_path(
                    Path(state.directory).resolve(), str(record["current_path"])
                )
                metadata_doi = _normalize_doi(
                    identifier.extract_doi_from_metadata(path) or ""
                )
            except (OSError, ValueError) as exc:
                result.failures.append(f"{record['current_path']}: {exc}")
                continue
        if not _eligible(record) and not filename_bibcode and not metadata_doi:
            continue
        if not run_ads and not (identity.dois or metadata_doi):
            continue
        if not run_ads and _saved_proof_current(record):
            continue
        seen.add(attachment_id)
        result.checked += 1
        lookups.append(
            _LookupItem(
                record=copy.deepcopy(record),
                bibcode=_first(metadata.get("bibcode")),
                bibcodes=_bibcodes(metadata)
                | (frozenset({filename_bibcode}) if filename_bibcode else frozenset()),
                dois=identity.dois
                | (frozenset({metadata_doi}) if metadata_doi else frozenset()),
                arxiv_ids=identity.arxiv_ids,
                filename_bibcode=filename_bibcode,
            )
        )
    if not lookups:
        return result
    attempted_prefix: set[int] = set()
    prefix_failures: dict[int, str] = {}
    if run_ads:
        ads_position = settings.providers.index(DoiProvider.ADS)
        prefix = settings.providers[:ads_position]
        if prefix:
            prefix_settings = settings.with_overrides(providers=prefix)
            remaining = []
            for lookup in lookups:
                if _stop_requested(cancelled):
                    result.cancelled = True
                    return result
                filename_first = bool(
                    lookup.filename_bibcode
                    and IdentifyStrategy.FILENAME_BIBCODE in settings.strategies
                    and (
                        IdentifyStrategy.PDF_METADATA_DOI not in settings.strategies
                        or settings.strategies.index(IdentifyStrategy.FILENAME_BIBCODE)
                        < settings.strategies.index(IdentifyStrategy.PDF_METADATA_DOI)
                    )
                )
                if filename_first or _saved_proof_current(lookup.record):
                    remaining.append(lookup)
                    continue
                location_id = int(lookup.record["location_id"])
                attempted_prefix.add(location_id)
                try:
                    handled = _recheck_external(
                        state,
                        lookup,
                        prefix_settings,
                        result,
                        apply=apply,
                        cancelled=cancelled,
                    )
                except InterruptedError:
                    result.cancelled = True
                    return result
                except (IdentityValidationError, ProviderError) as exc:
                    prefix_failures[location_id] = str(exc)
                    handled = False
                except (OSError, ValueError, sqlite3.Error, RuntimeError) as exc:
                    result.failures.append(f"{lookup.record['current_path']}: {exc}")
                    continue
                if not handled:
                    remaining.append(lookup)
            lookups = remaining
            if not lookups:
                return result
    documents = _search(identifier, lookups, result, cancelled) if run_ads else {}
    ads_search_succeeded = documents is not None and run_ads
    if documents is None:
        if result.cancelled:
            return result
        documents = {}
    own_updates: set[int] = set()
    for lookup in lookups:
        if _stop_requested(cancelled):
            result.cancelled = True
            break
        name = str(lookup.record["current_path"])
        stale = bool(
            lookup.record.get(METADATA_STALE_KEY)
            or lookup.record["metadata"].get(METADATA_STALE_KEY)
        )
        location_id = int(lookup.record["location_id"])
        fallback_providers = (
            settings.providers[settings.providers.index(DoiProvider.ADS) + 1 :]
            if run_ads and location_id in attempted_prefix
            else tuple(p for p in settings.providers if p is not DoiProvider.ADS)
        )

        def try_external_fallback(
            lookup: _LookupItem = lookup,
            stale: bool = stale,
            fallback_providers: tuple[DoiProvider, ...] = fallback_providers,
        ) -> bool:
            if stale or not fallback_providers:
                return False
            fallback_settings = settings.with_overrides(providers=fallback_providers)
            return _recheck_external(
                state,
                lookup,
                fallback_settings,
                result,
                apply=apply,
                cancelled=cancelled,
            )

        try:
            try:
                candidate = _candidate(lookup, documents)
            except ValueError:
                if try_external_fallback():
                    continue
                raise
            if candidate is None:
                if try_external_fallback():
                    continue
                if location_id in prefix_failures:
                    result.failures.append(f"{name}: {prefix_failures[location_id]}")
                if stale and ads_search_succeeded:
                    source = (
                        "saved identifiers"
                        if _eligible(lookup.record)
                        else "filename bibcode"
                    )
                    result.failures.append(
                        f"{name}: ADS found no match for the {source}. "
                        "The PDF and its metadata remain unchanged."
                    )
                elif lookup.filename_bibcode and ads_search_succeeded:
                    result.failures.append(
                        f"{name}: ADS found no record for filename bibcode "
                        f"{lookup.filename_bibcode}. Use Update by ID or Enter Details."
                    )
                continue
            new_code = _first(candidate.get("bibcode"))
            same_resolved_code = bool(
                lookup.bibcode
                and lookup.record["status"] == "resolved"
                and not lookup.record.get(METADATA_STALE_KEY)
                and not lookup.record["metadata"].get(METADATA_STALE_KEY)
                and lookup.bibcode.casefold() == new_code.casefold()
            )
            if same_resolved_code and _saved_proof_current(lookup.record):
                continue
            current = state.registry.catalog.get_file(state.directory, name)
            if current != lookup.record:
                if (
                    current is not None
                    and current["attachment_id"] in own_updates
                    and _first(current["bibcode"]).casefold() == new_code.casefold()
                ):
                    continue
                raise ValueError("The catalog changed during the ADS lookup.")
            snapshot = _snapshot(state, current, allow_stale=True)
            matched_filename = (
                lookup.filename_bibcode
                if lookup.filename_bibcode
                and lookup.filename_bibcode.casefold()
                in target_identity(candidate).bibcodes
                else None
            )
            filename_only = bool(matched_filename)
            revalidating = bool(
                lookup.record.get(METADATA_STALE_KEY)
                or lookup.record["metadata"].get(METADATA_STALE_KEY)
            )
            if (filename_only or revalidating) and any(
                Path(location["root"]).resolve() != Path(state.directory).resolve()
                for location in snapshot.locations
            ):
                raise ValueError(
                    "The PDF has a location in another library with unknown Identify settings."
                )
            try:
                metadata = _proposed_metadata(
                    snapshot.record["metadata"],
                    candidate,
                    snapshot.path,
                    snapshot.digest,
                    allow_missing_primary=_same_verified_doi(snapshot, candidate),
                    filename_bibcode=matched_filename,
                )
            except IdentityValidationError:
                if try_external_fallback():
                    continue
                raise
            if not _unchanged(state, snapshot):
                raise ValueError("The catalog changed during the ADS lookup.")
            if apply:
                ids = _commit(
                    state,
                    snapshot,
                    candidate,
                    metadata,
                    cancelled,
                    in_scope,
                    queue_rename=not filename_only and not same_resolved_code,
                    filename_based=filename_only,
                )
                own_updates.update(ids)
                result.updated += len(ids)
                result.planned_updates += len(ids)
                action = (
                    "identified"
                    if filename_only
                    else "reverified"
                    if same_resolved_code
                    else "verified and updated"
                )
                basis = (
                    " using the filename bibcode only"
                    if metadata[IDENTITY_KEY].get("status") == "filename_associated"
                    else ""
                )
                result.activities.append(f"{name}: {action} as {new_code}{basis}.")
                if not filename_only and not same_resolved_code:
                    _process_pending(
                        state,
                        rename_settings,
                        result,
                        in_scope,
                        client=guarded_client,
                        apply=True,
                        cancelled=cancelled,
                        attachment_ids=set(ids),
                    )
                if result.cancelled:
                    result.failures.append(
                        f"{name}: metadata updated; pending renames remain."
                    )
                    break
            else:
                result.planned_updates += 1
                if filename_only or same_resolved_code:
                    continue
                for location in snapshot.locations:
                    if Path(location["root"]).resolve() != Path(
                        state.directory
                    ).resolve() or not in_scope(location):
                        continue
                    record = {
                        **location,
                        "metadata": metadata,
                        "status": "resolved",
                        "sha256": snapshot.digest,
                    }
                    try:
                        if _rename(
                            state,
                            record,
                            rename_settings,
                            client=guarded_client,
                            apply=False,
                            cancelled=cancelled,
                        ):
                            result.planned_renames += 1
                    except _CancelledSearch:
                        result.cancelled = True
                        break
                    except (
                        ADSError,
                        IdentityValidationError,
                        OSError,
                        ValueError,
                        sqlite3.Error,
                        RuntimeError,
                    ) as exc:
                        result.failures.append(
                            f"{name}: rename cannot be planned: {exc}"
                        )
        except InterruptedError:
            result.cancelled = True
            if apply and result.updated:
                result.failures.append(f"{name}: update or rename was cancelled.")
            break
        except (
            IdentityValidationError,
            ProviderError,
            OSError,
            TypeError,
            ValueError,
            sqlite3.Error,
            RuntimeError,
        ) as exc:
            result.failures.append(f"{name}: {exc}")
    return result
