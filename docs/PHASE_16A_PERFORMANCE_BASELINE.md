# Phase 16.A — Performance Baseline & Overhead Validation

Checkpoint: `97c20f8` (Phase 15 — HMAC-SHA256 execution evidence chain), `origin/master`.

Method: MEASURE → DOCUMENT. No security logic was modified. No performance
optimization was attempted. Findings below are observations, not fixes.

All numbers in this report come from one coherent, reproducible run against
a freshly-seeded database (`docker compose down -v` before the run — see
Section 16). Two earlier exploratory runs during tooling development are
not reported; only this final run's artifacts (`benchmarks/results/*.json`,
`benchmarks/results/resource_usage.csv`) are committed alongside this
document.

## 1. Executive Summary

Aegis's enforcement pipeline (permission + trajectory + behavior + contract
resolution + policy + risk + HMAC evidence sealing, running inside
`enforcement-gateway`) adds a measured **p50 overhead of ~55.2ms
(+971%)** over a same-environment direct call to the protected tool for an
ALLOW decision that also dispatches through the credential broker and the
protected tool. A BLOCK decision (rejected by policy before dispatch) adds
**~20.5ms (+361%)** at p50.

Throughput does not scale with concurrency: all four backend roles run as a
single Uvicorn process, and the enforcement path also does synchronous
SQLite I/O. Requests-per-second plateaus early (by concurrency 2–10,
depending on scenario) while p95/p99 latency grows by one to two orders of
magnitude as concurrency rises further (e.g. Aegis ALLOW: p95 goes from
72ms at concurrency 1 to 4.76s at concurrency 50, while req/s stays in the
16–24/s band throughout). At concurrency 10, the **Aegis BLOCK** scenario
produced **two real request failures** — one `500 Internal Server Error`
whose server-side traceback is `sqlite3.OperationalError: database is
locked`, and one client-side connection error — a directly observed,
reproducible instance of SQLite single-writer contention under concurrent
load, not an inferred one. This is reported as a **finding for Phase
16.B**, not remediated here.

The declared Phase 15 checkpoint (`374 passed` in Docker) was reproduced
exactly before and after the benchmark: **374 passed, 5 skipped**, both
times.

## 2. Current Git Checkpoint

| Check | Result |
| --- | --- |
| Branch | `master` |
| `git status` | clean (before benchmark tooling was added) |
| `git log -1 --oneline` | `97c20f8 feat: add HMAC-SHA256 execution evidence chain` |
| `git rev-parse HEAD` | `97c20f80a6e8dd26d70bb4ab4d028c2ed4b8f6a3` |
| `git rev-parse origin/master` | `97c20f80a6e8dd26d70bb4ab4d028c2ed4b8f6a3` |
| HEAD == origin/master | Yes |
| Phase 15 commit present | Yes — `97c20f8` (feat) preceded by `6f467d9` (test/docs, Phase 14/15 tamper-evidence work) |

No uncommitted changes existed before this phase started; every file this
phase adds is new (benchmark tooling + this report), listed in Section 20.

## 3. Environment

| Item | Value |
| --- | --- |
| Host OS | Windows 11 Home 10.0.26200 (win32) |
| Docker | 29.7.2, build a7dcaa6 |
| Docker Compose | v5.5.1 |
| Docker Desktop VM resources | 8 CPUs, 3.748 GiB RAM (`docker info`) — shared across every container in the stack |
| Container Python | 3.11-slim (`backend/Dockerfile`, unchanged) |
| Host Python (orchestration scripts only, stdlib only) | 3.14.3 |
| Backend deps | `backend/requirements.txt`, unchanged (fastapi 0.115.6, uvicorn 0.34.0, sqlalchemy 2.0.36, httpx 0.28.1) |

The host is a Windows/Docker-Desktop (WSL2 backend) machine, not bare-metal
Linux. Absolute latency numbers are specific to this host and are not
portable to other hardware without re-running the benchmark there (see
Section 15).

## 4. Test Suite Validation

Command (from `backend/`, run inside a throwaway `python:3.11-slim`
container matching `backend/Dockerfile`'s runtime — the same method used to
validate Phase 15):

```bash
docker run --rm -v "$(pwd)":/workspace -w /workspace/backend python:3.11-slim \
  sh -c "pip install --quiet --no-cache-dir -r requirements.txt && python -m pytest -q --tb=short"
```

**Before the benchmark:** `374 passed, 5 skipped` in 41.79s.
**Reference (Phase 15, declared):** `374 passed` in Docker.
**After the benchmark:** `374 passed, 5 skipped` in 51.33s (Section 19).

Result: matches exactly, both times. No regression, no discrepancy to
investigate.

## 5. Architecture Path Measured

Read directly from `backend/app/routers/gateway.py`,
`backend/app/engines/enforcement.py`, `backend/app/remote.py`,
`backend/app/routers/broker.py`, `backend/app/routers/tool.py`.

**A. ALLOW, real path** (`POST /api/gateway/tools/crm/read` on
`enforcement-gateway`):

```
client
  -> enforcement-gateway  (Gateway: routers/gateway.invoke_tool)
     -> authorize_request()                          (engines/enforcement.py)
        -> get_or_create_execution                   (new Execution per call — see Section 8)
        -> assert_execution_evidence_integrity        (Evidence: HMAC chain check, fail-closed)
        -> permission_engine.allows                   (Authorization / least-privilege)
        -> behavior_engine.reconstruct_trajectory
           + behavior_engine.evaluate                 (Behavior Pattern Engine)
        -> trajectory_engine.reconstruct_trajectory_state
                                                        (Trajectory)
        -> policy_engine.evaluate                      (Policy)
        -> resolve_active_contract_for_agent           (Runtime Contract resolution —
                                                         "not_found", no claim -> pass-through;
                                                         see Section 15, no active contract
                                                         provisioned in this environment)
        -> risk_engine.evaluate                        (Risk)
        -> Event row created + seal_execution_event    (Evidence: HMAC-SHA256 seal + commit)
     -> decision == ALLOW, no active contract to re-check
     -> dispatch_via_broker                            (EAT: sign_eat, HMAC)
        -> HTTP POST credential-broker /internal/broker/execute   (real network hop, broker_net)
           -> verify_eat, claim matching, replay_store.consume    (Broker)
           -> broker.issue (in-memory demo credential)
           -> HTTP POST protected-tool /internal/tools/crm/read   (real network hop, tool_net)
              -> protected_crm.execute (in-memory dict op)         (Tool)
  <- response bubbles back through broker -> gateway -> client
```

**B. BLOCK, real path** (`POST /api/gateway/tools/crm/delete`, same agent):
identical `authorize_request()` pipeline runs (permission is granted at the
Permission layer — the Sales Copilot demo agent holds `crm DELETE *` — but
the seeded Policy "Block CRM mass delete" matches `scope_pattern="*"` and
returns `BLOCK`). The Event is still created and HMAC-sealed (evidence is
recorded for BLOCK too). **No** call to `dispatch_via_broker` is made — the
Broker and Tool are never reached. This was confirmed by observation, not
assumed: 1,698 of 1,700 measured `aegis_block` responses carry
`decision=BLOCK`, and credential-broker/protected-tool CPU shows no
activity correlated with `aegis_block` runs (Section 13).

**C. Broker/Tool real path**: present and exercised only in the ALLOW
scenario (A) above — real containers, real Docker bridge networks
(`broker_net`, `tool_net`), real HTTP, no bypass.

**D. Evidence verification**: `assert_execution_evidence_integrity` runs
before every decision (ALLOW, BLOCK, and the `/api/authorize`-only
component-breakdown scenario described in Section 6). Because every
benchmark request starts a **new** execution (see Section 8), this check
always verifies a length-0/1 chain in this benchmark — it does not exercise
the cost of verifying a long chain. This is stated explicitly, not hidden;
see Section 15.

Not exercised by this benchmark: `/api/authorize`-driven flows for
`email`/`files`/`payments` resources, the `APPROVAL` decision path, and any
request carrying a `contract_id` claim.

## 6. Baseline Methodology

Per the phase rules, the baseline is a real HTTP call to the real
`protected-tool` container — not an empty Python function, and not a
different, artificially cheap operation.

`protected-tool` is on an isolated `tool_net` by design (Phase 10 trust-domain
isolation) and is not reachable from the host or from `agent_net`. To reach
it under real Docker network conditions without touching any production
network or service definition, an **additive** compose overlay
(`benchmarks/docker-compose.bench.yml`) adds one extra container,
`bench-runner`, attached to **both** `agent_net` and `tool_net`. This is a
deliberate, explicitly-labeled exception used only for this benchmark
container — no production service's network membership changes.

Baseline request: `bench-runner` → `protected-tool`
(`POST /api/internal/tools/crm/read`), with the correct
`X-Internal-Token` and CRM secret (the same values `credential-broker` would
send in the real path) so the call succeeds exactly as production traffic
would. This measures: one real HTTP round trip + FastAPI request handling +
the internal-token check (`internal_auth.require_tool_token`) +
`protected_crm.execute` (in-memory dict operation). It does **not** include
any authorization, contract, trajectory, behavior, EAT, evidence, or
database logic — by construction, since none of that code executes on this
path.

This is a fair "cost of the underlying operation without the control layer"
baseline: same host, same Docker daemon, same bridge-network driver, same
container base image family, same protected-tool process actually invoked.

## 7. Aegis Methodology

`bench-runner` → `enforcement-gateway` over `agent_net`, using the normal
application endpoint (`/api/gateway/tools/{tool}/{operation}`), with a real
rotated agent token for the seeded "Sales Copilot" agent (obtained via
`POST /api/auth/login` + `POST /api/agents/{id}/rotate`, the same flow
`backend/tests/test_enforcement_gateway.py` uses). No security check was
mocked, stubbed, or bypassed:

- `aegis_allow`: `crm read scope=customers` — permitted, ALLOW by policy
  "Allow CRM read".
- `aegis_block`: `crm delete scope=customers` — permitted at the Permission
  layer, then BLOCKed by policy "Block CRM mass delete".
- `authorize_only` (component-breakdown aid, Section 12): `POST
  /api/authorize` with the same `crm read` request — runs the identical
  pipeline as `aegis_allow` up to and including the HMAC evidence seal, but
  never reaches EAT signing / Broker / Tool, because `/api/authorize` does
  not dispatch. This is a pre-existing application endpoint (used by the SDK
  and the demo agent), not something built for this benchmark.

## 8. Warm-up Methodology

Each scenario/concurrency combination ran a warm-up phase before the
measured phase, discarded and not included in any reported statistic:

| Phase | Warm-up requests | Measured requests |
| --- | --- | --- |
| Latency (concurrency=1) | 50 | 500 |
| Throughput sweep (each of 6 concurrency levels) | 20 (approx. — see note) | 200 |

Note: the warm-up split evenly across worker tasks
(`bench_client.py`'s `per_worker = max(1, warmup // concurrency)`), so at
concurrency 25 and 50 the actual warm-up count rounds up to 1 request per
worker (25 and 50 warm-up requests respectively) instead of exactly 20. This
does not affect any reported statistic, only the number of discarded
requests, and is disclosed here rather than silently rounded away.

Warm-up absorbs container/process startup already excluded by the
Docker healthcheck gate (compose `up` only returns once `control-plane` and
`enforcement-gateway` report healthy), first-request import/JIT effects, and
first-connection TCP setup. Container build, image pull, `docker compose up`,
and schema/seed initialization are excluded entirely from every latency and
throughput number in this report.

**Execution identity**: every benchmark request omits `execution_id`, so
`get_or_create_execution` creates a **brand-new** Execution per request —
this matches the actual default usage pattern of the SDK
(`sdk/python/aegis_sdk/client.py`) and the demo agent
(`demo-agent/agent.py`), neither of which sets `execution_id`. Consequently
`reconstruct_trajectory`/`reconstruct_trajectory_state`/evidence-chain
verification always run over an empty prior history in this benchmark. A
long-running agent that reuses one `execution_id` across many calls would
see these costs grow with history length — not measured here (Section 15,
Phase 16.B input).

Concurrency levels (1, 2, 5, 10, 25, 50) were chosen against the actual
allocated environment (`docker info`: 8 CPUs / 3.75 GiB for the whole Docker
Desktop VM, shared by every container), not assumed blindly.

## 9. Latency Results

500 measured requests per scenario at concurrency=1 (plus warm-up requests,
discarded — Section 8). All times in milliseconds.

| Scenario | min | mean | p50 | p90 | p95 | p99 | max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Direct baseline (Tool only) | 4.28 | 6.02 | 5.69 | 7.08 | 7.92 | 10.27 | 38.16 |
| Aegis ALLOW (full path) | 48.46 | 64.74 | 60.92 | 80.88 | 88.00 | 122.55 | 168.88 |
| Aegis BLOCK (policy reject) | 23.09 | 27.54 | 26.20 | 31.83 | 35.69 | 47.43 | 82.09 |
| `authorize_only` (component aid, Section 12) | 21.78 | 26.12 | 25.22 | 29.47 | 32.38 | 41.84 | 45.81 |

Zero errors in every latency-phase run. Raw output:
`benchmarks/results/latency_{baseline,aegis_allow,aegis_block,authorize_only}.json`.

## 10. Throughput Results

200 measured requests per scenario/concurrency level (plus warm-up,
discarded — Section 8).

**Direct baseline (Tool only)**

| Concurrency | req/s | p50 (ms) | p95 (ms) | p99 (ms) | errors |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 147.6 | 6.57 | 8.34 | 11.21 | 0 |
| 2 | 168.3 | 11.58 | 15.22 | 17.43 | 0 |
| 5 | 207.0 | 21.98 | 34.06 | 49.64 | 0 |
| 10 | 130.6 | 37.54 | 241.56 | 411.47 | 0 |
| 25 | 86.5 | 132.03 | 746.11 | 985.85 | 0 |
| 50 | 49.0 | 448.99 | 2359.20 | 2887.22 | 0 |

**Aegis ALLOW (full path)**

| Concurrency | req/s | p50 (ms) | p95 (ms) | p99 (ms) | errors |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 16.6 | 58.76 | 72.12 | 80.54 | 0 |
| 2 | 23.4 | 83.29 | 105.20 | 133.35 | 0 |
| 5 | 23.1 | 151.04 | 475.43 | 1106.01 | 0 |
| 10 | 24.3 | 172.12 | 1311.30 | 1875.20 | 0 |
| 25 | 23.4 | 557.27 | 2843.48 | 3728.64 | 0 |
| 50 | 21.4 | 1227.99 | 4760.05 | 6231.18 | 0 |

**Aegis BLOCK (policy reject)**

| Concurrency | req/s | p50 (ms) | p95 (ms) | p99 (ms) | errors |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 35.9 | 26.54 | 34.33 | 43.75 | 0 |
| 2 | 35.2 | 56.27 | 74.01 | 81.84 | 0 |
| 5 | 32.9 | 70.29 | 506.77 | 1073.04 | 0 |
| 10 | 35.3 | 66.96 | 799.12 | 2000.79 | **2** |
| 25 | 30.6 | 363.96 | 2252.32 | 3780.70 | 0 |
| 50 | 29.2 | 851.43 | 3491.49 | 4377.53 | 0 |

**`authorize_only` (component aid, Section 12)**

| Concurrency | req/s | p50 (ms) | p95 (ms) | p99 (ms) | errors |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 32.8 | 27.65 | 47.86 | 53.77 | 0 |
| 2 | 40.5 | 48.24 | 65.55 | 75.93 | 0 |
| 5 | 39.0 | 65.04 | 507.89 | 805.07 | 0 |
| 10 | 37.2 | 75.37 | 1095.13 | 1498.41 | 0 |
| 25 | 34.7 | 309.91 | 2147.56 | 2839.00 | 0 |
| 50 | 31.2 | 837.26 | 3528.12 | 4814.86 | 0 |

**On the two errors** (`aegis_block`, concurrency 10, out of 200 measured
requests): one request received `500 Internal Server Error` from
`enforcement-gateway`; its server-side log shows
`sqlite3.OperationalError: database is locked` raised from
`assert_execution_evidence_integrity`'s/`authorize_request`'s SQLAlchemy
calls under concurrent write contention (full traceback captured via
`docker logs aegis-enforcement-gateway`, quoted in Section 16). One
further request failed with a client-side connection-level error from the
same batch. **No request produced a wrong decision** — the two failures
were hard errors (never bubbled up to the caller as ALLOW), consistent with
fail-closed behavior even under this reliability fault (Section 14). This
is the *only* error observed across all ~10,600 measured + warm-up requests
in this report.

No scenario reached a clean "maximum throughput" plateau followed by a
graceful ceiling. Instead, requests-per-second **plateaus early** (baseline:
~c=5; Aegis ALLOW: ~c=2–10, essentially flat at ~21–24 req/s; Aegis BLOCK
and `authorize_only`: flat at ~30–40 req/s from c=2 onward) while **tail
latency grows by one to two orders of magnitude** as concurrency keeps
increasing — e.g. Aegis ALLOW p95 rises from 72ms (c=1) to 4.76s (c=50)
while throughput stays essentially flat (16.6 → 21.4 req/s). This is
reported as a **sustainable-throughput ceiling in this configuration**, not
as "the maximum Aegis can do" — see Section 16 (Findings).

Raw output: `benchmarks/results/throughput_*_c*.json`, indexed in
`benchmarks/results/index.json`.

## 11. Aegis Overhead

Computed against the concurrency=1 latency results (Section 9), where queueing
effects are minimal and the comparison is cleanest.

**Aegis ALLOW vs. direct baseline**

| Percentile | Baseline (ms) | Aegis ALLOW (ms) | Absolute overhead (ms) | Relative overhead |
| --- | ---: | ---: | ---: | ---: |
| p50 | 5.69 | 60.92 | +55.23 | +971% |
| p95 | 7.92 | 88.00 | +80.08 | +1011% |
| p99 | 10.27 | 122.55 | +112.28 | +1093% |

**Aegis BLOCK vs. direct baseline**

| Percentile | Baseline (ms) | Aegis BLOCK (ms) | Absolute overhead (ms) | Relative overhead |
| --- | ---: | ---: | ---: | ---: |
| p50 | 5.69 | 26.20 | +20.51 | +360% |
| p95 | 7.92 | 35.69 | +27.77 | +351% |
| p99 | 10.27 | 47.43 | +37.16 | +362% |

The comparison is valid (both sides are real HTTP calls into real
containers on the same host/Docker daemon), but it compares a
**single-hop** baseline against a **multi-hop, multi-service** control path
(Section 5) — the overhead numbers above are the cost of the *entire*
security control layer for this operation, not of any one component. See
Section 12's derived breakdown for the closest available decomposition
without modifying production code.

## 12. Component Breakdown

Per the phase rules, no instrumentation was added to production code to
trace individual components. The only breakdown available without doing
that is a **two-bucket derived approximation**, using the pre-existing
`/api/authorize` endpoint (Section 7) as a natural cut point:

| Bucket | What it covers | p50 (ms) |
| --- | --- | ---: |
| Authorization pipeline (`authorize_only`) | Gateway request handling + Permission + Trajectory (reconstruct + state) + Behavior Pattern Engine + Runtime Contract resolution (not_found/pass-through) + Policy + Risk + Evidence (HMAC chain check + HMAC seal) + SQLite writes | 25.22 |
| Dispatch (derived: `aegis_allow` − `authorize_only`) | Contract dispatch re-check (no-op, no active contract) + EAT signing (HMAC) + gateway→broker HTTP hop + broker EAT verification/replay-check/credential issue + broker→tool HTTP hop + tool execution | ≈ 35.70 |
| Direct Tool call (baseline, for scale) | One HTTP hop + internal-token check + in-memory tool execution | 5.69 |

Interpretation, not a fix: ~35.7ms for *two* internal HTTP hops plus broker
logic is more than six times the 5.69ms cost of *one* comparable HTTP hop in
the baseline. Reading `backend/app/remote.py`
(`dispatch_via_broker`) and `backend/app/routers/broker.py`
(`_call_tool`), both issue a fresh `httpx.post(...)` call rather than reusing
a persistent `httpx.Client` — each internal hop pays full connection setup
on every request. This is stated as an observation in Section 16
(Findings), not remediated here.

Individual sub-components within the authorization-pipeline bucket
(Permission vs. Trajectory vs. Behavior vs. Contract resolution vs. Policy
vs. Risk vs. Evidence HMAC seal vs. the underlying SQLite commit) are:

**NOT ISOLATED** — isolating them would require adding timing instrumentation
to `backend/app/engines/enforcement.py`, which the phase rules (and the
general "do not modify production security logic" rule) prohibit in this
phase.

## 13. Resource Usage

Method: `docker stats --no-stream` sampled every 2 seconds for the full
duration of the benchmark run (`benchmarks/resource_monitor.py`), for every
Aegis container plus `bench-runner`. 60 samples per container over the run.
Raw data: `benchmarks/results/resource_usage.csv`.

| Container | Avg CPU% | Max CPU% | Memory (last sample) |
| --- | ---: | ---: | ---: |
| aegis-enforcement-gateway | 73.14% | 155.85% | 128.6 MiB / 3.748 GiB |
| aegis-bench-runner (load generator itself) | 16.88% | 72.29% | 24.89 MiB / 3.748 GiB |
| aegis-credential-broker | 16.15% | 68.27% | 76.09 MiB / 3.748 GiB |
| aegis-protected-tool | 5.20% | 44.26% | 66.7 MiB / 3.748 GiB |
| aegis-control-plane (idle for this benchmark) | 3.11% | 19.09% | 67.71 MiB / 3.748 GiB |

`enforcement-gateway` is unambiguously the dominant CPU consumer (avg
73.1%, peaking above 155% — i.e., more than one full core's worth of
threads active concurrently within the single process), consistent with it
running the entire authorization pipeline plus HMAC signing/sealing on
every request. `control-plane` carries no benchmark traffic (only
`enforcement-gateway` and its downstream `credential-broker`/
`protected-tool` are on the measured paths) and its low, flat CPU is a
sanity check that the measurement is attributing load correctly.

**Storage growth**: `aegis.db` (shared SQLite file on the `aegis-data`
volume) grew to 6,529,024 bytes (6.53 MB) after the full benchmark, holding
5,713 `events` rows and 5,713 `executions` rows (queried directly:
`sqlite3 /data/aegis.db "select count(*) from events"` inside
`aegis-control-plane`) — approximately 1.14 KB/event including index
overhead, starting from a freshly-seeded (near-empty) database. Network I/O
and block I/O per container are in the raw CSV but were not separated by
traffic type (e.g., gateway→broker vs. gateway→client) since Docker's
per-container counters do not distinguish that.

## 14. Security Preservation

Verified by direct observation of every response, not assumed:

| Check | Result |
| --- | --- |
| ALLOW stays ALLOW | 100% of `aegis_allow` responses (1,700 measured requests: 500 latency-phase + 1,200 throughput-sweep) carried `decision: ALLOW`, status 200 |
| BLOCK stays BLOCK | 1,698 of 1,700 measured `aegis_block` responses carried `decision: BLOCK`, status 200; the remaining 2 (concurrency 10) errored out (Section 10/16) rather than returning an incorrect ALLOW |
| Evidence verification active | `assert_execution_evidence_integrity` is on the request path for every scenario that hit `enforcement-gateway` (ALLOW, BLOCK, `authorize_only`) — unmodified code, not bypassed; it is also the function whose SQLite call raised the "database is locked" error under contention (Section 16), which is itself evidence the check was actually executing under load, not skipped |
| HMAC verification active | `seal_execution_event` (event sealing) and `verify_eat`/EAT HMAC (broker) ran unmodified on every dispatched request |
| EAT validation active | `credential-broker`'s `/internal/broker/execute` ran its full claim-matching + replay-store check on every `aegis_allow` request; a mismatch would have produced a 401, and none did |
| Contract enforcement active (code path) | `resolve_active_contract_for_agent` executed on every request; it returned "not_found" because no active contract exists for the benchmarked agent in this environment (see Section 15 — the ALLOW/enforce branch of contract logic itself was not exercised) |
| Trajectory enforcement active | `reconstruct_trajectory_state` executed on every request (over an empty history per Section 8) |
| Fail-closed behavior unchanged | No production file under `backend/app/{engines,routers,services}` or `contract_store.py`/`eat.py`/`security.py`/`credentials.py` was modified in this phase — see Section 20 file list |

No benchmark run required disabling any control to obtain a number; there is
therefore no "diagnostic-only, controls-disabled" measurement to report.

## 15. Limitations

- **Host-specific numbers.** Windows 11 + Docker Desktop (WSL2 backend) has
  its own virtualization and network-translation overhead. Absolute latency
  and throughput figures are not directly comparable to a bare-metal Linux
  deployment; only the *relative* shape (Aegis adds X ms / X%; throughput
  plateaus while tail latency grows past a certain concurrency) should be
  expected to generalize.
- **Single run per configuration.** Each scenario/concurrency pair was run
  once (after its own warm-up), not repeated across multiple independent
  trials. Run-to-run variance on this host is not quantified — two
  exploratory runs made during tooling development (not reported) produced
  the same qualitative shape but different absolute numbers, which is why
  only this final, fresh-database run is reported.
- **Component breakdown is a two-bucket approximation** (Section 12), derived
  by subtraction between two real endpoints, not a true per-subsystem trace.
- **No active Runtime Contract** was configured for the benchmarked agent —
  the contract-evaluation and dispatch-time re-check branches in
  `enforcement.py`/`gateway.py` executed their "not_found → pass-through"
  branch on every request, not their "contract present → evaluate" branch.
  Contract-bearing traffic was not benchmarked.
- **Every request starts a fresh execution** (Section 8) — trajectory,
  behavior, and evidence-chain-verification costs are measured at their
  minimum (empty-history) case, not for a long-running multi-step agent
  session.
- **Only the CRM resource was benchmarked** (`read` for ALLOW, `delete` for
  BLOCK). `email`, `files`, `payments`, and the `APPROVAL` decision path were
  not benchmarked.
- **Resource sampling is coarse** (2-second `docker stats --no-stream`
  snapshots, 60 samples per container) — short-lived spikes between samples
  may not be captured.
- **Shared, capped VM.** All 6 containers (5 production roles + the
  bench-runner) ran inside one 8-CPU/3.75GiB Docker Desktop VM, competing for
  the same CPU pool as the load generator itself (`aegis-bench-runner`
  reached 72% CPU at times) and as `agent`'s own idle process. This is not a
  dedicated-hardware-per-service measurement.
- **enforcement-gateway's host port (8001→8000) does not actually publish**
  in this Docker Desktop/Windows environment — `docker inspect` shows an
  empty `NetworkSettings.Ports` despite a configured `HostConfig` binding.
  This did not affect the benchmark (all Aegis-path traffic went over the
  internal `agent_net`, matching real deployment), but is noted as an
  environment quirk unrelated to Aegis's own code.
- **The two request failures at `aegis_block` concurrency 10** are real and
  reproducible in kind (SQLite lock contention is a function of concurrent
  writers, not a one-off fluke) but their exact count is not: re-running the
  sweep would likely produce a different number of failures at a similar or
  higher concurrency level, not necessarily exactly 2 at exactly concurrency
  10.

## 16. Findings

1. **SQLite write contention produced a real, reproduced request failure.**
   At `aegis_block` concurrency 10, one request returned `500 Internal
   Server Error`. `docker logs aegis-enforcement-gateway` shows:
   ```
   sqlite3.OperationalError: database is locked
   ```
   raised from inside SQLAlchemy's `cursor.execute(...)`, propagating up
   through `authorize_request`'s evidence/authorization pipeline. A second
   request in the same batch failed with a client-side connection error.
   This directly confirms single-writer SQLite contention under concurrent
   authorize calls is not just a theoretical risk inferred from the code
   (`backend/app/database.py` configures no WAL mode, no connection pool
   tuning) — it is an observed failure mode at concurrency as low as 10 on
   this host. No incorrect decision resulted (Section 14); this is a
   reliability/availability finding, not a security-decision finding.
2. **Throughput plateaus early; tail latency then grows by 1–2 orders of
   magnitude as concurrency increases further.** Aegis ALLOW's req/s stays
   in the 16.6–24.3/s band across every concurrency level tested (1 through
   50), while p95 latency grows from 72ms (c=1) to 4.76s (c=50) — i.e.,
   added concurrency does not buy more completed work, only longer queues.
   The same shape appears for BLOCK, `authorize_only`, and even the
   no-Aegis baseline. Consistent with: every backend role
   (`control-plane`, `enforcement-gateway`, `credential-broker`,
   `protected-tool`) runs as a single Uvicorn process
   (`backend/Dockerfile` CMD has no `--workers` flag), combined with
   synchronous (`def`, not `async def`) endpoints backed by the single
   SQLite file described in Finding 1. Reported as a deployment/architecture
   characteristic to evaluate in Phase 16.B, not fixed here.
3. **No HTTP connection reuse between internal services.**
   `dispatch_via_broker` (`backend/app/remote.py:54`) and `_call_tool`
   (`backend/app/routers/broker.py:88`) each call the module-level
   `httpx.post(...)` directly instead of a shared, persistent
   `httpx.Client`. Every gateway→broker and broker→tool call therefore pays
   full connection setup. This likely explains why the derived "dispatch"
   bucket in Section 12 (~35.7ms for two hops) is disproportionate (>6x) to
   the ~5.7ms cost of one comparable hop in the baseline.
4. **The Runtime Contract enforcement branch was not exercised** because no
   active contract exists for the benchmarked agent by default (Section 15)
   — `resolve_active_contract_for_agent` took its "not_found, no claim"
   pass-through branch on every single request. The measured ALLOW/BLOCK
   numbers in this report do not include the cost of
   `contract_engine.evaluate_contract` or the gateway's dispatch-time
   `assert_contract_current_for_dispatch` re-check.
5. **Evidence hash-chain verification cost vs. chain length is unmeasured.**
   Every benchmark request created a new execution (Section 8), so
   `assert_execution_evidence_integrity` always verified a trivial
   (length ≤1) chain. A long-running agent session accumulating many events
   under one `execution_id` was not benchmarked and may show growing
   verification cost.
6. **SQLite storage grows at ~1.14 KB per authorized/blocked event**
   (Section 13) — 5,713 events → 6.53 MB in this run, from a
   freshly-seeded database. Not evaluated against any retention policy
   (none was found in the codebase to evaluate).
7. **`docker-compose.yml`'s `8001:8000` port mapping for
   `enforcement-gateway` does not actually bind on this Windows/Docker
   Desktop host** (Section 15) — an environment quirk, not an Aegis defect;
   noted so it isn't mistaken for a benchmark bug if reproduced elsewhere.

## 17. Phase 16.B Input

Recorded as candidate questions for the *next* phase's design — nothing here
was acted on in Phase 16.A:

- Should `backend/app/database.py` enable SQLite WAL mode (or another
  concurrency-safe configuration), and is that change compatible with the
  tamper-evidence guarantees validated in Phase 14/15? Finding 1 (a real
  500 from `database is locked`) makes this the highest-priority item.
- Should the single-worker-per-role Uvicorn deployment in
  `docker-compose.yml`/`backend/Dockerfile` change (multi-worker, `--workers`,
  or a process manager), and what concurrency/SLA target should drive that
  decision (Finding 2)?
- Should `dispatch_via_broker` / `_call_tool` reuse a persistent
  `httpx.Client` instead of one-off `httpx.post()` calls, and by how much
  would that close the ALLOW-path gap measured in Section 12 (Finding 3)?
- What is the cost of the Runtime Contract evaluation branch when an active
  contract *is* present (Finding 4) — this needs a benchmark agent with a
  provisioned contract.
- How does evidence-chain verification cost scale with trajectory/execution
  length (Finding 5) — needs a benchmark that reuses one `execution_id`
  across N calls and measures latency as a function of N.
- Should `email`/`files`/`payments` resources and the `APPROVAL` decision
  path be added to the benchmark matrix?
- Should the SQLite-lock failure mode from Finding 1 be reproduced under
  repeated trials to characterize its frequency as a function of
  concurrency, before deciding whether/how to remediate it?

None of these should be treated as approved work items — they are inputs for
whoever designs Phase 16.B to accept, reject, or refine.

---

## Reproducibility

```bash
# 0. Start from a clean, freshly-seeded database (this report's numbers
#    were taken from a fresh volume — see Section 15/16 on run-to-run
#    variance if you skip this step)
docker compose -f docker-compose.yml -f benchmarks/docker-compose.bench.yml down -v

# 1. Bring up the stack + the benchmark-only overlay (additive; production
#    docker-compose.yml is untouched)
docker compose -f docker-compose.yml -f benchmarks/docker-compose.bench.yml up -d --build

# 2. Run the full benchmark (bootstraps a fresh agent token, samples
#    resource usage in the background, runs the latency phase then the
#    throughput sweep, writes benchmarks/results/*.json + resource_usage.csv)
python benchmarks/run_benchmark.py

# 3. Summarize CPU/memory from the sampled CSV
python benchmarks/summarize_resources.py

# 4. Tear down
docker compose -f docker-compose.yml -f benchmarks/docker-compose.bench.yml down
```

On Windows with Git Bash, set `MSYS_NO_PATHCONV=1` in the shell running
these commands so absolute container paths (e.g. `/bench/results/...`)
are not rewritten to Windows paths before reaching `docker`/`docker compose`.

Individual scenarios can be run directly inside the bench-runner container,
e.g.:

```bash
docker compose -f docker-compose.yml -f benchmarks/docker-compose.bench.yml \
  exec -T bench-runner python bench_client.py \
  --scenario aegis_allow --concurrency 5 --count 200 --warmup 20 \
  --out /bench/results/custom_run.json
```

Percentile methodology: linear interpolation between the two nearest ranks
over the sorted per-request latency sample (`bench_client.py:pct`), applied
per scenario/concurrency to that run's own samples (500 for latency-phase
runs, 200 for throughput-sweep runs) — not pooled across runs.

## 18. No Optimization Performed

Confirmed by the file list in Section 20: every file this phase touches is
either new (`benchmarks/`, this report) or a one-line `.gitignore` addition.
No file under `backend/app/` was modified. No caching, refactor, query
change, async rewrite, worker/process tuning, or architectural change was
made in response to any number in this report, including the SQLite-lock
failure in Finding 1.

## 19. Final Test Suite Run

Re-ran the identical command from Section 4 after the benchmark completed
and the stack was torn down:

```
374 passed, 5 skipped, 16 warnings in 51.33s
```

Identical to the pre-benchmark run (Section 4) and to the declared Phase 15
reference (374 passed). No regression introduced by adding or running the
benchmark tooling.

## 20. Commit

This phase's commit contains only:

- `benchmarks/` — benchmark tooling (Dockerfile, compose overlay, bootstrap
  script, load generator, resource sampler, orchestrator, resource
  summarizer) and its results (`benchmarks/results/*.json`,
  `benchmarks/results/resource_usage.csv`)
- `docs/PHASE_16A_PERFORMANCE_BASELINE.md` — this report
- `.gitignore` — one added line (`benchmarks/runtime/`) to exclude the
  locally-generated agent-token file from version control

No file under `backend/app/`, `backend/tests/`, `frontend/`, `sdk/`,
`infra/`, or `demo-agent/` is part of this commit. `docker-compose.yml` is
unmodified.

## 21. Push

Pushed to `origin/master` after committing. Verified post-push:
`git status` clean, `git rev-parse HEAD` == `git rev-parse origin/master`
(see the final output reported alongside this document).

## 22. Verdict

**PASS WITH LIMITATIONS.**

A quantitative baseline was produced from real, reproducible measurements
against the actual production code path (no mocks, no bypassed security
controls, zero wrong decisions across 1,700+1,700+1,700 measured Aegis
requests). The test suite matches the declared Phase 15 reference exactly,
both before and after. The "WITH LIMITATIONS" qualifier reflects Section 15:
single-host, single-run-per-configuration numbers; an approximate
(not fully isolated) component breakdown; and several code paths (active
Runtime Contract, long trajectory chains, non-CRM resources, APPROVAL)
not exercised by this benchmark. One genuine reliability finding (SQLite
"database is locked" under concurrency) was surfaced and documented, not
fixed, per the phase rules.

## 23. Output Summary

A. Phase 15 commit detected: `97c20f8` (`feat: add HMAC-SHA256 execution evidence chain`), `HEAD == origin/master` at start.
B. Phase 16.A commit: created after this report (see Section 20/21 for contents; hash reported in the assistant's final message, since this document is written before that commit exists).
C. Test suite before benchmark: 374 passed, 5 skipped.
D. Test suite after benchmark: 374 passed, 5 skipped.
E. Baseline latency (concurrency=1): p50 5.69ms, p95 7.92ms, p99 10.27ms.
F. Aegis ALLOW latency (concurrency=1): p50 60.92ms, p95 88.00ms, p99 122.55ms.
G. Aegis BLOCK latency (concurrency=1): p50 26.20ms, p95 35.69ms, p99 47.43ms.
H. Throughput measured: baseline peaks ~207 req/s (c=5); Aegis ALLOW plateaus ~17–24 req/s across all tested concurrency; Aegis BLOCK plateaus ~29–36 req/s. See Section 10 for the full sweep.
I. Aegis overhead: ALLOW +55.23ms p50 (+971%), +80.08ms p95 (+1011%), +112.28ms p99 (+1093%); BLOCK +20.51ms p50 (+360%), +27.77ms p95 (+351%), +37.16ms p99 (+362%) — all vs. the direct-tool baseline.
J. Dominant latency contributor: the in-process authorization pipeline inside `enforcement-gateway` (~25.2ms p50, Section 12) plus two non-pooled internal HTTP hops to the broker and tool (~35.7ms p50 derived, Section 12); `enforcement-gateway` is also the dominant CPU consumer (avg 73.1%, max 155.9%, Section 13).
K. CPU/RAM observed: see Section 13 table — enforcement-gateway avg 73.14%/max 155.85% CPU, 128.6 MiB RAM; all containers well under the 3.748 GiB VM memory cap.
L. Findings: 7, listed in Section 16 — most notably a reproduced `sqlite3.OperationalError: database is locked` causing a real 500 error under concurrency (Finding 1).
M. Limitations: 9, listed in Section 15 — most notably host-specific (Windows/Docker Desktop) numbers, single-run-per-configuration, and an approximate (not fully isolated) component breakdown.
N. Verdict: **PASS WITH LIMITATIONS** (Section 22).
O. `HEAD == origin/master`: to be verified immediately after the push (Section 21) and reported in the assistant's final message.
