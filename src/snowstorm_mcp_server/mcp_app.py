from __future__ import annotations

import atexit
import json
import logging
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version as _dist_version
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.server import CacheableMethod, CacheHint
from mcp.types import ToolAnnotations
from starlette.requests import ClientDisconnect

from .config import load_config
from .guards import QueryGuards, SnowstormGuardError
from .http_client import HttpRequestError
from .runtime import ServerRuntime, UnsupportedBackendError
from .terminology import TerminologyNotFoundError

logger = logging.getLogger(__name__)

MAX_RESPONSE_CHARS = 75_000


# MCP 2026-07-28 requires ttlMs/cacheScope on tools/list and server/discover
# results. The tool set here is fixed at startup and identical for every caller
# — there is no per-user variation and no listChanged notification to invalidate
# it — so it is safe for shared intermediaries to cache. The SDK default is
# ttl_ms=0 / "private", which would make clients re-list on every conversation.
_STATIC_LIST_CACHE_HINT = CacheHint(ttl_ms=3_600_000, scope="public")
_CACHE_HINTS: dict[CacheableMethod, CacheHint] = {
    "tools/list": _STATIC_LIST_CACHE_HINT,
    "server/discover": _STATIC_LIST_CACHE_HINT,
}


def _package_version() -> str:
    """Installed distribution version, or "" when running from an unbuilt tree."""
    try:
        return _dist_version("snowstorm-mcp-server")
    except PackageNotFoundError:  # pragma: no cover - only when not pip-installed
        return ""


_READ_ONLY_ANNOTATIONS = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)


def create_mcp_app(config_path: str | Path | None = None) -> MCPServer:
    app_config = load_config(config_path)
    runtime = ServerRuntime(app_config)
    # Close the runtime's pooled per-target HTTP clients on shutdown. atexit
    # covers both transports (stdio and streamable-http) without depending on
    # a specific server lifecycle hook.
    atexit.register(runtime.close)
    server_mode = app_config.server_mode
    guards = QueryGuards(
        rate_limit_calls=app_config.guards.rate_limit_calls,
        rate_limit_window_seconds=app_config.guards.rate_limit_window_seconds,
        max_concurrent_requests=app_config.guards.max_concurrent_requests,
        max_count_per_call=app_config.guards.max_count_per_call,
        large_result_threshold=app_config.guards.large_result_threshold,
        max_children_calls_per_minute=app_config.guards.max_children_calls_per_minute,
        per_session_rate_limit_calls=app_config.guards.per_session_rate_limit_calls,
        block_zero_cardinality_on_large_sets=app_config.guards.block_zero_cardinality_on_large_sets,
        enable_expansion_size_guard=app_config.guards.enable_expansion_size_guard,
        expansion_count_threshold=app_config.guards.expansion_count_threshold,
        size_cache_ttl_seconds=app_config.guards.size_cache_ttl_seconds,
    )
    g = app_config.guards
    logger.info(
        "Guards active — rate_limit=%d/%ds, concurrency=%d, count_cap=%d",
        g.rate_limit_calls, g.rate_limit_window_seconds,
        g.max_concurrent_requests, g.max_count_per_call,
    )
    logger.info(
        "Guards active — per_session=%s, zero_cardinality_block=%s, "
        "expansion_size_guard=%s (threshold=%d)",
        g.per_session_rate_limit_calls,
        g.block_zero_cardinality_on_large_sets,
        g.enable_expansion_size_guard, g.expansion_count_threshold,
    )
    if g.per_session_rate_limit_calls is not None:
        logger.warning(
            "per_session_rate_limit_calls=%d is configured, but MCP 2026-07-28 "
            "removed protocol-level sessions: over Streamable HTTP every request "
            "gets its own session and this limit will never trigger. It still "
            "applies on stdio. Enforce per-client limits at the reverse proxy; "
            "rate_limit_calls=%d still caps total backend load.",
            g.per_session_rate_limit_calls, g.rate_limit_calls,
        )

    _ecl_guidance = (
        "\n\nTOOL SELECTION GUIDE:\n"
        "- snomed_expand: Primary tool for ECL queries, value set expansion, and concept retrieval. "
        "Use for attribute-based queries, refset membership, and counting concepts.\n"
        "- snomed_lookup: Look up full details (FSN, synonyms, attributes) for a known concept ID.\n"
        "- snomed_validate_code: Quick check whether a concept ID is valid and active.\n"
        "- snomed_get_children/ancestors/descendants: Hierarchy browsing without writing ECL.\n"
        "- snowstorm_search_concepts: Free-text search by term (Snowstorm backends only).\n\n"
        "TYPICAL WORKFLOW:\n"
        "1. snomed_get_children to explore immediate hierarchy structure\n"
        "2. snomed_expand with summary_only=true to check descendant count\n"
        "3. snomed_expand with ECL to retrieve the full set with constraints\n\n"
        "COMMONLY USED ROOT CONCEPT IDS:\n"
        "  404684003  Clinical finding       (TOO BROAD for constrained queries)\n"
        "  71388002   Procedure              (TOO BROAD for constrained queries)\n"
        "  105590001  Substance\n"
        "  410942007  Drug or medicament (subtype of Substance)\n"
        "  123037004  Body structure\n"
        "  80891009   Heart structure\n"
        "  39607008   Lung structure\n"
        "  50043002   Disorder of respiratory system\n"
        "  195967001  Asthma\n"
        "  900000000000455006  Reference set\n\n"
        "COMMONLY USED ATTRIBUTE CONCEPT IDS:\n"
        "  363698007  Finding site\n"
        "  246075003  Causative agent\n"
        "  116676008  Associated morphology\n"
        "  370135005  Pathological process\n"
        "  363699004  Direct device\n"
        "  260686004  Method\n"
        "  408732007  Subject relationship context\n"
        "  704321009  Characterizes\n"
        "  116680003  IS A (parent relationship)"
    )

    if server_mode == "lite":
        server_name = "snowstorm-lite-mcp-server"
        instructions = (
            "Use the available tools to query SNOMED terminologies via Snowstorm Lite (FHIR API). "
            "Each terminology represents a SNOMED edition (e.g. 'snomedct', 'snomedct-nz'). "
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
            + _ecl_guidance
        )
    else:
        server_name = "snowstorm-mcp-server"
        instructions = (
            "Use the available tools to query SNOMED terminologies via Snowstorm. "
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
            "refset membership or attribute-based constraints. "
            "Snowstorm-native tools (snowstorm_*) provide additional capabilities "
            "such as full-text concept search and detailed concept retrieval with synonyms."
            + _ecl_guidance
        )

    # Host/port are no longer constructor settings under the v2 SDK — they are
    # passed to streamable_http_app()/run() by __main__. The version is sent to
    # clients as serverInfo in every result's _meta under MCP 2026-07-28.
    mcp = MCPServer(
        server_name,
        instructions=instructions,
        version=_package_version(),
        cache_hints=_CACHE_HINTS,
    )

    @mcp.tool(
        title="List Terminologies",
        description=(
            "List the SNOMED CT editions and branches available on the connected server. "
            "WHEN TO USE: before running edition-specific queries to confirm available terminology keys, "
            "checking which release version is loaded, or verifying server connectivity."
        ),
        annotations=_READ_ONLY_ANNOTATIONS,
        structured_output=True,
    )
    def list_terminologies() -> dict[str, Any]:
        return {
            "terminologies": runtime.list_terminologies(),
            "default_terminology": runtime.registry.default_terminology,
            "discovery_errors": runtime.registry.discovery_errors,
        }

    @mcp.tool(
        title="Server Health Check",
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
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        guards.pre_lookup(ctx.session if ctx is not None else None)
        with guards.concurrency:
            return _tool_guard(lambda: runtime.server_health(terminology, target=target))

    @mcp.tool(
        title="Server Capabilities",
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
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        guards.pre_lookup(ctx.session if ctx is not None else None)
        with guards.concurrency:
            return _tool_guard(lambda: runtime.server_capabilities(terminology, target=target))

    @mcp.tool(
        title="FHIR Capability Metadata",
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
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        guards.pre_lookup(ctx.session if ctx is not None else None)
        with guards.concurrency:
            return _tool_guard(
                lambda: runtime.fhir_metadata(terminology, target=target, include_raw=include_raw)
            )

    @mcp.tool(
        title="SNOMED Concept Lookup",
        description=(
            "Retrieve full concept details via FHIR CodeSystem/$lookup for a known SNOMED CT concept ID. "
            "Returns the FSN, synonyms, parent concepts, and all defining attributes with their values. "
            "WHEN TO USE: verifying a concept ID before using it in an ECL expression, "
            "inspecting the full attribute modelling of a specific concept, "
            "retrieving all synonyms and descriptions for a concept, "
            "checking whether a concept is active or inactive, "
            "or confirming the correct concept ID for a common attribute (e.g. finding site). "
            "If the code does not exist, returns found=false with a message (not an error). "
            "NOT FOR: searching by text (use snomed_expand with a {{ term = \"...\" }} filter) or "
            "retrieving a concept set (use snomed_expand with ECL). "
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
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        guards.pre_lookup(ctx.session if ctx is not None else None)
        with guards.concurrency:
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
        title="SNOMED Validate Code",
        description=(
            "Validate whether a SNOMED CT concept ID exists and is currently active via FHIR "
            "CodeSystem/$validate-code. Returns true/false with the display name if valid and active. "
            "WHEN TO USE: confirming a concept ID is valid and active before storing or querying, "
            "checking whether a concept has been inactivated (retired/replaced), "
            "handling legacy codes from EHR data (check status before using in ECL), "
            "or verifying a code belongs to a specific edition or version. "
            "Preferred approach when history supplements are unavailable (they are NOT supported "
            "on this server). Use this to check individual inactive codes instead. "
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
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        guards.pre_lookup(ctx.session if ctx is not None else None)
        with guards.concurrency:
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
        title="SNOMED Subsumption Test",
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
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        guards.pre_lookup(ctx.session if ctx is not None else None)
        with guards.concurrency:
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

    _expand_description = (
            "Expand a SNOMED CT value set using FHIR ValueSet/$expand. The primary tool for "
            "retrieving concept sets via ECL (Expression Constraint Language) queries. "
            "Works on Snowstorm and Lite when FHIR is available.\n\n"
            "WHEN TO USE: retrieving concepts matching an ECL expression (most common use case), "
            "getting members of a reference set, "
            "counting concepts in a subhierarchy (use summary_only=true), "
            "text-filtering within a concept set, "
            "or retrieving attribute values across a concept set (dotted notation).\n\n"
            "VALUE SET URL FORMAT:\n"
            "  Subtype query:     http://snomed.info/sct?fhir_vs=ecl/<<CONCEPTID\n"
            "  Attribute query:   http://snomed.info/sct?fhir_vs=ecl/<<CONCEPTID:ATTR=<<VALUE\n"
            "  Refset members:    http://snomed.info/sct?fhir_vs=ecl/^REFSETID\n"
            "  Full ECL:          http://snomed.info/sct?fhir_vs=ecl/<YOUR ECL EXPRESSION>\n\n"
            "PERFORMANCE RULES (follow on every query):\n"
            "These rules protect the server from overload. Violating them causes timeouts, "
            "degraded performance, and may affect other users on shared infrastructure.\n"
            "1. SCOPE FIRST. Never root a constrained query at a top-level hierarchy concept "
            "without narrowing scope first. These roots are TOO BROAD on their own:\n"
            "     <<404684003 Clinical finding (~350k concepts)\n"
            "     <<71388002  Procedure (~100k concepts)\n"
            "     <<105590001 Substance (~70k concepts)\n"
            "     <<123037004 Body structure (~40k concepts)\n"
            "   Always narrow to a subhierarchy (e.g. <<195967001 Asthma) before adding "
            "attribute constraints, filters, or MINUS operations.\n"
            "2. COUNT BEFORE FETCHING. Always use summary_only=true as the first call to "
            "check the total size. If total > 1000, stop and confirm with the user before "
            "fetching pages. Never assume a result set is small.\n"
            "3. DO NOT PAGINATE AUTONOMOUSLY. Fetch the first page, summarise the results, "
            "and ask the user whether they need more. Never loop through all pages of a "
            "large result set without explicit user instruction.\n"
            "4. NO TOP-LEVEL MINUS ON LARGE SETS. X MINUS Y at the top level requires "
            "materialising both sets in full and will time out on large hierarchies. "
            "Always prefer MINUS inside an attribute value expression:\n"
            "     GOOD: <<X:{attr=(<<A MINUS <<B)}\n"
            "     BAD:  (<<X:attr=<<A) MINUS (<<X:attr=<<B)\n"
            "5. NO WILDCARD QUERIES ON LARGE SETS. Expressions like <<404684003:*=* or "
            "<<71388002:attr=* against top-level hierarchies are extremely expensive. "
            "Scope to a subhierarchy before using wildcards.\n"
            "6. NO RECURSIVE CHILDREN CALLS. Never call snomed_get_children in a loop to "
            "traverse a hierarchy. Use <<CONCEPTID ECL for transitive closure in one call.\n"
            "7. NO REPEATED snomed_lookup ACROSS A CONCEPT SET. Use dotted attribute "
            "notation (<<X.attrId) to retrieve attribute values across a set in one "
            "call instead of calling snomed_lookup for each concept individually.\n\n"
            "ECL QUICK REFERENCE:\n"
            "  Hierarchy operators:\n"
            "    <<X  Subtype of X (including X)\n"
            "    <X   Strict subtype of X (excluding X)\n"
            "    >>X  Supertype of X (including X)\n"
            "    >!X  Direct parents of X only (ECL 1.4)\n"
            "    <!X  Direct children of X only (ECL 1.4)\n"
            "    ^X   Member of reference set X\n"
            "  Set operators (use at top level between expressions):\n"
            "    X OR Y       Union\n"
            "    X AND Y      Intersection\n"
            "    X MINUS Y    Difference\n"
            "    WARNING: Top-level MINUS between two large expansions will TIME OUT. "
            "Prefer MINUS inside an attribute value expression instead.\n"
            "  Attribute constraints:\n"
            "    <<X : attr=<<Y                   Ungrouped (attribute may be in any role group)\n"
            "    <<X : {attr1=<<Y, attr2=<<Z}     Grouped (BOTH attributes in the SAME role group)\n"
            "    WARNING: Grouped vs ungrouped returns DIFFERENT results. Always choose deliberately.\n"
            "  MINUS inside attribute value (PREFERRED for performance):\n"
            "    <<X : {246075003=(<<105590001 MINUS <<410942007), 363698007=<<80891009}\n"
            "  Wildcards:\n"
            "    <<X : *=<<Y      Any attribute with value Y\n"
            "    <<X : attr=*     Any value for a specific attribute\n\n"
            "CARDINALITY (constrain how many times an attribute appears):\n"
            "  <<X : [0..0] attr=*          Concepts with NO instances of attr (content QA)\n"
            "  <<X : [1..1] {attr=<<Y}      Exactly one instance of attr in the same role group\n"
            "  <<X : [2..*] {attr=*}        Two or more instances of attr\n"
            "  NOTE: [0..0] does not require braces. [1..*] and above should use grouped syntax { }.\n\n"
            "DOTTED ATTRIBUTE NOTATION (returns attribute VALUES, not filtered concepts):\n"
            "  <<X . attrId     Returns the SET OF VALUES of attrId across all subtypes of X\n"
            "  This is the INVERSE of filtering: it returns destination concepts, not source.\n"
            "  Use for: exploring attribute value ranges, building value set pickers, "
            "content coverage analysis, verifying modelling scope.\n\n"
            "DESCRIPTION FILTERS (ECL 1.5/1.6):\n"
            "  <<X {{ term = \"mild\" }}                       Concepts with \"mild\" in any description\n"
            "  <<X {{ term = \"mild\", language = en }}        English descriptions only\n"
            "  <<X {{ typeId = 900000000000003001 }}          FSN filter\n"
            "  <<X {{ typeId = 900000000000013009 }}          Synonym filter\n"
            "  Multiple filters are ANDed together.\n\n"
            "CONCEPT FILTERS (ECL 1.6):\n"
            "  <<X {{ active = false }}                       Inactive concepts only\n"
            "  <<X {{ active = true }}                        Active concepts only (default)\n"
            "  <<X {{ effectiveTime > \"20200101\" }}           Modified after Jan 2020 (YYYYMMDD)\n"
            "  <<X {{ effectiveTime = \"20230901\" }}           Exact release date\n"
            "  <<X {{ effectiveTime >= \"20200101\", effectiveTime <= \"20231001\" }}  Date range\n"
            "  NOTE: Concept filters on very large sets will TIME OUT. Scope first.\n\n"
            "HISTORY SUPPLEMENTS (ECL 2.0): NOT SUPPORTED on this server (returns HTTP 500). "
            "Use snomed_validate_code to check individual inactive codes instead.\n\n"
            "FUZZY MATCHING:\n"
            "  Set fuzzy=true to enable approximate/fuzzy matching on the filter text for "
            "misspelled or partial terms (Snowstorm Lite only; silently ignored on full Snowstorm).\n\n"
            "PAGINATION:\n"
            "  ALWAYS use summary_only=true first to check total before fetching.\n"
            "  If total > 1000, confirm with user before paginating.\n"
            "  Default count=20. Raise explicitly for larger pages.\n"
            "  max_contains caps the internal buffer (default 100). "
            "Use offset for pagination.\n\n"
            "EXAMPLES:\n"
            "  Count respiratory disorders first (always do this before fetching):\n"
            "    value_set_url='http://snomed.info/sct?fhir_vs=ecl/<<50043002', summary_only=true\n"
            "  All subtypes of Asthma:\n"
            "    value_set_url='http://snomed.info/sct?fhir_vs=ecl/<<195967001'\n"
            "  Disorders by finding site (heart), ungrouped:\n"
            "    value_set_url='http://snomed.info/sct?fhir_vs=ecl/<<404684003:363698007=<<80891009'\n"
            "  Disorders caused by substance on heart, grouped (same role group):\n"
            "    value_set_url='http://snomed.info/sct?fhir_vs=ecl/<<404684003:{246075003=<<105590001,363698007=<<80891009}'\n"
            "  Excluding drug-caused (MINUS in attribute value, no timeout):\n"
            "    value_set_url='http://snomed.info/sct?fhir_vs=ecl/<<404684003:{246075003=(<<105590001 MINUS <<410942007),363698007=<<80891009}'\n"
            "  Concepts added to asthma hierarchy since Jan 2020:\n"
            "    value_set_url='http://snomed.info/sct?fhir_vs=ecl/<<195967001 {{ effectiveTime > \"20200101\" }}'\n"
            "  Inactive asthma concepts (for EHR legacy code handling):\n"
            "    value_set_url='http://snomed.info/sct?fhir_vs=ecl/<<195967001 {{ active = false }}'\n"
            "  Asthma concepts with \"mild\" in any description:\n"
            "    value_set_url='http://snomed.info/sct?fhir_vs=ecl/<<195967001 {{ term = \"mild\" }}'\n"
            "  Finding sites across all asthma subtypes (dotted notation, one call not many lookups):\n"
            "    value_set_url='http://snomed.info/sct?fhir_vs=ecl/<<195967001.363698007'\n"
            "  Clinical findings with exactly one cardiac finding site:\n"
            "    value_set_url='http://snomed.info/sct?fhir_vs=ecl/<<404684003:[1..1]{363698007=<<80891009}'\n"
            "  Clinical findings with NO finding site modelled (content QA):\n"
            "    value_set_url='http://snomed.info/sct?fhir_vs=ecl/<<404684003:[0..0]363698007=*'\n"
            "  Reference set members:\n"
            "    value_set_url='http://snomed.info/sct?fhir_vs=ecl/^723264001'"
    )
    if app_config.guards.enable_expansion_size_guard:
        _expand_description += (
            "\n\nRATE LIMIT NOTE: This server has an expansion size guard enabled. "
            "Novel ECL queries (not previously cached) require a preflight check that "
            "counts as an additional request against the rate limit. Use summary_only=true "
            "to check totals without triggering the preflight, or narrow your ECL to stay "
            "within the expansion threshold."
        )

    @mcp.tool(
        title="SNOMED Expand Value Set",
        description=_expand_description,
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
        fuzzy: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        session = ctx.session if ctx is not None else None
        capped_count = guards.pre_expand(value_set_url, count, session)
        # Threshold preflight: for non-summary calls, check the total concept count
        # first so novel large hierarchies are caught without a hardcoded allowlist.
        # The result is cached by URL so repeated calls pay no extra backend cost.
        # summary_only calls are exempt — the user is already doing the right thing.
        # The preflight query itself must respect the concurrency cap.
        if value_set_url and not summary_only:
            cache_key = json.dumps(
                {
                    "terminology": terminology,
                    "target": target,
                    "value_set_url": value_set_url,
                    "filter": filter,
                    "fuzzy": fuzzy,
                },
                sort_keys=True,
                separators=(",", ":"),
            )

            def _fetch_total() -> int:
                with guards.concurrency:
                    return _tool_guard(
                        lambda: runtime.snomed_expand(
                            terminology=terminology,
                            target=target,
                            value_set_url=value_set_url,
                            filter=filter,
                            summary_only=True,
                            count=1,
                            fuzzy=fuzzy,
                        )
                    ).get("total", 0)

            guards.expansion_preflight(cache_key, fetch_total=_fetch_total, session=session)
        with guards.concurrency:
            result = _tool_guard(
                lambda: runtime.snomed_expand(
                    terminology=terminology,
                    target=target,
                    value_set_url=value_set_url,
                    filter=filter,
                    offset=offset,
                    count=capped_count,
                    summary_only=summary_only,
                    max_contains=max_contains,
                    fuzzy=fuzzy,
                )
            )
        return guards.post_expand(result, capped_count, summary_only)

    @mcp.tool(
        title="SNOMED Get Ancestors",
        description=(
            "Get ancestor concepts of a SNOMED concept (parents, grandparents, etc. via IS-A hierarchy). "
            "Traverses upward through IS-A to find the classification position of a concept. "
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
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        capped_count = guards.pre_hierarchy(concept_id, count, ctx.session if ctx is not None else None)
        ecl_operator = ">!" if direct_only else ">"
        ecl_url = f"http://snomed.info/sct?fhir_vs=ecl/{ecl_operator} {concept_id}"
        with guards.concurrency:
            return _tool_guard(
                lambda: runtime.snomed_expand(
                    terminology=terminology,
                    target=target,
                    value_set_url=ecl_url,
                    offset=offset,
                    count=capped_count,
                )
            )

    @mcp.tool(
        title="SNOMED Get Children",
        description=(
            "Get direct IS-A children of a SNOMED concept (one level down in the hierarchy). "
            "More efficient than ECL for simple hierarchy browsing when you only need immediate children. "
            "WHEN TO USE: exploring the shape of a subhierarchy before writing an ECL query, "
            "understanding how a concept is subdivided at the next level, "
            "or lightweight hierarchy browsing without full ECL overhead. "
            "NOT FOR: transitive closure (all descendants) — use snomed_expand with <<CONCEPTID. "
            "Not for attribute-based queries — use snomed_expand with ECL. "
            "Not for filtering by description or metadata — use snomed_expand with filters. "
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
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        capped_count = guards.pre_hierarchy(concept_id, count, ctx.session if ctx is not None else None)
        ecl_url = f"http://snomed.info/sct?fhir_vs=ecl/<! {concept_id}"
        with guards.concurrency:
            return _tool_guard(
                lambda: runtime.snomed_expand(
                    terminology=terminology,
                    target=target,
                    value_set_url=ecl_url,
                    offset=offset,
                    count=capped_count,
                )
            )

    @mcp.tool(
        title="SNOMED Get Descendants",
        description=(
            "Get all descendant concepts of a SNOMED concept (children, grandchildren, etc. via IS-A hierarchy). "
            "Transitive closure downward, equivalent to <<X in ECL but returned as a hierarchy structure. "
            "Use snomed_expand with ECL if you need a flat concept list with attribute constraints instead. "
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
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        capped_count = guards.pre_hierarchy(concept_id, count, ctx.session if ctx is not None else None)
        ecl_url = f"http://snomed.info/sct?fhir_vs=ecl/< {concept_id}"
        with guards.concurrency:
            return _tool_guard(
                lambda: runtime.snomed_expand(
                    terminology=terminology,
                    target=target,
                    value_set_url=ecl_url,
                    offset=offset,
                    count=capped_count,
                )
            )

    # --- Snowstorm-native tools (only registered when server_mode is "snowstorm") ---
    if server_mode == "snowstorm":

        @mcp.tool(
            title="Snowstorm List Code Systems",
            description=(
                "List Snowstorm code systems with summarized latest version info. "
                "Optionally specify terminology and/or target; defaults to the server's default terminology."
            ),
            annotations=_READ_ONLY_ANNOTATIONS,
            structured_output=True,
        )
        def snowstorm_list_codesystems(
            terminology: str | None = None,
            target: str | None = None,
            ctx: Context | None = None,
        ) -> dict[str, Any]:
            guards.pre_lookup(ctx.session if ctx is not None else None)
            with guards.concurrency:
                return _tool_guard(
                    lambda: runtime.snowstorm_list_codesystems(terminology=terminology, target=target)
                )

        @mcp.tool(
            title="Snowstorm List Code System Versions",
            description=(
                "List versions for a Snowstorm code system short name. "
                "Optionally specify terminology and/or target for routing; defaults to the server's default terminology."
            ),
            annotations=_READ_ONLY_ANNOTATIONS,
            structured_output=True,
        )
        def snowstorm_list_versions(
            code_system_short_name: str,
            terminology: str | None = None,
            target: str | None = None,
            ctx: Context | None = None,
        ) -> dict[str, Any]:
            guards.pre_lookup(ctx.session if ctx is not None else None)
            with guards.concurrency:
                return _tool_guard(
                    lambda: runtime.snowstorm_list_versions(
                        terminology=terminology,
                        target=target,
                        code_system_short_name=code_system_short_name,
                    )
                )

        @mcp.tool(
            title="Snowstorm Search Concepts",
            description=(
                "Snowstorm-native concept search by term. "
                "Optionally specify terminology and/or target; defaults to the server's default terminology. "
                "Terms need at least 3 searchable characters (letters/digits); shorter "
                "terms return zero hits with a notice (not an error), e.g. prefer a "
                "longer phrase for acronyms."
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
            ctx: Context | None = None,
        ) -> dict[str, Any]:
            guards.pre_lookup(ctx.session if ctx is not None else None)
            with guards.concurrency:
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
            title="Snowstorm Get Concept Detail",
            description=(
                "Snowstorm-native concept detail by conceptId. "
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
            ctx: Context | None = None,
        ) -> dict[str, Any]:
            guards.pre_lookup(ctx.session if ctx is not None else None)
            with guards.concurrency:
                return _tool_guard(
                    lambda: runtime.snowstorm_get_concept_native(
                        terminology=terminology,
                        target=target,
                        concept_id=concept_id,
                        include_synonyms=include_synonyms,
                        max_synonyms=max_synonyms,
                    )
                )

    # --- Favicon for Anthropic Connector Directory listing ---------------
    _favicon_path = Path(__file__).resolve().parent / "static" / "favicon.svg"

    @mcp.custom_route("/favicon.ico", methods=["GET"])
    async def favicon(request):  # noqa: ARG001
        from starlette.responses import FileResponse, Response

        if _favicon_path.is_file():
            return FileResponse(_favicon_path, media_type="image/svg+xml")
        return Response(status_code=404)

    return mcp


def _truncate_response(result: dict[str, Any]) -> dict[str, Any]:
    """Truncate tool response if it exceeds the character limit.

    The Anthropic Connector Directory enforces a 25 000-token limit per tool
    result.  Using a conservative 3 chars-per-token estimate gives a
    75 000-character budget.  When the serialised JSON exceeds that budget
    we trim list-valued fields from the end, then drop oversized dict-valued
    fields (e.g. raw_parameters, raw FHIR metadata), and append a truncation
    notice.
    """
    serialised = json.dumps(result, default=str)
    if len(serialised) <= MAX_RESPONSE_CHARS:
        return result

    logger.warning(
        "Tool response exceeds %d chars (%d); truncating",
        MAX_RESPONSE_CHARS,
        len(serialised),
    )

    trimmed = dict(result)
    list_fields = [(k, v) for k, v in trimmed.items() if isinstance(v, list) and v]
    dict_fields = [(k, v) for k, v in trimmed.items() if isinstance(v, dict) and v]
    if not list_fields and not dict_fields:
        return trimmed

    trimmed["_truncated"] = True
    trimmed["_truncation_notice"] = (
        "Response was truncated to stay within size limits. "
        "Use more specific parameters (e.g. filter, count, offset) to narrow results."
    )

    # Pass 1: trim the largest list field from the end until we fit.
    if list_fields:
        largest_key = max(list_fields, key=lambda kv: len(json.dumps(kv[1], default=str)))[0]
        items = list(trimmed[largest_key])  # copy to avoid mutating the original
        trimmed[largest_key] = items
        # Drop a proportional chunk per iteration rather than one item at a
        # time, so large responses don't need thousands of re-serialisations.
        avg_item_size = max(1, len(json.dumps(items, default=str)) // len(items))
        while items:
            size = len(json.dumps(trimmed, default=str))
            if size <= MAX_RESPONSE_CHARS:
                break
            drop = min(len(items), max(1, (size - MAX_RESPONSE_CHARS) // avg_item_size))
            del items[-drop:]

    # Pass 2: dict-valued fields can't be trimmed item by item — replace the
    # largest with a removal marker until the response fits.
    removed_keys: set[str] = set()
    while len(json.dumps(trimmed, default=str)) > MAX_RESPONSE_CHARS:
        dict_fields = [
            (k, v)
            for k, v in trimmed.items()
            if isinstance(v, dict) and v and k not in removed_keys
        ]
        if not dict_fields:
            break
        largest_key = max(dict_fields, key=lambda kv: len(json.dumps(kv[1], default=str)))[0]
        trimmed[largest_key] = {"_removed": "Field removed to stay within size limits."}
        removed_keys.add(largest_key)

    return trimmed


def _log_tool_error(
    code: str,
    exc: Exception,
    *,
    status_code: int | None = None,
    exc_info: bool = False,
) -> None:
    """Log a tool failure with structured fields for the JSON log formatter."""
    logger.error(
        "Tool call failed [%s]: %s",
        code,
        exc,
        exc_info=exc_info,
        extra={
            "error_code": code,
            "error_type": type(exc).__name__,
            "status_code": status_code,
        },
    )


def _tool_guard(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        result = fn()
        return _truncate_response(result)
    except ClientDisconnect:
        # The caller hung up mid-request. That is ordinary client behaviour, not
        # a server fault, and it is common enough to bury real errors: logging it
        # at ERROR with a traceback produced hundreds of spurious entries per
        # week. Record it without one and let it propagate.
        logger.info("Client disconnected before the tool call completed")
        raise
    except SnowstormGuardError as exc:
        logger.warning(
            "Tool call blocked by guard: %s",
            exc,
            extra={"error_type": type(exc).__name__},
        )
        raise
    except TerminologyNotFoundError as exc:
        _log_tool_error("E_TARGET_SELECTION", exc)
        raise ValueError(f"[E_TARGET_SELECTION] {exc}") from exc
    except UnsupportedBackendError as exc:
        _log_tool_error("E_UNSUPPORTED_CAPABILITY", exc)
        raise ValueError(f"[E_UNSUPPORTED_CAPABILITY] {exc}") from exc
    except HttpRequestError as exc:
        msg = str(exc)
        if "timed out" in msg.lower():
            code = "E_BACKEND_TIMEOUT"
        elif exc.status_code in {401, 403}:
            code = "E_BACKEND_AUTH"
        elif exc.status_code is not None:
            code = "E_BACKEND_HTTP"
        else:
            code = "E_BACKEND_REQUEST"
        _log_tool_error(code, exc, status_code=exc.status_code)
        raise ValueError(f"[{code}] {msg}") from exc
    except Exception as exc:
        # Unexpected failure — log with traceback before FastMCP converts it
        # into a generic error response.
        _log_tool_error("E_INTERNAL", exc, exc_info=True)
        raise
