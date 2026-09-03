from collections.abc import Iterable, Iterator

import pytest
import requests

from adsapi.api.libraries import Library
from adsapi.config import ADSError
from adsapi.library_sync import (
    AddDocumentsResult,
    AdsLibraryService,
    BibcodeSelection,
    LibraryDestination,
    collect_identified_bibcodes,
)


class FakeLibraries:
    def __init__(
        self,
        libraries: Iterable[Library] = (),
        documents: Iterable[str] = (),
        add_effects: Iterable[Exception | None] = (),
    ) -> None:
        self._libraries = tuple(libraries)
        self._documents = tuple(documents)
        self._add_effects = iter(add_effects)
        self.list_calls = 0
        self.document_calls: list[tuple[str, bool]] = []
        self.create_calls: list[tuple[str, str, bool, list[str] | None]] = []
        self.add_calls: list[tuple[str, list[str]]] = []

    def iter_libraries(self) -> Iterator[Library]:
        self.list_calls += 1
        yield from self._libraries

    def iter_documents(self, library_id: str, *, raw: bool = False) -> Iterator[str]:
        self.document_calls.append((library_id, raw))
        yield from self._documents

    def create_library(
        self,
        name: str,
        description: str,
        public: bool,
        bibcodes: list[str] | None,
    ) -> Library:
        self.create_calls.append((name, description, public, bibcodes))
        return Library(
            id="created",
            name=name,
            description=description,
            public=public,
            permission="owner",
            num_documents=0,
        )

    def add_documents(self, library_id: str, bibcodes: list[str]) -> dict:
        self.add_calls.append((library_id, bibcodes))
        try:
            effect = next(self._add_effects)
        except StopIteration:
            effect = None
        if effect is not None:
            raise effect
        return {"number_added": len(bibcodes)}


class FakeClient:
    def __init__(self, libraries: FakeLibraries) -> None:
        self.libraries = libraries


def _library(
    library_id: str,
    permission: str,
    *,
    name: str | None = None,
    public: bool = False,
    num_documents: int = 0,
) -> Library:
    return Library(
        id=library_id,
        name=name or library_id,
        permission=permission,
        public=public,
        num_documents=num_documents,
    )


def test_collect_identified_bibcodes_deduplicates_and_counts_exclusions() -> None:
    result = collect_identified_bibcodes(
        [
            {"status": "resolved", "document_id": 1, "metadata": {"bibcode": "B2"}},
            {"status": "unresolved", "metadata": {"bibcode": "BAD"}},
            {"status": "resolved", "document_id": 2, "metadata": {"bibcode": "B1"}},
            {"status": "resolved", "document_id": 3, "metadata": {"bibcode": "B2"}},
            {"status": "resolved", "document_id": 4, "metadata": {}},
        ]
    )

    assert result == BibcodeSelection(
        bibcodes=("B2", "B1"),
        source_files=5,
        ineligible_files=2,
        local_duplicate_files=1,
    )


def test_collect_identified_bibcodes_accepts_top_level_and_trimmed_bibcodes() -> None:
    result = collect_identified_bibcodes(
        [
            {"status": "resolved", "document_id": 1, "bibcode": "  TOP  "},
            {
                "status": "resolved",
                "document_id": 2,
                "metadata": {"bibcode": " NESTED "},
            },
        ]
    )

    assert result.bibcodes == ("TOP", "NESTED")
    assert result.source_files == 2
    assert result.ineligible_files == 0
    assert result.local_duplicate_files == 0


def test_collect_identified_bibcodes_excludes_resolved_records_without_document_id() -> (
    None
):
    result = collect_identified_bibcodes(
        [
            {"status": "resolved", "bibcode": "NO-ID"},
            {"status": "resolved", "document_id": 0, "bibcode": "ZERO-ID"},
        ]
    )

    assert result.bibcodes == ("ZERO-ID",)
    assert result.source_files == 2
    assert result.ineligible_files == 1
    assert result.local_duplicate_files == 0


def test_list_destinations_preserves_order_and_only_write_permissions_are_writable() -> (
    None
):
    libraries = FakeLibraries(
        [
            _library("L-read", "read", public=True, num_documents=4),
            _library("L-owner", "owner", name="Owner library"),
            _library("L-admin", "admin"),
            _library("L-write", "write"),
            _library("L-unknown", "mystery"),
        ]
    )

    result = AdsLibraryService(FakeClient(libraries)).list_destinations()

    assert result == (
        LibraryDestination("L-read", "L-read", "read", True, 4),
        LibraryDestination("L-owner", "Owner library", "owner", False, 0),
        LibraryDestination("L-admin", "L-admin", "admin", False, 0),
        LibraryDestination("L-write", "L-write", "write", False, 0),
        LibraryDestination("L-unknown", "L-unknown", "mystery", False, 0),
    )
    assert [destination.writable for destination in result] == [
        False,
        True,
        True,
        True,
        False,
    ]


def test_create_destination_trims_name_and_always_creates_empty_library() -> None:
    libraries = FakeLibraries()
    service = AdsLibraryService(FakeClient(libraries))

    result = service.create_destination("  New library  ", "A description", True)

    assert result == LibraryDestination("created", "New library", "owner", True, 0)
    assert libraries.create_calls == [("New library", "A description", True, None)]


def test_create_destination_rejects_blank_name() -> None:
    libraries = FakeLibraries()
    service = AdsLibraryService(FakeClient(libraries))

    with pytest.raises(ValueError, match="name"):
        service.create_destination("  \t", "description", False)

    assert libraries.create_calls == []


def test_preflight_returns_stable_missing_and_already_present_tuples() -> None:
    libraries = FakeLibraries(documents=["B2", " B4 "])
    service = AdsLibraryService(FakeClient(libraries))

    result = service.preflight("L1", [" B1 ", "B2", "B3", "B1", "B4"])

    assert result == AddDocumentsResult(
        already_present=("B2", "B4"),
        unattempted=("B1", "B3"),
    )
    assert libraries.document_calls == [("L1", True)]


def test_add_documents_uses_exact_2000_item_batches() -> None:
    libraries = FakeLibraries()
    service = AdsLibraryService(FakeClient(libraries))
    bibcodes = [f"B{index}" for index in range(4001)]

    result = service.add_documents("L1", bibcodes)

    assert [len(batch) for _, batch in libraries.add_calls] == [2000, 2000, 1]
    assert result.acknowledged == tuple(bibcodes)
    assert result.failed == ()
    assert result.status_unknown == ()
    assert result.unattempted == ()


def test_ads_error_stops_after_failed_batch_and_preserves_remainder() -> None:
    libraries = FakeLibraries(add_effects=[None, ADSError("rejected"), None])
    service = AdsLibraryService(FakeClient(libraries))
    bibcodes = [f"B{index}" for index in range(4001)]

    result = service.add_documents("L1", bibcodes)

    assert [len(batch) for _, batch in libraries.add_calls] == [2000, 2000]
    assert result.acknowledged == tuple(bibcodes[:2000])
    assert result.failed == tuple(bibcodes[2000:4000])
    assert result.status_unknown == ()
    assert result.unattempted == (bibcodes[4000],)
    assert isinstance(result.error, ADSError)
    assert result.cancelled is False


@pytest.mark.parametrize(
    "transport_error",
    [requests.Timeout("timed out"), requests.ConnectionError("disconnected")],
)
def test_transport_error_marks_active_batch_status_unknown(
    transport_error: Exception,
) -> None:
    libraries = FakeLibraries(add_effects=[None, transport_error, None])
    service = AdsLibraryService(FakeClient(libraries))
    bibcodes = [f"B{index}" for index in range(4001)]

    result = service.add_documents("L1", bibcodes)

    assert [len(batch) for _, batch in libraries.add_calls] == [2000, 2000]
    assert result.acknowledged == tuple(bibcodes[:2000])
    assert result.failed == ()
    assert result.status_unknown == tuple(bibcodes[2000:4000])
    assert result.unattempted == (bibcodes[4000],)
    assert result.error is transport_error


def test_cancellation_is_checked_before_next_batch_request() -> None:
    libraries = FakeLibraries()
    service = AdsLibraryService(FakeClient(libraries))
    bibcodes = [f"B{index}" for index in range(4001)]

    def cancelled() -> bool:
        return len(libraries.add_calls) >= 1

    result = service.add_documents("L1", bibcodes, cancelled=cancelled)

    assert [len(batch) for _, batch in libraries.add_calls] == [2000]
    assert result.acknowledged == tuple(bibcodes[:2000])
    assert result.unattempted == tuple(bibcodes[2000:])
    assert result.cancelled is True
    assert result.error is None


def test_non_writable_destination_is_rejected_before_first_post() -> None:
    libraries = FakeLibraries([_library("L1", "read")])
    service = AdsLibraryService(FakeClient(libraries))
    service.list_destinations()

    with pytest.raises(PermissionError, match="writable"):
        service.add_documents("L1", ["B1"])

    assert libraries.add_calls == []


def test_programming_errors_from_add_documents_propagate() -> None:
    programming_error = RuntimeError("bug")
    libraries = FakeLibraries(add_effects=[programming_error])
    service = AdsLibraryService(FakeClient(libraries))

    with pytest.raises(RuntimeError, match="bug"):
        service.add_documents("L1", ["B1"])


def test_reconcile_and_retry_submits_only_bibcodes_absent_remotely() -> None:
    libraries = FakeLibraries(documents=["B2", " B4 "])
    service = AdsLibraryService(FakeClient(libraries))

    result = service.reconcile_and_retry("L1", ["B1", "B2", "B3", "B1", "B4"])

    assert libraries.document_calls == [("L1", True)]
    assert libraries.add_calls == [("L1", ["B1", "B3"])]
    assert result == AddDocumentsResult(
        acknowledged=("B1", "B3"),
        already_present=("B2", "B4"),
    )
