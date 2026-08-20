#!/bin/bash
# Waits for the server to publish a join token, then starts the agent.
# On restart the agent reuses its persisted SVID (data_dir volume) and the
# unused token is simply ignored.
set -euo pipefail

TOKEN_FILE="/run/spire/bootstrap/agent-join-token"

echo "[agent] waiting for join token at ${TOKEN_FILE}..."
until [[ -s "${TOKEN_FILE}" ]]; do sleep 1; done
TOKEN="$(cat "${TOKEN_FILE}")"
echo "[agent] got join token, starting agent."

exec spire-agent run \
  -config /opt/spire/conf/agent/agent.conf \
  -joinToken "${TOKEN}"
