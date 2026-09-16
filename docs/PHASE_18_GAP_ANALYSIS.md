# Phase 18 — Frontend/Backend Gap Analysis

Produced before any implementation, against `02841e0` with the stack running.
Every "existing API" row was read from the live route table (36 control-plane
routes), and every "frontend support" row from the actual page sources.

## 0. The constraint that shapes this phase

Measured, not assumed:

```
control-plane networks : aegis_public_net
gateway networks       : aegis_agent_net, aegis_broker_net
control-plane -> gateway : DNS_BLOCK  [Errno -2] Name or service not known
```

**The control plane cannot reach the enforcement gateway.** Neither can the
browser: the gateway publishes no host port (Phase 17 §3.5), and the frontend's
dev proxy points at `127.0.0.1:8000`, which is the control plane — a role that
does not mount the gateway routers at all.

Three consequences drive every design decision below.

1. **The existing Playground page is broken under the compose topology.** It
   POSTs to `/api/gateway/tools/{tool}/{operation}`, which 404s on the control
   plane. It only ever worked against a single-process `AEGIS_ROLE=all` dev
   server. This is a real defect, not a missing feature.

2. **No UI action can directly cause an agent action.** That is the deployment
   boundary doing its job, and it must not be "fixed". Proxying agent calls
   through the control plane would hand the control plane a route into
   `agent_net` and make the frontend part of the security boundary — exactly
   what §19 forbids.

3. **Therefore UI-triggered runtime verification has to ride the one permitted
   path: agent → gateway.** The agent asks for work; nothing pushes work at it.
   Control plane and gateway already share the SQLite volume, so the control
   plane can record a request and the gateway can serve it, without any new
   network edge.

## 1. Gap table

Legend: **API** = a backend endpoint exists. **UI** = a frontend page uses it.

| # | Area | Backend capability | API | UI | Missing user workflow |
|---|---|---|---|---|---|
| A | Organization / tenant | org per user, scoping enforced server-side everywhere | via `auth/me`, implicit in every route | shown in sidebar only | No tenant context view. Acceptable: single org per operator, and the backend never trusts a client-supplied org id. **No new concept needed.** |
| B | Agents | create, list, get, revoke, rotate | yes | `Agents.tsx` — list + create + revoke + inline permissions | No agent **detail** page. Cannot see an agent's contract, credential expiry, or activity in one place. Creation is a bare form, not an onboarding flow. |
| C | Agent authentication | opaque token, SHA-256 hashed, expires (Phase 17) | `POST /agents`, `POST /agents/{id}/rotate` return the token once | token shown in a box | Expiry is returned but never displayed. No "shown once" affordance. No rotate button. |
| D | Runtime Contracts | full lifecycle, mandatory, fail-closed | 6 routes | `Contracts.tsx` — read + revoke | **Cannot create a contract from the UI.** The one control the product leads with can only be authored by API. This is the single largest gap. |
| E | Capabilities | `capabilities[]`, `resources[]` inside the contract | part of the contract document | rendered as raw-ish text | No capability editor. No ALLOW / APPROVAL / DENY mental model surfaced. |
| F | Resources / destinations | `resources[]`, `constraints.destination_restrictions`, `data_constraints` | part of the contract document | constraint *names* listed only | Not configurable; not explained. |
| G | Policy | CRUD + toggle, decides ALLOW/APPROVAL/BLOCK | yes | `Policies.tsx` — create + toggle | Adequate. Policy and contract are two different things and the UI does not explain the difference. |
| H | Approval | bound, single-use, expiring, atomically claimed | list + decide | `Approvals.tsx` — 40 lines, status + allow/block | Does not show what the human is actually approving: no payload digest, no contract version, no expiry, no consumed state. An operator approves blind. |
| I | Evidence / audit | HMAC chain + verifier endpoint | `GET /executions/{id}/evidence` | `Evidence.tsx` (added 02841e0) | Good. Needs honest integrity labelling and a link from activity. |
| J | Runtime executions | `GET /executions`, `GET /events` | yes | `Events.tsx` (36 lines, flat list) | No execution-centric activity view; cannot follow one execution's story. No link to evidence. |
| K | Security posture | `GET /health` returns real posture (weak secrets, role) | yes, but only `/api/health` | **none** | No posture page. Phase 17 added `posture` to health and nothing displays it. |
| L | Revocation | agent revoke + contract revoke, both real | yes | agent revoke in `Agents.tsx`, contract revoke in `Contracts.tsx` | Works, but the operator cannot *see* that revocation changed runtime behaviour. No before/after. |
| M | Reference Agent | runs in the agent container; driven by a host script | **none** | none | Cannot be started from the UI. See §0.3 — needs a job channel over agent → gateway. |
| N | Agent connectivity | not modelled | none | none | There is no liveness signal. Must be labelled configuration state, not connectivity. |

## 2. What this phase will build

Ordered by whether it unblocks the others.

1. **Agent job channel** (enables M, and the whole canonical demo)
   `VerificationRun` row; control plane requests a run; the agent *claims* it
   from the gateway using its own token and posts results back. No new network
   edge, no push, no authority granted — the job names a scenario, it does not
   carry code, and every action the agent then takes is authorized normally.

2. **Contract authoring UI** (D, E, F) — the largest product gap.

3. **Agent detail page** (B, C, L, N) — one place showing real state.

4. **Activity view** (J) — execution-centric, linked to evidence.

5. **Approval detail** (H) — show the binding the human is actually granting.

6. **Posture page** (K) — from `/api/health` and real counts, not icons.

7. **Fix Playground** (defect found above).

## 3. What this phase will NOT build

Per §23, and because nothing in the repository needs them: no marketplace, no
analytics platform, no generic IAM, no SSO, no billing, no design-system
rewrite, no second agent implementation.

Also explicitly **not**: a control-plane → gateway proxy. It would make the
frontend part of the security boundary and undo the Phase 17 boundary proof.

## 4. Concepts deliberately reused, not duplicated

`Agent`, `Credential`, `Permission`, `RuntimeContract`, `Policy`, `Approval`,
`Event`, `Execution` all already exist and are authoritative. This phase adds
exactly one new model — `VerificationRun` — and only because there is no
existing way to express "the operator asked for a verification run".

## 5. Security invariants this phase must not break

- The frontend is never the boundary; every check stays server-side.
- The browser's `organization_id` and `agent_id` are attacker-controlled input.
- The agent still receives only its own token — never a tool credential, EAT
  key, or internal service token.
- `agent → broker / tool / control-plane / DB` stays network-blocked.
- The new gateway endpoints must not grant authority, and must scope every job
  to the agent identified by the presented token.
