from collections.abc import Iterator

import pytest

from adsfolio.api.libraries import LibrariesAPI
from adsfolio.config import ADSError


class FakeBaseClient:
    def __init__(self, responses: list[dict]) -> None:
        self._responses: Iterator[dict] = iter(responses)
        self.calls: list[tuple[str, dict | None]] = []

    def get(self, endpoint: str, params: dict | None = None) -> dict:
        self.calls.append((endpoint, params))
        try:
            return next(self._responses)
        except StopIteration as error:
            raise AssertionError("unexpected extra request") from error


def _library_response(
    library_id: str,
    documents: list[str],
    *,
    num_documents: int,
) -> dict:
    return {
        "metadata": {
            "id": library_id,
            "name": f"Library {library_id}",
            "description": "Preserved metadata",
            "num_documents": num_documents,
            "public": True,
            "permission": "owner",
            "owner": "owner-id",
            "date_created": "2026-01-01T00:00:00Z",
            "date_last_modified": "2026-01-02T00:00:00Z",
        },
        "documents": documents,
    }


def test_iter_libraries_advances_by_requested_page_size_until_empty_page() -> None:
    client = FakeBaseClient(
        [
            {
                "libraries": [
                    {"id": "L1", "name": "One"},
                    {"id": "L2", "name": "Two"},
                ]
            },
            {
                "libraries": [
                    {"id": "L3", "name": "Three"},
                    {"id": "L4", "name": "Four"},
                ]
            },
            {"libraries": []},
        ]
    )

    libraries = list(
        LibrariesAPI(client).iter_libraries(
            rows=2,
            sort="date_last_modified",
            order="desc",
        )
    )

    assert [library.id for library in libraries] == ["L1", "L2", "L3", "L4"]
    assert client.calls == [
        (
            "/biblib/libraries",
            {
                "start": 0,
                "rows": 2,
                "sort": "date_last_modified",
                "order": "desc",
            },
        ),
        (
            "/biblib/libraries",
            {
                "start": 2,
                "rows": 2,
                "sort": "date_last_modified",
                "order": "desc",
            },
        ),
        (
            "/biblib/libraries",
            {
                "start": 4,
                "rows": 2,
                "sort": "date_last_modified",
                "order": "desc",
            },
        ),
    ]


def test_iter_documents_combines_pages_and_uses_first_page_metadata() -> None:
    client = FakeBaseClient(
        [
            _library_response("L1", ["D1", "D2"], num_documents=4),
            _library_response("L1", ["D3", "D4"], num_documents=4),
        ]
    )

    documents = list(LibrariesAPI(client).iter_documents("L1", raw=True, rows=2))

    assert documents == ["D1", "D2", "D3", "D4"]
    assert client.calls == [
        (
            "/biblib/libraries/L1",
            {"start": 0, "rows": 2, "raw": "true"},
        ),
        (
            "/biblib/libraries/L1",
            {"start": 2, "rows": 2, "raw": "true"},
        ),
    ]


def test_iter_documents_stops_on_short_page_even_when_metadata_is_larger() -> None:
    client = FakeBaseClient(
        [
            _library_response("L1", ["D1", "D2"], num_documents=5),
            _library_response("L1", ["D3"], num_documents=5),
        ]
    )

    documents = list(LibrariesAPI(client).iter_documents("L1", rows=2))

    assert documents == ["D1", "D2", "D3"]
    assert [params["start"] for _, params in client.calls if params] == [0, 2]


def test_iter_documents_stops_on_empty_page() -> None:
    client = FakeBaseClient([_library_response("L1", [], num_documents=5)])

    assert list(LibrariesAPI(client).iter_documents("L1", rows=2)) == []
    assert client.calls == [("/biblib/libraries/L1", {"start": 0, "rows": 2})]


def test_iter_documents_uses_only_first_page_document_count() -> None:
    client = FakeBaseClient(
        [
            _library_response("L1", ["D1", "D2"], num_documents=0),
            _library_response("L1", ["D3", "D4"], num_documents=4),
            _library_response("L1", [], num_documents=4),
        ]
    )

    documents = list(LibrariesAPI(client).iter_documents("L1", rows=2))

    assert documents == ["D1", "D2", "D3", "D4"]
    assert [params["start"] for _, params in client.calls if params] == [0, 2, 4]


def test_iter_libraries_rejects_repeated_non_empty_page() -> None:
    page = {"libraries": [{"id": "L1", "name": "One"}]}
    client = FakeBaseClient([page, page])

    with pytest.raises(ADSError, match="repeated"):
        list(LibrariesAPI(client).iter_libraries(rows=1))

    assert [params["start"] for _, params in client.calls if params] == [0, 1]


def test_iter_documents_rejects_repeated_non_empty_page() -> None:
    page = _library_response("L1", ["D1", "D2"], num_documents=5)
    client = FakeBaseClient([page, page])

    with pytest.raises(ADSError, match="repeated"):
        list(LibrariesAPI(client).iter_documents("L1", rows=2))

    assert [params["start"] for _, params in client.calls if params] == [0, 2]


@pytest.mark.parametrize("rows", [0, -1])
def test_iter_libraries_rejects_non_positive_rows(rows: int) -> None:
    with pytest.raises(ValueError, match="rows"):
        list(LibrariesAPI(FakeBaseClient([])).iter_libraries(rows=rows))


@pytest.mark.parametrize("rows", [0, -1, 2001])
def test_iter_documents_rejects_invalid_rows(rows: int) -> None:
    with pytest.raises(ValueError, match="rows"):
        list(LibrariesAPI(FakeBaseClient([])).iter_documents("L1", rows=rows))
