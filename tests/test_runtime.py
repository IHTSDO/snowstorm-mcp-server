from __future__ import annotations

import pytest

from snowstorm_mcp_server.capabilities import BackendType, Capabilities, TargetStatus
from snowstorm_mcp_server.config import AppConfig, TargetConfig
from snowstorm_mcp_server.runtime import ServerRuntime, UnsupportedBackendError
from snowstorm_mcp_server.terminology import TerminologyInfo, TerminologyRegistry


def _make_registry_and_target() -> tuple[TerminologyRegistry, TargetConfig]:
    target = TargetConfig(base_url="http://example.test")
    registry = TerminologyRegistry()
    registry.register(
        TerminologyInfo(
            name="snomedct",
            target_name="snowstorm",
            backend_type=BackendType.SNOWSTORM,
            branch_path="MAIN",
        ),
        target,
    )
    registry.set_default("snomedct")
    registry.set_target_status(
        "snowstorm",
        TargetStatus(
            reachable=True,
            base_url=target.base_url,
            fhir_base_url=target.fhir_base_url,
            capabilities=Capabilities(
                backend_type=BackendType.SNOWSTORM,
                has_fhir=True,
                has_native_api=True,
                fhir_metadata={
                    "resourceType": "CapabilityStatement",
                    "fhirVersion": "4.0.1",
                    "status": "active",
                    "kind": "instance",
                    "software": {"name": "Snowstorm", "version": "10.6.0"},
                },
            ),
        ),
    )
    return registry, target


def test_fhir_metadata_returns_summary_and_raw_by_default(monkeypatch) -> None:
    registry, target = _make_registry_and_target()

    from snowstorm_mcp_server import runtime as runtime_module

    monkeypatch.setattr(runtime_module, "build_registry", lambda _cfg: registry)
    app = AppConfig(targets={"snowstorm": target})
    server = ServerRuntime(app)

    payload = server.fhir_metadata()

    assert payload["terminology"] == "snomedct"
    assert payload["fhir_base_url"] == "http://example.test/fhir"
    assert payload["summary"]["fhirVersion"] == "4.0.1"
    assert payload["summary"]["software_name"] == "Snowstorm"
    assert payload["metadata"]["resourceType"] == "CapabilityStatement"


def test_fhir_metadata_can_omit_raw_payload(monkeypatch) -> None:
    registry, target = _make_registry_and_target()

    from snowstorm_mcp_server import runtime as runtime_module

    monkeypatch.setattr(runtime_module, "build_registry", lambda _cfg: registry)
    app = AppConfig(targets={"snowstorm": target})
    server = ServerRuntime(app)

    payload = server.fhir_metadata(include_raw=False)

    assert payload["summary"]["fhirVersion"] == "4.0.1"
    assert "metadata" not in payload


def test_runtime_snomed_expand_delegates_and_adds_terminology(monkeypatch) -> None:
    registry, target = _make_registry_and_target()

    from snowstorm_mcp_server import runtime as runtime_module

    monkeypatch.setattr(runtime_module, "build_registry", lambda _cfg: registry)

    class _StubFhirService:
        def __init__(self, _target) -> None:
            self.target = _target

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def expand(self, **kwargs):
            assert kwargs["count"] == 25
            assert kwargs["summary_only"] is True
            assert kwargs["max_contains"] == 3
            assert kwargs["semantic_enabled"] is True
            assert kwargs["semantic_mode"] == "rerank"
            assert kwargs["semantic_provider"] == "http"

            class _Result:
                def model_dump(self):
                    return {
                        "value_set_url": "http://snomed.info/sct?fhir_vs",
                        "count": 25,
                        "summary_only": True,
                        "returned": 0,
                    }

            return _Result()

    monkeypatch.setattr(runtime_module, "SnomedLookupService", _StubFhirService)
    server = ServerRuntime(
        AppConfig(targets={"snowstorm": target}, response_limits={"max_expand_contains": 3})
    )

    payload = server.snomed_expand(
        count=25,
        summary_only=True,
        max_contains=50,
        semantic_enabled=True,
        semantic_mode="rerank",
        semantic_provider="http",
    )

    assert payload["terminology"] == "snomedct"
    assert payload["summary_only"] is True
    assert payload["count"] == 25


def test_runtime_snowstorm_list_versions_delegates(monkeypatch) -> None:
    registry, target = _make_registry_and_target()

    from snowstorm_mcp_server import runtime as runtime_module

    monkeypatch.setattr(runtime_module, "build_registry", lambda _cfg: registry)

    class _StubNativeService:
        def __init__(self, _target) -> None:
            self.target = _target

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def list_versions(self, *, code_system_short_name: str):
            assert code_system_short_name == "SNOMEDCT"

            class _Result:
                def model_dump(self):
                    return {
                        "code_system_short_name": "SNOMEDCT",
                        "returned": 1,
                        "versions": [{"version": "20251101"}],
                    }

            return _Result()

    monkeypatch.setattr(runtime_module, "SnowstormNativeService", _StubNativeService)
    server = ServerRuntime(AppConfig(targets={"snowstorm": target}))

    payload = server.snowstorm_list_versions(code_system_short_name="SNOMEDCT")

    assert payload["terminology"] == "snomedct"
    assert payload["versions"][0]["version"] == "20251101"


def test_runtime_snowstorm_list_codesystems_rejects_lite_backend(monkeypatch) -> None:
    target = TargetConfig(base_url="http://example.test", mode="lite", terminology_name="snomedct")
    registry = TerminologyRegistry()
    registry.register(
        TerminologyInfo(
            name="snomedct",
            target_name="lite",
            backend_type=BackendType.LITE,
            branch_path=None,
        ),
        target,
    )
    registry.set_default("snomedct")
    registry.set_target_status(
        "lite",
        TargetStatus(
            reachable=True,
            base_url=target.base_url,
            fhir_base_url=target.fhir_base_url,
            capabilities=Capabilities(
                backend_type=BackendType.LITE,
                has_fhir=True,
                has_native_api=False,
                has_lite_load_package=True,
            ),
        ),
    )

    from snowstorm_mcp_server import runtime as runtime_module

    monkeypatch.setattr(runtime_module, "build_registry", lambda _cfg: registry)
    server = ServerRuntime(AppConfig(targets={"lite": target}))

    with pytest.raises(UnsupportedBackendError, match="does not support Snowstorm native code system listing"):
        server.snowstorm_list_codesystems()


def test_runtime_search_limit_is_capped_by_config(monkeypatch) -> None:
    registry, target = _make_registry_and_target()

    from snowstorm_mcp_server import runtime as runtime_module

    monkeypatch.setattr(runtime_module, "build_registry", lambda _cfg: registry)

    class _StubNativeService:
        def __init__(self, _target) -> None:
            self.target = _target

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def search_concepts(self, **kwargs):
            assert kwargs["limit"] == 2

            class _Result:
                def model_dump(self):
                    return {
                        "term": "myocardial infarction",
                        "branch": "MAIN",
                        "limit": 2,
                        "returned": 0,
                        "hits": [],
                    }

            return _Result()

    monkeypatch.setattr(runtime_module, "SnowstormNativeService", _StubNativeService)
    server = ServerRuntime(AppConfig(targets={"snowstorm": target}, response_limits={"max_search_hits": 2}))

    payload = server.snowstorm_search_concepts(term="myocardial infarction", limit=20)

    assert payload["terminology"] == "snomedct"
    assert payload["limit"] == 2
