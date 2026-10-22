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
        def __init__(self, _target, *, client=None) -> None:
            self.target = _target

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def expand(self, **kwargs):
            assert kwargs["count"] == 25
            assert kwargs["summary_only"] is True
            assert kwargs["max_contains"] == 3

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

    payload = server.snomed_expand(count=25, summary_only=True, max_contains=50)

    assert payload["terminology"] == "snomedct"
    assert payload["summary_only"] is True
    assert payload["count"] == 25


def test_runtime_snowstorm_list_versions_delegates(monkeypatch) -> None:
    registry, target = _make_registry_and_target()

    from snowstorm_mcp_server import runtime as runtime_module

    monkeypatch.setattr(runtime_module, "build_registry", lambda _cfg: registry)

    class _StubNativeService:
        def __init__(self, _target, *, client=None) -> None:
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
    server = ServerRuntime(AppConfig(server_mode="lite", targets={"lite": target}))

    with pytest.raises(UnsupportedBackendError, match="does not support Snowstorm native code system listing"):
        server.snowstorm_list_codesystems()


def test_runtime_search_limit_is_capped_by_config(monkeypatch) -> None:
    registry, target = _make_registry_and_target()

    from snowstorm_mcp_server import runtime as runtime_module

    monkeypatch.setattr(runtime_module, "build_registry", lambda _cfg: registry)

    class _StubNativeService:
        def __init__(self, _target, *, client=None) -> None:
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


def _make_pending_discovery_registry() -> tuple[TerminologyRegistry, TargetConfig]:
    target = TargetConfig(base_url="http://example.test")
    registry = TerminologyRegistry()
    registry.register_target("snowstorm", target)
    registry.set_target_status(
        "snowstorm",
        TargetStatus(
            reachable=True,
            base_url=target.base_url,
            fhir_base_url=target.fhir_base_url,
            capabilities=Capabilities(backend_type=BackendType.SNOWSTORM),
        ),
    )
    registry.record_discovery_error("snowstorm", "boom")
    registry.mark_discovery_pending("snowstorm", target)
    return registry, target


def _recovered_terminology(target_name: str) -> TerminologyInfo:
    return TerminologyInfo(
        name="snomedct",
        target_name=target_name,
        backend_type=BackendType.SNOWSTORM,
        branch_path="MAIN",
    )


def test_runtime_list_terminologies_retries_pending_discovery(monkeypatch) -> None:
    registry, target = _make_pending_discovery_registry()

    from snowstorm_mcp_server import runtime as runtime_module
    from snowstorm_mcp_server import terminology

    monkeypatch.setattr(runtime_module, "build_registry", lambda _cfg: registry)
    monkeypatch.setattr(
        terminology,
        "discover_snowstorm_terminologies",
        lambda target_name, _target, *, client=None: [_recovered_terminology(target_name)],
    )
    server = ServerRuntime(AppConfig(targets={"snowstorm": target}))

    payload = server.list_terminologies()

    assert [t["name"] for t in payload] == ["snomedct"]
    assert server.registry.default_terminology == "snomedct"
    assert server.registry.discovery_errors == []


def test_runtime_resolve_retries_pending_discovery(monkeypatch) -> None:
    registry, target = _make_pending_discovery_registry()

    from snowstorm_mcp_server import runtime as runtime_module
    from snowstorm_mcp_server import terminology

    monkeypatch.setattr(runtime_module, "build_registry", lambda _cfg: registry)
    monkeypatch.setattr(
        terminology,
        "discover_snowstorm_terminologies",
        lambda target_name, _target, *, client=None: [_recovered_terminology(target_name)],
    )
    server = ServerRuntime(AppConfig(targets={"snowstorm": target}))

    info, target_cfg = server._resolve(None)

    assert info.name == "snomedct"
    assert target_cfg is target


def test_runtime_discovery_retry_is_throttled(monkeypatch) -> None:
    import time

    from snowstorm_mcp_server.terminology import TerminologyNotFoundError

    registry, target = _make_pending_discovery_registry()

    from snowstorm_mcp_server import runtime as runtime_module
    from snowstorm_mcp_server import terminology

    monkeypatch.setattr(runtime_module, "build_registry", lambda _cfg: registry)
    calls: list[str] = []

    def counting_discover(target_name, _target, *, client=None):
        calls.append(target_name)
        return [_recovered_terminology(target_name)]

    monkeypatch.setattr(terminology, "discover_snowstorm_terminologies", counting_discover)
    server = ServerRuntime(AppConfig(targets={"snowstorm": target}))
    # Simulate a retry having just run so the throttle window is active.
    server._last_discovery_retry = time.monotonic()

    with pytest.raises(TerminologyNotFoundError):
        server._resolve(None)
    assert calls == []


def _edition_registry() -> tuple[TerminologyRegistry, TargetConfig]:
    registry, target = _make_registry_and_target()
    registry.register(
        TerminologyInfo(
            name="snomedct-ee",
            target_name="snowstorm",
            backend_type=BackendType.SNOWSTORM,
            branch_path="MAIN/SNOMEDCT-EE",
            edition_uri="http://snomed.info/sct/11000181102",
            accept_language="et-X-71000181105,en",
            language_refsets=["71000181105"],
        ),
        target,
    )
    return registry, target


def test_runtime_native_calls_send_the_terminology_accept_language(monkeypatch) -> None:
    registry, target = _edition_registry()

    from snowstorm_mcp_server import runtime as runtime_module

    monkeypatch.setattr(runtime_module, "build_registry", lambda _cfg: registry)
    seen: dict[str, object] = {}

    class _Result:
        def model_dump(self):
            return {}

    class _StubNativeService:
        def __init__(self, _target, *, client=None) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def search_concepts(self, **kwargs):
            seen["search"] = kwargs["accept_language"]
            return _Result()

        def get_concept(self, **kwargs):
            seen["concept"] = kwargs["accept_language"]
            return _Result()

    monkeypatch.setattr(runtime_module, "SnowstormNativeService", _StubNativeService)
    server = ServerRuntime(AppConfig(targets={"snowstorm": target}))
    monkeypatch.setattr(server, "_ensure_native_supported", lambda *_args: None)

    server.snowstorm_search_concepts(terminology="snomedct-ee", term="suhkurtõbi")
    server.snowstorm_get_concept_native(terminology="snomedct-ee", concept_id="73211009")

    assert seen == {"search": "et-X-71000181105,en", "concept": "et-X-71000181105,en"}


@pytest.mark.parametrize(
    ("explicit_version", "expected_version"),
    [
        (None, "http://snomed.info/sct/11000181102"),
        (
            "http://snomed.info/sct/11000181102/version/20251130",
            "http://snomed.info/sct/11000181102/version/20251130",
        ),
    ],
)
def test_runtime_fhir_calls_default_to_the_terminology_edition(
    monkeypatch, explicit_version, expected_version
) -> None:
    registry, target = _edition_registry()

    from snowstorm_mcp_server import runtime as runtime_module

    monkeypatch.setattr(runtime_module, "build_registry", lambda _cfg: registry)
    seen: dict[str, object] = {}

    class _Result:
        def model_dump(self):
            return {}

    class _StubFhirService:
        def __init__(self, _target, *, client=None) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def lookup(self, **kwargs):
            seen["lookup"] = kwargs["version"]
            return _Result()

        def validate_code(self, **kwargs):
            seen["validate_code"] = kwargs["version"]
            return _Result()

        def subsumes(self, **kwargs):
            seen["subsumes"] = kwargs["version"]
            return _Result()

        def expand(self, **kwargs):
            seen["expand"] = kwargs["edition_uri"]
            return _Result()

    monkeypatch.setattr(runtime_module, "SnomedLookupService", _StubFhirService)
    server = ServerRuntime(AppConfig(targets={"snowstorm": target}))

    server.snomed_lookup(terminology="snomedct-ee", code="32684000", version=explicit_version)
    server.snomed_validate_code(terminology="snomedct-ee", code="32684000", version=explicit_version)
    server.snomed_subsumes(
        terminology="snomedct-ee", code_a="1", code_b="2", version=explicit_version
    )
    server.snomed_expand(terminology="snomedct-ee")

    assert seen["lookup"] == expected_version
    assert seen["validate_code"] == expected_version
    assert seen["subsumes"] == expected_version
    assert seen["expand"] == "http://snomed.info/sct/11000181102"


def test_runtime_fhir_calls_leave_version_unset_without_edition(monkeypatch) -> None:
    registry, target = _make_registry_and_target()

    from snowstorm_mcp_server import runtime as runtime_module

    monkeypatch.setattr(runtime_module, "build_registry", lambda _cfg: registry)
    seen: dict[str, object] = {}

    class _StubFhirService:
        def __init__(self, _target, *, client=None) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def lookup(self, **kwargs):
            seen["version"] = kwargs["version"]

            class _Result:
                def model_dump(self):
                    return {}

            return _Result()

    monkeypatch.setattr(runtime_module, "SnomedLookupService", _StubFhirService)
    server = ServerRuntime(AppConfig(targets={"snowstorm": target}))

    server.snomed_lookup(code="404684003")

    assert seen["version"] is None


def _lookup_acceptability_runtime(monkeypatch, *, found=True, native_error=None, has_native_api=True):
    registry, target = _edition_registry()
    if not has_native_api:
        status = registry.get_target_status("snowstorm")
        status.capabilities.has_native_api = False

    from snowstorm_mcp_server import runtime as runtime_module
    from snowstorm_mcp_server.fhir import LookupResult
    from snowstorm_mcp_server.snowstorm_native import ConceptDetail, DescriptionSummary

    monkeypatch.setattr(runtime_module, "build_registry", lambda _cfg: registry)
    native_calls: list[dict[str, object]] = []

    class _StubFhirService:
        def __init__(self, _target, *, client=None) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def lookup(self, **kwargs):
            return LookupResult(code=kwargs["code"], found=found)

    class _StubNativeService(_StubFhirService):
        def get_concept(self, **kwargs):
            native_calls.append(kwargs)
            if native_error is not None:
                raise native_error
            return ConceptDetail(
                concept_id=kwargs["concept_id"],
                descriptions=[DescriptionSummary(
                    term="Infarkt", lang="et", type="SYNONYM",
                    acceptability={"71000181105": "PREFERRED"}, edition_acceptability="PREFERRED",
                )],
            )

    monkeypatch.setattr(runtime_module, "SnomedLookupService", _StubFhirService)
    monkeypatch.setattr(runtime_module, "SnowstormNativeService", _StubNativeService)
    return ServerRuntime(AppConfig(targets={"snowstorm": target})), native_calls


def test_runtime_lookup_skips_acceptability_unless_asked(monkeypatch) -> None:
    server, native_calls = _lookup_acceptability_runtime(monkeypatch)

    result = server.snomed_lookup(terminology="snomedct-ee", code="55641003")

    assert native_calls == []
    assert "descriptions" not in result


def test_runtime_lookup_adds_acceptability_from_the_edition_branch(monkeypatch) -> None:
    server, native_calls = _lookup_acceptability_runtime(monkeypatch)

    result = server.snomed_lookup(
        terminology="snomedct-ee", code="55641003", include_acceptability=True,
    )

    assert native_calls[0]["branch"] == "MAIN/SNOMEDCT-EE"
    assert native_calls[0]["language_refsets"] == ["71000181105"]
    assert native_calls[0]["accept_language"] == "et-X-71000181105,en"
    assert result["descriptions"][0]["edition_acceptability"] == "PREFERRED"
    assert result["edition_language_refsets"] == ["71000181105"]
    assert "descriptions_notice" not in result


def test_runtime_lookup_acceptability_notes_a_requested_version(monkeypatch) -> None:
    server, _ = _lookup_acceptability_runtime(monkeypatch)

    result = server.snomed_lookup(
        terminology="snomedct-ee",
        code="55641003",
        version="http://snomed.info/sct/11000181102/version/20251130",
        include_acceptability=True,
    )

    assert result["descriptions"]
    assert "not the requested version" in result["descriptions_notice"]


def test_runtime_lookup_acceptability_skips_unknown_codes(monkeypatch) -> None:
    server, native_calls = _lookup_acceptability_runtime(monkeypatch, found=False)

    result = server.snomed_lookup(terminology="snomedct-ee", code="1", include_acceptability=True)

    assert native_calls == []
    assert "descriptions" not in result


def test_runtime_lookup_acceptability_reports_backends_without_native_api(monkeypatch) -> None:
    server, native_calls = _lookup_acceptability_runtime(monkeypatch, has_native_api=False)

    result = server.snomed_lookup(
        terminology="snomedct-ee", code="55641003", include_acceptability=True,
    )

    assert native_calls == []
    assert "not available on this backend" in result["descriptions_notice"]


def test_runtime_lookup_keeps_fhir_result_when_acceptability_fails(monkeypatch) -> None:
    from snowstorm_mcp_server.http_client import HttpRequestError

    server, _ = _lookup_acceptability_runtime(
        monkeypatch, native_error=HttpRequestError("HTTP 503", status_code=503),
    )

    result = server.snomed_lookup(
        terminology="snomedct-ee", code="55641003", include_acceptability=True,
    )

    assert result["found"] is True
    assert "descriptions" not in result
    assert "HTTP 503" in result["descriptions_notice"]


def test_runtime_lookup_acceptability_only_sends_sctids_to_the_native_api(monkeypatch) -> None:
    server, native_calls = _lookup_acceptability_runtime(monkeypatch)

    traversal = server.snomed_lookup(
        terminology="snomedct-ee", code="55641003/../../admin", include_acceptability=True,
    )
    other_system = server.snomed_lookup(
        terminology="snomedct-ee", code="55641003", system="http://loinc.org",
        include_acceptability=True,
    )

    assert native_calls == []
    assert "descriptions" not in traversal
    assert "6-18 digits" in traversal["descriptions_notice"]
    assert "descriptions" not in other_system
    assert "only available for SNOMED CT" in other_system["descriptions_notice"]


def test_runtime_lookup_acceptability_accepts_snomed_edition_system_uris(monkeypatch) -> None:
    server, native_calls = _lookup_acceptability_runtime(monkeypatch)

    for system in ("http://snomed.info/sct/", "http://snomed.info/sct/11000181102/version/20251130"):
        result = server.snomed_lookup(
            terminology="snomedct-ee", code="55641003", system=system, include_acceptability=True,
        )
        assert result["descriptions"]

    assert len(native_calls) == 2
