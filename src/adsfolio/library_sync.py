"""Qt-free local selection and ADS library synchronization helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import CancelledError
from dataclasses import dataclass
from typing import TYPE_CHECKING

import requests

from .api.export import ExportAPI
from .api.libraries import Library
from .config import ADSError
from .pdf.cli_common import is_resolved_record

if TYPE_CHECKING:
    from .client import ADSClient

__all__ = [
    "AddDocumentsResult",
    "AdsLibraryService",
    "BibcodeSelection",
    "LibraryDestination",
    "LibraryExportResult",
    "collect_identified_bibcodes",
]


@dataclass(frozen=True)
class BibcodeSelection:
    """Stable local Bibcode selection and exclusion counts."""

    bibcodes: tuple[str, ...]
    """Unique eligible Bibcodes in first-seen order."""

    source_files: int
    """Number of records considered."""

    ineligible_files: int
    """Number of records that were not eligible for synchronization."""

    local_duplicate_files: int
    """Number of eligible records whose Bibcode was already seen locally."""


@dataclass(frozen=True)
class LibraryDestination:
    """Immutable ADS library metadata used by callers and UI code."""

    id: str
    """ADS library identifier."""

    name: str
    """Human-readable ADS library name."""

    permission: str
    """Permission reported by ADS for the current user."""

    public: bool
    """Whether the ADS library is public."""

    num_documents: int
    """Number of documents reported by ADS."""

    @property
    def writable(self) -> bool:
        """Return whether the current user may add documents."""
        return self.permission in {"owner", "admin", "write"}


@dataclass(frozen=True)
class AddDocumentsResult:
    """Structured outcome for preflight or batched document additions."""

    acknowledged: tuple[str, ...] = ()
    """Bibcodes accepted by completed add requests."""

    already_present: tuple[str, ...] = ()
    """Bibcodes known to be present before an add request."""

    failed: tuple[str, ...] = ()
    """Bibcodes in a request rejected with an ADS-declared error."""

    status_unknown: tuple[str, ...] = ()
    """Bibcodes in a request whose transport outcome is uncertain."""

    unattempted: tuple[str, ...] = ()
    """Bibcodes left out because processing stopped before their request."""

    cancelled: bool = False
    """Whether cancellation prevented the next request from starting."""

    error: Exception | None = None
    """The exception that stopped processing, if any."""


@dataclass(frozen=True)
class LibraryExportResult:
    """Complete remote bibliography, or an explicitly cancelled operation."""

    text: str
    bibcodes: tuple[str, ...]
    cancelled: bool = False


def _text_value(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _first_bibcode(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            bibcode = _text_value(item)
            if bibcode:
                return bibcode
        return ""
    return _text_value(value)


def _record_bibcode(record: Mapping[str, object]) -> str:
    bibcode = _first_bibcode(record.get("bibcode"))
    if bibcode:
        return bibcode

    metadata = record.get("metadata")
    if isinstance(metadata, Mapping):
        return _first_bibcode(metadata.get("bibcode"))
    return ""


def _has_document_id(record: Mapping[str, object]) -> bool:
    if "document_id" not in record:
        return False
    document_id = record["document_id"]
    return document_id is not None and (
        not isinstance(document_id, str) or bool(document_id.strip())
    )


def collect_identified_bibcodes(
    records: Iterable[Mapping[str, object]],
) -> BibcodeSelection:
    """Collect eligible Bibcodes from local identification records.

    Parameters
    ----------
    records : iterable of mapping
        Local records containing status, document identity, and Bibcode data.

    Returns
    -------
    BibcodeSelection
        First-seen unique Bibcodes and counts for all excluded or duplicate
        source records.
    """
    bibcodes: dict[str, None] = {}
    source_files = 0
    ineligible_files = 0
    local_duplicate_files = 0

    for raw_record in records:
        source_files += 1
        if not isinstance(raw_record, Mapping):
            ineligible_files += 1
            continue

        record = dict(raw_record)
        bibcode = _record_bibcode(record)
        if not bibcode or not _has_document_id(record):
            ineligible_files += 1
            continue

        record["bibcode"] = bibcode
        if not is_resolved_record(record):
            ineligible_files += 1
            continue

        if bibcode in bibcodes:
            local_duplicate_files += 1
        else:
            bibcodes[bibcode] = None

    return BibcodeSelection(
        bibcodes=tuple(bibcodes),
        source_files=source_files,
        ineligible_files=ineligible_files,
        local_duplicate_files=local_duplicate_files,
    )


def _normalize_bibcodes(bibcodes: Iterable[str]) -> tuple[str, ...]:
    if isinstance(bibcodes, str):
        bibcodes = (bibcodes,)

    normalized: dict[str, None] = {}
    for bibcode in bibcodes:
        value = bibcode.strip()
        if value:
            normalized.setdefault(value, None)
    return tuple(normalized)


def _destination_from_library(library: Library) -> LibraryDestination:
    return LibraryDestination(
        id=library.id,
        name=library.name,
        permission=library.permission,
        public=library.public,
        num_documents=library.num_documents,
    )


class AdsLibraryService:
    """Coordinate local Bibcodes with the ADS libraries API."""

    MAX_DOCUMENTS_PER_REQUEST = 2000

    def __init__(self, client: ADSClient) -> None:
        """Initialize the service with an existing ADS client.

        Parameters
        ----------
        client : ADSClient
            Client owned by the caller. The service never closes it.
        """
        self._client = client
        self._destinations: tuple[LibraryDestination, ...] | None = None

    def export_library(
        self,
        library_id: str,
        format: str = "bibtex",
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> LibraryExportResult:
        """Read the complete remote library and export its bibliography.

        Parameters
        ----------
        library_id : str
            Accessible remote ADS library, including libraries with read permission.
        format : str, optional
            A format from `ExportAPI.BIBLIOGRAPHY_FORMATS`.
        cancelled : callable, optional
            Check for cancellation between network operations.

        Returns
        -------
        LibraryExportResult
            Full bibliography and sorted Bibcodes, an empty result for an empty
            library, or a cancelled result without partial bibliography text.

        Raises
        ------
        ADSError
            If library pagination is incomplete or export records are missing.
        ValueError
            If the library identifier or format is invalid.
        """
        if not library_id.strip():
            raise ValueError("An ADS library ID is required.")
        if format not in ExportAPI.BIBLIOGRAPHY_FORMATS:
            raise ValueError(f"Unsupported bibliography format: {format}")
        try:
            if cancelled is not None and cancelled():
                raise CancelledError
            library = self._client.libraries.get_library(library_id, rows=1)
            documents: set[str] = set()
            if cancelled is not None and cancelled():
                raise CancelledError
            for bibcode in self._client.libraries.iter_documents(library_id):
                if cancelled is not None and cancelled():
                    raise CancelledError
                documents.add(bibcode)
            if cancelled is not None and cancelled():
                raise CancelledError
            if len(documents) != library.num_documents:
                raise ADSError(
                    "ADS library contents are incomplete or changed during loading; "
                    "refresh the library and try again."
                )
            bibcodes = tuple(sorted(documents))
            if not bibcodes:
                return LibraryExportResult(text="", bibcodes=())
            text = self._client.export.export_complete(
                bibcodes, format=format, cancelled=cancelled
            )
            if cancelled is not None and cancelled():
                raise CancelledError
            return LibraryExportResult(text=text, bibcodes=bibcodes)
        except CancelledError:
            return LibraryExportResult(text="", bibcodes=(), cancelled=True)

    def list_destinations(self) -> tuple[LibraryDestination, ...]:
        """Return ADS libraries in the order supplied by the API."""
        destinations = tuple(
            _destination_from_library(library)
            for library in self._client.libraries.iter_libraries()
        )
        self._destinations = destinations
        return destinations

    def create_destination(
        self,
        name: str,
        description: str,
        public: bool,
    ) -> LibraryDestination:
        """Create an empty ADS library after validating its name.

        Parameters
        ----------
        name : str
            New library name.
        description : str
            New library description.
        public : bool
            Whether the new library should be public.

        Returns
        -------
        LibraryDestination
            Metadata returned by ADS for the newly created library.

        Raises
        ------
        ValueError
            If `name` is empty after trimming.
        """
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("library name must be non-empty.")

        library = self._client.libraries.create_library(
            name=clean_name,
            description=description,
            public=public,
            bibcodes=None,
        )
        destination = _destination_from_library(library)
        if self._destinations is not None:
            self._destinations = (*self._destinations, destination)
        return destination

    def preflight(
        self,
        library_id: str,
        bibcodes: Sequence[str],
    ) -> AddDocumentsResult:
        """Classify requested Bibcodes against the current remote library.

        Missing Bibcodes are returned in `AddDocumentsResult.unattempted`
        because no add request has been made.
        """
        normalized = _normalize_bibcodes(bibcodes)
        return self._preflight_normalized(library_id, normalized)

    def add_documents(
        self,
        library_id: str,
        bibcodes: Sequence[str],
        *,
        already_present: Sequence[str] = (),
        cancelled: Callable[[], bool] = lambda: False,
    ) -> AddDocumentsResult:
        """Add Bibcodes in bounded sequential requests.

        Parameters
        ----------
        library_id : str
            Destination ADS library identifier.
        bibcodes : sequence of str
            Bibcodes to add, normalized and deduplicated in source order.
        already_present : sequence of str, optional
            Bibcodes known to be in the destination already.
        cancelled : callable, optional
            Predicate checked before every add request.

        Returns
        -------
        AddDocumentsResult
            Structured partial outcome for the operation.

        Raises
        ------
        PermissionError
            If a listed destination is not writable.
        """
        normalized = _normalize_bibcodes(bibcodes)
        known_present = _normalize_bibcodes(already_present)
        return self._add_normalized(
            library_id,
            normalized,
            already_present=known_present,
            cancelled=cancelled,
        )

    def reconcile_and_retry(
        self,
        library_id: str,
        bibcodes: Sequence[str],
        *,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> AddDocumentsResult:
        """Reload remote state and retry only Bibcodes still absent."""
        normalized = _normalize_bibcodes(bibcodes)
        if not normalized:
            return AddDocumentsResult()
        if cancelled():
            return AddDocumentsResult(unattempted=normalized, cancelled=True)

        preflight = self._preflight_normalized(library_id, normalized)
        if preflight.error is not None:
            return preflight

        return self._add_normalized(
            library_id,
            normalized,
            already_present=preflight.already_present,
            cancelled=cancelled,
        )

    def _preflight_normalized(
        self,
        library_id: str,
        bibcodes: tuple[str, ...],
    ) -> AddDocumentsResult:
        if not bibcodes:
            return AddDocumentsResult()

        try:
            remote = self._client.libraries.iter_documents(library_id, raw=True)
            remote_set = set(_normalize_bibcodes(remote))
        except (requests.Timeout, requests.ConnectionError) as error:
            return AddDocumentsResult(
                status_unknown=bibcodes,
                error=error,
            )
        except ADSError as error:
            return AddDocumentsResult(
                failed=bibcodes,
                error=error,
            )

        already_present = tuple(
            bibcode for bibcode in bibcodes if bibcode in remote_set
        )
        missing = tuple(bibcode for bibcode in bibcodes if bibcode not in remote_set)
        return AddDocumentsResult(
            already_present=already_present,
            unattempted=missing,
        )

    def _ensure_writable_when_known(self, library_id: str) -> None:
        if self._destinations is None:
            self.list_destinations()

        destination = next(
            (item for item in self._destinations or () if item.id == library_id),
            None,
        )
        if destination is not None and not destination.writable:
            raise PermissionError(
                f"ADS library {library_id!r} is not writable for this user."
            )

    def _add_normalized(
        self,
        library_id: str,
        bibcodes: tuple[str, ...],
        *,
        already_present: tuple[str, ...],
        cancelled: Callable[[], bool],
    ) -> AddDocumentsResult:
        present_set = set(already_present)
        known_present = tuple(bibcode for bibcode in bibcodes if bibcode in present_set)
        pending = tuple(bibcode for bibcode in bibcodes if bibcode not in present_set)
        if not pending:
            return AddDocumentsResult(already_present=known_present)
        if cancelled():
            return AddDocumentsResult(
                already_present=known_present,
                unattempted=pending,
                cancelled=True,
            )

        self._ensure_writable_when_known(library_id)

        acknowledged: list[str] = []
        failed: list[str] = []
        status_unknown: list[str] = []
        unattempted: list[str] = []
        error: Exception | None = None
        was_cancelled = False

        for start in range(0, len(pending), self.MAX_DOCUMENTS_PER_REQUEST):
            if cancelled():
                unattempted.extend(pending[start:])
                was_cancelled = True
                break

            batch = list(pending[start : start + self.MAX_DOCUMENTS_PER_REQUEST])
            try:
                self._client.libraries.add_documents(library_id, batch)
            except (requests.Timeout, requests.ConnectionError) as caught:
                status_unknown.extend(batch)
                unattempted.extend(pending[start + len(batch) :])
                error = caught
                break
            except ADSError as caught:
                failed.extend(batch)
                unattempted.extend(pending[start + len(batch) :])
                error = caught
                break
            else:
                acknowledged.extend(batch)

        return AddDocumentsResult(
            acknowledged=tuple(acknowledged),
            already_present=known_present,
            failed=tuple(failed),
            status_unknown=tuple(status_unknown),
            unattempted=tuple(unattempted),
            cancelled=was_cancelled,
            error=error,
        )
