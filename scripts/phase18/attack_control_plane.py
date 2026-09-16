"""Phase 18 — attacking the live control plane directly, no browser involved.

Every request here is hand-built. The frontend is not the security boundary,
so the only question that matters is what the API does when the caller does
not cooperate.
"""
import json, os, sys, urllib.parse, urllib.request, urllib.error

BASE = "http://127.0.0.1:8000"
results = []

def call(method, path, body=None, token=None, base=BASE):
    req = urllib.request.Request(base + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={k: v for k, v in {
            "Content-Type": "application/json" if body is not None else None,
            "Authorization": f"Bearer {token}" if token else None}.items() if v})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode() or "null")
        except Exception: return e.code, None
    except Exception as e:
        return 0, str(e)

def check(name, expectation, ok, detail):
    results.append({"attack": name, "expected": expectation, "held": ok, "observed": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}\n        expected {expectation}\n        observed {detail}")

cfg = json.load(open(os.path.join(os.path.dirname(__file__), "uidrive", "part1.json")))

# Two tenants, two operators.
_, victim = call("POST", "/api/auth/login", {"email": "admin@acme.test", "password": "aegis-demo"})
_, attacker = call("POST", "/api/auth/login", {"email": cfg["email"], "password": cfg["password"]})
vtok, atok = victim["access_token"], attacker["access_token"]

_, vagents = call("GET", "/api/agents", token=vtok)
victim_agent = vagents[0]["id"]
_, aagents = call("GET", "/api/agents", token=atok)
attacker_agent = aagents[0]["id"]
print(f"victim agent {victim_agent[:8]} / attacker agent {attacker_agent[:8]}\n")

# 1-5. Cross-tenant reads and writes with a guessed-correct identifier.
s, b = call("GET", f"/api/agents/{victim_agent}", token=atok)
check("read another tenant's agent by id", "404", s == 404, f"{s} {b}")

s, b = call("POST", f"/api/agents/{victim_agent}/verification-runs", {"scenario": "canonical"}, token=atok)
check("queue a runtime job on another tenant's agent", "404", s == 404, f"{s} {b}")

s, b = call("POST", f"/api/agents/{victim_agent}/simulate",
            {"resource_kind": "crm", "action": "READ", "scope": "customers"}, token=atok)
check("simulate against another tenant's agent", "404", s == 404, f"{s} {b}")

s, b = call("POST", f"/api/agents/{victim_agent}/revoke", token=atok)
check("revoke another tenant's agent", "404", s == 404, f"{s} {b}")

s, b = call("POST", f"/api/agents/{victim_agent}/rotate", token=atok)
check("rotate another tenant's agent credential", "404", s == 404, f"{s} {b}")

# 6. Approvals belonging to another tenant.
s, vappr = call("GET", "/api/approvals", token=vtok)
if isinstance(vappr, list) and vappr:
    target = vappr[0]["id"]
    s, b = call("POST", f"/api/approvals/{target}/decide", {"decision": "ALLOW"}, token=atok)
    check("approve another tenant's approval", "404", s == 404, f"{s} {b}")
else:
    check("approve another tenant's approval", "404", None, "victim had no approvals to target")

# 7. Unauthenticated.
for path in ["/api/agents", "/api/approvals", "/api/events", "/api/capabilities",
             "/api/verification-runs", "/api/verification/scenarios"]:
    s, b = call("GET", path)
    check(f"unauthenticated GET {path}", "401", s == 401, f"{s} {b}")
# The run-request route is POST-only; unauthenticated POST must not create one.
s, b = call("POST", f"/api/agents/{attacker_agent}/verification-runs", {"scenario": "canonical"})
check("unauthenticated POST of a verification run", "401", s == 401, f"{s} {b}")

# 8. An agent token is not an operator token.
s, b = call("GET", "/api/agents", token=cfg["token"])
check("agent credential used as an operator session", "refused (401 or 403)",
      s in (401, 403), f"{s} {b}")

# 9. Server-controlled fields supplied by the client.
#
# This needs a *fresh* agent with no contract yet: attempting it on an agent
# that already has one returns 409 before the ownership fields are ever looked
# at, which would pass the test without testing anything.
s, fresh = call("POST", "/api/agents", {"name": "Smuggle Target", "provider": "custom",
                                        "model": "external", "description": "attack fixture"},
                token=atok)
fresh_id = fresh["agent"]["id"]
s, b = call("POST", f"/api/agents/{fresh_id}/contracts", {
    "organization_id": "forged-org",
    "agent_id": victim_agent,
    "contract_id": "smuggled", "version": 1, "status": "ACTIVE",
    "purpose": "attempt to bind a contract to someone else's agent",
    "capabilities": [{"name": "crm", "resource_kind": "crm", "actions": ["DELETE"]}],
    "resources": [{"kind": "crm", "scope": "*"}],
}, token=atok)
owner_ok = (s < 400 and isinstance(b, dict)
            and b.get("agent_id") == fresh_id
            and b.get("organization_id") not in ("forged-org", None))
check("client-supplied organization_id/agent_id on contract creation",
      "ignored; the contract binds to the caller's own agent, not the one named in the body",
      owner_ok,
      f"{s} agent_id={(b or {}).get('agent_id','')[:8]} org={(b or {}).get('organization_id','')[:8]} "
      f"(victim agent was {victim_agent[:8]})")

# The smuggled contract must not have given the victim's agent a DELETE capability.
s, vcontracts = call("GET", f"/api/agents/{victim_agent}/contracts", token=vtok)
leaked = any(c.get("contract_id") == "smuggled" for c in (vcontracts or []) if isinstance(c, dict))
check("smuggled contract appears on the victim's agent", "never", not leaked,
      f"{s} victim contracts: {[c.get('contract_id') for c in (vcontracts or []) if isinstance(c, dict)]}")

# 10. Nonexistent / malformed identifiers.
for bad in ["../../etc/passwd", "'; DROP TABLE agents;--", "00000000-0000-0000-0000-000000000000"]:
    s, b = call("GET", f"/api/agents/{urllib.parse.quote(bad, safe='')}", token=atok)
    check(f"hostile agent id {bad!r}", "404 or 422, never 500", s in (404, 422), f"{s}")

# 11. The gateway is not reachable from the control plane's network at all.
s, b = call("GET", "/api/agentctl/next", token=cfg["token"], base=BASE)
check("agent job channel exposed on the public control plane", "404 (gateway-only route)", s == 404, f"{s} {b}")

json.dump(results, open(os.path.join(os.path.dirname(__file__), "attack_live.json"), "w"), indent=2)
failed = [r for r in results if r["held"] is False]
print(f"\n{len(results) - len(failed)} held, {len(failed)} FAILED")
sys.exit(1 if failed else 0)
