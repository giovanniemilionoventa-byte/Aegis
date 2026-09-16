"""Phase 18 — the new operator-facing surface, and attacks on it.

Two endpoints the dashboard depends on: the capability catalogue it configures
against, and the decision simulator that replaced a Playground page which could
not work (it POSTed to gateway routes the browser and the control plane both
have no route to).

The simulator is the one worth attacking. It answers "what would Aegis decide?"
using the same engines the gateway uses, so the risk is not that it is wrong but
that it could become a second security model, leak across tenants, or leave a
trail of actions that never happened.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import models
from app.database import SessionLocal
from app.main import create_app
from app.seed import DEMO_EMAIL, DEMO_PASSWORD


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app("all")) as instance:
        yield instance


def _operator(client: TestClient) -> dict:
    token = client.post(
        "/api/auth/login", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _rival(client: TestClient) -> dict:
    response = client.post(
        "/api/auth/register",
        json={
            "organization_name": f"Rival {uuid4().hex[:6]}",
            "full_name": "Rae",
            "email": f"rae-{uuid4().hex[:8]}@rival.test",
            "password": "rival-pass",
        },
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _governed_agent(client: TestClient, name: str) -> tuple[str, str]:
    """An agent with crm READ allowed and UPDATE requiring a human."""
    headers = _operator(client)
    created = client.post(
        "/api/agents",
        headers=headers,
        json={"name": name, "provider": "d", "model": "m", "description": ""},
    ).json()
    agent_id, token = created["agent"]["id"], created["token"]
    for action in ("READ", "UPDATE"):
        client.post(
            f"/api/agents/{agent_id}/permissions",
            headers=headers,
            json={
                "resource_kind": "crm",
                "action": action,
                "scope": "customers",
                "effect": "allow",
            },
        )
    client.post(
        f"/api/agents/{agent_id}/contracts",
        headers=headers,
        json={
            "organization_id": "x",
            "agent_id": "x",
            "contract_id": f"c-{uuid4().hex[:8]}",
            "version": 1,
            "status": "ACTIVE",
            "purpose": "simulation fixture",
            "capabilities": [
                {"name": "crm", "resource_kind": "crm", "actions": ["READ", "UPDATE"]}
            ],
            "resources": [{"kind": "crm", "scope": "customers"}],
            "constraints": {},
            "data_constraints": {"denied_fields": ["ssn"]},
            "approval_rules": [
                {"resource_kind": "crm", "action": "UPDATE", "require": "human"}
            ],
        },
    )
    return agent_id, token


# ---------------------------------------------------------------------------
# Capability catalogue
# ---------------------------------------------------------------------------


def test_catalogue_reports_what_the_backend_supports(client):
    body = client.get("/api/capabilities", headers=_operator(client)).json()
    pairs = {
        (cap["resource_kind"], cap["action"]) for cap in body["capabilities"]
    }
    assert ("crm", "READ") in pairs
    assert ("payments", "TRANSFER") in pairs
    assert "crm" in body["executable_tools"]


def test_catalogue_distinguishes_enforceable_from_decision_only(client):
    """Only crm is wired to a tool. Showing the rest identically would imply
    an enforcement that does not exist."""
    body = client.get("/api/capabilities", headers=_operator(client)).json()
    by_pair = {
        (cap["resource_kind"], cap["action"]): cap for cap in body["capabilities"]
    }
    assert by_pair[("crm", "READ")]["enforceable"] is True
    assert by_pair[("payments", "TRANSFER")]["enforceable"] is False
    assert by_pair[("email", "SEND")]["enforceable"] is False


def test_catalogue_marks_irreversible_actions(client):
    body = client.get("/api/capabilities", headers=_operator(client)).json()
    by_pair = {
        (cap["resource_kind"], cap["action"]): cap for cap in body["capabilities"]
    }
    assert by_pair[("crm", "DELETE")]["irreversible"] is True
    assert by_pair[("crm", "READ")]["irreversible"] is False


def test_catalogue_requires_an_operator(client):
    assert client.get("/api/capabilities").status_code == 401


# ---------------------------------------------------------------------------
# Simulation agrees with the real engines
# ---------------------------------------------------------------------------


def test_simulation_matches_the_real_decision(client):
    """The simulator must not become a second security model."""
    agent_id, token = _governed_agent(client, "Sim Agrees")
    headers = _operator(client)

    for resource, action, scope in (
        ("crm", "READ", "customers"),
        ("crm", "DELETE", "all"),
        ("payments", "TRANSFER", "*"),
    ):
        simulated = client.post(
            f"/api/agents/{agent_id}/simulate",
            headers=headers,
            json={"resource_kind": resource, "action": action, "scope": scope},
        ).json()
        real = client.post(
            "/api/authorize",
            headers={"X-Agent-Token": token},
            json={"resource_kind": resource, "action": action, "scope": scope},
        ).json()
        assert simulated["decision"] == real["decision"], (
            f"{resource}.{action}: simulation said {simulated['decision']}, "
            f"the engine said {real['decision']}"
        )


def test_simulation_reports_contract_required_approval(client):
    """The contract raising an ALLOW to APPROVAL, attributed to the contract.

    Deliberately uses crm.READ, which organization policy allows. Using UPDATE
    would be ambiguous: a seeded org-wide policy already sends it to a human, so
    the contract layer would have no ALLOW left to raise and the test would pass
    or fail depending on which policies other tests had created.
    """
    headers = _operator(client)
    created = client.post(
        "/api/agents",
        headers=headers,
        json={"name": "Sim Approval", "provider": "d", "model": "m", "description": ""},
    ).json()
    agent_id = created["agent"]["id"]
    client.post(
        f"/api/agents/{agent_id}/permissions",
        headers=headers,
        json={
            "resource_kind": "crm",
            "action": "READ",
            "scope": "customers",
            "effect": "allow",
        },
    )
    client.post(
        f"/api/agents/{agent_id}/contracts",
        headers=headers,
        json={
            "organization_id": "x",
            "agent_id": "x",
            "contract_id": f"c-{uuid4().hex[:8]}",
            "version": 1,
            "status": "ACTIVE",
            "purpose": "reads are reviewed for this agent",
            "capabilities": [
                {"name": "crm", "resource_kind": "crm", "actions": ["READ"]}
            ],
            "resources": [{"kind": "crm", "scope": "customers"}],
            "constraints": {},
            "data_constraints": {},
            "approval_rules": [
                {"resource_kind": "crm", "action": "READ", "require": "human"}
            ],
        },
    )

    result = client.post(
        f"/api/agents/{agent_id}/simulate",
        headers=headers,
        json={"resource_kind": "crm", "action": "READ", "scope": "customers"},
    ).json()
    layers = {step["layer"]: step["outcome"] for step in result["steps"]}
    assert layers["policy"] == "ALLOW", "fixture assumes policy allows crm.READ"
    assert layers["contract"] == "APPROVAL"
    assert result["decision"] == "APPROVAL"


def test_simulation_explains_each_layer(client):
    agent_id, _ = _governed_agent(client, "Sim Layers")
    result = client.post(
        f"/api/agents/{agent_id}/simulate",
        headers=_operator(client),
        json={"resource_kind": "crm", "action": "READ", "scope": "customers"},
    ).json()
    layers = [step["layer"] for step in result["steps"]]
    assert layers == ["identity", "permission", "policy", "contract"]
    assert result["decision"] == "ALLOW"


def test_simulation_flags_its_own_limits(client):
    agent_id, _ = _governed_agent(client, "Sim Honest")
    result = client.post(
        f"/api/agents/{agent_id}/simulate",
        headers=_operator(client),
        json={"resource_kind": "crm", "action": "READ", "scope": "customers"},
    ).json()
    assert result["advisory"] is True
    assert result["models_trajectory"] is False


def test_simulation_sees_revocation(client):
    agent_id, _ = _governed_agent(client, "Sim Revoked")
    headers = _operator(client)
    client.post(f"/api/agents/{agent_id}/revoke", headers=headers)
    result = client.post(
        f"/api/agents/{agent_id}/simulate",
        headers=headers,
        json={"resource_kind": "crm", "action": "READ", "scope": "customers"},
    ).json()
    assert result["decision"] == "BLOCK"
    assert result["steps"][0]["layer"] == "identity"


def test_simulation_sees_contract_revocation(client):
    agent_id, _ = _governed_agent(client, "Sim Contract Revoked")
    headers = _operator(client)
    contracts = client.get(f"/api/agents/{agent_id}/contracts", headers=headers).json()
    target = contracts[0]
    client.post(
        f"/api/agents/{agent_id}/contracts/{target['contract_id']}/{target['version']}/status",
        headers=headers,
        json={"status": "REVOKED"},
    )
    result = client.post(
        f"/api/agents/{agent_id}/simulate",
        headers=headers,
        json={"resource_kind": "crm", "action": "READ", "scope": "customers"},
    ).json()
    assert result["decision"] == "BLOCK"
    assert "no runtime contract" in result["reason"].lower()


# ---------------------------------------------------------------------------
# Simulation writes nothing
# ---------------------------------------------------------------------------


def test_simulation_leaves_no_trace(client):
    """A simulation that wrote evidence would put actions in the audit record
    that never happened."""
    agent_id, _ = _governed_agent(client, "Sim No Trace")
    session = SessionLocal()
    try:
        before = (
            session.query(models.Event).count(),
            session.query(models.Execution).count(),
            session.query(models.Approval).count(),
        )
    finally:
        session.close()

    for action in ("READ", "UPDATE", "DELETE"):
        client.post(
            f"/api/agents/{agent_id}/simulate",
            headers=_operator(client),
            json={"resource_kind": "crm", "action": action, "scope": "customers"},
        )

    session = SessionLocal()
    try:
        after = (
            session.query(models.Event).count(),
            session.query(models.Execution).count(),
            session.query(models.Approval).count(),
        )
    finally:
        session.close()
    assert before == after, "simulation wrote to the audit record"


# ---------------------------------------------------------------------------
# Attacks
# ---------------------------------------------------------------------------


def test_agent_token_cannot_simulate(client):
    """Simulation is an operator tool. An agent must not use it to probe its own
    authority boundaries cheaply, or to reach the control plane at all."""
    agent_id, token = _governed_agent(client, "Sim Agent Token")
    response = client.post(
        f"/api/agents/{agent_id}/simulate",
        headers={"X-Agent-Token": token},
        json={"resource_kind": "crm", "action": "READ", "scope": "customers"},
    )
    assert response.status_code == 403


def test_unauthenticated_simulation_is_refused(client):
    agent_id, _ = _governed_agent(client, "Sim Anon")
    response = client.post(
        f"/api/agents/{agent_id}/simulate",
        json={"resource_kind": "crm", "action": "READ", "scope": "customers"},
    )
    assert response.status_code == 401


def test_rival_tenant_cannot_simulate_our_agent(client):
    agent_id, _ = _governed_agent(client, "Sim Tenant")
    response = client.post(
        f"/api/agents/{agent_id}/simulate",
        headers=_rival(client),
        json={"resource_kind": "crm", "action": "READ", "scope": "customers"},
    )
    assert response.status_code == 404


def test_simulation_of_an_unknown_agent_is_refused(client):
    response = client.post(
        f"/api/agents/{uuid4()}/simulate",
        headers=_operator(client),
        json={"resource_kind": "crm", "action": "READ", "scope": "customers"},
    )
    assert response.status_code == 404


def test_simulation_cannot_be_steered_by_extra_body_fields(client):
    """Identity and decision come from the server, never from the request."""
    agent_id, _ = _governed_agent(client, "Sim Steering")
    result = client.post(
        f"/api/agents/{agent_id}/simulate",
        headers=_operator(client),
        json={
            "resource_kind": "crm",
            "action": "DELETE",
            "scope": "all",
            "decision": "ALLOW",
            "organization_id": "another-org",
            "agent_id": "another-agent",
        },
    ).json()
    assert result["decision"] == "BLOCK"


@pytest.mark.parametrize(
    "payload",
    [
        {"resource_kind": "crm"},
        {"resource_kind": "crm", "action": "READ"},
        {"resource_kind": ["crm"], "action": "READ", "scope": "c"},
        {"resource_kind": "crm", "action": "READ", "scope": "c", "payload": "nope"},
    ],
)
def test_malformed_simulation_never_allows(client, payload):
    agent_id, _ = _governed_agent(client, f"Sim Malformed {uuid4().hex[:6]}")
    response = client.post(
        f"/api/agents/{agent_id}/simulate", headers=_operator(client), json=payload
    )
    assert response.status_code in (200, 400, 422)
    if response.status_code == 200:
        assert response.json()["decision"] != "ALLOW"
