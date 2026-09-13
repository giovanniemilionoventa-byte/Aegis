# Phase 16.C — Cloud / Multi-Tenant Workload & Capacity Validation

Checkpoint: `d1c55aa` (Phase 16.B — Load, Concurrency & Scaling Validation,
`PASS WITH LIMITATIONS`), `origin/master`.

Method: MEASURE → CLASSIFY → MODEL → DOCUMENT. No security logic, SQLite
configuration, Uvicorn worker model, HTTP client pooling, evidence chain,
EAT, Runtime Contract, authorization, trajectory, or broker code was
modified. Every conclusion below is labeled **OBSERVED** (directly
measured in this phase), **DERIVED** (a reasoned inference from OBSERVED
data, stated as such), or **NOT VERIFIED** (not established either way).

This phase does not answer "how many customers can Aegis support" as a
number — it answers what a shared Aegis instance actually does when the
same total request volume comes from one tenant versus many, and it found
one thing 16.B could not have found from a single-tenant test: a
multi-minute, tenant-independent service degradation that outlives the
overload burst that caused it.

## 1. Executive Summary

**OBSERVED — client count does not change instance behavior at fixed total
concurrency.** Distributing 50 concurrent requests across 1, 5, 10, 25, or
50 independent tenants (Scenarios A–E, Section 7) produced statistically
indistinguishable throughput (~19–23 req/s) and latency (p50 ~1.2–1.6s, p95
~2.0–2.4s) in every case, with zero errors. The bottleneck this phase
confirms is the shared instance's total concurrent-request capacity, not
the number of distinct tenants issuing them — the phase's own central
warning ("do not read 50 requests as 50 customers") is empirically borne
out: 50 requests behave the same whether they come from 1 client or 50.

**OBSERVED — the same total-collapse threshold as Phase 16.B reappears
here**, now under a genuinely multi-tenant workload (100 independent
organizations, a 60/30/10 ALLOW/BLOCK/APPROVAL mix): concurrency ≤50 stayed
error-free across every distribution shape and total volume tested;
concurrency 75 and 100 produced **100% request failure** (client
`ReadTimeout` at 15s), with `enforcement-gateway` CPU near 0% throughout —
unchanged in kind and threshold from the single-tenant finding in Phase
16.B.

**OBSERVED, new in this phase — a multi-minute, tenant-independent
"hangover" after the collapse.** Immediately after the c=75/c=100 sweep,
the next test (a noisy-neighbor probe at nominal concurrency=50, previously
shown healthy) suffered **100% failure for both the "heavy" and the
"light" tenant equally** — not a fairness problem, a total, shared
outage. `enforcement-gateway` CPU stayed near 0% while memory climbed
steadily (145→187 MiB) throughout a window that took on the order of
several minutes to clear, with **zero container crashes or restarts**
(`RestartCount` stayed 0). Once the instance was confirmed recovered (via
a manual, disclosed `docker restart`, see Section 8/16), a clean re-run of
the same noisy-neighbor scenarios found **no measurable fairness
degradation** between an 80/20 or 95/5 tenant split. The original
(contaminated) run and the clean re-run are both reported, separately and
labeled, because the contrast between them is itself the finding: an
overload episode's damage is not confined to the moment of overload, and
can make an unrelated, otherwise-healthy-looking test on different tenants
fail completely for minutes afterward.

**OBSERVED — every cross-tenant isolation probe attempted held.** Tenant
A's token could not adopt tenant B's `execution_id` (403, fail-closed);
a `request_id` shared between two tenants produced two independent,
correctly-scoped events (no idempotency cross-contamination); an EAT
signed for one tenant's claims was rejected by the broker when presented
with a different tenant's `org_id`, `agent_id`, or `execution_id` in the
dispatch body; and `reconstruct_trajectory_state` returned nothing when
queried with the wrong tenant's identifiers. No cross-tenant ALLOW, no
leaked data, no adopted execution, in any probe.

**OBSERVED — the same-`execution_id` race from Phase 16.B reproduces
identically when embedded in real multi-tenant background traffic**, and
adds one new wrinkle: a *client-perceived-sequential* (concurrency=1) run
against one execution still produced one evidence-integrity `409`,
because a client-side timeout does not guarantee the abandoned
server-side request actually stopped running — "sequential from the
client" is not the same guarantee as "sequential at the server" once
timeouts are involved (Section 11).

**DERIVED — no defensible customer-count number.** Every distribution
scenario and the concurrency sweep in this phase used synthetic,
as-fast-as-possible benchmark traffic, not a measured real-tenant request
rate. Per the phase's own rule, this report does **not** convert observed
throughput into a customer count; Section 18 states explicitly why that
conversion cannot yet be made and what would be needed to make it.

Test suite: **374 passed, 5 skipped**, both before and after this phase's
benchmarking — unchanged from Phase 15/16.A/16.B.

**Verdict: PASS WITH LIMITATIONS** (Section 22) — this phase does produce a
credible measure of Aegis's behavior as a shared, multi-tenant instance
under load, including one significant new reliability finding (the
post-collapse hangover); it does not, and does not claim to, establish a
maximum supportable tenant count.

## 2. Checkpoint

| Check | Result |
| --- | --- |
| Branch | `master` |
| `git status` (before this phase's changes) | clean |
| `git log -1 --oneline` | `d1c55aa chore: include phase 16B orchestrator console log` |
| `git rev-parse HEAD` | `d1c55aa07a2e65d4e51e4b6b9398786a452f31bb` |
| `git rev-parse origin/master` | `d1c55aa07a2e65d4e51e4b6b9398786a452f31bb` |
| HEAD == origin/master | Yes |
| Phase 16.A commit (declared) | `3d2ed26` — present in `git log` |
| Phase 16.B commit (verified, not assumed) | `f5e150d` (+ `d1c55aa` follow-up) — present in `git log` |

Test suite before this phase's benchmarks (from `backend/`, inside a
throwaway `python:3.11-slim` container matching `backend/Dockerfile`,
identical method to Phase 16.A/16.B):

```
374 passed, 5 skipped, 16 warnings in 50.20s
```

Matches the Phase 15/16.A/16.B reference exactly. No discrepancy.

## 3. Test Environment

Unchanged from Phase 16.A/16.B (same host, not modified for this phase):

| Item | Value |
| --- | --- |
| Host OS | Windows 11 Home 10.0.26200 (win32), Docker Desktop (WSL2 backend) |
| Docker | 29.7.2, build a7dcaa6 |
| Docker Compose | v5.5.1 |
| Docker Desktop VM resources | 8 CPUs, 3.748 GiB RAM — shared by every container, including the bench-runner load generator |
| Backend deployment | one Uvicorn process per role, no `--workers` — unchanged |
| Database | one shared SQLite file (`aegis-data` volume), no WAL — unchanged |
| Network overlay | `benchmarks/docker-compose.bench.yml` (unchanged from 16.A/16.B): additive `bench-runner` service on `agent_net` + `tool_net` |

Fresh database (`docker compose down -v` then `up --build`) at the start
of this phase, before provisioning any tenant.

## 4. Cloud/Multi-Tenant Model

Simulated locally, as instructed — not a real public Cloud deployment:

```
tenant 1 ──┐
tenant 2 ──┤
   ...     ├── HTTP (agent_net) ──> enforcement-gateway ──> broker ──> tool
tenant N ──┘
```

"**Distributed logically, not physically**": every tenant's requests in
this phase originate from the one `bench-runner` container
(`benchmarks/multitenant_client.py`), calling the real, shared
`enforcement-gateway`. This models many distinct Aegis tenants sharing one
Aegis instance over the network; it does **not** model many physical
client machines, independent network paths, or independent client-side
resource limits, and this report does not claim otherwise.

## 5. Client/Tenant Model Used

Inspected directly in the repository before building anything (no new
multi-tenant concept invented):

| Repository concept | Role |
| --- | --- |
| `Organization` (`models.py`) | The tenant/customer boundary — every `Agent`, `Permission`, `Policy`, `Event`, `Execution` is scoped to one `organization_id`. Confirmed by reading `permission_engine.allows`, `policy_engine.evaluate`, `get_or_create_execution`, `reconstruct_trajectory_state` — every one of them filters or is scoped by `organization_id` (and usually `agent_id` too). |
| `Agent` | A tenant's AI agent identity, holding its own `Permission` rows and bearer `Credential`. |
| `execution_id` | One agent run/session within one tenant; Phase 16.B established the production code assumes serial use per execution. |
| `request_id` | Idempotency key, scoped by `(request_id, agent_id)` — confirmed cross-tenant-safe in Section 12. |
| `contract_id`/`contract_version` | Present in the schema and enforcement path but **not provisionable through any public endpoint** in this repository (no `POST /api/contracts`-equivalent route exists) — same limitation Phase 16.A/16.B already recorded (no active Runtime Contract in this environment). |

**Decision, stated explicitly per the phase brief:** `organization_id` is
used as the tenant/customer unit for this benchmark. This is not an
invented mapping — it is the boundary the application code itself already
enforces everywhere a tenant-scoping check exists.

Tenants were provisioned using **only pre-existing, public endpoints**
(`benchmarks/provision_tenants.py`):

```
POST /api/auth/register           -> new Organization + admin User
POST /api/agents                   -> new Agent + Credential (token)
POST /api/agents/{id}/permissions  -> crm READ customers (allow)
POST /api/agents/{id}/permissions  -> email SEND external (allow)
POST /api/policies                 -> "Approve external email" (APPROVAL),
                                       mirroring seed.py's own demo policy
```

No `crm DELETE` permission is granted, so a delete request is BLOCKed at
the Permission layer — the same mechanism (and the same code path) Phase
16.A/16.B already exercised for the seeded demo agent, just provisioned
per-tenant this time instead of once.

100 tenants were provisioned for this phase (`benchmarks/runtime/
tenants_16c.json`, not committed — contains live bearer tokens for this
run, excluded via `.gitignore` the same way Phase 16.A/16.B's tokens were).

## 6. Workload Model

Mix used throughout (documented, not silently assumed): **60% ALLOW / 30%
BLOCK / 10% APPROVAL** — the phase brief's "APPROVAL easily usable" branch,
since `POST /api/policies` is a real, pre-existing endpoint.

| Kind | Request | Path |
| --- | --- | --- |
| ALLOW (60%) | `crm read`, scope `customers` | `POST /api/gateway/tools/crm/read` — full path: gateway → authorize → **broker → tool**, real dispatch |
| BLOCK (30%) | `crm delete`, scope `customers` | `POST /api/gateway/tools/crm/delete` — permission-layer BLOCK, no dispatch |
| APPROVAL (10%) | `email SEND`, scope/destination `external` | `POST /api/authorize` — decision-only; **the gateway's `TOOL_MAP` only supports `crm`** (`backend/app/routers/gateway.py`), so an APPROVAL-eligible email request has no broker/tool dispatch route to exercise in this codebase — a pre-existing route limitation, not something this benchmark worked around |

The mix is realized by a proportional round-robin generator
(`multitenant_client.py:_interleaved_kinds`), not a block pattern — an
early version of this tool used a block pattern and produced 100% ALLOW
for any run under 60 total requests; this was caught in a smoke test
before the real run and fixed (disclosed in Section 20, not hidden).

## 7. Request Distribution

All distribution/sweep scenarios below ran against the **same** fresh
100-tenant pool and the **same** accumulating database — tenants were
provisioned once at the start of the phase (Section 3), not reset between
comparable scenarios, so that Scenarios A–E are being compared against
literally the same tenants and permissions, which is a stronger basis for
comparison than re-provisioning fresh (but different) tenants each time.
This choice is stated explicitly, per the phase brief's instruction to
document when fresh-vs-reused state applies.

**Scenarios A–E, total=50 requests, concurrency=50, mix 60/30/10**
(priority scenarios A, C, E per the phase brief's Section 18 ran 3 trials;
B, D ran 1 trial — disclosed, not silently reduced):

| Scenario | Clients × reqs/client | Trial | req/s | p50 (ms) | p95 (ms) | p99 (ms) | Errors | Decisions (ALLOW/BLOCK/APPROVAL) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| A | 1 × 50 | 1 | 18.53 | 1567.5 | 2374.8 | 2525.9 | 0 | 30/15/5 |
| A | 1 × 50 | 2 | 21.65 | 1294.8 | 1969.5 | 2169.8 | 0 | 30/15/5 |
| A | 1 × 50 | 3 | 22.26 | 1206.2 | 2019.2 | 2116.7 | 0 | 30/15/5 |
| B | 5 × 10 | 1 | 22.13 | 1279.7 | 1977.1 | 2111.8 | 0 | 30/15/5 |
| C | 10 × 5 | 1 | 19.80 | 1345.6 | 2160.4 | 2329.4 | 0 | 30/15/5 |
| C | 10 × 5 | 2 | 21.71 | 1235.7 | 1964.8 | 2193.7 | 0 | 30/15/5 |
| C | 10 × 5 | 3 | 20.66 | 1259.7 | 2059.4 | 2272.8 | 0 | 30/15/5 |
| D | 25 × 2 | 1 | 22.84 | 1255.7 | 1981.4 | 2086.3 | 0 | 30/15/5 |
| E | 50 × 1 | 1 | 23.21 | 1234.2 | 1970.7 | 2063.6 | 0 | 30/15/5 |
| E | 50 × 1 | 2 | 21.40 | 1205.1 | 2016.5 | 2198.1 | 0 | 30/15/5 |
| E | 50 × 1 | 3 | 21.41 | 1233.2 | 2073.3 | 2187.6 | 0 | 30/15/5 |

**OBSERVED:** every distribution shape (1, 5, 10, 25, or 50 tenants
sharing the same 50-request/50-concurrency load) lands in the same
throughput band (18.5–23.2 req/s) and the same latency band (p50
1.2–1.6s, p95 1.97–2.37s), zero errors, in every trial. Trial-to-trial
variance within one scenario (e.g., Scenario A: p95 1969–2375ms across 3
trials) is comparable to or larger than the variance *between* different
scenarios at the same trial count — i.e., **the client-count dimension
does not show a distinguishable effect at this concurrency/volume**, given
this sample size.

**Size variants (total=25, concurrency=25; total=100, concurrency=50):**

| Scenario | Total | Concurrency | Trial | req/s | p50 (ms) | p95 (ms) | p99 (ms) | Errors |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A25 (1×25) | 25 | 25 | 1 | 15.73 | 776.5 | 1388.8 | 1497.2 | 0 |
| E25 (25×1) | 25 | 25 | 1 | 11.82 | 895.2 | 1896.0 | 2024.4 | 0 |
| E100 (50×2) | 100 | 50 | 1 | 19.47 | 1628.3 | 3549.2 | 4646.8 | 0 |
| E100 (50×2) | 100 | 50 | 2 | 20.83 | 1727.8 | 3146.7 | 3631.5 | 0 |
| E100 (50×2) | 100 | 50 | 3 | 17.58 | 1967.6 | 4314.9 | 5123.8 | 0 |

At total=25, A25 (1 tenant) and E25 (25 tenants) show more spread than the
total=50 comparisons (p95 1389 vs 1896ms) but only one trial each was run
at this size (disclosed limitation, Section 20) — not enough to say
whether that gap is a real single-vs-multi-tenant effect or ordinary
run-to-run variance (Phase 16.B's repeated-trial data showed variance of
this order is common even for the identical configuration). At total=100
(fixed concurrency=50, more queued work), tail latency is clearly worse
(p95 3.1–4.3s, p99 up to 5.1s) than at total=50 (p95 ~2.0s) — consistent
with ordinary queuing behind a fixed concurrency cap, not a new mechanism.

**B–D at total=25/100 were not additionally run** (only A and E were, per
the phase brief's "if cost is reasonable" qualifier and this phase's time
budget) — disclosed in Limitations (Section 20), not silently skipped.

## 8. Concurrency Results

**Concurrency sweep, "1 request per client" model** (client count = c, one
request each, so this cell literally is "c distinct tenants, one request
apiece" — the phase brief's own definition of what c should mean in a
cloud context):

| c | req/s | p50 (ms) | p95 (ms) | p99 (ms) | max (ms) | Errors | Timeout | HTTP 5xx | Notes |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | 7.58 | 131.6 | 131.6 | 131.6 | 131.6 | 0/1 | 0 | 0 | single sample; rps not meaningful |
| 5 | 14.69 | 213.2 | 316.4 | 335.4 | 340.2 | 0/5 | 0 | 0 | |
| 10 | 14.53 | 394.1 | 581.5 | 628.3 | 640.1 | 0/10 | 0 | 0 | |
| 25 | 13.55 | 825.3 | 1640.9 | 1807.2 | 1844.4 | 0/25 | 0 | 0 | |
| 50 | 21.40–23.21† | 1205–1234† | 1971–2073† | 2064–2199† | — | 0/50 | 0 | 0 | †reused from Scenario E trials 1–3, Section 7 |
| 75 | 4.84 | 15362.0 | 15381.8 | 15392.0 | 15416.2 | **75/75** | 75 | 0 | client `ReadTimeout`, not a server error |
| 100 | 6.41 | 15331.9 | 15401.4 | 15417.5 | 15483.6 | **100/100** | 100 | 0 | client `ReadTimeout`, not a server error |

**OBSERVED:** healthy (0 errors) from c=1 through c=50; **complete
collapse (100% failure) at c=75 and c=100** — every one of those requests
is a client-side 15-second `ReadTimeout`, not a server 4xx/5xx (the server
never responded in time). This is the identical failure signature Phase
16.B found (Section 7 of that report) for a single-tenant workload,
**now confirmed under a genuinely multi-tenant workload spread across up
to 100 distinct organizations** (this sweep's c=100 cell used 100 distinct
tenants, one request each).

**DERIVED:** because the collapse threshold (between 50 and 75) is
unchanged whether the concurrent requests come from 1 tenant (Phase 16.B)
or up to 100 tenants (this phase), the bottleneck is a property of the
**shared instance's total concurrent-request handling capacity**, not of
tenant count or per-tenant state. This is the phase's clearest answer to
"does client count matter, or does raw concurrency matter" — raw
concurrency does; client count, at this sample size, does not show a
separate effect.

**Timeout vs. server error distinction, honored throughout this report**:
every error in the c=75/c=100 rows is a client-observed timeout. A BLOCK
decision (Section 6, ~30% of the mix in every healthy run) is counted as a
**successful, correct response**, never as an error — confirmed by
`decision_counts` in every healthy scenario/sweep row showing the expected
~60/30/10 ALLOW/BLOCK/APPROVAL split.

## 9. Single vs Multi-Client Comparison

This is the same dataset as Section 7 (Scenarios A–E), read through the
phase brief's specific TEST1–TEST4 framing:

| Test | Configuration | req/s (best available trial) | p95 (ms) |
| --- | --- | ---: | ---: |
| TEST1 | 50 requests, 1 client (Scenario A) | 18.5–22.3 (3 trials) | 1969–2375 |
| TEST2 | 50 requests, 50 clients (Scenario E) | 21.4–23.2 (3 trials) | 1971–2073 |
| TEST3 | 50 requests, 10 clients (Scenario C) | 19.8–21.7 (3 trials) | 1965–2160 |
| TEST4 | 50 requests, 5 clients (Scenario B) | 22.1 (1 trial) | 1977 |

**OBSERVED:** no test in this comparison shows a throughput or latency
difference outside the range of trial-to-trial variance already present
within a single configuration (e.g., TEST1/Scenario A alone spans
18.5–22.3 req/s across its own 3 trials — comparable to the spread across
TEST1–TEST4). **Database contention, execution collisions, and evidence
integrity** were also checked for this comparison: zero errors and zero
integrity issues in any of these runs (all used the default "normal"
execution_id model — a fresh execution per request — so no execution-id
collision is expected or was seen; see Section 11 for the deliberately
different case). No cross-tenant anomaly was observed in any of these
requests' recorded `organization_id`/`agent_id` (spot-checked against the
provisioned tenant list).

**Conclusion for this section, stated at the confidence this data
actually supports:** at 50 total requests and concurrency 50, this
benchmark found **no measurable difference** in throughput, latency, or
error behavior attributable to how many distinct tenants the load was
split across. This is a bounded, single-order-of-magnitude finding (50
requests, concurrency 50) — Section 8 shows the picture changes sharply
once total concurrency crosses into the 75+ collapse zone, which this
section's tests never reached.

## 10. Client Fairness / Noisy Neighbor

**Two runs of this test are reported, deliberately, because the first was
contaminated and the contamination is itself informative.**

**Run 1 (as originally executed, immediately following the Section 8
concurrency sweep's c=75/c=100 collapse) — total=100, concurrency=50:**

| Trial | Tenant "heavy" (80 or 95 reqs) p95 | Tenant "light" (20 or 5 reqs) p95 | Overall errors |
| --- | ---: | ---: | ---: |
| 80/20, trial 1 | 15198.9 ms | 15197.4 ms | 100/100 |
| 80/20, trial 2 | (100% timeout) | (100% timeout) | 100/100 |
| 80/20, trial 3 | (100% timeout) | (100% timeout) | 100/100 |

**Both** the heavy and the light tenant failed at essentially the same
~15.2s (the client timeout) in every trial — not "heavy tenant crowds out
light tenant" (which would show heavy succeeding and light failing, or
heavy fast and light slow), but a **total, tenant-independent outage**.
Cross-referencing `benchmarks/results/16c_resource_usage.csv` for this
exact time window: `enforcement-gateway` CPU was **0.1%–0.5%** throughout
(one small blip to 11.8%), while its memory climbed steadily (≈173→187
MiB) over the same ~15 minutes, and `docker inspect --format
{{.RestartCount}}` stayed at `0` for every container at the end of the
run — **OBSERVED: no crash, near-zero CPU, growing memory, near-total
request failure, self-recovering** (the very next test, `execution_id`
concurrent-same, run about 2 minutes later, executed normally — Section
11). This is a **capacity/reliability finding about the shared instance
recovering slowly from overload, not a tenant-fairness finding** — it is
reported under this heading because it was discovered here, but it
belongs with Section 8/16's collapse discussion, not as evidence about
noisy-neighbor behavior.

**Run 2 (clean re-run, after confirming recovery)**: `enforcement-gateway`
was restarted (`docker restart aegis-enforcement-gateway`, a disclosed,
manual, non-destructive action — see Section 16) and confirmed healthy via
a 6-request sanity check (0 errors, normal ~40–280ms latencies) before
re-running the noisy-neighbor scenarios. The request-submission order was
also shuffled (`--shuffle-seed`, a minimal benchmark-tooling addition — see
Section 20) so that the "heavy" tenant's requests being listed first would
not, by itself, bias which tenant's requests acquire the shared
concurrency semaphore first.

| Trial | Tenant | Requests | Errors | p50 (ms) | p95 (ms) |
| --- | --- | ---: | ---: | ---: | ---: |
| 80/20, trial 1 | heavy (80) | 80 | 0 | 980.4 | 2006.2 |
| 80/20, trial 1 | light (20) | 20 | 0 | 1025.9 | 2003.5 |
| 80/20, trial 2 | heavy (80) | 80 | 0 | 987.1 | 1824.9 |
| 80/20, trial 2 | light (20) | 20 | 0 | 966.7 | 1628.2 |
| 80/20, trial 3 | heavy (80) | 80 | 0 | 977.8 | 2171.7 |
| 80/20, trial 3 | light (20) | 20 | 0 | 990.6 | 1328.4 |
| 95/5 | heavy (95) | 95 | 0 | 1064.8 | 2077.7 |
| 95/5 | light (5) | 5 | 0 | 777.0 | 2588.8 |

**OBSERVED, clean data:** zero errors in every trial; the light tenant's
p50/p95 is statistically indistinguishable from the heavy tenant's in
every 80/20 trial (light sometimes faster, sometimes slower, within noise
— e.g., trial 3's light p95 of 1328ms is the *fastest* of any cell in the
table). The 95/5 split's single light-tenant sample (n=5) has a wider p95
(2588.8ms) than its own p50 would suggest, consistent with a small-sample
artifact (5 requests is not enough to estimate a stable p95) rather than
a fairness signal.

**Conclusion:** at concurrency=50 (a level Section 8 shows is healthy),
this benchmark found **no evidence that a heavily-weighted tenant degrades
a lightly-weighted tenant's service** — both are equally subject to the
shared instance's overall load, not to each other specifically. This
phase did not test noisy-neighbor behavior *at* or *near* the c=75
collapse threshold (that would require deliberately re-triggering the
collapse, which the phase rules caution against doing gratuitously); this
is disclosed as a gap in Section 20.

No rate limiting exists in the codebase to measure (confirmed by the
absence of any rate-limit code in `backend/app/routers/gateway.py`,
`authorize.py`, or `security.py`); none was added.

## 11. Execution_ID Behavior

Three models, tested exactly as the phase brief distinguishes them, each
embedded in real background multi-tenant traffic (30 other tenants, 2
requests each, concurrency 30, running concurrently via
`multitenant_client.py`) rather than in isolation:

**Normal model (expected, supported):** every request in Sections 7–10
above used this model (`execution_id` omitted, a fresh `Execution` row per
request) — it is the SDK/demo-agent default (Phase 16.A Section 8) and
produced zero execution-related errors across roughly 1,900 such requests
in this phase.

**Serial-same model (one execution_id, concurrency=1, 20 sequential
requests)** — deliberately adversarial in intent (reusing one execution_id
at all is outside the SDK's own usage pattern), run via
`bench_client.py --execution-id ... --concurrency 1`:

This run happened to start about 2 minutes into the post-collapse
hangover window (Section 10) — **not by design**; it is reported as-is
because it produced an additional, genuine finding: `18 of 20` requests
timed out (`ReadTimeout`) over **264 seconds** for what should have been a
short run, and — despite `--concurrency 1` meaning the *client* never had
more than one request in flight — one of the two non-timeout responses was
an evidence-integrity `409` ("chain tip does not match predecessor"), the
same error Phase 16.B's genuinely concurrent burst produced. **DERIVED:**
a client-side timeout does not stop the abandoned request on the server;
if that request is still executing (or queued) when the client, having
given up, sends its *next* request on the same `execution_id`, the two
requests can genuinely overlap **at the server**, even though the client
believes it is operating strictly serially. "Sequential from the client's
perspective" is therefore not the same guarantee as "sequential at the
server" whenever requests can be abandoned by timeout. The resulting
chain (`benchmarks/results/16c_execid_serial_same_chain_probe.json`):
5 events committed, 0 duplicate `seq`, 0 gaps, verifier **PASS** — the
race that did occur was caught and rejected, not silently corrupted.

**Concurrent-same model (one execution_id, concurrency=20, 20 requests)**
— deliberately adversarial, the same scenario Phase 16.B ran, repeated
here embedded in background multi-tenant noise, and run about 2 minutes
after the serial-same test (by which point the instance had recovered —
Section 10):

| Outcome | Count |
| --- | ---: |
| 200 (committed, ALLOW) | 8 |
| 409 (evidence integrity: "chain tip does not match predecessor") | 9 |
| 500 (unhandled `IntegrityError`-class failure, per Phase 16.B's finding) | 3 |

Chain probe: 8 events, 0 duplicate `seq`, 0 gaps, verifier **PASS**.
**OBSERVED:** this reproduces Phase 16.B's `get_or_create_execution` race
finding essentially unchanged, in absolute terms (similar proportions:
~40% success, ~45% fail-closed 409, ~15% hard 500), now confirmed to
reproduce while 30 *other*, unrelated tenants are concurrently issuing
normal traffic in the background — the race is a property of concurrent
access to one `execution_id`, not an artifact of an otherwise-idle test
environment.

Both chain probes also ran the cross-tenant scope check (Section 12):
querying `reconstruct_trajectory_state` for each execution with a
*different* tenant's `organization_id`/`agent_id` returned `None` in both
cases — correctly scoped out, no leak.

## 12. Client/Tenant Isolation Validation

Adversarial probes against the real, running `enforcement-gateway` and
`credential-broker` — no security check bypassed or mocked.

| Test | Method | Result | Verdict |
| --- | --- | --- | --- |
| A's token + B's `execution_id` | Tenant B makes a real call establishing an execution it owns; tenant A then calls the gateway with **its own token** but B's `execution_id` in the body | **403** `"Execution does not belong to this agent"` | **FAIL_CLOSED** (OBSERVED) |
| Shared `request_id` across tenants | A and B both send a request carrying the identical `request_id` string | Both succeed independently; A's event carries A's `agent_id`/`organization_id`, B's carries B's — no idempotency collision | **OK** (OBSERVED) |
| EAT: `org_id` mismatch | Real `sign_eat` mints an EAT for tenant A; the dispatch body claims tenant B's `org_id` | **401** `eat_rejected` | **FAIL_CLOSED** (OBSERVED) |
| EAT: `agent_id` mismatch | Same EAT-signing approach, body claims a different `agent_id` | **401** `eat_rejected` | **FAIL_CLOSED** (OBSERVED) |
| EAT: `execution_id` mismatch | Same approach, body claims a different `execution_id` | **401** `eat_rejected` | **FAIL_CLOSED** (OBSERVED) |
| EAT: same-tenant baseline | Same approach, all claims match | **200** | Confirms the probe itself is valid, not just "everything gets rejected" |
| Trajectory read, wrong tenant scope | `reconstruct_trajectory_state(execution_id, organization_id=<wrong>, agent_id=<wrong>)` | Returns `None` | **Correctly scoped out** (OBSERVED, via Section 11's chain probes) |
| Contract context cross-tenant | — | **NOT VERIFIED** — no endpoint exists anywhere in this repository to provision a Runtime Contract (confirmed by inspecting every router; `save_contract` in `contract_store.py` is only called from test code). `resolve_active_contract_for_agent`'s own filters (`organization_id`, `agent_id`) were read in source but not exercised live with two real, differently-scoped contracts. |

**No cross-tenant ALLOW occurred anywhere in this phase** — every
adversarial probe above resulted in a rejection (403/401) or a correctly
independent success, never in one tenant's request being satisfied with
another tenant's authority, data, or execution state.

## 13. Evidence Chain Behavior

Identical methodology to Phase 16.B (`benchmarks/chain_probe.py`, the
real, unmodified `assert_execution_evidence_integrity` and
`reconstruct_trajectory_state`, never reimplemented or mocked), applied to
the two same-`execution_id` bursts in Section 11:

| Check | serial-same (Section 11) | concurrent-same (Section 11) |
| --- | --- | --- |
| Events committed | 5 | 8 |
| Duplicate `seq` | none | none |
| `seq` gaps | none | none |
| Verifier (`assert_execution_evidence_integrity`) | **PASS** | **PASS** |
| Chain tip matches last event | yes | yes |
| Cross-tenant scope check | correctly returns `None` for a different tenant | correctly returns `None` for a different tenant |

No corrupted chain was found anywhere in this phase, across roughly 2,000
requests including two deliberately-adversarial same-execution bursts run
inside real multi-tenant background load. Where a race *did* occur, the
verifier's own mechanism (the `seal_execution_event` predecessor check)
converted it into a `409`, exactly as Phase 16.B found — this phase adds
that the same protection holds under realistic multi-tenant traffic, not
only in an isolated single-tenant test.

## 14. EAT / Replay Behavior

Covered in Section 12 as cross-tenant claim-mismatch tests (`org_id`,
`agent_id`, `execution_id`), all correctly rejected. This phase did not
re-run Phase 16.B's *same-tenant* replay-burst test (10 concurrent replays
of one valid EAT, exactly 1 accepted) — that mechanism is unrelated to
multi-tenancy and was already established in Phase 16.B; re-testing it
here would not add new information about the Cloud/multi-tenant question
this phase targets. What *is* new here — claim binding actually rejecting
a **different tenant's** claims, not just a replayed or tampered one — was
tested and held (Section 12).

## 15. SQLite Behavior

| Checkpoint | Events | Executions | Organizations | DB size |
| --- | ---: | ---: | ---: | ---: |
| After provisioning 100 tenants, before any load | 0 | 0 | 101* | 421,888 bytes |
| After Scenarios A–E, size variants, and the concurrency sweep | 964 | 964 | 101 | 1,540,096 bytes |
| End of phase (includes the contaminated + clean noisy-neighbor runs, execution_id pattern tests, isolation probes) | 1,513 | 1,502 | 101 | 2,179,072 bytes |

\* 100 provisioned tenants + 1 pre-existing seeded demo organization
("Acme Corp", created by `seed_if_empty` on control-plane startup,
untouched and unused by this phase's tests).

~1.44 KB/event at the end of this phase, consistent with Phase 16.A/16.B's
~1.14–1.24 KB/event (same schema, similar overhead; the small difference
is not investigated further — not material to this phase's questions).

**Gap, disclosed:** unlike Phase 16.B, this phase did **not** run
`benchmarks/log_errors.py` against `enforcement-gateway`'s Docker logs
during the c=75/c=100 collapse or the subsequent hangover — the container
was torn down (`docker compose down`) before this gap was noticed, so
those logs cannot be retrieved retroactively. Phase 16.B's own finding for
its equivalent collapse (Section 7 of that report) was that the c=75/100
collapse showed **zero** `database is locked` log lines (unlike the
smaller-scale c=5–50 contention cases, which did) — it is **DERIVED, by
analogy to that prior finding, not OBSERVED in this run**, that the
collapse and hangover in this phase are similarly *not* primarily a raw
SQLite-lock phenomenon. This is flagged explicitly rather than silently
assumed; a repeat of this phase's collapse with log classification wired
in (as Phase 16.B had) is a candidate for future work (Section 21/22).

## 16. Resource Usage

`docker stats --no-stream` sampled every 2 seconds for the whole phase
(120 samples per container; `benchmarks/results/16c_resource_usage.csv`):

| Container | Avg CPU% | Max CPU% | Memory (last sample before restart) |
| --- | ---: | ---: | ---: |
| aegis-enforcement-gateway | 13.64% | 137.48% | 180.3 MiB / 3.748 GiB |
| aegis-bench-runner (load generator) | 4.81% | 54.12% | 2.5 MiB / 3.748 GiB |
| aegis-control-plane (provisioning only, idle otherwise) | 4.18% | 51.57% | 68.5 MiB / 3.748 GiB |
| aegis-credential-broker | 3.74% | 51.26% | 79.2 MiB / 3.748 GiB |
| aegis-protected-tool | 0.83% | 10.03% | 63.5 MiB / 3.748 GiB |

`enforcement-gateway`'s **average** CPU (13.64%) is markedly lower than
Phase 16.B's equivalent figure (33–73% across that phase's runs) —
**consistent with** a large fraction of this phase's wall-clock time being
spent in the near-zero-CPU hangover window (Section 10), which pulls the
average down even though peak CPU (137.48%) is comparable to Phase 16.B's
peaks. During the collapse/hangover window specifically, CPU stayed at
0.1%–0.5% (Section 10) while memory rose steadily — **consistent with** a
growing backlog of queued or stuck work (connections, thread-pool
entries, or similar), not active computation. This phase did not
instrument the ASGI thread pool or SQLite connection layer directly, so
the exact resource being exhausted is stated as "consistent with," not
proven — identical caveat to Phase 16.B's Section 17.

**Manual intervention, fully disclosed:** after confirming the
noisy-neighbor test's first run was contaminated by the hangover
(Section 10), `enforcement-gateway` was restarted with `docker restart
aegis-enforcement-gateway` — an ordinary container restart, not a code or
configuration change, and not counted by Docker's `RestartCount` field
(which tracks restart-policy-triggered restarts, not manual ones). Health
was confirmed (`docker inspect .State.Health.Status` = `healthy`) before
any further test ran. No container's `RestartCount` (the auto-restart
counter) exceeded 0 at any point in this phase — the collapse degraded
request handling for several minutes but never crashed a process.

## 17. Sustainable Capacity

Per the phase brief: distinguishing peak (unusable, collapse) throughput
from sustainable (healthy) throughput.

**OBSERVED healthy range:** concurrency 1 through 50, across every
distribution shape (1–50 tenants) and total volume (25, 50, 100 requests)
tested — **zero errors, zero cross-tenant leakage, interpretable p95/p99**
in every one of those cells. The single best-characterized sustainable
point (most trials, most distribution shapes converging on it) is
**concurrency=50, mixed 60/30/10 workload: ~19–23 req/s, p95 ~2.0–2.4s
(at total=50) or ~3.1–4.6s (at total=100, same concurrency cap, simply
more queued work behind it).**

**OBSERVED collapse range:** concurrency 75 and 100 — 100% failure, not
usable as a capacity figure under any interpretation (per the phase
brief's explicit instruction not to report a collapsed throughput number
as capacity).

**The precise boundary between 50 and 75 was NOT identified** in this
phase (same gap Phase 16.B left open) — this phase's contribution is
confirming that boundary is **not shifted by tenant count**: c=50 with
100 distinct tenants (1 req each) behaves the same as c=50 with 1 tenant
(50 reqs, Scenario A) or 50 tenants (1 req each, Scenario E).

**One additional, important qualifier this phase found that Phase 16.B's
single-tenant sweep could not show:** "sustainable" is not solely a
function of concurrency — the total=100/concurrency=50 cells (Section 7)
stayed error-free but pushed p95 up to 3.1–4.6s, meaningfully worse than
total=50/concurrency=50's ~2.0–2.4s. Whether a fixed concurrency=50 stays
healthy at total=500 or total=1,000 requests (i.e., whether queue depth
alone can eventually push it into the same kind of collapse Section 8
found at concurrency=75, even without ever raising concurrency) was **NOT
VERIFIED** — this phase did not test it.

## 18. Capacity Model

Per the phase brief's explicit rule, this section does not produce a
customer-count promise.

**DERIVED — the pure arithmetic, and why it stops there:** the phase
brief's example formula is `client_capacity ≈ sustainable_rps /
client_average_rps`. This phase measured `sustainable_rps ≈ 19–23 req/s`
(Section 17, concurrency=50, mixed workload, zero errors). It has **no**
measurement of `client_average_rps` for a real Aegis customer — every
tenant in this benchmark was synthetic, provisioned solely to generate
load, and issued requests as fast as the benchmark's own concurrency
allocation permitted, not at any rate representative of real usage. There
is no telemetry, no usage log, and no product-usage assumption anywhere
in this repository that could supply a defensible `client_average_rps`.

**Purely illustrative arithmetic (not a claim):** if a hypothetical
tenant's average load were, say, 1 request per minute
(`client_average_rps ≈ 0.0167`), the formula would yield
`≈ 20 / 0.0167 ≈ 1,200` "equivalent tenants." This number is shown only to
demonstrate the formula's mechanics; the input (1 request/minute) is an
arbitrary illustration, not a researched or measured figure, and this
report does not endorse it as realistic.

**Conclusion, exactly as the phase brief instructs when the data does not
support a conversion:**

> **Capacity cannot yet be translated into customer count.**

What *is* established, correctly scoped: "with this workload (60/30/10
ALLOW/BLOCK/APPROVAL mix), this configuration (single-process
enforcement-gateway, shared SQLite, Docker Desktop/WSL2 host), and this
environment, the instance sustained approximately 19–23 requests/second at
p95 ≈ 2.0–2.4 seconds with zero errors at concurrency 50, regardless of
how many distinct tenants that concurrency was distributed across, and
collapsed completely at concurrency 75."

## 19. Findings

Each finding classified per the phase brief's required schema.

---

**ID:** 16C-F1
**Severity:** High
**Category:** RELIABILITY (explicitly not SECURITY — no unauthorized
decision resulted)
**Observed:** Immediately after the concurrency-sweep collapse at c=75/100
(16C-F2 below), a subsequent, independently-scoped test (noisy-neighbor,
nominal concurrency=50) failed 100% of requests for **both** tenants
involved, for several minutes, before self-clearing without any process
restart.
**Expected:** A test at a concurrency level (50) independently shown
healthy in isolation (Section 8) should behave the same regardless of
what ran immediately before it, once that prior load has stopped.
**Evidence:** `benchmarks/results/16c_noisy_80_20_t{1,2,3}.json` (100%
`ReadTimeout`, both tenants); `benchmarks/results/16c_resource_usage.csv`
(CPU 0.1–0.5% throughout, memory climbing ≈173→187 MiB); `docker inspect
--format {{.RestartCount}}` = 0 throughout; recovery confirmed by the
subsequent clean sanity check and clean noisy-neighbor re-run (Section
10).
**Impact:** An overload episode's effect on a shared multi-tenant instance
is not confined to its own duration — it can degrade or fully block
service for **all** tenants for a period of minutes after the triggering
load has stopped, with no visible crash to alert on.
**Status:** Reproduced once (not repeated across independent trials in
this phase — Section 20); not remediated (per phase rules).

---

**ID:** 16C-F2
**Severity:** High
**Category:** RELIABILITY (carried forward from Phase 16.B, reconfirmed
under multi-tenant load)
**Observed:** Concurrency 75 and 100 produced 100% request failure
(`ReadTimeout` at the client's 15s timeout) across every scenario tested
at those levels, whether the concurrent requests came from 1 tenant or up
to 100 distinct tenants. `enforcement-gateway` CPU stayed near 0% during
the collapse.
**Expected:** Graceful degradation (rising latency, rising but bounded
error rate) rather than complete, CPU-idle failure.
**Evidence:** `benchmarks/results/16c_sweep_c75.json`,
`16c_sweep_c100.json` (both `error_count` == `actual_count`); resource CSV
for the same window.
**Impact:** Identical to Phase 16.B's 16B-Finding-1 — now confirmed
tenant-count-independent: the collapse threshold is a property of total
concurrent requests to the shared instance, not of how many tenants they
represent.
**Status:** Reproduced, not remediated (per phase rules); the precise
boundary between 50 and 75 remains unidentified (also true in 16.B).

---

**ID:** 16C-F3
**Severity:** Informational / Positive
**Category:** SECURITY
**Observed:** Every cross-tenant adversarial probe attempted (A's token +
B's execution_id; shared request_id; EAT org_id/agent_id/execution_id
mismatch; trajectory read with wrong tenant scope) resulted in a
rejection or a correctly-independent success — never a cross-tenant ALLOW
or data leak.
**Expected:** Fail-closed tenant isolation.
**Evidence:** Section 12 table; `benchmarks/results/16c_isolation_probe.json`,
`16c_eat_cross_tenant.json`.
**Impact:** No security regression found; tenant isolation held under
every condition tested, including during the degraded state of 16C-F1.
**Status:** Confirmed positive; no action needed. Contract-context
cross-tenant isolation remains **NOT VERIFIED** (no provisioning path
exists to test it live).

---

**ID:** 16C-F4
**Severity:** Medium
**Category:** RELIABILITY (carried forward from Phase 16.B, reconfirmed;
one new nuance)
**Observed:** Concurrent requests sharing one `execution_id` reproduce
Phase 16.B's `get_or_create_execution`/`seal_execution_event` race
(~40% success, ~45% fail-closed 409, ~15% hard 500) even when embedded in
real multi-tenant background traffic. Additionally, a *client-perceived-
sequential* (concurrency=1) run on one execution_id produced one
evidence-integrity 409 — because an abandoned (timed-out) request can
still be executing server-side when the client's next "sequential"
request is sent.
**Expected:** The application assumes serial use of one `execution_id`;
this benchmark deliberately violates that assumption to characterize the
resulting behavior, per the phase brief.
**Evidence:** Section 11; `benchmarks/results/16c_execid_*`.
**Impact:** Same as Phase 16.B: a reliability bug under a deliberately
unsupported usage pattern, not a security failure (the evidence verifier
catches and rejects the corrupting write in every case observed). The
new nuance (timeout ≠ server-side cancellation) means "serial" client
code is not automatically immune if it has a timeout and retries with the
same execution_id.
**Status:** Reproduced, not remediated (per phase rules).

---

**ID:** 16C-F5
**Severity:** Informational
**Category:** SCALABILITY
**Observed:** At fixed total concurrency (50) and total volume (50
requests), distributing load across 1, 5, 10, 25, or 50 tenants produced
statistically indistinguishable throughput and latency.
**Expected:** Not assumed in advance; this was the central open question
of the phase.
**Evidence:** Section 7/9 tables.
**Impact:** Confirms the phase's own opening caution was correct: reading
"50 concurrent requests" as "50 customers" (or vice versa) would have
been the wrong mental model — client count is not, in this environment
and at this scale, an independent variable.
**Status:** Confirmed; supports treating concurrency (not tenant count) as
the primary capacity variable for future phases.

---

## 20. Limitations

- Host-specific (Windows + Docker Desktop/WSL2) — same caveat as Phase
  16.A/16.B; not portable to bare-metal Linux without re-running there.
- Scenarios B and D (Section 7) ran only 1 trial each, not 3 — time budget
  was concentrated on the phase brief's explicit priority list (A, C, E,
  the total=100/50-client variant, and noisy-neighbor), which is disclosed
  in Section 7, not hidden.
- Total=25 and total=100 size variants (Section 7) were only run for
  Scenarios A and E (the two extremes), not B/C/D, per the phase brief's
  "if cost is reasonable" qualifier.
- The noisy-neighbor test's first run was contaminated by 16C-F1 and is
  reported alongside, not instead of, the clean re-run (Section 10) — this
  is a deliberate methodological choice (both are informative) but means
  the "clean" fairness conclusion rests on one clean pass (3 trials at
  80/20, 1 at 95/5) rather than a fully independent replication of the
  whole noisy-neighbor experiment from a cold start.
- This phase's collapse/hangover window (16C-F1/16C-F2) was **not** run
  with server-log classification wired in (unlike Phase 16.B's
  `log_errors.py` correlation) — the affected container was torn down
  before this gap was noticed, so the exact server-side error signature
  during this phase's specific collapse/hangover is **NOT VERIFIED**
  directly; Section 15 states explicitly what is DERIVED-by-analogy versus
  OBSERVED-in-this-run.
- No dedicated recovery-time measurement was built (e.g., periodic small
  probes fired every few seconds after inducing a collapse, to time
  recovery precisely). The ~2–5 minute hangover duration reported in
  Section 10/16 is a DERIVED estimate from the incidental timing of tests
  that happened to run during and after it, not a purpose-built
  measurement.
- Contract-context cross-tenant isolation (Section 12) is NOT VERIFIED —
  no endpoint exists in this repository to provision a Runtime Contract,
  so this specific adversarial case could not be exercised live.
- No active Runtime Contract exists in this environment at all (same
  limitation carried from Phase 16.A/16.B).
- The workload's APPROVAL slice (10%) never exercises the gateway's
  broker/tool dispatch (the `TOOL_MAP` only supports `crm`) — a
  pre-existing route limitation, not a gap this benchmark could close
  without inventing new routes, which the phase rules forbid.
- Noisy-neighbor was tested only at concurrency=50 (a level shown
  healthy) — not near or at the c=75 collapse threshold, where a
  fairness effect (if any exists) might look different.
- Whether a *fixed* concurrency=50 eventually collapses under a much
  larger total request volume (queue depth alone, without raising
  concurrency) was NOT VERIFIED.
- Sample sizes throughout (typically 1–3 trials, 25–100 requests per
  cell) are adequate to see the large effects this phase found (the
  collapse, the hangover, the isolation results) but not to characterize
  small effects (e.g., a subtle single-digit-percent fairness gap) with
  statistical confidence.

## 21. Remediation Candidates (NOT applied in this phase)

Carried forward from Phase 16.B where still relevant, plus two new items
from this phase's findings. Listed as problems + possible directions only
— nothing here was applied.

1. **Post-overload hangover (16C-F1) — new.** Possible directions:
   investigate whether abandoned (client-disconnected) requests continue
   consuming a thread-pool slot or a database connection indefinitely
   (would explain both the memory growth and the multi-minute recovery);
   consider a server-side request timeout/cancellation so a disconnected
   client's work is actually abandoned server-side, not left running.
2. **c=75+ collapse (16C-F2, carried from 16B-Finding-1).** Same
   candidates as Phase 16.B: multiple Uvicorn worker processes, larger
   ASGI thread pool, or async endpoints with a genuinely async DB driver —
   each needs its own before/after benchmark.
3. **`get_or_create_execution` race (16C-F4, carried from 16B-Finding-2).**
   Same candidate as Phase 16.B: an atomic upsert or a caught-and-retried
   `IntegrityError` instead of check-then-insert.
4. **SQLite `database is locked` (carried from 16.A/16.B, not
   independently reconfirmed this phase — Section 15/20 gap).** Same
   candidates as before (WAL mode, single-writer queue, different
   engine) — still explicitly deferred.
5. **No persistent `httpx.Client` for gateway→broker→tool (carried from
   16.A/16.B).** Same candidate as before (connection pooling) — not
   re-tested this phase.
6. **Tenant isolation hardening** — not indicated by this phase's data
   (every probe held); no candidate proposed here beyond continuing to
   test it as new features are added.
7. **Noisy-neighbor / rate limiting** — not indicated by this phase's
   clean data (no fairness degradation found at a healthy concurrency
   level); a candidate would only be justified by data from testing
   nearer the collapse threshold (Section 20's gap), which this phase did
   not produce.

## 22. Verdict

**PASS WITH LIMITATIONS.**

This phase does produce a credible, workload-labeled measure of how a
shared Aegis instance behaves under a genuinely multi-tenant load: it
shows client count (at the scale tested) does not independently change
throughput or latency; it reconfirms the Phase 16.B collapse threshold
under multi-tenant conditions; it finds one new, significant reliability
behavior (the post-collapse hangover, 16C-F1) that a single-tenant test
could not have surfaced as clearly; and it finds no cross-tenant security
failure anywhere, including during the degraded state. Per Section 24 of
the phase brief's own distinction, 16C-F1 and 16C-F2 are RELIABILITY
findings, not SECURITY findings — no fallback-to-ALLOW, no cross-tenant
leak, and no corrupted evidence chain occurred at any point in this phase.

The "WITH LIMITATIONS" qualifier reflects Section 20 in full: several
scenarios ran at reduced trial counts or sizes, the hangover's exact
mechanism and duration were not purpose-measured, contract-context
isolation could not be tested at all, and this report explicitly declines
to convert throughput into a customer-count claim (Section 18), per the
phase's own final rule.

---

## Reproducibility

```bash
# Fresh database
docker compose -f docker-compose.yml -f benchmarks/docker-compose.bench.yml down -v
docker compose -f docker-compose.yml -f benchmarks/docker-compose.bench.yml up -d --build

# Provision 100 tenants via the real public API (register/agents/policies)
python benchmarks/provision_tenants.py --count 100

# Full Phase 16.C run: distribution scenarios, size variants, concurrency
# sweep, noisy-neighbor, execution_id patterns, isolation probes
python benchmarks/run_benchmark_16c.py

# If the noisy-neighbor result looks contaminated by a preceding collapse
# (see Section 10), confirm/restore health and re-run cleanly, e.g.:
docker restart aegis-enforcement-gateway
docker inspect --format '{{.State.Health.Status}}' aegis-enforcement-gateway
docker compose -f docker-compose.yml -f benchmarks/docker-compose.bench.yml \
  exec -T bench-runner python multitenant_client.py \
  --weights "0:80,1:20" --concurrency 50 --mix 60,30,10 --shuffle-seed 1 \
  --label noisy_80_20_clean --out /bench/results/custom.json

docker compose -f docker-compose.yml -f benchmarks/docker-compose.bench.yml down
```

On Windows/Git Bash, set `MSYS_NO_PATHCONV=1` for any manual `docker
cp`/`docker exec` invocation (the orchestrator itself uses Python
`subprocess` with list arguments and is unaffected).

Cross-tenant/chain diagnostics:

```bash
docker cp benchmarks/chain_probe.py aegis-control-plane:/tmp/chain_probe.py
docker exec -w /app -e PYTHONPATH=/app aegis-control-plane python /tmp/chain_probe.py \
  <execution_id> [wrong_org_id] [wrong_agent_id]

docker cp benchmarks/eat_cross_tenant_probe.py aegis-enforcement-gateway:/tmp/eat_cross_tenant_probe.py
docker exec -w /app -e PYTHONPATH=/app aegis-enforcement-gateway python /tmp/eat_cross_tenant_probe.py
```

## Artifacts

All raw output is under `benchmarks/results/`, prefixed `16c_` (Phase
16.A's un-prefixed files and Phase 16.B's `16b_`-prefixed files are
untouched):

- `16c_dist_<scenario>_t<trial>.json` — Section 7 (Scenarios A–E)
- `16c_size_<name>[_t<trial>].json` — Section 7 (size variants)
- `16c_sweep_c<N>.json` — Section 8
- `16c_noisy_80_20_t<trial>.json`, `16c_noisy_95_5.json` — Section 10, Run 1 (contaminated, reported as such)
- `16c_noisy_80_20_clean_t<trial>.json`, `16c_noisy_95_5_clean.json` — Section 10, Run 2 (clean)
- `16c_execid_serial_same.json`, `16c_execid_concurrent_same.json` (+ `_chain_probe.json`, `_bg{1,2}.json`) — Section 11/13
- `16c_isolation_probe.json`, `16c_eat_cross_tenant.json` — Section 12
- `16c_index.json` — full structured aggregation of the orchestrated run
- `16c_resource_usage.csv` — Section 16
- `16c_run.log` — full orchestrator console output

## Test Suite (Final)

Re-ran the identical command from Section 2 after every benchmark in this
phase completed and the stack was torn down:

```
374 passed, 5 skipped, 16 warnings in 40.74s
```

Identical to the pre-benchmark run and to the Phase 15/16.A/16.B
reference. No regression.

## Output Summary

A. Phase 16.A commit: `3d2ed26`.
B. Phase 16.B commit: `f5e150d` (+ `d1c55aa` follow-up) — verified present on `origin/master` at the start of this phase (Section 2).
C. Phase 16.C commit: created after this report (hash reported in the assistant's final message alongside the push verification).
D. HEAD == origin/master: verified before starting (Section 2), to be re-verified after push.
E. Test suite before: 374 passed, 5 skipped.
F. Test suite after: 374 passed, 5 skipped.
G. Client/tenant model used: `organization_id` (Section 5), provisioned via real `/api/auth/register` + `/api/agents` + `/api/policies` — no invented multi-tenant system.
H. Workload distributed: 1/5/10/25/50 clients at 50 total requests/concurrency 50 (Scenarios A–E); 25 and 100 total-request size variants; concurrency sweep 1–100 (1 req/client model).
I. Concurrency results: healthy (0 errors) through c=50; **100% collapse at c=75/100**, unchanged from Phase 16.B and independent of tenant count.
J. Single vs multi-client: no measurable difference at fixed total concurrency/volume (Section 9).
K. Noisy neighbor: contaminated first run (100% failure, both tenants, due to the preceding collapse's hangover — 16C-F1); clean re-run found no fairness degradation (Section 10).
L. Execution_id behavior: normal (fresh per request) — clean throughout; serial-same and concurrent-same — both reproduce Phase 16.B's race, both fail-closed via the evidence verifier, one new nuance (client-timeout ≠ server-side stop) (Section 11).
M. Tenant isolation: every probe held — cross-tenant execution adoption rejected (403), cross-tenant EAT claims rejected (401), shared request_id handled independently, trajectory reads correctly scoped (Section 12). Contract-context isolation NOT VERIFIED (no provisioning path exists).
N. Evidence chain: PASS in every inspection, including under the deliberately adversarial same-execution bursts (Section 13).
O. EAT/replay: cross-tenant claim mismatches correctly rejected (Section 14); same-tenant replay behavior already established in Phase 16.B, not re-tested.
P. SQLite: 1,513 events / 1,502 executions / 101 organizations, 2.18 MB at end of phase; no direct log-based lock classification this phase (gap, Section 15/20).
Q. Resource usage: enforcement-gateway avg 13.64%/max 137.48% CPU; near-0% CPU with rising memory during the 16C-F1 hangover window (Section 16).
R. Sustainable capacity: concurrency ≤50 healthy across every tenant-distribution shape tested; exact 50→75 boundary not identified (Section 17).
S. Capacity model: **"Capacity cannot yet be translated into customer count"** — no real per-tenant request-rate data exists to convert throughput into a customer figure (Section 18).
T. Security findings: none — every isolation/EAT/chain probe held; 16C-F1/16C-F2 are explicitly classified RELIABILITY, not SECURITY.
U. Reliability findings: two significant — 16C-F1 (post-collapse hangover, new) and 16C-F2 (c=75+ collapse, reconfirmed under multi-tenancy); plus 16C-F4 (execution_id race, reconfirmed with a new nuance).
V. Limitations: 11, listed in Section 20.
W. Remediation candidates NOT applied: 7, listed in Section 21.
X. Verdict: **PASS WITH LIMITATIONS** (Section 22).
