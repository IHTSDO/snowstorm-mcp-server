# v0.1 Release Checklist

## 1. Version and changelog

- Confirm release branch is up to date with `main`.
- Confirm `pyproject.toml` version is `0.1.0`.
- Finalize changelog/release notes for v0.1 scope.

## 2. Quality gates

- Run local checks:
  - `./scripts/check.sh`
- Run integration checks (when release infra is available):
  - `SNOWSTORM_MCP_TEST_CONFIG=examples/config.docker-integration.yaml ./.venv/bin/pytest -q tests/integration`

## 3. Smoke test steps

- Start the server with local config and verify MCP boot:
  - `python -m snowstorm_mcp_server --config examples/config.local.yaml --help`
- Validate basic tool surface (manual smoke):
  - `list_terminologies`
  - `server_health`
  - `snomed_lookup`

## 4. Tagging and GitHub release

- Merge finalization PR to `main`.
- Create annotated tag:
  - `git tag -a v0.1.0 -m "snowstorm-mcp-server v0.1.0"`
  - `git push origin v0.1.0`
- Publish GitHub release from tag `v0.1.0` and attach release notes.
