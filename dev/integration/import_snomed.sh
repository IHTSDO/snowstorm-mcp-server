#!/usr/bin/env bash
set -euo pipefail

SNOWSTORM_URL="${SNOWSTORM_URL:-http://localhost:8080}"
SNOWSTORM_LITE_URL="${SNOWSTORM_LITE_URL:-http://localhost:8082}"
SNOWSTORM_LITE_ADMIN_PASSWORD="${SNOWSTORM_LITE_ADMIN_PASSWORD:-snowstorm-lite-admin}"
LITE_VERSION_URI="${LITE_VERSION_URI:-}"
RF2_ZIP="${RF2_ZIP:-}"
SKIP_SNOWSTORM=0
SKIP_LITE=0
POLL_TIMEOUT_SECONDS="${POLL_TIMEOUT_SECONDS:-5400}"

usage() {
  cat <<'EOF'
Usage: dev/integration/import_snomed.sh --rf2-zip /path/to/SnomedCT_...zip [options]

Options:
  --rf2-zip PATH                  RF2 zip to import into Snowstorm and Snowstorm Lite.
  --snowstorm-url URL             Snowstorm base URL (default: http://localhost:8080)
  --lite-url URL                  Snowstorm Lite base URL (default: http://localhost:8082)
  --lite-admin-password PASSWORD  Snowstorm Lite admin password.
  --lite-version-uri URI          FHIR version-uri for Lite import. If omitted, inferred from zip filename.
  --skip-snowstorm                Skip Snowstorm import.
  --skip-lite                     Skip Snowstorm Lite import.
  --poll-timeout-seconds N        Max wait time per import readiness poll (default: 5400)

Environment variables with same names are also supported.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --rf2-zip) RF2_ZIP="$2"; shift 2 ;;
    --snowstorm-url) SNOWSTORM_URL="$2"; shift 2 ;;
    --lite-url) SNOWSTORM_LITE_URL="$2"; shift 2 ;;
    --lite-admin-password) SNOWSTORM_LITE_ADMIN_PASSWORD="$2"; shift 2 ;;
    --lite-version-uri) LITE_VERSION_URI="$2"; shift 2 ;;
    --skip-snowstorm) SKIP_SNOWSTORM=1; shift ;;
    --skip-lite) SKIP_LITE=1; shift ;;
    --poll-timeout-seconds) POLL_TIMEOUT_SECONDS="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "$RF2_ZIP" ]]; then
  echo "Missing --rf2-zip" >&2
  usage
  exit 2
fi
if [[ ! -f "$RF2_ZIP" ]]; then
  echo "RF2 zip not found: $RF2_ZIP" >&2
  exit 2
fi

wait_http_ok() {
  local url="$1"
  local deadline=$((SECONDS + POLL_TIMEOUT_SECONDS))
  while (( SECONDS < deadline )); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 5
  done
  echo "Timed out waiting for $url" >&2
  return 1
}

infer_lite_version_uri() {
  local file="$1"
  local base
  base="$(basename "$file")"
  if [[ "$base" =~ _([0-9]{8})T[0-9]{6}Z\.zip$ ]]; then
    printf 'http://snomed.info/sct/900000000000207008/version/%s\n' "${BASH_REMATCH[1]}"
    return 0
  fi
  return 1
}

extract_import_id_from_headers() {
  local header_file="$1"
  python3 - "$header_file" <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8", errors="ignore").read()
for line in text.splitlines():
    if line.lower().startswith("location:"):
        m = re.search(r"/imports/([0-9a-fA-F-]+)", line)
        if m:
            print(m.group(1))
            sys.exit(0)
sys.exit(1)
PY
}

poll_snowstorm_import_status() {
  local import_id="$1"
  local url="${SNOWSTORM_URL%/}/imports/$import_id"
  local deadline=$((SECONDS + POLL_TIMEOUT_SECONDS))
  while (( SECONDS < deadline )); do
    local body
    body="$(curl -fsS "$url")"
    local status
    status="$(BODY_JSON="$body" python3 -c 'import json, os; print(json.loads(os.environ["BODY_JSON"]).get("status",""))')"
    echo "Snowstorm import $import_id status: $status"
    case "$status" in
      COMPLETED) return 0 ;;
      FAILED) echo "Snowstorm import failed: $body" >&2; return 1 ;;
      *) sleep 15 ;;
    esac
  done
  echo "Timed out waiting for Snowstorm import $import_id" >&2
  return 1
}

wait_lookup_ready() {
  local base="$1"
  local deadline=$((SECONDS + POLL_TIMEOUT_SECONDS))
  local url="${base%/}/fhir/CodeSystem/\$lookup?system=http://snomed.info/sct&code=404684003"
  while (( SECONDS < deadline )); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 10
  done
  echo "Timed out waiting for lookup readiness at $base" >&2
  return 1
}

import_snowstorm() {
  echo "Waiting for Snowstorm to start at ${SNOWSTORM_URL%/}..."
  wait_http_ok "${SNOWSTORM_URL%/}/version" || wait_http_ok "${SNOWSTORM_URL%/}/fhir/metadata"

  local headers_file
  headers_file="$(mktemp)"
  trap 'rm -f "$headers_file"' RETURN

  echo "Creating Snowstorm RF2 import job..."
  curl -fsS -D "$headers_file" \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json' \
    -X POST "${SNOWSTORM_URL%/}/imports" \
    -d '{"branchPath":"MAIN","createCodeSystemVersion":true,"type":"SNAPSHOT"}' >/dev/null

  local import_id
  import_id="$(extract_import_id_from_headers "$headers_file")"
  echo "Snowstorm import job id: $import_id"

  echo "Uploading RF2 archive to Snowstorm..."
  curl -fsS \
    -H 'Accept: application/json' \
    -X POST "${SNOWSTORM_URL%/}/imports/${import_id}/archive" \
    -F "file=@${RF2_ZIP}" >/dev/null

  poll_snowstorm_import_status "$import_id"
  wait_lookup_ready "$SNOWSTORM_URL"
  echo "Snowstorm import completed and lookup is ready."
}

import_snowstorm_lite() {
  echo "Waiting for Snowstorm Lite to start at ${SNOWSTORM_LITE_URL%/}..."
  wait_http_ok "${SNOWSTORM_LITE_URL%/}/fhir/metadata"

  local version_uri="$LITE_VERSION_URI"
  if [[ -z "$version_uri" ]]; then
    if ! version_uri="$(infer_lite_version_uri "$RF2_ZIP")"; then
      echo "Could not infer Lite version-uri from filename. Supply --lite-version-uri." >&2
      return 2
    fi
  fi
  echo "Using Snowstorm Lite version-uri: $version_uri"

  echo "Uploading RF2 archive to Snowstorm Lite..."
  curl -fsS -u "admin:${SNOWSTORM_LITE_ADMIN_PASSWORD}" \
    -F "file=@${RF2_ZIP}" \
    -F "version-uri=${version_uri}" \
    "${SNOWSTORM_LITE_URL%/}/fhir-admin/load-package" >/dev/null

  wait_lookup_ready "$SNOWSTORM_LITE_URL"
  echo "Snowstorm Lite import completed and lookup is ready."
}

echo "RF2 package: $RF2_ZIP"
if (( SKIP_SNOWSTORM == 0 )); then
  import_snowstorm
fi
if (( SKIP_LITE == 0 )); then
  import_snowstorm_lite
fi
echo "Done."
