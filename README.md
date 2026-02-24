# snowstorm-mcp-server

Python MCP server for SNOMED terminology on Snowstorm and Snowstorm Lite.

## Quick start (local dev)

```bash
uv venv
uv pip install -e .[dev]
uv run pytest -q
```

## Local config example

See `examples/config.local.yaml` (Snowstorm at `http://localhost:8080`).

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

### FHIR operations and multi-edition Snowstorm

For native Snowstorm operations (search, concept detail), the terminology's
branch path is used automatically to select the correct edition.

For FHIR operations (`$lookup`, `$validate-code`, `$subsumes`), the terminology
routes to the correct backend server. On a multi-edition Snowstorm instance,
you may additionally need to specify the FHIR `version` parameter for precise
edition targeting, as FHIR edition selection is governed by the `system`/`version`
parameters rather than branch paths.

## Snowstorm native search constraint (important)

The MCP tool `snowstorm_search_concepts` calls Snowstorm's native description search endpoint
(`GET /browser/{branch}/descriptions`), which may reject very short queries (for example `AD`, `B2`)
with HTTP `400`.

Practical guidance:
- Use at least `3` searchable characters (letters/digits).
- For short acronyms, include context (for example use a longer phrase instead of `AD`).

The MCP server validates this early and returns a clear error message before calling Snowstorm.
