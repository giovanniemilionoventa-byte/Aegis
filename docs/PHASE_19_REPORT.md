# Phase 19 — report

Labels used throughout: **IMPLEMENTED**, **VERIFIED**, **PARTIAL**,
**NOT VERIFIED**, **LIMITATION**.

"VERIFIED" here means a reproducible test asserts the property in this
repository. It does not mean the property was observed against a real Google
account or a running Docker stack unless the row says so — and no row does,
because neither was available in this environment.

---

## 1. What Phase 19 built

| Area | Status | Where |
|---|---|---|
| Typed Gmail connector (5 operations, no generic proxy) | IMPLEMENTED | `backend/app/protected/gmail.py` |
| Google OAuth flow, operator-driven | IMPLEMENTED | `backend/app/routers/gmail.py` |
| Sealed server-side credential store | IMPLEMENTED | `backend/app/gmail_store.py`, `backend/app/secretbox.py` |
| Canonical Gmail policy on the existing engines | IMPLEMENTED | `backend/app/seed.py`, gateway `TOOL_MAP` |
| Real external AI agent | IMPLEMENTED | `infra/ai-agent/agent.py`, `infra/ai-agent/tools.py` |
| Egress proxy with host allow-list | IMPLEMENTED | `infra/egress-proxy/proxy.py` |
| Connector-call evidence (said vs. done) | IMPLEMENTED | `backend/app/services/connector_evidence.py`, `models.ConnectorCall` |
| Minimal dashboard surface | IMPLEMENTED | `frontend/src/pages/Gmail.tsx`, `Evidence.tsx` |
| Cost observations | IMPLEMENTED | `scripts/phase19/measure.py` |

**No second authorization engine was built.** Gmail requests are ruled on by
the permission engine, policy engine and runtime contract that Phases 10–18
built. The gateway's `TOOL_MAP` gained one entry.

---

## 2. Security properties actually demonstrated

Each row is backed by tests that assert a **side effect did not happen**, read
from the Gmail stand-in's ledger — not by a response that returned the right
word.

| Property | Status | Evidence |
|---|---|---|
| `gmail.search/read/draft` execute; `send` requires a human; `delete` is denied | VERIFIED | `test_phase19_policy.py` (11) |
| `delete` is refused independently by permissions, by policy, and by contract — each proven with the layer above removed | VERIFIED | `test_phase19_policy.py` |
| An APPROVAL_REQUIRED request sends nothing until approved | VERIFIED | `test_phase19_policy.py`, `test_phase19_adversarial.py` |
| Approving does not itself execute; the agent must re-submit | VERIFIED | `test_phase19_policy.py` |
| An approval is single-use and cannot be replayed | VERIFIED | `test_phase19_policy.py`, `test_phase19_adversarial.py` |
| An approval does not authorize a different payload, destination or execution | VERIFIED | `test_phase19_policy.py`, `test_phase19_adversarial.py` |
| An expired approval does not execute; an expired request cannot be approved | VERIFIED | `test_phase19_adversarial.py` |
| Aegis rules on the typed operation sent, never on the agent's account of it | VERIFIED | `test_phase19_adversarial.py`, `test_phase19_evidence.py` |
| Client-supplied `org_id` / `tenant_id` / `agent_id` / `user_id` are not authority | VERIFIED | `test_phase19_adversarial.py` (6 parametrised cases + a borrowed-permission case) |
| One tenant cannot read another tenant's mailbox | VERIFIED | `test_phase19_adversarial.py` (separate mailboxes, separate credentials) |
| Another tenant's connector credential is refused | VERIFIED | `test_phase19_adversarial.py` |
| Revocation stops the next action on the same live credential, no restart | VERIFIED | `test_phase19_revocation.py` |
| An approval granted before revocation does not survive it | VERIFIED | `test_phase19_revocation.py` |
| The connector exposes no generic operation, even to a correctly authenticated caller | VERIFIED | `test_phase19_adversarial.py` |
| No field in any request becomes a Gmail URL | VERIFIED | `test_phase19_adversarial.py` |
| A fully compromised agent obeying an injected email achieves nothing | VERIFIED | `test_phase19_prompt_injection.py` |
| Mail content is never an authorization source | VERIFIED | `test_phase19_prompt_injection.py` |
| No API response contains a Google credential | VERIFIED | `test_phase19_credential_isolation.py` |
| The refresh token is sealed, tenant-bound, and unusable if moved or tampered with | VERIFIED | `test_phase19_credential_isolation.py` |
| Access tokens are never persisted | VERIFIED | `test_phase19_credential_isolation.py` |
| The gateway never receives the Google credential | VERIFIED | `test_phase19_credential_isolation.py` |
| Exactly one code path can produce a refresh token | VERIFIED | `test_phase19_credential_isolation.py` |
| The sealed event chain still verifies after Gmail activity | VERIFIED | `test_phase19_evidence.py` |
| Evidence holds no message subject, body, address, or credential | VERIFIED | `test_phase19_evidence.py` |
| Deployment config gives the agent no route to Gmail or to the connector | VERIFIED (configuration check) | `test_phase19_network.py` |
| The egress allow-list refuses every Google host, including lookalikes | VERIFIED (unit check) | `test_phase19_network.py` |
| The sealed credential volume is mounted by exactly two containers | VERIFIED (configuration check) | `test_phase19_network.py` |
| The agent container holds no Google or internal secret | VERIFIED (configuration check) | `test_phase19_network.py` |

---

## 3. Security properties NOT demonstrated

Stated plainly, because a claim without evidence is worse than no claim.

| Property | Status | Why |
|---|---|---|
| Real Gmail side effects (a real message really sent, really drafted) | **NOT VERIFIED** | No Google account is configured here. Every Gmail test runs against `backend/tests/gmail_fake.py`. Needs the human steps in `PHASE_19_GMAIL.md`. |
| The real OAuth consent flow end to end | **NOT VERIFIED** | Same. The code path exists and is exercised, the Google side is not. |
| A real model deciding what to do | **NOT VERIFIED** | The agent runtime is real; the model in tests is scripted. Needs an API key. |
| Runtime network isolation between containers | **NOT VERIFIED** | No Docker daemon in this environment. The compose configuration is checked; the containers were never started. The 47 skipped tests are exactly these. |
| The egress proxy refusing a real CONNECT | **NOT VERIFIED** | The host matcher is unit-tested; the proxy process was not run against live traffic. |
| Browser verification of any of the above | **NOT VERIFIED** | Nothing was clicked through a real browser. |
| "Non-bypassable", "fully isolated", "production ready" | **NOT CLAIMED** | Nothing here establishes these, and they are not asserted anywhere in the repository. |

---

## 4. Test results

Run with `cd backend && python3 -m pytest -q`:

```
662 passed, 47 skipped
```

Baseline before Phase 19 was **539 passed, 47 skipped**. Phase 19 adds **123
tests**, and the skip count is unchanged — no Phase 19 test skips.

| File | Tests |
|---|---|
| `test_phase19_adversarial.py` | 54 |
| `test_phase19_network.py` | 22 |
| `test_phase19_credential_isolation.py` | 16 |
| `test_phase19_policy.py` | 11 |
| `test_phase19_evidence.py` | 10 |
| `test_phase19_prompt_injection.py` | 5 |
| `test_phase19_revocation.py` | 5 |

Frontend: `tsc --noEmit` clean, `vite build` succeeds. There is no frontend
test suite in this repository.

**All 47 skips are pre-existing** and carry the message
`RUNTIME VERIFICATION: NOT VERIFIED — no Docker daemon` or `the Aegis stack is
not running`. None were added, and no existing test was deleted or weakened.

### Tests changed, and why

Three pre-existing tests were modified. None of them changed what is asserted.

1. `test_eat_binding.py` (2 tests) and `test_phase13f_fail_closed.py` (1 test)
   build `SimpleNamespace` / local-class stubs of `AuthorizationOutcome` and
   `Event`. Phase 19 records `event.id` and `outcome.approval_granted` when it
   writes the connector-call evidence row. Both fields exist on the real
   objects — `Event.id` is the primary key, `approval_granted` has been on the
   dataclass since Phase 17 — so the stubs were simply incomplete. Fields were
   added; every assertion is unchanged.

2. In `test_phase19_adversarial.py`, two tests written during this phase were
   **passing for the wrong reason** and were fixed before being committed as
   evidence of anything. `INTERNAL_TOOL_TOKEN` is empty in the test
   environment, so every request to the connector's internal API was refused
   for want of a configured internal token — the per-tenant credential check
   never ran. The fixture now sets the token, and a new test establishes the
   baseline first (no token → 401, wrong token → 401, right token and right
   credential → 200) so the refusals below it mean something.

### A pre-existing test-isolation defect, noted but not fixed

`test_phase17_contract_api.py::test_operator_cannot_write_into_another_tenant`
registers a fixed email address and fails on a **second consecutive run**
against the same SQLite file, because `backend/aegis.db` persists between runs.
It passes from a clean state. This predates Phase 19 and is out of its scope;
it is recorded here rather than silently worked around. Run `rm -f
backend/aegis.db` before the suite, or give the run its own database.

---

## 5. Cost observations

From `scripts/phase19/measure.py`, 40 iterations per operation, in-process
against the stand-in on SQLite. Full output:
`docs/evidence/phase19_cost_observations.json`.

| Observation | Value |
|---|---|
| Aegis authorization overhead, median | ~17 ms per request |
| Gmail API requests for a denied `gmail.delete` | **0** |
| Gmail API requests for an unapproved `gmail.send` | **0** |
| Gmail API requests for `gmail.read` / `gmail.draft` | 1 |
| Gmail API requests for `gmail.search` | 1 list + 1 per **result returned** |
| Evidence rows per operation | 1 event + 1 connector-call row |

The two zeroes are the interesting numbers: they are quantitative evidence that
a DENY and an unapproved APPROVAL cost the protected service nothing at all,
because nothing reaches it.

`gmail.search` scaling with results returned (not with `max_results`) is the
one obvious cost driver. A query matching twenty messages costs twenty-one
Gmail requests. The connector caps results at 25, so the ceiling is 26. If this
ever matters, batch it.

**LIMITATION.** The ~17 ms figure is this environment, this process, SQLite,
no network. It is not a production latency figure and must not be quoted as
one. Real Gmail latency, model latency, token cost and approval latency are all
**NOT MEASURED**, and the JSON says so in the file itself.

---

## 6. Known limitations

1. **The credential seal is not a KMS.** `AEGIS_OAUTH_ENCRYPTION_KEY` is an
   environment variable, so anyone who can read the process environment can
   read the refresh tokens. It protects a leaked file, not a compromised host.
   No rotation, no envelope encryption. `secretbox.py` says this in its own
   docstring.
2. **`secretbox.py` is hand-built from HMAC-SHA256** (encrypt-then-MAC, CTR-mode
   keystream) because the `cryptography` package's native backend is unusable
   in this environment (`_cffi_backend` missing). The construction is standard
   and the primitive is from the standard library, but AES-GCM from a
   maintained library would be preferable where it is installable.
3. **The egress proxy checks the CONNECT host, not the TLS SNI.** A client that
   sent an allowed CONNECT host and a different SNI would defeat the proxy on
   its own. The deployment's answer is that the proxy is not the only control —
   the agent has no route to Google at all — but the proxy should not be
   described as sufficient by itself. Its docstring says so.
4. **`gmail.modify` is broader than what Aegis does.** Google's consent screen
   will say "permanently delete", which is its wording for this scope. Aegis
   denies `gmail.delete` and does not request
   `https://mail.google.com/`, but a user reading the consent screen is being
   asked for more than Aegis uses.
5. **Disconnecting does not revoke at Google.** It deletes Aegis's copy. The
   API response and the UI both say so explicitly.
6. **The agent's approval wait is a poll, bounded by a timeout.** An abandoned
   request stays approvable until its grant expires — the Phase 18 caveat about
   stale approvals in the queue still applies.
7. **One mailbox per tenant.** The store is keyed by organization.
8. **The connector's `delete` method exists** although policy denies the
   operation. It is unreachable in the canonical deployment — the tests prove
   that by counting connector entries, not by trusting the claim — and it uses
   trash rather than permanent deletion.

---

## 7. Blockers

| Blocker | Class | What unblocks it |
|---|---|---|
| No real Gmail verification | **GOOGLE/OAUTH HUMAN ACTION** | A person completes the two steps in `PHASE_19_GMAIL.md` §1–2. Nothing can be automated here: it requires logging in as a human and consenting. |
| No real model run | **ENVIRONMENT BLOCKER** | An API key for any OpenAI-compatible provider in `.env`. |
| No runtime network proof, no browser proof | **ENVIRONMENT BLOCKER** | A Docker daemon. This environment has none; `docker compose version` works, `docker version` cannot reach a daemon. |
| Test isolation defect in a Phase 17 test | **TESTING BLOCKER (pre-existing, low)** | Give the suite a per-run database, or `rm -f backend/aegis.db` first. |

No **CODE BLOCKER** and no **SECURITY BLOCKER** is outstanding.

---

## 8. Recommended next phase

In priority order:

1. **Do the real run.** Everything needed is in place; it is gated on the two
   human steps and a Docker host. That single run converts most of the
   NOT VERIFIED rows in §3 into VERIFIED, and it is the only way to find out
   what Google does that the stand-in does not.
2. **Run the boundary proof against live containers**, including a real CONNECT
   to `gmail.googleapis.com` from inside the agent container, captured as
   evidence alongside the Phase 17 boundary document.
3. **Move the refresh tokens into a real secret store**, which is the honest
   fix for limitation 1 and the same gap Phase 17 recorded for tool
   credentials: the provider can still derive or read every tenant's
   credential.
4. **Decide what a second real service costs.** Gmail took one `TOOL_MAP`
   entry, one connector, one store and one policy block. Whether that shape
   generalises is not yet known, and finding out with a second service is
   cheaper than guessing.
