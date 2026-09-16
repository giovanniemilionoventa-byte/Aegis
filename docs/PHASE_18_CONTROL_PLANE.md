# Phase 18 — The User-Facing Control Plane

Checkpoint: `be57881` (Phase 18 job channel). Prior phase: `docs/PHASE_17_RUNTIME_PROOF.md`.

Phase 17 proved the runtime. It left a product that only its author could
operate: every capability it proved was reachable through `curl`, `sqlite3` and
`docker exec`, and through nothing else. Phase 18 closes that gap without
moving the security boundary.

The rule this phase was built against, and the one worth checking every claim
below against:

> **The frontend is not the security boundary.** Every page calls the same API
> an attacker calls. Nothing in the browser decides anything.

## 1. Executive summary

| Capability | Before Phase 18 | After |
| --- | --- | --- |
| Create and configure an agent | `curl` against four endpoints, in the right order | A four-step wizard, **RUNTIME-PROVEN** end to end |
| Grant capabilities | Hand-written JSON for permission + contract | Three choices per capability, written to both |
| `approval_rules` in a contract | Stored since Phase 11, **never read by anything** | Read by the engine; raise ALLOW to APPROVAL |
| Approve an action | `POST /api/approvals/{id}/decide`, blind | Execution, request, contract version, digest and expiry shown first |
| See what an agent did | `SELECT * FROM events` | Activity, grouped by execution |
| Verify evidence | An endpoint with no caller | A button; the server recomputes the chain |
| Run an agent against the runtime | `docker exec` into a container | **Run verification**, over the one permitted path |
| Revoke | `UPDATE agents SET status` | A button, **verified against a live running agent** |
| Check a decision safely | The Playground, which called an endpoint it could not reach | A simulator reusing the real engines |
| Deployment posture | Four phase reports | One page, separating live state from recorded evidence |

Backend suite: **586 passed, 0 skipped** (was 537 at the end of Phase 17).

Live attacks on the new API surface, browser not involved: **28 run, 28 held**
(`docs/evidence/phase18_api_attacks.json`).

Operator walkthrough in a real browser, by a new operator on a new tenant,
ending in a **12/12 PASS** verification run by a real agent process that held
nothing but the credential the wizard displayed
(`docs/evidence/phase18_operator_walkthrough.json`).

## 2. The constraint that shaped the design

From `docs/PHASE_18_GAP_ANALYSIS.md`, measured, not assumed:

```
control-plane networks : aegis_public_net
gateway networks       : aegis_agent_net, aegis_broker_net
control-plane -> gateway : DNS_BLOCK  [Errno -2] Name or service not known
```

The dashboard cannot reach the enforcement gateway. Neither can the browser.
This is the deployment boundary working, and it is what stops a compromised
dashboard from driving an agent.

It was tempting to "fix" it — put the gateway on the public network, or proxy
gateway calls through the control plane. Both would have dismantled the one
property Phase 17 spent its whole length proving. So the design rides the only
path that exists instead: the operator **queues** a job on the control plane,
and the agent **claims** it from the gateway, over the network it already has.

Two consequences are permanent and are stated in the product, not hidden:

1. No UI action can directly cause an agent action. The UI can only ask.
2. If no agent is running, a queued run stays `PENDING` forever. The page says
   that instead of inventing a failure.

## 3. What was built

### 3.1 `approval_rules` became real

`approval_rules` had been stored in contracts since Phase 11 and **read by
nothing**. A contract could say "customer record changes need a human" and the
runtime would allow them silently.

`contract_engine.requires_human()` now resolves them, and the enforcement path
applies one rule about direction:

```python
elif verdict.requires_approval and decision == "ALLOW":
    # The contract can raise an ALLOW to APPROVAL, never the reverse.
```

A contract can add a human. It can never remove one that policy or permission
demanded.

### 3.2 One copy of the contract-resolution wording

The simulator and the enforcement path explained the same BLOCK in two
different ways, because each had its own copy of the reason strings. They had
already drifted. `contract_engine.resolution_reason()` is now the only copy, and
both call it. An operator who checks a decision and then sees it happen for real
gets the same sentence.

### 3.3 The capability catalogue is honest about what executes

`/api/capabilities` is built from the real sources — the gateway's tool map, the
organization's resources, and the irreversible-action list — and it marks
`enforceable` **true only for `crm`**, because `crm` is the only kind wired to a
protected tool.

The wizard shows that distinction as **executes** vs **decision only**. Granting
`payments.TRANSFER` produces a decision and a record and moves no money. Saying
so in the UI costs a demo its shine and is the difference between a product and
a claim.

### 3.4 The simulator replaced a page that could not work

The old Playground offered "send through the enforcement gateway". That could
never work from a browser, and worse, `/api/authorize` is itself a gateway
route: the 404 it produced was rendered as if it were a policy result.

`POST /api/agents/{id}/simulate` evaluates on the control plane using
`permission_engine`, `policy_engine` and the contract engine directly — no
second implementation of the rules. It writes nothing, and it returns
`models_trajectory: false` and `advisory: true`, because trajectory and workflow
rules depend on an execution's history that a hypothetical request does not have.

## 4. The walkthrough, performed not described

Driven with Chromium against the built frontend, clicking what a person clicks.

1. New operator, no account → **Create a new organization**.
2. **Posture** on an empty tenant: 3 OK, 1 UNKNOWN (no heartbeat), 1 ATTENTION
   (the provider can still derive tenant credentials).
3. **Add agent** → `crm.READ` = Allow, `crm.UPDATE` = Needs a human, everything
   else Deny.
4. Credential shown once, copied.
5. A container started on `aegis_agent_net` holding **only that credential**.
6. **Run verification** → the agent claimed the job from the gateway.
7. The approval appeared in **Approvals** with execution, request id, contract
   version `support-copilot-contract v1`, parameter digest and expiry.
8. Approved in the UI → the agent's next unchanged attempt executed, once.
9. **12/12 PASS**, including: forbidden delete blocked, denied payload fields
   blocked, mutated request rejected against the granted approval, approval
   replay refused, and the broker, tool and control plane all unreachable.
10. **Activity** grouped the sequence into one story; **Evidence** recomputed the
    chain and reported it intact.
11. **Revoke agent** → the live container's very next poll: `401 Invalid or
    revoked agent token`. A direct `crm.read` and `crm.update` with that
    credential: `401` both.

### 4.1 The two runs that failed, and why they are kept

Runs `ea65ad1d` and `c32dfd83` report **11/12**. The failing check is *"approved
action executes after a human grants it"*, and in both cases the cause was on my
side of the glass: the first time the driver crashed before approving, the second
time it approved the *previous* run's still-pending request instead of the new
one.

Aegis behaved correctly both times — the unapproved action did not execute, and
the run reported `FAIL` rather than quietly passing. Both are kept in the
evidence file. A walkthrough that shows only the clean run is a demo, not
evidence.

They also surfaced something real for §7: a stale pending approval from an
abandoned run sits in the queue looking exactly like a live one.

## 5. Attacking the new surface

28 attacks, hand-built, browser not involved: control-plane attacks from the
host, gateway attacks from a throwaway container on `aegis_agent_net`, using two
real tenants created through the product. All 28 held.

| Attack | Result |
| --- | --- |
| Read / revoke / rotate / simulate / queue a job on another tenant's agent | `404` on all five |
| Approve another tenant's approval | `404` |
| Unauthenticated access to agents, approvals, events, capabilities, runs, scenarios | `401` |
| An agent credential used as an operator session | `403`, refused before routing |
| An operator session used as an agent credential | `401` at the gateway |
| Forged `organization_id` + `agent_id` in a contract body | Ignored; bound to the caller's own agent and org |
| Path traversal, SQL injection and a null UUID as an agent id | `404`, never a `500` |
| Another agent claims a job queued for the victim | `{"run": null}` |
| Forge a `PASS` verdict on another agent's run | `404` |
| Control-plane routes on the gateway; operator login on the gateway | `404` / `403` |

Two of these were recorded as failures on the first pass and both were **my
harness being wrong**, which is worth saying plainly:

- `GET /api/agents/{id}/verification-runs` returned `405`. That route is
  POST-only; listing is `/api/verification-runs`. I had tested a method that
  does not exist and called its absence a hole.
- An agent token on the control plane returned `403`, not the `401` I expected.
  `403` with an explicit refusal is the better answer, not a weaker one.

A third was worse: a **false pass**. The forged-ownership test returned `409`
because the agent already had a contract, so the request was rejected before the
ownership fields were ever examined — the test passed without testing anything.
It now creates a fresh agent first, and the observation is conclusive: the
forged `agent_id` was ignored and the contract bound to the caller's own agent
(`0aaf231f`, not the victim's `82cfcd92`), under the caller's own organization.

Not covered: denial of service and rate limiting; browser-side attacks (XSS,
CSRF, token storage). These are point-in-time observations of one deployment.

## 6. Capability classification

Nothing is promoted beyond its evidence.

| Capability | Classification |
| --- | --- |
| Agent onboarding, capability grant, contract creation through the UI | **RUNTIME-PROVEN** |
| Contract `approval_rules` raising ALLOW to APPROVAL | **RUNTIME-PROVEN** |
| Human approval bound to one request, single use, expiring | **RUNTIME-PROVEN** |
| Revocation taking effect at the gateway on a live agent | **RUNTIME-PROVEN** |
| Evidence chain verification from the UI | **RUNTIME-PROVEN** |
| Execution boundary (agent cannot reach broker, tool, control plane) | **RUNTIME-PROVEN** (Phase 17, re-observed here) |
| Multi-tenant isolation of the Phase 18 API surface | **RUNTIME-PROVEN** (28/28) |
| Decision simulation | **TESTED** — advisory by construction; no trajectory or workflow |
| Capability catalogue beyond `crm` | **IMPLEMENTED** — decision only; nothing executes |
| Deployment under a real orchestrator, TLS, real identity provider | **NOT PROVEN** |
| Any customer use | **NOT PROVEN.** Nothing is customer-proven or commercially-proven |

## 7. What is still wrong, or missing

1. **No heartbeat.** "Active" is configuration. Aegis does not know whether an
   agent is running, and the UI says so on every page that could mislead.
2. **Stale approvals cannot be distinguished by Aegis, only flagged.** An
   approval outlives the attempt that raised it: agents wait a bounded time for
   a human and then give up, but the grant stays valid until it expires. Found
   during §4.1, where the driver approved an abandoned run's request believing
   it was releasing the one in flight.

   The queue now shows each request's **execution** and **age**, and marks rows
   older than three minutes *"agent may have stopped waiting"*. That is a
   judgement aid and nothing more. Aegis genuinely cannot tell the two cases
   apart — it has no heartbeat, and re-submissions of an already-pending request
   write no new events — so the honest fix was to show the operator what it does
   know and name the question they are actually being asked. Approving a stale
   request was never dangerous in itself: the grant is bound to one request and
   single-use. The risk is the operator believing they released something they
   did not.
3. **One connected tool.** Only `crm` executes. Everything else is decision-only.
4. **The protected tool is a mock.**
5. **The provider can still derive every tenant's credential.** CAN USE ≠ CAN
   READ remains unsolved. It is on Posture as ATTENTION, not buried.
6. **Evidence truncation.** Deleting the tail *and* rewriting the stored tip is
   still not detected by the chain alone (Phase 14).
7. **The credential is shown in the browser.** It has to be — the operator must
   copy it. It is displayed once, never re-fetchable, and not persisted in
   frontend state beyond the page. That is mitigation, not elimination.
8. **No frontend tests.** The gate is `tsc --noEmit` plus a production build.
   The walkthrough in §4 was performed by hand-driven browser, not by a suite
   that will run again tomorrow.
9. **Performance numbers are stale.** They were measured with the contract
   engine in pass-through and do not describe this system.

## 8. Where Aegis actually is today

A working, runtime-verified authorization layer with a control plane a
competent non-developer can operate end to end, protecting one mock tool, in one
Docker deployment, used by nobody.

The security model is real and has been attacked. The product around it is a
prototype, and the distance between those two sentences is the honest position.
