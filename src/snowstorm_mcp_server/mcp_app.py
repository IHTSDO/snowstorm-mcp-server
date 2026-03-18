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
            "You may optionally pass a backend 'target' to constrain routing/disambiguate. "
            "Call list_terminologies first to discover available editions. "
            "ECL (Expression Constraint Language) queries are supported via snomed_expand: "
            "pass an ECL expression as value_set_url using the format "
            "'http://snomed.info/sct?fhir_vs=ecl/<ECL>' "
            "(e.g. 'http://snomed.info/sct?fhir_vs=ecl/<<404684003' for all clinical findings). "
            "Always prefer snomed_expand with ECL over snowstorm_search_concepts for "
            "hierarchy traversal, refset membership, or attribute-based queries."
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
            "Optionally specify terminology and/or target; defaults to the server's default terminology."
        ),
        structured_output=True,
    )
    def server_health(
        terminology: str | None = None,
        target: str | None = None,
    ) -> dict[str, Any]:
        return _tool_guard(lambda: runtime.server_health(terminology, target=target))

    @mcp.tool(
        description=(
            "Return backend classification and capabilities for a terminology. "
            "Optionally specify terminology and/or target; defaults to the server's default terminology."
        ),
        structured_output=True,
    )
    def server_capabilities(
        terminology: str | None = None,
        target: str | None = None,
    ) -> dict[str, Any]:
        return _tool_guard(lambda: runtime.server_capabilities(terminology, target=target))

    @mcp.tool(
        description=(
            "Return a parsed FHIR CapabilityStatement summary for a terminology's backend. "
            "Set include_raw=true (default) to also include the raw CapabilityStatement payload. "
            "Optionally specify terminology and/or target; defaults to the server's default terminology."
        ),
        structured_output=True,
    )
    def fhir_metadata(
        terminology: str | None = None,
        target: str | None = None,
        include_raw: bool = True,
    ) -> dict[str, Any]:
        return _tool_guard(
            lambda: runtime.fhir_metadata(terminology, target=target, include_raw=include_raw)
        )

    @mcp.tool(
        description=(
            "FHIR CodeSystem/$lookup for a SNOMED concept. "
            "Optionally specify terminology (e.g. 'snomedct-us') and/or target; "
            "defaults to the server's default terminology when omitted."
        ),
        structured_output=True,
    )
    def snomed_lookup(
        code: str,
        terminology: str | None = None,
        target: str | None = None,
        system: str = "http://snomed.info/sct",
        version: str | None = None,
    ) -> dict[str, Any]:
        return _tool_guard(
            lambda: runtime.snomed_lookup(
                terminology=terminology,
                target=target,
                code=code,
                system=system,
                version=version,
            )
        )

    @mcp.tool(
        description=(
            "FHIR CodeSystem/$validate-code for a SNOMED concept. "
            "Optionally specify terminology and/or target; defaults to the server's default terminology."
        ),
        structured_output=True,
    )
    def snomed_validate_code(
        code: str,
        terminology: str | None = None,
        target: str | None = None,
        system: str = "http://snomed.info/sct",
        version: str | None = None,
    ) -> dict[str, Any]:
        return _tool_guard(
            lambda: runtime.snomed_validate_code(
                terminology=terminology,
                target=target,
                code=code,
                system=system,
                version=version,
            )
        )

    @mcp.tool(
        description=(
            "FHIR CodeSystem/$subsumes for two SNOMED codes. "
            "Optionally specify terminology and/or target; defaults to the server's default terminology."
        ),
        structured_output=True,
    )
    def snomed_subsumes(
        code_a: str,
        code_b: str,
        terminology: str | None = None,
        target: str | None = None,
        system: str = "http://snomed.info/sct",
        version: str | None = None,
    ) -> dict[str, Any]:
        return _tool_guard(
            lambda: runtime.snomed_subsumes(
                terminology=terminology,
                target=target,
                code_a=code_a,
                code_b=code_b,
                system=system,
                version=version,
            )
        )

    @mcp.tool(
        description=(
            "FHIR ValueSet/$expand for SNOMED (works on Snowstorm and Lite when FHIR is available). "
            "Supports ECL (Expression Constraint Language) queries via value_set_url: "
            "pass 'http://snomed.info/sct?fhir_vs=ecl/<ECL>' to run any ECL expression. "
            "ECL examples: "
            "'http://snomed.info/sct?fhir_vs=ecl/<<404684003' (subtypes of Clinical finding), "
            "'http://snomed.info/sct?fhir_vs=ecl/^447562003' (refset members), "
            "'http://snomed.info/sct?fhir_vs=ecl/<<27624003:363698007=<<39057004' (attribute constraint). "
            "Omit value_set_url to use the default implicit SNOMED ValueSet. "
            "Use filter for text filtering within the expansion. "
            "Use summary_only=true to get only the count without returning all items."
        ),
        structured_output=True,
    )
    def snomed_expand(
        terminology: str | None = None,
        target: str | None = None,
        value_set_url: str | None = None,
        filter: str | None = None,
        offset: int = 0,
        count: int = 20,
        summary_only: bool = False,
        max_contains: int = 100,
    ) -> dict[str, Any]:
        return _tool_guard(
            lambda: runtime.snomed_expand(
                terminology=terminology,
                target=target,
                value_set_url=value_set_url,
                filter=filter,
                offset=offset,
                count=count,
                summary_only=summary_only,
                max_contains=max_contains,
            )
        )

    @mcp.tool(
        description=(
            "List Snowstorm code systems with summarized latest version info "
            "(Snowstorm only; not supported on Lite). "
            "Optionally specify terminology and/or target; defaults to the server's default terminology."
        ),
        structured_output=True,
    )
    def snowstorm_list_codesystems(
        terminology: str | None = None,
        target: str | None = None,
    ) -> dict[str, Any]:
        return _tool_guard(
            lambda: runtime.snowstorm_list_codesystems(terminology=terminology, target=target)
        )

    @mcp.tool(
        description=(
            "List versions for a Snowstorm code system short name (Snowstorm only; not supported on Lite). "
            "Optionally specify terminology and/or target for routing; defaults to the server's default terminology."
        ),
        structured_output=True,
    )
    def snowstorm_list_versions(
        code_system_short_name: str,
        terminology: str | None = None,
        target: str | None = None,
    ) -> dict[str, Any]:
        return _tool_guard(
            lambda: runtime.snowstorm_list_versions(
                terminology=terminology,
                target=target,
                code_system_short_name=code_system_short_name,
            )
        )

    @mcp.tool(
        description=(
            "Snowstorm-native concept search by term (Snowstorm only; not supported on Lite). "
            "Optionally specify terminology and/or target; defaults to the server's default terminology. "
            "Backend may reject very short terms; use at least 3 searchable characters "
            "(letters/digits), e.g. prefer a longer phrase for acronyms."
        ),
        structured_output=True,
    )
    def snowstorm_search_concepts(
        term: str,
        terminology: str | None = None,
        target: str | None = None,
        limit: int = 10,
        active_only: bool = True,
    ) -> dict[str, Any]:
        return _tool_guard(
            lambda: runtime.snowstorm_search_concepts(
                terminology=terminology,
                target=target,
                term=term,
                limit=limit,
                active_only=active_only,
            )
        )

    @mcp.tool(
        description=(
            "Snowstorm-native concept detail by conceptId (Snowstorm only; not supported on Lite). "
            "Optionally specify terminology and/or target; defaults to the server's default terminology."
        ),
        structured_output=True,
    )
    def snowstorm_get_concept_native(
        concept_id: str,
        terminology: str | None = None,
        target: str | None = None,
        include_synonyms: bool = True,
        max_synonyms: int = 15,
    ) -> dict[str, Any]:
        return _tool_guard(
            lambda: runtime.snowstorm_get_concept_native(
                terminology=terminology,
                target=target,
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
        raise ValueError(f"[E_TARGET_SELECTION] {exc}") from exc
    except UnsupportedBackendError as exc:
        raise ValueError(f"[E_UNSUPPORTED_CAPABILITY] {exc}") from exc
    except HttpRequestError as exc:
        msg = str(exc)
        if "timed out" in msg.lower():
            raise ValueError(f"[E_BACKEND_TIMEOUT] {msg}") from exc
        if exc.status_code in {401, 403}:
            raise ValueError(f"[E_BACKEND_AUTH] {msg}") from exc
        if exc.status_code is not None:
            raise ValueError(f"[E_BACKEND_HTTP] {msg}") from exc
        raise ValueError(f"[E_BACKEND_REQUEST] {msg}") from exc
