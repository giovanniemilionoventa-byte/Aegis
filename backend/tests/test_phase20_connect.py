"""Phase 20 -- connecting an agent without reading the source.

The owner's own live run needed two hand fixes before anything worked: a seed
that never ran on an existing database, and a mailbox status the gateway could
not see. The rest of the friction was quieter: no address to paste, no sign the
first call arrived, twelve capability rows to fill in by hand, and timestamps
that made a two-minute-old request read "121 min ago".
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import config, models, seed
from app.database import SessionLocal
from app.main import create_app

PASSWORD = "a-long-enough-password"
INTERNAL = "pilot-c.test"


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app("all")) as instance:
        yield instance


def _operator(client: TestClient) -> dict:
    suffix = uuid4().hex[:8]
    registered = client.post(
        "/api/auth/register",
        json={
            "organization_name": f"Connect {suffix}",
            "full_name": "Boss",
            "email": f"boss-{suffix}@{INTERNAL}",
            "password": PASSWORD,
        },
    )
    return {"Authorization": f"Bearer {registered.json()['access_token']}"}


def _plain_agent(client: TestClient, headers: dict, **extra):
    created = client.post(
        "/api/agents",
        headers=headers,
        json={"name": "Connector", "provider": "d", "model": "m", "description": "", **extra},
    )
    body = created.json()
    return created.status_code, body


def _ask(client, token, kind, action, scope, payload=None):
    body = {"resource_kind": kind, "action": action, "scope": scope}
    if payload is not None:
        body["payload"] = payload
    return client.post("/api/authorize", headers={"X-Agent-Token": token}, json=body).json()


# ---------------------------------------------------------------------------
# Is it connected yet?
# ---------------------------------------------------------------------------


def test_last_seen_starts_empty_and_follows_the_first_call(client):
    headers = _operator(client)
    _, created = _plain_agent(client, headers)
    agent_id, token = created["agent"]["id"], created["token"]
    assert client.get(f"/api/agents/{agent_id}", headers=headers).json()["last_seen_at"] is None
    _ask(client, token, "crm", "READ", "customers")
    seen = client.get(f"/api/agents/{agent_id}", headers=headers).json()["last_seen_at"]
    assert seen is not None and seen.endswith("Z")


def test_last_seen_is_not_rewritten_on_every_request(client):
    headers = _operator(client)
    _, created = _plain_agent(client, headers)
    agent_id, token = created["agent"]["id"], created["token"]
    _ask(client, token, "crm", "READ", "customers")
    first = client.get(f"/api/agents/{agent_id}", headers=headers).json()["last_seen_at"]
    _ask(client, token, "crm", "READ", "customers")
    second = client.get(f"/api/agents/{agent_id}", headers=headers).json()["last_seen_at"]
    assert first == second, "one write per few seconds, not one per request"


def test_a_refused_credential_does_not_mark_the_agent_as_seen(client):
    headers = _operator(client)
    _, created = _plain_agent(client, headers)
    agent_id, token = created["agent"]["id"], created["token"]
    client.post(f"/api/agents/{agent_id}/revoke", headers=headers)
    refused = client.post(
        "/api/authorize",
        headers={"X-Agent-Token": token},
        json={"resource_kind": "crm", "action": "READ", "scope": "customers"},
    )
    assert refused.status_code == 401
    assert client.get(f"/api/agents/{agent_id}", headers=headers).json()["last_seen_at"] is None


# ---------------------------------------------------------------------------
# What to paste
# ---------------------------------------------------------------------------


def test_setup_gives_the_gateway_address_and_ready_snippets(client, monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_GATEWAY_URL", "https://gateway.pilot.test")
    headers = _operator(client)
    _, created = _plain_agent(client, headers)
    agent_id, token = created["agent"]["id"], created["token"]
    setup = client.get(f"/api/agents/{agent_id}/setup", headers=headers).json()
    assert setup["gateway_url"] == "https://gateway.pilot.test"
    assert setup["authorize_url"] == "https://gateway.pilot.test/api/authorize"
    curl, python = setup["snippets"]["curl"], setup["snippets"]["python"]
    assert "https://gateway.pilot.test/api/authorize" in curl
    assert '"https://gateway.pilot.test"' in python and "/api/authorize" in python
    for snippet in (curl, python):
        assert "X-Agent-Token" in snippet and "YOUR_AGENT_TOKEN" in snippet
        assert "request_id" in snippet
    assert token not in json.dumps(setup), "the real token is shown once, never again"


def test_setup_without_a_public_address_says_so(client, monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_GATEWAY_URL", "")
    headers = _operator(client)
    _, created = _plain_agent(client, headers)
    setup = client.get(f"/api/agents/{created['agent']['id']}/setup", headers=headers).json()
    assert setup["gateway_url"] is None
    assert "YOUR-GATEWAY-HOST" in setup["snippets"]["curl"]


# ---------------------------------------------------------------------------
# A sensible starting authority
# ---------------------------------------------------------------------------


def test_an_agent_without_a_preset_still_starts_with_nothing(client):
    headers = _operator(client)
    _, created = _plain_agent(client, headers)
    agent_id = created["agent"]["id"]
    assert client.get(f"/api/agents/{agent_id}/permissions", headers=headers).json() == []
    assert client.get(f"/api/agents/{agent_id}/contracts/active", headers=headers).status_code == 404


def test_an_unknown_preset_is_refused_and_creates_nothing(client):
    headers = _operator(client)
    before = len(client.get("/api/agents", headers=headers).json())
    status, _ = _plain_agent(client, headers, preset="root")
    assert status == 422
    assert len(client.get("/api/agents", headers=headers).json()) == before


@pytest.fixture
def recommended(client):
    headers = _operator(client)
    status, created = _plain_agent(client, headers, preset="recommended")
    assert status == 200, created
    return headers, created["agent"]["id"], created["token"]


def test_the_recommended_preset_grants_reading_and_never_the_dangerous_actions(client, recommended):
    headers, agent_id, _ = recommended
    granted = {
        (p["resource_kind"], p["action"])
        for p in client.get(f"/api/agents/{agent_id}/permissions", headers=headers).json()
    }
    assert {("crm", "READ"), ("gmail", "SEARCH"), ("gmail", "READ"), ("files", "READ")} <= granted
    dangerous = {("crm", "DELETE"), ("payments", "TRANSFER"), ("files", "EXPORT"), ("gmail", "DELETE")}
    assert granted.isdisjoint(dangerous)
    assert client.get(f"/api/agents/{agent_id}/contracts/active", headers=headers).status_code == 200


def test_the_recommended_preset_decides_the_way_the_pitch_says(client, recommended):
    _, _, token = recommended
    inside = _ask(client, token, "email", "SEND", "internal", {"to": f"colleague@{INTERNAL}"})
    outside = _ask(client, token, "email", "SEND", "internal", {"to": "stranger@evil.test"})
    assert _ask(client, token, "crm", "READ", "customers")["decision"] == "ALLOW"
    assert inside["decision"] == "ALLOW"
    assert outside["decision"] == "APPROVAL"
    assert _ask(client, token, "crm", "UPDATE", "customers", {"id": "c-1"})["decision"] == "APPROVAL"
    assert _ask(client, token, "gmail", "SEND", "mailbox", {"to": f"colleague@{INTERNAL}"})["decision"] == "APPROVAL"
    for kind, action, scope in (
        ("crm", "DELETE", "all"),
        ("payments", "TRANSFER", "any"),
        ("gmail", "DELETE", "mailbox"),
        ("files", "EXPORT", "/Finance"),
    ):
        assert _ask(client, token, kind, action, scope)["decision"] == "BLOCK", (kind, action)


def test_two_presets_do_not_collide(client):
    headers = _operator(client)
    first = _plain_agent(client, headers, preset="recommended")[0]
    second = _plain_agent(client, headers, preset="recommended")[0]
    assert (first, second) == (200, 200)


def test_the_recommended_preset_leaves_out_gmail_where_gmail_is_not_offered(client, monkeypatch):
    # A hosted deployment has no Gmail connector: permissions for it would read
    # as authority ("send from Gmail: allowed") with nothing behind them.
    monkeypatch.setattr(config, "ENABLE_GMAIL", False)
    headers = _operator(client)
    status, created = _plain_agent(client, headers, preset="recommended")
    assert status == 200, created
    granted = {
        (p["resource_kind"], p["action"])
        for p in client.get(
            f"/api/agents/{created['agent']['id']}/permissions", headers=headers
        ).json()
    }
    assert ("crm", "READ") in granted
    assert not any(kind == "gmail" for kind, _ in granted)


# ---------------------------------------------------------------------------
# Times are unambiguous
# ---------------------------------------------------------------------------


def test_every_timestamp_the_api_returns_says_utc(client):
    headers = _operator(client)
    _, created = _plain_agent(client, headers, preset="recommended")
    token = created["token"]
    _ask(client, token, "crm", "UPDATE", "customers", {"id": "c-1"})
    samples = [
        client.get("/api/agents", headers=headers).json()[0]["created_at"],
        client.get("/api/events", headers=headers).json()[0]["created_at"],
        client.get("/api/approvals", headers=headers).json()[0]["created_at"],
        client.get("/api/approvals", headers=headers).json()[0]["expires_at"],
        client.get("/api/policies", headers=headers).json()[0]["created_at"],
        client.get("/api/auth/me", headers=headers).json()["organization"]["created_at"],
    ]
    assert all(value.endswith("Z") for value in samples), samples


# ---------------------------------------------------------------------------
# The seed that never ran
# ---------------------------------------------------------------------------


def _demo_counts(db):
    org = db.query(models.Organization).filter(models.Organization.slug == "acme").one()
    return {
        "resources": db.query(models.Resource)
        .filter(models.Resource.organization_id == org.id, models.Resource.kind == "gmail")
        .count(),
        "policies": db.query(models.Policy)
        .filter(
            models.Policy.organization_id == org.id,
            models.Policy.name == "Sending mail needs a human",
        )
        .count(),
        "agents": db.query(models.Agent)
        .filter(models.Agent.organization_id == org.id, models.Agent.name == "Gmail Assistant")
        .count(),
    }


def test_seeding_gmail_on_an_existing_database_is_idempotent(monkeypatch):
    monkeypatch.setattr(config, "GMAIL_AGENT_TOKEN", "aegis_test_gmail_agent_" + uuid4().hex)
    db = SessionLocal()
    try:
        seed.seed_gmail_if_missing(db)
        first = _demo_counts(db)
        seed.seed_gmail_if_missing(db)
        second = _demo_counts(db)
    finally:
        db.close()
    assert first == second, "running it again must not add anything"
    assert first["agents"] >= 1, "the Gmail assistant exists once its token is configured"
    assert first["resources"] == 1 and first["policies"] == 1


def test_seeding_gmail_without_a_token_creates_no_agent(monkeypatch):
    monkeypatch.setattr(config, "GMAIL_AGENT_TOKEN", "")
    db = SessionLocal()
    try:
        before = _demo_counts(db)["agents"]
        seed.seed_gmail_if_missing(db)
        assert _demo_counts(db)["agents"] == before
    finally:
        db.close()
