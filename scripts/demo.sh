#!/usr/bin/env bash
# End-to-end validation of the SPIFFE demo. Run from the repo root on the VM.
set -uo pipefail

echo "############################################################"
echo "# 1. SPIRE registration entries"
echo "############################################################"
docker compose exec -T spire-server spire-server entry show

echo
echo "############################################################"
echo "# 2. Legitimate call: Open WebUI -> localhost:10000 (client sidecar)"
echo "#    -> mTLS -> mcp-server:9000 (server sidecar) -> 127.0.0.1:8000 (MCP)"
echo "############################################################"
docker compose exec -T open-webui bash -lc '
  curl -sS -m 10 http://localhost:10000/mcp \
    -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2024-11-05\",\"capabilities\":{},\"clientInfo\":{\"name\":\"demo\",\"version\":\"1\"}}}"
' && echo "  -> mTLS path OK"

echo
echo "############################################################"
echo "# 3. Hacker container (no sidecar / no SVID) -> should be REJECTED"
echo "############################################################"
docker compose exec -T hacker sh /attack.sh

echo
echo "############################################################"
echo "# 4. Public OIDC discovery / JWKS (served via Caddy)"
echo "############################################################"
curl -sS "https://${PUBLIC_DOMAIN:-spiffe.ethandemo.com}/.well-known/openid-configuration" | head -c 600; echo

echo
echo "############################################################"
echo "# 5. Workload federation (B): SPIFFE JWT-SVID -> Entra -> Key Vault (no secret)"
echo "#    Requires the Entra federated credential + Key Vault (see README)."
echo "############################################################"
if [ -n "${ENTRA_TENANT_ID:-}" ] && [ -n "${ENTRA_CLIENT_ID:-}" ]; then
  echo "--- 5a. LEGIT agent (spiffe://ethandemo.com/agent) -> should SUCCEED ---"
  docker compose exec -T federation-demo /federate.sh || true
  echo
  echo "--- 5b. IMPOSTOR (spiffe://ethandemo.com/mcp-server) -> should be REJECTED ---"
  docker compose exec -T federation-impostor /federate.sh || true
else
  echo "  (skipped: export ENTRA_TENANT_ID and ENTRA_CLIENT_ID to run this step)"
fi
