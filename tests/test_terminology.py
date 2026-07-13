from __future__ import annotations

import httpx
import pytest

from snowstorm_mcp_server.capabilities import BackendType, Capabilities, TargetStatus
from snowstorm_mcp_server.config import AppConfig, TargetConfig
from snowstorm_mcp_server.http_client import HttpClient
from snowstorm_mcp_server.terminology import (
    DiscoveryError,
    DuplicateTerminologyError,
    TerminologyInfo,
    TerminologyNotFoundError,
    TerminologyRegistry,
    build_lite_terminology,
    build_registry,
    discover_snowstorm_terminologies,
)


# ---------------------------------------------------------------------------
# Discovery tests
# ---------------------------------------------------------------------------

CODESYSTEMS_RESPONSE = {
    "items": [
        {
            "shortName": "SNOMEDCT",
            "name": "International Edition",
            "branchPath": "MAIN",
        },
        {
            "shortName": "SNOMEDCT-US",
            "name": "US Edition",
            "branchPath": "MAIN/SNOMEDCT-US",
        },
    ]
}


def _make_snowstorm_client(target: TargetConfig, codesystems_json: dict) -> HttpClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/codesystems":
            return httpx.Response(200, json=codesystems_json)
        return httpx.Response(404)

    raw = httpx.Client(transport=httpx.MockTransport(handler), base_url=target.base_url)
    return HttpClient(target, client=raw)


def test_discover_snowstorm_terminologies_parses_response() -> None:
    target = TargetConfig(base_url="http://test")
    client = _make_snowstorm_client(target, CODESYSTEMS_RESPONSE)

    result = discover_snowstorm_terminologies("ss", target, client=client)

    assert len(result) == 2
    assert result[0].name == "snomedct"
    assert result[0].display_name == "International Edition"
    assert result[0].branch_path == "MAIN"
    assert result[0].backend_type == BackendType.SNOWSTORM
    assert result[1].name == "snomedct-us"
    assert result[1].branch_path == "MAIN/SNOMEDCT-US"


def test_discover_lowercases_short_name() -> None:
    target = TargetConfig(base_url="http://test")
    client = _make_snowstorm_client(
        target,
        {"items": [{"shortName": "SNOMEDCT-NZ", "branchPath": "MAIN/SNOMEDCT-NZ"}]},
    )

    result = discover_snowstorm_terminologies("ss", target, client=client)

    assert result[0].name == "snomedct-nz"


def test_discover_skips_malformed_items() -> None:
    target = TargetConfig(base_url="http://test")
    client = _make_snowstorm_client(
        target,
        {
            "items": [
                {"shortName": "", "branchPath": "MAIN"},
                {"shortName": "GOOD", "branchPath": "MAIN"},
                {"shortName": "NO_BRANCH"},
                "not-a-dict",
            ]
        },
    )

    result = discover_snowstorm_terminologies("ss", target, client=client)

    assert len(result) == 1
    assert result[0].name == "good"


def test_discover_raises_on_http_error() -> None:
    target = TargetConfig(base_url="http://test")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    raw = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    client = HttpClient(target, client=raw)

    with pytest.raises(DiscoveryError, match="Failed to discover"):
        discover_snowstorm_terminologies("ss", target, client=client)


def test_build_lite_terminology_uses_config_name() -> None:
    target = TargetConfig(base_url="http://test", mode="lite", terminology_name="snomedct-nz")

    result = build_lite_terminology("lite-nz", target)

    assert result.name == "snomedct-nz"
    assert result.target_name == "lite-nz"
    assert result.backend_type == BackendType.LITE
    assert result.branch_path is None


def test_build_lite_terminology_requires_name() -> None:
    target = TargetConfig(base_url="http://test", mode="lite")

    with pytest.raises(DiscoveryError, match="terminology_name"):
        build_lite_terminology("lite", target)


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------


def _make_info(name: str, target_name: str = "t1", **kwargs) -> TerminologyInfo:
    return TerminologyInfo(
        name=name,
        target_name=target_name,
        backend_type=kwargs.get("backend_type", BackendType.SNOWSTORM),
        branch_path=kwargs.get("branch_path", "MAIN"),
    )


def test_registry_resolve_explicit() -> None:
    reg = TerminologyRegistry()
    target = TargetConfig(base_url="http://test")
    reg.register(_make_info("snomedct"), target)
    reg.register(_make_info("snomedct-us", branch_path="MAIN/SNOMEDCT-US"), target)

    info = reg.resolve("snomedct-us")

    assert info.name == "snomedct-us"
    assert info.branch_path == "MAIN/SNOMEDCT-US"


def test_registry_resolve_default() -> None:
    reg = TerminologyRegistry()
    target = TargetConfig(base_url="http://test")
    reg.register(_make_info("snomedct"), target)
    reg.set_default("snomedct")

    info = reg.resolve(None)

    assert info.name == "snomedct"


def test_registry_resolve_unknown_raises() -> None:
    reg = TerminologyRegistry()
    target = TargetConfig(base_url="http://test")
    reg.register(_make_info("snomedct"), target)

    with pytest.raises(TerminologyNotFoundError, match="Unknown terminology 'nope'"):
        reg.resolve("nope")


def test_registry_resolve_no_default_raises() -> None:
    reg = TerminologyRegistry()
    target = TargetConfig(base_url="http://test")
    reg.register(_make_info("snomedct"), target)

    with pytest.raises(TerminologyNotFoundError, match="No terminology specified"):
        reg.resolve(None)


def test_registry_duplicate_raises() -> None:
    reg = TerminologyRegistry()
    target = TargetConfig(base_url="http://test")
    reg.register(_make_info("snomedct", target_name="t1"), target)

    with pytest.raises(DuplicateTerminologyError, match="already registered"):
        reg.register(_make_info("snomedct", target_name="t2"), target)


def test_registry_set_default_unknown_raises() -> None:
    reg = TerminologyRegistry()
    target = TargetConfig(base_url="http://test")
    reg.register(_make_info("snomedct"), target)

    with pytest.raises(TerminologyNotFoundError, match="Default terminology 'nope'"):
        reg.set_default("nope")


def test_registry_list_terminologies_sorted() -> None:
    reg = TerminologyRegistry()
    target = TargetConfig(base_url="http://test")
    reg.register(_make_info("snomedct-us"), target)
    reg.register(_make_info("snomedct"), target)

    names = [t.name for t in reg.list_terminologies()]

    assert names == ["snomedct", "snomedct-us"]


def test_registry_resolve_for_target_single_terminology() -> None:
    reg = TerminologyRegistry()
    target = TargetConfig(base_url="http://test")
    reg.register(_make_info("snomedct", target_name="snowstorm"), target)

    info = reg.resolve_for_target(target_name="snowstorm")

    assert info.name == "snomedct"


def test_registry_resolve_for_target_with_explicit_terminology() -> None:
    reg = TerminologyRegistry()
    target = TargetConfig(base_url="http://test")
    reg.register(_make_info("snomedct", target_name="snowstorm"), target)
    reg.register(_make_info("snomedct-us", target_name="snowstorm"), target)

    info = reg.resolve_for_target(target_name="snowstorm", terminology="snomedct-us")

    assert info.name == "snomedct-us"


def test_registry_resolve_for_target_requires_terminology_when_ambiguous() -> None:
    reg = TerminologyRegistry()
    target = TargetConfig(base_url="http://test")
    reg.register(_make_info("snomedct", target_name="snowstorm"), target)
    reg.register(_make_info("snomedct-us", target_name="snowstorm"), target)

    with pytest.raises(TerminologyNotFoundError, match="serves multiple terminologies"):
        reg.resolve_for_target(target_name="snowstorm")


# ---------------------------------------------------------------------------
# build_registry integration tests (with mocked HTTP)
# ---------------------------------------------------------------------------


def _mock_snowstorm_handler(request: httpx.Request) -> httpx.Response:
    """Simulates a Snowstorm backend with FHIR and /codesystems."""
    if request.url.path == "/fhir/metadata":
        return httpx.Response(200, json={"resourceType": "CapabilityStatement"})
    if request.url.path == "/codesystems":
        return httpx.Response(200, json=CODESYSTEMS_RESPONSE)
    if request.url.path == "/fhir-admin/load-package":
        return httpx.Response(404)
    return httpx.Response(404)


def test_registry_routes_multiple_lite_targets() -> None:
    """Two Lite instances (different editions) resolve to their respective targets."""
    reg = TerminologyRegistry()
    target_int = TargetConfig(base_url="http://lite-int:8082", mode="lite", terminology_name="snomedct")
    target_nz = TargetConfig(base_url="http://lite-nz:8083", mode="lite", terminology_name="snomedct-nz")

    reg.register(
        _make_info("snomedct", target_name="lite-int", backend_type=BackendType.LITE, branch_path=None),
        target_int,
    )
    reg.register(
        _make_info("snomedct-nz", target_name="lite-nz", backend_type=BackendType.LITE, branch_path=None),
        target_nz,
    )
    reg.set_default("snomedct")

    # Default resolves to the international Lite instance
    default_info = reg.resolve(None)
    assert default_info.name == "snomedct"
    assert default_info.target_name == "lite-int"
    assert reg.get_target("lite-int").base_url == "http://lite-int:8082"

    # Explicit NZ resolves to the NZ Lite instance
    nz_info = reg.resolve("snomedct-nz")
    assert nz_info.name == "snomedct-nz"
    assert nz_info.target_name == "lite-nz"
    assert reg.get_target("lite-nz").base_url == "http://lite-nz:8083"

    # Both show up in listing
    names = reg.list_terminology_names()
    assert "snomedct" in names
    assert "snomedct-nz" in names


def test_build_registry_discovers_snowstorm_terminologies(monkeypatch) -> None:
    config = AppConfig(
        default_terminology="snomedct",
        targets={
            "snowstorm": TargetConfig(base_url="http://test"),
        },
    )

    # Patch probe_target and discover to avoid real HTTP
    from snowstorm_mcp_server import capabilities, terminology

    transport = httpx.MockTransport(_mock_snowstorm_handler)

    def patched_probe(target, *, client=None):
        raw = httpx.Client(transport=transport, base_url=target.base_url)
        return capabilities.probe_target(target, client=HttpClient(target, client=raw))

    def patched_discover(target_name, target, *, client=None):
        raw = httpx.Client(transport=transport, base_url=target.base_url)
        return discover_snowstorm_terminologies(
            target_name, target, client=HttpClient(target, client=raw)
        )

    monkeypatch.setattr(terminology, "probe_target", patched_probe)
    monkeypatch.setattr(terminology, "discover_snowstorm_terminologies", patched_discover)

    registry = build_registry(config)

    names = registry.list_terminology_names()
    assert "snomedct" in names
    assert "snomedct-us" in names
    assert registry.default_terminology == "snomedct"


def test_build_registry_single_terminology_becomes_default(monkeypatch) -> None:
    config = AppConfig(
        targets={
            "snowstorm": TargetConfig(base_url="http://test"),
        },
    )

    from snowstorm_mcp_server import capabilities, terminology

    single_response = {"items": [{"shortName": "SNOMEDCT", "branchPath": "MAIN"}]}
    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, json={"resourceType": "CapabilityStatement"})
        if r.url.path == "/fhir/metadata"
        else httpx.Response(200, json=single_response)
        if r.url.path == "/codesystems"
        else httpx.Response(404)
    )

    def patched_probe(target, *, client=None):
        raw = httpx.Client(transport=transport, base_url=target.base_url)
        return capabilities.probe_target(target, client=HttpClient(target, client=raw))

    def patched_discover(target_name, target, *, client=None):
        raw = httpx.Client(transport=transport, base_url=target.base_url)
        return discover_snowstorm_terminologies(
            target_name, target, client=HttpClient(target, client=raw)
        )

    monkeypatch.setattr(terminology, "probe_target", patched_probe)
    monkeypatch.setattr(terminology, "discover_snowstorm_terminologies", patched_discover)

    registry = build_registry(config)

    assert registry.default_terminology == "snomedct"


def test_build_registry_no_stub_on_discovery_failure(monkeypatch) -> None:
    config = AppConfig(
        targets={
            "mysnowstorm": TargetConfig(base_url="http://test"),
        },
    )

    from snowstorm_mcp_server import capabilities, terminology

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/fhir/metadata":
            return httpx.Response(200, json={"resourceType": "CapabilityStatement"})
        if request.url.path == "/codesystems":
            return httpx.Response(200, json={"items": []})
        if request.url.path == "/browser/MAIN/descriptions":
            return httpx.Response(200, json={"items": []})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)

    def patched_probe(target, *, client=None):
        raw = httpx.Client(transport=transport, base_url=target.base_url)
        return capabilities.probe_target(target, client=HttpClient(target, client=raw))

    def patched_discover(target_name, target, *, client=None):
        raise DiscoveryError("boom")

    monkeypatch.setattr(terminology, "probe_target", patched_probe)
    monkeypatch.setattr(terminology, "discover_snowstorm_terminologies", patched_discover)

    registry = build_registry(config)

    # Discovery failure must NOT silently register a terminology named after the
    # target — that masks misconfiguration. The target should have zero
    # terminologies and the error must be surfaced in discovery_errors.
    assert registry.list_terminology_names() == []
    assert "mysnowstorm" in registry.list_target_names()
    assert len(registry.discovery_errors) == 1
    assert "boom" in registry.discovery_errors[0]
    assert registry.has_pending_discovery()


def test_build_registry_default_terminology_graceful_on_discovery_failure(monkeypatch) -> None:
    """When discovery fails, the configured default_terminology cannot be
    resolved; the server should start without a default rather than crash."""
    config = AppConfig(
        default_terminology="snomedct",
        targets={
            "dev-snowstorm": TargetConfig(base_url="http://test"),
        },
    )

    from snowstorm_mcp_server import capabilities, terminology

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/fhir/metadata":
            return httpx.Response(200, json={"resourceType": "CapabilityStatement"})
        if request.url.path == "/browser/MAIN/descriptions":
            return httpx.Response(200, json={"items": []})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)

    def patched_probe(target, *, client=None):
        raw = httpx.Client(transport=transport, base_url=target.base_url)
        return capabilities.probe_target(target, client=HttpClient(target, client=raw))

    def patched_discover(target_name, target, *, client=None):
        raise DiscoveryError("429 Too Many Requests")

    monkeypatch.setattr(terminology, "probe_target", patched_probe)
    monkeypatch.setattr(terminology, "discover_snowstorm_terminologies", patched_discover)

    # Previously this would raise TerminologyNotFoundError and crash
    registry = build_registry(config)

    assert registry.default_terminology is None
    assert registry.list_terminology_names() == []
    assert "dev-snowstorm" in registry.list_target_names()
    assert any("default_terminology" in e for e in registry.discovery_errors)


def _registry_with_pending_target(
    target_name: str = "mysnowstorm",
    configured_default: str | None = None,
) -> tuple[TerminologyRegistry, TargetConfig]:
    target = TargetConfig(base_url="http://test")
    registry = TerminologyRegistry()
    registry.register_target(target_name, target)
    registry.set_target_status(
        target_name,
        TargetStatus(
            reachable=True,
            base_url=target.base_url,
            fhir_base_url=target.fhir_base_url,
            capabilities=Capabilities(backend_type=BackendType.SNOWSTORM),
        ),
    )
    registry.record_discovery_error(target_name, "boom")
    registry.mark_discovery_pending(target_name, target)
    if configured_default:
        registry.set_configured_default(configured_default)
        registry.record_discovery_error(
            "default_terminology",
            f"default_terminology '{configured_default}' not found; continuing without a default",
        )
    return registry, target


def test_retry_pending_discovery_recovers_when_backend_comes_up(monkeypatch) -> None:
    registry, _target = _registry_with_pending_target()

    from snowstorm_mcp_server import terminology

    def patched_discover(target_name, target, *, client=None):
        return [
            TerminologyInfo(
                name="snomedct",
                display_name="International Edition",
                target_name=target_name,
                backend_type=BackendType.SNOWSTORM,
                branch_path="MAIN",
            )
        ]

    monkeypatch.setattr(terminology, "discover_snowstorm_terminologies", patched_discover)

    assert registry.retry_pending_discovery() is True
    assert registry.list_terminology_names() == ["snomedct"]
    assert not registry.has_pending_discovery()
    assert registry.discovery_errors == []
    # Single terminology with no configured default becomes the default.
    assert registry.default_terminology == "snomedct"
    # Nothing left to retry.
    assert registry.retry_pending_discovery() is False


def test_retry_pending_discovery_applies_configured_default(monkeypatch) -> None:
    registry, _target = _registry_with_pending_target(configured_default="snomedct")

    from snowstorm_mcp_server import terminology

    def patched_discover(target_name, target, *, client=None):
        return [
            TerminologyInfo(
                name="snomedct",
                target_name=target_name,
                backend_type=BackendType.SNOWSTORM,
                branch_path="MAIN",
            ),
            TerminologyInfo(
                name="snomedct-us",
                target_name=target_name,
                backend_type=BackendType.SNOWSTORM,
                branch_path="MAIN/SNOMEDCT-US",
            ),
        ]

    monkeypatch.setattr(terminology, "discover_snowstorm_terminologies", patched_discover)

    assert registry.retry_pending_discovery() is True
    assert registry.default_terminology == "snomedct"
    assert registry.discovery_errors == []


def test_retry_pending_discovery_reprobes_unknown_backend(monkeypatch) -> None:
    """A backend that was completely down at startup has unknown backend type;
    a retry must re-probe it and then discover its terminologies."""
    target = TargetConfig(base_url="http://test")
    registry = TerminologyRegistry()
    registry.register_target("mysnowstorm", target)
    registry.record_discovery_error("mysnowstorm", "connection refused")
    registry.mark_discovery_pending("mysnowstorm", target)

    from snowstorm_mcp_server import terminology

    def patched_probe(target_cfg, *, client=None):
        return TargetStatus(
            reachable=True,
            base_url=target_cfg.base_url,
            fhir_base_url=target_cfg.fhir_base_url,
            capabilities=Capabilities(
                backend_type=BackendType.SNOWSTORM,
                has_fhir=True,
                has_native_api=True,
            ),
        )

    def patched_discover(target_name, target_cfg, *, client=None):
        return [
            TerminologyInfo(
                name="snomedct",
                target_name=target_name,
                backend_type=BackendType.SNOWSTORM,
                branch_path="MAIN",
            )
        ]

    monkeypatch.setattr(terminology, "probe_target", patched_probe)
    monkeypatch.setattr(terminology, "discover_snowstorm_terminologies", patched_discover)

    assert registry.retry_pending_discovery() is True
    assert registry.list_terminology_names() == ["snomedct"]
    status = registry.get_target_status("mysnowstorm")
    assert status is not None
    assert status.capabilities.backend_type == BackendType.SNOWSTORM
    assert registry.discovery_errors == []


def test_retry_pending_discovery_keeps_error_while_backend_down(monkeypatch) -> None:
    registry, _target = _registry_with_pending_target()

    from snowstorm_mcp_server import terminology

    def patched_discover(target_name, target, *, client=None):
        raise DiscoveryError("still down")

    monkeypatch.setattr(terminology, "discover_snowstorm_terminologies", patched_discover)

    assert registry.retry_pending_discovery() is False
    assert registry.has_pending_discovery()
    assert registry.list_terminology_names() == []
    assert any("still down" in e for e in registry.discovery_errors)
