from __future__ import annotations

import base64

import httpx
import pytest

from snowstorm_mcp_server.config import TargetConfig
from snowstorm_mcp_server.http_client import HttpClient, HttpRequestError


def test_http_client_none_auth_sends_no_authorization_header() -> None:
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"ok": True})

    target = TargetConfig(base_url="http://test", auth={"mode": "none"})
    raw = httpx.Client(transport=httpx.MockTransport(handler), base_url=target.base_url)
    with HttpClient(target, client=raw) as client:
        client.request("GET", "http://test/health", expect_json=True)

    assert seen["authorization"] is None


def test_http_client_basic_auth_injects_authorization_header() -> None:
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"ok": True})

    target = TargetConfig(
        base_url="http://test",
        auth={"mode": "basic", "username": "alice", "password": "secret"},
    )
    raw = httpx.Client(transport=httpx.MockTransport(handler), base_url=target.base_url)
    with HttpClient(target, client=raw) as client:
        client.request("GET", "http://test/health", expect_json=True)

    assert seen["authorization"] is not None
    expected = "Basic " + base64.b64encode(b"alice:secret").decode("ascii")
    assert seen["authorization"] == expected


def test_http_client_bearer_auth_injects_authorization_header() -> None:
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"ok": True})

    target = TargetConfig(base_url="http://test", auth={"mode": "bearer", "token": "abc123"})
    raw = httpx.Client(transport=httpx.MockTransport(handler), base_url=target.base_url)
    with HttpClient(target, client=raw) as client:
        client.request("GET", "http://test/health", expect_json=True)

    assert seen["authorization"] == "Bearer abc123"


def test_http_client_headers_mode_injects_static_headers() -> None:
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["x-api-key"] = request.headers.get("X-API-Key")
        return httpx.Response(200, json={"ok": True})

    target = TargetConfig(
        base_url="http://test",
        auth={"mode": "headers", "headers": {"X-API-Key": "demo-key"}},
    )
    raw = httpx.Client(transport=httpx.MockTransport(handler), base_url=target.base_url)
    with HttpClient(target, client=raw) as client:
        client.request("GET", "http://test/health", expect_json=True)

    assert seen["x-api-key"] == "demo-key"


def test_http_client_error_message_includes_target_method_path_and_status() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable")

    target = TargetConfig(
        name="snowstorm",
        base_url="http://test",
        auth={"mode": "none"},
    )
    raw = httpx.Client(transport=httpx.MockTransport(handler), base_url=target.base_url)
    with HttpClient(target, client=raw) as client:
        with pytest.raises(HttpRequestError, match="HTTP 503"):
            client.request("GET", "http://test/fhir/metadata", expect_json=True)

    try:
        with HttpClient(target, client=raw) as client:
            client.request("GET", "http://test/fhir/metadata", expect_json=True)
    except HttpRequestError as exc:
        msg = str(exc)
        assert "target 'snowstorm'" in msg
        assert "GET /fhir/metadata" in msg
        assert "HTTP 503" in msg
