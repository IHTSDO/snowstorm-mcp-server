from __future__ import annotations

import argparse
import logging
import os

from dotenv import find_dotenv, load_dotenv

from .logging_utils import configure_logging
from .mcp_app import create_mcp_app

STREAMABLE_HTTP_PATH = "/mcp"

DEFAULT_CORS_ALLOW_ORIGINS = ("https://claude.ai", "https://claude.com")
# MCP 2026-07-28 mirrors selected body fields into request headers so that
# intermediaries can route without parsing the body; Mcp-Method and Mcp-Name are
# REQUIRED on POSTs. Mcp-Session-Id and Last-Event-ID are retained only so that
# clients still speaking 2025-11-25 or earlier keep working against the same
# endpoint — the modern revision removed both.
DEFAULT_CORS_ALLOW_HEADERS = (
    "Accept",
    "Authorization",
    "Content-Type",
    "Last-Event-ID",
    "MCP-Protocol-Version",
    "MCP-Session-Id",
    "Mcp-Method",
    "Mcp-Name",
)
DEFAULT_CORS_EXPOSE_HEADERS = (
    "MCP-Protocol-Version",
    "MCP-Session-Id",
)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Snowstorm MCP server")
    parser.add_argument(
        "--config",
        default=None,
        help="Path to YAML/JSON config file (or use SNOWSTORM_MCP_CONFIG env var)",
    )
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "streamable-http"],
        help=(
            "MCP transport to run. The standalone 'sse' transport was removed — "
            "HTTP+SSE is Deprecated as of MCP 2026-07-28; use streamable-http."
        ),
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        type=str.upper,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    parser.add_argument(
        "--log-format",
        default="json",
        choices=["json", "text"],
        help=(
            "Log output format (default: json). "
            "json emits one JSON object per line with a UTC timestamp."
        ),
    )
    return parser


def _cors_allow_origins() -> list[str]:
    raw_value = os.environ.get("SNOWSTORM_MCP_CORS_ALLOW_ORIGINS")
    if raw_value is None:
        return list(DEFAULT_CORS_ALLOW_ORIGINS)
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def _transport_security(allow_origins: list[str]):
    """DNS-rebinding protection, opt-in via SNOWSTORM_MCP_ALLOWED_HOSTS.

    The MCP transport spec requires servers to reject a present-but-invalid
    Origin. The SDK enables that automatically when bound to localhost, but a
    container binds 0.0.0.0 and the SDK cannot guess the public hostname, so it
    disables the check entirely. Setting SNOWSTORM_MCP_ALLOWED_HOSTS to the
    hostnames this server is reached on (e.g. "mcp.example.org,mcp.example.org:443")
    turns it back on. Left unset, behaviour is unchanged — enabling it with an
    incomplete host list would reject every request with HTTP 421.
    """
    raw_value = os.environ.get("SNOWSTORM_MCP_ALLOWED_HOSTS")
    if not raw_value:
        return None

    from mcp.server.transport_security import TransportSecuritySettings

    allowed_hosts = [item.strip() for item in raw_value.split(",") if item.strip()]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allow_origins,
    )


def _reject_get_on_mcp_endpoint(asgi_app):
    """Answer GET on the MCP endpoint with 405 Method Not Allowed.

    MCP 2026-07-28 removed the standalone GET stream and tells servers to reject
    GET on the MCP endpoint. The SDK still honours it for 2025-era clients by
    opening an SSE stream that never terminates, so any unauthenticated caller
    could hold connections open indefinitely. This server registers only tools —
    no resources, prompts or subscriptions — so that stream never carries a
    message and refusing it costs nothing. Other paths (notably /favicon.ico)
    are untouched.
    """
    from starlette.responses import PlainTextResponse

    async def wrapped(scope, receive, send):
        if (
            scope["type"] == "http"
            and scope.get("method") == "GET"
            and scope.get("path", "").rstrip("/") == STREAMABLE_HTTP_PATH
        ):
            await PlainTextResponse("Method Not Allowed", status_code=405)(scope, receive, send)
            return
        await asgi_app(scope, receive, send)

    return wrapped


def _build_streamable_http_asgi(config_path: str | None):
    from starlette.middleware.cors import CORSMiddleware

    app = create_mcp_app(config_path)
    allow_origins = _cors_allow_origins()
    # stateless_http only governs the legacy (2025-era handshake) path: requests
    # carrying a modern MCP-Protocol-Version are routed to the stateless handler
    # by the SDK regardless. Enabling it keeps older clients from pinning
    # per-session transports in memory, which this server has no need for — it
    # exposes no sampling, elicitation, resources or subscriptions.
    asgi_app = _reject_get_on_mcp_endpoint(
        app.streamable_http_app(
            streamable_http_path=STREAMABLE_HTTP_PATH,
            stateless_http=True,
            host=os.environ.get("FASTMCP_HOST", "127.0.0.1"),
            transport_security=_transport_security(allow_origins),
        )
    )
    if not allow_origins:
        return asgi_app

    return CORSMiddleware(
        asgi_app,
        allow_origins=allow_origins,
        # DELETE terminated a session under the 2025-era transport; sessions no
        # longer exist. GET remains for the /favicon.ico custom route.
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=list(DEFAULT_CORS_ALLOW_HEADERS),
        expose_headers=list(DEFAULT_CORS_EXPOSE_HEADERS),
        allow_credentials=False,
    )


def main() -> None:
    import uvicorn

    load_dotenv(find_dotenv(usecwd=True))
    args = build_arg_parser().parse_args()
    configure_logging(getattr(logging, args.log_level), args.log_format)
    if args.transport == "streamable-http":
        asgi_app = _build_streamable_http_asgi(args.config)
        uvicorn.run(
            asgi_app,
            host=os.environ.get("FASTMCP_HOST", "127.0.0.1"),
            port=int(os.environ.get("FASTMCP_PORT", "8000")),
            log_level=args.log_level.lower(),
        )
        return

    app = create_mcp_app(args.config)
    app.run(transport=args.transport)


if __name__ == "__main__":
    main()
