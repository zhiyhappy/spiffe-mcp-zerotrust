#!/bin/bash
# Registers the two workload SPIFFE IDs, keyed by docker container label.
# Idempotent: re-running is harmless (duplicate entries are skipped by SPIRE).
set -euo pipefail

TRUST_DOMAIN="${TRUST_DOMAIN:-ethandemo.com}"
NODE_ID="${NODE_ID:-spiffe://${TRUST_DOMAIN}/node}"

register() {
  local spiffe_id="$1" label="$2"
  echo "[entries] ${spiffe_id}  <-  docker:label:app:${label}"
  spire-server entry create \
    -parentID "${NODE_ID}" \
    -spiffeID "${spiffe_id}" \
    -selector "docker:label:app:${label}" \
    -x509SVIDTTL 3600 \
    2>&1 | grep -v "similar entry already exists" || true
}

# AI Agent (Open WebUI) side -> presented by envoy-client
register "spiffe://${TRUST_DOMAIN}/agent"      "openwebui"
# MCP side -> presented by envoy-server
register "spiffe://${TRUST_DOMAIN}/mcp-server" "mcp-server"
# OIDC discovery provider -> needs an identity to fetch the JWT bundle (JWKS) via the Workload API
register "spiffe://${TRUST_DOMAIN}/oidc-discovery-provider" "oidc-discovery-provider"

echo "[entries] current entries:"
spire-server entry show || true
