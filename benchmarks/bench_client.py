"""Phase 16.A/16.B load generator.

Runs INSIDE the aegis-bench-runner container (see docker-compose.bench.yml),
which is attached to agent_net (reaches enforcement-gateway) and tool_net
(reaches protected-tool directly).

Scenarios, all real HTTP requests against real running containers (no
mocks, no bypassed security checks):

  baseline       -> protected-tool directly (no Aegis in the path at all)
  aegis_allow    -> enforcement-gateway /api/gateway/tools/crm/read (real ALLOW)
  aegis_block    -> enforcement-gateway /api/gateway/tools/crm/delete (real
                     BLOCK, policy "Block CRM mass delete" rejects it before
                     dispatch)
  authorize_only -> enforcement-gateway /api/authorize crm/READ (component-
                     breakdown aid: same pipeline as aegis_allow minus EAT/
                     Broker/Tool dispatch)
  aegis_mixed    -> alternates crm read (ALLOW) / crm delete (BLOCK) against
                     the gateway, always on a caller-supplied --execution-id
                     (Phase 16.B race/trajectory probe; requires --execution-id)

By default each request uses its own fresh execution (execution_id
omitted), matching the Aegis SDK / demo-agent default usage pattern. Pass
--execution-id to force every request onto the SAME execution -- used by
Phase 16.B to probe concurrent-write races on one execution's evidence
chain/trajectory (see benchmarks/chain_probe.py and race_burst.py).

Usage (inside the container):
  python bench_client.py --scenario aegis_allow --concurrency 10 --count 500 \
      --warmup 50 --out /bench/results/aegis_allow_c10.json
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import os
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

GATEWAY_URL = "http://enforcement-gateway:8000"
TOOL_URL = "http://protected-tool:8000"

CRM_SECRET = os.environ.get("AEGIS_CRM_SECRET", "change-me-crm-secret")
INTERNAL_TOOL_TOKEN = os.environ.get("AEGIS_INTERNAL_TOOL_TOKEN", "aegis-internal-tool-token")

TOKEN_FILE = Path("/bench/runtime/token.json")

_MAX_ERROR_SAMPLES = 20


def _agent_token() -> str:
    data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    return data["agent_token"]


def _request_kwargs(
    scenario: str,
    agent_token: str,
    execution_id: str | None,
    op_index: int,
) -> dict:
    if scenario == "baseline":
        return dict(
            method="POST",
            url=f"{TOOL_URL}/api/internal/tools/crm/read",
            headers={"X-Internal-Token": INTERNAL_TOOL_TOKEN},
            json={"secret": CRM_SECRET, "scope": "customers", "payload": {}},
        )
    if scenario == "aegis_allow":
        body = {"scope": "customers", "payload": {}}
        if execution_id:
            body["execution_id"] = execution_id
        return dict(
            method="POST",
            url=f"{GATEWAY_URL}/api/gateway/tools/crm/read",
            headers={"X-Agent-Token": agent_token},
            json=body,
        )
    if scenario == "aegis_block":
        body = {"scope": "customers", "payload": {}}
        if execution_id:
            body["execution_id"] = execution_id
        return dict(
            method="POST",
            url=f"{GATEWAY_URL}/api/gateway/tools/crm/delete",
            headers={"X-Agent-Token": agent_token},
            json=body,
        )
    if scenario == "authorize_only":
        # Same authorization pipeline as aegis_allow (permission, trajectory,
        # behavior, contract resolution, policy, risk, HMAC evidence seal)
        # but WITHOUT EAT signing, the broker HTTP hop, or the tool HTTP hop
        # -- used only for the component-breakdown approximation in the
        # report, not as a scenario on its own.
        body = {"resource_kind": "crm", "action": "READ", "scope": "customers"}
        if execution_id:
            body["execution_id"] = execution_id
        return dict(
            method="POST",
            url=f"{GATEWAY_URL}/api/authorize",
            headers={"X-Agent-Token": agent_token},
            json=body,
        )
    if scenario == "aegis_mixed":
        if not execution_id:
            raise ValueError("aegis_mixed requires --execution-id")
        operation = "read" if op_index % 2 == 0 else "delete"
        return dict(
            method="POST",
            url=f"{GATEWAY_URL}/api/gateway/tools/crm/{operation}",
            headers={"X-Agent-Token": agent_token},
            json={"scope": "customers", "payload": {}, "execution_id": execution_id},
        )
    raise ValueError(f"unknown scenario: {scenario}")


def _classify_error(status: int, body_text: str | None, exc_name: str | None) -> str:
    if exc_name:
        return f"EXCEPTION:{exc_name}"
    text = (body_text or "")[:200]
    if status == 500 and "database is locked" in text.lower():
        return "HTTP 500: database is locked"
    if status == 500:
        return f"HTTP 500: {text or '(no body)'}"
    if status == 409:
        return f"HTTP 409: {text or '(no body)'}"
    if status >= 500:
        return f"HTTP {status}: {text or '(no body)'}"
    if status >= 400:
        return f"HTTP {status}: {text or '(no body)'}"
    return f"HTTP {status}"


async def _one_request(
    client: httpx.AsyncClient, kwargs: dict
) -> tuple[float, int, str | None, str | None]:
    """Returns (elapsed_seconds, status_code, decision_or_None, error_label_or_None)."""
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
        error_label = None
        if resp.status_code >= 400:
            error_label = _classify_error(resp.status_code, resp.text, None)
        return elapsed, resp.status_code, decision, error_label
    except httpx.HTTPError as exc:
        elapsed = time.perf_counter() - start
        label = _classify_error(-1, None, exc.__class__.__name__)
        return elapsed, -1, None, label


async def _worker(
    client: httpx.AsyncClient,
    scenario: str,
    agent_token: str,
    execution_id: str | None,
    counter: itertools.count,
    n: int,
    results: list,
) -> None:
    for _ in range(n):
        idx = next(counter)
        kwargs = _request_kwargs(scenario, agent_token, execution_id, idx)
        results.append(await _one_request(client, kwargs))


async def run(
    scenario: str,
    concurrency: int,
    count: int,
    warmup: int,
    execution_id: str | None = None,
) -> dict:
    agent_token = _agent_token() if scenario != "baseline" else ""

    limits = httpx.Limits(max_connections=concurrency + 5, max_keepalive_connections=concurrency + 5)
    async with httpx.AsyncClient(timeout=15.0, limits=limits) as client:
        if warmup > 0:
            warmup_results: list = []
            warmup_counter = itertools.count()
            per_worker = max(1, warmup // concurrency)
            await asyncio.gather(
                *[
                    _worker(client, scenario, agent_token, None, warmup_counter, per_worker, warmup_results)
                    for _ in range(concurrency)
                ]
            )

        results: list = []
        op_counter = itertools.count()
        base_per_worker = count // concurrency
        remainder = count % concurrency
        started_at = datetime.now(timezone.utc)
        wall_start = time.perf_counter()
        tasks = []
        for i in range(concurrency):
            n = base_per_worker + (1 if i < remainder else 0)
            if n > 0:
                tasks.append(
                    _worker(client, scenario, agent_token, execution_id, op_counter, n, results)
                )
        await asyncio.gather(*tasks)
        wall_elapsed = time.perf_counter() - wall_start
        finished_at = datetime.now(timezone.utc)

    latencies_ms = sorted(r[0] * 1000 for r in results)
    statuses = [r[1] for r in results]
    decisions = [r[2] for r in results if r[2]]
    error_labels = [r[3] for r in results if r[3]]

    error_samples: dict[str, int] = {}
    for label in error_labels:
        error_samples[label] = error_samples.get(label, 0) + 1
    if len(error_samples) > _MAX_ERROR_SAMPLES:
        items = sorted(error_samples.items(), key=lambda kv: -kv[1])
        kept = dict(items[: _MAX_ERROR_SAMPLES - 1])
        kept["(other, truncated)"] = sum(c for _, c in items[_MAX_ERROR_SAMPLES - 1 :])
        error_samples = kept

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
        "execution_id": execution_id,
        "requested_count": count,
        "warmup": warmup,
        "actual_count": len(results),
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
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
        "error_count": len(error_labels),
        "error_rate": len(error_labels) / len(results) if results else None,
        "error_samples": error_samples,
    }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario",
        required=True,
        choices=["baseline", "aegis_allow", "aegis_block", "authorize_only", "aegis_mixed"],
    )
    parser.add_argument("--concurrency", type=int, required=True)
    parser.add_argument("--count", type=int, required=True, help="requests measured (excludes warmup)")
    parser.add_argument("--warmup", type=int, default=0)
    parser.add_argument("--execution-id", default=None, help="pin every measured request to this execution_id")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    summary = asyncio.run(
        run(args.scenario, args.concurrency, args.count, args.warmup, args.execution_id)
    )
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
