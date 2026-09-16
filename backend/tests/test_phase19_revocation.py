"""Phase 19 — revoking a running agent, mid-execution.

The brief is explicit that restarting the agent is not proof. So the agent here
is a live object with an in-flight execution: it acts, a human revokes it while
that execution is open, and it keeps acting on the same credential it has been
using all along. Nothing is restarted and no new token is issued.

Three things are checked each time:
  * the next call is refused,
  * the mailbox is untouched afterwards,
  * an approval granted before the revocation does not survive it.
"""

from __future__ import annotations

import json
import os
import sys
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

from .gmail_fake import FakeGoogle
from .phase19_harness import approve, build_tenant, call, isolate_oauth_store, use_fake_google
from .test_phase19_prompt_injection import InProcessAegisClient, ScriptedModel

AGENT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "infra",
    "ai-agent",
)
if AGENT_DIR not in sys.path:
    sys.path.insert(0, AGENT_DIR)

import agent as ai_agent  # noqa: E402


@pytest.fixture
def client():
    with TestClient(create_app("all")) as instance:
        yield instance


@pytest.fixture
def fake(tmp_path, monkeypatch):
    isolate_oauth_store(tmp_path, monkeypatch)
    google = FakeGoogle()
    google.add_message(
        message_id="m-1", sender="marco@example.test", subject="Numbers", body="text"
    )
    use_fake_google(monkeypatch, google)
    monkeypatch.setattr(ai_agent, "APPROVAL_WAIT_SECONDS", 0.0)
    monkeypatch.setattr(ai_agent, "APPROVAL_POLL_SECONDS", 0.0)
    return google


@pytest.fixture
def tenant(client, fake):
    return build_tenant(client)


def test_revocation_stops_the_very_next_call_on_the_same_credential(client, tenant, fake):
    """No restart. Same token, same execution, one revocation in between."""
    execution_id = str(uuid4())

    first = call(
        client, tenant, "search", payload={"query": "marco"}, execution_id=execution_id
    )
    assert first.status_code == 200
    assert first.json()["executed"] is True

    revoked = client.post(
        f"/api/agents/{tenant.agent_id}/revoke", headers=tenant.operator
    )
    assert revoked.status_code == 200

    for operation, payload in (
        ("search", {"query": "marco"}),
        ("read", {"message_id": "m-1"}),
        ("draft", {"to": "a@b.test", "subject": "s", "body": "b"}),
        ("send", {"to": "a@b.test", "subject": "s", "body": "b"}),
        ("delete", {"message_id": "m-1"}),
    ):
        after = call(
            client, tenant, operation, payload=payload, execution_id=execution_id
        )
        assert after.status_code == 401, operation

    assert fake.sent == []
    assert fake.trashed == []


def test_a_live_agent_loop_is_cut_off_mid_run(client, tenant, fake):
    """The real agent runtime, revoked between two of its own tool calls."""
    revoked_flag = {"done": False}

    class RevokingAegis(InProcessAegisClient):
        """Revokes the agent after its first successful call, then continues."""

        def invoke(self, operation, **kwargs):
            outcome = super().invoke(operation, **kwargs)
            if not revoked_flag["done"] and outcome.executed:
                client.post(
                    f"/api/agents/{tenant.agent_id}/revoke", headers=tenant.operator
                )
                revoked_flag["done"] = True
            return outcome

    script = [
        [{"name": "aegis_gmail_search", "arguments": {"query": "marco", "intent": "Finding the message."}}],
        [{"name": "aegis_gmail_read", "arguments": {"message_id": "m-1", "intent": "Reading it."}}],
        [{"name": "aegis_gmail_draft", "arguments": {"to": "marco@example.test", "subject": "Re", "body": "ok", "intent": "Replying."}}],
        [],
    ]
    agent = ai_agent.GmailAgent(
        aegis=RevokingAegis(client, tenant.agent_token),
        model=ScriptedModel(script),
    )
    transcript = agent.run("Find Marco's email, read it, and draft a reply.", execution_id=str(uuid4()))

    steps = transcript["steps"]
    assert steps[0]["executed"] is True, "the first call should have succeeded"
    assert revoked_flag["done"] is True
    for step in steps[1:]:
        assert step["executed"] is False
        assert step["decision"] == "DENIED"

    assert fake.drafts == {}
    assert fake.sent == []


def test_an_approval_granted_before_revocation_does_not_survive_it(client, tenant, fake):
    """A human said yes. Then the agent was revoked. The yes is worth nothing."""
    execution_id = str(uuid4())
    request_id = f"send-{uuid4().hex[:8]}"
    payload = {"to": "marco@example.test", "subject": "queued", "body": "body"}

    pending = call(
        client, tenant, "send", payload=payload,
        execution_id=execution_id, request_id=request_id,
    ).json()
    assert pending["decision"] == "APPROVAL"

    decided = approve(client, tenant, pending["approval_id"])
    assert decided.json()["status"] == "approved"

    client.post(f"/api/agents/{tenant.agent_id}/revoke", headers=tenant.operator)

    redeemed = call(
        client, tenant, "send", payload=payload,
        execution_id=execution_id, request_id=request_id,
    )
    assert redeemed.status_code == 401
    assert fake.sent == []


def test_revocation_is_not_undone_by_reconnecting_gmail(client, tenant, fake):
    """Gmail being connected is not authority; the agent's credential is."""
    from .phase19_harness import connect_gmail

    client.post(f"/api/agents/{tenant.agent_id}/revoke", headers=tenant.operator)
    connect_gmail(tenant.organization_id)

    assert call(client, tenant, "search", payload={"query": "marco"}).status_code == 401
    assert fake.sent == []


def test_other_agents_in_the_tenant_are_unaffected(client, fake):
    """Revocation is targeted: revoking one agent does not disarm the tenant."""
    revoked_tenant = build_tenant(client, name="To Be Revoked")
    other = build_tenant(client, name="Still Working")

    client.post(
        f"/api/agents/{revoked_tenant.agent_id}/revoke", headers=revoked_tenant.operator
    )

    assert call(client, revoked_tenant, "search", payload={"query": "m"}).status_code == 401
    assert call(client, other, "search", payload={"query": "m"}).status_code == 200
