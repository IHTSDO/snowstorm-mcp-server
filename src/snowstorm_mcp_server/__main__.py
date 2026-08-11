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


def _security_allowed_origins(cors_allow_origins: list[str]) -> list[str] | None:
    """Origin allow list for the anti-DNS-rebinding check; None disables it.

    Defaults to the CORS allow list: an origin that cannot pass CORS cannot
    read responses anyway, so rejecting it outright breaks no legitimate
    browser client. SNOWSTORM_MCP_ALLOWED_ORIGINS overrides the list without
    touching CORS (e.g. same-origin deployments behind a reverse proxy, where
    CORS is off but browser POSTs still carry an Origin). It replaces the list
    rather than extending it. A literal "*" disables the app-level check; the
    SDK-level check still runs if SNOWSTORM_MCP_ALLOWED_HOSTS is set, matching
    Origin against the CORS list.

    Note the empty case is deliberate and fail-closed: SNOWSTORM_MCP_CORS_ALLOW_ORIGINS=""
    with no override yields an empty list, so every request carrying any Origin
    is rejected. Operators disabling CORS on a browser-facing deployment must
    set this variable.
    """
    raw_value = os.environ.get("SNOWSTORM_MCP_ALLOWED_ORIGINS")
    if raw_value is None:
        return list(cors_allow_origins)
    items = [item.strip() for item in raw_value.split(",") if item.strip()]
    if "*" in items:
        return None
    return items


def _reject_untrusted_origin(asgi_app, allowed_origins: list[str]):
    """403 requests to the MCP endpoint whose Origin is present but not allowed.

    The MCP transport spec requires servers to validate Origin to prevent DNS
    rebinding. The SDK couples that check to Host validation behind one flag,
    and its Host matcher has no full wildcard — so with an unknowable public
    hostname (a container binding 0.0.0.0) the SDK check cannot be enabled
    without an operator-supplied host list, and historically it was simply off.

    Origin alone is sufficient here: the endpoint is POST-only (GET is 405'd
    below), browsers append Origin to every POST — including the same-origin
    POSTs a rebound page issues, where it names the attacker's own origin —
    and non-browser clients send no Origin and pass untouched. Matching
    mirrors the SDK: exact values plus "scheme://host:*" port wildcards.

    Origin: null (sandboxed iframe, no-referrer policy, cross-origin redirect)
    is a present-but-unlisted value and so is rejected, which is the safe
    outcome. Firefox before 103 could omit the header entirely instead; those
    clients need SNOWSTORM_MCP_ALLOWED_HOSTS to be covered.
    """
    from starlette.responses import PlainTextResponse
    from starlette.routing import get_route_path

    def _origin_allowed(origin: str) -> bool:
        if origin in allowed_origins:
            return True
        return any(
            allowed.endswith(":*") and origin.startswith(allowed[:-2] + ":")
            for allowed in allowed_origins
        )

    async def wrapped(scope, receive, send):
        if (
            scope["type"] == "http"
            and get_route_path(scope).rstrip("/") == STREAMABLE_HTTP_PATH
        ):
            origin = next(
                (
                    value.decode("latin-1")
                    for name, value in scope.get("headers", [])
                    if name == b"origin"
                ),
                None,
            )
            if origin is not None and not _origin_allowed(origin):
                response = PlainTextResponse("Invalid Origin header", status_code=403)
                await response(scope, receive, send)
                return
        await asgi_app(scope, receive, send)

    return wrapped


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

    Matching uses get_route_path, not scope["path"]. ASGI's scope["path"] is the
    full request path including any mount prefix, so behind `--root-path /api` or
    a Starlette Mount it reads "/api/mcp" and a naive comparison against "/mcp"
    silently stops matching — reopening the unbounded SSE stream this exists to
    close, while 405-ing a "/mcp" that no longer routes anywhere.
    """
    from starlette.responses import PlainTextResponse
    from starlette.routing import get_route_path

    async def wrapped(scope, receive, send):
        if (
            scope["type"] == "http"
            and scope.get("method") == "GET"
            and get_route_path(scope).rstrip("/") == STREAMABLE_HTTP_PATH
        ):
            # RFC 9110 15.5.6: a 405 MUST carry Allow.
            response = PlainTextResponse(
                "Method Not Allowed", status_code=405, headers={"Allow": "POST"}
            )
            await response(scope, receive, send)
            return
        await asgi_app(scope, receive, send)

    return wrapped


def _build_streamable_http_asgi(config_path: str | None):
    from starlette.middleware.cors import CORSMiddleware

    app = create_mcp_app(config_path)
    allow_origins = _cors_allow_origins()
    security_origins = _security_allowed_origins(allow_origins)
    bind_host = os.environ.get("FASTMCP_HOST", "127.0.0.1")
    if bind_host not in ("127.0.0.1", "localhost", "::1") and not os.environ.get(
        "SNOWSTORM_MCP_ALLOWED_HOSTS"
    ):
        logging.getLogger(__name__).warning(
            "Bound to %s without SNOWSTORM_MCP_ALLOWED_HOSTS: Host-header "
            "validation is off. Origin validation still protects browser "
            "traffic; set SNOWSTORM_MCP_ALLOWED_HOSTS to the public hostnames "
            "for defence in depth.",
            bind_host,
        )
    # stateless_http only governs the legacy (2025-era handshake) path: requests
    # carrying a modern MCP-Protocol-Version are routed to the stateless handler
    # by the SDK regardless. Enabling it keeps older clients from pinning
    # per-session transports in memory, which this server has no need for — it
    # exposes no sampling, elicitation, resources or subscriptions.
    asgi_app = _reject_get_on_mcp_endpoint(
        app.streamable_http_app(
            streamable_http_path=STREAMABLE_HTTP_PATH,
            stateless_http=True,
            host=bind_host,
            # The SDK check validates Origin against this list too, so feed it
            # the same list the app-level check uses (falling back to the CORS
            # list when the origin check is disabled with "*").
            transport_security=_transport_security(
                security_origins if security_origins is not None else allow_origins
            ),
        )
    )
    if security_origins is not None:
        asgi_app = _reject_untrusted_origin(asgi_app, security_origins)
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
