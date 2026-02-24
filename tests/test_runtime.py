from __future__ import annotations

from snowstorm_mcp_server.capabilities import BackendType, Capabilities, TargetStatus
from snowstorm_mcp_server.config import AppConfig, TargetConfig
from snowstorm_mcp_server.runtime import ServerRuntime
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
