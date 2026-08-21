#!/bin/bash
# End-to-end capstone: use a SPIRE JWT-SVID (ZERO client secret) to obtain a
# Microsoft Entra token for Microsoft Graph, then actually READ a cloud resource:
# GET https://graph.microsoft.com/v1.0/organization (the tenant's org object).
#
#   1. Fetch a JWT-SVID from SPIRE (audience api://AzureADTokenExchange).
#   2. Present it to Entra as an OIDC client assertion (grant_type=client_credentials),
#      requesting a token for Microsoft Graph (scope https://graph.microsoft.com/.default).
#   3. Use the returned token to GET /v1.0/organization and print the org info.
#
# Requires the App to hold the Graph application permission Organization.Read.All
# (admin-consented). Without it, Graph returns 403 and this script says so.
#
# Env (see .env): ENTRA_TENANT_ID, ENTRA_CLIENT_ID.
# UILANG=en|zh selects the human-readable output language (default en).
set -euo pipefail

TENANT_ID="${ENTRA_TENANT_ID:?set ENTRA_TENANT_ID in .env}"
CLIENT_ID="${ENTRA_CLIENT_ID:?set ENTRA_CLIENT_ID in .env}"
SOCK="/tmp/spire-sockets/api.sock"
AUDIENCE="api://AzureADTokenExchange"
SCOPE="https://graph.microsoft.com/.default"
UILANG="${UILANG:-en}"

# say "<english>" "<chinese>" — print the line for the selected language.
say() { if [ "${UILANG}" = "zh" ]; then printf '%s\n' "$2"; else printf '%s\n' "$1"; fi; }

b64url_decode() {
  local s="$1" pad
  pad=$(( (4 - ${#s} % 4) % 4 ))
  printf '%s' "$s$(printf '=%.0s' $(seq 1 $pad 2>/dev/null))" | tr '_-' '/+' | base64 -d 2>/dev/null || true
}
json_get() { printf '%s' "$1" | sed -n "s/.*\"$2\":\"\([^\"]*\)\".*/\1/p" | head -n1; }

say "== 1) Fetch a JWT-SVID from SPIRE (audience=${AUDIENCE}) ==" \
    "== 1) 从 SPIRE 获取 JWT-SVID (audience=${AUDIENCE}) =="
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
say "   SVID obtained; sub=${SUB}" \
    "   SVID 已获取; sub=${SUB}"
echo

say "== 2) Exchange the JWT-SVID for a Microsoft Graph token (no client secret; scope=${SCOPE}) ==" \
    "== 2) 用 JWT-SVID 换取 Microsoft Graph 令牌 (无客户端密钥; scope=${SCOPE}) =="
RESP="$(curl -sS -X POST "https://login.microsoftonline.com/${TENANT_ID}/oauth2/v2.0/token" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d 'grant_type=client_credentials' \
  -d "client_id=${CLIENT_ID}" \
  -d "scope=${SCOPE}" \
  -d 'client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer' \
  --data-urlencode "client_assertion=${JWT}")"

ACCESS_TOKEN="$(json_get "${RESP}" access_token)"
if [[ -z "${ACCESS_TOKEN}" ]]; then
  say "   REJECTED by Entra:" "   被 Entra 拒绝:"
  echo "   $(json_get "${RESP}" error): $(json_get "${RESP}" error_description | cut -c1-160)"
  exit 1
fi
CLAIMS="$(b64url_decode "$(printf '%s' "$ACCESS_TOKEN" | cut -d. -f2)")"
say "   Graph token obtained (zero secret). aud=$(printf '%s' "$CLAIMS" | sed -n 's/.*"aud":"\([^"]*\)".*/\1/p')" \
    "   已获取 Graph 令牌 (零密钥)。aud=$(printf '%s' "$CLAIMS" | sed -n 's/.*"aud":"\([^"]*\)".*/\1/p')"
echo

say "== 3) Call Microsoft Graph to read a cloud resource: GET /v1.0/organization ==" \
    "== 3) 调用 Microsoft Graph 读取云资源: GET /v1.0/organization =="
ORG_RESP="$(curl -sS -w '\n%{http_code}' https://graph.microsoft.com/v1.0/organization \
  -H "Authorization: Bearer ${ACCESS_TOKEN}")"
HTTP_CODE="$(printf '%s' "$ORG_RESP" | tail -n1)"
ORG_BODY="$(printf '%s' "$ORG_RESP" | sed '$d')"

if [[ "${HTTP_CODE}" != "200" ]]; then
  echo "   HTTP ${HTTP_CODE} -- $(json_get "${ORG_BODY}" code): $(json_get "${ORG_BODY}" message | cut -c1-140)"
  echo
  say "== Token is valid, but Graph denied access. Grant the app the Microsoft Graph permission ==" \
      "== 令牌有效,但 Graph 拒绝访问。请给该应用授予 Microsoft Graph 应用权限 =="
  say "   Organization.Read.All with admin consent, then retry this step in ~1 minute." \
      "   Organization.Read.All 并完成管理员同意,约 1 分钟后重试本步。"
  exit 1
fi

ORG_NAME="$(json_get "${ORG_BODY}" displayName)"
ORG_TID="$(json_get "${ORG_BODY}" id)"
ORG_DOMAIN="$(printf '%s' "${ORG_BODY}" | grep -o '"name":"[^"]*"' | head -n1 | sed 's/.*:"\([^"]*\)"/\1/')"
say "   HTTP 200 -- organization object read successfully:" \
    "   HTTP 200 —— 成功读取组织对象:"
say "     displayName : ${ORG_NAME}" \
    "     组织名称 displayName : ${ORG_NAME}"
say "     domain      : ${ORG_DOMAIN}" \
    "     默认域名 domain      : ${ORG_DOMAIN}"
say "     tenantId    : ${ORG_TID}" \
    "     租户 ID  tenantId    : ${ORG_TID}"
echo
say "== SUCCESS: used only a SPIFFE identity (zero local secret) to read a Microsoft Graph cloud resource. ==" \
    "== SUCCESS: 仅凭 SPIFFE 身份(零本地密钥)读取到 Microsoft Graph 云资源。 =="
