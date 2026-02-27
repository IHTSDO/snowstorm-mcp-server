#!/usr/bin/env bash
set -euo pipefail

SNOWSTORM_URL="${SNOWSTORM_URL:-http://localhost:8080}"
OUT_ROOT="${OUT_ROOT:-tests/fixtures/live_contracts}"
RF2_TAG="${RF2_TAG:-20251101}"

usage() {
  cat <<'EOF'
Capture live Snowstorm endpoint payloads for parser contract fixtures.

Usage:
  dev/integration/capture_live_contract_fixtures.sh [options]

Options:
  --snowstorm-url URL   Snowstorm base URL (default: http://localhost:8080)
  --out-root DIR        Output root directory (default: tests/fixtures/live_contracts)
  --rf2-tag TAG         Version tag suffix for filenames (default: 20251101)

Examples:
  dev/integration/capture_live_contract_fixtures.sh
  RF2_TAG=20251101 dev/integration/capture_live_contract_fixtures.sh
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --snowstorm-url) SNOWSTORM_URL="$2"; shift 2 ;;
    --out-root) OUT_ROOT="$2"; shift 2 ;;
    --rf2-tag) RF2_TAG="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

SNOWSTORM_OUT_DIR="${OUT_ROOT%/}/snowstorm"
FHIR_OUT_DIR="${OUT_ROOT%/}/fhir"

mkdir -p "$SNOWSTORM_OUT_DIR" "$FHIR_OUT_DIR"

capture_json() {
  local url="$1"
  local out="$2"
  echo "Capturing $url -> $out"
  curl -fsS "$url" | python3 -m json.tool > "$out"
}

capture_json \
  "${SNOWSTORM_URL%/}/codesystems/SNOMEDCT/versions" \
  "${SNOWSTORM_OUT_DIR%/}/codesystems_snomedct_versions_${RF2_TAG}.json"

capture_json \
  "${SNOWSTORM_URL%/}/codesystems" \
  "${SNOWSTORM_OUT_DIR%/}/codesystems_${RF2_TAG}.json"

capture_json \
  "$(curl -fsS -G -o /dev/null -w '%{url_effective}' \
    "${SNOWSTORM_URL%/}/fhir/ValueSet/\$expand" \
    --data-urlencode 'url=http://snomed.info/sct?fhir_vs' \
    --data-urlencode 'filter=myocardial infarction' \
    --data 'count=10')" \
  "${FHIR_OUT_DIR%/}/valueset_expand_snomed_myocardial_infarction_${RF2_TAG}.json"

capture_json \
  "$(curl -fsS -G -o /dev/null -w '%{url_effective}' \
    "${SNOWSTORM_URL%/}/fhir/CodeSystem/\$lookup" \
    --data-urlencode 'system=http://snomed.info/sct' \
    --data 'code=404684003')" \
  "${FHIR_OUT_DIR%/}/codesystem_lookup_404684003_${RF2_TAG}.json"

capture_json \
  "$(curl -fsS -G -o /dev/null -w '%{url_effective}' \
    "${SNOWSTORM_URL%/}/fhir/CodeSystem/\$subsumes" \
    --data-urlencode 'system=http://snomed.info/sct' \
    --data 'codeA=22298006' \
    --data 'codeB=57054005')" \
  "${FHIR_OUT_DIR%/}/codesystem_subsumes_22298006_57054005_${RF2_TAG}.json"

echo "Capturing validate-code payloads..."
curl -fsS \
  -H 'Content-Type: application/fhir+json' \
  -X POST "${SNOWSTORM_URL%/}/fhir/CodeSystem/\$validate-code" \
  -d '{"resourceType":"Parameters","parameter":[{"name":"url","valueUri":"http://snomed.info/sct"},{"name":"code","valueCode":"404684003"}]}' \
  | python3 -m json.tool > "${FHIR_OUT_DIR%/}/codesystem_validate_code_404684003_valid_${RF2_TAG}.json"

curl -fsS \
  -H 'Content-Type: application/fhir+json' \
  -X POST "${SNOWSTORM_URL%/}/fhir/CodeSystem/\$validate-code" \
  -d '{"resourceType":"Parameters","parameter":[{"name":"url","valueUri":"http://snomed.info/sct"},{"name":"code","valueCode":"999999999999999999"}]}' \
  | python3 -m json.tool > "${FHIR_OUT_DIR%/}/codesystem_validate_code_invalid_${RF2_TAG}.json"

capture_json \
  "$(curl -fsS -G -o /dev/null -w '%{url_effective}' \
    "${SNOWSTORM_URL%/}/browser/MAIN/descriptions" \
    --data-urlencode 'term=myocardial infarction' \
    --data 'active=true' \
    --data 'limit=40')" \
  "${SNOWSTORM_OUT_DIR%/}/browser_main_descriptions_myocardial_infarction_${RF2_TAG}.json"

capture_json \
  "${SNOWSTORM_URL%/}/browser/MAIN/concepts/22298006" \
  "${SNOWSTORM_OUT_DIR%/}/browser_main_concept_22298006_${RF2_TAG}.json"

echo "Done."
