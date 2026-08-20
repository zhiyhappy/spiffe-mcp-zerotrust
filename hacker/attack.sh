#!/bin/sh
# Runs inside the "hacker" container, which has NO Envoy sidecar and NO SPIRE
# registration entry -> it cannot obtain an X.509-SVID, so it cannot complete
# mTLS with the MCP server's Envoy. This script demonstrates the rejection.
#
# Topology note (true sidecars): the MCP mTLS listener is reachable as
# mcp-server:9000; the MCP app itself binds 127.0.0.1:8000 inside that netns and
# is NOT exposed to the network; and the client sidecar (:10000) lives inside
# Open WebUI's netns and has no network address at all.
set -u

echo "== 1) Try the mTLS-only MCP port (:9000) without a client cert =="
curl -sS -k -m 5 https://mcp-server:9000/mcp \
  && echo "  !! unexpectedly succeeded" \
  || echo "  -> rejected (as expected: mTLS requires a valid SPIFFE client cert)"
echo

echo "== 2) Try to bypass the sidecar and hit the MCP app port (:8000) directly =="
curl -sS -m 5 http://mcp-server:8000/mcp \
  && echo "  !! unexpectedly succeeded" \
  || echo "  -> unreachable (as expected: MCP binds 127.0.0.1 behind the sidecar)"
echo

echo "== 3) Confirm no SPIRE socket is available to this container =="
if [ -S /tmp/spire-sockets/api.sock ]; then
  echo "  !! socket unexpectedly present"
else
  echo "  -> no /tmp/spire-sockets/api.sock: cannot fetch an SVID. Attack blocked."
fi
