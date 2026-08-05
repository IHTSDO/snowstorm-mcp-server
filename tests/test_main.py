from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from snowstorm_mcp_server.__main__ import _build_streamable_http_asgi


CONFIG_PATH = Path(__file__).resolve().parents[1] / "example-configs" / "config.local.yaml"


def test_streamable_http_handles_claude_cors_preflight(monkeypatch) -> None:
    monkeypatch.setenv("FASTMCP_HOST", "0.0.0.0")
    app = _build_streamable_http_asgi(str(CONFIG_PATH))

    with TestClient(app) as client:
        response = client.options(
            "/mcp",
            headers={
                "Origin": "https://claude.ai",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type,accept,mcp-protocol-version",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://claude.ai"
    assert "POST" in response.headers["access-control-allow-methods"]
    assert "MCP-Protocol-Version" in response.headers["access-control-allow-headers"]


MODERN_PROTOCOL_VERSION = "2026-07-28"

# Per-request metadata replaces the initialize handshake under MCP 2026-07-28.
MODERN_META = {
    "io.modelcontextprotocol/protocolVersion": MODERN_PROTOCOL_VERSION,
    "io.modelcontextprotocol/clientInfo": {"name": "pytest", "version": "1.0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}


def test_streamable_http_serves_legacy_initialize_without_minting_a_session(monkeypatch) -> None:
    """Clients on 2025-era revisions still work, but no session is pinned.

    The server is dual-era: it answers the legacy `initialize` handshake so
    existing clients keep working. Because it runs stateless, it does not mint
    an Mcp-Session-Id — permitted by those revisions, which only say a server
    MAY assign one.
    """
    monkeypatch.setenv("FASTMCP_HOST", "0.0.0.0")
    app = _build_streamable_http_asgi(str(CONFIG_PATH))

    with TestClient(app) as client:
        response = client.post(
            "/mcp",
            headers={
                "Origin": "https://claude.ai",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "1.0"},
                },
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://claude.ai"
    assert "mcp-session-id" not in response.headers


def test_streamable_http_serves_modern_request_without_handshake(monkeypatch) -> None:
    """A 2026-07-28 client calls tools/list cold — no initialize, no session."""
    monkeypatch.setenv("FASTMCP_HOST", "0.0.0.0")
    app = _build_streamable_http_asgi(str(CONFIG_PATH))

    with TestClient(app) as client:
        response = client.post(
            "/mcp",
            headers={
                "Origin": "https://claude.ai",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "MCP-Protocol-Version": MODERN_PROTOCOL_VERSION,
                "Mcp-Method": "tools/list",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {"_meta": MODERN_META},
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://claude.ai"
    assert "mcp-session-id" not in response.headers

    result = response.json()["result"]
    assert result["resultType"] == "complete"
    assert len(result["tools"]) > 0
    # CacheableResult fields are required on tools/list from 2026-07-28.
    assert result["cacheScope"] == "public"
    assert result["ttlMs"] > 0


def test_modern_request_rejected_when_header_contradicts_body(monkeypatch) -> None:
    """Header/body disagreement MUST be rejected with 400 + HeaderMismatch (-32020)."""
    monkeypatch.setenv("FASTMCP_HOST", "0.0.0.0")
    app = _build_streamable_http_asgi(str(CONFIG_PATH))

    with TestClient(app) as client:
        response = client.post(
            "/mcp",
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "MCP-Protocol-Version": MODERN_PROTOCOL_VERSION,
                "Mcp-Method": "prompts/list",  # contradicts the body's tools/list
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {"_meta": MODERN_META},
            },
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32020


def _modern_tools_list(client, origin: str | None = None):
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": MODERN_PROTOCOL_VERSION,
        "Mcp-Method": "tools/list",
    }
    if origin is not None:
        headers["Origin"] = origin
    return client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": MODERN_META}},
    )


def test_get_on_mcp_endpoint_is_rejected(monkeypatch) -> None:
    """The standalone GET stream is gone in 2026-07-28 and must not hang.

    Left to the SDK, GET /mcp opens a legacy SSE stream that never terminates,
    letting an unauthenticated caller pin connections indefinitely.
    """
    monkeypatch.setenv("FASTMCP_HOST", "0.0.0.0")
    app = _build_streamable_http_asgi(str(CONFIG_PATH))

    with TestClient(app) as client:
        response = client.get("/mcp")
        assert response.status_code == 405
        # RFC 9110 15.5.6: a 405 MUST carry Allow.
        assert response.headers["allow"] == "POST"
        # DELETE terminated a session pre-2026-07-28; the SDK already refuses it.
        assert client.delete("/mcp").status_code == 405
        # The favicon route shares the app and must keep serving GET.
        assert client.get("/favicon.ico").status_code == 200


def test_get_on_mcp_endpoint_is_rejected_under_mount(monkeypatch) -> None:
    """The GET block must survive a sub-path deployment.

    scope["path"] carries the mount prefix, so matching on it directly stops
    working behind `--root-path /api` or a Starlette Mount, and the request falls
    through to the SDK. Under uvicorn that opens the standalone SSE stream, which
    never terminates.

    The parent deliberately does not propagate the child's lifespan: the session
    manager then refuses to serve, so a regression fails fast here instead of
    hanging the suite on an SSE stream that never closes. Do not "fix" that by
    wiring the lifespan through.
    """
    from starlette.applications import Starlette
    from starlette.routing import Mount

    monkeypatch.setenv("FASTMCP_HOST", "0.0.0.0")
    inner = _build_streamable_http_asgi(str(CONFIG_PATH))
    mounted = Starlette(routes=[Mount("/api", app=inner)])

    with TestClient(mounted) as client:
        response = client.get("/api/mcp", headers={"Accept": "text/event-stream"})
        assert response.status_code == 405
        assert response.headers["allow"] == "POST"


def test_dns_rebinding_protection_is_off_by_default(monkeypatch) -> None:
    """Unset SNOWSTORM_MCP_ALLOWED_HOSTS must not change behaviour."""
    monkeypatch.setenv("FASTMCP_HOST", "0.0.0.0")
    monkeypatch.delenv("SNOWSTORM_MCP_ALLOWED_HOSTS", raising=False)
    app = _build_streamable_http_asgi(str(CONFIG_PATH))

    with TestClient(app) as client:
        assert _modern_tools_list(client, origin="https://evil.example").status_code == 200


def test_allowed_hosts_rejects_foreign_origin_and_host(monkeypatch) -> None:
    """With allowed hosts declared, a bad Origin is rejected and a good one passes."""
    monkeypatch.setenv("FASTMCP_HOST", "0.0.0.0")
    monkeypatch.setenv("SNOWSTORM_MCP_ALLOWED_HOSTS", "testserver")
    app = _build_streamable_http_asgi(str(CONFIG_PATH))

    with TestClient(app) as client:
        # Origin absent -> allowed (spec only requires rejecting a present, invalid Origin).
        assert _modern_tools_list(client).status_code == 200
        # Origin allowed via the CORS origin list.
        assert _modern_tools_list(client, origin="https://claude.ai").status_code == 200
        # Origin present and not allowed -> 403, as the transport spec requires.
        assert _modern_tools_list(client, origin="https://evil.example").status_code == 403
        # Host not in the allow list -> rejected.
        bad_host = client.post(
            "/mcp",
            headers={
                "Host": "attacker.example",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "MCP-Protocol-Version": MODERN_PROTOCOL_VERSION,
                "Mcp-Method": "tools/list",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {"_meta": MODERN_META},
            },
        )
        assert bad_host.status_code == 421


def test_server_discover_is_available(monkeypatch) -> None:
    """server/discover is a MUST-implement RPC under 2026-07-28."""
    monkeypatch.setenv("FASTMCP_HOST", "0.0.0.0")
    app = _build_streamable_http_asgi(str(CONFIG_PATH))

    with TestClient(app) as client:
        response = client.post(
            "/mcp",
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                "MCP-Protocol-Version": MODERN_PROTOCOL_VERSION,
                "Mcp-Method": "server/discover",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "server/discover",
                "params": {"_meta": MODERN_META},
            },
        )

    assert response.status_code == 200
    result = response.json()["result"]
    # Only modern versions are advertised here — server/discover does not exist
    # in the handshake-based revisions, so a legacy client never reaches it.
    # Dual-era serving is covered by the legacy initialize test above.
    assert MODERN_PROTOCOL_VERSION in result["supportedVersions"]
