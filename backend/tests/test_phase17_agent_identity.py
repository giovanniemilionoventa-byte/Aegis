"""Phase 17 — agent credentials expire.

The Credential model has always carried expires_at and get_agent_from_token has
always checked it, but no creation path ever set it. Every agent token ever
issued was eternal: a leaked token stayed valid until a human noticed and
revoked it by hand. The check existed; nothing ever triggered it.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import config, models
from app.database import SessionLocal
from app.main import create_app
from app.seed import DEMO_EMAIL, DEMO_PASSWORD
from app.security import utcnow


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app("all")) as instance:
        yield instance


def _headers(client: TestClient) -> dict:
    token = client.post(
        "/api/auth/login", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _new_agent(client: TestClient, name: str) -> tuple[str, str]:
    created = client.post(
        "/api/agents",
        headers=_headers(client),
        json={"name": name, "provider": "demo", "model": "m", "description": ""},
    ).json()
    return created["agent"]["id"], created["token"]


def _credential(agent_id: str) -> models.Credential:
    session = SessionLocal()
    try:
        return (
            session.query(models.Credential)
            .filter(
                models.Credential.agent_id == agent_id,
                models.Credential.status == "active",
            )
            .one()
        )
    finally:
        session.close()


def test_new_agent_token_carries_an_expiry(client):
    agent_id, _ = _new_agent(client, "Expiring Identity")
    credential = _credential(agent_id)
    assert credential.expires_at is not None
    assert config.AGENT_TOKEN_TTL_DAYS > 0


def test_creation_response_reports_the_expiry(client):
    created = client.post(
        "/api/agents",
        headers=_headers(client),
        json={"name": "Reported Expiry", "provider": "demo", "model": "m", "description": ""},
    ).json()
    assert created["expires_at"] is not None


def test_rotated_token_also_expires(client):
    agent_id, _ = _new_agent(client, "Rotating Identity")
    rotated = client.post(
        f"/api/agents/{agent_id}/rotate", headers=_headers(client)
    ).json()
    assert rotated["expires_at"] is not None
    assert _credential(agent_id).expires_at is not None


def test_seeded_demo_tokens_expire():
    session = SessionLocal()
    try:
        eternal = (
            session.query(models.Credential)
            .filter(
                models.Credential.status == "active",
                models.Credential.expires_at.is_(None),
            )
            .count()
        )
    finally:
        session.close()
    assert eternal == 0, "an active credential was issued without an expiry"


def test_expired_token_is_rejected(client):
    """The check that never fired now fires."""
    agent_id, token = _new_agent(client, "Already Expired")
    client.post(
        f"/api/agents/{agent_id}/permissions",
        headers=_headers(client),
        json={
            "resource_kind": "crm",
            "action": "READ",
            "scope": "customers",
            "effect": "allow",
        },
    )

    session = SessionLocal()
    try:
        credential = (
            session.query(models.Credential)
            .filter(
                models.Credential.agent_id == agent_id,
                models.Credential.status == "active",
            )
            .one()
        )
        credential.expires_at = utcnow() - timedelta(seconds=1)
        session.commit()
    finally:
        session.close()

    response = client.post(
        "/api/authorize",
        headers={"X-Agent-Token": token},
        json={"resource_kind": "crm", "action": "READ", "scope": "customers"},
    )
    assert response.status_code == 401
    assert "expired" in response.json()["detail"].lower()


def test_unexpired_token_still_works(client):
    agent_id, token = _new_agent(client, "Still Valid")
    client.post(
        f"/api/agents/{agent_id}/permissions",
        headers=_headers(client),
        json={
            "resource_kind": "crm",
            "action": "READ",
            "scope": "customers",
            "effect": "allow",
        },
    )
    response = client.post(
        "/api/authorize",
        headers={"X-Agent-Token": token},
        json={"resource_kind": "crm", "action": "READ", "scope": "customers"},
    )
    # No contract yet, so BLOCK -- but authentication succeeded, which is the
    # point here: the token was accepted rather than rejected as expired.
    assert response.status_code == 200
    assert response.json()["decision"] == "BLOCK"


def test_revoked_token_is_rejected_regardless_of_expiry(client):
    agent_id, token = _new_agent(client, "Revoked Identity")
    client.post(f"/api/agents/{agent_id}/revoke", headers=_headers(client))
    response = client.post(
        "/api/authorize",
        headers={"X-Agent-Token": token},
        json={"resource_kind": "crm", "action": "READ", "scope": "customers"},
    )
    assert response.status_code == 401
