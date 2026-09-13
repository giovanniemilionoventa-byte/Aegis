"""Phase 16.C cross-tenant isolation adversarial probe.

Runs INSIDE aegis-bench-runner, against the real enforcement-gateway, using
two real provisioned tenants (see provision_tenants.py). No security check
is bypassed or mocked -- these are ordinary HTTP requests an actual
malicious or misconfigured client could send.

Test 1 -- A's agent token + B's execution_id:
  Tenant B makes one normal call with an explicit execution_id it owns.
  Tenant A then sends a gateway request using ITS OWN token but claiming
  B's execution_id. Expected: 403 (get_or_create_execution's ownership
  check in backend/app/engines/enforcement.py), not a silently-adopted
  cross-tenant execution.

Test 2 -- request_id reused across tenants:
  A and B both send a request carrying the identical request_id string.
  Expected: two independent Events are created (the idempotency lookup in
  authorize_request filters by agent_id as well as request_id), not one
  event overwriting/short-circuiting the other's decision.

Usage (inside the container):
  python tenant_isolation_probe.py --tenant-a 0 --tenant-b 1 \
      --out /bench/results/16c_isolation_probe.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from pathlib import Path

import httpx

GATEWAY_URL = "http://enforcement-gateway:8000"
TENANTS_FILE = Path("/bench/runtime/tenants_16c.json")


def _load_tenants() -> list[dict]:
    return json.loads(TENANTS_FILE.read_text(encoding="utf-8"))


async def _call(client: httpx.AsyncClient, token: str, op: str, **body_extra) -> dict:
    resp = await client.post(
        f"{GATEWAY_URL}/api/gateway/tools/crm/{op}",
        headers={"X-Agent-Token": token},
        json={"scope": "customers", "payload": {}, **body_extra},
    )
    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text[:300]}
    return {"status": resp.status_code, "body": body}


async def main_async(tenant_a: dict, tenant_b: dict) -> dict:
    results: dict = {}
    async with httpx.AsyncClient(timeout=15.0) as client:
        # --- Test 1: A's token + B's execution_id ---
        b_execution_id = f"tenant-b-owned-exec-{uuid.uuid4()}"
        b_legit = await _call(client, tenant_b["agent_token"], "read", execution_id=b_execution_id)

        a_cross = await _call(client, tenant_a["agent_token"], "read", execution_id=b_execution_id)

        results["test1_execution_id_cross_tenant"] = {
            "tenant_b_legit_call": b_legit,
            "tenant_a_using_b_execution_id": a_cross,
            "expected": "tenant_a call rejected (403 execution not owned), NOT a 200 that adopts B's execution",
            "verdict": "FAIL_CLOSED" if a_cross["status"] in (403,) else (
                "UNEXPECTED_ALLOW" if a_cross["status"] == 200 and a_cross["body"].get("decision") == "ALLOW" else "OTHER"
            ),
        }

        # --- Test 2: shared request_id across tenants ---
        shared_request_id = f"tenant-isolation-shared-{uuid.uuid4()}"
        a_req = await _call(client, tenant_a["agent_token"], "read", request_id=shared_request_id)
        b_req = await _call(client, tenant_b["agent_token"], "read", request_id=shared_request_id)

        results["test2_shared_request_id"] = {
            "tenant_a_call": a_req,
            "tenant_b_call": b_req,
            "expected": "both succeed independently with their own agent_id/organization_id -- no cross-tenant idempotency collision",
            "verdict": "OK" if (
                a_req["status"] == 200 and b_req["status"] == 200
                and a_req["body"].get("agent_id") != b_req["body"].get("agent_id")
                and a_req["body"].get("organization_id") != b_req["body"].get("organization_id")
            ) else "NEEDS_REVIEW",
        }

        results["tenant_a"] = {"org_id": tenant_a["org_id"], "agent_id": tenant_a["agent_id"]}
        results["tenant_b"] = {"org_id": tenant_b["org_id"], "agent_id": tenant_b["agent_id"]}
        results["shared_request_id"] = shared_request_id
        results["b_execution_id"] = b_execution_id

    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant-a", type=int, required=True)
    parser.add_argument("--tenant-b", type=int, required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    tenants = _load_tenants()
    tenant_a = tenants[args.tenant_a]
    tenant_b = tenants[args.tenant_b]

    results = asyncio.run(main_async(tenant_a, tenant_b))
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
