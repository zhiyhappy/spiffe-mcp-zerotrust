#!/usr/bin/env python3
"""Dedicated web demo for the SPIFFE/SPIRE zero-trust lab.

Serves a single-page UI (index.html) on http://0.0.0.0:8080 (override with
DEMO_PORT) and, on demand, runs the REAL backend demo commands on this host via
`docker compose exec ...`. Each "下一步" in the UI maps to exactly one entry in
STEPS below -- the browser only ever sends a step *id*, never a command, so there
is no arbitrary command execution.

  - No HTTPS on purpose (front it with an NSG source-IP allowlist, as planned).
  - stdlib only: no pip install needed on the VM. Just `python3 demo-web/server.py`.
  - cwd for every command is the repo root, so docker compose picks up ./.env.

WARNING: anyone who can reach this port can drive the whole demo (including
`entry delete`). Keep it behind the NSG allowlist; do not expose it publicly.
"""
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("DEMO_PORT", "8080"))
PUBLIC_DOMAIN = os.environ.get("PUBLIC_DOMAIN", "spiffe.ethandemo.com")

DC = "docker compose"  # v2 plugin, run from REPO_ROOT so ./.env is loaded

# Each step: id, title/desc (shown in the UI), the highlighted topology nodes/edges,
# the real command to run, and what the output should contain when it behaves as
# designed (the UI turns that into a ✓/⚠ verdict). `edge` is the highlight colour:
# "on" (blue, normal), "bad" (red, denied/revoked), "ok" (green, restored).
STEPS = [
    {
        "id": "overview",
        "title": "1 · 拓扑总览 / 组件状态",
        "desc": "确认 SPIRE 控制面(server+agent)、两侧工作负载与 Envoy sidecar、"
                "OIDC/Caddy 边缘,以及无身份的 hacker 容器均已就绪。",
        "cmd": f"{DC} ps",
        "nodes": ["spire-server", "spire-agent", "oidc", "caddy",
                  "app", "mcp", "entra", "hacker"],
        "edges": [],
        "edge": "on",
        "expect": {"contains": "Up ", "label": "容器已在运行"},
    },
    {
        "id": "registration",
        "title": "2 · 组件注册(注册即授权)",
        "desc": "在 SPIRE Server 为每个工作负载登记一条注册条目:docker 标签选择器 → SPIFFE ID"
                "(选择器匹配到哪个容器,就给它颁发对应身份)。"
                "这是整个系统唯一的授权来源:没有条目 = 没有身份。",
        "cmd": f"{DC} exec -T spire-server spire-server entry show",
        "nodes": ["spire-server", "spire-agent", "app", "mcp", "oidc"],
        "edges": ["e-srv-agent", "e-agent-app", "e-agent-mcp", "e-agent-oidc"],
        "edge": "on",
        "expect": {"contains": "spiffe://ethandemo.com/agent", "label": "已登记 /agent 等条目"},
    },
    {
        "id": "svid",
        "title": "3 · 工作负载获取 SVID",
        "desc": "两侧工作负载各自经 Workload API 向 SPIRE Agent 申请身份。Agent 通过各容器的 "
                "docker 标签分别认证:app=openwebui → /agent,app=mcp-server → /mcp-server —— "
                "各拿各的,全程零密钥,拿不到别人的证书。"
                "(记住第一条命令:第 10 步撤销注册后,同一条命令会被拒绝。)",
        "cmd": (
            "echo '=== Agent 侧 (federation-demo, app=openwebui) ==='; "
            + DC + " exec -T federation-demo "
            "spire-agent api fetch x509 -socketPath /tmp/spire-sockets/api.sock "
            "| grep -E 'Received|SPIFFE ID|SVID Valid'; "
            "echo; echo '=== MCP 侧 (federation-impostor, app=mcp-server) ==='; "
            + DC + " exec -T federation-impostor "
            "spire-agent api fetch x509 -socketPath /tmp/spire-sockets/api.sock "
            "| grep -E 'Received|SPIFFE ID|SVID Valid'"
        ),
        "nodes": ["spire-agent", "app", "mcp"],
        "edges": ["e-agent-app", "e-agent-mcp"],
        "edge": "on",
        "expect": {"contains": ["spiffe://ethandemo.com/agent",
                                "spiffe://ethandemo.com/mcp-server"],
                   "label": "两侧均已签发 X.509-SVID"},
    },
    {
        "id": "mtls",
        "title": "4 · 持有 SVID 后相互访问(mTLS)",
        "desc": "Open WebUI → envoy-client → mTLS → envoy-server → MCP。"
                "双方各自用 SVID 校验对端 SPIFFE ID,握手成功后返回真实员工数据。",
        "cmd": "bash scripts/mcp-call.sh list_employees",
        "nodes": ["app", "mcp"],
        "edges": ["e-app-mcp"],
        "edge": "on",
        "expect": {"contains": "Ada Lovelace", "label": "mTLS 成功,取回数据"},
    },
    {
        "id": "hacker",
        "title": "5 · 模拟 hacker 攻击(被拒)",
        "desc": "无 sidecar、无 SVID 的 hacker 容器尝试直连 mTLS 端口、绕过 sidecar 直击应用端口,"
                "并查找 SPIRE socket —— 全部失败。",
        "cmd": f"{DC} exec -T hacker sh /attack.sh",
        "nodes": ["hacker", "mcp"],
        "edges": ["e-hacker-mcp"],
        "edge": "bad",
        "expect": {"contains": "Attack blocked", "label": "攻击被拒"},
    },
    {
        "id": "jwks",
        "title": "6 · 身份公网可验证(JWKS)",
        "desc": "OIDC Discovery Provider 经 Caddy 在公网发布 SPIRE 的 JWKS 与 OIDC 元数据,"
                "使外部(如 Entra)可验证 SPIFFE 签发的 JWT-SVID。",
        "cmd": f"curl -sS https://{PUBLIC_DOMAIN}/.well-known/openid-configuration",
        "nodes": ["oidc", "caddy", "entra"],
        "edges": ["e-oidc-caddy", "e-caddy-entra"],
        "edge": "on",
        "expect": {"contains": "jwks_uri", "label": "公网可发现 JWKS"},
    },
    {
        "id": "fed-legit",
        "title": "7 · 零密钥云联邦(合法)",
        "desc": "Agent 用 JWT-SVID 作为 OIDC client assertion 向 Microsoft Entra 换取访问令牌 —— "
                "不使用任何客户端密钥。Entra 通过公网 JWKS 验证该断言。",
        "cmd": f"{DC} exec -T federation-demo /federate.sh",
        "nodes": ["app", "entra"],
        "edges": ["e-app-entra"],
        "edge": "on",
        "expect": {"contains": "Got an Entra access token", "label": "换取到 Entra 令牌(零密钥)"},
    },
    {
        "id": "fed-impostor",
        "title": "8 · 冒充身份联邦(被拒)",
        "desc": "持有合法但错误 SPIFFE ID(/mcp-server)的容器尝试联邦。"
                "Entra 因联邦凭据 subject 不匹配而拒绝(AADSTS700213)。",
        "cmd": f"{DC} exec -T federation-impostor /federate.sh",
        "nodes": ["mcp", "entra"],
        "edges": ["e-mcp-entra"],
        "edge": "bad",
        "expect": {"contains": "AADSTS700213", "label": "冒充身份被 Entra 拒绝"},
    },
    {
        "id": "revoke",
        "title": "9 · 撤销 SVID 注册",
        "desc": "在 SPIRE Server 删除 /agent 的注册条目。约一个同步周期(~5 秒)后,"
                "该工作负载将无法再获取或续期任何 SVID。"
                "注意:已签发的短期 SVID 会继续有效直到其 TTL 到期 —— 这是 SPIFFE 的短期凭据模型,"
                "撤销 = 停止签发/续期,而非即时吊销已有证书。",
        "cmd": (
            "ID=$(" + DC + " exec -T spire-server spire-server entry show "
            "-spiffeID spiffe://ethandemo.com/agent "
            "| awk -F': *' '/Entry ID/{print $2; exit}' | tr -d '[:space:]'); "
            'echo "deleting entry: $ID"; '
            + DC + ' exec -T spire-server spire-server entry delete -entryID "$ID"; '
            "echo 'waiting 8s for agent cache to sync...'; sleep 8"
        ),
        "nodes": ["spire-server", "app"],
        "edges": ["e-srv-agent"],
        "edge": "bad",
        "expect": {"contains": "Deleted", "label": "注册条目已删除"},
    },
    {
        "id": "revoke-verify",
        "title": "10 · 撤销后无法再获取身份",
        "desc": "重跑第 3 步那条获取 SVID 的命令。注册已被撤销,Agent 立即拒绝签发新身份"
                "(rpc PermissionDenied: no identity issued)。这就是撤销生效的直接证据:"
                "该工作负载再也拿不到新的 SVID。",
        "cmd": f"{DC} exec -T federation-demo "
               "spire-agent api fetch x509 -socketPath /tmp/spire-sockets/api.sock",
        "nodes": ["spire-agent", "app"],
        "edges": ["e-agent-app"],
        "edge": "bad",
        "expect": {"contains": "no identity issued", "label": "已被拒绝(无法获取身份)"},
    },
    {
        "id": "restore",
        "title": "11 · 恢复注册",
        "desc": "重新注册工作负载条目。约一个同步周期后,Agent 重新获得授权并可再次签发 SVID。",
        "cmd": (DC + " exec -T spire-server /opt/spire/scripts/register-entries.sh; "
                "echo 'waiting 8s for agent cache to sync...'; sleep 8"),
        "nodes": ["spire-server", "spire-agent", "app"],
        "edges": ["e-srv-agent", "e-agent-app"],
        "edge": "ok",
        "expect": {"contains": "spiffe://ethandemo.com/agent", "label": "条目已恢复"},
    },
    {
        "id": "restore-verify",
        "title": "12 · 访问恢复(端到端:读取云资源)",
        "desc": "注册恢复后,工作负载仅凭 SPIFFE 身份(零客户端密钥)向 Entra 换取 "
                "Microsoft Graph 访问令牌,并用该令牌真正读取一个 Azure 云资源 —— "
                "Graph 的组织对象(GET /v1.0/organization),返回租户名称/域名。"
                "身份 → 联邦 → 云资源,整条链路端到端打通。",
        "cmd": f"{DC} exec -T federation-demo /access-graph.sh",
        "nodes": ["app", "entra"],
        "edges": ["e-app-entra"],
        "edge": "ok",
        "expect": {"contains": "读取到 Microsoft Graph 云资源",
                   "label": "已用令牌读到 Graph 组织资源"},
    },
    {
        "id": "idira",
        "title": "13 · 平滑演进:CyberArk Idira 可接管的组件",
        "desc": "本 demo 用开源 SPIRE 自建了身份控制面。今后可平滑演进为 CyberArk Idira:"
                "紫色高亮的是 Idira 可接管的部分 —— SPIRE Server / Agent、OIDC Discovery "
                "三件套(完全接管),以及 OIDC→Caddy→Entra 这条 JWKS 发布/验证链"
                "(Idira 自带 issuer,吸收 Caddy 对外发布 JWKS 的那半)。"
                "而工作负载→Entra 的联邦请求本身、Envoy 数据面 mTLS、业务应用与云 IdP 保持不变。"
                "下方输出列出完整归属对照。",
        "cmd": (
            "cat <<'IDIRA'\n"
            "\n"
            "╔══════════════════════════════════════════════════════════╗\n"
            "║   CyberArk Idira 平滑演进 · 本 demo 各组件的未来归属      ║\n"
            "╚══════════════════════════════════════════════════════════╝\n"
            "\n"
            "🟣  可接管 —— 身份控制面 + 联邦经纪(左图紫色高亮)\n"
            "\n"
            "    • SPIRE Server\n"
            "        └ 身份权威;签发 X.509 / JWT-SVID;注册即授权(托管控制台)\n"
            "    • SPIRE Agent\n"
            "        └ 节点与工作负载认证;经 SDS 下发 SVID\n"
            "    • OIDC Discovery Provider\n"
            "        └ 内置 OIDC issuer,对外发布 JWKS\n"
            "    • 零密钥云联邦(JWKS 发布/验证链)\n"
            "        └ Idira issuer 吸收「Caddy 对外发布 JWKS」的那半\n"
            "\n"
            "────────────────────────────────────────────────────────────\n"
            "\n"
            "🟡  部分接管\n"
            "\n"
            "    • Caddy\n"
            "        └ 发布 JWKS 的职责并入 Idira issuer\n"
            "        └ SSO 反向代理 / 公网 TLS 边缘仍由 Caddy 承担\n"
            "\n"
            "────────────────────────────────────────────────────────────\n"
            "\n"
            "⚪  保持不变 —— Idira 不替代\n"
            "\n"
            "    • envoy-client / envoy-server\n"
            "        └ 数据面 mTLS 执行仍是 Envoy/网格;Idira 作为其 SDS/SVID 来源\n"
            "    • Open WebUI / MCP Server\n"
            "        └ 业务应用本身\n"
            "    • Microsoft Entra ID\n"
            "        └ 云 IdP;作为被联邦的对端\n"
            "\n"
            "╔══════════════════════════════════════════════════════════╗\n"
            "║   演进动作(最小改造)                                     ║\n"
            "╚══════════════════════════════════════════════════════════╝\n"
            "\n"
            "    1. 拔掉 SPIRE server / agent / oidc 三件套 → 换成 CyberArk Idira\n"
            "    2. 两个 Envoy 的 SDS 源改指向 Idira 下发的 socket\n"
            "    3. 联邦目标从「自建 OIDC → Entra」扩展到 Conjur / Secrets Hub / 多云 IAM\n"
            "\n"
            "    ✔ 业务应用与 mTLS 数据面几乎不动 —— 可平滑演进到 Idira 架构\n"
            "\n"
            "IDIRA"
        ),
        "nodes": ["spire-server", "spire-agent", "oidc"],
        "edges": ["e-srv-agent", "e-agent-oidc", "e-oidc-caddy", "e-caddy-entra"],
        "edge": "evolve",
        "expect": {"contains": ["CyberArk Idira", "可平滑演进到 Idira 架构"],
                   "label": "已标注可演进到 Idira 的组件"},
    },
]

STEP_BY_ID = {s["id"]: s for s in STEPS}


def public_steps():
    """Step metadata for the browser -- everything except the raw command is fine
    to expose, but we deliberately include `cmd` too so the demo can *show* the
    exact command being run (transparency is the point of the demo)."""
    return [
        {k: s[k] for k in ("id", "title", "desc", "cmd", "nodes", "edges", "edge", "expect")}
        for s in STEPS
    ]


def run_step(step_id):
    step = STEP_BY_ID.get(step_id)
    if step is None:
        return {"ok": False, "cmd": "", "output": f"unknown step: {step_id}", "code": -1}
    cmd = step["cmd"]
    try:
        proc = subprocess.run(
            ["bash", "-lc", cmd],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=120,
        )
        output = proc.stdout.decode("utf-8", "replace")
        return {"ok": True, "cmd": cmd, "output": output, "code": proc.returncode}
    except subprocess.TimeoutExpired as e:
        partial = (e.output or b"").decode("utf-8", "replace")
        return {"ok": False, "cmd": cmd,
                "output": partial + "\n[timed out after 120s]", "code": -1}
    except Exception as e:  # noqa: BLE001 - surface anything to the UI
        return {"ok": False, "cmd": cmd, "output": f"error running command: {e}", "code": -1}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            try:
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(500, b"index.html not found next to server.py", "text/plain")
            return
        if path == "/api/steps":
            self._send(200, json.dumps({"steps": public_steps(),
                                        "domain": PUBLIC_DOMAIN}))
            return
        self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):  # noqa: N802
        if self.path.split("?", 1)[0] != "/api/run":
            self._send(404, json.dumps({"error": "not found"}))
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            step_id = json.loads(raw or b"{}").get("step", "")
        except json.JSONDecodeError:
            self._send(400, json.dumps({"error": "bad json"}))
            return
        self._send(200, json.dumps(run_step(step_id)))

    def log_message(self, fmt, *args):  # quieter, one line per request
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"SPIFFE demo web UI  ->  http://0.0.0.0:{PORT}  (repo: {REPO_ROOT})")
    print("Restrict access with your NSG source-IP allowlist. Ctrl-C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
        srv.shutdown()


if __name__ == "__main__":
    main()
