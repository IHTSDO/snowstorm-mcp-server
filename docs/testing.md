# Testing Guide

## Unit Tests (fast, no Docker)

Create a local virtual environment and install dev dependencies:

```bash
uv venv
uv pip install -e .[dev]
```

Run the unit test suite:

```bash
uv run pytest -q tests \
  -m "not integration"
```

If you already use the repo-local virtual environment:

```bash
./.venv/bin/pytest -q tests -m "not integration"
```

Run lint + typecheck + unit tests in one command:

```bash
./scripts/check.sh
```

## Integration Tests (requires Snowstorm + Snowstorm Lite)

Start the local Docker integration stack:

```bash
docker compose -f docker-compose.integration.yml up -d
```

This starts:

- Snowstorm (full) on `http://localhost:8080`
- Snowstorm Lite on `http://localhost:8082`
- Elasticsearch on `http://localhost:9200`

Import an RF2 package into both backends (required before integration tests):

```bash
dev/integration/import_snomed.sh \
  --rf2-zip /path/to/SnomedCT_InternationalRF2_PRODUCTION_YYYYMMDDT120000Z.zip
```

Run the integration suite against the provided Docker config:

```bash
SNOWSTORM_MCP_TEST_CONFIG=examples/config.docker-integration.yaml \
  ./.venv/bin/pytest -q tests/integration
```

Capture real endpoint payloads for parser contract fixtures (optional but recommended when adding new parsers/normalizers):

```bash
dev/integration/capture_live_contract_fixtures.sh --rf2-tag 20251101
```

## Notes

- Tests under `tests/integration/` are marked with `@pytest.mark.integration`.
- The integration config expects Snowstorm on `8080` and Snowstorm Lite on `8082`.
- If `8080` is already in use, stop or remap the conflicting service before starting `docker-compose.integration.yml`.
