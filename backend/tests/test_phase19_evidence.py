"""Phase 19 — the record distinguishes what was said from what was done.

Four facts have to stay apart for an auditor to reconstruct an incident:

    REQUESTED   what arrived on the wire
    CANONICAL   what Aegis ruled on
    AUTHORIZED  the decision, and any approval behind it
    ACTUAL      what the connector really did, and to what

The tests below force them to disagree and check the record shows it. They also
check what is deliberately absent: no token, no client secret, and no message
body or subject in the evidence.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

from .gmail_fake import FakeGoogle
from .phase19_harness import approve, build_tenant, call, isolate_oauth_store, use_fake_google


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
        sender="marco@example.test",
        subject="Very Secret Subject Line",
        body="Extremely confidential body text that must not be stored.",
    )
    use_fake_google(monkeypatch, google)
    return google


@pytest.fixture
def tenant(client, fake):
    return build_tenant(client)


def _calls(client, tenant, execution_id):
    response = client.get(
        f"/api/executions/{execution_id}/connector-calls",
        headers=tenant.operator,
    )
    assert response.status_code == 200, response.text
    return response.json()["calls"]


def test_an_allowed_call_records_the_connector_operation(client, tenant, fake):
    execution_id = str(uuid4())
    call(
        client, tenant, "search", payload={"query": "marco"},
        execution_id=execution_id, intent="Searching for Marco's message.",
    )
    row = _calls(client, tenant, execution_id)[0]
    assert row["requested_operation"] == "gmail/search"
    assert row["canonical_operation"] == "gmail.SEARCH"
    assert row["decision"] == "ALLOW"
    assert row["executed"] is True
    assert row["connector_operation"] == "search"
    assert row["result_status"] == "ok"


def test_a_denied_call_records_no_connector_operation(client, tenant, fake):
    """connector_operation is NULL when nothing ran. That is the whole point."""
    execution_id = str(uuid4())
    call(client, tenant, "delete", payload={"message_id": "m-1"}, execution_id=execution_id)
    row = _calls(client, tenant, execution_id)[0]
    assert row["canonical_operation"] == "gmail.DELETE"
    assert row["decision"] == "BLOCK"
    assert row["executed"] is False
    assert row["connector_operation"] is None
    assert row["result_status"] == "not_executed"
    assert row["resource_ref"] is None


def test_a_lying_agent_is_visible_in_the_record(client, tenant, fake):
    """Says read, sends delete. The row shows both, and Aegis ruled on delete."""
    execution_id = str(uuid4())
    call(
        client, tenant, "delete", payload={"message_id": "m-1"},
        execution_id=execution_id,
        intent="I am only reading this message to summarise it.",
    )
    row = _calls(client, tenant, execution_id)[0]
    assert "reading" in row["declared_intent_untrusted"]
    assert row["canonical_operation"] == "gmail.DELETE"
    assert row["decision"] == "BLOCK"
    assert row["executed"] is False
    # The hint an auditor sees. It is a hint, not a control.
    assert row["intent_matches_operation"] is False


def test_an_honest_agent_matches(client, tenant, fake):
    execution_id = str(uuid4())
    call(
        client, tenant, "search", payload={"query": "marco"},
        execution_id=execution_id, intent="Search the mailbox for Marco.",
    )
    assert _calls(client, tenant, execution_id)[0]["intent_matches_operation"] is True


def test_an_approved_send_records_the_approval_and_the_execution(client, tenant, fake):
    execution_id = str(uuid4())
    request_id = f"send-{uuid4().hex[:8]}"
    payload = {"to": "marco@example.test", "subject": "Very Secret Subject Line", "body": "secret"}

    pending = call(
        client, tenant, "send", payload=payload,
        execution_id=execution_id, request_id=request_id, intent="Sending the reply.",
    ).json()
    approve(client, tenant, pending["approval_id"])
    call(
        client, tenant, "send", payload=payload,
        execution_id=execution_id, request_id=request_id, intent="Sending the reply.",
    )

    rows = _calls(client, tenant, execution_id)
    assert len(rows) == 2
    first, second = rows
    assert first["decision"] == "APPROVAL" and first["executed"] is False
    assert first["approval_id"] == pending["approval_id"]
    assert second["executed"] is True
    assert second["approval_granted"] is True
    assert second["connector_operation"] == "send"
    # Identifies the message without revealing it.
    assert second["resource_ref"].startswith("message_id=")


def test_evidence_holds_no_message_content(client, tenant, fake):
    """An auditor learns which message, never what it said."""
    execution_id = str(uuid4())
    call(
        client, tenant, "read", payload={"message_id": "m-1", "include_body": True},
        execution_id=execution_id,
    )
    call(
        client, tenant, "draft",
        payload={"to": "marco@example.test", "subject": "Very Secret Subject Line", "body": "Extremely confidential body text that must not be stored."},
        execution_id=execution_id,
    )
    serialized = json.dumps(_calls(client, tenant, execution_id))
    assert "Very Secret Subject Line" not in serialized
    assert "Extremely confidential body text" not in serialized
    assert "marco@example.test" not in serialized


def test_evidence_holds_no_credential(client, tenant, fake):
    from app import config, gmail_store

    execution_id = str(uuid4())
    call(client, tenant, "search", payload={"query": "marco"}, execution_id=execution_id)
    serialized = json.dumps(_calls(client, tenant, execution_id))
    for secret in (
        gmail_store.reveal_refresh_token(tenant.organization_id),
        config.GOOGLE_OAUTH_CLIENT_SECRET,
        config.GMAIL_OAUTH_ENCRYPTION_KEY,
        config.CRM_SECRET,
        "stand-in-access-token",
    ):
        if secret:
            assert secret not in serialized


def test_connector_evidence_is_tenant_scoped(client, tenant, fake):
    execution_id = str(uuid4())
    call(client, tenant, "search", payload={"query": "marco"}, execution_id=execution_id)
    rival = build_tenant(client, name="Rival")
    response = client.get(
        f"/api/executions/{execution_id}/connector-calls",
        headers=rival.operator,
    )
    assert response.status_code == 404


def test_an_agent_cannot_read_the_evidence_about_itself(client, tenant, fake):
    execution_id = str(uuid4())
    call(client, tenant, "search", payload={"query": "marco"}, execution_id=execution_id)
    response = client.get(
        f"/api/executions/{execution_id}/connector-calls",
        headers={"X-Agent-Token": tenant.agent_token},
    )
    assert response.status_code in (401, 403)


def test_the_sealed_event_chain_still_verifies_after_gmail_activity(client, tenant, fake):
    """The connector record is outside the chain and must not disturb it."""
    execution_id = str(uuid4())
    call(client, tenant, "search", payload={"query": "marco"}, execution_id=execution_id)
    call(client, tenant, "read", payload={"message_id": "m-1"}, execution_id=execution_id)
    call(client, tenant, "delete", payload={"message_id": "m-1"}, execution_id=execution_id)

    verified = client.get(
        f"/api/executions/{execution_id}/evidence", headers=tenant.operator
    )
    assert verified.status_code == 200
    body = verified.json()
    assert body["verdict"]["valid"] is True, body["verdict"]
    assert body["event_count"] >= 3
