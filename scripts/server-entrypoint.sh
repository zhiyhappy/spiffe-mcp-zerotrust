#!/bin/bash
# Starts the SPIRE server, then (once healthy) generates a one-time join token for
# the agent node and registers the workload entries. Keeps the server in foreground.
set -euo pipefail

TRUST_DOMAIN="${TRUST_DOMAIN:-ethandemo.com}"
BOOTSTRAP_DIR="/run/spire/bootstrap"
TOKEN_FILE="${BOOTSTRAP_DIR}/agent-join-token"
NODE_ID="spiffe://${TRUST_DOMAIN}/node"

mkdir -p "${BOOTSTRAP_DIR}"

echo "[server] starting spire-server..."
spire-server run -config /opt/spire/conf/server/server.conf &
SERVER_PID=$!

echo "[server] waiting for health..."
until spire-server healthcheck >/dev/null 2>&1; do sleep 1; done
echo "[server] healthy."

# Generate a fresh join token bound to the node SPIFFE ID on every server start.
# (The agent persists its SVID, so it only consumes this on first boot.)
echo "[server] generating agent join token for ${NODE_ID}..."
TOKEN="$(spire-server token generate -spiffeID "${NODE_ID}" | awk -F': ' '/Token/ {print $2}')"
if [[ -z "${TOKEN}" ]]; then
  echo "[server] ERROR: failed to generate join token" >&2
  exit 1
fi
printf '%s' "${TOKEN}" > "${TOKEN_FILE}"
echo "[server] join token written to ${TOKEN_FILE}"

echo "[server] registering workload entries..."
TRUST_DOMAIN="${TRUST_DOMAIN}" NODE_ID="${NODE_ID}" /opt/spire/scripts/register-entries.sh || \
  echo "[server] WARN: entry registration reported an error (may already exist)"

echo "[server] bootstrap complete; server running (pid ${SERVER_PID})."
wait "${SERVER_PID}"
