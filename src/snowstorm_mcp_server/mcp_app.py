from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import load_config
from .http_client import HttpRequestError
from .runtime import ServerRuntime, UnsupportedBackendError
from .terminology import TerminologyNotFoundError


def create_mcp_app(config_path: str | Path | None = None) -> FastMCP:
    app_config = load_config(config_path)
    runtime = ServerRuntime(app_config)

    mcp = FastMCP(
        "snowstorm-mcp-server",
        instructions=(
            "Use the available tools to query SNOMED terminologies. "
            "Each terminology represents a SNOMED edition (e.g. 'snomedct', 'snomedct-us'). "
            "If you omit the 'terminology' parameter, the server's default terminology is used. "
            "Call list_terminologies first to discover available editions."
        ),
    )

    @mcp.tool(
        description="List available SNOMED terminologies (editions) on this server.",
        structured_output=True,
    )
    def list_terminologies() -> dict[str, Any]:
        return {
            "terminologies": runtime.list_terminologies(),
            "default_terminology": runtime.registry.default_terminology,
        }

    @mcp.tool(
        description=(
            "Return reachability and basic backend capability flags. "
            "Optionally specify terminology; defaults to the server's default."
        ),
        structured_output=True,
    )
    def server_health(terminology: str | None = None) -> dict[str, Any]:
        return _tool_guard(lambda: runtime.server_health(terminology))

    @mcp.tool(
        description=(
            "Return backend classification and capabilities for a terminology. "
            "Optionally specify terminology; defaults to the server's default."
        ),
        structured_output=True,
    )
    def server_capabilities(terminology: str | None = None) -> dict[str, Any]:
        return _tool_guard(lambda: runtime.server_capabilities(terminology))

    @mcp.tool(
        description=(
            "Return a parsed FHIR CapabilityStatement summary for a terminology's backend. "
            "Set include_raw=true (default) to also include the raw CapabilityStatement payload. "
            "Optionally specify terminology; defaults to the server's default."
        ),
        structured_output=True,
    )
    def fhir_metadata(
        terminology: str | None = None,
        include_raw: bool = True,
    ) -> dict[str, Any]:
        return _tool_guard(lambda: runtime.fhir_metadata(terminology, include_raw=include_raw))

    @mcp.tool(
        description=(
            "FHIR CodeSystem/$lookup for a SNOMED concept. "
            "Optionally specify terminology (e.g. 'snomedct-us'); "
            "defaults to the server's default terminology."
        ),
        structured_output=True,
    )
    def snomed_lookup(
        code: str,
        terminology: str | None = None,
        system: str = "http://snomed.info/sct",
        version: str | None = None,
    ) -> dict[str, Any]:
        return _tool_guard(
            lambda: runtime.snomed_lookup(
                terminology=terminology, code=code, system=system, version=version
            )
        )

    @mcp.tool(
        description=(
            "FHIR CodeSystem/$validate-code for a SNOMED concept. "
            "Optionally specify terminology; defaults to the server's default."
        ),
        structured_output=True,
    )
    def snomed_validate_code(
        code: str,
        terminology: str | None = None,
        system: str = "http://snomed.info/sct",
        version: str | None = None,
    ) -> dict[str, Any]:
        return _tool_guard(
            lambda: runtime.snomed_validate_code(
                terminology=terminology, code=code, system=system, version=version
            )
        )

    @mcp.tool(
        description=(
            "FHIR CodeSystem/$subsumes for two SNOMED codes. "
            "Optionally specify terminology; defaults to the server's default."
        ),
        structured_output=True,
    )
    def snomed_subsumes(
        code_a: str,
        code_b: str,
        terminology: str | None = None,
        system: str = "http://snomed.info/sct",
        version: str | None = None,
    ) -> dict[str, Any]:
        return _tool_guard(
            lambda: runtime.snomed_subsumes(
                terminology=terminology,
                code_a=code_a,
                code_b=code_b,
                system=system,
                version=version,
            )
        )

    @mcp.tool(
        description=(
            "Snowstorm-native concept search by term (Snowstorm only; not supported on Lite). "
            "Optionally specify terminology; defaults to the server's default. "
            "Backend may reject very short terms; use at least 3 searchable characters "
            "(letters/digits), e.g. prefer a longer phrase for acronyms."
        ),
        structured_output=True,
    )
    def snowstorm_search_concepts(
        term: str,
        terminology: str | None = None,
        limit: int = 10,
        active_only: bool = True,
    ) -> dict[str, Any]:
        return _tool_guard(
            lambda: runtime.snowstorm_search_concepts(
                terminology=terminology,
                term=term,
                limit=limit,
                active_only=active_only,
            )
        )

    @mcp.tool(
        description=(
            "Snowstorm-native concept detail by conceptId (Snowstorm only; not supported on Lite). "
            "Optionally specify terminology; defaults to the server's default."
        ),
        structured_output=True,
    )
    def snowstorm_get_concept_native(
        concept_id: str,
        terminology: str | None = None,
        include_synonyms: bool = True,
        max_synonyms: int = 15,
    ) -> dict[str, Any]:
        return _tool_guard(
            lambda: runtime.snowstorm_get_concept_native(
                terminology=terminology,
                concept_id=concept_id,
                include_synonyms=include_synonyms,
                max_synonyms=max_synonyms,
            )
        )

    return mcp


def _tool_guard(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return fn()
    except TerminologyNotFoundError as exc:
        raise ValueError(str(exc)) from exc
    except UnsupportedBackendError as exc:
        raise ValueError(str(exc)) from exc
    except HttpRequestError as exc:
        raise ValueError(f"Backend request failed: {exc}") from exc
