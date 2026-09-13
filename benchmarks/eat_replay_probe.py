"""Phase 16.B EAT / replay diagnostic.

Uses the REAL, unmodified `app.eat.sign_eat` to mint EATs exactly as
`dispatch_via_broker` (backend/app/remote.py) does, then sends them to the
REAL credential-broker `/internal/broker/execute` endpoint -- no
reimplementation, no bypass, no mocked verification. Intended to run inside
the enforcement-gateway container, which already has AEGIS_EAT_KEY and
AEGIS_INTERNAL_GATEWAY_TOKEN configured and a network route to
credential-broker over broker_net:

    docker cp benchmarks/eat_replay_probe.py aegis-enforcement-gateway:/tmp/eat_replay_probe.py
    docker exec aegis-enforcement-gateway python /tmp/eat_replay_probe.py

Tests, all against the live broker, concurrently where noted:
  1. A single valid EAT sent N times concurrently -> exactly one should be
     accepted (200), the rest rejected by replay_store (401 eat_rejected).
  2. The same EAT sent again afterward (sequential) -> must still be
     rejected (replay window does not "reset").
  3. A tampered EAT (flipped signature byte) -> must be rejected
     (bad_signature).
  4. An expired EAT (ttl=-5s) -> must be rejected (expired).

Prints one JSON object to stdout with each test's observed result.
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
REPLAY_CONCURRENCY = 10


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if config.INTERNAL_GATEWAY_TOKEN:
        h["X-Internal-Token"] = config.INTERNAL_GATEWAY_TOKEN
    return h


def _body(eat: str, *, org_id: str, agent_id: str, execution_id: str, request_id: str) -> dict:
    return {
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


async def _post(client: httpx.AsyncClient, body: dict) -> tuple[int, str]:
    try:
        resp = await client.post(
            f"{BROKER_URL.rstrip('/')}/internal/broker/execute",
            json=body,
            headers=_headers(),
            timeout=10.0,
        )
        return resp.status_code, resp.text[:200]
    except httpx.HTTPError as exc:
        return -1, f"EXCEPTION:{exc.__class__.__name__}"


async def main() -> None:
    org_id = "bench-16b-org"
    agent_id = "bench-16b-agent"

    results = {}

    async with httpx.AsyncClient() as client:
        # --- Test 1 & 2: replay ---
        execution_id = f"bench-16b-exec-{uuid.uuid4()}"
        request_id = f"bench-16b-req-{uuid.uuid4()}"
        eat = sign_eat(
            org_id=org_id,
            agent_id=agent_id,
            execution_id=execution_id,
            request_id=request_id,
            tool="crm",
            operation="read",
            scope="customers",
            destination=None,
            payload={},
        )
        body = _body(eat, org_id=org_id, agent_id=agent_id, execution_id=execution_id, request_id=request_id)

        concurrent_results = await asyncio.gather(
            *[_post(client, body) for _ in range(REPLAY_CONCURRENCY)]
        )
        accepted = sum(1 for status, _ in concurrent_results if status == 200)
        rejected = sum(1 for status, _ in concurrent_results if status != 200)
        results["concurrent_replay"] = {
            "attempts": REPLAY_CONCURRENCY,
            "accepted_200": accepted,
            "rejected": rejected,
            "expected": "accepted_200 == 1 (exactly one JTI redemption)",
            "raw_statuses": [s for s, _ in concurrent_results],
        }

        # Test 2: replay again afterward, sequentially
        status2, text2 = await _post(client, body)
        results["sequential_replay_after_burst"] = {
            "status": status2,
            "body": text2,
            "expected": "non-200 (jti already consumed)",
        }

        # --- Test 3: tampered signature ---
        head, sig = eat.rsplit(".", 1)
        tampered_sig = ("A" if sig[0] != "A" else "B") + sig[1:]
        tampered_eat = f"{head}.{tampered_sig}"
        tampered_body = _body(
            tampered_eat, org_id=org_id, agent_id=agent_id,
            execution_id=execution_id, request_id=f"{request_id}-tampered",
        )
        status3, text3 = await _post(client, tampered_body)
        results["tampered_signature"] = {
            "status": status3,
            "body": text3,
            "expected": "401 eat_rejected (bad_signature)",
        }

        # --- Test 4: expired EAT ---
        expired_execution_id = f"bench-16b-exec-{uuid.uuid4()}"
        expired_request_id = f"bench-16b-req-{uuid.uuid4()}"
        expired_eat = sign_eat(
            org_id=org_id,
            agent_id=agent_id,
            execution_id=expired_execution_id,
            request_id=expired_request_id,
            tool="crm",
            operation="read",
            scope="customers",
            destination=None,
            payload={},
            ttl_seconds=-5,
        )
        expired_body = _body(
            expired_eat, org_id=org_id, agent_id=agent_id,
            execution_id=expired_execution_id, request_id=expired_request_id,
        )
        status4, text4 = await _post(client, expired_body)
        results["expired_eat"] = {
            "status": status4,
            "body": text4,
            "expected": "401 eat_rejected (expired)",
        }

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
