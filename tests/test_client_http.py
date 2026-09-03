import json
from unittest.mock import Mock

import pytest
import requests

from adsapi.client import ADSClient, BaseClient
from adsapi.config import Config

TIMEOUT = (3.05, 30.0)
HEADERS = {
    "Authorization": "Bearer token",
    "Content-Type": "application/json",
}


def _response() -> Mock:
    return Mock(ok=True, status_code=200, headers={}, json=dict)


def test_base_client_passes_configured_timeout_to_get(monkeypatch) -> None:
    client = BaseClient(token="token", timeout=TIMEOUT)
    response = _response()
    get = Mock(return_value=response)
    monkeypatch.setattr(client._session, "get", get)
    params = {"q": "x"}

    client.get("/test", params=params)

    assert get.call_args.args == (f"{Config.API_BASE}/test",)
    assert get.call_args.kwargs == {
        "headers": HEADERS,
        "params": params,
        "timeout": TIMEOUT,
    }


def test_base_client_passes_configured_timeout_to_post(monkeypatch) -> None:
    client = BaseClient(token="token", timeout=TIMEOUT)
    response = _response()
    post = Mock(return_value=response)
    monkeypatch.setattr(client._session, "post", post)
    payload = {"q": "x"}

    client.post("/test", data=payload)

    assert post.call_args.args == (f"{Config.API_BASE}/test",)
    assert post.call_args.kwargs == {
        "headers": HEADERS,
        "data": json.dumps(payload),
        "timeout": TIMEOUT,
    }


def test_base_client_passes_configured_timeout_to_post_raw(monkeypatch) -> None:
    client = BaseClient(token="token", timeout=TIMEOUT)
    response = _response()
    post = Mock(return_value=response)
    monkeypatch.setattr(client._session, "post", post)
    payload = b"raw payload"
    params = {"q": "x"}
    extra_headers = {"Content-Type": "text/plain"}

    client.post_raw(
        "/test",
        data=payload,
        params=params,
        headers=extra_headers,
    )

    assert post.call_args.args == (f"{Config.API_BASE}/test",)
    assert post.call_args.kwargs == {
        "headers": {
            "Authorization": "Bearer token",
            "Content-Type": "text/plain",
        },
        "params": params,
        "data": payload,
        "timeout": TIMEOUT,
    }


def test_base_client_passes_configured_timeout_to_put(monkeypatch) -> None:
    client = BaseClient(token="token", timeout=TIMEOUT)
    response = _response()
    put = Mock(return_value=response)
    monkeypatch.setattr(client._session, "put", put)
    payload = {"q": "x"}

    client.put("/test", data=payload)

    assert put.call_args.args == (f"{Config.API_BASE}/test",)
    assert put.call_args.kwargs == {
        "headers": HEADERS,
        "data": json.dumps(payload),
        "timeout": TIMEOUT,
    }


def test_base_client_passes_configured_timeout_to_delete(monkeypatch) -> None:
    client = BaseClient(token="token", timeout=TIMEOUT)
    response = _response()
    delete = Mock(return_value=response)
    monkeypatch.setattr(client._session, "delete", delete)

    client.delete("/test")

    assert delete.call_args.args == (f"{Config.API_BASE}/test",)
    assert delete.call_args.kwargs == {
        "headers": HEADERS,
        "timeout": TIMEOUT,
    }


def test_base_client_propagates_requests_timeout_unchanged(monkeypatch) -> None:
    client = BaseClient(token="token", timeout=TIMEOUT)
    timeout = requests.Timeout("timed out")
    get = Mock(side_effect=timeout)
    monkeypatch.setattr(client._session, "get", get)

    with pytest.raises(requests.Timeout) as error:
        client.get("/test")

    assert error.value is timeout


def test_ads_client_passes_configured_timeout_to_base_client(monkeypatch) -> None:
    base_client = Mock()
    base_client_factory = Mock(return_value=base_client)
    monkeypatch.setattr("adsapi.client.BaseClient", base_client_factory)

    ADSClient(token="token", timeout=TIMEOUT)

    base_client_factory.assert_called_once_with("token", timeout=TIMEOUT)
