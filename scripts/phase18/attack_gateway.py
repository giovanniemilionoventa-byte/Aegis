"""Attacks on the agent-facing job channel, run from inside the agent network."""
import json, os, urllib.request, urllib.error

GW = "http://enforcement-gateway:8000"

def call(method, path, body=None, token=None):
    req = urllib.request.Request(GW + path, method=method,
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

out = []
def check(name, expectation, ok, detail):
    out.append({"attack": name, "expected": expectation, "held": ok, "observed": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}\n        expected {expectation}\n        observed {detail}")

victim_token = os.environ["VICTIM_TOKEN"]   # the seeded agent, which has a run queued for it
attacker_token = os.environ["ATTACKER_TOKEN"]  # a different agent, different tenant
victim_run = os.environ["VICTIM_RUN"]

# 1. Unauthenticated.
s, b = call("GET", "/api/agentctl/next")
check("unauthenticated claim of a queued job", "401", s == 401, f"{s} {b}")

# 2. A different agent must not receive a job queued for the victim.
s, b = call("GET", "/api/agentctl/next", token=attacker_token)
stolen = isinstance(b, dict) and b.get("id") == victim_run
check("another tenant's agent claims a job queued for the victim",
      "no job, or only its own", not stolen, f"{s} {json.dumps(b)[:160]}")

# 3. Forge a result for someone else's run.
s, b = call("POST", f"/api/agentctl/runs/{victim_run}/result",
            {"status": "COMPLETED", "execution_id": "forged",
             "result": {"evaluation": {"verdict": "PASS", "passed": 99, "total": 99, "checks": [], "failed": []}}},
            token=attacker_token)
check("forge a PASS verdict on another agent's run", "404 or 403", s in (403, 404), f"{s} {b}")

# 4. An operator session is not an agent.
s, b = call("GET", "/api/agentctl/next", token=os.environ["OPERATOR_JWT"])
check("operator session used as an agent credential", "401 or 403", s in (401, 403), f"{s} {b}")

# 5. Control-plane routes must not be usable on the gateway.
#
# Two refusals are in play and both are correct. Unauthenticated, the route is
# simply not mounted in this role, so it 404s. With an agent credential, a
# middleware refuses it with 403 before routing ever happens. The attack fails
# either way; what must never happen is a 2xx.
for path in ["/api/agents", "/api/approvals", "/api/auth/login"]:
    s_anon, _ = call("GET", path)
    s_agent, _ = call("GET", path, token=attacker_token)
    check(f"control-plane route {path} usable on the gateway",
          "refused both anonymously and with an agent credential",
          s_anon >= 400 and s_agent >= 400,
          f"anonymous={s_anon} with-agent-token={s_agent}")

# And the one that would actually matter: a real login against the gateway.
s, b = call("POST", "/api/auth/login", {"email": "admin@acme.test", "password": "aegis-demo"})
check("operator login served by the enforcement gateway", "404; the route is not in this role",
      s == 404, f"{s} {b}")

print(json.dumps(out))
