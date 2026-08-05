"""Integration tests for performance guards against a live Snowstorm backend.

These tests verify that the guard layer works end-to-end when tools are
invoked through the MCP stdio transport against a real Snowstorm instance.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import CallToolResult

CONFIG_PATH = Path(
    os.getenv(
        "SNOWSTORM_MCP_TEST_CONFIG",
        str(Path(__file__).resolve().parents[2] / "examples" / "config.local.yaml"),
    )
)
REPO_ROOT = Path(__file__).resolve().parents[2]

ASTHMA = "195967001"
CLINICAL_FINDING = "404684003"
PROCEDURE = "71388002"
SUBSTANCE = "105590001"


def _ensure_local_config() -> None:
    if not CONFIG_PATH.exists():
        pytest.skip(f"Missing local config file: {CONFIG_PATH}")


def _server_params(*, guard_overrides: dict | None = None) -> StdioServerParameters:
    env = dict(os.environ)
    env["SNOWSTORM_MCP_CONFIG"] = str(CONFIG_PATH)
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "snowstorm_mcp_server", "--transport", "stdio"],
        env=env,
        cwd=str(REPO_ROOT),
    )


async def _call_tool(session: ClientSession, name: str, arguments: dict) -> CallToolResult:
    result = await session.call_tool(name, arguments)
    assert isinstance(result, CallToolResult)
    return result


# ── ECL pre-screening (blocked patterns) ──────────────────────────────


@pytest.mark.integration
@pytest.mark.anyio
async def test_guard_blocks_bare_clinical_finding() -> None:
    """Bare <<404684003 should be rejected with actionable guidance."""
    _ensure_local_config()
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await _call_tool(
                session,
                "snomed_expand",
                {"value_set_url": f"http://snomed.info/sct?fhir_vs=ecl/<<{CLINICAL_FINDING}"},
            )
            assert result.is_error is True
            error_text = result.content[0].text
            assert "E_QUERY_BLOCKED" in error_text
            assert "too broad" in error_text.lower()
            # Actionable: suggests a narrower subhierarchy
            assert "subhierarchy" in error_text.lower()
            assert "50043002" in error_text  # respiratory disorders example


@pytest.mark.integration
@pytest.mark.anyio
async def test_guard_blocks_bare_procedure() -> None:
    """Bare <<71388002 should be rejected with actionable guidance."""
    _ensure_local_config()
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await _call_tool(
                session,
                "snomed_expand",
                {"value_set_url": f"http://snomed.info/sct?fhir_vs=ecl/<<{PROCEDURE}"},
            )
            assert result.is_error is True
            error_text = result.content[0].text
            assert "E_QUERY_BLOCKED" in error_text
            assert "too broad" in error_text.lower()
            assert "subhierarchy" in error_text.lower()


@pytest.mark.integration
@pytest.mark.anyio
async def test_guard_blocks_bare_substance() -> None:
    """Bare <<105590001 should be rejected with actionable guidance."""
    _ensure_local_config()
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await _call_tool(
                session,
                "snomed_expand",
                {"value_set_url": f"http://snomed.info/sct?fhir_vs=ecl/<<{SUBSTANCE}"},
            )
            assert result.is_error is True
            error_text = result.content[0].text
            assert "E_QUERY_BLOCKED" in error_text
            assert "too broad" in error_text.lower()
            assert "narrow" in error_text.lower() or "constraints" in error_text.lower()


@pytest.mark.integration
@pytest.mark.anyio
async def test_guard_blocks_history_supplement() -> None:
    """History supplements should be blocked with alternative suggested."""
    _ensure_local_config()
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await _call_tool(
                session,
                "snomed_expand",
                {"value_set_url": f"http://snomed.info/sct?fhir_vs=ecl/<<{ASTHMA} {{{{+ HISTORY}}}}"},
            )
            assert result.is_error is True
            error_text = result.content[0].text
            assert "E_QUERY_BLOCKED" in error_text
            assert "History supplements" in error_text
            # Actionable: suggests the alternative tool
            assert "snomed_validate_code" in error_text


@pytest.mark.integration
@pytest.mark.anyio
async def test_guard_blocks_wildcard_on_clinical_finding() -> None:
    """Full wildcard <<404684003:*=* should be blocked with guidance."""
    _ensure_local_config()
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await _call_tool(
                session,
                "snomed_expand",
                {"value_set_url": f"http://snomed.info/sct?fhir_vs=ecl/<<{CLINICAL_FINDING}:*=*"},
            )
            assert result.is_error is True
            error_text = result.content[0].text
            assert "E_QUERY_BLOCKED" in error_text
            assert "wildcard" in error_text.lower()
            # Actionable: suggests scoping down
            assert "subhierarchy" in error_text.lower()


@pytest.mark.integration
@pytest.mark.anyio
async def test_guard_blocks_top_level_minus() -> None:
    """Top-level MINUS between bracketed expressions should be blocked with ECL guidance."""
    _ensure_local_config()
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await _call_tool(
                session,
                "snomed_expand",
                {
                    "value_set_url": (
                        "http://snomed.info/sct?fhir_vs=ecl/"
                        f"(<<{CLINICAL_FINDING}:363698007=<<80891009) MINUS (<<{PROCEDURE})"
                    ),
                },
            )
            assert result.is_error is True
            error_text = result.content[0].text
            assert "E_QUERY_BLOCKED" in error_text
            assert "MINUS" in error_text
            # Actionable: suggests MINUS inside attribute value instead
            assert "attribute value" in error_text.lower()


# ── Safe queries pass through guards ──────────────────────────────────


@pytest.mark.integration
@pytest.mark.anyio
async def test_guard_allows_scoped_query() -> None:
    """A properly scoped ECL query should pass all guards."""
    _ensure_local_config()
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await _call_tool(
                session,
                "snomed_expand",
                {
                    "value_set_url": f"http://snomed.info/sct?fhir_vs=ecl/<<{ASTHMA}",
                    "summary_only": True,
                },
            )
            assert result.is_error is False
            payload = result.structured_content
            assert payload["total"] > 0


@pytest.mark.integration
@pytest.mark.anyio
async def test_guard_allows_constrained_clinical_finding() -> None:
    """Clinical finding with attribute constraint should pass."""
    _ensure_local_config()
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            # Clinical findings with finding site = heart structure
            result = await _call_tool(
                session,
                "snomed_expand",
                {
                    "value_set_url": f"http://snomed.info/sct?fhir_vs=ecl/<<{CLINICAL_FINDING}:363698007=<<80891009",
                    "summary_only": True,
                },
            )
            assert result.is_error is False
            payload = result.structured_content
            assert payload["total"] > 0


# ── Count capping ─────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.anyio
async def test_guard_caps_count() -> None:
    """Requesting count > max_count_per_call should be silently capped."""
    _ensure_local_config()
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            # Default max is 500; request 1000
            result = await _call_tool(
                session,
                "snomed_expand",
                {
                    "value_set_url": f"http://snomed.info/sct?fhir_vs=ecl/<<{ASTHMA}",
                    "count": 1000,
                    "summary_only": False,
                },
            )
            assert result.is_error is False
            payload = result.structured_content
            # Should succeed, and returned items should not exceed the cap
            assert payload["returned"] <= 500


# ── Large result advisory ─────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.anyio
async def test_guard_injects_large_result_advisory() -> None:
    """Queries returning > large_result_threshold should get an advisory."""
    _ensure_local_config()
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            # Respiratory disorders: ~4000 concepts, well above default 1000 threshold
            result = await _call_tool(
                session,
                "snomed_expand",
                {
                    "value_set_url": "http://snomed.info/sct?fhir_vs=ecl/<<50043002",
                    "count": 5,
                    "summary_only": False,
                },
            )
            assert result.is_error is False
            payload = result.structured_content
            assert payload["total"] > 1000
            assert "_guard_advisory" in payload
            assert "large" in payload["_guard_advisory"].lower()


@pytest.mark.integration
@pytest.mark.anyio
async def test_guard_no_advisory_on_summary_only() -> None:
    """summary_only=true should suppress the large result advisory."""
    _ensure_local_config()
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await _call_tool(
                session,
                "snomed_expand",
                {
                    "value_set_url": "http://snomed.info/sct?fhir_vs=ecl/<<50043002",
                    "summary_only": True,
                },
            )
            assert result.is_error is False
            payload = result.structured_content
            assert payload["total"] > 1000
            assert "_guard_advisory" not in payload


# ── Hierarchy tools honour guards ─────────────────────────────────────


@pytest.mark.integration
@pytest.mark.anyio
async def test_guard_hierarchy_tools_work() -> None:
    """snomed_get_children/ancestors/descendants should work with guards enabled."""
    