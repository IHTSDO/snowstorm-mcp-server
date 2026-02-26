from __future__ import annotations

from typing import Any

import httpx
import pytest

from snowstorm_mcp_server.config import TargetConfig
from snowstorm_mcp_server.fhir import SnomedLookupService
from snowstorm_mcp_server.http_client import HttpClient, HttpRequestError


def _make_service(target: TargetConfig, handler) -> SnomedLookupService:
    raw = httpx.Client(transport=httpx.MockTransport(handler), base_url=target.base_url)
    return SnomedLookupService(target, client=HttpClient(target, client=raw))


def test_expand_uses_implicit_snomed_valueset_and_parses_contains() -> None:
    target = TargetConfig(base_url="http://test")
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["query"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={
                "resourceType": "ValueSet",
                "expansion": {
                    "identifier": "urn:uuid:test",
                    "total": 123,
                    "offset": 5,
                    "contains": [
                        {
                            "system": "http://snomed.info/sct",
                            "code": "22298006",
                            "display": "Myocardial infarction",
                        },
                        {
                            "system": "http://snomed.info/sct",
                            "code": "57054005",
                            "display": "Acute myocardial infarction",
                            "inactive": False,
                        },
                    ],
                },
            },
        )

    with _make_service(target, handler) as svc:
        result = svc.expand(filter="myocard", offset=5, count=10)

    assert seen["path"] == "/fhir/ValueSet/$expand"
    assert seen["query"]["url"] == "http://snomed.info/sct?fhir_vs"
    assert seen["query"]["filter"] == "myocard"
    assert seen["query"]["offset"] == "5"
    assert seen["query"]["count"] == "10"
    assert result.value_set_url == "http://snomed.info/sct?fhir_vs"
    assert result.total == 123
    assert result.offset == 5
    assert result.count == 10
    assert result.returned == 2
    assert result.truncated is False
    assert result.contains[0].code == "22298006"


def test_expand_summary_mode_omits_contains_but_reports_raw_count() -> None:
    target = TargetConfig(base_url="http://test")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "resourceType": "ValueSet",
                "expansion": {
                    "total": 5000,
                    "contains": [{"code": str(i)} for i in range(50)],
                },
            },
        )

    with _make_service(target, handler) as svc:
        result = svc.expand(summary_only=True, count=25)

    assert result.summary_only is True
    assert result.contains == []
    assert result.returned == 0
    assert result.raw_contains_count == 50
    assert result.truncated is False


def test_expand_respects_server_reported_zero_offset() -> None:
    target = TargetConfig(base_url="http://test")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "resourceType": "ValueSet",
                "expansion": {
                    "offset": 0,
                    "contains": [],
                },
            },
        )

    with _make_service(target, handler) as svc:
        result = svc.expand(offset=25, count=10)

    assert result.offset == 0


def test_expand_caps_contains_and_marks_truncated() -> None:
    target = TargetConfig(base_url="http://test")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "resourceType": "ValueSet",
                "expansion": {"contains": [{"code": str(i)} for i in range(20)]},
            },
        )

    with _make_service(target, handler) as svc:
        result = svc.expand(max_contains=5)

    assert result.returned == 5
    assert result.raw_contains_count == 20
    assert result.truncated is True


def test_expand_requires_valueset_response() -> None:
    target = TargetConfig(base_url="http://test")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"resourceType": "Parameters"})

    with _make_service(target, handler) as svc:
        with pytest.raises(HttpRequestError, match="ValueSet"):
            svc.expand()
