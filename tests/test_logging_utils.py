from __future__ import annotations

import json
import logging

from snowstorm_mcp_server.logging_utils import JsonLogFormatter


def _make_record(
    level: int = logging.ERROR,
    msg: str = "something failed",
    args: tuple = (),
    exc_info=None,
    extra: dict | None = None,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name="snowstorm_mcp_server.mcp_app",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=exc_info,
    )
    for key, value in (extra or {}).items():
        setattr(record, key, value)
    return record


class TestJsonLogFormatter:
    def test_basic_fields(self):
        payload = json.loads(JsonLogFormatter().format(_make_record()))
        assert payload["level"] == "ERROR"
        assert payload["logger"] == "snowstorm_mcp_server.mcp_app"
        assert payload["message"] == "something failed"

    def test_timestamp_is_utc_iso8601(self):
        payload = json.loads(JsonLogFormatter().format(_make_record()))
        assert payload["timestamp"].endswith("Z")
        assert "T" in payload["timestamp"]

    def test_message_args_are_interpolated(self):
        record = _make_record(msg="HTTP %d for %s", args=(500, "GET /fhir"))
        payload = json.loads(JsonLogFormatter().format(record))
        assert payload["message"] == "HTTP 500 for GET /fhir"

    def test_extra_fields_are_included(self):
        record = _make_record(extra={"error_code": "E_BACKEND_HTTP", "status_code": 502})
        payload = json.loads(JsonLogFormatter().format(record))
        assert payload["error_code"] == "E_BACKEND_HTTP"
        assert payload["status_code"] == 502

    def test_exception_traceback_is_included(self):
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            import sys

            record = _make_record(exc_info=sys.exc_info())
        payload = json.loads(JsonLogFormatter().format(record))
        assert "RuntimeError: boom" in payload["exception"]

    def test_output_is_single_line(self):
        record = _make_record(msg="line one\nline two")
        assert "\n" not in JsonLogFormatter().format(record)

    def test_non_serialisable_extra_falls_back_to_str(self):
        record = _make_record(extra={"session": object()})
        payload = json.loads(JsonLogFormatter().format(record))
        assert "object object at" in payload["session"]
