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


def test_streamable_http_exposes_mcp_headers_to_claude_origin(monkeypatch) -> None:
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
    assert "MCP-Session-Id" in response.headers["access-control-expose-headers"]
    assert response.headers["mcp-session-id"]
