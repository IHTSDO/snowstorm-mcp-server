from __future__ import annotations

import argparse
import logging
import os
import sys

from dotenv import find_dotenv, load_dotenv

from .mcp_app import create_mcp_app

DEFAULT_CORS_ALLOW_ORIGINS = ("https://claude.ai", "https://claude.com")
DEFAULT_CORS_ALLOW_HEADERS = (
    "Accept",
    "Authorization",
    "Content-Type",
    "Last-Event-ID",
    "MCP-Protocol-Version",
    "MCP-Session-Id",
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
        choices=["stdio", "sse", "streamable-http"],
        help="MCP transport to run",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        type=str.upper,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    return parser


def _cors_allow_origins() -> list[str]:
    raw_value = os.environ.get("SNOWSTORM_MCP_CORS_ALLOW_ORIGINS")
    if raw_value is None:
        return list(DEFAULT_CORS_ALLOW_ORIGINS)
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def _build_streamable_http_asgi(config_path: str | None):
    from starlette.middleware.cors import CORSMiddleware

    app = create_mcp_app(config_path)
    asgi_app = app.streamable_http_app()
    allow_origins = _cors_allow_origins()
    if not allow_origins:
        return asgi_app

    return CORSMiddleware(
        asgi_app,
        allow_origins=allow_origins,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=list(DEFAULT_CORS_ALLOW_HEADERS),
        expose_headers=list(DEFAULT_CORS_EXPOSE_HEADERS),
        allow_credentials=False,
    )


def main() -> None:
    import uvicorn

    load_dotenv(find_dotenv(usecwd=True))
    args = build_arg_parser().parse_args()
    logging.basicConfig(
        stream=sys.stderr,
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
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
