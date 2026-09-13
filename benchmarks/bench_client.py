"""Phase 16.A load generator.

Runs INSIDE the aegis-bench-runner container (see docker-compose.bench.yml),
which is attached to agent_net (reaches enforcement-gateway) and tool_net
(reaches protected-tool directly).

Three scenarios, all real HTTP requests against real running containers
(no mocks, no bypassed security checks):

  baseline    -> protected-tool directly (no Aegis in the path at all)
  aegis_allow -> enforcement-gateway /api/gateway/tools/crm/read (real ALLOW)
  aegis_block -> enforcement-gateway /api/gateway/tools/crm/delete (real BLOCK,
                 policy "Block CRM mass delete" rejects it before dispatch)

Each request uses its own fresh execution (execution_id omitted), matching
the Aegis SDK / demo-agent default usage pattern -- see report section 8.

Usage (inside the container):
  python bench_client.py --scenario aegis_allow --concurrency 10 --count 500 \
      --warmup 50 --out /bench/results/aegis_allow_c10.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
from pathlib import Path

import httpx

GATEWAY_URL = "http://enforcement-gateway:8000"
TOOL_URL = "http://protected-tool:8000"

CRM_SECRET = os.environ.get("AEGIS_CRM_SECRET", "change-me-crm-secret")
INTERNAL_TOOL_TOKEN = os.environ.get("AEGIS_INTERNAL_TOOL_TOKEN", "aegis-internal-tool-token")

TOKEN_FILE = Path("/bench/runtime/token.json")


def _agent_token() -> str:
    data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    return data["agent_token"]


def _request_kwargs(scenario: str, agent_token: str) -> dict:
    if scenario == "baseline":
        return dict(
            method="POST",
            url=f"{TOOL_URL}/api/internal/tools/crm/read",
            headers={"X-Internal-Token": INTERNAL_TOOL_TOKEN},
            json={"secret": CRM_SECRET, "scope": "customers", "payload": {}},
        )
    if scenario == "aegis_allow":
        return dict(
            method="POST",
            url=f"{GATEWAY_URL}/api/gateway/tools/crm/read",
            headers={"X-Agent-Token": agent_token},
            json={"scope": "customers", "payload": {}},
        )
    if scenario == "aegis_block":
        return dict(
            method="POST",
            url=f"{GATEWAY_URL}/api/gateway/tools/crm/delete",
            headers={"X-Agent-Token": agent_token},
            json={"scope": "customers", "payload": {}},
        )
    if scenario == "authorize_only":
        # Same authorization pipeline as aegis_allow (permission, trajectory,
        # behavior, contract resolution, policy, risk, HMAC evidence seal)
        # but WITHOUT EAT signing, the broker HTTP hop, or the tool HTTP hop
        # -- used only for the component-breakdown approximation in the
        # report, not as a scenario on its own.
        return dict(
            method="POST",
            url=f"{GATEWAY_URL}/api/authorize",
            headers={"X-Agent-Token": agent_token},
            json={"resource_kind": "crm", "action": "READ", "scope": "customers"},
        )
    raise ValueError(f"unknown scenario: {scenario}")


async def _one_request(client: httpx.AsyncClient, kwargs: dict) -> tuple[float, int, str | None]:
    start = time.perf_counter()
    try:
        resp = await client.request(**kwargs)
        elapsed = time.perf_counter() - start
        decision = None
        if "/api/gateway" in kwargs["url"] or "/api/authorize" in kwargs["url"]:
            try:
                decision = resp.json().get("decision")
            except Exception:
                decision = None
        return elapsed, resp.status_code, decision
    except httpx.HTTPError as exc:
        elapsed = time.perf_counter() - start
        return elapsed, -1, f"ERROR:{exc.__class__.__name__}"


async def _worker(
    client: httpx.AsyncClient,
    kwargs: dict,
    n: int,
    results: list,
) -> None:
    for _ in range(n):
        results.append(await _one_request(client, kwargs))


async def run(scenario: str, concurrency: int, count: int, warmup: int) -> dict:
    agent_token = _agent_token() if scenario != "baseline" else ""
    kwargs = _request_kwargs(scenario, agent_token)

    limits = httpx.Limits(max_connections=concurrency + 5, max_keepalive_connections=concurrency + 5)
    async with httpx.AsyncClient(timeout=10.0, limits=limits) as client:
        if warmup > 0:
            warmup_results: list = []
            per_worker = max(1, warmup // concurrency)
            await asyncio.gather(
                *[_worker(client, kwargs, per_worker, warmup_results) for _ in range(concurrency)]
            )

        results: list = []
        base_per_worker = count // concurrency
        remainder = count % concurrency
        wall_start = time.perf_counter()
        tasks = []
        for i in range(concurrency):
            n = base_per_worker + (1 if i < remainder else 0)
            if n > 0:
                tasks.append(_worker(client, kwargs, n, results))
        await asyncio.gather(*tasks)
        wall_elapsed = time.perf_counter() - wall_start

    latencies_ms = sorted(r[0] * 1000 for r in results)
    statuses = [r[1] for r in results]
    decisions = [r[2] for r in results if r[2] and not str(r[2]).startswith("ERROR")]
    errors = [r for r in results if r[1] == -1 or r[1] >= 400]

    def pct(p: float) -> float:
        if not latencies_ms:
            return float("nan")
        k = (len(latencies_ms) - 1) * (p / 100)
        f = int(k)
        c = min(f + 1, len(latencies_ms) - 1)
        if f == c:
            return latencies_ms[f]
        return latencies_ms[f] + (latencies_ms[c] - latencies_ms[f]) * (k - f)

    summary = {
        "scenario": scenario,
        "concurrency": concurrency,
        "requested_count": count,
        "warmup": warmup,
        "actual_count": len(results),
        "wall_seconds": wall_elapsed,
        "throughput_rps": len(results) / wall_elapsed if wall_elapsed > 0 else float("nan"),
        "latency_ms": {
            "min": min(latencies_ms) if latencies_ms else None,
            "mean": statistics.fmean(latencies_ms) if latencies_ms else None,
            "p50": pct(50),
            "p90": pct(90),
            "p95": pct(95),
            "p99": pct(99),
            "max": max(latencies_ms) if latencies_ms else None,
        },
        "status_counts": {str(s): statuses.count(s) for s in sorted(set(statuses))},
        "decision_counts": {d: decisions.count(d) for d in sorted(set(decisions))},
        "error_count": len(errors),
        "error_rate": len(errors) / len(results) if results else None,
    }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario",
        required=True,
        choices=["baseline", "aegis_allow", "aegis_block", "authorize_only"],
    )
    parser.add_argument("--concurrency", type=int, required=True)
    parser.add_argument("--count", type=int, required=True, help="requests measured (excludes warmup)")
    parser.add_argument("--warmup", type=int, default=0)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    summary = asyncio.run(run(args.scenario, args.concurrency, args.count, args.warmup))
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
