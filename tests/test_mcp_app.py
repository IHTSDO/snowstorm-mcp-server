from __future__ import annotations

from unittest.mock import patch

import pytest

from snowstorm_mcp_server.mcp_app import create_mcp_app


@pytest.fixture()
def _captured_expand(monkeypatch):
    """Patch ServerRuntime.snomed_expand to capture kwargs instead of hitting a backend."""
    captured: dict = {}

    def _fake_expand(self, **kwargs):
        captured.update(kwargs)
        return {
            "terminology": "snomedct",
            "value_set_url": kwargs.get("value_set_url"),
            "total": 0,
            "offset": kwargs.get("offset", 0),
            "returned": 0,
            "items": [],
        }

    from snowstorm_mcp_server.runtime import ServerRuntime

    monkeypatch.setattr(ServerRuntime, "snomed_expand", _fake_expand)
    return captured


@pytest.fixture()
def mcp(monkeypatch):
    """Create an MCP app with a minimal stub config so tools can be registered."""
    from snowstorm_mcp_server.capabilities import BackendType, Capabilities, TargetStatus
    from snowstorm_mcp_server.config import AppConfig, TargetConfig
    from snowstorm_mcp_server.terminology import TerminologyInfo, TerminologyRegistry

    target = TargetConfig(base_url="http://stub.test")
    stub_config = AppConfig(targets={"stub": target})

    def _fake_build_registry(_cfg):
        registry = TerminologyRegistry()
        registry.register(
            TerminologyInfo(
                name="snomedct",
                target_name="stub",
                backend_type=BackendType.SNOWSTORM,
                branch_path="MAIN",
            ),
            target,
        )
        registry.set_default("snomedct")
        registry.set_target_status(
            "stub",
            TargetStatus(
                reachable=True,
                base_url="http://stub.test",
                fhir_base_url="http://stub.test/fhir",
                capabilities=Capabilities(
                    backend_type=BackendType.SNOWSTORM,
                    has_fhir=True,
                    has_native_api=True,
                ),
            ),
        )
        return registry

    from snowstorm_mcp_server import mcp_app as mcp_app_module
    from snowstorm_mcp_server import runtime as runtime_module

    monkeypatch.setattr(mcp_app_module, "load_config", lambda _path=None: stub_config)
    monkeypatch.setattr(runtime_module, "build_registry", _fake_build_registry)
    return create_mcp_app()


def _call_tool(mcp_app, name: str, arguments: dict):
    """Invoke a registered tool function by name."""
    for tool_fn in mcp_app._tool_manager._tools.values():
        if tool_fn.name == name:
            return tool_fn.fn(**arguments)
    raise KeyError(f"Tool {name!r} not registered")


class TestSnomedGetAncestors:
    def test_all_ancestors_ecl(self, mcp, _captured_expand):
        _call_tool(mcp, "snomed_get_ancestors", {"concept_id": "22298006"})
        assert _captured_expand["value_set_url"] == "http://snomed.info/sct?fhir_vs=ecl/> 22298006"

    def test_direct_parents_only_ecl(self, mcp, _captured_expand):
        _call_tool(mcp, "snomed_get_ancestors", {"concept_id": "22298006", "direct_only": True})
        assert _captured_expand["value_set_url"] == "http://snomed.info/sct?fhir_vs=ecl/>! 22298006"

    def test_passes_pagination(self, mcp, _captured_expand):
        _call_tool(mcp, "snomed_get_ancestors", {"concept_id": "123", "offset": 10, "count": 25})
        assert _captured_expand["offset"] == 10
        assert _captured_expand["count"] == 25


class TestSnomedGetChildren:
    def test_children_ecl(self, mcp, _captured_expand):
        _call_tool(mcp, "snomed_get_children", {"concept_id": "404684003"})
        assert _captured_expand["value_set_url"] == "http://snomed.info/sct?fhir_vs=ecl/<! 404684003"

    def test_passes_pagination(self, mcp, _captured_expand):
        _call_tool(mcp, "snomed_get_children", {"concept_id": "123", "offset": 5, "count": 10})
        assert _captured_expand["offset"] == 5
        assert _captured_expand["count"] == 10


class TestSnomedGetDescendants:
    def test_descendants_ecl(self, mcp, _captured_expand):
        _call_tool(mcp, "snomed_get_descendants", {"concept_id": "73211009"})
        assert _captured_expand["value_set_url"] == "http://snomed.info/sct?fhir_vs=ecl/< 73211009"

    def test_passes_pagination(self, mcp, _captured_expand):
        _call_tool(mcp, "snomed_get_descendants", {"concept_id": "123", "offset": 20, "count": 100})
        assert _captured_expand["offset"] == 20
        assert _captured_expand["count"] == 100
