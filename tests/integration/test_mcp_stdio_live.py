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
CLINICAL_FINDING = ("404684003", "Clinical finding")
MYOCARDIAL_INFARCTION = ("22298006", "Myocardial infarction")
ACUTE_MYOCARDIAL_INFARCTION = ("57054005", "Acute myocardial infarction")


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
                "snomed_expand",
                "snomed_lookup",
                "snomed_validate_code",
                "snomed_subsumes",
                "snowstorm_list_codesystems",
                "snowstorm_list_versions",
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
    clinical_finding_code, _clinical_finding_desc = CLINICAL_FINDING
    async with stdio_client(_server_params()) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await _call_tool(
                session,
                "snomed_lookup",
                {"code": clinical_finding_code},
            )
            payload = result.structuredContent
            assert "terminology" in payload
            assert payload["code"] == clinical_finding_code
            assert "clinical finding" in (payload.get("display") or "").lower()


@pytest.mark.integration
@pytest.mark.anyio
async def test_mcp_stdio_validate_code_and_subsumes_live() -> None:
    _ensure_local_config()
    clinical_finding_code, _clinical_finding_desc = CLINICAL_FINDING
    mi_code, mi_desc = MYOCARDIAL_INFARCTION
    ami_code, ami_desc = ACUTE_MYOCARDIAL_INFARCTION
    async with stdio_client(_server_params()) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            valid = await _call_tool(
                session,
                "snomed_validate_code",
                {"code": clinical_finding_code},
            )
            valid_payload = valid.structuredContent
            assert valid_payload["result"] is True
            assert valid_payload["code"] == clinical_finding_code

            rel = await _call_tool(
                session,
                "snomed_subsumes",
                {"code_a": mi_code, "code_b": ami_code},
            )
            rel_payload = rel.structuredContent
            assert rel_payload["outcome"] in {"subsumes", "equivalent"}, f"{mi_desc} vs {ami_desc}"


@pytest.mark.integration
@pytest.mark.anyio
async def test_mcp_stdio_snomed_expand_live() -> None:
    _ensure_local_config()
    async with stdio_client(_server_params()) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await _call_tool(
                session,
                "snomed_expand",
                {"filter": "myocard", "count": 5, "summary_only": True},
            )
            payload = result.structuredContent
            assert "terminology" in payload
            assert payload["summary_only"] is True
            assert payload["count"] == 5
            assert "raw_contains_count" in payload


@pytest.mark.integration
@pytest.mark.anyio
async def test_mcp_stdio_snowstorm_list_codesystems_and_versions_live() -> None:
    _ensure_local_config()
    async with stdio_client(_server_params()) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            cs = await _call_tool(session, "snowstorm_list_codesystems", {})
            cs_payload = cs.structuredContent
            assert "terminology" in cs_payload
            short_names = {item["short_name"] for item in cs_payload.get("code_systems", [])}
            assert "SNOMEDCT" in short_names

            versions = await _call_tool(
                session,
                "snowstorm_list_versions",
                {"code_system_short_name": "SNOMEDCT"},
            )
            versions_payload = versions.structuredContent
            version_values = {
                v.get("version") or v.get("effective_date")
                for v in versions_payload.get("versions", [])
                if isinstance(v, dict)
            }
            assert len(version_values) >= 1


@pytest.mark.integration
@pytest.mark.anyio
async def test_mcp_stdio_snowstorm_search_concepts_live() -> None:
    _ensure_local_config()
    mi_code, mi_desc = MYOCARDIAL_INFARCTION
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
            assert mi_code in concept_ids, mi_desc


@pytest.mark.integration
@pytest.mark.anyio
async def test_mcp_stdio_snowstorm_get_concept_native_live() -> None:
    _ensure_local_config()
    mi_code, _mi_desc = MYOCARDIAL_INFARCTION
    async with stdio_client(_server_params()) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            detail = await session.call_tool(
                "snowstorm_get_concept_native",
                {"concept_id": mi_code, "max_synonyms": 20},
            )
            assert detail.isError is False, f"detail failed: {detail}"
            payload = detail.structuredContent or {}
            assert payload["concept_id"] == mi_code
            assert payload["semantic_tag"] == "disorder"
            assert any("heart attack" in s.lower() for s in payload.get("synonyms", []))
