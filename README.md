# snowstorm-mcp-server

[![CI](https://github.com/IHTSDO/snowstorm-mcp-server/actions/workflows/ci.yml/badge.svg)](https://github.com/IHTSDO/snowstorm-mcp-server/actions/workflows/ci.yml)

An MCP server for querying [SNOMED CT](https://www.snomed.org/) clinical terminology
via [Snowstorm](https://github.com/IHTSDO/snowstorm) and
[Snowstorm Lite](https://github.com/IHTSDO/snowstorm-lite) backends.

SNOMED CT is the world's most comprehensive clinical terminology, used in
electronic health records across 80+ countries. This server exposes SNOMED CT
lookup, search, validation, hierarchy navigation, and value set expansion
through the [Model Context Protocol (MCP)](https://modelcontextprotocol.io),
enabling AI assistants to work with clinical terminology directly.

Supports all SNOMED CT editions available on the connected backend (International,
US, UK, AU, etc.). No user account is required when connected to a public
Snowstorm instance.

This repository supports two related but distinct usage modes:

1. **Hosted remote connector**: run a public HTTPS MCP endpoint and connect Claude
   to it via the custom connector / Connector Directory flow.
2. **Self-hosted or local usage**: run the server yourself against your own
   Snowstorm or Snowstorm Lite deployment, including Claude Desktop and future
   MCPB packaging scenarios.

If you are preparing a hosted connector for Claude web/desktop/mobile, start with
[Hosted remote connector](#hosted-remote-connector). If you want to run the server
yourself against your own terminology backend, start with
[Self-hosted and local usage](#self-hosted-and-local-usage).

## Hosted remote connector

Use this mode when you are operating a public MCP endpoint, for example
`https://your-domain.example/mcp`, and want Claude to connect to it from
Anthropic's infrastructure.

### Hosted deployment (Docker)

Build and run the container:

```bash
docker build -t snowstorm-mcp-server .
docker run -p 8000:8000 snowstorm-mcp-server
```

The server starts in Streamable HTTP mode on port 8000 using the
bundled `config.docker-snowstorm.yaml` (expects a local Snowstorm at
`http://localhost:8080`). Mount your own config at runtime:

```bash
docker run -p 8000:8000 \
  -v /path/to/your/config.yaml:/app/config.yaml \
  snowstorm-mcp-server
```

For production, deploy behind an HTTPS reverse proxy or on a platform
with automatic TLS (Cloud Run, Fly.io, Railway, etc.). For public-facing
deployments, configure **per-client** rate limiting at the reverse proxy
(IP-based). MCP 2026-07-28 removed protocol-level sessions, so the
application-level per-session limit no longer applies to HTTP clients; the
global limit still caps total backend load — see
[Performance guards](#performance-guards) below.

For remote MCP connector deployments intended for Claude web/desktop, the
server enables CORS for `https://claude.ai` and `https://claude.com` on the
Streamable HTTP endpoint by default. Override the allowed origin list with
the `SNOWSTORM_MCP_CORS_ALLOW_ORIGINS` environment variable if needed
using a comma-separated list.

Set `SNOWSTORM_MCP_ALLOWED_HOSTS` to the hostnames the server is reached on
(comma-separated, e.g. `mcp.example.org,mcp.example.org:443`) to enable DNS
rebinding protection: a request whose `Origin` is present but not in the CORS
allow list is rejected with HTTP 403, and an unrecognised `Host` with HTTP 421.
This is off by default because an incomplete host list rejects all traffic. It
is enabled automatically when binding to localhost.

### MCP protocol versions

The server is dual-era: it serves the stateless
[MCP 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28)
revision and the older handshake-based revisions (`2025-11-25` and earlier)
from the same endpoint, so existing clients keep working. It runs stateless in
both cases and never mints an `Mcp-Session-Id`, which means it can be scaled
horizontally without session affinity.

### Remote connector notes

- Anthropic connects to your hosted MCP endpoint from its cloud infrastructure.
- Anthropic does **not** configure your internal Snowstorm backend settings such
  as `base_url`, `user_agent`, or target auth. Those stay in your server config.
- `manifest.json` in this repo is for local packaging scenarios, not for the
  hosted remote connector flow.

## Self-hosted and local usage

Use this mode when you want to run the MCP server yourself against your own
Snowstorm or Snowstorm Lite backend, whether locally, on private infrastructure,
or for Claude Desktop / MCPB-style packaging.

## Development quick start

```bash
uv venv
uv pip install -e ".[dev]"
uv run pytest -q
./scripts/check.sh
```

For unit vs integration test workflows (including Docker stack setup and RF2 import), see `docs/testing.md`.

## Docker integration stack (Snowstorm + Lite)

Start local containers for integration testing:

```bash
docker compose -f docker-compose.integration.yml up -d
```

Import a local RF2 archive into both Snowstorm and Snowstorm Lite:

```bash
dev/integration/import_snomed.sh \
  --rf2-zip ../SnomedCT_InternationalRF2_PRODUCTION_20251101T120000Z.zip
```

Run integration tests against each backend. Set `SNOWSTORM_MCP_TEST_CONFIG` in
your `.env` to point at the relevant config, then:

```bash
uv run pytest -q tests/integration
```

## Running the server locally

The server reads its config from a YAML file (see `example-configs/config.local.yaml`).
Create a `.env` file in the project root to set the config path and any secrets
(see `.env.example`):

```bash
cp .env.example .env
# edit .env to point at your config file
```

The server automatically loads `.env` from the current working directory at startup.
Existing shell environment variables take precedence over `.env` values.

**stdio** (for Claude Desktop and most MCP clients):

```bash
uv run snowstorm-mcp-server --transport stdio
```

Use `--log-level DEBUG|INFO|WARNING|ERROR` to control verbosity (default: `INFO`).
Logs go to stderr and are captured by Claude Desktop in `mcp-server-snowstorm.log`.
The server prints the active guard configuration on startup so you can confirm
settings are being loaded from the right config file.

### Logging

By default the server logs one JSON object per line with a UTC timestamp,
which log aggregators can parse directly:

```json
{"timestamp": "2026-07-13T10:29:04.929Z", "level": "ERROR", "logger": "snowstorm_mcp_server.mcp_app", "message": "Tool call failed [E_BACKEND_HTTP]: HTTP 502 ...", "error_code": "E_BACKEND_HTTP", "error_type": "HttpRequestError", "status_code": 502}
```

Tool failures carry structured fields (`error_code`, `error_type`,
`status_code`) so you can break an error rate down by cause. Guard trips
(rate limits, blocked ECL, traversal detection) are logged as warnings.
Use `--log-format text` for the traditional human-readable output during
local development.

**Streamable HTTP** (for HTTP-based MCP clients):

```bash
uv run snowstorm-mcp-server --transport streamable-http
```

The standalone `sse` transport was removed in favour of Streamable HTTP. The
HTTP+SSE transport has been deprecated since MCP `2025-03-26` and is formally
Deprecated under the feature lifecycle policy as of `2026-07-28`.

**Claude Desktop config example** (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "snowstorm": {
      "command": "uv",
      "args": [
        "run",
        "--project", "/path/to/snowstorm-mcp-server",
        "snowstorm-mcp-server",
        "--transport", "stdio"
      ]
    }
  }
}
```

> **Note:** Claude Desktop runs `uv` from the project directory, so it picks up
> the `.env` file automatically. If you prefer explicit env vars, pass them via
> the `"env"` key in the Claude Desktop config.

### Local packaging / MCPB installs

If you install the server from a Connector Directory entry, the manifest prompts
for a config file path and passes it as `--config` at startup. Choose one of the
YAML files in `example-configs/` for local development, or provide the path to
your own Snowstorm/Snowstorm Lite deployment config.

## Configuration

See `example-configs/config.local.yaml` (Snowstorm at `http://localhost:8080`).

### Backend type restriction

A single configuration must use **either** Snowstorm **or** Snowstorm Lite targets —
mixing both in the same config is not supported. The `server_mode` field must match
the target type (`"snowstorm"` for Snowstorm targets, `"lite"` for Lite targets).

Multiple Snowstorm Lite instances are supported (one per SNOMED edition).

### Multi-edition Lite config example

```yaml
server_mode: "lite"
default_terminology: snomedct
response_limits:
  max_expand_contains: 100
  max_search_hits: 50
  max_synonyms: 25

# Guards — all values shown are defaults. Omit the block to use defaults.
# For public-facing HTTP deployments, do per-client limiting at the reverse
# proxy: MCP 2026-07-28 removed sessions, so per_session_rate_limit_calls
# only has an effect on stdio.
# guards:
#   rate_limit_calls: 10
#   rate_limit_window_seconds: 60
#   max_concurrent_requests: 3
#   max_count_per_call: 500
#   large_result_threshold: 1000
#   max_children_calls_per_minute: 5
#   per_session_rate_limit_calls: null   # stdio only; no effect over HTTP
#   block_zero_cardinality_on_large_sets: false  # set true to block [0..0] on top-level roots
#   enable_expansion_size_guard: false   # preflight summary check for any non-summary expansion
#   expansion_count_threshold: 20000     # block if total concepts exceeds this value
#   size_cache_ttl_seconds: 86400

targets:
  lite-int:
    base_url: "http://localhost:8081"
    mode: "lite"
    terminology_name: "snomedct"
    fhir_path: "/fhir"
    auth:
      mode: "none"

  lite-us:
    base_url: "http://localhost:8082"
    mode: "lite"
    terminology_name: "snomedct-us"
    fhir_path: "/fhir"
    auth:
      mode: "bearer"
      token: "${SNOWSTORM_LITE_TOKEN}"
```

## Terminology-based routing

This server routes requests by **terminology** (SNOMED edition), not by backend target.

- **Snowstorm**: Terminologies are auto-discovered from `GET /codesystems`.
  Each code system's `shortName` (lowercased) becomes the terminology name
  (e.g., `snomedct`, `snomedct-us`). The branch path is used automatically
  for native Snowstorm operations.
- **Snowstorm Lite**: Each instance serves one terminology. Configure
  `terminology_name` in the target config.

### Default terminology

Set `default_terminology` in config to allow callers to omit the
`terminology` parameter. If only one terminology is available, it
becomes the default automatically.

### Available MCP tools

| Tool | Description | Backend |
|------|-------------|---------|
| `list_terminologies` | List available SNOMED terminologies and the default | All |
| `server_health` | Check reachability and capabilities for a terminology | All |
| `server_capabilities` | Detailed backend info for a terminology | All |
| `fhir_metadata` | FHIR CapabilityStatement summary (optional raw payload) | All |
| `snomed_expand` | FHIR ValueSet/$expand with ECL support | All |
| `snomed_lookup` | FHIR CodeSystem/$lookup | All |
| `snomed_validate_code` | FHIR CodeSystem/$validate-code | All |
| `snomed_subsumes` | FHIR CodeSystem/$subsumes | All |
| `snomed_get_ancestors` | Get ancestor concepts via IS-A hierarchy (ECL-based) | All |
| `snomed_get_children` | Get direct children of a concept (ECL-based) | All |
| `snomed_get_descendants` | Get all descendants of a concept (ECL-based) | All |
| `snowstorm_list_codesystems` | Native code system summaries | Snowstorm only |
| `snowstorm_list_versions` | Native code system versions | Snowstorm only |
| `snowstorm_search_concepts` | Native concept search by term | Snowstorm only |
| `snowstorm_get_concept_native` | Native concept detail with synonyms | Snowstorm only |

All tools accept an optional `terminology` parameter (e.g., `"snomedct-us"`).
Most tools also accept optional `target` to constrain routing/disambiguate target selection.
If omitted, the default terminology is used.

### Env secret overrides

Secrets can be injected at runtime using env vars instead of committing values:

- Placeholder interpolation in config: `${ENV_VAR}` or `${ENV_VAR:-default}`
- Target auth secret override variables:
  - `SNOWSTORM_MCP_TARGETS__<TARGET_NAME_UPPER>__AUTH__PASSWORD`
  - `SNOWSTORM_MCP_TARGETS__<TARGET_NAME_UPPER>__AUTH__TOKEN`

### Sample MCP tool calls

`list_terminologies`:

```json
{}
```

Expected response shape:

```json
{
  "terminologies": [
    {"name": "snomedct", "backend_type": "snowstorm", "branch_path": "MAIN"},
    {"name": "snomedct-us", "backend_type": "lite", "branch_path": null}
  ],
  "default_terminology": "snomedct"
}
```

`server_capabilities` (default terminology):

```json
{}
```

Expected response shape:

```json
{
  "terminology": "snomedct",
  "backend_type": "snowstorm",
  "reachable": true,
  "fhir_base_url": "http://localhost:8080/fhir",
  "capabilities": {"has_fhir": true, "has_native_api": true, "has_lite_load_package": false},
  "fhir_metadata_summary": {"resourceType": "CapabilityStatement", "fhirVersion": "4.0.1"}
}
```

`fhir_metadata` summary-only mode (omit raw CapabilityStatement body):

```json
{"terminology": "snomedct", "include_raw": false}
```

Expected response shape:

```json
{
  "terminology": "snomedct",
  "fhir_base_url": "http://localhost:8080/fhir",
  "summary": {"resourceType": "CapabilityStatement", "fhirVersion": "4.0.1"}
}
```

`snomed_lookup`:

```json
{"code": "404684003", "terminology": "snomedct"}
```

Expected response shape:

```json
{
  "terminology": "snomedct",
  "code": "404684003",
  "found": true,
  "display": "Clinical finding",
  "system": "http://snomed.info/sct"
}
```

If the code does not exist in the edition, the tool returns a structured
negative rather than an error:

```json
{
  "terminology": "snomedct",
  "code": "99999999999",
  "found": false,
  "message": "Code '99999999999' was not found in this SNOMED CT edition/version. Verify the concept ID or search for the concept by term."
}
```

`snowstorm_search_concepts` (Snowstorm only):

```json
{"terminology": "snomedct", "term": "myocardial infarction", "limit": 5}
```

Expected response shape:

```json
{
  "terminology": "snomedct",
  "term": "myocardial infarction",
  "branch": "MAIN",
  "returned": 5,
  "hits": [{"concept_id": "22298006", "pt": "Myocardial infarction"}]
}
```

### Usage examples

These examples show how an AI assistant uses the server's tools in response
to natural language questions.

**Example 1 — Looking up a clinical concept**

> **User:** "What is SNOMED CT concept 22298006?"

The assistant calls `snomed_lookup` with `{"code": "22298006"}` and receives
the concept's preferred term ("Myocardial infarction"), its SNOMED CT system
URI, and any associated properties. The assistant can then explain the concept
to the user in plain language, including its clinical meaning.

**Example 2 — Checking a hierarchical relationship**

> **User:** "Is type 2 diabetes mellitus a kind of endocrine disorder in SNOMED CT?"

The assistant calls `snomed_subsumes` with
`{"code_a": "362969004", "code_b": "44054006"}` (Endocrine disorder and
Type 2 diabetes mellitus respectively). The response indicates whether
code\_a subsumes code\_b, confirming or denying the IS-A relationship.

**Example 3 — Finding concepts by clinical term**

> **User:** "Find SNOMED CT concepts related to 'atrial fibrillation'."

The assistant calls `snomed_expand` with
`{"filter": "atrial fibrillation", "count": 10}` to search across the
terminology. The response returns matching concepts with their IDs,
preferred terms, and whether they are active, allowing the assistant to
present a concise list of clinically relevant matches.

### FHIR operations and multi-edition Snowstorm

For native Snowstorm operations (search, concept detail), the terminology's
branch path is used automatically to select the correct edition.

For FHIR operations (`$lookup`, `$validate-code`, `$subsumes`), the terminology
routes to the correct backend server. On a multi-edition Snowstorm instance,
you may additionally need to specify the FHIR `version` parameter for precise
edition targeting, as FHIR edition selection is governed by the `system`/`version`
parameters rather than branch paths.

Additional backend capability notes and v0.1 scope boundaries are documented in
`docs/v0.1-capability-matrix.md`.
Release tagging/smoke steps are in `docs/release-v0.1-checklist.md`.

## Performance guards

All tools that make backend HTTP calls share a common set of guards to protect
the Snowstorm instance from overload. These apply regardless of which tool is
called — `server_health`, `server_capabilities`, `fhir_metadata`,
`snomed_expand`, `snomed_lookup`, `snomed_validate_code`, `snomed_subsumes`,
all hierarchy tools, and all `snowstorm_*` native tools.

Guards that are always active:

| Guard | Default | Description |
|-------|---------|-------------|
| Global rate limit | 10 calls / 60 s | Rolling window across all sessions in the process |
| Concurrency cap | 3 concurrent | Semaphore on parallel Snowstorm requests |
| ECL pre-screening | — | Blocks known-expensive patterns before they hit the backend |
| Count capping | 500 max | Hard ceiling on concepts requested per `snomed_expand` or hierarchy tool call |
| Recursive traversal detection | 5 hierarchy calls / min | Catches looping `get_children` patterns |
| Expansion size threshold | disabled | Preflight `summary_only` check — blocks any expansion over N concepts regardless of concept ID |

> **Per-process only.** Guards use in-memory state. If you run multiple server
> processes behind a load balancer, each process enforces its own independent limits.
> For shared limits across processes, a Redis-backed implementation is needed.

### Per-session rate limiting (stdio only)

> **MCP 2026-07-28 removed protocol-level sessions.** Over Streamable HTTP every
> request now stands alone, so `per_session_rate_limit_calls` has nothing stable
> to key on and will never trigger — each request gets a fresh, empty window.
> The server logs a warning at startup if you set it. It still works on stdio,
> where one process serves exactly one client. **For HTTP deployments, do
> per-client limiting at the reverse proxy instead** (Nginx `limit_req`, Caddy
> `rate_limit`, Cloudflare, etc.), which keys on network identity the protocol
> no longer carries.

The global rate limit is shared across all callers and is unaffected — it
remains the control that caps total backend load:

```yaml
guards:
  rate_limit_calls: 30            # global ceiling across all callers
  rate_limit_window_seconds: 60
  per_session_rate_limit_calls: 8 # stdio only; a no-op over HTTP
```

When `per_session_rate_limit_calls` is set, each session gets its own
independent rolling window using the same `rate_limit_window_seconds`. Sessions
are tracked by object identity and are dropped automatically when the underlying
session object is garbage-collected.

### Unguarded tools

In-memory tools that do not call the Snowstorm backend are intentionally left
unguarded: `list_terminologies`.

## Snowstorm native search constraint (important)

The MCP tool `snowstorm_search_concepts` calls Snowstorm's native description search endpoint
(`GET /browser/{branch}/descriptions`), which may reject very short queries (for example `AD`, `B2`)
with HTTP `400`.

Practical guidance:
- Use at least `3` searchable characters (letters/digits).
- For short acronyms, include context (for example use a longer phrase instead of `AD`).

The MCP server validates this early: a too-short term returns a successful
response with zero hits and a `notice` field explaining the constraint, without
calling Snowstorm. Expected negative outcomes (a term that is too short, or a
`snomed_lookup` code that does not exist) are returned as structured data rather
than tool errors, so MCP error metrics only reflect genuine failures.

## Privacy policy

This server acts as a stateless proxy between an MCP client and a configured
SNOMED CT backend (Snowstorm or Snowstorm Lite). It does not collect, store,
or process personal data and does not send data to any third party beyond
the configured backend. All query content is forwarded to the backend and
discarded after the response is delivered. When per-session rate limiting is
enabled, the server holds in-memory call timestamps per session purely for
rate enforcement; this state contains no PII and is automatically discarded
when the session ends.

Responses contain SNOMED CT terminology content. Access to that content
through this server is governed by the SNOMED CT Browser License Agreement
(see [License](#license) below).
When deployed as a hosted service, standard web server access logs (IP address,
timestamp, request path) may be retained by the hosting infrastructure for
operational purposes.

For the full privacy policy, see [PRIVACY.md](PRIVACY.md).

## Support

- **Issues and bug reports:** [GitHub Issues](https://github.com/IHTSDO/snowstorm-mcp-server/issues)
- **SNOMED CT licensing and content:** [SNOMED International](https://www.snomed.org)
- **General enquiries:** [info@snomed.org](mailto:info@snomed.org)

## License

The **server software** is licensed under [Apache 2.0](LICENSE).

**SNOMED CT content** returned by this server is not covered by that licence.
Access to SNOMED CT through this server is governed by the
[SNOMED CT Browser License Agreement](SNOMED%20CT%20Browser%20License%20Agreement%20-%202026.pdf),
the same basis on which the public [SNOMED CT Browser](https://browser.ihtsdotools.org/)
is provided. In summary, End Users who do not hold a SNOMED International
Affiliate License may use this server to explore and evaluate the terminology,
but may not:

- copy SNOMED CT identifiers into a record system, database, or document
  (use as a "Data Creation System" or "Data Analysis System");
- translate or modify SNOMED CT content; or
- redistribute or share SNOMED CT content.

If you want to host this server yourself, integrate SNOMED CT into a product or
service, or otherwise use SNOMED CT beyond exploring and evaluating it, you
should obtain a full SNOMED CT licence — see
[Get SNOMED CT](https://www.snomed.org/get-snomed). SNOMED International
Affiliates may use this server within the terms of their
[Affiliate License](http://snomed.org/license-affiliate). SNOMED CT is
© SNOMED International; "SNOMED" and "SNOMED CT" are registered trademarks.
