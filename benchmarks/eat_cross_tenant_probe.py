"""Phase 16.C EAT cross-tenant diagnostic.

Uses the REAL, unmodified `app.eat.sign_eat` (same function
`dispatch_via_broker` calls) to mint an EAT whose claims say one tenant,
then present it to the REAL credential-broker with an execute-request body
claiming a DIFFERENT tenant's org_id/agent_id -- exactly what
`backend/app/routers/broker.py:execute` checks for
(`claims["org_id"] != body.org_id`, `claims["agent_id"] != body.agent_id`).
No bypass, no mocked verification. Intended to run inside
enforcement-gateway (has AEGIS_EAT_KEY, AEGIS_INTERNAL_GATEWAY_TOKEN, and a
network route to credential-broker over broker_net):

    docker cp benchmarks/eat_cross_tenant_probe.py aegis-enforcement-gateway:/tmp/eat_cross_tenant_probe.py
    docker exec -w /app -e PYTHONPATH=/app aegis-enforcement-gateway python /tmp/eat_cross_tenant_probe.py
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid

import httpx

from app import config
from app.eat import sign_eat

BROKER_URL = os.environ.get("AEGIS_BROKER_URL", "http://credential-broker:8000/api")


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if config.INTERNAL_GATEWAY_TOKEN:
        h["X-Internal-Token"] = config.INTERNAL_GATEWAY_TOKEN
    return h


async def _post(client: httpx.AsyncClient, eat: str, *, org_id: str, agent_id: str, execution_id: str, request_id: str) -> tuple[int, str]:
    body = {
        "eat": eat,
        "tool": "crm",
        "operation": "read",
        "scope": "customers",
        "destination": None,
        "payload": {},
        "org_id": org_id,
        "agent_id": agent_id,
        "execution_id": execution_id,
        "request_id": request_id,
        "contract_id": None,
        "contract_version": None,
    }
    resp = await client.post(
        f"{BROKER_URL.rstrip('/')}/internal/broker/execute",
        json=body,
        headers=_headers(),
        timeout=10.0,
    )
    return resp.status_code, resp.text[:200]


async def main() -> None:
    tenant_a = {"org_id": "tenant-16c-A", "agent_id": "agent-16c-A"}
    tenant_b = {"org_id": "tenant-16c-B", "agent_id": "agent-16c-B"}
    results = {}

    async with httpx.AsyncClient() as client:
        # Baseline: EAT signed for A, body claims A -- must succeed (proves the probe itself works).
        exec_id = f"bench-16c-eat-{uuid.uuid4()}"
        req_id = f"bench-16c-eat-{uuid.uuid4()}"
        eat_a = sign_eat(
            org_id=tenant_a["org_id"], agent_id=tenant_a["agent_id"],
            execution_id=exec_id, request_id=req_id,
            tool="crm", operation="read", scope="customers", destination=None, payload={},
        )
        status, body = await _post(client, eat_a, org_id=tenant_a["org_id"], agent_id=tenant_a["agent_id"], execution_id=exec_id, request_id=req_id)
        results["baseline_same_tenant"] = {"status": status, "body": body, "expected": "200 (proves the EAT/broker plumbing works)"}

        # Test: EAT signed for A's org, body claims B's org (org_id mismatch).
        exec_id2 = f"bench-16c-eat-{uuid.uuid4()}"
        req_id2 = f"bench-16c-eat-{uuid.uuid4()}"
        eat_a2 = sign_eat(
            org_id=tenant_a["org_id"], agent_id=tenant_a["agent_id"],
            execution_id=exec_id2, request_id=req_id2,
            tool="crm", operation="read", scope="customers", destination=None, payload={},
        )
        status2, body2 = await _post(client, eat_a2, org_id=tenant_b["org_id"], agent_id=tenant_a["agent_id"], execution_id=exec_id2, request_id=req_id2)
        results["org_id_mismatch"] = {"status": status2, "body": body2, "expected": "401 eat_rejected (claims.org_id != body.org_id)"}

        # Test: EAT signed for A's org+agent, body claims A's org but B's agent_id.
        exec_id3 = f"bench-16c-eat-{uuid.uuid4()}"
        req_id3 = f"bench-16c-eat-{uuid.uuid4()}"
        eat_a3 = sign_eat(
            org_id=tenant_a["org_id"], agent_id=tenant_a["agent_id"],
            execution_id=exec_id3, request_id=req_id3,
            tool="crm", operation="read", scope="customers", destination=None, payload={},
        )
        status3, body3 = await _post(client, eat_a3, org_id=tenant_a["org_id"], agent_id=tenant_b["agent_id"], execution_id=exec_id3, request_id=req_id3)
        results["agent_id_mismatch"] = {"status": status3, "body": body3, "expected": "401 eat_rejected (claims.agent_id != body.agent_id)"}

        # Test: EAT signed for A's execution_id, body claims a DIFFERENT (B-owned-looking) execution_id.
        exec_id_a = f"bench-16c-eat-a-{uuid.uuid4()}"
        exec_id_b = f"bench-16c-eat-b-{uuid.uuid4()}"
        req_id4 = f"bench-16c-eat-{uuid.uuid4()}"
        eat_a4 = sign_eat(
            org_id=tenant_a["org_id"], agent_id=tenant_a["agent_id"],
            execution_id=exec_id_a, request_id=req_id4,
            tool="crm", operation="read", scope="customers", destination=None, payload={},
        )
        status4, body4 = await _post(client, eat_a4, org_id=tenant_a["org_id"], agent_id=tenant_a["agent_id"], execution_id=exec_id_b, request_id=req_id4)
        results["execution_id_mismatch"] = {"status": status4, "body": body4, "expected": "401 eat_rejected (claims.execution_id != body.execution_id)"}

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
