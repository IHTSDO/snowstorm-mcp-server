from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .config import load_config
from .http_client import HttpRequestError
from .runtime import ServerRuntime, UnsupportedBackendError
from .terminology import TerminologyNotFoundError

logger = logging.getLogger(__name__)

MAX_RESPONSE_CHARS = 75_000


_READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)


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
            "For hierarchy navigation, use snomed_get_ancestors, snomed_get_children, "
            "or snomed_get_descendants instead of writing ECL manually. "
            "Use snomed_expand with ECL for advanced queries such as "
            "refset membership or attribute-based constraints."
        ),
    )

    @mcp.tool(
        description="List available SNOMED terminologies (editions) on this server.",
        annotations=_READ_ONLY_ANNOTATIONS,
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
        annotations=_READ_ONLY_ANNOTATIONS,
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
        annotations=_READ_ONLY_ANNOTATIONS,
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
        annotations=_READ_ONLY_ANNOTATIONS,
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
        annotations=_READ_ONLY_ANNOTATIONS,
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
        annotations=_READ_ONLY_ANNOTATIONS,
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
        annotations=_READ_ONLY_ANNOTATIONS,
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
        annotations=_READ_ONLY_ANNOTATIONS,
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
            "Get ancestor concepts of a SNOMED concept (parents, grandparents, etc. via IS-A hierarchy). "
            "Set direct_only=true to return only immediate parents. "
            "Optionally specify terminology and/or target; defaults to the server's default terminology."
        ),
        annotations=_READ_ONLY_ANNOTATIONS,
        structured_output=True,
    )
    def snomed_get_ancestors(
        concept_id: str,
        terminology: str | None = None,
        target: str | None = None,
        direct_only: bool = False,
        offset: int = 0,
        count: int = 50,
    ) -> dict[str, Any]:
        ecl_operator = ">!" if direct_only else ">"
        ecl_url = f"http://snomed.info/sct?fhir_vs=ecl/{ecl_operator} {concept_id}"
        return _tool_guard(
            lambda: runtime.snomed_expand(
                terminology=terminology,
                target=target,
                value_set_url=ecl_url,
                offset=offset,
                count=count,
            )
        )

    @mcp.tool(
        description=(
            "Get direct children of a SNOMED concept (one level down in the IS-A hierarchy). "
            "Optionally specify terminology and/or target; defaults to the server's default terminology."
        ),
        annotations=_READ_ONLY_ANNOTATIONS,
        structured_output=True,
    )
    def snomed_get_children(
        concept_id: str,
        terminology: str | None = None,
        target: str | None = None,
        offset: int = 0,
        count: int = 50,
    ) -> dict[str, Any]:
        ecl_url = f"http://snomed.info/sct?fhir_vs=ecl/<! {concept_id}"
        return _tool_guard(
            lambda: runtime.snomed_expand(
                terminology=terminology,
                target=target,
                value_set_url=ecl_url,
                offset=offset,
                count=count,
            )
        )

    @mcp.tool(
        description=(
            "Get all descendant concepts of a SNOMED concept (children, grandchildren, etc. via IS-A hierarchy). "
            "Use count and offset to paginate large result sets. "
            "Optionally specify terminology and/or target; defaults to the server's default terminology."
        ),
        annotations=_READ_ONLY_ANNOTATIONS,
        structured_output=True,
    )
    def snomed_get_descendants(
        concept_id: str,
        terminology: str | None = None,
        target: str | None = None,
        offset: int = 0,
        count: int = 50,
    ) -> dict[str, Any]:
        ecl_url = f"http://snomed.info/sct?fhir_vs=ecl/< {concept_id}"
        return _tool_guard(
            lambda: runtime.snomed_expand(
                terminology=terminology,
                target=target,
                value_set_url=ecl_url,
                offset=offset,
                count=count,
            )
        )

    @mcp.tool(
        description=(
            "List Snowstorm code systems with summarized latest version info "
            "(Snowstorm only; not supported on Lite). "
            "Optionally specify terminology and/or target; defaults to the server's default terminology."
        ),
        annotations=_READ_ONLY_ANNOTATIONS,
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
        annotations=_READ_ONLY_ANNOTATIONS,
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
        annotations=_READ_ONLY_ANNOTATIONS,
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
        annotations=_READ_ONLY_ANNOTATIONS,
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


def _truncate_response(result: dict[str, Any]) -> dict[str, Any]:
    """Truncate tool response if it exceeds the character limit.

    The Anthropic Connector Directory enforces a 25 000-token limit per tool
    result.  Using a conservative 3 chars-per-token estimate gives a
    75 000-character budget.  When the serialised JSON exceeds that budget
    we trim list-valued fields from the end and append a truncation notice.
    """
    serialised = json.dumps(result, default=str)
    if len(serialised) <= MAX_RESPONSE_CHARS:
        return result

    logger.warning(
        "Tool response exceeds %d chars (%d); truncating",
        MAX_RESPONSE_CHARS,
        len(serialised),
    )

    # Trim the largest list field until we fit.
    trimmed = dict(result)
    notice = (
        "Response was truncated to stay within size limits. "
        "Use more specific parameters (e.g. filter, count, offset) to narrow results."
    )
    # Find the largest list field by serialised size.
    list_fields = [
        (k, v) for k, v in trimmed.items() if isinstance(v, list) and v
    ]
    if not list_fields:
        return trimmed

    largest_key = max(list_fields, key=lambda kv: len(json.dumps(kv[1], default=str)))[0]
    items = list(trimmed[largest_key])  # copy to avoid mutating the original
    trimmed[largest_key] = items
    trimmed["_truncated"] = True
    trimmed["_truncation_notice"] = notice
    while items and len(json.dumps(trimmed, default=str)) > MAX_RESPONSE_CHARS:
        items.pop()

    return trimmed


def _tool_guard(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        result = fn()
        return _truncate_response(result)
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
