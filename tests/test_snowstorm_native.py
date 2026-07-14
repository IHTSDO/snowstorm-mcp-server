from __future__ import annotations

import json
from pathlib import Path

import pytest

from snowstorm_mcp_server.config import TargetConfig
from snowstorm_mcp_server.snowstorm_native import SnowstormNativeService


class _StubClient:
    def __init__(self) -> None:
        self.called = False
        self.calls: list[tuple[tuple, dict]] = []
        self.next_response = {"items": [], "totalElements": 0}

    def request(self, *_args, **_kwargs):
        self.called = True
        self.calls.append((_args, _kwargs))
        return self.next_response

    def close(self) -> None:
        return None


def _load_json_fixture(path: str) -> dict:
    fixture_path = Path(__file__).parent / "fixtures" / path
    return json.loads(fixture_path.read_text(encoding="utf-8"))


def test_snowstorm_native_search_short_term_returns_notice_not_error() -> None:
    target = TargetConfig(base_url="http://localhost:8080")
    stub = _StubClient()
    with SnowstormNativeService(target, client=stub) as svc:
        result = svc.search_concepts(term="AD")

    assert result.returned == 0
    assert result.hits == []
    assert result.notice and "at least 3 searchable characters" in result.notice
    assert stub.called is False, "short terms must not hit the backend"


def test_snowstorm_native_search_allows_three_char_term_before_backend_call() -> None:
    target = TargetConfig(base_url="http://localhost:8080")
    stub = _StubClient()
    with SnowstormNativeService(target, client=stub) as svc:
        assert svc.MIN_SEARCH_TERM_LENGTH == 3
        svc.search_concepts(term="A1C", limit=1)
        assert stub.called is True


def test_snowstorm_native_list_codesystems_parses_latest_version_summary() -> None:
    target = TargetConfig(base_url="http://localhost:8080")
    stub = _StubClient()
    stub.next_response = {
        "items": [
            {
                "shortName": "SNOMEDCT",
                "name": "International Edition",
                "branchPath": "MAIN",
                "latestVersion": {
                    "version": "20250131",
                    "effectiveDate": "20250131",
                },
            },
            {
                "shortName": "SNOMEDCT-US",
                "branchPath": "MAIN/SNOMEDCT-US",
                "latestVersion": "20240901",
                "latestVersionEffectiveDate": "20240901",
            },
            {"shortName": "", "branchPath": "MAIN"},
        ]
    }

    with SnowstormNativeService(target, client=stub) as svc:
        result = svc.list_codesystems()

    assert result.returned == 2
    assert result.code_systems[0].short_name == "SNOMEDCT"
    assert result.code_systems[0].latest_version == "20250131"
    assert result.code_systems[0].latest_effective_date == "20250131"
    assert result.code_systems[1].short_name == "SNOMEDCT-US"
    assert result.code_systems[1].latest_version == "20240901"


def test_snowstorm_native_list_versions_calls_versions_endpoint_and_parses_items() -> None:
    target = TargetConfig(base_url="http://localhost:8080")
    stub = _StubClient()
    stub.next_response = {
        "items": [
            {
                "version": "20251101",
                "effectiveDate": "20251101",
                "branchPath": "MAIN/2025-11-01",
                "importDate": "2025-11-02T01:23:45Z",
            },
            {
                "effectiveDate": "20240801",
                "description": "August 2024",
            },
        ]
    }

    with SnowstormNativeService(target, client=stub) as svc:
        result = svc.list_versions(code_system_short_name="SNOMEDCT")

    ((args, kwargs),) = stub.calls
    assert args[0] == "GET"
    assert args[1].endswith("/codesystems/SNOMEDCT/versions")
    assert kwargs["expect_json"] is True
    assert result.code_system_short_name == "SNOMEDCT"
    assert result.returned == 2
    assert result.versions[0].version == "20251101"
    assert result.versions[0].effective_date == "20251101"
    assert result.versions[1].version == "20240801"


def test_snowstorm_native_list_versions_rejects_blank_short_name() -> None:
    target = TargetConfig(base_url="http://localhost:8080")
    with SnowstormNativeService(target, client=_StubClient()) as svc:
        with pytest.raises(ValueError, match="must not be empty"):
            svc.list_versions(code_system_short_name="   ")


def test_snowstorm_native_list_versions_parses_real_snowstorm_payload_fixture() -> None:
    target = TargetConfig(base_url="http://localhost:8080")
    stub = _StubClient()
    stub.next_response = _load_json_fixture(
        "live_contracts/snowstorm/codesystems_snomedct_versions_20251101.json"
    )

    with SnowstormNativeService(target, client=stub) as svc:
        result = svc.list_versions(code_system_short_name="SNOMEDCT")

    assert result.returned >= 1
    first = result.versions[0]
    # Real Snowstorm payload uses version='2025-11-01' and effectiveDate=20251101.
    # We normalize to compact YYYYMMDD to make MCP output stable for callers/tests.
    assert first.version == "20251101"
    assert first.effective_date == "20251101"
    assert first.branch_path == "MAIN/2025-11-01"
    assert first.description and "20251101" in first.description
    assert first.import_date is not None


def test_snowstorm_native_list_codesystems_parses_real_snowstorm_payload_fixture() -> None:
    target = TargetConfig(base_url="http://localhost:8080")
    stub = _StubClient()
    stub.next_response = _load_json_fixture("live_contracts/snowstorm/codesystems_20251101.json")

    with SnowstormNativeService(target, client=stub) as svc:
        result = svc.list_codesystems()

    assert result.returned >= 1
    snomedct = next((c for c in result.code_systems if c.short_name == "SNOMEDCT"), None)
    assert snomedct is not None
    assert snomedct.branch_path == "MAIN"
    assert snomedct.latest_version == "20251101"
    assert snomedct.latest_effective_date == "20251101"


def test_snowstorm_native_search_parses_real_descriptions_payload_fixture() -> None:
    target = TargetConfig(base_url="http://localhost:8080")
    stub = _StubClient()
    stub.next_response = _load_json_fixture(
        "live_contracts/snowstorm/browser_main_descriptions_myocardial_infarction_20251101.json"
    )

    with SnowstormNativeService(target, client=stub) as svc:
        result = svc.search_concepts(term="myocardial infarction", branch="MAIN", limit=10)

    assert result.branch == "MAIN"
    assert result.total_elements == 659
    assert result.returned == 10
    concept_ids = {hit.concept_id for hit in result.hits}
    assert "22298006" in concept_ids
    assert result.hits[0].semantic_tag is not None
    top_text = " ".join(
        [result.hits[0].pt or "", result.hits[0].fsn or "", result.hits[0].matched_term or ""]
    ).lower()
    assert "myocardial infarction" in top_text


def test_snowstorm_native_get_concept_parses_real_payload_fixture() -> None:
    target = TargetConfig(base_url="http://localhost:8080")
    stub = _StubClient()
    stub.next_response = _load_json_fixture(
        "live_contracts/snowstorm/browser_main_concept_22298006_20251101.json"
    )

    with SnowstormNativeService(target, client=stub) as svc:
        detail = svc.get_concept(concept_id="22298006", branch="MAIN", max_synonyms=20)

    assert detail.concept_id == "22298006"
    assert detail.semantic_tag == "disorder"
    assert detail.fsn and "myocardial infarction" in detail.fsn.lower()
    assert detail.raw_description_count == 12
    assert any("heart attack" in s.lower() for s in detail.synonyms)
