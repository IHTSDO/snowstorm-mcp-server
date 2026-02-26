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


def _ensure_local_config() -> None:
    if not CONFIG_PATH.exists():
        pytest.skip(f"Missing local config file: {CONFIG_PATH}")


async def _call_tool(session: ClientSession, name: str, arguments: dict) -> CallToolResult:
    result = await session.call_tool(name, arguments)
    assert isinstance(result, CallToolResult)
    assert result.isError is False, f"{name} failed: {result}"
    return result


def _server_params() -> StdioServerParameters:
    env = dict(os.environ)
    env["SNOWSTORM_MCP_CONFIG"] = str(CONFIG_PATH)
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "snowstorm_mcp_server", "--transport", "stdio"],
        env=env,
        cwd=str(REPO_ROOT),
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_mcp_stdio_lists_terminologies_and_capabilities() -> None:
    _ensure_local_config()
    async with stdio_client(_server_params()) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            tools = await session.list_tools()
            tool_names = {tool.name for tool in tools.tools}
            assert {
                "list_terminologies",
                "server_health",
                "server_capabilities",
                "snomed_lookup",
                "snomed_validate_code",
                "snomed_subsumes",
                "snowstorm_search_concepts",
                "snowstorm_get_concept_native",
            } <= tool_names

            list_result = await _call_tool(session, "list_terminologies", {})
            terminologies = list_result.structuredContent["terminologies"]
            terminology_names = {t["name"] for t in terminologies}
            assert len(terminology_names) >= 1

            # Use the default terminology (auto-discovered from Snowstorm)
            cap_result = await _call_tool(session, "server_capabilities", {})
            payload = cap_result.structuredContent
            assert "terminology" in payload
            assert payload["capabilities"]["has_fhir"] is True
            assert payload["backend_type"] in {"snowstorm", "unknown"}

            meta_summary = await _call_tool(
                session,
                "fhir_metadata",
                {"include_raw": False},
            )
            meta_payload = meta_summary.structuredContent
            assert "summary" in meta_payload
            assert "metadata" not in meta_payload
            assert meta_payload["summary"]["resourceType"] == "CapabilityStatement"


@pytest.mark.integration
@pytest.mark.anyio
async def test_mcp_stdio_snomed_lookup_live() -> None:
    _ensure_local_config()
    async with stdio_client(_server_params()) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await _call_tool(
                session,
                "snomed_lookup",
                {"code": "404684003"},
            )
            payload = result.structuredContent
            assert "terminology" in payload
            assert payload["code"] == "404684003"
            assert "clinical finding" in (payload.get("display") or "").lower()


@pytest.mark.integration
@pytest.mark.anyio
async def test_mcp_stdio_validate_code_and_subsumes_live() -> None:
    _ensure_local_config()
    async with stdio_client(_server_params()) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            valid = await _call_tool(
                session,
                "snomed_validate_code",
                {"code": "404684003"},
            )
            valid_payload = valid.structuredContent
            assert valid_payload["result"] is True
            assert valid_payload["code"] == "404684003"

            rel = await _call_tool(
                session,
                "snomed_subsumes",
                {"code_a": "22298006", "code_b": "57054005"},
            )
            rel_payload = rel.structuredContent
            assert rel_payload["outcome"] in {"subsumes", "equivalent"}


@pytest.mark.integration
@pytest.mark.anyio
async def test_mcp_stdio_snowstorm_search_concepts_live() -> None:
    _ensure_local_config()
    async with stdio_client(_server_params()) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            search_ok = await session.call_tool(
                "snowstorm_search_concepts",
                {"term": "myocardial infarction", "limit": 10},
            )
            assert search_ok.isError is False, f"search failed: {search_ok}"
            payload = search_ok.structuredContent or {}
            assert "terminology" in payload
            concept_ids = {hit["concept_id"] for hit in payload.get("hits", [])}
            assert "22298006" in concept_ids


@pytest.mark.integration
@pytest.mark.anyio
async def test_mcp_stdio_snowstorm_get_concept_native_live() -> None:
    _ensure_local_config()
    async with stdio_client(_server_params()) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            detail = await session.call_tool(
                "snowstorm_get_concept_native",
                {"concept_id": "22298006", "max_synonyms": 20},
            )
            assert detail.isError is False, f"detail failed: {detail}"
            payload = detail.structuredContent or {}
            assert payload["concept_id"] == "22298006"
            assert payload["semantic_tag"] == "disorder"
            assert any("heart attack" in s.lower() for s in payload.get("synonyms", []))
