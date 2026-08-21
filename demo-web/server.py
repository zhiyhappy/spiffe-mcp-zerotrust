#!/usr/bin/env python3
"""Dedicated web demo for the SPIFFE/SPIRE zero-trust lab.

Serves a single-page UI (index.html) on http://0.0.0.0:8080 (override with
DEMO_PORT) and, on demand, runs the REAL backend demo commands on this host via
`docker compose exec ...`. Each "Next" in the UI maps to exactly one entry in
STEPS below -- the browser only ever sends a step *id* (plus a language), never a
command, so there is no arbitrary command execution.

  - No HTTPS on purpose (front it with an NSG source-IP allowlist, as planned).
  - stdlib only: no pip install needed on the VM. Just `python3 demo-web/server.py`.
  - cwd for every command is the repo root, so docker compose picks up ./.env.

Internationalisation: user-facing strings (title/desc/expect + a few commands
whose output text is localised) are stored as {"en": ..., "zh": ...} dicts and
resolved per request via `_pick`. The default language is English.

WARNING: anyone who can reach this port can drive the whole demo (including
`entry delete`). Keep it behind the NSG allowlist; do not expose it publicly.
"""
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("DEMO_PORT", "8080"))
PUBLIC_DOMAIN = os.environ.get("PUBLIC_DOMAIN", "spiffe.ethandemo.com")

DC = "docker compose"  # v2 plugin, run from REPO_ROOT so ./.env is loaded

LANGS = ("en", "zh")
DEFAULT_LANG = "en"


def L(en, zh):
    """A translatable string: English + Chinese."""
    return {"en": en, "zh": zh}


def _pick(v, lang):
    """Resolve a possibly-translatable value for `lang`, defaulting to English.
    Plain strings/lists (same in every language) are returned unchanged."""
    if isinstance(v, dict) and "en" in v:
        return v.get(lang) or v["en"]
    return v


# ------------------------------------------------------------------ step 13 output
# The Idira "future ownership" mapping is pure informational content (not tool
# output), so it is fully localised. Horizontal rule lines avoid the CJK
# double-width box-misalignment problem.
_RULE = "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
_SEP = "───────────────────────────────────────────────────────"

IDIRA_ZH = (
    "cat <<'IDIRA'\n"
    "\n"
    f"{_RULE}\n"
    "  CyberArk Idira 平滑演进 · 本 demo 各组件的未来归属\n"
    f"{_RULE}\n"
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
    f"{_SEP}\n"
    "\n"
    "🟡  部分接管\n"
    "\n"
    "    • Caddy\n"
    "        └ 发布 JWKS 的职责并入 Idira issuer\n"
    "        └ SSO 反向代理 / 公网 TLS 边缘仍由 Caddy 承担\n"
    "\n"
    f"{_SEP}\n"
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
    f"{_RULE}\n"
    "  演进动作(最小改造)\n"
    f"{_RULE}\n"
    "\n"
    "    1. 移除 SPIRE server / agent / oidc 三件套 → 换成 CyberArk Idira\n"
    "    2. 两个 Envoy 的 SDS 源改指向 Idira 下发的 socket\n"
    "    3. 联邦目标从「自建 OIDC → Entra」扩展到 Conjur / Secrets Hub / 多云 IAM\n"
    "\n"
    "    ✔ 业务应用与 mTLS 数据面几乎不动 —— 可平滑演进到 Idira 架构\n"
    "\n"
    "IDIRA"
)

IDIRA_EN = (
    "cat <<'IDIRA'\n"
    "\n"
    f"{_RULE}\n"
    "  CyberArk Idira · Future ownership of each demo component\n"
    f"{_RULE}\n"
    "\n"
    "🟣  Can take over -- identity control plane + federation broker (purple in the diagram)\n"
    "\n"
    "    • SPIRE Server\n"
    "        └ Identity authority; issues X.509 / JWT-SVIDs; registration = authorization (managed console)\n"
    "    • SPIRE Agent\n"
    "        └ Node & workload attestation; delivers SVIDs via SDS\n"
    "    • OIDC Discovery Provider\n"
    "        └ Built-in OIDC issuer; publishes JWKS publicly\n"
    "    • Zero-secret cloud federation (JWKS publish/verify chain)\n"
    "        └ The Idira issuer absorbs the \"Caddy publishes JWKS\" half\n"
    "\n"
    f"{_SEP}\n"
    "\n"
    "🟡  Partial takeover\n"
    "\n"
    "    • Caddy\n"
    "        └ The JWKS-publishing duty folds into the Idira issuer\n"
    "        └ SSO reverse proxy / public TLS edge is still handled by Caddy\n"
    "\n"
    f"{_SEP}\n"
    "\n"
    "⚪  Unchanged -- Idira does not replace\n"
    "\n"
    "    • envoy-client / envoy-server\n"
    "        └ Data-plane mTLS enforcement stays Envoy/mesh; Idira is its SDS/SVID source\n"
    "    • Open WebUI / MCP Server\n"
    "        └ The business apps themselves\n"
    "    • Microsoft Entra ID\n"
    "        └ Cloud IdP; the federation counterparty\n"
    "\n"
    f"{_RULE}\n"
    "  Evolution steps (minimal change)\n"
    f"{_RULE}\n"
    "\n"
    "    1. Remove the SPIRE server / agent / oidc trio → replace with CyberArk Idira\n"
    "    2. Point both Envoys' SDS source at the socket Idira serves\n"
    "    3. Extend the federation target from \"self-hosted OIDC → Entra\" to Conjur / Secrets Hub / multi-cloud IAM\n"
    "\n"
    "    ✔ Business apps and the mTLS data plane barely change -- can smoothly evolve to the Idira architecture\n"
    "\n"
    "IDIRA"
)

# Each step: id, title/desc (shown in the UI), the highlighted topology nodes/edges,
# the real command to run, and what the output should contain when it behaves as
# designed (the UI turns that into a check/warn verdict). `edge` is the highlight
# colour: "on" (blue, normal), "bad" (red, denied/revoked), "ok" (green, restored),
# "evolve" (violet, future Idira takeover).
STEPS = [
    {
        "id": "overview",
        "title": L("1 · Topology Overview / Component Status",
                   "1 · 拓扑总览 / 组件状态"),
        "desc": L("Confirm the SPIRE control plane (server + agent), both workload "
                  "sides with their Envoy sidecars, the OIDC/Caddy edge, and the "
                  "identity-less hacker container are all up.",
                  "确认 SPIRE 控制面(server+agent)、两侧工作负载与 Envoy sidecar、"
                  "OIDC/Caddy 边缘,以及无身份的 hacker 容器均已就绪。"),
        "cmd": f"{DC} ps",
        "nodes": ["spire-server", "spire-agent", "oidc", "caddy",
                  "app", "mcp", "entra", "hacker"],
        "edges": [],
        "edge": "on",
        "expect": {"contains": "Up ", "label": L("Containers are running", "容器已在运行")},
    },
    {
        "id": "registration",
        "title": L("2 · Component Registration (registration = authorization)",
                   "2 · 组件注册(注册即授权)"),
        "desc": L("Register one entry per workload on the SPIRE Server: a docker-label "
                  "selector → SPIFFE ID (whichever container the selector matches gets "
                  "that identity). This is the system's only source of authorization: "
                  "no entry = no identity.",
                  "在 SPIRE Server 为每个工作负载登记一条注册条目:docker 标签选择器 → SPIFFE ID"
                  "(选择器匹配到哪个容器,就给它颁发对应身份)。"
                  "这是整个系统唯一的授权来源:没有条目 = 没有身份。"),
        "cmd": f"{DC} exec -T spire-server spire-server entry show",
        "nodes": ["spire-server", "spire-agent", "app", "mcp", "oidc"],
        "edges": ["e-srv-agent", "e-agent-app", "e-agent-mcp", "e-agent-oidc"],
        "edge": "on",
        "expect": {"contains": "spiffe://ethandemo.com/agent",
                   "label": L("/agent and other entries registered", "已登记 /agent 等条目")},
    },
    {
        "id": "svid",
        "title": L("3 · Workloads Obtain Their SVIDs", "3 · 工作负载获取 SVID"),
        "desc": L("Each side asks the SPIRE Agent for identity over the Workload API. "
                  "The Agent authenticates each container by its docker label: "
                  "app=openwebui → /agent, app=mcp-server → /mcp-server -- each gets "
                  "only its own, zero secrets, and never the other's certificate. "
                  "(Remember the first command: after revocation in step 11, the same "
                  "command is denied.)",
                  "两侧工作负载各自经 Workload API 向 SPIRE Agent 申请身份。Agent 通过各容器的 "
                  "docker 标签分别认证:app=openwebui → /agent,app=mcp-server → /mcp-server —— "
                  "各拿各的,全程零密钥,拿不到别人的证书。"
                  "(记住第一条命令:第 11 步撤销注册后,同一条命令会被拒绝。)"),
        "cmd": L(
            "echo '=== Agent side (federation-demo, app=openwebui) ==='; "
            + DC + " exec -T federation-demo "
            "spire-agent api fetch x509 -socketPath /tmp/spire-sockets/api.sock "
            "| grep -E 'Received|SPIFFE ID|SVID Valid'; "
            "echo; echo '=== MCP side (federation-impostor, app=mcp-server) ==='; "
            + DC + " exec -T federation-impostor "
            "spire-agent api fetch x509 -socketPath /tmp/spire-sockets/api.sock "
            "| grep -E 'Received|SPIFFE ID|SVID Valid'",
            "echo '=== Agent 侧 (federation-demo, app=openwebui) ==='; "
            + DC + " exec -T federation-demo "
            "spire-agent api fetch x509 -socketPath /tmp/spire-sockets/api.sock "
            "| grep -E 'Received|SPIFFE ID|SVID Valid'; "
            "echo; echo '=== MCP 侧 (federation-impostor, app=mcp-server) ==='; "
            + DC + " exec -T federation-impostor "
            "spire-agent api fetch x509 -socketPath /tmp/spire-sockets/api.sock "
            "| grep -E 'Received|SPIFFE ID|SVID Valid'",
        ),
        "nodes": ["spire-agent", "app", "mcp"],
        "edges": ["e-agent-app", "e-agent-mcp"],
        "edge": "on",
        "expect": {"contains": ["spiffe://ethandemo.com/agent",
                                "spiffe://ethandemo.com/mcp-server"],
                   "label": L("Both sides issued an X.509-SVID", "两侧均已签发 X.509-SVID")},
    },
    {
        "id": "mtls",
        "title": L("4 · Mutual Access with SVIDs (mTLS)", "4 · 持有 SVID 后相互访问(mTLS)"),
        "desc": L("Open WebUI → envoy-client → mTLS → envoy-server → MCP. Each side "
                  "validates the peer's SPIFFE ID with its SVID; after a successful "
                  "handshake real employee data is returned.",
                  "Open WebUI → envoy-client → mTLS → envoy-server → MCP。"
                  "双方各自用 SVID 校验对端 SPIFFE ID,握手成功后返回真实员工数据。"),
        "cmd": "bash scripts/mcp-call.sh list_employees",
        "nodes": ["app", "mcp"],
        "edges": ["e-app-mcp"],
        "edge": "on",
        "expect": {"contains": "Ada Lovelace",
                   "label": L("mTLS succeeded, data retrieved", "mTLS 成功,取回数据")},
    },
    {
        "id": "hacker",
        "title": L("5 · Simulated Hacker Attack (denied)", "5 · 模拟 hacker 攻击(被拒)"),
        "desc": L("A hacker container with no sidecar and no SVID tries to connect "
                  "straight to the mTLS port, bypass the sidecar to hit the app port "
                  "directly, and hunt for the SPIRE socket -- all fail.",
                  "无 sidecar、无 SVID 的 hacker 容器尝试直连 mTLS 端口、绕过 sidecar 直击应用端口,"
                  "并查找 SPIRE socket —— 全部失败。"),
        "cmd": f"{DC} exec -T hacker sh /attack.sh",
        "nodes": ["hacker", "mcp"],
        "edges": ["e-hacker-mcp"],
        "edge": "bad",
        "expect": {"contains": "Attack blocked", "label": L("Attack denied", "攻击被拒")},
    },
    {
        "id": "svid-jwt",
        "title": L("6 · Workload Obtains a JWT-SVID", "6 · 工作负载获取 JWT-SVID"),
        "desc": L("The same registration also yields a JWT-SVID -- a portable, "
                  "publicly-verifiable token form of the identity (the X.509-SVID above "
                  "drives the mTLS data plane; this JWT-SVID drives cloud federation). "
                  "The workload fetches it from the SPIRE Agent over the Workload API "
                  "and we decode its claims: sub = the SPIFFE ID, aud = the federation "
                  "audience, signed by SPIRE and short-lived. The next step publishes "
                  "the JWKS that lets anyone verify it.",
                  "同一条注册也能签发 JWT-SVID —— 身份的可移植、可公开验证的令牌形式"
                  "(上面的 X.509-SVID 驱动 mTLS 数据面;这条 JWT-SVID 驱动云联邦)。"
                  "工作负载经 Workload API 向 SPIRE Agent 获取它,并解码其声明:"
                  "sub = SPIFFE ID,aud = 联邦受众,由 SPIRE 签名且为短期凭据。"
                  "下一步将发布可供任何人验证它的 JWKS。"),
        "cmd": L(f"{DC} exec -T -e UILANG=en federation-demo /fetch-jwt-svid.sh",
                 f"{DC} exec -T -e UILANG=zh federation-demo /fetch-jwt-svid.sh"),
        "nodes": ["spire-agent", "app"],
        "edges": ["e-agent-app"],
        "edge": "on",
        "expect": {"contains": "spiffe://ethandemo.com/agent",
                   "label": L("JWT-SVID issued (sub = /agent)", "已签发 JWT-SVID(sub = /agent)")},
    },
    {
        "id": "jwks",
        "title": L("7 · Identity Publicly Verifiable (JWKS)", "7 · 身份公网可验证(JWKS)"),
        "desc": L("The OIDC Discovery Provider publishes SPIRE's JWKS and OIDC metadata "
                  "on the public internet via Caddy, so outsiders (e.g. Entra) can "
                  "verify JWT-SVIDs issued by SPIFFE.",
                  "OIDC Discovery Provider 经 Caddy 在公网发布 SPIRE 的 JWKS 与 OIDC 元数据,"
                  "使外部(如 Entra)可验证 SPIFFE 签发的 JWT-SVID。"),
        "cmd": f"curl -sS https://{PUBLIC_DOMAIN}/.well-known/openid-configuration",
        "nodes": ["oidc", "caddy", "entra"],
        "edges": ["e-oidc-caddy", "e-caddy-entra"],
        "edge": "on",
        "expect": {"contains": "jwks_uri",
                   "label": L("JWKS discoverable on the public internet", "公网可发现 JWKS")},
    },
    {
        "id": "fed-legit",
        "title": L("8 · Zero-Secret Cloud Federation (legit: read a cloud resource)",
                   "8 · 零密钥云联邦(合法:读取云资源)"),
        "desc": L("The Agent presents its JWT-SVID as an OIDC client assertion to "
                  "Microsoft Entra to obtain an access token -- using no client secret; "
                  "Entra verifies the assertion via the public JWKS. It then uses that "
                  "token to actually read an Azure cloud resource: the Microsoft Graph "
                  "organization object (GET /v1.0/organization), returning the tenant "
                  "name/domain.",
                  "Agent 用 JWT-SVID 作为 OIDC client assertion 向 Microsoft Entra 换取访问令牌 —— "
                  "不使用任何客户端密钥;Entra 通过公网 JWKS 验证该断言。随后用该令牌真正读取一个 "
                  "Azure 云资源:Microsoft Graph 的组织对象(GET /v1.0/organization),返回租户名称/域名。"),
        "cmd": L(f"{DC} exec -T -e UILANG=en federation-demo /access-graph.sh",
                 f"{DC} exec -T -e UILANG=zh federation-demo /access-graph.sh"),
        "nodes": ["app", "entra"],
        "edges": ["e-app-entra"],
        "edge": "on",
        "expect": {"contains": L("read a Microsoft Graph cloud resource",
                                 "读取到 Microsoft Graph 云资源"),
                   "label": L("Exchanged a token and read the Graph org resource (zero secret)",
                              "换取令牌并读到 Graph 组织资源(零密钥)")},
    },
    {
        "id": "fed-impostor",
        "title": L("9 · Impersonated Federation (denied)", "9 · 冒充身份联邦(被拒)"),
        "desc": L("A container holding a valid but wrong SPIFFE ID (/mcp-server) "
                  "attempts federation. Entra rejects it because the federated-credential "
                  "subject doesn't match (AADSTS700213).",
                  "持有合法但错误 SPIFFE ID(/mcp-server)的容器尝试联邦。"
                  "Entra 因联邦凭据 subject 不匹配而拒绝(AADSTS700213)。"),
        "cmd": f"{DC} exec -T federation-impostor /federate.sh",
        "nodes": ["mcp", "entra"],
        "edges": ["e-mcp-entra"],
        "edge": "bad",
        "expect": {"contains": "AADSTS700213",
                   "label": L("Impersonation rejected by Entra", "冒充身份被 Entra 拒绝")},
    },
    {
        "id": "revoke",
        "title": L("10 · Revoke the SVID Registration", "10 · 撤销 SVID 注册"),
        "desc": L("Delete the /agent registration entry on the SPIRE Server. After "
                  "about one sync period (~5s) that workload can no longer obtain or "
                  "renew any SVID. Note: already-issued short-lived SVIDs stay valid "
                  "until their TTL expires -- this is SPIFFE's short-lived-credential "
                  "model: revocation = stop issuing/renewing, not instant revocation of "
                  "existing certificates.",
                  "在 SPIRE Server 删除 /agent 的注册条目。约一个同步周期(~5 秒)后,"
                  "该工作负载将无法再获取或续期任何 SVID。"
                  "注意:已签发的短期 SVID 会继续有效直到其 TTL 到期 —— 这是 SPIFFE 的短期凭据模型,"
                  "撤销 = 停止签发/续期,而非即时吊销已有证书。"),
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
        "expect": {"contains": "Deleted", "label": L("Registration entry deleted", "注册条目已删除")},
    },
    {
        "id": "revoke-verify",
        "title": L("11 · After Revocation, No New Identity", "11 · 撤销后无法再获取身份"),
        "desc": L("Re-run the SVID-fetch command from step 3. The registration is gone, "
                  "so the Agent immediately refuses to issue a new identity (rpc "
                  "PermissionDenied: no identity issued). That's the direct proof "
                  "revocation took effect: this workload can never get a new SVID.",
                  "重跑第 3 步那条获取 SVID 的命令。注册已被撤销,Agent 立即拒绝签发新身份"
                  "(rpc PermissionDenied: no identity issued)。这就是撤销生效的直接证据:"
                  "该工作负载再也拿不到新的 SVID。"),
        "cmd": f"{DC} exec -T federation-demo "
               "spire-agent api fetch x509 -socketPath /tmp/spire-sockets/api.sock",
        "nodes": ["spire-agent", "app"],
        "edges": ["e-agent-app"],
        "edge": "bad",
        "expect": {"contains": "no identity issued",
                   "label": L("Denied (cannot obtain identity)", "已被拒绝(无法获取身份)")},
    },
    {
        "id": "restore",
        "title": L("12 · Restore the Registration", "12 · 恢复注册"),
        "desc": L("Re-register the workload entry. After about one sync period the "
                  "Agent is re-authorized and can issue SVIDs again.",
                  "重新注册工作负载条目。约一个同步周期后,Agent 重新获得授权并可再次签发 SVID。"),
        "cmd": (DC + " exec -T spire-server /opt/spire/scripts/register-entries.sh; "
                "echo 'waiting 8s for agent cache to sync...'; sleep 8"),
        "nodes": ["spire-server", "spire-agent", "app"],
        "edges": ["e-srv-agent", "e-agent-app"],
        "edge": "ok",
        "expect": {"contains": "spiffe://ethandemo.com/agent",
                   "label": L("Entry restored", "条目已恢复")},
    },
    {
        "id": "restore-verify",
        "title": L("13 · Access Restored (end-to-end: read a cloud resource)",
                   "13 · 访问恢复(端到端:读取云资源)"),
        "desc": L("With registration restored, the workload -- using only its SPIFFE "
                  "identity (zero client secret) -- exchanges for a Microsoft Graph "
                  "access token at Entra and uses it to actually read an Azure cloud "
                  "resource: the Graph organization object (GET /v1.0/organization), "
                  "returning the tenant name/domain. Identity → federation → cloud "
                  "resource, the whole chain working end to end.",
                  "注册恢复后,工作负载仅凭 SPIFFE 身份(零客户端密钥)向 Entra 换取 "
                  "Microsoft Graph 访问令牌,并用该令牌真正读取一个 Azure 云资源 —— "
                  "Graph 的组织对象(GET /v1.0/organization),返回租户名称/域名。"
                  "身份 → 联邦 → 云资源,整条链路端到端打通。"),
        "cmd": L(f"{DC} exec -T -e UILANG=en federation-demo /access-graph.sh",
                 f"{DC} exec -T -e UILANG=zh federation-demo /access-graph.sh"),
        "nodes": ["app", "entra"],
        "edges": ["e-app-entra"],
        "edge": "ok",
        "expect": {"contains": L("read a Microsoft Graph cloud resource",
                                 "读取到 Microsoft Graph 云资源"),
                   "label": L("Read the Graph org resource with the token",
                              "已用令牌读到 Graph 组织资源")},
    },
    {
        "id": "idira",
        "title": L("14 · Smooth Evolution: Components CyberArk Idira Can Take Over",
                   "14 · 平滑演进:CyberArk Idira 可接管的组件"),
        "desc": L("This demo built an identity control plane with open-source SPIRE. It "
                  "can smoothly evolve into CyberArk Idira: highlighted in purple is "
                  "what Idira can take over -- the SPIRE Server / Agent and OIDC "
                  "Discovery trio (full takeover), plus the OIDC→Caddy→Entra JWKS "
                  "publish/verify chain (Idira ships its own issuer, absorbing the half "
                  "where Caddy publishes JWKS). The workload→Entra federation request "
                  "itself, the Envoy data-plane mTLS, the business apps and the cloud "
                  "IdP stay unchanged. The output below lists the full mapping.",
                  "本 demo 用开源 SPIRE 自建了身份控制面。今后可平滑演进为 CyberArk Idira:"
                  "紫色高亮的是 Idira 可接管的部分 —— SPIRE Server / Agent、OIDC Discovery "
                  "三件套(完全接管),以及 OIDC→Caddy→Entra 这条 JWKS 发布/验证链"
                  "(Idira 自带 issuer,吸收 Caddy 对外发布 JWKS 的那半)。"
                  "而工作负载→Entra 的联邦请求本身、Envoy 数据面 mTLS、业务应用与云 IdP 保持不变。"
                  "下方输出列出完整归属对照。"),
        "cmd": L(IDIRA_EN, IDIRA_ZH),
        "nodes": ["spire-server", "spire-agent", "oidc"],
        "edges": ["e-srv-agent", "e-agent-oidc", "e-oidc-caddy", "e-caddy-entra"],
        "edge": "evolve",
        "expect": {"contains": L(["CyberArk Idira", "can smoothly evolve to the Idira architecture"],
                                 ["CyberArk Idira", "可平滑演进到 Idira 架构"]),
                   "label": L("Marked the components that can evolve to Idira",
                              "已标注可演进到 Idira 的组件")},
    },
]

STEP_BY_ID = {s["id"]: s for s in STEPS}


def _norm_lang(lang):
    return lang if lang in LANGS else DEFAULT_LANG


def public_steps(lang):
    """Step metadata for the browser, resolved for `lang`. We deliberately include
    the resolved `cmd` too so the demo can *show* the exact command being run
    (transparency is the point of the demo)."""
    out = []
    for s in STEPS:
        exp = s["expect"]
        step = {
            "id": s["id"],
            "title": _pick(s["title"], lang),
            "desc": _pick(s["desc"], lang),
            "cmd": _pick(s["cmd"], lang),
            "nodes": s["nodes"],
            "edges": s["edges"],
            "edge": s["edge"],
            "expect": {
                "contains": _pick(exp.get("contains"), lang),
                "label": _pick(exp.get("label"), lang),
            },
        }
        if "ci" in exp:
            step["expect"]["ci"] = exp["ci"]
        out.append(step)
    return out


def run_step(step_id, lang):
    step = STEP_BY_ID.get(step_id)
    if step is None:
        return {"ok": False, "cmd": "", "output": f"unknown step: {step_id}", "code": -1}
    cmd = _pick(step["cmd"], lang)
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
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            try:
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(500, b"index.html not found next to server.py", "text/plain")
            return
        if path == "/api/steps":
            lang = _norm_lang((parse_qs(parsed.query).get("lang", [DEFAULT_LANG]))[0])
            self._send(200, json.dumps({"steps": public_steps(lang),
                                        "domain": PUBLIC_DOMAIN, "lang": lang}))
            return
        self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):  # noqa: N802
        if urlparse(self.path).path != "/api/run":
            self._send(404, json.dumps({"error": "not found"}))
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._send(400, json.dumps({"error": "bad json"}))
            return
        step_id = body.get("step", "")
        lang = _norm_lang(body.get("lang", DEFAULT_LANG))
        self._send(200, json.dumps(run_step(step_id, lang)))

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
