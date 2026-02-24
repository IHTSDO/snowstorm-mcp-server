# snowstorm-mcp-server

Python MCP server for SNOMED terminology on Snowstorm and Snowstorm Lite.

## Quick start (local dev)

```bash
uv venv
uv pip install -e .[dev]
uv run pytest -q
```

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

Use the provided MCP config for local integration tests:

```bash
SNOWSTORM_MCP_TEST_CONFIG=examples/config.docker-integration.yaml ./.venv/bin/pytest -q tests/integration
```

## Local config example

See `examples/config.local.yaml` (Snowstorm at `http://localhost:8080`).

### Multi-target config example (Snowstorm + Lite)

```yaml
default_terminology: snomedct

targets:
  snowstorm:
    base_url: "http://localhost:8080"
    mode: "auto"
    fhir_path: "/fhir"
    auth:
      mode: "none"

  lite-us:
    base_url: "http://localhost:8081"
    mode: "lite"
    terminology_name: "snomedct-us"
    fhir_path: "/fhir"
    auth:
      mode: "none"
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

| Tool | Description |
|------|-------------|
| `list_terminologies` | List available SNOMED terminologies and the default |
| `server_health` | Check reachability and capabilities for a terminology |
| `server_capabilities` | Detailed backend info for a terminology |
| `fhir_metadata` | Raw FHIR CapabilityStatement for a terminology's backend |
| `snomed_lookup` | FHIR CodeSystem/$lookup |
| `snomed_validate_code` | FHIR CodeSystem/$validate-code |
| `snomed_subsumes` | FHIR CodeSystem/$subsumes |
| `snowstorm_search_concepts` | Native concept search (Snowstorm only) |
| `snowstorm_get_concept_native` | Native concept detail (Snowstorm only) |

All tools accept an optional `terminology` parameter (e.g., `"snomedct-us"`).
If omitted, the default terminology is used.

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

## Snowstorm native search constraint (important)

The MCP tool `snowstorm_search_concepts` calls Snowstorm's native description search endpoint
(`GET /browser/{branch}/descriptions`), which may reject very short queries (for example `AD`, `B2`)
with HTTP `400`.

Practical guidance:
- Use at least `3` searchable characters (letters/digits).
- For short acronyms, include context (for example use a longer phrase instead of `AD`).

The MCP server validates this early and returns a clear error message before calling Snowstorm.
