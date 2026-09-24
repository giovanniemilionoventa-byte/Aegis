"""Phase 19 — the canonical Gmail policy, and the layers under it.

    gmail.search  ALLOW
    gmail.read    ALLOW
    gmail.draft   ALLOW
    gmail.send    APPROVAL_REQUIRED
    gmail.delete  DENY

Every assertion here is about a *real side effect*, not a return value: the
Gmail stand-in records what was actually sent, drafted or trashed, and the
tests check that ledger. A decision that says BLOCK while a message leaves the
mailbox would pass a response-shape test and fail these.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.protected.gmail import gmail_connector

from .gmail_fake import FakeGoogle
from .phase19_harness import approve, build_tenant, call, connect_gmail, isolate_oauth_store, use_fake_google


@pytest.fixture
def client():
    with TestClient(create_app("all")) as instance:
        yield instance


@pytest.fixture
def fake(tmp_path, monkeypatch):
    isolate_oauth_store(tmp_path, monkeypatch)
    google = FakeGoogle()
    google.add_message(
        message_id="m-1",
        sender="Marco <marco@example.test>",
        subject="Quarterly numbers",
        body="Here are the numbers you asked for.",
    )
    use_fake_google(monkeypatch, google)
    return google


@pytest.fixture
def tenant(client, fake):
    return build_tenant(client)


# -- ALLOW -----------------------------------------------------------------


def test_search_is_allowed_and_really_runs(client, tenant, fake):
    response = call(client, tenant, "search", payload={"query": "marco"})
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "ALLOW"
    assert body["executed"] is True
    assert body["result"]["count"] == 1
    assert body["result"]["messages"][0]["message_id"] == "m-1"
    # The connector really talked to the Gmail endpoint.
    assert any("/messages" in url for _, url in fake.requests)


def test_read_is_allowed_and_returns_the_message(client, tenant, fake):
    response = call(client, tenant, "read", payload={"message_id": "m-1"})
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "ALLOW"
    assert body["executed"] is True
    assert body["result"]["subject"] == "Quarterly numbers"


def test_draft_is_allowed_and_creates_a_real_draft(client, tenant, fake):
    response = call(
        client,
        tenant,
        "draft",
        payload={
            "to": "marco@example.test",
            "subject": "Re: Quarterly numbers",
            "body": "Thanks, received.",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "ALLOW"
    assert body["executed"] is True
    assert body["result"]["sent"] is False
    # A draft exists; nothing was sent.
    assert len(fake.drafts) == 1
    assert fake.sent == []


# -- APPROVAL_REQUIRED -----------------------------------------------------


def test_send_requires_approval_and_sends_nothing(client, tenant, fake):
    response = call(
        client,
        tenant,
        "send",
        payload={"to": "marco@example.test", "subject": "Hi", "body": "Hello"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "APPROVAL"
    assert body["executed"] is False
    assert body["approval_id"]
    # The decisive assertion: Google was never asked to send.
    assert fake.sent == []


def test_send_executes_only_after_a_human_approves(client, tenant, fake):
    execution_id = str(uuid4())
    request_id = f"send-{uuid4().hex[:8]}"
    first = call(
        client,
        tenant,
        "send",
        payload={"to": "marco@example.test", "subject": "Hi", "body": "Hello"},
        execution_id=execution_id,
        request_id=request_id,
    ).json()
    assert first["decision"] == "APPROVAL"
    assert fake.sent == []

    decided = approve(client, tenant, first["approval_id"])
    assert decided.status_code == 200
    assert decided.json()["status"] == "approved"
    # Approving does not send. The agent re-submits, and that is when it runs.
    assert fake.sent == []

    second = call(
        client,
        tenant,
        "send",
        payload={"to": "marco@example.test", "subject": "Hi", "body": "Hello"},
        execution_id=execution_id,
        request_id=request_id,
    ).json()
    assert second["decision"] == "ALLOW"
    assert second["executed"] is True
    assert len(fake.sent) == 1
    assert "marco@example.test" in fake.sent[0]["raw"]


def test_approval_is_single_use(client, tenant, fake):
    execution_id = str(uuid4())
    request_id = f"send-{uuid4().hex[:8]}"
    payload = {"to": "marco@example.test", "subject": "Once", "body": "Only once"}
    first = call(
        client, tenant, "send", payload=payload,
        execution_id=execution_id, request_id=request_id,
    ).json()
    approve(client, tenant, first["approval_id"])

    redeemed = call(
        client, tenant, "send", payload=payload,
        execution_id=execution_id, request_id=request_id,
    ).json()
    assert redeemed["executed"] is True
    assert len(fake.sent) == 1

    replayed = call(
        client, tenant, "send", payload=payload,
        execution_id=execution_id, request_id=request_id,
    ).json()
    assert replayed["executed"] is False
    # Still exactly one send. The grant was spent.
    assert len(fake.sent) == 1


def test_approval_does_not_authorize_a_different_message(client, tenant, fake):
    """A human approved one email. It is not authority for another."""
    execution_id = str(uuid4())
    request_id = f"send-{uuid4().hex[:8]}"
    approved = call(
        client,
        tenant,
        "send",
        payload={"to": "marco@example.test", "subject": "Agreed", "body": "Yes"},
        execution_id=execution_id,
        request_id=request_id,
    ).json()
    approve(client, tenant, approved["approval_id"])

    swapped = call(
        client,
        tenant,
        "send",
        payload={"to": "attacker@evil.test", "subject": "Agreed", "body": "Yes"},
        execution_id=execution_id,
        request_id=request_id,
    )
    # Same idempotency key, different payload: refused outright.
    assert swapped.status_code == 409
    assert fake.sent == []


# -- DENY ------------------------------------------------------------------


def test_delete_is_denied_by_least_privilege(client, tenant, fake):
    """Layer 1: the agent holds no DELETE permission."""
    before = gmail_connector.call_count
    response = call(client, tenant, "delete", payload={"message_id": "m-1"})
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "BLOCK"
    assert body["executed"] is False
    assert fake.trashed == []
    # The connector was never entered at all.
    assert gmail_connector.call_count == before


def test_delete_is_denied_by_policy_even_with_the_permission(client, fake):
    """Layer 2: an agent that *was* granted DELETE is still refused.

    This is the layer that matters. Least privilege is the first floor, but an
    operator can misconfigure it; the organization policy is what makes
    gmail.delete unreachable regardless.
    """
    tenant = build_tenant(
        client,
        name="Over-permissioned Assistant",
        actions=("SEARCH", "READ", "DRAFT", "SEND", "DELETE"),
    )
    before = gmail_connector.call_count
    body = call(client, tenant, "delete", payload={"message_id": "m-1"}).json()
    assert body["decision"] == "BLOCK"
    assert body["executed"] is False
    assert "Gmail delete is never allowed" in body["reason"]
    assert fake.trashed == []
    assert gmail_connector.call_count == before


def test_delete_is_outside_the_runtime_contract(client, fake):
    """Layer 3: with the policy gone, the contract still refuses.

    Built by granting the permission and giving the contract no DELETE
    capability, then removing the blocking policy — so the only thing left
    standing between the agent and the mailbox is the runtime contract.
    """
    from .phase19_harness import create_gmail_agent, register_operator, Tenant

    operator = register_operator(client)
    # Deliberately no canonical policy installed for this tenant.
    agent_id, agent_token, contract_id = create_gmail_agent(
        client,
        operator,
        name="Contract Bounded Assistant",
        actions=("SEARCH", "READ", "DRAFT", "SEND", "DELETE"),
        contract_actions=("SEARCH", "READ", "DRAFT", "SEND"),
    )
    organization_id = (
        client.get(f"/api/agents/{agent_id}", headers=operator).json()["organization_id"]
    )
    # Phase 20: every new organization now starts with the starter policies,
    # including "Gmail delete is never allowed". This test isolates the CONTRACT
    # layer, so it removes the policy layer first, as the docstring above says.
    from app import models
    from app.database import SessionLocal

    session = SessionLocal()
    try:
        session.query(models.Policy).filter(
            models.Policy.organization_id == organization_id
        ).delete()
        session.commit()
    finally:
        session.close()
    connect_gmail(organization_id)
    tenant = Tenant(
        organization_id=organization_id,
        operator=operator,
        agent_id=agent_id,
        agent_token=agent_token,
        contract_id=contract_id,
    )

    before = gmail_connector.call_count
    body = call(client, tenant, "delete", payload={"message_id": "m-1"}).json()
    assert body["decision"] == "BLOCK"
    assert body["executed"] is False
    assert "contract" in body["reason"].lower()
    assert fake.trashed == []
    assert gmail_connector.call_count == before


def test_the_five_operations_are_the_whole_surface(client, tenant):
    """There is no sixth operation, and no way to name an endpoint."""
    for attempt in (
        "execute",
        "http",
        "list",
        "trash",
        "modify",
        "batchDelete",
        "..%2fprofile",
    ):
        response = call(client, tenant, attempt, payload={})
        assert response.status_code in (400, 404), attempt
