from __future__ import annotations

import json

import pytest

from snowstorm_mcp_server.mcp_app import MAX_RESPONSE_CHARS, _truncate_response, create_mcp_app


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


NATIVE_ONLY_TOOLS = {
    "snowstorm_list_codesystems",
    "snowstorm_list_versions",
    "snowstorm_search_concepts",
    "snowstorm_get_concept_native",
}

FHIR_TOOLS = {
    "list_terminologies",
    "server_health",
    "server_capabilities",
    "fhir_metadata",
    "snomed_lookup",
    "snomed_validate_code",
    "snomed_subsumes",
    "snomed_expand",
    "snomed_get_ancestors",
    "snomed_get_children",
    "snomed_get_descendants",
}


def _create_mcp_for_mode(monkeypatch, server_mode):
    from snowstorm_mcp_server.capabilities import BackendType, Capabilities, TargetStatus
    from snowstorm_mcp_server.config import AppConfig, TargetConfig
    from snowstorm_mcp_server.terminology import TerminologyInfo, TerminologyRegistry

    backend_type = BackendType.LITE if server_mode == "lite" else BackendType.SNOWSTORM
    target = TargetConfig(
        base_url="http://stub.test",
        mode=server_mode,
        terminology_name="snomedct" if server_mode == "lite" else None,
    )
    stub_config = AppConfig(server_mode=server_mode, targets={"stub": target})

    def _fake_build_registry(_cfg):
        registry = TerminologyRegistry()
        registry.register(
            TerminologyInfo(
                name="snomedct",
                target_name="stub",
                backend_type=backend_type,
                branch_path="MAIN" if server_mode == "snowstorm" else None,
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
                    backend_type=backend_type,
                    has_fhir=True,
                    has_native_api=(server_mode == "snowstorm"),
                ),
            ),
        )
        return registry

    from snowstorm_mcp_server import mcp_app as mcp_app_module
    from snowstorm_mcp_server import runtime as runtime_module

    monkeypatch.setattr(mcp_app_module, "load_config", lambda _path=None: stub_config)
    monkeypatch.setattr(runtime_module, "build_registry", _fake_build_registry)
    return create_mcp_app()


def _tool_names(mcp_app):
    return {t.name for t in mcp_app._tool_manager._tools.values()}


class TestServerModeToolRegistration:
    def test_snowstorm_mode_registers_all_tools(self, monkeypatch):
        app = _create_mcp_for_mode(monkeypatch, "snowstorm")
        tools = _tool_names(app)
        assert FHIR_TOOLS <= tools
        assert NATIVE_ONLY_TOOLS <= tools

    def test_lite_mode_excludes_native_tools(self, monkeypatch):
        app = _create_mcp_for_mode(monkeypatch, "lite")
        tools = _tool_names(app)
        assert FHIR_TOOLS <= tools
        assert tools & NATIVE_ONLY_TOOLS == set()

    def test_snowstorm_mode_server_name(self, monkeypatch):
        app = _create_mcp_for_mode(monkeypatch, "snowstorm")
        assert app.name == "snowstorm-mcp-server"

    def test_lite_mode_server_name(self, monkeypatch):
        app = _create_mcp_for_mode(monkeypatch, "lite")
        assert app.name == "snowstorm-lite-mcp-server"


class TestToolAnnotations:
    def test_all_tools_have_read_only_annotations(self, mcp):
        """Every registered tool must carry readOnlyHint=True, destructiveHint=False."""
        tools = mcp._tool_manager._tools.values()
        assert len(list(tools)) > 0, "No tools registered"
        for tool in tools:
            ann = tool.annotations
            assert ann is not None, f"Tool {tool.name!r} is missing annotations"
            assert ann.readOnlyHint is True, f"Tool {tool.name!r}: readOnlyHint should be True"
            assert ann.destructiveHint is False, f"Tool {tool.name!r}: destructiveHint should be False"


class TestResponseTruncation:
    def test_small_response_unchanged(self):
        result = {"items": [1, 2, 3], "total": 3}
        assert _truncate_response(result) == result

    def test_large_response_is_truncated(self):
        big_list = [{"data": "x" * 200} for _ in range(2000)]
        result = {"items": big_list, "total": len(big_list)}
        truncated = _truncate_response(result)
        serialised = json.dumps(truncated, default=str)
        assert len(serialised) <= MAX_RESPONSE_CHARS
        assert truncated["_truncated"] is True
        assert "_truncation_notice" in truncated
        assert len(truncated["items"]) < len(big_list)

    def test_no_list_fields_returns_as_is(self):
        result = {"data": "x" * 200_000}
        truncated = _truncate_response(result)
        assert "_truncated" not in truncated


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
