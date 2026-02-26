from __future__ import annotations

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


def test_snowstorm_native_search_rejects_short_term_with_clear_message() -> None:
    target = TargetConfig(base_url="http://localhost:8080")
    with SnowstormNativeService(target, client=_StubClient()) as svc:
        with pytest.raises(ValueError, match="at least 3 searchable characters"):
            svc.search_concepts(term="AD")


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
