# Live Contract Fixtures

These fixtures are captured from real Snowstorm / Snowstorm Lite endpoints and
used by unit tests to keep parser expectations aligned with real payload shapes.

Use cases:

- Lock down parser/normalization behavior against real response shapes
- Reduce drift between integration-tested behavior and mocked unit fixtures
- Keep synthetic fixtures for edge/error cases that are hard to reproduce live

## Capture Workflow

1. Start the local integration stack and import RF2 data (see `docs/testing.md`).
2. Capture fixtures from the running backend:

```bash
dev/integration/capture_live_contract_fixtures.sh --rf2-tag 20251101
```

## Naming

- Include the endpoint/domain in the filename.
- Include the RF2 version tag when the payload depends on loaded content.
- Store the raw endpoint JSON (pretty-printed), not MCP-normalized output.

Current captured examples include:

- FHIR `CodeSystem/$lookup` (concept `404684003`)
- FHIR `CodeSystem/$validate-code` (valid + invalid examples)
- FHIR `CodeSystem/$subsumes` (`22298006` vs `57054005`)
- FHIR `ValueSet/$expand` for implicit SNOMED ValueSet (filter: `myocardial infarction`)
- Snowstorm `/codesystems`
- Snowstorm `/codesystems/SNOMEDCT/versions`
- Snowstorm `/browser/MAIN/descriptions` (term: `myocardial infarction`)
- Snowstorm `/browser/MAIN/concepts/22298006`
