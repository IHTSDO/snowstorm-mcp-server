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

## Quick start (local dev)

```bash
uv venv
uv pip install -e ".[dev]"
uv run pytest -q
./scripts/check.sh
```

For unit vs integration test workflows (including Docker stack setup and RF2 import), see `docs/testing.md`.

## Docker Integration Stack (Snowstorm + Lite)

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

## Running the server

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

**SSE / Streamable HTTP** (for HTTP-based MCP clients):

```bash
uv run snowstorm-mcp-server --transport sse
# or
uv run snowstorm-mcp-server --transport streamable-http
```

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

## Hosted deployment (Docker)

Build and run the container pointing at the public SNOMED International Snowstorm instance:

```bash
docker build -t snowstorm-mcp-server .
docker run -p 8000:8000 snowstorm-mcp-server
```

The server starts in Streamable HTTP mode on port 8000 using the
bundled `config.public-snowstorm.yaml`. Override the config at runtime:

```bash
docker run -p 8000:8000 \
  -v /path/to/your/config.yaml:/app/config.yaml \
  snowstorm-mcp-server
```

For production, deploy behind an HTTPS reverse proxy or on a platform
with automatic TLS (Cloud Run, Fly.io, Railway, etc.).

## Local config example

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
  "display": "Clinical finding",
  "system": "http://snomed.info/sct"
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

## Snowstorm native search constraint (important)

The MCP tool `snowstorm_search_concepts` calls Snowstorm's native description search endpoint
(`GET /browser/{branch}/descriptions`), which may reject very short queries (for example `AD`, `B2`)
with HTTP `400`.

Practical guidance:
- Use at least `3` searchable characters (letters/digits).
- For short acronyms, include context (for example use a longer phrase instead of `AD`).

The MCP server validates this early and returns a clear error message before calling Snowstorm.

## License and privacy

This project is licensed under [Apache 2.0](LICENSE).
See [PRIVACY.md](PRIVACY.md) for the privacy policy.
