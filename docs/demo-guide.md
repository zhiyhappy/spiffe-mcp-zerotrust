# SPIFFE / SPIRE Zero-Trust Demo — Presenter's Guide

A step-by-step runbook for demonstrating **workload identity, mTLS, and zero-secret
cloud federation** with SPIFFE/SPIRE. Written for a mixed audience: each step has
**What you'll see** (the visible effect), **Under the hood** (the technical explanation),
and **Why it matters** (the business talking point).

- **Environment:** Azure VM · public host `spiffe.ethandemo.com` · trust domain `ethandemo.com` · Docker Compose
- **Architecture diagram:** [`architecture.png`](architecture.png)
- **Latest acceptance run:** [`acceptance-report.md`](acceptance-report.md) — 8/8 pass
- **中文版：** [`demo-guide.zh.md`](demo-guide.zh.md)

---

## The one-sentence pitch

> Every workload gets a short-lived, cryptographic identity issued automatically — and the
> **same identity** is what gates the network (service-to-service mTLS) *and* the cloud
> (getting an Azure token **with no stored secret**). Registration = authorization; nothing
> unregistered can talk to anything.

**The problem it solves:** today teams protect service-to-service calls with long-lived API
keys, shared passwords, and secrets copied into config files and CI. Those secrets leak,
rarely rotate, and grant standing access. SPIFFE replaces them with identities that are
issued on attestation, expire in an hour, and can be revoked centrally by deleting one entry.

---

## Cast of characters (what's running)

| Component | Role in the story |
|---|---|
| **SPIRE Server + Agent** | The identity authority. Verifies *what* each container is and issues it an SVID (SPIFFE Verifiable Identity Document). |
| **Open WebUI** | The "AI agent" app. Talks plain HTTP to `localhost`; the mesh secures the rest. |
| **envoy-client / envoy-server** | Transparent mTLS sidecars, one per app, sharing the app's network namespace. |
| **MCP Server** | The backend tool/API (FastMCP + SQLite). Bound to `127.0.0.1` only — no public door. |
| **Caddy** | Public TLS edge; publishes the SPIFFE JWKS at `/keys`; proxies SSO. |
| **OIDC Discovery Provider** | Turns SPIRE's signing keys into a standard OIDC/JWKS endpoint the cloud can read. |
| **Microsoft Entra ID / Key Vault** | The external cloud that trusts a SPIFFE identity — no secret exchanged. |
| **hacker** | An unregistered container. Our stand-in for a compromised or rogue workload. |

---

## Pre-flight (before the audience arrives)

```bash
ssh ezhi@spiffe.ethandemo.com
cd ~/identity
docker compose ps                # all services Up
curl -s -o /dev/null -w '%{http_code}\n' https://spiffe.ethandemo.com/keys   # expect 200
```

Everything below is produced by a single script — **`bash scripts/demo.sh`** — but the guide
breaks it into scenes so you can pause and narrate. Set this once per shell:

```bash
export PUBLIC_DOMAIN=spiffe.ethandemo.com
set -a; . ./.env; set +a
```

---

## Step 1 — Identities exist because workloads were *attested*

**Run**
```bash
docker compose exec spire-server spire-server entry show
```

**What you'll see** — four registration entries mapping a SPIFFE ID to a container selector:

| SPIFFE ID | Selector (how it's recognized) |
|---|---|
| `spiffe://ethandemo.com/agent` | `docker:label:app:openwebui` |
| `spiffe://ethandemo.com/mcp-server` | `docker:label:app:mcp-server` |
| `spiffe://ethandemo.com/oidc-discovery-provider` | `docker:label:app:oidc-discovery-provider` |
| `spiffe://ethandemo.com/node` | the agent's one-time join token |

**Under the hood** — SPIRE never trusts a container's *claim* about who it is. The SPIRE
Agent runs a **docker workload attestor**: when a workload connects to the local Workload API
socket, the agent resolves the caller's PID (`pid: host` + read-only `docker.sock`) to a real
container and reads its labels. Only if the labels match a registered *selector* does SPIRE
issue that workload its SVID. The node itself was attested first via a one-time join token.

**Why it matters** — Identity is **earned by proof, not asserted by config**. There's no
password to steal that would let an attacker "become" the agent. And this table is the single
authorization list for the whole system — add a row to grant access, delete a row to revoke it.

---

## Step 2 — A legitimate call succeeds over automatic mTLS

**Run**
```bash
docker compose exec open-webui sh -c \
  'curl -s localhost:10000/mcp -H "Content-Type: application/json" \
   -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{}}"'
```

**What you'll see** — a normal JSON-RPC response:
```
serverInfo: {"name":"spiffe-demo-mcp","version":"3.4.7"}   ->  mTLS path OK
```

**Under the hood** — The app spoke **plain HTTP to `localhost:10000`**. That port is its
`envoy-client` sidecar (same network namespace). Envoy fetched a short-lived **X.509-SVID**
over SDS, opened a **mutual-TLS** connection to `envoy-server`, and both sides verified each
other's SPIFFE ID via SAN pinning. `envoy-server` then forwarded plain HTTP to the MCP app on
`127.0.0.1:8000`. The application code contains **zero** TLS logic, certificates, or identity
handling — the mesh did it all.

**Why it matters** — You get encrypted, mutually-authenticated service-to-service traffic
**without changing application code** and without managing certificates by hand. Certs are
minted on demand and expire hourly, so a leaked cert is worthless within the hour.

> **Tip — call a real tool, not just `initialize`.** [`scripts/mcp-call.sh`](../scripts/mcp-call.sh)
> runs the full MCP handshake (initialize → session id → `notifications/initialized` → `tools/*`)
> over the exact same mesh path, so you can show live data instead of a bare protocol reply:
> ```bash
> scripts/mcp-call.sh                          # list the tools the MCP server exposes
> scripts/mcp-call.sh list_employees           # -> Ada Lovelace, Alan Turing, Grace Hopper
> scripts/mcp-call.sh find_employees Platform  # -> just the Platform team
> ```

---

## Step 3 — An unregistered "hacker" is rejected three different ways

**Run**
```bash
docker compose exec hacker /attack.sh
```

**What you'll see** — three failed attack attempts:

| Attack | Result |
|---|---|
| Hit the mTLS port `mcp-server:9000` with no client cert | `curl (55) Broken pipe` — **rejected** |
| Bypass the sidecar, hit the app port `:8000` directly | `Could not connect` — **unreachable** |
| Try to fetch its own SVID from SPIRE | no Workload API socket — **can't get an identity** |

**Under the hood** — The `hacker` container has no matching label, so SPIRE issues it no SVID
(Step 1's list has no row for it). `envoy-server` therefore rejects its TLS handshake — mTLS
requires a *valid, peer-verified* client certificate, not just any TLS. The app port `:8000`
is bound to loopback inside the MCP netns, so it isn't reachable on the network at all. And
the SPIRE agent socket is only mounted into attested workloads, so the hacker can't even ask
for an identity to forge.

**Why it matters** — This is **zero-trust in action**: being *inside the network* grants an
attacker nothing. There is no shared secret to steal, no flat network to move laterally
across, and no service that accepts anonymous callers. Compromising one container does not
compromise its neighbors.

---

## Step 4 — The identity is portable to the public internet (OIDC/JWKS)

**Run**
```bash
curl -s https://spiffe.ethandemo.com/.well-known/openid-configuration
```

**What you'll see** — a standard OIDC discovery document:
```json
{ "issuer": "https://spiffe.ethandemo.com",
  "jwks_uri": "https://spiffe.ethandemo.com/keys",
  "id_token_signing_alg_values_supported": ["RS256","ES256","ES384"] }
```

**Under the hood** — The OIDC Discovery Provider fetches SPIRE's JWT signing keys over the
Workload API and republishes them as a normal **JWKS** endpoint. Caddy serves it over public
TLS (Let's Encrypt). Any relying party on the internet can now validate a SPIFFE **JWT-SVID**
using nothing but this public URL — the same way it would validate a Google or Okta token.

**Why it matters** — SPIFFE identities aren't locked inside the cluster. Because they're
published in an open standard (OIDC), **external clouds and SaaS can trust them directly** —
which sets up the finale.

---

## Step 5 — Zero-secret cloud federation (the finale)

The payoff: a workload uses **its SPIFFE identity as the credential** to obtain a Microsoft
Entra token — **no client secret anywhere** — and that token would read an Azure Key Vault
secret.

### 5a — The legitimate agent succeeds

**Run**
```bash
docker compose exec federation-demo /federate.sh
```

**What you'll see**
```
SVID acquired; sub=spiffe://ethandemo.com/agent
Got an Entra access token (no secret used). Claims:
   appid = 825be817-...            (our Entra app)
   iss   = https://sts.windows.net/6e3ed169-.../   (Entra tenant)
   aud   = https://vault.azure.net (Key Vault audience)
```

**Under the hood** — `federate.sh` fetches a **JWT-SVID** (`sub=spiffe://ethandemo.com/agent`,
`aud=api://AzureADTokenExchange`) and presents it to Entra as an **OIDC client assertion**
(`grant_type=client_credentials`, **no `client_secret`**). Entra validates the assertion by
fetching SPIRE's JWKS from `https://spiffe.ethandemo.com/keys` (Step 4) and checking it
against a **Federated Credential** configured on the app (issuer + subject + audience must all
match). It returns a real Azure access token scoped to Key Vault.

**Why it matters** — This is the **death of the stored cloud secret**. Normally this app would
hold an Entra client secret or certificate — a long-lived credential that leaks and must be
rotated. Here the credential is a 5-minute, cryptographically-attested identity. Nothing
sensitive sits in config, env vars, or CI.

### 5b — An impostor with a valid-but-wrong identity is rejected

**Run**
```bash
docker compose exec federation-impostor /federate.sh
```

**What you'll see**
```
SVID acquired; sub=spiffe://ethandemo.com/mcp-server
REJECTED by Entra:
   AADSTS700213: No matching federated identity record found for presented
   assertion subject 'spiffe://ethandemo.com/mcp-server'.
```

**Under the hood** — Same code, same trust domain, a **genuinely valid SVID** — but the wrong
*subject*. The Federated Credential only trusts `spiffe://ethandemo.com/agent`, so Entra
refuses `mcp-server`. Authorization is pinned to a specific identity, not "anyone we issued a
cert to."

**Why it matters** — Access is **least-privilege by identity**. Even a legitimate internal
workload can't reach the cloud resource unless it's *the specific identity* that was granted
access. One misconfigured or compromised service can't escalate to another's cloud permissions.

> **Note on this environment:** the Entra app's tenant has no Azure subscription, so the final
> "read the Key Vault secret" step is intentionally skipped — `federate.sh` succeeds at the
> token exchange and exits. With a subscription, add a vault + secret + the `Key Vault Secrets
> User` RBAC role and set `KEY_VAULT_NAME`; the script then prints the retrieved secret value.

---

## Step 6 (optional, high-impact) — Break it live: one delete revokes everything

This scene makes "registration = authorization" visceral.

**Run**
```bash
# Delete the /agent registration entry
ID=$(docker compose exec -T spire-server spire-server entry show \
      -spiffeID spiffe://ethandemo.com/agent | awk '/Entry ID/{print $4; exit}')
docker compose exec spire-server spire-server entry delete -entryID "$ID"

# Now BOTH the mesh call (Step 2) and cloud federation (Step 5a) stop working:
docker compose exec federation-demo /federate.sh      # FAILS — no /agent SVID to present

# Restore it — access comes right back:
docker compose exec spire-server /opt/spire/scripts/register-entries.sh
```

**What you'll see** — after the delete, the agent can't get an SVID, so both its mTLS calls
and its cloud token requests fail. After re-registering, everything works again within seconds.

**Why it matters** — **One control plane governs both network and cloud access.** Off-boarding
a workload — or containing a breach — is a single revocation, and it propagates everywhere
that identity was trusted. No hunting down scattered API keys across services and clouds.

---

## Optional — Demo in the browser (Open WebUI GUI)

The steps above are the technical core (run in a terminal). For a business audience you can
open the actual product UI and show two things: **single sign-on** and the **MCP tool backend**
reachable only through the mesh. (This lab deliberately ships **no chat model**, so we don't
demo an LLM conversation — just SSO + the tool path.)

### G1 — Sign in with corporate identity (SSO)

1. Browse to **`https://spiffe.ethandemo.com`**.
2. Click **"Continue with Microsoft"**.
3. Complete the Microsoft Entra login (e.g. `se2@ethanzhi.onmicrosoft.com`).
4. You land in Open WebUI, signed in — **no local username/password was ever created**.

**Under the hood** — Open WebUI uses its native `microsoft` OIDC provider. The browser is
redirected to Entra, comes back to `…/oauth/microsoft/callback`, and Open WebUI exchanges the
code for tokens and provisions the user on the fly (`ENABLE_OAUTH_SIGNUP=true`,
`OAUTH_MERGE_ACCOUNTS_BY_EMAIL=true`). Caddy terminates public TLS in front of it.

**Why it matters** — The app itself stores **no passwords**. Access is governed by the
corporate IdP: joiners/leavers, MFA, and conditional access are all handled centrally in Entra.

> **Gotcha (already fixed here).** Cloud-only Entra accounts have no mailbox, so Microsoft's
> `userinfo` omits the `email` claim and returns only `preferred_username`. Open WebUI requires
> an email and otherwise fails the callback with a misleading *"The email or password provided
> is incorrect."* The fix is one env var — `OAUTH_EMAIL_CLAIM=preferred_username` — set on the
> `open-webui` service. Also register **both** redirect URIs on the Entra app:
> `…/oauth/microsoft/callback` (native provider) and `…/oauth/oidc/callback`.

### G2 — Show the tool backend that only the mesh can reach

The employee-directory tools the agent would call live in the MCP server, which is bound to
`127.0.0.1` behind its mTLS sidecar — unreachable from the browser or the public internet.
Prove the *authorized* path returns data while narrating that nothing outside the mesh can:

```bash
scripts/mcp-call.sh list_employees           # data comes back over mTLS
scripts/mcp-call.sh find_employees Platform  # filtered query, same secured path
```

Contrast with **Step 3** (the `hacker` container gets `Broken pipe` / `Could not connect`):
same network, no identity, no access.

**Why it matters** — SSO proves *who the human is*; SPIFFE proves *what the workload is*. The
GUI login and the tool call are two halves of the same zero-trust story — human identity at the
edge, workload identity on the wire — with **no shared secret** on either side.

> **Operator note.** Recreating the `open-webui` container (e.g. after an env change) gives it a
> new network namespace, which orphans its `envoy-client` sidecar. Re-attach it with
> `docker compose up -d --force-recreate envoy-client`, or the mesh path (Step 2 / G2) returns
> `Connection refused` on `localhost:10000`.

---

## Run it all at once

```bash
export PUBLIC_DOMAIN=spiffe.ethandemo.com
bash scripts/demo.sh
```
Prints Steps 1–5 end to end. See [`acceptance-report.md`](acceptance-report.md) for a captured run.

---

## Talking points cheat-sheet (for the room)

| Audience question | One-liner answer |
|---|---|
| "How is this different from TLS/HTTPS we already have?" | HTTPS proves the *server*; this proves *both* sides, automatically, with hourly-rotating certs and no app code. |
| "What replaces our API keys and secrets?" | A short-lived, attested identity. Nothing long-lived to leak; revoke by deleting one entry. |
| "What if a container is hacked?" | It can't reach neighbors (mTLS), can't reach the cloud (wrong/absent identity), and can't forge an identity (attestation). |
| "Does this lock us into one cloud?" | No — identities are published as standard OIDC/JWKS; any cloud or SaaS that speaks OIDC federation can trust them. |
| "Is this production-grade?" | The pattern is (SPIFFE/SPIRE is CNCF-graduated, used in Istio-style meshes). This lab uses a few demo shortcuts noted in the README. |
