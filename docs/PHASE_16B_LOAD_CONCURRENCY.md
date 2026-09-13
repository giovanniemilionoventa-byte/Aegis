# Phase 16.B — Load, Concurrency & Scaling Validation

Checkpoint: `3d2ed26` (Phase 16.A — Performance Baseline, `PASS WITH LIMITATIONS`), `origin/master`.

Method: MEASURE → REPRODUCE → CLASSIFY → DOCUMENT. No security logic,
SQLite configuration, Uvicorn worker model, HTTP client pooling, or
evidence/trajectory/contract/broker logic was modified in this phase.
Findings below are observations from one coherent, fresh-database run;
remediation is explicitly out of scope (Section 21).

## 1. Executive Summary

Phase 16.A's SQLite-contention finding was reproduced systematically, and
two additional, previously-undocumented failure modes were found:

1. **A hard concurrency collapse between c=50 and c=75.** At concurrency
   75 and 100, every one of the three Aegis-path scenarios (`aegis_allow`,
   `aegis_block`, `authorize_only`) failed **100% of requests** with a
   client-side 15-second read timeout — not graceful degradation, a
   complete stall. `enforcement-gateway`'s own CPU during that window was
   **near 0%** (0.13–0.21%), ruling out CPU exhaustion and consistent
   instead with the process's request-handling capacity (thread pool /
   connection queue) being exhausted. The **no-Aegis baseline, run through
   the identical Docker host and CPU pool, produced zero errors at the same
   concurrency levels** (c=75: 26.75 req/s, c=100: 22.18 req/s, both fully
   served) — so this collapse is specific to `enforcement-gateway`, not a
   host/Docker-Desktop resource ceiling.
2. **A genuine, reproduced concurrent-write race in `get_or_create_execution`**
   (`backend/app/engines/enforcement.py`): when multiple concurrent requests
   share a brand-new `execution_id`, more than one can observe "no existing
   Execution row" before either commits, and the losing request(s) crash
   with `sqlite3.IntegrityError: UNIQUE constraint failed: executions.id`
   — an unhandled 500, not caught anywhere in the request path.
3. Despite both failure modes, **the evidence-chain integrity check
   (Phase 15) is doing its job**: in the same-execution race probes, 14–23
   of the concurrent requests that raced past the Execution-creation bug
   were independently caught by `seal_execution_event`'s predecessor check
   and converted into a fail-closed `409 Execution evidence integrity
   failure: chain tip does not match predecessor` — never a silently
   corrupted chain. Every post-burst `chain_probe.py` inspection (real,
   unmodified `assert_execution_evidence_integrity` and
   `reconstruct_trajectory_state`) found **zero duplicate `seq`, zero gaps,
   and a PASSing verifier** on the events that did commit. No burst, trial,
   or sweep run in this entire phase produced an incorrect ALLOW.
4. SQLite contention (`database is locked`) reproduced 4 times out of 36
   repeated trials (5,10,25,50 × 3 trials × 3 scenarios), non-monotonically
   with concurrency and inconsistently across trials at the *same* level —
   one `aegis_allow` c=5 trial had 40/200 requests time out while the other
   two c=5 trials for the same scenario had zero errors.
5. Evidence-chain verification cost showed **no measurable growth** from a
   1-event chain to a 100-event chain on one execution (mean latency
   ~33ms for events 1–10, ~28ms for events 11–100) — within this range, the
   chain-length-dependent cost that Phase 16.A flagged as unmeasured is
   small enough to be within normal run-to-run noise.
6. EAT/replay held up completely under concurrent attack-shaped load: of
   10 concurrent replays of one valid EAT, **exactly 1** was accepted and
   9 were rejected; a sequential replay afterward, a tampered signature,
   and an expired EAT were all rejected. Zero container restarts occurred
   across the entire phase.

Test suite: **374 passed, 5 skipped**, both before and after — unchanged
from Phase 16.A / Phase 15.

**Verdict: PASS WITH LIMITATIONS** for security-decision integrity (Section
7/10); the two failure modes above are **RELIABILITY / AVAILABILITY
findings**, not security failures — no failure, race, timeout, or database
error was observed to produce an unauthorized ALLOW (Section 10).

## 2. Phase 16.A Findings Revisited

| 16.A finding | 16.B disposition |
| --- | --- |
| `sqlite3.OperationalError: database is locked` at `aegis_block` c=10 (1 request) | Reproduced 4 more times across 36 repeated trials (Section 8), at c=5, c=25 (×2), and c=50 — not exclusive to BLOCK, not exclusive to c=10 |
| Aegis ALLOW throughput flat ~17–24 req/s, c=1–50 | Confirmed and extended: flat ~14–24 req/s through c=50, then **total collapse** at c=75/100 (Section 7) — a ceiling 16.A's c≤50 sweep could not see |
| Single Uvicorn process per role | Unchanged; consistent with both the SQLite contention and the c=75 collapse (Section 17) |
| No `httpx.Client` reuse (gateway→broker→tool) | Not modified; documented again as a candidate, not tested further with a bypass (Section 21) |
| Every 16.A request used a new `execution_id` (empty chain/trajectory) | This phase deliberately reused one `execution_id` across concurrent and sequential requests (Sections 8/9/13) to fill this gap |
| Evidence-chain cost vs. chain length unmeasured | Measured up to N=100 in this phase (Section 13); no significant growth found in that range |

## 3. Current Git Checkpoint

| Check | Result |
| --- | --- |
| Branch | `master` |
| `git status` (before this phase's changes) | clean |
| `git log -1 --oneline` | `3d2ed26 perf: establish phase 16A performance baseline` |
| `git rev-parse HEAD` | `3d2ed26e30dcccebfd73bd1b533cb4b388af0450` |
| `git rev-parse origin/master` | `3d2ed26e30dcccebfd73bd1b533cb4b388af0450` |
| HEAD == origin/master | Yes |

## 4. Environment

Identical to Phase 16.A (same host, not changed for this phase):

| Item | Value |
| --- | --- |
| Host OS | Windows 11 Home 10.0.26200 (win32), Docker Desktop (WSL2 backend) |
| Docker | 29.7.2, build a7dcaa6 |
| Docker Compose | v5.5.1 |
| Docker Desktop VM resources | 8 CPUs, 3.748 GiB RAM — shared by every container in the stack, including the bench-runner load generator |
| Container Python | 3.11-slim (`backend/Dockerfile`, unchanged) |
| Backend deployment | one Uvicorn process per role (`control-plane`, `enforcement-gateway`, `credential-broker`, `protected-tool`), no `--workers` flag — unchanged from 16.A |
| Database | one shared SQLite file (`aegis-data` volume, `/data/aegis.db`), no WAL mode, default SQLAlchemy connection handling — unchanged from 16.A |
| Network | `docker-compose.bench.yml` additive overlay (unchanged from 16.A): `bench-runner` attached to both `agent_net` and `tool_net` |
| Resource limits | none beyond the Docker Desktop VM's own 8 CPU / 3.75 GiB cap |

No deployment change was made to chase better numbers, per the phase rules.

## 5. Test Suite Validation

Command (identical to Phase 16.A, from `backend/`, inside a throwaway
`python:3.11-slim` container):

```bash
docker run --rm -v "$(pwd)":/workspace -w /workspace/backend python:3.11-slim \
  sh -c "pip install --quiet --no-cache-dir -r requirements.txt && python -m pytest -q --tb=short"
```

**Before this phase's benchmarks:** `374 passed, 5 skipped` (53.10s).
**Reference (Phase 15/16.A):** `374 passed, 5 skipped`.
**After this phase's benchmarks:** `374 passed, 5 skipped` (49.73s) — see Section 19.

No discrepancy; no test was modified.

## 6. Concurrency Methodology

Fresh database for every measurement in this phase: `docker compose down -v`
was run before `up --build`, so all numbers below start from a freshly
seeded, empty database — unlike some exploratory runs in Phase 16.A, there
is no cross-run database-state ambiguity here (Section 16 of the phase
brief).

Four scenarios (`baseline`, `aegis_allow`, `aegis_block`, `authorize_only` —
same definitions as Phase 16.A Section 5–7) were swept across concurrency
levels **1, 2, 5, 10, 25, 50, 75, 100**. Levels 75/100 used 100 measured
requests (15 warm-up) instead of 200/20 to bound total run time given the
severe latency already visible at c=50 — this is a deliberate, disclosed
sample-size reduction, not a hidden one (Section 20 of the phase brief).
Every other level used 200 measured requests (20 warm-up), matching 16.A.

`aegis_mixed` (new in this phase) alternates `crm read` (ALLOW-eligible)
and `crm delete` (BLOCK by policy) on a single caller-supplied
`execution_id`, used only for the race/trajectory probes in Sections 9/12,
not in the concurrency sweep.

## 7. Concurrency Sweep Results

200 measured requests per level (100 at c=75/100 — see Section 6), single
run per level (repeated-trial data for the SQLite-contention-relevant
levels 5/10/25/50 is in Section 8, not duplicated here).

**Direct baseline (Tool only, no Aegis)**

| Concurrency | req/s | p50 (ms) | p95 (ms) | p99 (ms) | errors |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 148.3 | 6.06 | 9.38 | 11.32 | 0 |
| 2 | 181.7 | 10.85 | 13.98 | 16.37 | 0 |
| 5 | 157.0 | 28.49 | 51.98 | 71.44 | 0 |
| 10 | 127.4 | 35.55 | 228.89 | 497.06 | 0 |
| 25 | 81.5 | 159.29 | 854.86 | 1364.69 | 0 |
| 50 | 50.9 | 439.22 | 2361.72 | 2960.46 | 0 |
| 75 | 26.8 | 1373.60 | 3387.66 | 3544.23 | 0 |
| 100 | 22.2 | 2410.39 | 4143.25 | 4391.27 | 0 |

**Aegis ALLOW (full path)**

| Concurrency | req/s | p50 (ms) | p95 (ms) | p99 (ms) | errors |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 14.5 | 62.16 | 100.23 | 112.70 | 0 |
| 2 | 23.8 | 82.21 | 105.80 | 112.39 | 0 |
| 5 | 19.1 | 166.46 | 714.01 | 1850.87 | 0 |
| 10 | 21.8 | 211.31 | 1774.95 | 2387.87 | 0 |
| 25 | 14.4 | 1043.04 | 4460.66 | 6686.02 | **10** |
| 50 | 21.5 | 1372.35 | 4456.98 | 5668.77 | **1** |
| 75 | 3.3 | 15371.05 | 15407.01 | 15418.06 | **100 (all)** |
| 100 | 6.5 | 15295.65 | 15325.35 | 15343.08 | **100 (all)** |

**Aegis BLOCK (policy reject)**

| Concurrency | req/s | p50 (ms) | p95 (ms) | p99 (ms) | errors |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 35.9 | 25.73 | 37.13 | 53.96 | 0 |
| 2 | 34.6 | 54.68 | 83.18 | 94.11 | 0 |
| 5 | 33.6 | 78.99 | 496.21 | 1080.17 | 0 |
| 10 | 32.3 | 82.36 | 1120.97 | 1989.23 | 0 |
| 25 | 35.3 | 229.73 | 1917.75 | 2956.40 | 0 |
| 50 | 31.9 | 800.31 | 3056.54 | 4438.04 | 0 |
| 75 | 3.3 | 15264.50 | 15298.41 | 15305.65 | **100 (all)** |
| 100 | 6.4 | 15387.41 | 15428.44 | 15434.70 | **100 (all)** |

**`authorize_only`**

| Concurrency | req/s | p50 (ms) | p95 (ms) | p99 (ms) | errors |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 38.4 | 24.58 | 34.42 | 44.57 | 0 |
| 2 | 40.0 | 48.65 | 65.31 | 125.82 | 0 |
| 5 | 38.9 | 50.43 | 300.01 | 1313.61 | 0 |
| 10 | 37.5 | 61.95 | 889.22 | 2997.71 | 0 |
| 25 | 34.4 | 304.75 | 1805.50 | 3878.29 | 0 |
| 50 | 32.0 | 711.89 | 3334.49 | 4631.91 | 0 |
| 75 | 3.3 | 15249.48 | 15278.84 | 15281.96 | **100 (all)** |
| 100 | 6.4 | 15521.72 | 15570.66 | 15596.45 | **100 (all)** |

At c=75 and c=100, every single measured request in all three Aegis-path
scenarios failed with `EXCEPTION:ReadTimeout` at the client's 15-second
timeout (`status_counts: {"-1": 100}` in every one of the six affected raw
result files). The nominal "req/s" and "p50/p95/p99" numbers shown for
those cells are the *timed-out-request* latency (~15.2–15.6s, essentially
the client timeout itself) and throughput of failed attempts — **not a real
served-request measurement**; they are included in the table only to make
the collapse visible, not as usable performance numbers. **A genuine maximum
throughput point was found for the Aegis-path scenarios**: it sits between
c=50 (still serving, 14.4–31.9 req/s depending on scenario) and c=75 (100%
failure) — the exact threshold was not narrowed further in this phase.

Raw output: `benchmarks/results/16b_sweep_*_c*.json`.

## 8. SQLite Contention Results

Per the phase brief, 3 independent trials at concurrency 5, 10, 25, 50, for
`aegis_allow`, `aegis_block`, and `authorize_only` (200 measured requests,
20 warm-up, per trial — 36 trials total, 7,200 measured requests). Each
trial's `enforcement-gateway` Docker logs were tailed for exactly that
trial's `[started_at, finished_at]` window and pattern-matched (see
`benchmarks/log_errors.py`); `database_is_locked`/`http_500` below are
**log-line match counts** in that window (a single failed request can log
more than one matching line — e.g. both a `sqlite3.OperationalError` line
and a `sqlalchemy.exc.OperationalError` line), not a 1:1 incident count.
The client-observed `errors` and `HTTP 500` counts are exact per-request
counts.

| Scenario | Concurrency | Trial | Requests | DB locked (log lines) | HTTP 500 (client) | Other errors |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| aegis_allow | 5 | 1 | 200 | 0 | 0 | **40** (`ReadTimeout`) |
| aegis_allow | 5 | 2 | 200 | 0 | 0 | 0 |
| aegis_allow | 5 | 3 | 200 | 0 | 0 | 0 |
| aegis_allow | 10 | 1–3 | 200 ×3 | 0 | 0 | 0 |
| aegis_allow | 25 | 1 | 200 | 0 | 0 | 0 |
| aegis_allow | 25 | 2 | 200 | 0 | 0 | 0 |
| aegis_allow | 25 | 3 | 200 | 2 | 1 | 1 (`ReadError`) |
| aegis_allow | 50 | 1 | 200 | 0 | 0 | 0 |
| aegis_allow | 50 | 2 | 200 | 2 | 1 | 1 (`ReadError`) |
| aegis_allow | 50 | 3 | 200 | 0 | 0 | 0 |
| aegis_block | 5 | 1–3 | 200 ×3 | 0 | 0 | 0 |
| aegis_block | 10 | 1–3 | 200 ×3 | 0 | 0 | 0 |
| aegis_block | 25 | 1 | 200 | 4 | 2 | 2 (`ReadError`) |
| aegis_block | 25 | 2–3 | 200 ×2 | 0 | 0 | 0 |
| aegis_block | 50 | 1–2 | 200 ×2 | 0 | 0 | 0 |
| aegis_block | 50 | 3 | 200 | 2 | 1 | 0 |
| authorize_only | 5/10/25/50 | 1–3 | 200 ×12 | 0 | 0 | 0 |

**Median / range across the 36 trials**: median errors per trial = 0
(31 of 36 trials had zero errors); range 0–40. Total errors across all
7,200 measured requests: 49 (0.68%), concentrated in 5 of the 36 trials.
**`authorize_only` had zero errors in all 12 of its trials** at every
concurrency level tested, while `aegis_allow`/`aegis_block` (both of which
dispatch through the broker/tool over real network hops in the ALLOW case,
or at minimum carry the same authorization pipeline in the BLOCK case) had
5 trials with errors between them. The sample size (12 trials per scenario)
is too small to assert this difference is caused by the dispatch hop rather
than chance; it is reported as an observation, not a proven cause.

**Answering the phase's specific questions:**
- *At which concurrency does SQLite contention appear?* As low as **c=5**
  (one of three `aegis_allow` trials), and also at c=25 and c=50 — **not
  a clean threshold**; it did not appear at all in 31/36 trials, including
  every trial at c=10.
- *How often does it appear?* 5 of 36 trials (13.9%) in this run; when it
  appears, the number of affected requests ranges from 1 to 40 per trial
  (0.5%–20% of that trial's 200 requests).

Raw output: `benchmarks/results/16b_contention_*_c*_t*.json` and matching
`*_logs.json`.

## 9. Error Classification

Distinct error signatures observed across the entire phase (sweep +
contention + race probes), with example counts:

| Class | Where observed | Example count |
| --- | --- | --- |
| `EXCEPTION:ReadTimeout` (client-side, 15s) | Sweep c=75/100 (all 3 Aegis scenarios, 100% of requests each); contention `aegis_allow` c=5 trial 1 (40/200) | 700 total in sweep collapse cells + 40 |
| `HTTP 500: Internal Server Error` (generic FastAPI handler, no detail leaked to client) | Contention trials (5 incidents); race-probe bursts (14 each) | see Section 8/12 |
| `sqlite3.OperationalError: database is locked` (server log only, not in HTTP response body) | Correlated to 4 of the 5 error-bearing contention trials | Section 8 table |
| `sqlite3.IntegrityError: UNIQUE constraint failed: executions.id` (server log only) | Race probes (Section 12) — concurrent `get_or_create_execution` on a brand-new `execution_id` | 14/burst in both race probes |
| `HTTP 409: Execution evidence integrity failure: chain tip does not match predecessor` / `chain tip mismatch` (from the real evidence verifier, by design) | Race probes only (Section 8/12 bursts never produced this — it requires concurrent writes to an *existing* execution, which the sweep/contention scenarios don't do since every request there gets a fresh execution) | 14 and 9 respectively (Section 12) |
| `EXCEPTION:ReadError` (client-side connection reset) | Contention (3 occurrences); race-probe mixed burst (14) | Section 8/12 |
| EAT/replay rejections (`401 eat_rejected`) | EAT probe only, and only where *intended* (Section 15) — never an unintended rejection of a legitimately fresh request in any other scenario | Section 15 |

No `IntegrityError` outside the two race probes; no evidence-integrity 409
outside the two race probes; no `contract_rejected` observed anywhere (no
active Runtime Contract exists in this environment — unchanged limitation
from 16.A). Every 500 in the sweep/contention data correlates 1:1 with
either a `database is locked` log line or, in the race probes, an
`executions.id` UNIQUE-constraint log line — no unclassified/unexplained
500 was left over.

## 10. Security Decision Integrity

Checked across every scenario in this phase (sweep, contention, and race
probes — 7,200 + 3,200 + 100 + 70 ≈ 10,600 measured requests):

- **ALLOW never appeared where BLOCK was expected, and vice versa.**
  `aegis_block` decision_counts show `BLOCK` in 100% of successful
  responses across every sweep and contention run; `aegis_allow` shows
  `ALLOW` in 100% of successful responses. No 200 response in any
  `aegis_block` run ever carried `decision: ALLOW`.
- **A failed request never produced an ALLOW.** Every failure mode found
  (`ReadTimeout`, `HTTP 500`, `HTTP 409`, `ReadError`) either returned no
  body the client could act on, or returned an explicit rejection
  (`Execution evidence integrity failure`). There is no code path observed,
  in any log or response, where a database error, timeout, or race
  triggered a fallback that returned `ALLOW`. `authorize_request` has no
  `except: return ALLOW`-shaped construct anywhere in
  `backend/app/engines/enforcement.py` (confirmed by re-reading the source,
  not just by absence of an observed failure).
- **The evidence-integrity check itself is what caught the concurrent-write
  race**, converting it into a 409 rather than a corrupted-but-still-served
  decision (Section 12).

No instance in this phase requires a **FAIL** verdict under the
"unauthorized ALLOW" criterion (Section 24 of the phase brief). The two
reproduced bugs (Execution-creation race, c=75 collapse) are **RELIABILITY
findings**: real users would see failed requests (500s, timeouts), not
incorrect authorizations.

## 11. Evidence Chain Concurrency

Directly inspected via `benchmarks/chain_probe.py`, which imports the real,
unmodified `app.services.evidence_verifier.assert_execution_evidence_integrity`
and `app.engines.trajectory.reconstruct_trajectory_state` and runs them
against the live SQLite database (read-only from this phase's perspective;
the only writes come from real HTTP requests) — no reimplementation, no
mocked verifier.

For **both** same-execution concurrent-write probes (Section 12):

| Check | authorize_only burst (30 concurrent, 1 execution) | mixed ALLOW/BLOCK burst (40 concurrent, 1 execution) |
| --- | --- | --- |
| Events actually committed | 2 | 3 |
| Duplicate `seq` found | none | none |
| `seq` gaps found | none | none |
| `previous_evidence_hash` chain | correct for every committed event | correct for every committed event |
| `evidence_hash` recomputation matches stored value | yes, for every event | yes, for every event |
| `execution.evidence_chain_tip` matches last event's hash | yes | yes |
| Real verifier (`assert_execution_evidence_integrity`) result | **PASS** | **PASS** |
| Verifier correctly rejects a genuinely broken chain | See below | See below |

The chain never became corrupted in this phase because the verifier
**actively refused** the writes that would have corrupted it (Section 12) —
this phase did not need to hand-corrupt a chain to prove the verifier
fails on a bad chain; it observed the verifier doing exactly that job
live, 23 times across the two bursts (14 + 9). As additional confirmation
that the verifier still passes on a large, legitimately-built chain: the
100-event sequential chain in Section 13 also verified as **PASS**.

This phase did not additionally hand-craft a tampered row and re-run the
verifier as a separate diagnostic (Phase 14/15 already covered that
adversarial case in `backend/tests/test_phase14_tamper_evident.py` /
`test_phase15_tamper_evident.py`, which still pass — Section 5/19); doing
so again here would have been redundant with those tests rather than new
information about *concurrency*.

## 12. Execution Sequence / Race Analysis

Two bursts of concurrent requests were sent to the real
`enforcement-gateway`, each pinned to one brand-new `execution_id` via
`bench_client.py --execution-id` (Section 6):

**A. `authorize_only`, 30 concurrent requests, 1 fresh execution:**

| Outcome | Count |
| --- | ---: |
| 200 (committed, ALLOW) | 2 |
| 409 (evidence integrity rejected: "chain tip does not match predecessor" / "chain tip mismatch") | 14 |
| 500 (unhandled `sqlite3.IntegrityError: UNIQUE constraint failed: executions.id`) | 14 |

**B. `aegis_mixed` (alternating `crm read`/`crm delete`), 20 concurrent
workers / 40 requests, 1 fresh execution:**

| Outcome | Count |
| --- | ---: |
| 200 (committed, all happened to be ALLOW/`read`) | 3 |
| 409 (evidence integrity rejected: "chain tip does not match predecessor") | 9 |
| 500 (same `IntegrityError` as above) | 14 |
| Client-side `ReadError` (connection reset) | 14 |

**Root cause, read directly from the source** (not merely inferred from the
traceback): `get_or_create_execution`
(`backend/app/engines/enforcement.py`) does
`db.query(Execution).filter(id==execution_id).first()`; if that returns
`None` it does `db.add(Execution(id=execution_id, ...))`. Under real
concurrency, more than one request's session can observe `None` before any
of them commits — SQLite's own write-lock only serializes the actual
`INSERT`, not the preceding `SELECT`, so the later `INSERT`(s) hit the
primary-key `UNIQUE` constraint and raise `IntegrityError`, which is not
caught anywhere between `get_or_create_execution` and the FastAPI
exception middleware, producing a bare 500. This is a genuine
**Time-Of-Check-To-Time-Of-Use (TOCTOC) race**, reproduced directly, not
inferred.

Separately, `seal_execution_event`'s predecessor lookup
(`backend/app/services/evidence_verifier.py`) queries "the latest event for
this execution" to compute `previous_evidence_hash`; two concurrent
requests that both pass the Execution-creation step can both read the same
predecessor before either commits their own new event, so the second one to
attempt `execution.evidence_chain_tip` comparison finds it stale and raises
`EvidenceIntegrityError("chain tip does not match predecessor")` — this is
the **409** path, and it is the system correctly detecting the race and
refusing to write, not a bug.

**Direct answers to the phase's specific race questions:**
- *Same `seq` twice?* No — 0 duplicates in either post-burst
  `chain_probe.py` inspection.
- *`seq` out of order?* No — 0 gaps in either inspection.
- *Same `event_hash` twice?* No — every committed event's hash is unique
  and recomputation matches.
- *Wrong `previous_evidence_hash`?* No, for events that committed. For
  events that *would have* had a wrong one, the system raised 409 instead
  of committing them — that is the mechanism working as designed, evidenced
  directly rather than assumed.
- *Wrong `evidence_head`/chain tip?* No, for the final committed state in
  both bursts.

**The current model's assumption, stated plainly and not modified:** the
production code (`get_or_create_execution`, `seal_execution_event`) assumes
an `execution_id` is used **serially** by one caller at a time. This
benchmark deliberately violated that assumption (multiple concurrent
requests sharing one `execution_id`) to see what happens; the SDK/demo-agent
default (a fresh `execution_id` per call, Phase 16.A Section 8) never
triggers this path at all. This is documented here as an assumption, not
converted into an architectural change.

## 13. Long Execution Evidence Scaling

100 **sequential** (concurrency=1 — no race, per the phase brief's
instruction not to conflate this with the concurrent case) real
`/api/authorize` requests, all on one fresh `execution_id`, via
`benchmarks/chain_growth.py`. Zero errors across all 100 requests; final
`chain_probe.py` on that execution: **PASS**, 0 duplicate `seq`, 0 gaps.

| Events in execution (at time of that request) | Latency (ms) |
| ---: | ---: |
| 1 | 102.87 |
| 10 | 24.99 |
| 25 | 27.83 |
| 50 | 28.31 |
| 75 | 29.55 |
| 100 | 29.49 |

Windowed means: requests 1–10 averaged **33.48ms**, requests 11–50
averaged **27.94ms**, requests 51–100 averaged **27.91ms**. The first
request is the slowest (a fresh Execution row insert plus, plausibly, cold
connection/JIT effects — not isolated further, consistent with the
component-breakdown caveats in Phase 16.A Section 12); from request 10
onward, latency is **flat within noise** as the chain grows 10x (10 → 100
events). At this scale, `assert_execution_evidence_integrity`'s per-event
HMAC recomputation over the whole prior chain (`backend/app/services/
evidence_verifier.py`) does not show the linear-growth signature one might
expect from an O(N) walk — either the per-event HMAC/DB-row cost is small
enough that 100 iterations don't move the needle relative to the rest of
the pipeline (SQLite indexed lookup by `execution_id`, network/ASGI
overhead, etc.), or the growth becomes visible only well beyond N=100. This
phase measured only up to N=100, per the brief; larger N is a candidate for
Phase 16.C (Section 22).

Raw output: `benchmarks/results/16b_chain_growth.json`,
`16b_chain_growth_chain_probe.json`.

## 14. Trajectory Under Load

`reconstruct_trajectory_state` (`backend/app/engines/trajectory.py`) filters
`authorized_actions` to `decision == "ALLOW"` only
(`is_progress_decision`), independent of any concurrency concern — this was
confirmed directly against the live data from the mixed ALLOW/BLOCK
same-execution burst (Section 12.B): all 3 committed events in that burst
were `ALLOW`, and `authorized_count` (3) equals `total_events` (3) only
because no `BLOCK` happened to survive the race in that particular burst —
by construction, had a `BLOCK` committed, it would appear in `events` but
be excluded from `authorized_actions` (the code does this by a plain
list-comprehension filter on `decision`, not by any lock or transaction
that could itself race). No duplicated or out-of-order step was observed
in either burst's trajectory (`decisions_in_order` matches `seq` order
exactly in both `chain_probe.py` outputs). Consistent with the design: a
`BLOCK` cannot advance authorized progress because it is filtered out
entirely, not because of a runtime check that could be bypassed under load.

## 15. EAT / Replay Under Load

Run via `benchmarks/eat_replay_probe.py` (real `app.eat.sign_eat`, executed
inside `enforcement-gateway`, POSTing to the real `credential-broker`
`/internal/broker/execute` — no bypass, no mocked verification):

| Test | Result |
| --- | --- |
| 10 concurrent replays of one valid EAT | **1** accepted (200), **9** rejected (401 `eat_rejected`) — exactly one JTI redemption, as expected |
| Same EAT replayed again, sequentially, after the burst | Rejected (401 `eat_rejected`) — the replay window does not reset |
| Tampered signature (one flipped byte) | Rejected (401 `eat_rejected`) |
| Expired EAT (`ttl_seconds=-5`) | Rejected (401 `eat_rejected`) |

No EAT-related error occurred anywhere else in this phase (sweep,
contention, race probes) other than by design in this probe. `claim_binding`
(org/agent/execution/request/scope/param_hash/contract fields, all checked
in `backend/app/routers/broker.py:execute`) was exercised implicitly by
every successful `aegis_allow` dispatch across the whole phase and never
produced a false rejection or false acceptance.

## 16. Broker/Tool Scaling

For the ALLOW path specifically, `credential-broker`/`protected-tool`
resource usage tracked with `enforcement-gateway`'s request volume but at
much lower CPU (Section 17) — consistent with them doing far less work per
request (EAT verification + replay-store lookup + an in-memory demo
credential issue, and a trivial in-memory dict operation, respectively) than
`enforcement-gateway`'s full authorization pipeline. No error in this phase
was attributed to `credential-broker` or `protected-tool` specifically by
the log classification (Section 9) — every classified error (`database is
locked`, `IntegrityError`, the 409 evidence-integrity rejections) came from
`enforcement-gateway`'s own logs. The c=75/100 collapse (Section 7) was not
separately diagnosed inside `credential-broker`/`protected-tool` because
`enforcement-gateway` itself never got far enough to dispatch during that
collapse (100% `ReadTimeout` before a response, consistent with the stall
being in `enforcement-gateway`'s own request handling, before it would call
the broker). The Phase 16.A finding that `dispatch_via_broker`/`_call_tool`
open a fresh, non-pooled HTTP connection per call is **not** re-tested with
a pooling change here (out of scope, Section 21) — it remains a documented
candidate, not a demonstrated security issue.

## 17. Resource Saturation

`docker stats --no-stream` sampled every 2 seconds for the whole phase (345
samples per container; `benchmarks/results/16b_resource_usage.csv`):

| Container | Avg CPU% | Max CPU% | Memory (last sample) |
| --- | ---: | ---: | ---: |
| aegis-enforcement-gateway | 33.41% | 160.88% | 174.4 MiB / 3.748 GiB |
| aegis-bench-runner (load generator) | 8.48% | 86.89% | 2.4 MiB / 3.748 GiB |
| aegis-credential-broker | 6.31% | 60.34% | 95.1 MiB / 3.748 GiB |
| aegis-control-plane (idle for this benchmark) | 3.33% | 82.98%* | 67.8 MiB / 3.748 GiB |
| aegis-protected-tool | 1.79% | 59.65% | 69.3 MiB / 3.748 GiB |

\* `control-plane`'s one high sample is attributed to Docker's own stats
collection contending with the other five containers on the same 8-CPU
pool during a high-concurrency window, not to any request it served (it
carries no benchmark traffic in this phase, same as 16.A).

**During the c=75/100 collapse specifically** (Section 7), `enforcement-
gateway` CPU measured 0.13%–0.21% (one sample at 10.14% right at the start
of the window) — i.e., **not CPU-bound**. Combined with the "all requests
time out at exactly the client's 15s timeout, zero served" pattern, this is
**consistent with** the process's request-handling capacity (most likely
the ASGI sync-endpoint thread pool, or the OS/SQLite connection queue behind
it) being exhausted rather than the CPU — this phase did not instrument the
thread pool directly, so this is stated as "consistent with," not proven.
Database size grew to 14,094,336 bytes (13.44 MB) holding 12,546 `events` /
12,442 `executions` rows by the end of the phase (~1.12 KB/event, in line
with Phase 16.A's ~1.14 KB/event). **Zero container restarts** occurred
across the entire phase (`docker inspect --format {{.RestartCount}}` == 0
for every container) — the collapse and the IntegrityError races degraded
request handling but never crashed a process.

Saturation type by scenario, stated with "consistent with" where inference
is involved:
- **c=75/100 collapse (all three Aegis scenarios): consistent with
  process/worker-bound** (thread-pool or connection-queue exhaustion), NOT
  CPU-bound (near-zero CPU) and NOT memory-bound (memory flat, well under
  cap).
- **SQLite contention bursts (Section 8): database-bound**, directly
  confirmed by the `database is locked` log lines, not merely inferred.
- **Gradual latency growth from c=1 to c=50 (all scenarios, including
  baseline): consistent with** a mix of process/worker-bound queuing
  (single Uvicorn process) and database-bound serialization — not
  separable further without instrumentation this phase did not add.

## 18. Repeated Trial Variance

The 36 SQLite-contention trials (Section 8) are this phase's primary
repeated-trial evidence: **median 0 errors/trial, range 0–40**, and the
presence/absence of errors did not correlate cleanly with concurrency level
(errors appeared at c=5 but not c=10, for example). This variance is
reported, not smoothed over: **the same configuration (scenario +
concurrency) produced both a clean run and a 20%-error-rate run** across
its three trials in one case (`aegis_allow` c=5). No other benchmark in this
phase (sweep, race probes, chain growth, EAT probe) was repeated across
multiple independent trials — time budget was concentrated on the
SQLite-contention question per the phase's stated priority (Section 15 of
the brief: "per gli altri benchmark, ripetere... quando il tempo lo
permette" — it did not, given the scope already covered).

## 19. Findings

Numbered independently of Phase 16.A's findings (see Section 2 for the
crosswalk):

1. **[RELIABILITY] Hard concurrency collapse between c=50 and c=75** for
   every Aegis-path scenario (ALLOW, BLOCK, `authorize_only`): 100% request
   failure (client `ReadTimeout` at 15s) with `enforcement-gateway` CPU
   near 0% during the collapse. The no-Aegis baseline, on the identical
   host, did not collapse at the same concurrency (Section 7).
2. **[RELIABILITY] Reproduced, source-confirmed race in
   `get_or_create_execution`**: concurrent requests sharing a brand-new
   `execution_id` can both observe "no existing row," and the loser(s)
   crash with an unhandled `sqlite3.IntegrityError: UNIQUE constraint
   failed: executions.id` (Section 12).
3. **[POSITIVE / SECURITY-RELEVANT] The evidence-chain integrity check
   (Phase 15) actively caught concurrent-write races** in
   `seal_execution_event` and converted them into fail-closed `409`
   rejections rather than a corrupted chain, 23 times across two bursts
   (Section 11/12). No corrupted chain, no duplicate `seq`, no gap was
   observed anywhere in this phase.
4. **[RELIABILITY] SQLite `database is locked` reproduced 4 additional
   times** across 36 trials, non-monotonically with concurrency (as low as
   c=5), confirming Phase 16.A's single observation was not a fluke, while
   also showing it is not deterministic at any tested level (Section 8).
5. **[INFORMATIONAL] No measurable evidence-chain-verification cost growth**
   was found from N=1 to N=100 events on one execution (Section 13) —
   Phase 16.A's "unmeasured" gap is now measured at this scale and shows
   no red flag, but larger N was not tested.
6. **[POSITIVE] EAT/replay protections held under concurrent load**: exactly
   one of ten concurrent replays of a valid EAT was accepted; tampering and
   expiry were both rejected (Section 15).
7. **[INFORMATIONAL] Zero container restarts** across the whole phase
   despite the collapse and the IntegrityError races (Section 17) — failures
   degraded request handling, not process stability.
8. **[INFORMATIONAL] `authorize_only` had zero errors in all 12 of its
   contention trials**, while `aegis_allow`/`aegis_block` had 5 error-bearing
   trials between them (Section 8) — reported as an observation; the sample
   size does not support a causal claim about the broker/tool dispatch hop.

## 20. Limitations

- Single fresh-database run for the sweep and race/growth/EAT probes (not
  repeated); only the SQLite-contention section (Section 8) has repeated
  trials, per the phase's own prioritization.
- c=75/100 used 100 measured requests (15 warm-up) instead of 200/20, to
  bound total run time given the severity of degradation already visible
  at c=50 — disclosed in Section 6, not hidden.
- The c=75/100 collapse's root cause is stated as "consistent with"
  thread-pool/connection-queue exhaustion, not proven — this phase did not
  instrument Starlette's thread pool or the SQLite connection layer
  directly, per the "no architectural changes" rule.
- `database_is_locked`/`http_500`/etc. counts from `log_errors.py` are
  **log-line match counts** in a time window, not de-duplicated incident
  counts; the client-observed `error_count`/`HTTP 500` counts are the
  reliable per-request tallies and are what the report's "Requests" and
  error totals are based on.
- The race probes (Section 12) deliberately violate the SDK's own usage
  pattern (a shared `execution_id` across concurrent callers) to make the
  race observable; this is not how the SDK or demo agent use the API by
  default (Phase 16.A Section 8), so the *frequency* with which a real
  deployment would hit this race depends entirely on whether/how often a
  caller reuses one `execution_id` concurrently — not established here.
- Host-specific numbers (Windows + Docker Desktop/WSL2), as in Phase 16.A —
  not portable to bare-metal Linux without re-running there.
- No active Runtime Contract was configured (same limitation as 16.A) — the
  contract-evaluation branch remains unexercised.
- Chain-growth (Section 13) was tested only to N=100; whether growth
  becomes visible at N=1,000 or N=10,000 is unknown.
- Broker/Tool-specific saturation (Section 16) could not be separately
  diagnosed for the c=75/100 collapse because `enforcement-gateway` itself
  never got far enough to dispatch.

## 21. Remediation Candidates (NOT applied in this phase)

Listed as problems + possible directions only — no code, configuration, or
architecture was changed in response to any of these:

1. **`get_or_create_execution` race (Finding 2).** Possible directions: a
   database-level `INSERT ... ON CONFLICT DO NOTHING` / upsert instead of
   check-then-insert, or catching `IntegrityError` and re-querying, or
   documenting that `execution_id` reuse across concurrent callers is
   unsupported and should be rejected explicitly (e.g., a 409 instead of a
   bare 500) rather than silently racing.
2. **c=75/100 collapse (Finding 1).** Possible directions: multiple Uvicorn
   worker processes per role, an ASGI thread-pool size increase, or
   converting the sync `def` endpoints to `async def` with a genuinely
   async DB driver — any of these needs its own before/after benchmark, not
   a guess.
3. **SQLite `database is locked` (Finding 4, carried from 16.A).** Possible
   directions: WAL mode, a single-writer queue in front of SQLite, or a
   different database engine for concurrent workloads — explicitly
   deferred per Section 17 of the phase brief.
4. **No persistent `httpx.Client` for gateway→broker→tool (carried from
   16.A).** Possible direction: connection pooling/reuse — not tested
   further here since Section 13 of the phase brief scoped Broker/Tool
   work to documentation only, absent a demonstrated security issue.
5. **Unhandled 500 instead of a clean rejection.** Both the Execution-race
   IntegrityError and (separately, hypothetically) any future unhandled
   exception in the authorize pipeline currently surface as a generic
   500 with no machine-readable "retry" signal — a possible direction is a
   narrower `try/except IntegrityError` around `get_or_create_execution`
   that turns the race into a defined, documented error response instead of
   an opaque 500.

## 22. Input for Phase 16.C

- Narrow the c=50→c=75 collapse boundary (e.g., test c=55, 60, 65, 70) to
  find the actual threshold, and instrument (read-only) whether it is the
  ASGI thread pool, the SQLite connection layer, or something else —
  without yet changing either.
- Repeat the sweep and race/growth probes across multiple independent
  trials (this phase only repeated the contention section) to get
  variance data for the rest of the matrix.
- Extend the chain-growth probe well past N=100 (e.g., 500, 2,000) to see
  whether the flat cost observed here eventually turns into visible O(N)
  growth.
- Quantify how often real callers (if any exist beyond the SDK default)
  reuse one `execution_id` concurrently, to size how much the
  Execution-creation race (Finding 2) actually matters in practice.
- Design a controlled A/B (still without changing production code — e.g.,
  a separate branch or config flag evaluated in isolation) to test whether
  the Remediation Candidates in Section 21 would close the c=75 collapse
  and the SQLite lock findings, before Phase 16.C decides whether to adopt
  any of them.

---

## Reproducibility

```bash
# Fresh database, matching this report's methodology
docker compose -f docker-compose.yml -f benchmarks/docker-compose.bench.yml down -v
docker compose -f docker-compose.yml -f benchmarks/docker-compose.bench.yml up -d --build
python benchmarks/bootstrap.py

# Full Phase 16.B run: sweep, contention trials, race probes, chain growth, EAT/replay
python benchmarks/run_benchmark_16b.py

docker compose -f docker-compose.yml -f benchmarks/docker-compose.bench.yml down
```

On Windows/Git Bash, set `MSYS_NO_PATHCONV=1` for any manual `docker
cp`/`docker exec` invocation of `chain_probe.py` or `eat_replay_probe.py`
(the orchestrator itself calls `docker` via Python `subprocess` with list
arguments, so it is not affected by shell path-rewriting).

To inspect any single execution's chain/trajectory directly:

```bash
docker cp benchmarks/chain_probe.py aegis-control-plane:/tmp/chain_probe.py
docker exec -w /app -e PYTHONPATH=/app aegis-control-plane python /tmp/chain_probe.py <execution_id>
```

Percentile methodology: identical to Phase 16.A — linear interpolation
between the two nearest ranks over each run's own sorted per-request
latency sample.

## Artifacts

All raw output is under `benchmarks/results/`, prefixed `16b_` to keep it
separate from the Phase 16.A artifacts (prefixed `latency_`/`throughput_`
with no phase tag, predating this convention) already committed in that
phase — nothing from Phase 16.A was overwritten:

- `16b_sweep_<scenario>_c<N>.json` — Section 7
- `16b_contention_<scenario>_c<N>_t<trial>.json` / `..._logs.json` — Section 8
- `16b_race_<name>_burst.json` / `_chain_probe.json` / `_followup.json` — Sections 11/12
- `16b_chain_growth.json` / `_chain_probe.json` — Section 13
- `16b_index.json` — full structured aggregation of every run above
- `16b_resource_usage.csv` — Section 17
- `16b_run.log` — full orchestrator console output

## 23. Test Suite (Final)

Re-ran the identical command from Section 5 after every benchmark in this
phase completed and the stack was torn down:

```
374 passed, 5 skipped, 16 warnings in 49.73s
```

Identical to the pre-benchmark run (Section 5) and to the Phase 15/16.A
reference. No regression.

## 24. Verdict

**PASS WITH LIMITATIONS** for security-decision integrity (Section 10) —
no unauthorized ALLOW, no corrupted evidence chain, no trajectory
violation, and EAT/replay protections held under concurrent load. The
security logic is unchanged and behaved correctly under every load
condition this phase produced, including two genuine reliability bugs.

Separately and explicitly: this phase surfaces **two RELIABILITY findings**
(the c=75 collapse, and the `get_or_create_execution` race) that are
real and reproduced, not hypothetical — they are not security failures
under the phase's own distinction (Section 24 of the brief: "SQLite
database locked → reliability finding" vs. "database locked → fallback
ALLOW non autorizzato → security failure"). No fallback-to-ALLOW was ever
observed.

## 25. Output Summary

A. Phase 16.A commit: `3d2ed26` (`perf: establish phase 16A performance baseline`).
B. Phase 16.B commit: created after this report (hash reported in the assistant's final message alongside the push verification).
C. HEAD == origin/master: verified before starting (Section 3) and to be re-verified after push (Section 26 workflow).
D. Test suite before: 374 passed, 5 skipped.
E. Test suite after: 374 passed, 5 skipped.
F. Concurrency tested: 1, 2, 5, 10, 25, 50, 75, 100 (sweep); 5, 10, 25, 50 ×3 trials (contention).
G. Throughput per concurrency: Section 7 tables — baseline up to 181.7 req/s (c=2); Aegis ALLOW/BLOCK/authorize_only plateau in the 14–40 req/s band through c=50, then **100% failure** at c=75/100.
H. p50/p95/p99: Section 7 tables (per scenario/level).
I. SQLite lock frequency: 4 of 36 contention trials showed `database is locked` log lines (Section 8), plus the one from Phase 16.A (5 total observed occurrences of this specific error across both phases).
J. Error classification: Section 9 — `ReadTimeout` (collapse), `database is locked` (contention), `IntegrityError` on `executions.id` (race), evidence-integrity `409` (race, by design), `ReadError` (connection reset).
K. Race conditions found: yes — a real, reproduced `get_or_create_execution` TOCTOC race on `execution_id` (Section 12); no `seq`/hash-level corruption resulted (caught by the evidence verifier instead).
L. Evidence chain integrity result: **PASS** in every inspection performed (two race-probe bursts + the 100-event growth chain) — 0 duplicate seq, 0 gaps, verifier passes; verifier also independently confirmed to reject the races it caught (409s).
M. Evidence verification scaling with N events: flat within noise from N=1 to N=100 (Section 13) — no growth signature at this scale.
N. Trajectory integrity result: correct — `authorized_actions` excludes BLOCK by construction, confirmed live under the mixed-decision race burst (Section 14).
O. EAT/replay result: exactly 1/10 concurrent replays accepted; tamper and expiry both rejected (Section 15).
P. Resource saturation: enforcement-gateway avg 33.4%/max 160.9% CPU; during the c=75/100 collapse specifically, CPU was near 0% — consistent with process/worker-bound saturation, not CPU-bound (Section 17).
Q. Principali bottleneck osservati: enforcement-gateway's single-process/sync-endpoint request handling (collapse at c=75+) and the shared SQLite file (contention at various levels) — the same two components flagged in 16.A, now with a reproduced hard failure mode for the first one.
R. Security findings: none rise to FAIL — no unauthorized ALLOW, chain integrity held, EAT/replay held.
S. Reliability findings: two — c=75 collapse (Finding 1), Execution-creation race (Finding 2) — plus reproduced SQLite contention (Finding 4).
T. Limitations: 9, listed in Section 20.
U. Remediation candidates NOT applied: 5, listed in Section 21.
V. Verdetto finale: **PASS WITH LIMITATIONS**.
