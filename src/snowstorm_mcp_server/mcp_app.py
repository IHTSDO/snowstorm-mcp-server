from __future__ import annotations

import json
import logging
import os
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
    server_mode = app_config.server_mode

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

    mcp = FastMCP(
        server_name,
        host=os.environ.get("FASTMCP_HOST", "127.0.0.1"),
        port=int(os.environ.get("FASTMCP_PORT", "8000")),
        instructions=instructions,
    )

    @mcp.tool(
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
            "Retrieve full concept details via FHIR CodeSystem/$lookup for a known SNOMED CT concept ID. "
            "Returns the FSN, synonyms, parent concepts, and all defining attributes with their values. "
            "WHEN TO USE: verifying a concept ID before using it in an ECL expression, "
            "inspecting the full attribute modelling of a specific concept, "
            "retrieving all synonyms and descriptions for a concept, "
            "checking whether a concept is active or inactive, "
            "or confirming the correct concept ID for a common attribute (e.g. finding site). "
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
        fuzzy: bool = False,
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
                fuzzy=fuzzy,
            )
        )

    @mcp.tool(
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

    # --- Snowstorm-native tools (only registered when server_mode is "snowstorm") ---
    if server_mode == "snowstorm":

        @mcp.tool(
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
        ) -> dict[str, Any]:
            return _tool_guard(
                lambda: runtime.snowstorm_list_codesystems(terminology=terminology, target=target)
            )

        @mcp.tool(
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
                "Snowstorm-native concept search by term. "
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
