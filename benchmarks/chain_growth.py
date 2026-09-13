"""Phase 16.B long-execution evidence-chain growth probe.

Runs INSIDE aegis-bench-runner. Sends N SEQUENTIAL (concurrency=1, one at a
time -- no race, a clean growing chain) real requests via /api/authorize,
all pinned to the SAME execution_id, so the evidence chain and trajectory
for that execution grow by exactly one event per request. Records each
request's own wall-clock latency and index, so the report can show how
per-request latency (which includes assert_execution_evidence_integrity
walking every prior event in the chain, per
backend/app/services/evidence_verifier.py) changes as the chain grows from
0 to N-1 prior events.

No event is inserted directly into the database -- every event is produced
by a real HTTP request through the real, unmodified authorize_request()
pipeline.

Usage (inside the container):
  python chain_growth.py --count 100 --out /bench/results/16b_chain_growth.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from pathlib import Path

import httpx

GATEWAY_URL = "http://enforcement-gateway:8000"
TOKEN_FILE = Path("/bench/runtime/token.json")


def _agent_token() -> str:
    data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    return data["agent_token"]


async def run(count: int, execution_id: str) -> dict:
    agent_token = _agent_token()
    per_request = []
    async with httpx.AsyncClient(timeout=15.0) as client:
        for i in range(1, count + 1):
            start = time.perf_counter()
            resp = await client.post(
                f"{GATEWAY_URL}/api/authorize",
                headers={"X-Agent-Token": agent_token},
                json={
                    "resource_kind": "crm",
                    "action": "READ",
                    "scope": "customers",
                    "execution_id": execution_id,
                    "request_id": f"{execution_id}-req-{i}",
                },
            )
            elapsed_ms = (time.perf_counter() - start) * 1000
            per_request.append(
                {
                    "index": i,
                    "prior_events_in_chain": i - 1,
                    "latency_ms": elapsed_ms,
                    "status": resp.status_code,
                    "decision": (resp.json().get("decision") if resp.status_code == 200 else None),
                }
            )
    return {"execution_id": execution_id, "count": count, "requests": per_request}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--execution-id", default=None)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    execution_id = args.execution_id or f"bench-16b-chaingrowth-{uuid.uuid4()}"

    result = asyncio.run(run(args.count, execution_id))
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"execution_id": execution_id, "count": args.count, "out": str(out_path)}, indent=2))


if __name__ == "__main__":
    main()
