from __future__ import annotations

import json
from typing import Any
from pathlib import Path

import httpx
import pytest

from snowstorm_mcp_server.config import TargetConfig
from snowstorm_mcp_server.fhir import SnomedLookupService
from snowstorm_mcp_server.http_client import HttpClient, HttpRequestError


def _make_service(target: TargetConfig, handler) -> SnomedLookupService:
    raw = httpx.Client(transport=httpx.MockTransport(handler), base_url=target.base_url)
    return SnomedLookupService(target, client=HttpClient(target, client=raw))


def _load_json_fixture(path: str) -> dict:
    fixture_path = Path(__file__).parent / "fixtures" / path
    return json.loads(fixture_path.read_text(encoding="utf-8"))


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


def test_lookup_parses_real_parameters_payload_fixture() -> None:
    target = TargetConfig(base_url="http://test")
    payload = _load_json_fixture("live_contracts/fhir/codesystem_lookup_404684003_20251101.json")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with _make_service(target, handler) as svc:
        result = svc.lookup(code="404684003")

    assert result.code == "404684003"
    assert result.system == "http://snomed.info/sct"
    assert result.version and "20251101" in result.version
    assert result.display and "clinical finding" in result.display.lower()
    assert "display" in result.raw_parameters


def test_validate_code_parses_real_valid_payload_fixture() -> None:
    target = TargetConfig(base_url="http://test")
    payload = _load_json_fixture("live_contracts/fhir/codesystem_validate_code_404684003_valid_20251101.json")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with _make_service(target, handler) as svc:
        result = svc.validate_code(code="404684003")

    assert result.code == "404684003"
    assert result.result is True
    assert result.display and "clinical finding" in result.display.lower()
    assert result.system == "http://snomed.info/sct"
    assert result.version and "20251101" in result.version


def test_validate_code_parses_real_invalid_payload_fixture() -> None:
    target = TargetConfig(base_url="http://test")
    payload = _load_json_fixture("live_contracts/fhir/codesystem_validate_code_invalid_20251101.json")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with _make_service(target, handler) as svc:
        result = svc.validate_code(code="999999999999999999")

    assert result.code == "999999999999999999"
    assert result.result is False
    assert result.system == "http://snomed.info/sct"
    assert result.version and "20251101" in result.version
    assert result.message and "not found" in result.message.lower()


def test_validate_code_uses_get_with_query_params() -> None:
    # Regression: some Snowstorm deployments sit behind a proxy that rejects
    # POST with HTTP 405 while allowing GET. $validate-code must use the GET
    # form (url/code/version as query params), like $lookup and $subsumes.
    target = TargetConfig(base_url="http://test")
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["query"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={
                "resourceType": "Parameters",
                "parameter": [{"name": "result", "valueBoolean": True}],
            },
        )

    with _make_service(target, handler) as svc:
        svc.validate_code(code="404684003", version="http://snomed.info/sct/version/20251101")

    assert seen["method"] == "GET"
    assert seen["path"].endswith("/CodeSystem/$validate-code")
    assert seen["query"]["url"] == "http://snomed.info/sct"
    assert seen["query"]["code"] == "404684003"
    assert seen["query"]["version"] == "http://snomed.info/sct/version/20251101"


def test_subsumes_parses_real_parameters_payload_fixture() -> None:
    target = TargetConfig(base_url="http://test")
    payload = _load_json_fixture("live_contracts/fhir/codesystem_subsumes_22298006_57054005_20251101.json")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with _make_service(target, handler) as svc:
        result = svc.subsumes(code_a="22298006", code_b="57054005")

    assert result.code_a == "22298006"
    assert result.code_b == "57054005"
    assert result.outcome == "subsumes"
    assert result.system == "http://snomed.info/sct"
    assert result.version and "20251101" in result.version


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


def test_lookup_unknown_code_returns_found_false_not_error() -> None:
    target = TargetConfig(base_url="http://test")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={
                "resourceType": "OperationOutcome",
                "issue": [
                    {
                        "severity": "error",
                        "code": "not-found",
                        "diagnostics": "Code '99999999999' not found for system "
                        "'http://snomed.info/sct'.",
                    }
                ],
            },
        )

    with _make_service(target, handler) as svc:
        result = svc.lookup(code="99999999999")

    assert result.found is False
    assert result.code == "99999999999"
    assert result.display is None
    assert result.message and "not found" in result.message.lower()


def test_lookup_backend_failure_still_raises() -> None:
    target = TargetConfig(base_url="http://test")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    with _make_service(target, handler) as svc:
        with pytest.raises(HttpRequestError):
            svc.lookup(code="22298006")


def test_lookup_known_code_reports_found_true() -> None:
    target = TargetConfig(base_url="http://test")
    payload = _load_json_fixture("live_contracts/fhir/codesystem_lookup_404684003_20251101.json")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with _make_service(target, handler) as svc:
        result = svc.lookup(code="404684003")

    assert result.found is True
    assert result.message is None


def test_validate_code_falls_back_to_lookup_when_not_supported() -> None:
    target = TargetConfig(base_url="http://test")
    lookup_payload = _load_json_fixture("live_contracts/fhir/codesystem_lookup_404684003_20251101.json")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/CodeSystem/$validate-code"):
            return httpx.Response(400, text="OperationOutcome: not-supported")
        if request.url.path.endswith("/CodeSystem/$lookup"):
            return httpx.Response(200, json=lookup_payload)
        return httpx.Response(404)

    with _make_service(target, handler) as svc:
        result = svc.validate_code(code="404684003")

    assert result.result is True
    assert result.display and "clinical finding" in result.display.lower()
    assert result.message and "lookup fallback" in result.message.lower()


def test_validate_code_fallback_maps_lookup_not_found_to_false() -> None:
    target = TargetConfig(base_url="http://test")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/CodeSystem/$validate-code"):
            return httpx.Response(400, text="OperationOutcome: not-supported")
        if request.url.path.endswith("/CodeSystem/$lookup"):
            return httpx.Response(404, text="not found")
        return httpx.Response(404)

    with _make_service(target, handler) as svc:
        result = svc.validate_code(code="999999999999999999")

    assert result.result is False
    assert result.code == "999999999999999999"
    assert result.message and "lookup fallback" in result.message.lower()


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


def test_expand_parses_real_valueset_expand_payload_fixture() -> None:
    target = TargetConfig(base_url="http://test")
    payload = _load_json_fixture(
        "live_contracts/fhir/valueset_expand_snomed_myocardial_infarction_20251101.json"
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with _make_service(target, handler) as svc:
        result = svc.expand(filter="myocardial infarction", count=10)

    assert result.offset == 0
    assert result.count == 10
    assert result.total is not None and result.total >= 10
    assert result.returned == 10
    assert result.raw_contains_count == 10
    assert result.truncated is False
    codes = {item.code for item in result.contains}
    assert "22298006" in codes


def test_expand_fuzzy_appends_tilde_to_filter() -> None:
    target = TargetConfig(base_url="http://test")
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["query"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={"resourceType": "ValueSet", "expansion": {"contains": []}},
        )

    with _make_service(target, handler) as svc:
        svc.expand(filter="myocardal", fuzzy=True)

    assert seen["query"]["filter"] == "myocardal~"


def test_expand_no_fuzzy_does_not_append_tilde() -> None:
    target = TargetConfig(base_url="http://test")
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["query"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={"resourceType": "ValueSet", "expansion": {"contains": []}},
        )

    with _make_service(target, handler) as svc:
        svc.expand(filter="myocardial", fuzzy=False)

    assert seen["query"]["filter"] == "myocardial"
