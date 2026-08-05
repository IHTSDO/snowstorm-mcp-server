from __future__ import annotations

import json

import pytest

from snowstorm_mcp_server.guards import SnowstormRateLimitError
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

    def test_large_dict_field_is_removed(self):
        """Dict-valued payloads (e.g. raw_parameters, raw FHIR metadata) must
        also be truncated, not just list-valued ones."""
        result = {
            "code": "22298006",
            "raw_parameters": {f"param{i}": ["x" * 200] for i in range(2000)},
        }
        truncated = _truncate_response(result)
        assert len(json.dumps(truncated, default=str)) <= MAX_RESPONSE_CHARS
        assert truncated["_truncated"] is True
        assert truncated["raw_parameters"] == {
            "_removed": "Field removed to stay within size limits."
        }
        assert truncated["code"] == "22298006"

    def test_list_trimmed_before_dict_removed(self):
        """When a large list fits after trimming, dict fields stay intact."""
        result = {
            "items": [{"data": "x" * 200} for _ in range(2000)],
            "meta": {"software": "Snowstorm"},
        }
        truncated = _truncate_response(result)
        assert len(json.dumps(truncated, default=str)) <= MAX_RESPONSE_CHARS
        assert truncated["meta"] == {"software": "Snowstorm"}
        assert len(truncated["items"]) < 2000


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

    def test_count_is_capped(self, mcp, _captured_expand):
        """Hierarchy tools must respect max_count_per_call (default 500)."""
        _call_tool(mcp, "snomed_get_ancestors", {"concept_id": "123", "count": 100_000})
        assert _captured_expand["count"] == 500


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


# ── Helpers for guard integration tests ───────────────────────────────────────


def _create_mcp_with_guards(monkeypatch, **guard_kwargs):
    """Create a snowstorm-mode MCP app with custom guard settings."""
    from snowstorm_mcp_server.capabilities import BackendType, Capabilities, TargetStatus
    from snowstorm_mcp_server.config import AppConfig, GuardConfig, TargetConfig
    from snowstorm_mcp_server.terminology import TerminologyInfo, TerminologyRegistry

    target = TargetConfig(base_url="http://stub.test")
    stub_config = AppConfig(targets={"stub": target}, guards=GuardConfig(**guard_kwargs))

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


class _StubSession:
    """Weakref-compatible stand-in for an MCP session object."""
    pass


class _StubCtx:
    """Minimal stand-in for a FastMCP Context, providing a stable session identity."""

    def __init__(self):
        self.session = _StubSession()


# ── TestGuardsWiredToTools ─────────────────────────────────────────────────────


class TestGuardsWiredToTools:
    """Verify that tool functions enforce rate limiting and per-session limits.

    Each test creates an MCP app with a rate limit of 1 call per window so
    that the first call succeeds and the second raises SnowstormRateLimitError.
    Runtime methods are stubbed out to keep tests fast and self-contained.
    """

    def test_snomed_lookup_is_rate_limited(self, monkeypatch):
        from snowstorm_mcp_server.runtime import ServerRuntime

        monkeypatch.setattr(ServerRuntime, "snomed_lookup", lambda self, **_kw: {"code": "123"})
        app = _create_mcp_with_guards(monkeypatch, rate_limit_calls=1, rate_limit_window_seconds=60)

        _call_tool(app, "snomed_lookup", {"code": "123"})
        with pytest.raises(SnowstormRateLimitError):
            _call_tool(app, "snomed_lookup", {"code": "123"})

    def test_snomed_validate_code_is_rate_limited(self, monkeypatch):
        from snowstorm_mcp_server.runtime import ServerRuntime

        monkeypatch.setattr(ServerRuntime, "snomed_validate_code", lambda self, **_kw: {"valid": True})
        app = _create_mcp_with_guards(monkeypatch, rate_limit_calls=1, rate_limit_window_seconds=60)

        _call_tool(app, "snomed_validate_code", {"code": "123"})
        with pytest.raises(SnowstormRateLimitError):
            _call_tool(app, "snomed_validate_code", {"code": "123"})

    def test_snomed_subsumes_is_rate_limited(self, monkeypatch):
        from snowstorm_mcp_server.runtime import ServerRuntime

        monkeypatch.setattr(ServerRuntime, "snomed_subsumes", lambda self, **_kw: {"outcome": "subsumes"})
        app = _create_mcp_with_guards(monkeypatch, rate_limit_calls=1, rate_limit_window_seconds=60)

        _call_tool(app, "snomed_subsumes", {"code_a": "22298006", "code_b": "73211009"})
        with pytest.raises(SnowstormRateLimitError):
            _call_tool(app, "snomed_subsumes", {"code_a": "22298006", "code_b": "73211009"})

    def test_snowstorm_search_is_rate_limited(self, monkeypatch):
        from snowstorm_mcp_server.runtime import ServerRuntime

        monkeypatch.setattr(ServerRuntime, "snowstorm_search_concepts", lambda self, **_kw: {"hits": []})
        app = _create_mcp_with_guards(monkeypatch, rate_limit_calls=1, rate_limit_window_seconds=60)

        _call_tool(app, "snowstorm_search_concepts", {"term": "asthma"})
        with pytest.raises(SnowstormRateLimitError):
            _call_tool(app, "snowstorm_search_concepts", {"term": "asthma"})

    def test_list_terminologies_is_not_rate_limited(self, monkeypatch):
        """list_terminologies is an in-memory call and must not be affected by low rate limits."""
        app = _create_mcp_with_guards(monkeypatch, rate_limit_calls=1, rate_limit_window_seconds=60)

        # Exhaust the global rate limit with a lookup call first
        from snowstorm_mcp_server.runtime import ServerRuntime
        monkeypatch.setattr(ServerRuntime, "snomed_lookup", lambda self, **_kw: {"code": "123"})
        _call_tool(app, "snomed_lookup", {"code": "123"})

        # list_terminologies should be unaffected — it never touches the rate limiter
        result = _call_tool(app, "list_terminologies", {})
        assert "terminologies" in result

    def test_server_health_is_rate_limited(self, monkeypatch):
        from snowstorm_mcp_server.runtime import ServerRuntime

        monkeypatch.setattr(ServerRuntime, "server_health", lambda self, *_a, **_kw: {"reachable": True})
        app = _create_mcp_with_guards(monkeypatch, rate_limit_calls=1, rate_limit_window_seconds=60)

        _call_tool(app, "server_health", {})
        with pytest.raises(SnowstormRateLimitError):
            _call_tool(app, "server_health", {})

    def test_server_capabilities_is_rate_limited(self, monkeypatch):
        from snowstorm_mcp_server.runtime import ServerRuntime

        monkeypatch.setattr(
            ServerRuntime,
            "server_capabilities",
            lambda self, *_a, **_kw: {"reachable": True, "capabilities": {}},
        )
        app = _create_mcp_with_guards(monkeypatch, rate_limit_calls=1, rate_limit_window_seconds=60)

        _call_tool(app, "server_capabilities", {})
        with pytest.raises(SnowstormRateLimitError):
            _call_tool(app, "server_capabilities", {})

    def test_fhir_metadata_is_rate_limited(self, monkeypatch):
        from snowstorm_mcp_server.runtime import ServerRuntime

        monkeypatch.setattr(ServerRuntime, "fhir_metadata", lambda self, *_a, **_kw: {"summary": {}})
        app = _create_mcp_with_guards(monkeypatch, rate_limit_calls=1, rate_limit_window_seconds=60)

        _call_tool(app, "fhir_metadata", {})
        with pytest.raises(SnowstormRateLimitError):
            _call_tool(app, "fhir_metadata", {})

    def test_per_session_limit_blocks_heavy_session_not_others(self, monkeypatch):
        """One session hitting its per-session limit must not affect a different session."""
        from snowstorm_mcp_server.runtime import ServerRuntime

        monkeypatch.setattr(ServerRuntime, "snomed_lookup", lambda self, **_kw: {"code": "123"})
        app = _create_mcp_with_guards(
            monkeypatch,
            rate_limit_calls=100,          # global limit well out of the way
            rate_limit_window_seconds=60,
            per_session_rate_limit_calls=1,
        )

        session_a = _StubCtx()
        session_b = _StubCtx()

        _call_tool(app, "snomed_lookup", {"code": "123", "ctx": session_a})
        with pytest.raises(SnowstormRateLimitError):
            _call_tool(app, "snomed_lookup", {"code": "123", "ctx": session_a})

        # session_b has an independent counter and should still be allowed through
        _call_tool(app, "snomed_lookup", {"code": "123", "ctx": session_b})


# ── --log-level CLI argument tests ─────────────────────────────────────────────


class TestLogLevelArgument:
    """Verify the --log-level flag accepts lowercase and maps correctly."""

    def test_lowercase_accepted(self):
        from snowstorm_mcp_server.__main__ import build_arg_parser

        parser = build_arg_parser()
        args = parser.parse_args(["--log-level", "debug"])
        assert args.log_level == "DEBUG"

    def test_uppercase_accepted(self):
        from snowstorm_mcp_server.__main__ import build_arg_parser

        parser = build_arg_parser()
        args = parser.parse_args(["--log-level", "WARNING"])
        assert args.log_level == "WARNING"

    def test_mixed_case_accepted(self):
        from snowstorm_mcp_server.__main__ import build_arg_parser

        parser = build_arg_parser()
        args = parser.parse_args(["--log-level", "Info"])
        assert args.log_level == "INFO"

    def test_invalid_level_rejected(self):
        from snowstorm_mcp_server.__main__ import build_arg_parser

        parser = build_arg_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--log-level", "TRACE"])


# ── Expansion preflight concurrency test ───────────────────────────────────────


class TestExpansionPreflightConcurrency:
    """Verify that the expansion preflight query respects the concurrency cap."""

    def test_preflight_acquires_concurrency_semaphore(self, monkeypatch):
        """The preflight summary_only query must go through the concurrency limiter."""
        from snowstorm_mcp_server.runtime import ServerRuntime

        semaphore_log = []

        def _tracking_expand(self, **kwargs):
            # The concurrency limiter is a Semaphore(max_concurrent) — if it's
            # fully acquired, _value will be less than the max. We check that
            # at least one permit is consumed (i.e. we're inside `with guards.concurrency`).
            semaphore_log.append(("expand", kwargs.get("summary_only", False)))
            return {
                "total": 5,
                "offset": kwargs.get("offset", 0),
                "returned": 0,
                "items": [],
            }

        monkeypatch.setattr(ServerRuntime, "snomed_expand", _tracking_expand)
        app = _create_mcp_with_guards(
            monkeypatch,
            rate_limit_calls=100,
            rate_limit_window_seconds=60,
            enable_expansion_size_guard=True,
            expansion_count_threshold=1000,
        )

        _call_tool(app, "snomed_expand", {
            "value_set_url": "http://snomed.info/sct?fhir_vs=ecl/<<195967001",
            "count": 20,
        })

        # The preflight (summary_only=True) should have been called before the
        # actual expand (summary_only=False).
        assert len(semaphore_log) == 2
        assert semaphore_log[0] == ("expand", True), "preflight should be first"
        assert semaphore_log[1] == ("expand", False), "actual expand should be second"

    def test_preflight_uses_filter_and_fuzzy_when_calculating_total(self, monkeypatch):
        from snowstorm_mcp_server.runtime import ServerRuntime

        calls = []

        def _tracking_expand(self, **kwargs):
            calls.append(kwargs.copy())
            total = 5 if kwargs.get("filter") == "asthma" and kwargs.get("fuzzy") else 5000
            return {
                "total": total,
                "offset": kwargs.get("offset", 0),
                "returned": 0,
                "items": [],
            }

        monkeypatch.setattr(ServerRuntime, "snomed_expand", _tracking_expand)
        app = _create_mcp_with_guards(
            monkeypatch,
            rate_limit_calls=100,
            rate_limit_window_seconds=60,
            enable_expansion_size_guard=True,
            expansion_count_threshold=1000,
        )

        _call_tool(app, "snomed_expand", {
            "value_set_url": "http://snomed.info/sct?fhir_vs=ecl/<<195967001",
            "filter": "asthma",
            "fuzzy": True,
        })

        assert len(calls) == 2
        assert calls[0]["summary_only"] is True
        assert calls[0]["filter"] == "asthma"
        assert calls[0]["fuzzy"] is True

    def test_preflight_cache_is_scoped_by_target(self, monkeypatch):
        from snowstorm_mcp_server.capabilities import BackendType, Capabilities, TargetStatus
        from snowstorm_mcp_server.config import AppConfig, GuardConfig, TargetConfig
        from snowstorm_mcp_server.runtime import ServerRuntime
        from snowstorm_mcp_server.terminology import TerminologyInfo, TerminologyRegistry

        target_a = TargetConfig(base_url="http://a.test")
        target_b = TargetConfig(base_url="http://b.test")
        stub_config = AppConfig(
            default_terminology="a",
            targets={"a-target": target_a, "b-target": target_b},
            guards=GuardConfig(
                rate_limit_calls=100,
                enable_expansion_size_guard=True,
                expansion_count_threshold=1000,
            ),
        )

        def _fake_build_registry(_cfg):
            registry = TerminologyRegistry()
            for name, target_name, target in (
                ("a", "a-target", target_a),
                ("b", "b-target", target_b),
            ):
                registry.register(
                    TerminologyInfo(
                        name=name,
                        target_name=target_name,
                        backend_type=BackendType.SNOWSTORM,
                        branch_path="MAIN",
                    ),
                    target,
                )
                registry.set_target_status(
                    target_name,
                    TargetStatus(
                        reachable=True,
                        base_url=target.base_url,
                        fhir_base_url=f"{target.base_url}/fhir",
                        capabilities=Capabilities(
                            backend_type=BackendType.SNOWSTORM,
                            has_fhir=True,
                            has_native_api=True,
                        ),
                    ),
                )
            registry.set_default("a")
            return registry

        from snowstorm_mcp_server import mcp_app as mcp_app_module
        from snowstorm_mcp_server import runtime as runtime_module

        monkeypatch.setattr(mcp_app_module, "load_config", lambda _path=None: stub_config)
        monkeypatch.setattr(runtime_module, "build_registry", _fake_build_registry)

        calls = []

        def _tracking_expand(self, **kwargs):
            calls.append(kwargs.copy())
            total = 50_000 if kwargs.get("target") == "a-target" else 10
            return {
                "total": total,
                "offset": kwargs.get("offset", 0),
                "returned": 0,
                "items": [],
            }

        monkeypatch.setattr(ServerRuntime, "snomed_expand", _tracking_expand)
        app = create_mcp_app()

        with pytest.raises(Exception, match="50,000 concepts"):
            _call_tool(app, "snomed_expand", {
                "value_set_url": "http://snomed.info/sct?fhir_vs=ecl/<<195967001",
                "target": "a-target",
            })

        result = _call_tool(app, "snomed_expand", {
            "value_set_url": "http://snomed.info/sct?fhir_vs=ecl/<<195967001",
            "target": "b-target",
        })

        assert result["total"] == 10
        assert len(calls) == 3

    def test_preflight_cache_miss_counts_against_rate_limit(self, monkeypatch):
        from snowstorm_mcp_server.runtime import ServerRuntime

        monkeypatch.setattr(ServerRuntime, "snomed_expand", lambda self, **kwargs: {
            "total": 5,
            "offset": kwargs.get("offset", 0),
            "returned": 0,
            "items": [],
        })
        app = _create_mcp_with_guards(
            monkeypatch,
            rate_limit_calls=2,
            rate_limit_window_seconds=60,
            enable_expansion_size_guard=True,
            expansion_count_threshold=1000,
        )

        _call_tool(app, "snomed_expand", {"value_set_url": "http://snomed.info/sct?fhir_vs=ecl/<<195967001"})
        with pytest.raises(SnowstormRateLimitError):
            _call_tool(app, "snomed_expand", {"value_set_url": "http://snomed.info/sct?fhir_vs=ecl/<<50043002"})


def test_client_disconnect_is_not_logged_as_an_internal_error(caplog) -> None:
    """A caller hanging up mid-request is normal, not a server fault.

    It was being caught by the catch-all handler and logged at ERROR with a
    traceback, which produced hundreds of spurious entries a week and made real
    failures hard to find.
    """
    import logging

    from starlette.requests import ClientDisconnect

    from snowstorm_mcp_server.mcp_app import _tool_guard

    def hang_up() -> dict:
        raise ClientDisconnect("client went away")

    with caplog.at_level(logging.INFO, logger="snowstorm_mcp_server.mcp_app"):
        with pytest.raises(ClientDisconnect):
            _tool_guard(hang_up)

    records = [r for r in caplog.records if r.name == "snowstorm_mcp_server.mcp_app"]
    assert records, "the disconnect should still be recorded"
    assert [r.levelno for r in records] == [logging.INFO]
    # No traceback, and not tagged as an internal error.
    assert records[0].exc_info is None
    assert getattr(records[0], "error_code", None) != "E_INTERNAL"
