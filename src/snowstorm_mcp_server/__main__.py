from __future__ import annotations

import argparse
import logging
import sys

from dotenv import find_dotenv, load_dotenv

from .mcp_app import create_mcp_app


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
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    return parser


def main() -> None:
    load_dotenv(find_dotenv(usecwd=True))
    args = build_arg_parser().parse_args()
    logging.basicConfig(
        stream=sys.stderr,
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = create_mcp_app(args.config)
    app.run(transport=args.transport)


if __name__ == "__main__":
    main()
