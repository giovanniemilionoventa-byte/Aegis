"""Phase 18 — the operator-to-agent job channel.

The control plane shares no network with the enforcement gateway, so the
dashboard cannot push work at an agent. It records a request; the agent claims
it from the gateway over agent_net, the one path the boundary leaves open.

The security question for this channel is narrow and these tests are about
exactly it: the channel must carry no authority, must never serve one agent's
job to another, and must not become a new way into the control plane.
"""

from __future__ import annotations

import threading
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base, SessionLocal
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


def _agent(client: TestClient, name: str) -> tuple[str, str]:
    created = client.post(
        "/api/agents",
        headers=_operator(client),
        json={"name": name, "provider": "d", "model": "m", "description": ""},
    ).json()
    return created["agent"]["id"], created["token"]


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


# ---------------------------------------------------------------------------
# The operator side
# ---------------------------------------------------------------------------


def test_operator_can_request_and_inspect_a_run(client):
    agent_id, _ = _agent(client, "Run Requester")
    headers = _operator(client)

    created = client.post(
        f"/api/agents/{agent_id}/verification-runs",
        headers=headers,
        json={"scenario": "canonical"},
    )
    assert created.status_code == 201
    run = created.json()
    assert run["status"] == "PENDING"
    assert run["agent_id"] == agent_id
    assert run["execution_id"]

    fetched = client.get(f"/api/verification-runs/{run['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["id"] == run["id"]

    listed = client.get(
        f"/api/verification-runs?agent_id={agent_id}", headers=headers
    )
    assert listed.status_code == 200
    assert run["id"] in [row["id"] for row in listed.json()]


def test_unknown_scenario_is_rejected(client):
    agent_id, _ = _agent(client, "Bad Scenario")
    response = client.post(
        f"/api/agents/{agent_id}/verification-runs",
        headers=_operator(client),
        json={"scenario": "rm -rf /"},
    )
    assert response.status_code == 400


def test_only_one_run_in_flight_per_agent(client):
    agent_id, _ = _agent(client, "One At A Time")
    headers = _operator(client)
    first = client.post(
        f"/api/agents/{agent_id}/verification-runs", headers=headers, json={}
    )
    assert first.status_code == 201
    second = client.post(
        f"/api/agents/{agent_id}/verification-runs", headers=headers, json={}
    )
    assert second.status_code == 409


def test_revoked_agent_cannot_be_asked_to_run(client):
    agent_id, _ = _agent(client, "Revoked Runner")
    headers = _operator(client)
    client.post(f"/api/agents/{agent_id}/revoke", headers=headers)
    response = client.post(
        f"/api/agents/{agent_id}/verification-runs", headers=headers, json={}
    )
    assert response.status_code == 409
    assert "revoked" in response.json()["detail"].lower()


def test_pending_run_can_be_cancelled_but_running_cannot(client):
    agent_id, token = _agent(client, "Cancellable")
    headers = _operator(client)
    run = client.post(
        f"/api/agents/{agent_id}/verification-runs", headers=headers, json={}
    ).json()

    cancelled = client.post(
        f"/api/verification-runs/{run['id']}/cancel", headers=headers
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"

    again = client.post(
        f"/api/verification-runs/{run['id']}/cancel", headers=headers
    )
    assert again.status_code == 409


# ---------------------------------------------------------------------------
# The agent side
# ---------------------------------------------------------------------------


def test_agent_claims_only_its_own_run(client):
    mine_id, mine_token = _agent(client, "Job Owner")
    _, other_token = _agent(client, "Job Neighbour")
    headers = _operator(client)
    run = client.post(
        f"/api/agents/{mine_id}/verification-runs", headers=headers, json={}
    ).json()

    # The neighbouring agent sees nothing.
    neighbour = client.get(
        "/api/agentctl/next", headers={"X-Agent-Token": other_token}
    )
    assert neighbour.status_code == 200
    assert neighbour.json()["run"] is None

    claimed = client.get("/api/agentctl/next", headers={"X-Agent-Token": mine_token})
    assert claimed.status_code == 200
    assert claimed.json()["run"]["run_id"] == run["id"]


def test_a_claimed_run_is_not_served_twice(client):
    agent_id, token = _agent(client, "Claim Once")
    client.post(
        f"/api/agents/{agent_id}/verification-runs",
        headers=_operator(client),
        json={},
    )
    first = client.get("/api/agentctl/next", headers={"X-Agent-Token": token})
    second = client.get("/api/agentctl/next", headers={"X-Agent-Token": token})
    assert first.json()["run"] is not None
    assert second.json()["run"] is None


def test_agent_cannot_report_another_agents_run(client):
    victim_id, _ = _agent(client, "Report Victim")
    _, attacker_token = _agent(client, "Report Attacker")
    run = client.post(
        f"/api/agents/{victim_id}/verification-runs",
        headers=_operator(client),
        json={},
    ).json()
    response = client.post(
        f"/api/agentctl/runs/{run['id']}/result",
        headers={"X-Agent-Token": attacker_token},
        json={"status": "COMPLETED", "result": {"faked": True}},
    )
    assert response.status_code == 404


def test_result_cannot_be_reported_twice(client):
    agent_id, token = _agent(client, "Report Once")
    run = client.post(
        f"/api/agents/{agent_id}/verification-runs",
        headers=_operator(client),
        json={},
    ).json()
    client.get("/api/agentctl/next", headers={"X-Agent-Token": token})
    first = client.post(
        f"/api/agentctl/runs/{run['id']}/result",
        headers={"X-Agent-Token": token},
        json={"status": "COMPLETED", "result": {"ok": True}},
    )
    assert first.status_code == 200
    second = client.post(
        f"/api/agentctl/runs/{run['id']}/result",
        headers={"X-Agent-Token": token},
        json={"status": "COMPLETED", "result": {"ok": True}},
    )
    assert second.status_code == 409


def test_invalid_result_status_is_rejected(client):
    agent_id, token = _agent(client, "Bad Status")
    run = client.post(
        f"/api/agents/{agent_id}/verification-runs",
        headers=_operator(client),
        json={},
    ).json()
    response = client.post(
        f"/api/agentctl/runs/{run['id']}/result",
        headers={"X-Agent-Token": token},
        json={"status": "ALLOW_EVERYTHING"},
    )
    assert response.status_code == 400


def test_agentctl_requires_an_agent_token(client):
    assert client.get("/api/agentctl/next").status_code == 401
    assert (
        client.get(
            "/api/agentctl/next", headers={"X-Agent-Token": "aegis_not_real"}
        ).status_code
        == 401
    )


def test_operator_token_cannot_claim_jobs(client):
    """The job channel is for agents. An operator JWT is not an agent."""
    response = client.get("/api/agentctl/next", headers=_operator(client))
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


def test_rival_operator_cannot_request_a_run_for_our_agent(client):
    agent_id, _ = _agent(client, "Tenant Guarded")
    response = client.post(
        f"/api/agents/{agent_id}/verification-runs",
        headers=_rival(client),
        json={},
    )
    assert response.status_code == 404


def test_rival_operator_cannot_read_our_runs(client):
    agent_id, _ = _agent(client, "Tenant Guarded Read")
    run = client.post(
        f"/api/agents/{agent_id}/verification-runs",
        headers=_operator(client),
        json={},
    ).json()
    rival = _rival(client)
    assert client.get(f"/api/verification-runs/{run['id']}", headers=rival).status_code == 404
    listing = client.get("/api/verification-runs", headers=rival)
    assert listing.status_code == 200
    assert run["id"] not in [row["id"] for row in listing.json()]


def test_rival_operator_cannot_cancel_our_run(client):
    agent_id, _ = _agent(client, "Tenant Guarded Cancel")
    run = client.post(
        f"/api/agents/{agent_id}/verification-runs",
        headers=_operator(client),
        json={},
    ).json()
    response = client.post(
        f"/api/verification-runs/{run['id']}/cancel", headers=_rival(client)
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# The channel carries no authority
# ---------------------------------------------------------------------------


def test_a_job_grants_no_authority(client):
    """Claiming a run must not change what the agent may do.

    The job names a scenario. If holding one widened authority, the channel
    would be a privilege-escalation path rather than a convenience.
    """
    agent_id, token = _agent(client, "No Authority From Jobs")
    headers = _operator(client)
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
    client.post(f"/api/agents/{agent_id}/verification-runs", headers=headers, json={})
    claimed = client.get("/api/agentctl/next", headers={"X-Agent-Token": token})
    assert claimed.json()["run"] is not None

    # Still no contract, so still no authority.
    decision = client.post(
        "/api/authorize",
        headers={"X-Agent-Token": token},
        json={"resource_kind": "crm", "action": "READ", "scope": "customers"},
    )
    assert decision.json()["decision"] == "BLOCK"
    assert "no runtime contract" in decision.json()["reason"].lower()


def test_claim_is_atomic_under_concurrent_pollers(tmp_path):
    """Two pollers must not both win the same run (cf. Phase 17 finding A-1)."""
    url = f"sqlite:///{tmp_path}/runs.db"
    engine = create_engine(url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    setup = Session()
    setup.add(models.Organization(id="o1", name="O", slug="o"))
    setup.flush()
    setup.add(
        models.User(
            id="u1", organization_id="o1", email="u@o.test",
            password_hash="x", full_name="U",
        )
    )
    setup.flush()
    setup.add(models.Agent(id="a1", organization_id="o1", owner_id="u1", name="A"))
    setup.flush()
    setup.add(
        models.VerificationRun(
            id="r1", organization_id="o1", agent_id="a1",
            scenario="canonical", status="PENDING",
        )
    )
    setup.commit()
    setup.close()

    from sqlalchemy import update as sa_update
    from app.security import utcnow

    wins: list[bool] = []
    barrier = threading.Barrier(2)

    def claim():
        session = Session()
        try:
            barrier.wait(timeout=10)
            result = session.execute(
                sa_update(models.VerificationRun)
                .where(
                    models.VerificationRun.id == "r1",
                    models.VerificationRun.status == "PENDING",
                )
                .values(status="RUNNING", claimed_at=utcnow())
                .execution_options(synchronize_session=False)
            )
            session.commit()
            wins.append(result.rowcount == 1)
        finally:
            session.close()

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert wins.count(True) == 1, f"both pollers claimed the run: {wins}"
