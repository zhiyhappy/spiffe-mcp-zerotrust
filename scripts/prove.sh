#!/usr/bin/env bash
# Two quick proofs that reinforce the SPIFFE federation story. Run from repo root.
set -uo pipefail
SOCK=/tmp/spire-sockets/api.sock
AUD=api://AzureADTokenExchange

echo "############################################################"
echo "# Proof 1: the federation path carries NO secret"
echo "############################################################"
echo "- env of federation-demo (grep for any secret):"
docker compose exec -T federation-demo sh -c 'env | grep -iE "secret|password" || echo "  (none)"'
echo "- federate.sh sends client_assertion, not client_secret:"
grep -nE "client_assertion|client_secret" scripts/federate.sh | sed 's/^/  /'
echo "  -> a leaked container yields no reusable long-lived credential."

echo
echo "############################################################"
echo "# Proof 2: JWT-SVIDs are short-lived and minted fresh each fetch"
echo "############################################################"
decode() { # $1 = jwt -> print iat/exp/jti of payload
  docker compose exec -T federation-demo sh -c '
    j="'"$1"'"; p=$(printf "%s" "$j" | cut -d. -f2)
    pad=$(( (4 - ${#p} % 4) % 4 )); i=0; while [ $i -lt $pad ]; do p="${p}="; i=$((i+1)); done
    printf "%s" "$p" | tr "_-" "/+" | base64 -d 2>/dev/null'
}
fetch() {
  docker compose exec -T federation-demo sh -c \
    "spire-agent api fetch jwt -audience ${AUD} -socketPath ${SOCK} | grep -A1 -m1 '^token(' | tail -n1 | tr -d '[:space:]'"
}
J1=$(fetch); sleep 2; J2=$(fetch)
echo "- SVID #1 claims: $(decode "$J1" | sed -n 's/.*\("iat":[0-9]*\).*\("exp":[0-9]*\).*\("jti":"[^"]*"\).*/\1 \2 \3/p')"
echo "- SVID #2 claims: $(decode "$J2" | sed -n 's/.*\("iat":[0-9]*\).*\("exp":[0-9]*\).*\("jti":"[^"]*"\).*/\1 \2 \3/p')"
echo "  -> different jti / exp: each call yields a fresh, minutes-long credential"
echo "     (the mTLS X.509-SVIDs likewise auto-rotate at ~half TTL, streamed to Envoy via SDS)."
