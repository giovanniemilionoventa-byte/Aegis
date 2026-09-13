"""Phase 16.C tenant provisioning.

Runs on the HOST against the published control-plane port. Creates N
independent tenants using ONLY pre-existing, public application endpoints
-- no direct database writes, no new multi-tenant logic invented:

  POST /api/auth/register        -> new Organization + admin User
  POST /api/agents                -> new Agent + initial Credential (token)
  POST /api/agents/{id}/permissions (x2) -> crm READ customers (allow),
                                             email SEND external (allow)
  POST /api/policies               -> "APPROVAL on external email" policy,
                                        mirroring seed.py's own demo policy
                                        for the seeded Acme org

Each tenant intentionally does NOT get a "crm DELETE" permission, so a
crm/delete request is BLOCKed at the Permission layer (no policy needed --
see backend/app/engines/enforcement.py's `if not permitted: decision =
"BLOCK"` branch) -- the same mechanism Phase 16.A/16.B already exercised
for the seeded demo agent.

Writes benchmarks/runtime/tenants_16c.json: a list of
{index, org_id, org_name, agent_id, agent_token}.

This is setup, not part of any measured latency (matches the Phase 16.A/B
convention of excluding bootstrap from benchmark numbers).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

CONTROL_PLANE_URL = "http://127.0.0.1:8000"
OUT_PATH = Path(__file__).resolve().parent / "runtime" / "tenants_16c.json"


def _req(method: str, path: str, body: dict | None = None, token: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(CONTROL_PLANE_URL + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def provision_one(index: int) -> dict:
    suffix = uuid.uuid4().hex[:8]
    org_name = f"Tenant-16C-{index:03d}-{suffix}"
    email = f"admin-16c-{index:03d}-{suffix}@example.test"
    password = "aegis-16c-bench-pw!"

    register = _req(
        "POST",
        "/api/auth/register",
        {
            "organization_name": org_name,
            "full_name": f"Bench Admin {index}",
            "email": email,
            "password": password,
        },
    )
    admin_token = register["access_token"]

    agent = _req(
        "POST",
        "/api/agents",
        {"name": f"Tenant Agent {index}", "provider": "demo", "model": "local-demo",
         "description": "Phase 16.C multi-tenant benchmark agent"},
        token=admin_token,
    )
    agent_id = agent["agent"]["id"]
    agent_token = agent["token"]

    _req(
        "POST",
        f"/api/agents/{agent_id}/permissions",
        {"resource_kind": "crm", "action": "READ", "scope": "customers", "effect": "allow"},
        token=admin_token,
    )
    _req(
        "POST",
        f"/api/agents/{agent_id}/permissions",
        {"resource_kind": "email", "action": "SEND", "scope": "external", "effect": "allow"},
        token=admin_token,
    )
    _req(
        "POST",
        "/api/policies",
        {
            "name": "Approve external email",
            "description": "Sending email outside the tenant requires a human (mirrors seed.py).",
            "resource_kind": "email",
            "action": "SEND",
            "scope_pattern": "external",
            "destination_pattern": "external",
            "decision": "APPROVAL",
            "priority": 20,
        },
        token=admin_token,
    )

    return {
        "index": index,
        "org_id": agent["agent"]["organization_id"],
        "org_name": org_name,
        "agent_id": agent_id,
        "agent_token": agent_token,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--out", default=str(OUT_PATH))
    args = parser.parse_args()

    tenants = []
    for i in range(1, args.count + 1):
        try:
            tenants.append(provision_one(i))
        except urllib.error.HTTPError as exc:
            print(f"ERROR provisioning tenant {i}: {exc.code} {exc.read()[:200]}", file=sys.stderr)
            raise
        if i % 10 == 0:
            print(f"provisioned {i}/{args.count}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(tenants, indent=2), encoding="utf-8")
    print(f"Wrote {len(tenants)} tenants to {out_path}")


if __name__ == "__main__":
    main()
