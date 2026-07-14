"""Logging configuration for the MCP server.

Provides a JSON log formatter so errors are machine-parseable in production
deployments (one JSON object per line on stderr, with a UTC timestamp).
Fields passed via ``logger.error(..., extra={...})`` — e.g. ``error_code``,
``status_code`` — are included in the JSON output automatically.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

# Attributes present on every LogRecord. Anything else on the record was
# supplied via the `extra` parameter and should appear in the JSON output.
_STANDARD_ATTRS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()
) | {"message", "asctime", "taskName"}


class JsonLogFormatter(logging.Formatter):
    """Format each log record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: int, log_format: str = "json") -> None:
    """Set up root logging on stderr in either JSON or plain-text format."""
    handler = logging.StreamHandler(sys.stderr)
    if log_format == "json":
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
    logging.basicConfig(level=level, handlers=[handler], force=True)
