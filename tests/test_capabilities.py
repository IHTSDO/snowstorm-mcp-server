from __future__ import annotations

import httpx

from snowstorm_mcp_server.capabilities import BackendType, probe_target
from snowstorm_mcp_server.config import TargetConfig
from snowstorm_mcp_server.http_client import HttpClient


def test_probe_classifies_snowstorm_from_codesystems_and_fhir() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/fhir/metadata":
            return httpx.Response(200, json={"resourceType": "CapabilityStatement"})
        if request.url.path == "/codesystems":
            return httpx.Response(200, json={"items": []})
        if request.url.path == "/fhir-admin/load-package":
            return httpx.Response(404)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    target = TargetConfig(base_url="http://test", auth={"mode": "none"})

    status = probe_target(target, client=HttpClient(target, client=client))

    assert status.reachable is True
    assert status.capabilities.has_fhir is True
    assert status.capabilities.has_native_api is True
    assert status.capabilities.backend_type == BackendType.SNOWSTORM


def test_probe_classifies_lite_from_fhir_and_fhir_admin() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/fhir/metadata":
            return httpx.Response(200, json={"resourceType": "CapabilityStatement"})
        if request.url.path == "/codesystems":
            return httpx.Response(404)
        if request.url.path == "/fhir-admin/load-package":
            return httpx.Response(405)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    target = TargetConfig(base_url="http://test", auth={"mode": "none"})

    status = probe_target(target, client=HttpClient(target, client=client))

    assert status.reachable is True
    assert status.capabilities.has_fhir is True
    assert status.capabilities.has_lite_load_package is True
    assert status.capabilities.backend_type == BackendType.LITE


def test_probe_classifies_snowstorm_when_codesystems_500_but_browser_api_works() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/fhir/metadata":
            return httpx.Response(200, json={"resourceType": "CapabilityStatement"})
        if request.url.path == "/codesystems":
            return httpx.Response(500, json={"error": "transient"})
        if request.url.path == "/browser/MAIN/descriptions":
            return httpx.Response(200, json={"items": []})
        if request.url.path == "/fhir-admin/load-package":
            return httpx.Response(404)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")
    target = TargetConfig(base_url="http://test", auth={"mode": "none"})

    status = probe_target(target, client=HttpClient(target, client=client))

    assert status.reachable is True
    assert status.capabilities.has_native_api is True
    assert status.capabilities.backend_type == BackendType.SNOWSTORM
