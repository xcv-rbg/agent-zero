#!/usr/bin/env bash
set -euo pipefail

# Restart Agent Zero Docker container with:
# - persistent data: /Users/pranshupathak/Tools/agent-zero-data  ->  /a0/usr
# - UI port: 6969 (maps to container :80)
#
# Optional env overrides:
#   A0_CONTAINER_NAME=agent-zero-manual
#   A0_IMAGE=agent-zero:latest
#   A0_DOCKERFILE=DockerfileLocal
#   A0_BRANCH=local
#   A0_HOST_DATA_DIR=/Users/pranshupathak/Tools/agent-zero-data
#   A0_SKIP_BUILD=1                 # skip docker build
#   A0_BUILD_PULL=1                 # allow pulling newer base images
#   A0_BUILD_CACHE_DATE=1700000000  # optional cache buster

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." >/dev/null 2>&1 && pwd)"

CONTAINER_NAME="${A0_CONTAINER_NAME:-agent-zero-manual}"
IMAGE="${A0_IMAGE:-agent-zero:latest}"
DOCKERFILE="${A0_DOCKERFILE:-DockerfileLocal}"
BRANCH="${A0_BRANCH:-local}"
HOST_DATA_DIR="${A0_HOST_DATA_DIR:-/home/xero/agent-zero-data}"
HOST_PORT=6969
SKIP_BUILD="${A0_SKIP_BUILD:-0}"
BUILD_PULL="${A0_BUILD_PULL:-0}"
BUILD_CACHE_DATE="${A0_BUILD_CACHE_DATE:-}"

wait_for_ready() {
  local port="$1"
  local deadline_seconds="${2:-120}"
  local start
  start="$(date +%s)"

  while true; do
    if curl -fsS "http://127.0.0.1:${port}/api/health" >/dev/null 2>&1; then
      return 0
    fi

    local now
    now="$(date +%s)"
    if (( now - start >= deadline_seconds )); then
      return 1
    fi
    sleep 2
  done
}

main() {
  cd "$REPO_ROOT"

  mkdir -p "$HOST_DATA_DIR"

  # Stop + remove old container (if any)
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

  if [[ "$SKIP_BUILD" != "1" ]]; then
    local pull_flag="--pull=false"
    if [[ "$BUILD_PULL" == "1" ]]; then
      pull_flag="--pull=true"
    fi

    # DockerfileLocal produces the full A0 runtime (recommended).
    # It uses BRANCH=local by default.
    if [[ -n "$BUILD_CACHE_DATE" ]]; then
      docker build \
        $pull_flag \
        -f "$DOCKERFILE" \
        --build-arg "BRANCH=$BRANCH" \
        --build-arg "CACHE_DATE=$BUILD_CACHE_DATE" \
        -t "$IMAGE" \
        .
    else
      docker build \
        $pull_flag \
        -f "$DOCKERFILE" \
        --build-arg "BRANCH=$BRANCH" \
        -t "$IMAGE" \
        .
    fi
  fi

  # Run fresh container
  docker run -d \
    --name "$CONTAINER_NAME" \
    --restart unless-stopped \
    -p "$HOST_PORT:80" \
    -v "$HOST_DATA_DIR:/a0/usr" \
    "$IMAGE" \
    >/dev/null

  if wait_for_ready "$HOST_PORT" 180; then
    echo "Agent Zero is up. Open: http://127.0.0.1:$HOST_PORT/"
  else
    echo "Agent Zero container started, but did not become ready in time."
    echo "Check logs: docker logs -f $CONTAINER_NAME"
    echo "Port mapping: docker port $CONTAINER_NAME 80/tcp"
    exit 2
  fi

  echo "Logs: docker logs -f $CONTAINER_NAME"
}

main "$@"
