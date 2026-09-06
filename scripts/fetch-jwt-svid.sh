#!/bin/bash
# Fetch a JWT-SVID from SPIRE and show its decoded claims.
#
# This is the portable, publicly-verifiable form of the workload identity. Whereas
# the X.509-SVID (see the "obtain SVID" step) is used for the mTLS data plane, the
# JWT-SVID is what the cloud-federation arc consumes: it is signed by SPIRE and
# verified via the JWKS published on the public internet (next step), then presented
# to Microsoft Entra as an OIDC client assertion (later step) -- with zero secrets.
#
# Env: UILANG=en|zh|ko selects the human-readable output language (default en).
set -euo pipefail

SOCK="/tmp/spire-sockets/api.sock"
AUDIENCE="api://AzureADTokenExchange"
UILANG="${UILANG:-en}"

# say "<english>" "<chinese>" "<korean>" — print the line for the selected language.
# The Korean arg is optional; lines that read the same in every language omit it.
say() {
  case "${UILANG}" in
    zh) printf '%s\n' "$2" ;;
    ko) printf '%s\n' "${3:-$1}" ;;
    *)  printf '%s\n' "$1" ;;
  esac
}

b64url_decode() {
  local s="$1" pad
  pad=$(( (4 - ${#s} % 4) % 4 ))
  printf '%s' "$s$(printf '=%.0s' $(seq 1 $pad 2>/dev/null))" | tr '_-' '/+' | base64 -d 2>/dev/null || true
}
claim() { printf '%s' "$1" | sed -n "s/.*\"$2\":\"\([^\"]*\)\".*/\1/p" | head -n1; }
claim_num() { printf '%s' "$1" | sed -n "s/.*\"$2\":\([0-9]*\).*/\1/p" | head -n1; }
# aud may be a JSON array (["api://..."]) or a bare string; tolerate both.
claim_aud() { printf '%s' "$1" | sed -n 's/.*"aud":\[*"\([^"]*\)".*/\1/p' | head -n1; }

say "== 1) Fetch a JWT-SVID from SPIRE (audience=${AUDIENCE}) ==" \
    "== 1) 从 SPIRE 获取 JWT-SVID (audience=${AUDIENCE}) ==" \
    "== 1) SPIRE에서 JWT-SVID 획득 (audience=${AUDIENCE}) =="
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

HEADER="$(b64url_decode "$(printf '%s' "$JWT" | cut -d. -f1)")"
PAYLOAD="$(b64url_decode "$(printf '%s' "$JWT" | cut -d. -f2)")"
ALG="$(claim "$HEADER" alg)"
KID="$(claim "$HEADER" kid)"
SUB="$(claim "$PAYLOAD" sub)"
ISS="$(claim "$PAYLOAD" iss)"
AUD="$(claim_aud "$PAYLOAD")"
EXP="$(claim_num "$PAYLOAD" exp)"
NOW="$(date +%s 2>/dev/null || echo 0)"
TTL="?"
if [[ -n "${EXP}" && "${NOW}" != "0" ]]; then TTL="$(( EXP - NOW ))"; fi

say "   JWT-SVID obtained (${#JWT} chars, compact JWS):" \
    "   已获取 JWT-SVID (${#JWT} 字符, 紧凑 JWS):" \
    "   JWT-SVID 획득됨 (${#JWT}자, 컴팩트 JWS):"
echo "     ${JWT:0:48}...${JWT: -16}"
if printf '%s\n' "${JWT_RAW}" | grep -q '^bundle('; then
  say "   + a trust bundle (the JWKS public keys) is returned alongside -- exactly what the next step publishes." \
      "   + 同时返回了信任捆绑包(即 JWKS 公钥)—— 正是下一步对外发布的内容。" \
      "   + 신뢰 번들(JWKS 공개 키)도 함께 반환됩니다 -- 바로 다음 단계에서 게시하는 내용입니다."
fi
echo

say "== 2) Decoded JWT-SVID claims (verifiable by anyone holding the public JWKS) ==" \
    "== 2) 解码后的 JWT-SVID 声明(任何持有公开 JWKS 者都可验证)==" \
    "== 2) 디코딩된 JWT-SVID 클레임(공개 JWKS를 가진 누구나 검증 가능)=="
say "     header.alg      : ${ALG}"                "     头部 alg        : ${ALG}"                "     header.alg      : ${ALG}"
say "     header.kid      : ${KID}"                "     头部 kid        : ${KID}"                "     header.kid      : ${KID}"
say "     sub (SPIFFE ID) : ${SUB}"                "     sub (SPIFFE ID) : ${SUB}"                "     sub (SPIFFE ID) : ${SUB}"
say "     aud (audience)  : ${AUD}"                "     aud (受众)       : ${AUD}"                "     aud (audience)  : ${AUD}"
say "     iss (issuer)    : ${ISS}"                "     iss (签发者)     : ${ISS}"                "     iss (issuer)    : ${ISS}"
say "     exp / TTL       : ${EXP}  (~${TTL}s, short-lived)" \
    "     exp / TTL       : ${EXP}  (~${TTL} 秒, 短期凭据)" \
    "     exp / TTL       : ${EXP}  (~${TTL}초, 단기)"
echo

say "== SUCCESS: issued a JWT-SVID for ${SUB}. ==" \
    "== SUCCESS: 已为 ${SUB} 签发 JWT-SVID。 ==" \
    "== SUCCESS: ${SUB}에 대한 JWT-SVID를 발급했습니다. =="
say "   Same identity as the X.509-SVID, in a portable token form: SPIRE-signed, publicly" \
    "   与 X.509-SVID 是同一身份,只是换成可移植的令牌形式:由 SPIRE 签名,可经公开" \
    "   X.509-SVID와 동일한 신원이며, 이식 가능한 토큰 형태입니다: SPIRE 서명, 공개"
say "   verifiable via the JWKS (next step), then exchanged for a cloud token at Entra (later)." \
    "   JWKS 验证(下一步),随后在 Entra 换取云令牌(后续步骤)。全程零密钥。" \
    "   JWKS로 검증 가능(다음 단계), 이후 Entra에서 클라우드 토큰으로 교환(이후 단계). 전 과정 제로 시크릿."
