# Phase 19 — a real agent, a real mailbox

Until Phase 19 every protected service in Aegis was a mock. The CRM was a
dictionary in memory and the "agent" was a fixed list of actions. That was
enough to build the control path and prove it held, and not enough to know
whether it holds against something that can actually surprise you.

Phase 19 replaces both ends with real ones: Gmail behind the connector, and an
AI agent with a model behind it that decides for itself what to ask for.

```
        REAL AI AGENT  (untrusted, holds only its Aegis token)
              |
              |  Aegis agent credential
              v
            AEGIS  ── deterministic authentication + policy
              |
              +── ALLOW ─────────────────────┐
              |                              |
              +── APPROVAL_REQUIRED ──> HUMAN APPROVAL
              |                              |
              +── DENY  (nothing happens)    |
                                             v
                              CONTROLLED GMAIL CONNECTOR
                                             |
                                             | server-side OAuth credential
                                             v
                                       REAL GMAIL API
```

The agent is not on that last arrow anywhere, and cannot be: see
[The boundary](#the-boundary).

---

## What a human has to do

Two things cannot be automated, and this document exists mostly for them. Both
are identity-sensitive: they require a person to log in as themselves and agree
to something.

**You will never be asked to paste a password, a token, a client secret or a
private key into a chat.** Everything below is typed into Google's own console
or into a local `.env` file that is gitignored.

### 1. Create a Google OAuth client

Where: <https://console.cloud.google.com>

1. **Create or select a project.** Top bar, project dropdown, *New Project*.
   Call it anything; `aegis-gmail-poc` is fine.
2. **Enable the Gmail API.** Navigation menu → *APIs & Services* → *Library* →
   search "Gmail API" → **Enable**.
3. **Configure the consent screen.** *APIs & Services* → *OAuth consent screen*.
   - User type: **External** (unless you have a Workspace org, in which case
     Internal is simpler and skips the test-user step).
   - App name, support email, developer email: your own.
   - **Scopes**: you do not need to add any here; Aegis requests them at
     runtime. If you add one anyway, add only
     `https://www.googleapis.com/auth/gmail.modify`.
   - **Test users**: add the Gmail address you intend to connect. While the app
     is in "Testing" this is required, and it is the right posture for a PoC —
     nobody else can consent.
4. **Create the client.** *APIs & Services* → *Credentials* → *Create
   credentials* → **OAuth client ID**.
   - Application type: **Web application**.
   - Authorised redirect URI: exactly
     `http://localhost:8000/api/gmail/oauth/callback`
     (or whatever you set `AEGIS_GOOGLE_REDIRECT_URI` to — it must match
     character for character, including the scheme and the trailing path).
   - **Create**.
5. Google shows a **client ID** and a **client secret**. Copy them into your
   local `.env`:

   ```
   AEGIS_GOOGLE_CLIENT_ID=<the client id>
   AEGIS_GOOGLE_CLIENT_SECRET=<the client secret>
   ```

   `.env` is gitignored. Do not commit it, do not paste the secret into an
   issue, a chat, or a screenshot.

**What success looks like:** `GET /api/gmail/status` reports
`"oauth_client_configured": true`, and the dashboard's Gmail page shows a
**Connect Gmail** button instead of a "not configured" notice.

### 2. Consent, as the mailbox owner

1. Open the dashboard, go to **Gmail**, click **Connect Gmail**.
2. Google asks which account. Choose the **test mailbox**, not your real one.
3. Google lists what is being requested. It will say something like *"Read,
   compose, send, and permanently delete all your email from Gmail"* — that is
   Google's wording for `gmail.modify`, and it is broader than what Aegis will
   ever do with it. Aegis denies `gmail.delete` in policy, and the scope
   requested does not include `https://mail.google.com/`, which is the one that
   grants true permanent deletion.
4. You will see an **unverified app** warning, because the client is in
   Testing. *Advanced* → *Go to … (unsafe)*. This is expected for a PoC with a
   client you created minutes ago; do not click through this warning for an app
   you did not create.
5. **Allow**.

**What success looks like:** the browser lands on a plain page saying *"Aegis
is connected to <address>"*, and the Gmail page shows the mailbox, the granted
scopes, and a Disconnect button. No token is shown anywhere, because no
endpoint returns one.

### 3. Give the agent a model

The agent needs an API key for an OpenAI-compatible provider. Any low-cost
model is fine for a PoC.

```
AEGIS_AGENT_LLM_BASE_URL=https://api.deepseek.com/v1
AEGIS_AGENT_LLM_API_KEY=<your key>
AEGIS_AGENT_LLM_MODEL=deepseek-chat
AEGIS_EGRESS_ALLOWLIST=api.deepseek.com
```

The host in `AEGIS_EGRESS_ALLOWLIST` must match the host in the base URL, or
the agent cannot reach its own model. **Do not add a Google host to that list.**
It is the list of everywhere the agent container may go, and adding
`gmail.googleapis.com` would hand it the direct route the whole design exists
to prevent.

---

## Running it

```bash
./scripts/init-env.sh          # generates the Aegis secrets; leaves Google blank
# ... edit .env with the values from the steps above ...
docker compose up -d --build
```

Then, with Gmail connected:

```bash
docker compose exec ai-agent python agent.py \
  "Find the latest email from Marco, read it, and draft a short reply."
```

The agent prints a transcript. Each step shows the canonical operation, the
decision, and whether anything ran.

To watch an approval: ask it to *send* something. The run pauses, the
dashboard's **Approvals** page shows the request, and the agent proceeds only
after a person approves it.

---

## The five operations

| Canonical | Default posture | What it does |
|---|---|---|
| `gmail.search` | ALLOW | List message ids and headers matching a query. |
| `gmail.read` | ALLOW | One message: headers, snippet, optionally a truncated body. |
| `gmail.draft` | ALLOW | Create a draft. Nothing leaves the mailbox. |
| `gmail.send` | APPROVAL_REQUIRED | Actually send. Irreversible. |
| `gmail.delete` | DENY | Refused. |

There is no sixth. The connector builds every request from constants in
`backend/app/protected/gmail.py`; there is no `execute(method, url, payload)`,
no passthrough of a caller-supplied path, and no field anywhere in the request
that becomes a Gmail URL. An unsupported operation name is rejected before a
credential is touched.

`gmail.delete` is refused four times over, and the tests prove each layer with
the one above it removed:

1. the agent holds no `DELETE` permission (least privilege);
2. an organization policy BLOCKs it at the highest priority, which catches an
   agent that *was* granted the permission;
3. the runtime contract does not list it as a capability;
4. the OAuth scope Aegis requests does not grant permanent deletion at all —
   a floor that lives at Google, not in our code.

---

## The boundary

The agent cannot reach Gmail. That claim is worth what the evidence behind it
is worth, so here is the evidence, labelled by strength:

| Control | Where | How it is checked |
|---|---|---|
| Agent is on an internal network only — no route off the host | `docker-compose.yml` | `test_phase19_network.py`, parsing the compose file |
| Gmail connector is on no network the agent is on | `docker-compose.yml` | same |
| Model traffic leaves via an allow-listed proxy; Google is not on the list | `infra/egress-proxy/proxy.py` | unit test of the host matcher, including lookalikes |
| The sealed Google credential is mounted by exactly two containers | `docker-compose.yml` | same |
| The agent's container holds no Google or internal secret | `docker-compose.yml` | environment is enumerated and checked |
| Containers really cannot reach each other at runtime | running stack | `infra/boundary/boundary_proof.py` — **needs a Docker daemon; skips without one** |

Only the last row is a runtime proof. The rest are configuration and unit
checks, and nothing in this repository claims otherwise.

---

## Where the credential lives

```
human consents at Google
        |
        v
control-plane  ── seals the refresh token ──> aegis-oauth volume
                                                    |
                                          (read-only) |
                                                    v
                                            gmail-connector ──> Google
```

- The refresh token is sealed with `AEGIS_OAUTH_ENCRYPTION_KEY`
  (`backend/app/secretbox.py`: encrypt-then-MAC over HMAC-SHA256) and bound to
  the tenant it belongs to, so moving one tenant's record into another's slot
  produces an authentication failure, not a working credential.
- Access tokens are **never persisted**. They are fetched when needed and held
  in memory.
- Exactly one function can produce a refresh token,
  `gmail_store.reveal_refresh_token`, and a test asserts that it stays the only
  one.
- The enforcement gateway and the credential broker never receive it. What the
  broker hands the connector is a per-tenant derived credential that proves the
  call came through Aegis — it is not the Google credential and cannot be used
  at Google.

**What this is not.** The encryption key is an environment variable, so anyone
who can read the process environment can read the tokens. This protects a
leaked file, not a compromised host. It is not a KMS, there is no rotation and
no envelope encryption. A deployment that needs those should put the refresh
tokens in a real secret store.

---

## Prompt injection

Gmail content is data. A message that tells the agent to send, delete, reveal
credentials or call Google directly is an attack report, not an instruction.

`backend/tests/test_phase19_prompt_injection.py` tests this the hard way: the
real agent runtime runs with a model that obeys the injected instructions
completely. The agent is fully compromised on purpose, and the mailbox is
untouched afterwards — the send needs a human, the delete is denied, the direct
HTTP call fails locally because no tool takes a URL, and no credential appears
anywhere in the transcript.

The system prompt in `infra/ai-agent/agent.py` also tells the model to treat
content as data. That is a usability measure and **not** a control. Every
security property in this phase is tested with those instructions deliberately
subverted.

---

## What is not verified

Honest gaps, stated plainly:

- **No run against a real Google account has been performed in this
  repository.** Every Gmail test runs against a stand-in
  (`backend/tests/gmail_fake.py`) that answers the way Google answers. The
  stand-in's own docstring says no test using it may be cited as real-Gmail
  verification. Doing the real run needs the human steps above.
- **No run against a real model has been performed.** The agent runtime is
  real; the model in the tests is scripted. Running it for real needs an API
  key.
- **The runtime network boundary is unverified here**, because this environment
  has no Docker daemon. The configuration is checked; the containers were not
  started.
- **No browser verification.** Nothing in this phase was clicked through a real
  browser against a real mailbox.

See `docs/PHASE_19_REPORT.md` for the full labelled list.
