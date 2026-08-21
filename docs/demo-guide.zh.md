# SPIFFE / SPIRE 零信任 Demo — 演示指南

一份用于演示 **工作负载身份、mTLS、以及零密钥云联邦（SPIFFE/SPIRE）** 的分步脚本。
面向技术人员与销售人员双重受众：每一步都包含 **你会看到什么**（可见效果）、
**背后原理**（技术说明）、**业务价值**（销售话术）。

- **环境**：Azure VM · 公网域名 `spiffe.ethandemo.com` · trust domain `ethandemo.com` · Docker Compose
- **架构图**：[`architecture.png`](architecture.png)
- **最新验收记录**：[`acceptance-report.md`](acceptance-report.md) —— 8/8 全部通过
- **English version**：[`demo-guide.md`](demo-guide.md)

---

## 一句话卖点

> 每个工作负载都会**自动**获得一个短时、加密的身份 —— 而**同一个身份**同时门禁
> **网络**（服务间 mTLS）和**云**（获取 Azure token **无需任何存储密钥**）。
> 注册即授权；任何未注册的东西都无法与任何服务通信。

**它解决的问题**：今天团队用长期 API key、共享口令、以及散落在配置文件和 CI 里的密钥来保护
服务间调用。这些密钥会泄露、极少轮换、且授予的是长期访问权。SPIFFE 用一种全新的身份取而代之：
基于**证明**签发、**一小时后过期**、并可通过删除一条注册项**集中吊销**。

---

## 出场角色（正在运行的组件）

| 组件 | 在故事里的角色 |
|---|---|
| **SPIRE Server + Agent** | 身份权威。核验每个容器**究竟是什么**，并为其签发 SVID（SPIFFE 可验证身份文档）。 |
| **Open WebUI** | “AI Agent”应用。只对 `localhost` 说明文 HTTP，其余交给网格加密。 |
| **envoy-client / envoy-server** | 透明 mTLS sidecar，每个应用一个，与应用共享网络命名空间。 |
| **MCP Server** | 后端工具/API（FastMCP + SQLite）。仅绑定 `127.0.0.1`，对外无入口。 |
| **Caddy** | 公网 TLS 边缘；在 `/keys` 发布 SPIFFE JWKS；代理 SSO。 |
| **OIDC Discovery Provider** | 把 SPIRE 的签名密钥变成云端可读的标准 OIDC/JWKS 端点。 |
| **Microsoft Entra ID / Key Vault** | 信任 SPIFFE 身份的外部云 —— 全程不交换任何密钥。 |
| **hacker** | 一个未注册的容器。我们用它模拟被攻陷或流氓工作负载。 |

---

## 演示前检查（观众到场之前）

```bash
ssh ezhi@spiffe.ethandemo.com
cd ~/identity
docker compose ps                # 所有服务 Up
curl -s -o /dev/null -w '%{http_code}\n' https://spiffe.ethandemo.com/keys   # 期望 200
```

下文所有内容都可由一条脚本 **`bash scripts/demo.sh`** 产出，但本指南拆成一幕幕，便于你
边演示边讲解。每个 shell 会话先执行一次：

```bash
export PUBLIC_DOMAIN=spiffe.ethandemo.com
set -a; . ./.env; set +a
```

---

## 第 1 步 —— 身份因工作负载被**证明（attest）**而存在

**运行**
```bash
docker compose exec spire-server spire-server entry show
```

**你会看到什么** —— 四条注册项，把 SPIFFE ID 映射到容器选择器：

| SPIFFE ID | 选择器（如何被识别）|
|---|---|
| `spiffe://ethandemo.com/agent` | `docker:label:app:openwebui` |
| `spiffe://ethandemo.com/mcp-server` | `docker:label:app:mcp-server` |
| `spiffe://ethandemo.com/oidc-discovery-provider` | `docker:label:app:oidc-discovery-provider` |
| `spiffe://ethandemo.com/node` | agent 的一次性 join token |

**背后原理** —— SPIRE 从不相信容器对“我是谁”的**自述**。SPIRE Agent 运行一个
**docker workload attestor**：当工作负载连接到本地 Workload API socket 时，agent 通过
调用方 PID（`pid: host` + 只读 `docker.sock`）反查到真实容器并读取其标签。只有标签匹配已
注册的**选择器**，SPIRE 才为该工作负载签发 SVID。节点本身则先通过一次性 join token 完成证明。

**业务价值** —— 身份是**靠证明赢得的，而非靠配置声明的**。没有任何口令可被窃取来“冒充”
agent。而且这张表就是整个系统唯一的授权清单 —— 加一行即授权，删一行即吊销。

---

## 第 2 步 —— 合法调用通过自动 mTLS 成功

**运行**
```bash
docker compose exec open-webui sh -c \
  'curl -s localhost:10000/mcp -H "Content-Type: application/json" \
   -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{}}"'
```

**你会看到什么** —— 一个正常的 JSON-RPC 响应：
```
serverInfo: {"name":"spiffe-demo-mcp","version":"3.4.7"}   ->  mTLS path OK
```

**背后原理** —— 应用只对 **`localhost:10000` 说明文 HTTP**。这个端口就是它的
`envoy-client` sidecar（同一网络命名空间）。Envoy 通过 SDS 取到一张短时 **X.509-SVID**，与
`envoy-server` 建立**双向 TLS**，双方通过 SAN pinning 互验对方的 SPIFFE ID。`envoy-server`
再把明文 HTTP 转发给 `127.0.0.1:8000` 上的 MCP 应用。应用代码里**没有任何** TLS 逻辑、
证书或身份处理 —— 全部由网格完成。

**业务价值** —— 无需改动应用代码、无需手工管理证书，即可获得加密且双向认证的服务间流量。
证书按需签发、每小时过期，因此一张泄露的证书一小时内就作废。

> **小贴士 —— 调用真实工具，而不只是 `initialize`。** [`scripts/mcp-call.sh`](../scripts/mcp-call.sh)
> 会走完整的 MCP 握手（initialize → 会话 id → `notifications/initialized` → `tools/*`），
> 且走的是**完全相同的网格路径**,因此可以展示真实数据而非仅仅一个协议应答：
> ```bash
> scripts/mcp-call.sh                          # 列出 MCP server 暴露的工具
> scripts/mcp-call.sh list_employees           # -> Ada Lovelace、Alan Turing、Grace Hopper
> scripts/mcp-call.sh find_employees Platform  # -> 仅 Platform 团队
> ```

---

## 第 3 步 —— 未注册的“hacker”被以三种方式拒绝

**运行**
```bash
docker compose exec hacker /attack.sh
```

**你会看到什么** —— 三次攻击尝试全部失败：

| 攻击 | 结果 |
|---|---|
| 用无客户端证书打 mTLS 端口 `mcp-server:9000` | `curl (55) Broken pipe` —— **被拒** |
| 绕过 sidecar，直接打应用端口 `:8000` | `Could not connect` —— **不可达** |
| 尝试向 SPIRE 索取自己的 SVID | 无 Workload API socket —— **拿不到身份** |

**背后原理** —— `hacker` 容器没有匹配的标签，所以 SPIRE 不给它签发 SVID（第 1 步的清单里
没有它这一行）。因此 `envoy-server` 拒绝它的 TLS 握手 —— mTLS 要求的是**经对端验证的、
合法的**客户端证书，而非随便一个 TLS。应用端口 `:8000` 绑定在 MCP 命名空间内的回环地址，
根本不在网络上可达。而 SPIRE agent socket 只挂载给已证明的工作负载，所以 hacker 连索取
身份来伪造的机会都没有。

**业务价值** —— 这就是**零信任的实战**：待在网络内部并不能给攻击者任何东西。没有共享密钥
可偷、没有可横向移动的扁平网络、没有接受匿名调用的服务。攻陷一个容器并不会殃及其邻居。

---

## 第 4 步 —— 身份可携带到公网（OIDC/JWKS）

**运行**
```bash
curl -s https://spiffe.ethandemo.com/.well-known/openid-configuration
```

**你会看到什么** —— 一份标准的 OIDC discovery 文档：
```json
{ "issuer": "https://spiffe.ethandemo.com",
  "jwks_uri": "https://spiffe.ethandemo.com/keys",
  "id_token_signing_alg_values_supported": ["RS256","ES256","ES384"] }
```

**背后原理** —— OIDC Discovery Provider 通过 Workload API 取到 SPIRE 的 JWT 签名密钥，并把
它们重新发布为一个标准 **JWKS** 端点。Caddy 用公网 TLS（Let's Encrypt）对外提供。互联网上
任何依赖方现在都能仅凭这个公开 URL 来验证 SPIFFE **JWT-SVID** —— 就像验证 Google 或 Okta
的 token 一样。

**业务价值** —— SPIFFE 身份并不被锁死在集群内部。因为它以开放标准（OIDC）发布，
**外部云和 SaaS 可以直接信任它** —— 这就为压轴戏铺好了路。

---

## 第 5 步 —— 零密钥云联邦（压轴）

回报来了：一个工作负载用**它的 SPIFFE 身份作为凭据**去换取 Microsoft Entra token ——
**任何地方都没有 client secret** —— 而这个 token 可用于读取 Azure Key Vault secret。

### 5a —— 合法 agent 成功

**运行**
```bash
docker compose exec federation-demo /federate.sh
```

**你会看到什么**
```
SVID acquired; sub=spiffe://ethandemo.com/agent
Got an Entra access token (no secret used). Claims:
   appid = 825be817-...            (我们的 Entra 应用)
   iss   = https://sts.windows.net/6e3ed169-.../   (Entra tenant)
   aud   = https://vault.azure.net (Key Vault 受众)
```

**背后原理** —— `federate.sh` 取到一张 **JWT-SVID**（`sub=spiffe://ethandemo.com/agent`，
`aud=api://AzureADTokenExchange`），并把它作为 **OIDC 客户端断言** 提交给 Entra
（`grant_type=client_credentials`，**无 `client_secret`**）。Entra 通过从
`https://spiffe.ethandemo.com/keys`（第 4 步）拉取 SPIRE 的 JWKS 来验证该断言，并核对应用上
配置的 **Federated Credential**（issuer + subject + audience 必须全部匹配）。核对通过后，
它返回一个作用域为 Key Vault 的真实 Azure access token。

**业务价值** —— 这是**存储式云密钥的终结**。通常这个应用需要持有一个 Entra client secret 或
证书 —— 一个会泄露、必须轮换的长期凭据。而这里的凭据是一个 5 分钟、经加密证明的身份。
配置、环境变量、CI 里都没有任何敏感信息。

### 5b —— 持有“合法但错误”身份的冒充者被拒绝

**运行**
```bash
docker compose exec federation-impostor /federate.sh
```

**你会看到什么**
```
SVID acquired; sub=spiffe://ethandemo.com/mcp-server
REJECTED by Entra:
   AADSTS700213: No matching federated identity record found for presented
   assertion subject 'spiffe://ethandemo.com/mcp-server'.
```

**背后原理** —— 相同的代码、相同的 trust domain、一张**货真价实的合法 SVID** —— 但 subject
错了。Federated Credential 只信任 `spiffe://ethandemo.com/agent`，所以 Entra 拒绝
`mcp-server`。授权被钉死在**某个特定身份**上，而不是“任何我们签过证书的人”。

**业务价值** —— 访问权是**按身份的最小权限**。即便是一个合法的内部工作负载，只要它不是
被授权的**那个特定身份**，就无法触达该云资源。一个配置错误或被攻陷的服务，无法越权拿到
另一个服务的云权限。

> **关于本环境的说明**：该 Entra 应用所在 tenant 没有 Azure 订阅，所以最后“读取 Key Vault
> secret”那一步被有意跳过 —— `federate.sh` 在换到 token 后即成功退出。若有订阅，只需新建
> vault + secret + 给应用配 `Key Vault Secrets User` RBAC 角色并设置 `KEY_VAULT_NAME`，
> 脚本便会打印取回的 secret 值。

---

## 第 6 步（可选，效果拔群）—— 现场“搞坏它”：一次删除吊销一切

这一幕让“注册即授权”变得可感可知。

**运行**
```bash
# 删除 /agent 的注册项
ID=$(docker compose exec -T spire-server spire-server entry show \
      -spiffeID spiffe://ethandemo.com/agent | awk '/Entry ID/{print $4; exit}')
docker compose exec spire-server spire-server entry delete -entryID "$ID"

# 现在网格调用（第 2 步）和云联邦（第 5a 步）双双停摆：
docker compose exec federation-demo /federate.sh      # 失败 —— 没有 /agent SVID 可提交

# 恢复它 —— 访问立刻回来：
docker compose exec spire-server /opt/spire/scripts/register-entries.sh
```

**你会看到什么** —— 删除后，agent 拿不到 SVID，于是它的 mTLS 调用和云 token 请求都失败。
重新注册后，一切在数秒内恢复正常。

**业务价值** —— **一个控制平面同时治理网络与云访问**。下线一个工作负载 —— 或遏制一次入侵
—— 只需一次吊销，就会传播到该身份被信任的所有地方。无需在各服务、各云之间四处搜罗散落的
API key。

---

## 可选 —— 在浏览器里演示（Open WebUI GUI）

上面的步骤是技术内核（在终端里跑）。面向业务受众时,可以打开真实的产品界面,展示两件事:
**单点登录（SSO）** 与 **只能经网格触达的 MCP 工具后端**。(本 lab 有意**不配聊天模型**,
所以我们不演示 LLM 对话 —— 只演 SSO + 工具路径。)

### G1 —— 用企业身份登录（SSO）

1. 浏览器打开 **`https://spiffe.ethandemo.com`**。
2. 点击 **"Continue with Microsoft"**。
3. 完成 Microsoft Entra 登录（例如 `se2@ethanzhi.onmicrosoft.com`）。
4. 你会登入 Open WebUI —— **从未创建过任何本地用户名/密码**。

**背后原理** —— Open WebUI 使用其原生 `microsoft` OIDC provider。浏览器被重定向到 Entra,
再回到 `…/oauth/microsoft/callback`,Open WebUI 用授权码换取 token 并即时开通该用户
（`ENABLE_OAUTH_SIGNUP=true`、`OAUTH_MERGE_ACCOUNTS_BY_EMAIL=true`）。Caddy 在前端终结公网 TLS。

**业务价值** —— 应用本身**不存储任何口令**。访问由企业 IdP 治理:入职/离职、MFA、条件访问
全部在 Entra 集中管理。

> **坑（这里已修复）。** 纯云端 Entra 账号没有邮箱,所以微软的 `userinfo` 不返回 `email`
> claim,只返回 `preferred_username`。Open WebUI 强制要邮箱,拿不到就用一句极具误导性的
> *"The email or password provided is incorrect."* 让回调失败。修复只需一个环境变量 ——
> 在 `open-webui` 服务上设 `OAUTH_EMAIL_CLAIM=preferred_username`。另外在 Entra 应用上要
> **同时**注册两个重定向 URI:`…/oauth/microsoft/callback`（原生 provider）与
> `…/oauth/oidc/callback`。

### G2 —— 展示只有网格才能触达的工具后端

Agent 会调用的「员工目录」工具位于 MCP server 中,它绑定在 mTLS sidecar 后的 `127.0.0.1`,
浏览器和公网都无法触达。演示**被授权**的路径能返回数据,同时讲解网格之外的任何人都不行:

```bash
scripts/mcp-call.sh list_employees           # 数据经 mTLS 返回
scripts/mcp-call.sh find_employees Platform  # 过滤查询,同一条受保护路径
```

与**第 3 步**对比（`hacker` 容器得到 `Broken pipe` / `Could not connect`）:
同一网络、无身份、无访问。

> **在 Open WebUI GUI 里接入(Admin → Settings → Tools → MCP)。** 添加一个 MCP server,
> URL 填 `http://localhost:10000/mcp` —— 这是 open-webui 容器**内部**的 `envoy-client` sidecar,
> 所以**后端**会经 mTLS 连到 MCP server。Open WebUI 从后端保存并使用这条连接,是能用的。
> **但 GUI 里那个"连接测试"红色徽标会报错 —— 这是正常且仅为表象:** 你浏览器里的 `localhost`
> 是你自己的电脑,不是容器;按设计,网格之外的任何人(包括浏览器)都无法触达 `:10000`。
> 工具依然由后端正常调用,可用 `scripts/mcp-call.sh` 验证。(不要为了把徽标变绿就把 `/mcp`
> 对公网暴露 —— 那会让工具可从公网直接调用,削弱零信任叙事。)

**业务价值** —— SSO 证明**人是谁**;SPIFFE 证明**工作负载是什么**。GUI 登录与工具调用是
同一个零信任故事的两半 —— 边缘的人的身份 + 链路上的工作负载身份 —— 两侧都**没有共享密钥**。

> **运维提示。** 重建 `open-webui` 容器（例如改了环境变量后）会给它一个新的网络命名空间,
> 从而让它的 `envoy-client` sidecar 掉线孤立。用
> `docker compose up -d --force-recreate envoy-client` 重新挂载,否则网格路径（第 2 步 / G2）
> 会在 `localhost:10000` 报 `Connection refused`。

---

## 一键跑完全流程

```bash
export PUBLIC_DOMAIN=spiffe.ethandemo.com
bash scripts/demo.sh
```
端到端打印第 1–5 步。已捕获的一次运行见 [`acceptance-report.md`](acceptance-report.md)。

---

## 现场问答速查表

| 观众提问 | 一句话回答 |
|---|---|
| “这跟我们已有的 TLS/HTTPS 有何不同？” | HTTPS 只证明**服务器**；这里**双方**都被证明，自动完成，证书每小时轮换，且无需应用代码。 |
| “它替代我们的哪些 API key 和密钥？” | 用一个短时、经证明的身份替代。没有长期凭据可泄露；删一条注册项即吊销。 |
| “如果某个容器被攻陷了呢？” | 它触达不了邻居（mTLS）、触达不了云（身份错误或缺失）、也伪造不出身份（attestation）。 |
| “这会不会把我们锁定在某一朵云上？” | 不会 —— 身份以标准 OIDC/JWKS 发布；任何支持 OIDC 联邦的云或 SaaS 都能信任它。 |
| “这是生产级的吗？” | 模式是（SPIFFE/SPIRE 是 CNCF 毕业项目，用于 Istio 式网格）。本 lab 采用了 README 中标注的若干演示便利项。 |
