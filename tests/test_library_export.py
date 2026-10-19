"""Tests for complete ADS library bibliography exports."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import CancelledError

import pytest

from adsfolio.api.export import ExportAPI
from adsfolio.api.libraries import LibrariesAPI, Library
from adsfolio.config import ADSError
from adsfolio.library_sync import AdsLibraryService, LibraryExportResult


class RecordingExport:
    """Record service export calls and return a controlled bibliography."""

    def __init__(
        self,
        *,
        result: str = "bibliography",
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[tuple[str, ...], str, Callable[[], bool] | None]] = []

    def export_complete(
        self,
        bibcodes: Iterable[str],
        *,
        format: str = "bibtex",
        cancelled: Callable[[], bool] | None = None,
    ) -> str:
        self.calls.append((tuple(bibcodes), format, cancelled))
        if self.error is not None:
            raise self.error
        return self.result


class RecordingLibraries:
    """Provide library metadata and documents to the service."""

    def __init__(
        self,
        documents: Iterable[str],
        *,
        advertised_count: int | None = None,
        after_yield: Callable[[int], None] | None = None,
        permission: str = "read",
    ) -> None:
        self.documents = tuple(documents)
        self.advertised_count = (
            len(self.documents) if advertised_count is None else advertised_count
        )
        self.after_yield = after_yield
        self.permission = permission
        self.calls: list[tuple[object, ...]] = []

    def get_library(
        self,
        library_id: str,
        raw: bool = False,
        start: int = 0,
        rows: int = 2_000,
    ) -> Library:
        self.calls.append(("get_library", library_id, raw, start, rows))
        return Library(
            id=library_id,
            name="Read-only remote library",
            permission=self.permission,
            num_documents=self.advertised_count,
            documents=list(self.documents[start : start + rows]),
        )

    def iter_documents(
        self,
        library_id: str,
        *,
        raw: bool = False,
    ):
        self.calls.append(("iter_documents", library_id, raw))
        for index, document in enumerate(self.documents):
            yield document
            if self.after_yield is not None:
                self.after_yield(index)


class ServiceClient:
    """Minimal client protocol used by `AdsLibraryService` tests."""

    def __init__(
        self,
        libraries: RecordingLibraries,
        export: RecordingExport,
    ) -> None:
        self.libraries = libraries
        self.export = export


class PagingClient:
    """Fake transport wired to the production pagination and export APIs."""

    def __init__(self, documents: Iterable[str]) -> None:
        self.documents = tuple(documents)
        self.get_calls: list[tuple[str, dict[str, object]]] = []
        self.post_calls: list[tuple[str, dict[str, object]]] = []
        self.libraries = LibrariesAPI(self)
        self.export = ExportAPI(self)

    def get(self, endpoint: str, *, params: dict[str, object]) -> dict[str, object]:
        self.get_calls.append((endpoint, params))
        assert endpoint == "/biblib/libraries/L-read"
        start = int(params["start"])
        rows = int(params["rows"])
        return {
            "metadata": {
                "id": "L-read",
                "name": "Read-only remote library",
                "permission": "read",
                "num_documents": len(self.documents),
            },
            "documents": list(self.documents[start : start + rows]),
        }

    def post(
        self,
        endpoint: str,
        *,
        data: dict[str, object],
    ) -> dict[str, object]:
        self.post_calls.append((endpoint, data))
        bibcodes = data["bibcode"]
        assert isinstance(bibcodes, list)
        return {
            "num_docs": len(bibcodes),
            "docs": [
                {"bibcode": bibcode, "reference": f"ref:{bibcode}"}
                for bibcode in bibcodes
            ],
            "header": "HEADER",
            "footer": "FOOTER",
        }


def test_service_exports_complete_sorted_remote_read_only_library() -> None:
    libraries = RecordingLibraries(("B", "A", "C"), permission="read")
    export = RecordingExport(result="exported bibliography")
    service = AdsLibraryService(ServiceClient(libraries, export))

    result = service.export_library("L-read", format="ris")

    assert result == LibraryExportResult(
        text="exported bibliography",
        bibcodes=("A", "B", "C"),
    )
    assert libraries.calls == [
        ("get_library", "L-read", False, 0, 1),
        ("iter_documents", "L-read", False),
    ]
    assert len(export.calls) == 1
    assert export.calls[0][0] == ("A", "B", "C")
    assert export.calls[0][1] == "ris"


def test_service_rejects_incomplete_remote_content_without_export() -> None:
    libraries = RecordingLibraries(("A",), advertised_count=2)
    export = RecordingExport()

    with pytest.raises(ADSError, match="incomplete"):
        AdsLibraryService(ServiceClient(libraries, export)).export_library("L1")

    assert export.calls == []


def test_service_rejects_duplicate_documents_without_export() -> None:
    libraries = RecordingLibraries(("A", "A"), advertised_count=2)
    export = RecordingExport()

    with pytest.raises(ADSError, match="incomplete"):
        AdsLibraryService(ServiceClient(libraries, export)).export_library("L1")

    assert export.calls == []


def test_service_empty_library_returns_empty_result_without_export() -> None:
    libraries = RecordingLibraries(())
    export = RecordingExport(error=AssertionError("empty library was exported"))

    result = AdsLibraryService(ServiceClient(libraries, export)).export_library("L1")

    assert result == LibraryExportResult(text="", bibcodes=())
    assert export.calls == []


def test_service_cancellation_before_enumeration_skips_network_and_export() -> None:
    libraries = RecordingLibraries(("A",))
    export = RecordingExport()

    result = AdsLibraryService(ServiceClient(libraries, export)).export_library(
        "L1",
        cancelled=lambda: True,
    )

    assert result == LibraryExportResult(text="", bibcodes=(), cancelled=True)
    assert libraries.calls == []
    assert export.calls == []


def test_service_cancellation_during_enumeration_discards_partial_codes() -> None:
    cancelled = False

    def after_yield(index: int) -> None:
        nonlocal cancelled
        if index == 0:
            cancelled = True

    libraries = RecordingLibraries(("A", "B"), after_yield=after_yield)
    export = RecordingExport()

    result = AdsLibraryService(ServiceClient(libraries, export)).export_library(
        "L1",
        cancelled=lambda: cancelled,
    )

    assert result == LibraryExportResult(text="", bibcodes=(), cancelled=True)
    assert export.calls == []


def test_service_propagates_export_error_after_complete_fetch() -> None:
    libraries = RecordingLibraries(("A",))
    export = RecordingExport(error=ADSError("export unavailable"))

    with pytest.raises(ADSError, match="export unavailable"):
        AdsLibraryService(ServiceClient(libraries, export)).export_library("L1")

    assert export.calls[0][0] == ("A",)


def test_service_rejects_unsupported_format_before_network() -> None:
    libraries = RecordingLibraries(("A",))
    export = RecordingExport()

    with pytest.raises(ValueError, match="Unsupported bibliography format"):
        AdsLibraryService(ServiceClient(libraries, export)).export_library(
            "L1", format="custom"
        )

    assert libraries.calls == []
    assert export.calls == []


def test_service_exports_all_paginated_documents_in_stable_batches() -> None:
    documents = tuple(f"{2_000 - index:04d}" for index in range(2_001))
    client = PagingClient(documents)

    result = AdsLibraryService(client).export_library("L-read")

    expected = tuple(sorted(documents))
    assert result.bibcodes == expected
    assert result.cancelled is False
    assert "ref:0000" in result.text
    assert "ref:2000" in result.text
    assert [params["rows"] for _endpoint, params in client.get_calls] == [
        1,
        2_000,
        2_000,
    ]
    assert [params["start"] for _endpoint, params in client.get_calls] == [0, 0, 2_000]
    batches = [params["bibcode"] for _endpoint, params in client.post_calls]
    assert all(isinstance(batch, list) for batch in batches)
    assert [code for batch in batches for code in batch] == list(expected)
    assert all(len(batch) <= ExportAPI.MAX_RECORDS_PER_REQUEST for batch in batches)
    assert [endpoint for endpoint, _params in client.post_calls] == [
        "/export/bibtex",
        "/export/bibtex",
    ]


def test_export_api_cancellation_before_request_raises_without_network() -> None:
    client = PagingClient(())

    with pytest.raises(CancelledError):
        client.export.export_complete(("A",), cancelled=lambda: True)

    assert client.post_calls == []


def test_export_api_cancellation_between_batches_discards_partial_export() -> None:
    client = PagingClient(())
    documents = tuple(f"B{index}" for index in range(2_001))

    with pytest.raises(CancelledError):
        client.export.export_complete(
            documents,
            cancelled=lambda: len(client.post_calls) >= 1,
        )

    assert len(client.post_calls) == 1
