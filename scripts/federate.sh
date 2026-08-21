#!/bin/bash
# Workload federation demo: use a SPIRE JWT-SVID to obtain a Microsoft Entra token
# WITHOUT any client secret, then read a secret from Azure Key Vault with it.
#
#   1. Fetch a JWT-SVID from SPIRE (audience api://AzureADTokenExchange).
#   2. Present it to Entra as an OIDC client assertion (grant_type=client_credentials),
#      requesting a token for Key Vault (scope https://vault.azure.net/.default).
#   3. Entra validates the assertion against the App's Federated Credential by fetching
#      SPIRE's JWKS from https://spiffe.ethandemo.com/keys.
#   4. Use the returned token to GET a secret from Key Vault.
#
# The SAME script runs in both `federation-demo` (identity spiffe://ethandemo.com/agent
# -> SUCCESS) and `federation-impostor` (identity spiffe://ethandemo.com/mcp-server ->
# Entra rejects with AADSTS700213: no matching federated identity record).
#
# Env (see .env): ENTRA_TENANT_ID, ENTRA_CLIENT_ID, KEY_VAULT_NAME, SECRET_NAME.
set -euo pipefail

TENANT_ID="${ENTRA_TENANT_ID:?set ENTRA_TENANT_ID in .env}"
CLIENT_ID="${ENTRA_CLIENT_ID:?set ENTRA_CLIENT_ID in .env}"
SOCK="/tmp/spire-sockets/api.sock"
AUDIENCE="api://AzureADTokenExchange"
SCOPE="${AZURE_SCOPE:-https://vault.azure.net/.default}"
KV_NAME="${KEY_VAULT_NAME:-}"
SECRET_NAME="${SECRET_NAME:-demo-secret}"

b64url_decode() {
  local s="$1" pad
  pad=$(( (4 - ${#s} % 4) % 4 ))
  printf '%s' "$s$(printf '=%.0s' $(seq 1 $pad 2>/dev/null))" | tr '_-' '/+' | base64 -d 2>/dev/null || true
}
json_get() { printf '%s' "$1" | sed -n "s/.*\"$2\":\"\([^\"]*\)\".*/\1/p" | head -n1; }

echo "== 1) Fetch JWT-SVID from SPIRE (audience=${AUDIENCE}) =="
# Capture the full output first, THEN extract. Piping spire-agent directly into
# `grep -m1` makes grep close the pipe early, which SIGPIPE-kills spire-agent while
# it is still writing the JWT bundle; under `set -o pipefail` that non-zero status
# trips `set -e` and the script exits here intermittently with no error message.
JWT_RAW="$(spire-agent api fetch jwt -audience "${AUDIENCE}" -socketPath "${SOCK}" 2>&1)" || {
  echo "ERROR: spire-agent could not fetch a JWT-SVID:" >&2
  printf '%s\n' "${JWT_RAW}" | head -n 3 >&2
  exit 1
}
JWT="$(printf '%s\n' "${JWT_RAW}" | grep -A1 -m1 '^token(' | tail -n1 | tr -d '[:space:]')"
if [[ -z "${JWT}" ]]; then
  echo "ERROR: no JWT-SVID returned. Is this container registered (matching docker label)?" >&2
  exit 1
fi
SUB="$(b64url_decode "$(printf '%s' "$JWT" | cut -d. -f2)" | sed -n 's/.*"sub":"\([^"]*\)".*/\1/p')"
echo "   SVID acquired; sub=${SUB}"
echo

echo "== 2) Exchange the JWT-SVID for an Entra token (NO client secret; scope=${SCOPE}) =="
RESP="$(curl -sS -X POST "https://login.microsoftonline.com/${TENANT_ID}/oauth2/v2.0/token" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d 'grant_type=client_credentials' \
  -d "client_id=${CLIENT_ID}" \
  -d "scope=${SCOPE}" \
  -d 'client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer' \
  --data-urlencode "client_assertion=${JWT}")"

ACCESS_TOKEN="$(json_get "${RESP}" access_token)"
if [[ -z "${ACCESS_TOKEN}" ]]; then
  echo "   REJECTED by Entra:"
  echo "   $(json_get "${RESP}" error): $(json_get "${RESP}" error_description | cut -c1-160)"
  echo
  echo "== RESULT: this identity (${SUB}) is NOT authorized to federate. =="
  echo "   (Expected for the impostor: its SPIFFE ID does not match the Federated Credential subject.)"
  exit 1
fi
echo "   Got an Entra access token (no secret used). Claims:"
CLAIMS="$(b64url_decode "$(printf '%s' "$ACCESS_TOKEN" | cut -d. -f2)")"
echo "     aud   = $(printf '%s' "$CLAIMS" | sed -n 's/.*"aud":"\([^"]*\)".*/\1/p')"
echo "     appid = $(printf '%s' "$CLAIMS" | sed -n 's/.*"appid":"\([^"]*\)".*/\1/p')"
echo "     iss   = $(printf '%s' "$CLAIMS" | sed -n 's/.*"iss":"\([^"]*\)".*/\1/p')"
echo

if [[ -z "${KV_NAME}" ]]; then
  echo "== Done. (Set KEY_VAULT_NAME + SECRET_NAME to also read a Key Vault secret.) =="
  exit 0
fi

echo "== 3) Read Key Vault secret '${SECRET_NAME}' from ${KV_NAME} =="
KV_RESP="$(curl -sS "https://${KV_NAME}.vault.azure.net/secrets/${SECRET_NAME}?api-version=7.4" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}")"
SECRET_VALUE="$(json_get "${KV_RESP}" value)"
if [[ -n "${SECRET_VALUE}" ]]; then
  echo "   secret value = ${SECRET_VALUE}"
  echo
  echo "== SUCCESS: read a cloud secret using only a SPIFFE identity — zero secrets stored locally. =="
else
  echo "   $(printf '%s' "$KV_RESP" | head -c 300)"
  echo
  echo "== Token OK but Key Vault denied. Grant this App the 'Key Vault Secrets User' role. =="
  exit 1
fi
