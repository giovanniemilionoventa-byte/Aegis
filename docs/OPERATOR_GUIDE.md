# Running an agent through Aegis

This is the whole job, in the order you actually do it. Everything here was
performed in a browser against a running deployment before it was written down;
the transcript is in `docs/evidence/phase18_operator_walkthrough.json`.

You need no Python, no SQL, no `docker exec`, and no edits to any file in this
repository.

---

## Before you start: what Aegis is, in one paragraph

Your agent does not talk to your CRM. It talks to Aegis, and Aegis decides, on
every request, whether that action is permitted — then performs it on the agent's
behalf and seals the decision into a tamper-evident record. The agent never
holds the credential for the system it is acting on. If you take its authority
away, the next thing it tries fails, whether or not anyone restarts it.

---

## 1. Sign in

Open the dashboard. If you have no account, choose **Create a new
organization**. Your organization is your tenant: agents, policies, approvals
and evidence all belong to it, and no other tenant can see or touch them.

## 2. Look at Posture first

**Posture** tells you what Aegis can currently see about this deployment. Read
it before you trust anything else, because it is deliberately blunt:

- **Live state** is measured now — whether default secrets are in use, whether
  every active agent is governed by a contract, whether the deployment is
  fail-closed.
- **UNKNOWN** means Aegis does not know. Agent liveness is UNKNOWN because
  there is no heartbeat: Aegis knows an agent is *allowed* to run, not that it
  *is* running.
- **ATTENTION** is a real limitation, not a warning to dismiss. Today the one
  that matters is credential isolation from the provider: per-tenant
  credentials are derived from a master key the provider holds, so the provider
  can still derive yours. **CAN USE ≠ CAN READ is not solved.**
- **Proven by runtime experiment** is evidence from recorded runs, not a live
  measurement. If the deployment's topology changes, those runs have to be
  repeated before you may keep believing them.

## 3. Create the agent

**Agents → Add agent.** Four steps.

**Identity.** A name and what it is for. This identity is separate from the
human who owns it and from the model provider behind it.

**What may it do?** Everything starts denied. For each capability you choose
one of three answers:

| You choose | What Aegis does |
|---|---|
| **Allow** | The action runs when requested, and is recorded. |
| **Needs a human** | The action is refused, an approval is raised, and the tool does not run until a person approves that exact request. |
| **Deny** | Refused. |

Two labels in that table are worth reading carefully:

- **executes** — this capability is connected to a real protected tool, so a
  decision to allow it causes something to happen.
- **decision only** — Aegis will rule on the request, but no tool is connected,
  so nothing executes either way. Today only `crm` executes. Granting
  `payments.TRANSFER` does not move money; it produces a decision and a record
  and nothing else. Do not read it as protection of a payment system you have
  not connected.

`irreversible` marks actions that cannot be undone if they do run.

**Review.** The table shows every capability and what you chose. What you
grant here is written into two places — the agent's permissions and its runtime
contract — and they must both agree before an action is allowed.

**Credential.** Shown once. Copy it now; Aegis does not store a form it can
show you again. If you lose it, use **Rotate token** on the agent's page, which
issues a new one and kills the old one immediately.

## 4. Connect your agent

Your agent process needs two things:

    AEGIS_BASE_URL   the enforcement gateway's address
    AEGIS_AGENT_TOKEN the credential you just copied

It needs nothing else. In particular it must not hold your CRM credential —
Aegis holds that, and the agent never sees it. The reference agent in
`infra/reference-agent/` is a working example of the request shape.

The agent must be able to reach the enforcement gateway. It must *not* be able
to reach the credential broker, the protected tool, or this dashboard. In the
supplied Docker deployment that separation is enforced by the network itself,
and the agent's own probe at startup reports what it can and cannot reach.

## 5. Watch it work

Open the agent and press **Run verification**. This queues a job; your agent
claims it over the one network path it has, exercises its authority against the
live gateway, and reports back.

> If no agent process is running, the run stays `PENDING` forever. That is not
> a failure, and the page says so rather than inventing one.

The canonical scenario deliberately tries things that should fail: a forbidden
delete, a payload with denied fields, a mutated request against a granted
approval, a replay of a spent approval, and direct connections to the broker,
the tool and the control plane.

The **Agent self-check** column is the *agent's own account of itself*. It is
useful and it is not authority. The authority is the evidence chain, written by
the gateway while deciding, which the agent cannot reach or alter.

## 6. Approve something

If you granted a **Needs a human** capability, the agent's attempt lands in
**Approvals**. Press **Inspect** before deciding. You will see:

- the **execution** and **request** the approval belongs to,
- the **contract version** it was made under,
- a **parameter digest**,
- when it **expires**.

Approving authorizes *that exact request, once*. A request with different
parameters is not covered by it, and it cannot be spent twice.

You see the digest rather than the payload on purpose: approving must not
become a way to read customer data you are not otherwise entitled to see. The
cost is real — you are confirming *that this is the same request*, not reading
what it contains — and you should know which one you are doing.

Your agent must still be running and still retrying. If nobody approves within
the agent's wait window, the agent gives up and the run reports a failure. That
is the honest outcome, not a bug.

## 7. Read what happened

**Activity** shows every attempt grouped by execution, so a sequence reads as
one story: what was tried, what Aegis decided, whether it executed, and why.

**Evidence** takes any execution and has the server recompute the entire hash
chain from the events themselves. `chain intact` means the record has not been
altered since it was written. Known limit, stated plainly: deleting the tail of
a chain *and* rewriting the stored tip is not detected by the chain alone.

## 8. Check a decision without running anything

**Ask** answers "what would Aegis decide?" using the same permission, policy
and contract engines the gateway uses. It writes nothing and executes nothing.

It does not evaluate trajectory or workflow rules, because those depend on an
execution's history and a hypothetical request has none. The answer is
advisory. The gateway decides for real when an agent actually asks.

## 9. Take authority away

**Revoke agent** on the agent's page. This is not a UI state change:

- the agent's next request to the gateway returns `401 Invalid or revoked agent token`,
- that applies to tool calls, not only to the job channel,
- it takes effect on the next request; nobody has to restart anything.

This was verified against a live, running agent: it was mid-poll when the
button was pressed, and it was refused from that moment on.

**Revoke contract** is the narrower tool. It removes the agent's authority to
act while leaving the identity in place. With a fail-closed deployment
(`AEGIS_REQUIRE_RUNTIME_CONTRACT`), an agent with no active contract is refused
everything.

---

## What this does not give you

- **No heartbeat.** Aegis does not know whether your agent is running. "active"
  is configuration, not connectivity.
- **One connected tool.** Only `crm` executes. Everything else is decision-only.
- **A mock behind the gateway.** The security path is real. The system it
  protects, in this deployment, is not.
- **The provider can still derive your credentials.** See Posture.
- **No customer has used this.** Nothing here is customer-proven.
