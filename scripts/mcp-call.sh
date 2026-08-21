#!/usr/bin/env bash
# Call an MCP tool through the mTLS mesh, the way the AI agent would.
#
# The request travels: this script -> open-webui container -> localhost:10000
# (envoy-client sidecar) -> mTLS -> mcp-server:9000 (envoy-server) -> 127.0.0.1:8000
# (the MCP app). It performs the full MCP streamable-HTTP handshake for you:
#   initialize  ->  capture Mcp-Session-Id  ->  notifications/initialized  ->  tools/*
#
# Usage (run from the repo root, e.g. ~/identity):
#   scripts/mcp-call.sh                          # list the available tools
#   scripts/mcp-call.sh list_employees           # call a tool that takes no arguments
#   scripts/mcp-call.sh find_employees Platform  # shortcut: -> {"department":"Platform"}
#   scripts/mcp-call.sh find_employees '{"department":"Platform"}'   # explicit JSON arguments
set -euo pipefail

# Not inside the mesh yet? Hop into open-webui, which shares the envoy-client netns.
# We pipe this very file into the container's bash (no bind-mount needed) and re-run it
# there with MCP_INSIDE=1 set so the block below runs instead of hopping again.
if [ -z "${MCP_INSIDE:-}" ]; then
  exec docker compose exec -T -e MCP_INSIDE=1 open-webui bash -s -- "$@" < "$0"
fi

# ---- from here on we are inside the open-webui container, on the mesh ----
exec python3 - "$@" <<'PY'
import json, sys, urllib.request

URL = "http://localhost:10000/mcp"
BASE_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}

def rpc(method, params, sid=None, rid=None):
    body = {"jsonrpc": "2.0", "method": method, "params": params}
    if rid is not None:
        body["id"] = rid
    headers = dict(BASE_HEADERS)
    if sid:
        headers["Mcp-Session-Id"] = sid
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                 headers=headers, method="POST")
    resp = urllib.request.urlopen(req, timeout=15)
    out_sid = resp.headers.get("Mcp-Session-Id")
    payload = None
    for line in resp.read().decode().splitlines():
        line = line.strip()
        if line.startswith("data:"):            # server replies as SSE (event: message)
            payload = json.loads(line[5:].strip())
    return out_sid, payload

def die(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)

# 1) handshake: initialize (server hands back the session id in a response header)
sid, _ = rpc("initialize", {
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "clientInfo": {"name": "mcp-call.sh", "version": "1"},
}, rid=1)
# 2) required notification (no id, server answers 202 with an empty body)
try:
    rpc("notifications/initialized", {}, sid=sid)
except Exception:
    pass

# 3) the actual request
args = sys.argv[1:]
if not args:
    _, out = rpc("tools/list", {}, sid=sid, rid=2)
    if out.get("error"):
        die(out["error"])
    print("Available MCP tools:")
    for t in out.get("result", {}).get("tools", []):
        desc = (t.get("description") or "").strip().splitlines()
        print(f"  - {t['name']}: {desc[0] if desc else ''}")
    sys.exit(0)

tool = args[0]
arguments = {}
if len(args) >= 2:
    if args[1].lstrip().startswith("{"):
        arguments = json.loads(args[1])
    elif tool == "find_employees":
        arguments = {"department": args[1]}
    else:
        die(f"don't know how to map '{args[1]}' to arguments for {tool}; "
            f"pass explicit JSON, e.g. '{{\"key\":\"value\"}}'")

_, out = rpc("tools/call", {"name": tool, "arguments": arguments}, sid=sid, rid=2)
if out.get("error"):
    die(out["error"])
res = out.get("result", {})
if "structuredContent" in res:
    print(json.dumps(res["structuredContent"], indent=2, ensure_ascii=False))
elif res.get("content"):
    for c in res["content"]:
        print(c["text"] if c.get("type") == "text" else json.dumps(c, ensure_ascii=False))
else:
    print(json.dumps(out, indent=2, ensure_ascii=False))
PY
