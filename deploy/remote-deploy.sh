#!/usr/bin/env bash
# Host-side deploy entrypoint, run by GitHub Actions over SSH.
#
# Install it as the forced command for the deploy key, so the key can do
# nothing except call this script (see docs/deployment.md):
#
#   command="/usr/local/bin/snowstorm-mcp-deploy",restrict ssh-ed25519 AAAA... gha-deploy
#
# The SSH client's command line arrives in $SSH_ORIGINAL_COMMAND and must be
# exactly one image reference from this repo's GHCR package. For manual use,
# pass it as the first argument instead.
#
# Host-specific settings come from /etc/snowstorm-mcp-deploy.env if present.
set -euo pipefail

[ -r /etc/snowstorm-mcp-deploy.env ] && . /etc/snowstorm-mcp-deploy.env

CONTAINER="${CONTAINER:-snowstorm-mcp}"
CONFIG_PATH="${CONFIG_PATH:-/opt/snowstorm-mcp-server/config.yaml}"
# Optional extra environment for the container (e.g. SNOWSTORM_MCP_ALLOWED_HOSTS).
ENV_FILE="${ENV_FILE:-/opt/snowstorm-mcp-server/container.env}"
PUBLISH="${PUBLISH:-127.0.0.1:8000:8000}"
MEMORY="${MEMORY:-512m}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-90}"
LOCAL_REPO="snowstorm-mcp-server"

IMAGE_REF="${SSH_ORIGINAL_COMMAND:-${1:-}}"
IMAGE_RE='^ghcr\.io/ihtsdo/snowstorm-mcp-server(@sha256:[0-9a-f]{64}|:sha-[0-9a-f]{40})$'
if ! [[ "$IMAGE_REF" =~ $IMAGE_RE ]]; then
    echo "refusing: expected ghcr.io/ihtsdo/snowstorm-mcp-server@sha256:<digest> or :sha-<commit>, got '$IMAGE_REF'" >&2
    exit 2
fi

# Serialise deploys on this host.
exec 9>"/tmp/${CONTAINER}-deploy.lock"
flock -w 600 9 || { echo "another deploy is still running" >&2; exit 1; }

log() { echo "[deploy] $*"; }

run_container() {
    local image="$1"
    local env_args=()
    [ -r "$ENV_FILE" ] && env_args=(--env-file "$ENV_FILE")
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    # --memory is mandatory: without a cgroup limit a runaway query lets the
    # host's global OOM killer pick the largest process on the box.
    docker run -d \
        --name "$CONTAINER" \
        --restart unless-stopped \
        --memory="$MEMORY" \
        -p "$PUBLISH" \
        -v "$CONFIG_PATH:/app/config.yaml:ro" \
        "${env_args[@]}" \
        "$image" >/dev/null
}

wait_healthy() {
    local deadline=$((SECONDS + HEALTH_TIMEOUT)) status
    while [ "$SECONDS" -lt "$deadline" ]; do
        status="$(docker inspect -f '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || echo missing)"
        case "$status" in
            healthy) return 0 ;;
            unhealthy|missing) break ;;
        esac
        sleep 3
    done
    log "container is '$status' after ${HEALTH_TIMEOUT}s"
    docker logs --tail 50 "$CONTAINER" 2>&1 || true
    return 1
}

[ -f "$CONFIG_PATH" ] || { echo "config not found at $CONFIG_PATH" >&2; exit 1; }

log "pulling $IMAGE_REF"
docker pull -q "$IMAGE_REF" >/dev/null

# Validate the host config against the new code before touching the running
# container. AppConfig forbids unknown keys, so a schema change that the host
# config doesn't match fails here instead of taking the service down.
log "validating $CONFIG_PATH against new image"
docker run --rm --network none -v "$CONFIG_PATH:/app/config.yaml:ro" "$IMAGE_REF" \
    python -c "from snowstorm_mcp_server.config import load_config; load_config('/app/config.yaml')"

previous="$(docker inspect -f '{{.Image}}' "$CONTAINER" 2>/dev/null || true)"

log "starting $CONTAINER"
run_container "$IMAGE_REF"

if ! wait_healthy; then
    if [ -n "$previous" ]; then
        log "rolling back to $previous"
        run_container "$previous"
        wait_healthy || log "rollback container is not healthy either, investigate now"
    fi
    exit 1
fi

# Keep the previous image tagged so it survives pruning for manual rollback;
# untagged older builds are cleaned up.
[ -n "$previous" ] && docker tag "$previous" "$LOCAL_REPO:previous"
docker tag "$IMAGE_REF" "$LOCAL_REPO:current"
docker image prune -f >/dev/null

log "deployed $IMAGE_REF"
