"""Shared setup for the Phase 19 Gmail tests.

Each test module builds its own tenant through the real public API — register,
create policies, create an agent, grant permissions, write a runtime contract —
so nothing here depends on the seed having run, on the order tests execute in,
or on state left in the shared SQLite file by another module.

The canonical Phase 19 posture this installs is the one from the phase brief:

    gmail.search  ALLOW
    gmail.read    ALLOW
    gmail.draft   ALLOW
    gmail.send    APPROVAL_REQUIRED
    gmail.delete  DENY
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from uuid import uuid4

from fastapi.testclient import TestClient

from app import config, gmail_store
from app.protected.gmail import gmail_connector

GMAIL_SCOPE = "mailbox"


@dataclass
class Tenant:
    organization_id: str
    operator: dict
    agent_id: str
    agent_token: str
    contract_id: str

    @property
    def agent_headers(self) -> dict:
        return {"X-Agent-Token": self.agent_token}

    _executions: list = field(default_factory=list)


def isolate_oauth_store(tmp_path: Path, monkeypatch) -> Path:
    """Point the credential store at a temp file and give it a real key.

    The OAuth client id/secret below are test fixtures, not credentials: they
    are generated per run, they authenticate against the Gmail stand-in only,
    and no real Google client would accept them.
    """
    target = tmp_path / "gmail_connections.json"
    monkeypatch.setattr(config, "GMAIL_OAUTH_STORE_PATH", str(target))
    monkeypatch.setattr(
        config, "GMAIL_OAUTH_ENCRYPTION_KEY", "phase19-test-oauth-key-" + uuid4().hex
    )
    monkeypatch.setattr(config, "GOOGLE_OAUTH_CLIENT_ID", "test-client-id.invalid")
    monkeypatch.setattr(
        config, "GOOGLE_OAUTH_CLIENT_SECRET", "test-client-secret-" + uuid4().hex[:12]
    )
    return target


def connect_gmail(
    organization_id: str,
    *,
    email: str = "mailbox@example.test",
    refresh_token: str = "stand-in-refresh-token",
) -> None:
    """Record a Gmail connection the way the OAuth callback would."""
    gmail_store.save_connection(
        organization_id=organization_id,
        google_email=email,
        refresh_token=refresh_token,
        scopes=["https://www.googleapis.com/auth/gmail.modify"],
        connected_by=None,
    )
    gmail_connector.forget_tokens(organization_id)


def use_fake_google(monkeypatch, fake) -> None:
    """Drive the real connector against the Gmail stand-in."""
    monkeypatch.setattr(gmail_connector, "_transport", fake, raising=False)
    gmail_connector.forget_tokens()
    gmail_connector.calls.clear()


def register_operator(client: TestClient) -> dict:
    response = client.post(
        "/api/auth/register",
        json={
            "organization_name": f"Gmail Tenant {uuid4().hex[:6]}",
            "full_name": "Ops Person",
            "email": f"ops-{uuid4().hex[:10]}@tenant.test",
            "password": "tenant-pass",
        },
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def install_canonical_policy(client: TestClient, operator: dict) -> None:
    """gmail.delete is denied; gmail.send needs a human. Nothing else."""
    for policy in (
        {
            "name": "Gmail delete is never allowed",
            "description": "Deleting mail is not an agent action.",
            "resource_kind": "gmail",
            "action": "DELETE",
            "scope_pattern": "*",
            "decision": "BLOCK",
            "priority": 1,
        },
        {
            "name": "Sending mail needs a human",
            "description": "An agent may compose; a person decides to send.",
            "resource_kind": "gmail",
            "action": "SEND",
            "scope_pattern": "*",
            "decision": "APPROVAL",
            "priority": 3,
        },
    ):
        created = client.post("/api/policies", headers=operator, json=policy)
        assert created.status_code == 200, created.text


def create_gmail_agent(
    client: TestClient,
    operator: dict,
    *,
    name: str = "Gmail Assistant",
    actions: tuple[str, ...] = ("SEARCH", "READ", "DRAFT", "SEND"),
    contract_actions: Optional[tuple[str, ...]] = None,
) -> tuple[str, str, str]:
    """Agent + least-privilege permissions + an ACTIVE runtime contract."""
    created = client.post(
        "/api/agents",
        headers=operator,
        json={
            "name": name,
            "provider": "external-llm",
            "model": "test-model",
            "description": "Untrusted external agent.",
        },
    )
    assert created.status_code == 200, created.text
    agent_id = created.json()["agent"]["id"]
    agent_token = created.json()["token"]

    for action in actions:
        granted = client.post(
            f"/api/agents/{agent_id}/permissions",
            headers=operator,
            json={
                "resource_kind": "gmail",
                "action": action,
                "scope": GMAIL_SCOPE,
                "effect": "allow",
            },
        )
        assert granted.status_code == 200, granted.text

    contract_id = f"gmail-{uuid4().hex[:8]}"
    contract = client.post(
        f"/api/agents/{agent_id}/contracts",
        headers=operator,
        json={
            "organization_id": "ignored",
            "agent_id": "ignored",
            "contract_id": contract_id,
            "version": 1,
            "status": "ACTIVE",
            "purpose": "Read mail, draft replies, send only what a human approved.",
            "capabilities": [
                {
                    "name": "gmail",
                    "resource_kind": "gmail",
                    "actions": list(contract_actions or actions),
                }
            ],
            "resources": [{"kind": "gmail", "scope": GMAIL_SCOPE}],
            "constraints": {"payload_size": {"max_bytes": 16384}},
            "data_constraints": {},
            "approval_rules": [
                {"resource_kind": "gmail", "action": "SEND", "require": "human"}
            ],
        },
    )
    assert contract.status_code in (200, 201), contract.text
    return agent_id, agent_token, contract_id


def build_tenant(
    client: TestClient, *, refresh_token: str = "stand-in-refresh-token", **kwargs
) -> Tenant:
    operator = register_operator(client)
    install_canonical_policy(client, operator)
    agent_id, agent_token, contract_id = create_gmail_agent(client, operator, **kwargs)
    organization_id = (
        client.get(f"/api/agents/{agent_id}", headers=operator)
        .json()["organization_id"]
    )
    connect_gmail(organization_id, refresh_token=refresh_token)
    return Tenant(
        organization_id=organization_id,
        operator=operator,
        agent_id=agent_id,
        agent_token=agent_token,
        contract_id=contract_id,
    )


def call(
    client: TestClient,
    tenant: Tenant,
    operation: str,
    *,
    payload: Optional[dict] = None,
    execution_id: Optional[str] = None,
    request_id: Optional[str] = None,
    intent: Optional[str] = None,
    scope: str = GMAIL_SCOPE,
    token: Optional[str] = None,
    extra_metadata: Optional[dict] = None,
):
    """One typed Gmail request through the enforcement gateway."""
    metadata: dict = {}
    if intent is not None:
        metadata["declared_intent"] = intent
    if extra_metadata:
        metadata.update(extra_metadata)
    body = {
        "scope": scope,
        "payload": payload or {},
        "metadata": metadata or None,
        "execution_id": execution_id,
        "request_id": request_id,
    }
    return client.post(
        f"/api/gateway/tools/gmail/{operation}",
        headers={"X-Agent-Token": token or tenant.agent_token},
        json=body,
    )


def approve(client: TestClient, tenant: Tenant, approval_id: str):
    return client.post(
        f"/api/approvals/{approval_id}/decide",
        headers=tenant.operator,
        json={"decision": "ALLOW"},
    )


def store_contents(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text())
