#!/usr/bin/env bash
set -euo pipefail

ROOT="${CODEY_ROOT:-/opt/codey}"
COMPOSE_FILE="${CODEY_COMPOSE_FILE:-$ROOT/docker-compose.droplet.yml}"
OUT_DIR="${CODEY_SCAN_DIR:-$ROOT/var/deep-scan}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TMP_JSON="$(mktemp)"
DOCKER_BIN="${CODEY_DOCKER_BIN:-/usr/bin/docker}"
BACKEND_PYTHON_BIN="${CODEY_BACKEND_PYTHON_BIN:-python3}"

cleanup() {
  rm -f "$TMP_JSON"
}

handle_interrupt() {
  cleanup
  exit 130
}

handle_terminate() {
  cleanup
  exit 143
}

trap cleanup EXIT
trap handle_interrupt INT
trap handle_terminate TERM

mkdir -p "$OUT_DIR"

cd "$ROOT"
"$DOCKER_BIN" compose -f "$COMPOSE_FILE" up -d backend >/dev/null
"$DOCKER_BIN" compose -f "$COMPOSE_FILE" exec -T backend \
  env PYTHONPATH=/app "$BACKEND_PYTHON_BIN" /app/scripts/deep_repo_scan.py /app > "$TMP_JSON"

install -m 0644 "$TMP_JSON" "$OUT_DIR/latest.json"
install -m 0644 "$TMP_JSON" "$OUT_DIR/$TIMESTAMP.json"

find "$OUT_DIR" -type f -name '*.json' -mtime +7 -delete
echo "[$(date -u +%FT%TZ)] updated $OUT_DIR/latest.json"
