"""Phase 16.C multi-tenant load generator.

Runs INSIDE aegis-bench-runner (agent_net -> enforcement-gateway). Loads a
pool of independently-provisioned tenants (see provision_tenants.py, which
uses only the real, public /api/auth/register + /api/agents +
/api/policies endpoints -- no new multi-tenant logic) and distributes a
workload across M of them, bounded by one GLOBAL concurrency limit (a
single asyncio.Semaphore shared by every tenant's requests) so "--concurrency
50" means 50 requests in flight across the whole simulated client
population at once, not 50 per tenant.

"Distributed logically, not physically": every tenant's requests originate
from this one bench-runner process/container. This models many distinct
Aegis tenants sharing one Aegis instance; it does not model many physical
client machines, and this script does not claim otherwise.

Mix (--mix "60,30,10", ALLOW/BLOCK/APPROVAL percentages) is realized as a
repeating pattern applied to the global request stream:
  ALLOW    -> POST /api/gateway/tools/crm/read     (full path: gateway ->
              authorize -> broker -> tool; real dispatch)
  BLOCK    -> POST /api/gateway/tools/crm/delete   (permission-layer BLOCK;
              the tenant is never granted crm DELETE by provision_tenants.py)
  APPROVAL -> POST /api/authorize {email SEND external} (decision-only --
              the gateway's TOOL_MAP only supports "crm", so an APPROVAL-
              eligible email/SEND request cannot go through the gateway's
              broker/tool dispatch in this codebase; this is a pre-existing
              limitation of the routes, not something this script works
              around)

Every request uses a fresh execution_id (execution_id omitted) by default --
the "normal" model from Phase 16.B Section 10. Use bench_client.py directly
(with --token and --execution-id) for the serial-same/concurrent-same
execution_id models against one specific tenant.

Distribution modes:
  --clients M --requests-per-client K   (even split, tenants[0:M])
  --weights "0:40,3:10"                 (explicit tenant_index:count pairs,
                                          for noisy-neighbor scenarios)

Usage:
  python multitenant_client.py --clients 50 --requests-per-client 1 \
      --concurrency 50 --mix 60,30,10 --out /bench/results/16c_x.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

GATEWAY_URL = "http://enforcement-gateway:8000"
TENANTS_FILE = Path("/bench/runtime/tenants_16c.json")

_MAX_ERROR_SAMPLES = 20


def _load_tenants() -> list[dict]:
    return json.loads(TENANTS_FILE.read_text(encoding="utf-8"))


def _mix_percentages(mix: str) -> dict[str, int]:
    parts = [int(p) for p in mix.split(",")]
    if len(parts) == 2:
        allow_pct, block_pct = parts
        approval_pct = 0
    elif len(parts) == 3:
        allow_pct, block_pct, approval_pct = parts
    else:
        raise ValueError("--mix must be 'ALLOW,BLOCK' or 'ALLOW,BLOCK,APPROVAL' percentages")
    if allow_pct + block_pct + approval_pct != 100:
        raise ValueError("--mix percentages must sum to 100")
    return {"ALLOW": allow_pct, "BLOCK": block_pct, "APPROVAL": approval_pct}


def _interleaved_kinds(total: int, pct: dict[str, int]) -> list[str]:
    """Proportional round-robin: e.g. mix 60/30/10 over 15 requests still
    yields ~9/4/2 ALLOW/BLOCK/APPROVAL requests *spread across the whole
    sequence*, not the first 9 then the next 4 then the last 2 -- avoids
    the small-N bug where taking pattern[:N] of a block pattern would give
    all-ALLOW for any N below the ALLOW percentage's slot count."""
    counts = {k: round(total * p / 100) for k, p in pct.items()}
    # rounding can leave the total off by a couple of slots; fix up against ALLOW
    diff = total - sum(counts.values())
    counts["ALLOW"] += diff
    remaining = dict(counts)
    progress = {k: 0.0 for k in counts}
    result = []
    for _ in range(total):
        candidates = [k for k, v in remaining.items() if v > 0]
        best = min(candidates, key=lambda k: (progress[k] + 1) / counts[k])
        result.append(best)
        progress[best] += 1
        remaining[best] -= 1
    return result


def _request_kwargs(kind: str, tenant: dict) -> dict:
    token = tenant["agent_token"]
    if kind == "ALLOW":
        return dict(
            method="POST",
            url=f"{GATEWAY_URL}/api/gateway/tools/crm/read",
            headers={"X-Agent-Token": token},
            json={"scope": "customers", "payload": {}},
        )
    if kind == "BLOCK":
        return dict(
            method="POST",
            url=f"{GATEWAY_URL}/api/gateway/tools/crm/delete",
            headers={"X-Agent-Token": token},
            json={"scope": "customers", "payload": {}},
        )
    if kind == "APPROVAL":
        return dict(
            method="POST",
            url=f"{GATEWAY_URL}/api/authorize",
            headers={"X-Agent-Token": token},
            json={
                "resource_kind": "email",
                "action": "SEND",
                "scope": "external",
                "destination": "external",
            },
        )
    raise ValueError(f"unknown kind: {kind}")


def _classify_error(status: int, body_text: str | None, exc_name: str | None) -> str:
    if exc_name:
        return f"EXCEPTION:{exc_name}"
    text = (body_text or "")[:200]
    if status == 500 and "database is locked" in text.lower():
        return "HTTP 500: database is locked"
    if status >= 400:
        return f"HTTP {status}: {text or '(no body)'}"
    return f"HTTP {status}"


async def _one_request(client: httpx.AsyncClient, kwargs: dict) -> dict:
    start = time.perf_counter()
    try:
        resp = await client.request(**kwargs)
        elapsed_ms = (time.perf_counter() - start) * 1000
        decision = None
        try:
            decision = resp.json().get("decision")
        except Exception:
            decision = None
        error_label = None
        if resp.status_code >= 400:
            error_label = _classify_error(resp.status_code, resp.text, None)
        return {"latency_ms": elapsed_ms, "status": resp.status_code, "decision": decision, "error": error_label}
    except httpx.HTTPError as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000
        return {
            "latency_ms": elapsed_ms,
            "status": -1,
            "decision": None,
            "error": _classify_error(-1, None, exc.__class__.__name__),
        }


def _build_plan(args, tenants: list[dict]) -> list[tuple[dict, str]]:
    """Returns a flat list of (tenant, kind) request assignments.

    The kind sequence (ALLOW/BLOCK/APPROVAL) is generated once, proportional
    round-robin, over the WHOLE plan (see _interleaved_kinds), then paired
    positionally with the tenant sequence -- so every tenant's own requests
    land on a representative spread of kinds rather than all clustering on
    one kind for small per-tenant counts.
    """
    pct = _mix_percentages(args.mix)

    tenant_sequence: list[dict] = []
    if args.weights:
        pairs = [p.split(":") for p in args.weights.split(",")]
        for idx_str, count_str in pairs:
            idx = int(idx_str)
            count = int(count_str)
            tenant = tenants[idx]
            tenant_sequence.extend([tenant] * count)
    else:
        for tenant in tenants[: args.clients]:
            tenant_sequence.extend([tenant] * args.requests_per_client)

    kinds = _interleaved_kinds(len(tenant_sequence), pct)
    plan = list(zip(tenant_sequence, kinds))
    if args.shuffle_seed is not None:
        # Task-creation order can bias which tenant's requests acquire the
        # global semaphore first (asyncio.Semaphore grants in FIFO waiter
        # order), which would confound a noisy-neighbor/fairness measurement
        # with a pure submission-order artifact rather than genuine
        # resource contention. Shuffle (deterministically, for
        # reproducibility) so no tenant is systematically first or last.
        rng = random.Random(args.shuffle_seed)
        rng.shuffle(plan)
    return plan


async def run(args) -> dict:
    tenants = _load_tenants()
    plan = _build_plan(args, tenants)

    sem = asyncio.Semaphore(args.concurrency)
    results: list[dict] = []

    async def _bounded(tenant: dict, kind: str) -> None:
        async with sem:
            kwargs = _request_kwargs(kind, tenant)
            r = await _one_request(client, kwargs)
            r["tenant_index"] = tenant["index"]
            r["org_id"] = tenant["org_id"]
            r["kind"] = kind
            results.append(r)

    limits = httpx.Limits(max_connections=args.concurrency + 10, max_keepalive_connections=args.concurrency + 10)
    async with httpx.AsyncClient(timeout=15.0, limits=limits) as client:
        if args.warmup_per_client > 0:
            warmup_tasks = []
            for tenant in tenants[: args.clients or len(tenants)]:
                for _ in range(args.warmup_per_client):
                    warmup_tasks.append(_one_request(client, _request_kwargs("ALLOW", tenant)))
            await asyncio.gather(*warmup_tasks)

        started_at = datetime.now(timezone.utc)
        wall_start = time.perf_counter()
        await asyncio.gather(*[_bounded(t, k) for t, k in plan])
        wall_elapsed = time.perf_counter() - wall_start
        finished_at = datetime.now(timezone.utc)

    latencies_ms = sorted(r["latency_ms"] for r in results)

    def pct(vals: list[float], p: float) -> float | None:
        if not vals:
            return None
        k = (len(vals) - 1) * (p / 100)
        f = int(k)
        c = min(f + 1, len(vals) - 1)
        if f == c:
            return vals[f]
        return vals[f] + (vals[c] - vals[f]) * (k - f)

    error_samples: dict[str, int] = {}
    for r in results:
        if r["error"]:
            error_samples[r["error"]] = error_samples.get(r["error"], 0) + 1
    if len(error_samples) > _MAX_ERROR_SAMPLES:
        items = sorted(error_samples.items(), key=lambda kv: -kv[1])
        kept = dict(items[: _MAX_ERROR_SAMPLES - 1])
        kept["(other, truncated)"] = sum(c for _, c in items[_MAX_ERROR_SAMPLES - 1 :])
        error_samples = kept

    by_tenant: dict[str, dict] = {}
    for r in results:
        key = str(r["tenant_index"])
        by_tenant.setdefault(key, []).append(r)
    per_tenant_summary = {}
    for key, rows in by_tenant.items():
        lat = sorted(x["latency_ms"] for x in rows)
        errs = sum(1 for x in rows if x["error"])
        per_tenant_summary[key] = {
            "org_id": rows[0]["org_id"],
            "requests": len(rows),
            "errors": errs,
            "error_rate": errs / len(rows) if rows else None,
            "p50": pct(lat, 50),
            "p95": pct(lat, 95),
            "mean": sum(lat) / len(lat) if lat else None,
            "decision_counts": {d: sum(1 for x in rows if x["decision"] == d) for d in set(x["decision"] for x in rows if x["decision"])},
        }

    kind_counts = {k: sum(1 for r in results if r["kind"] == k) for k in ("ALLOW", "BLOCK", "APPROVAL")}
    decision_counts = {}
    for r in results:
        if r["decision"]:
            decision_counts[r["decision"]] = decision_counts.get(r["decision"], 0) + 1

    summary = {
        "label": args.label,
        "clients_used": args.clients if not args.weights else len({r["tenant_index"] for r in results}),
        "requests_per_client": args.requests_per_client if not args.weights else None,
        "weights": args.weights,
        "concurrency": args.concurrency,
        "mix": args.mix,
        "total_requests": len(plan),
        "actual_count": len(results),
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "wall_seconds": wall_elapsed,
        "throughput_rps": len(results) / wall_elapsed if wall_elapsed > 0 else None,
        "latency_ms": {
            "min": min(latencies_ms) if latencies_ms else None,
            "mean": sum(latencies_ms) / len(latencies_ms) if latencies_ms else None,
            "p50": pct(latencies_ms, 50),
            "p90": pct(latencies_ms, 90),
            "p95": pct(latencies_ms, 95),
            "p99": pct(latencies_ms, 99),
            "max": max(latencies_ms) if latencies_ms else None,
        },
        "kind_counts": kind_counts,
        "decision_counts": decision_counts,
        "error_count": sum(1 for r in results if r["error"]),
        "error_rate": sum(1 for r in results if r["error"]) / len(results) if results else None,
        "error_samples": error_samples,
        "per_tenant": per_tenant_summary,
    }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clients", type=int, default=0)
    parser.add_argument("--requests-per-client", type=int, default=0)
    parser.add_argument("--weights", default=None, help="explicit 'tenant_index:count,...' overrides --clients/--requests-per-client")
    parser.add_argument("--concurrency", type=int, required=True)
    parser.add_argument("--mix", default="100,0", help="ALLOW,BLOCK[,APPROVAL] percentages summing to 100")
    parser.add_argument("--warmup-per-client", type=int, default=0)
    parser.add_argument("--shuffle-seed", type=int, default=None, help="deterministically shuffle request submission order (avoids semaphore-FIFO submission-order bias in fairness tests)")
    parser.add_argument("--label", default="")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    summary = asyncio.run(run(args))
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "per_tenant"}, indent=2))


if __name__ == "__main__":
    main()
