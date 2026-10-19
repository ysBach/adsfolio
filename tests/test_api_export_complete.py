"""Complete bibliography exports validate all records across ADS batches."""

from __future__ import annotations

from concurrent.futures import CancelledError
from typing import Any
from unittest.mock import Mock

import pytest

from adsfolio.api.export import ExportAPI
from adsfolio.config import ADSError


class ExportClient:
    """Record requests and model ADS individual-reference responses."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def post(self, endpoint: str, data: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((endpoint, data))
        return {
            "num_docs": len(data["bibcode"]),
            "docs": [
                {"bibcode": code, "reference": f"Reference {code} 한글\n"}
                for code in reversed(data["bibcode"])
            ],
        }


@pytest.mark.parametrize(
    ("format", "custom_format"),
    [("%R %T", None), ("custom", "%R %T"), ("bibtex", "%R %T")],
)
def test_custom_export_sends_format_string(
    format: str, custom_format: str | None
) -> None:
    client = Mock()
    client.post.return_value = {"export": "B1 title"}

    result = ExportAPI(client).export(
        ["B1"], format=format, custom_format=custom_format, sort="year desc"
    )

    assert result == "B1 title"
    client.post.assert_called_once_with(
        "/export/custom",
        data={"bibcode": ["B1"], "format": "%R %T", "sort": "year desc"},
    )


@pytest.mark.parametrize("custom_format", [None, "", "   "])
def test_custom_export_requires_nonempty_format_before_request(
    custom_format: str | None,
) -> None:
    client = Mock()
    with pytest.raises(ValueError, match="custom_format is required"):
        ExportAPI(client).export(["B1"], format="custom", custom_format=custom_format)
    client.post.assert_not_called()


@pytest.mark.parametrize("format", ExportAPI.BIBLIOGRAPHY_FORMATS)
def test_selected_format_preserves_all_records_and_input_order(format: str) -> None:
    client = ExportClient()
    text = ExportAPI(client).export_complete(["B2", "B1"], format=format)
    assert text == "Reference B2 한글\n\nReference B1 한글\n"
    assert client.calls == [
        (
            f"/export/{format}",
            {"bibcode": ["B2", "B1"], "sort": "no sort", "outputformat": 2},
        )
    ]


def test_export_larger_than_ads_limit_includes_final_record() -> None:
    client = ExportClient()
    bibcodes = [f"B{index:05d}" for index in range(2001)]
    result = ExportAPI(client).export_complete(bibcodes)
    assert [len(data["bibcode"]) for _, data in client.calls] == [2000, 1]
    assert result.count("Reference ") == len(bibcodes)
    assert result.endswith("Reference B02000 한글\n")


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"num_docs": 0, "docs": []},
        {"num_docs": True, "docs": []},
        {"num_docs": 2, "docs": [None, None]},
        {"num_docs": 2, "docs": [{"bibcode": "B1", "reference": "x"}] * 2},
        {
            "num_docs": 2,
            "docs": [
                {"bibcode": "B1", "reference": ""},
                {"bibcode": "B2", "reference": "y"},
            ],
        },
        {
            "num_docs": 2,
            "docs": [
                {"bibcode": "B1", "reference": "x"},
                {"bibcode": "other", "reference": "y"},
            ],
        },
        {
            "num_docs": 2,
            "docs": [
                {"bibcode": "B1", "reference": "x"},
                {"bibcode": "B2", "reference": "y"},
            ],
            "header": 1,
        },
    ],
)
def test_missing_duplicate_or_malformed_records_fail(
    monkeypatch, response: dict
) -> None:
    client = ExportClient()
    monkeypatch.setattr(client, "post", lambda *args, **kwargs: response)
    with pytest.raises(ADSError):
        ExportAPI(client).export_complete(["B1", "B2"])


def test_error_in_later_batch_does_not_return_partial_bibliography(monkeypatch) -> None:
    client = ExportClient()
    post = client.post

    def fail_second(endpoint: str, data: dict[str, Any]) -> dict[str, Any]:
        if client.calls:
            raise ADSError("export unavailable")
        return post(endpoint, data)

    monkeypatch.setattr(client, "post", fail_second)
    with pytest.raises(ADSError, match="export unavailable"):
        ExportAPI(client).export_complete([f"B{i}" for i in range(2001)])


def test_cancelled_export_stops_after_inflight_request() -> None:
    client = ExportClient()
    with pytest.raises(CancelledError):
        ExportAPI(client).export_complete(
            [f"B{i}" for i in range(2001)], cancelled=lambda: bool(client.calls)
        )
    assert len(client.calls) == 1


def test_empty_library_has_no_export_request() -> None:
    client = ExportClient()
    assert ExportAPI(client).export_complete([]) == ""
    assert client.calls == []


@pytest.mark.parametrize("bibcodes", [[""], ["B1", "b1"], "B1"])
def test_invalid_bibcodes_fail_before_network(bibcodes) -> None:
    client = ExportClient()
    with pytest.raises(TypeError if isinstance(bibcodes, str) else ValueError):
        ExportAPI(client).export_complete(bibcodes)
    assert client.calls == []


def test_unsupported_format_fails_before_network() -> None:
    client = ExportClient()
    with pytest.raises(ValueError, match="Unsupported bibliography format"):
        ExportAPI(client).export_complete(["B1"], format="custom")
    assert client.calls == []
