# SPIFFE / SPIRE 零信任 Demo — 端到端验收记录

**环境**：Azure VM · `spiffe.ethandemo.com` · trust domain `ethandemo.com` · Docker Compose
**日期**：2026-08-21
**执行命令**：`export PUBLIC_DOMAIN=spiffe.ethandemo.com && bash scripts/demo.sh`

## 结果概览

| # | 验收项 | 期望 | 结果 | 证据 |
|---|---|---|---|---|
| 1 | SPIRE 注册项 | 存在 4 条 entry | ✅ | `/agent`、`/mcp-server`、`/node`、`/oidc-discovery-provider` |
| 2 | 合法 mTLS 调用 | Open WebUI→sidecar→mTLS→MCP 成功 | ✅ | MCP `initialize` 返回 `serverInfo: spiffe-demo-mcp v3.4.7` |
| 3a | hacker 打 mTLS 端口 :9000 | 被拒 | ✅ | `curl (55) Broken pipe`（无合法客户端证书）|
| 3b | hacker 绕过 sidecar 打 :8000 | 不可达 | ✅ | MCP 只绑 `127.0.0.1`，`Could not connect` |
| 3c | hacker 取 SVID | 无 socket | ✅ | 无 `/tmp/spire-sockets/api.sock`，无法取证 |
| 4 | 公网 OIDC discovery / JWKS | Caddy 返回文档 | ✅ | `issuer=https://spiffe.ethandemo.com`，`jwks_uri=.../keys` |
| 5a | 联邦 `/agent`（合法）| 换到 Entra token（零密钥）| ✅ | `appid=825be817…`，`iss=sts.windows.net/6e3ed169…`，`aud=vault.azure.net` |
| 5b | 联邦 `/mcp-server`（冒充）| 被 Entra 拒绝 | ✅ | `AADSTS700213: No matching federated identity record` |

**结论：8/8 全部通过。** 零信任核心闭环成立——注册即授权，同一个 SPIFFE 身份同时门禁
**网络（mTLS 网格）**与**云（Entra 联邦，零密钥）**；未注册 / 错误身份在网络层和云层都被拒。

**范围外（可选）**：联邦第 3 步「用 token 读 Azure Key Vault secret」未执行——Entra 应用所在
tenant（`ethanzhi` / `6e3ed169`）无 Azure 订阅，故 `.env` 中 `KEY_VAULT_NAME` 留空，
`federate.sh` 在换到 token 后成功退出。若该 tenant 后续开通订阅，只需建 vault + 写 secret +
给应用配 `Key Vault Secrets User` RBAC + 填 `KEY_VAULT_NAME`，即可跑通该步。

---

## 完整输出

```text
############################################################
# 1. SPIRE registration entries
############################################################
Found 4 entries
Entry ID         : 76682cde-28ad-4970-9bf2-4060b6bf8bc8
SPIFFE ID        : spiffe://ethandemo.com/agent
Parent ID        : spiffe://ethandemo.com/node
Selector         : docker:label:app:openwebui

Entry ID         : 648cf428-a946-4119-8e88-36fd6b536c99
SPIFFE ID        : spiffe://ethandemo.com/mcp-server
Parent ID        : spiffe://ethandemo.com/node
Selector         : docker:label:app:mcp-server

Entry ID         : 25c75fc9-9249-47ee-988f-f2cdeff878bf
SPIFFE ID        : spiffe://ethandemo.com/node
Parent ID        : spiffe://ethandemo.com/spire/agent/join_token/758f7550-00e1-463e-a1d8-eca2ed38010e

Entry ID         : 96d8351e-a1e5-4ee2-ac09-8a9acb4ed539
SPIFFE ID        : spiffe://ethandemo.com/oidc-discovery-provider
Parent ID        : spiffe://ethandemo.com/node
Selector         : docker:label:app:oidc-discovery-provider

############################################################
# 2. Legitimate call: Open WebUI -> localhost:10000 (client sidecar)
#    -> mTLS -> mcp-server:9000 (server sidecar) -> 127.0.0.1:8000 (MCP)
############################################################
event: message
data: {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05",...,
  "serverInfo":{"name":"spiffe-demo-mcp","version":"3.4.7"}}}

  -> mTLS path OK

############################################################
# 3. Hacker container (no sidecar / no SVID) -> should be REJECTED
############################################################
== 1) Try the mTLS-only MCP port (:9000) without a client cert ==
curl: (55) Send failure: Broken pipe
  -> rejected (as expected: mTLS requires a valid SPIFFE client cert)

== 2) Try to bypass the sidecar and hit the MCP app port (:8000) directly ==
curl: (7) Failed to connect to mcp-server port 8000 after 0 ms: Could not connect to server
  -> unreachable (as expected: MCP binds 127.0.0.1 behind the sidecar)

== 3) Confirm no SPIRE socket is available to this container ==
  -> no /tmp/spire-sockets/api.sock: cannot fetch an SVID. Attack blocked.

############################################################
# 4. Public OIDC discovery / JWKS (served via Caddy)
############################################################
{
  "issuer": "https://spiffe.ethandemo.com",
  "jwks_uri": "https://spiffe.ethandemo.com/keys",
  "id_token_signing_alg_values_supported": ["RS256","ES256","ES384"]
}

############################################################
# 5. Workload federation (B): SPIFFE JWT-SVID -> Entra -> Key Vault (no secret)
############################################################
--- 5a. LEGIT agent (spiffe://ethandemo.com/agent) -> should SUCCEED ---
== 1) Fetch JWT-SVID from SPIRE (audience=api://AzureADTokenExchange) ==
   SVID acquired; sub=spiffe://ethandemo.com/agent

== 2) Exchange the JWT-SVID for an Entra token (NO client secret; scope=https://vault.azure.net/.default) ==
   Got an Entra access token (no secret used). Claims:
     aud   = cfa8b339-82a2-471a-a3c9-0fc0be7a4093
     appid = 825be817-6186-4fae-a60b-182b0ebbba80
     iss   = https://sts.windows.net/6e3ed169-a445-4e65-bcfb-1269f301c4d7/

== Done. (Set KEY_VAULT_NAME + SECRET_NAME to also read a Key Vault secret.) ==

--- 5b. IMPOSTOR (spiffe://ethandemo.com/mcp-server) -> should be REJECTED ---
== 1) Fetch JWT-SVID from SPIRE (audience=api://AzureADTokenExchange) ==
   SVID acquired; sub=spiffe://ethandemo.com/mcp-server

== 2) Exchange the JWT-SVID for an Entra token (NO client secret; scope=https://vault.azure.net/.default) ==
   REJECTED by Entra:
   invalid_client: AADSTS700213: No matching federated identity record found for presented
   assertion subject 'spiffe://ethandemo.com/mcp-server'.

== RESULT: this identity (spiffe://ethandemo.com/mcp-server) is NOT authorized to federate. ==
```
