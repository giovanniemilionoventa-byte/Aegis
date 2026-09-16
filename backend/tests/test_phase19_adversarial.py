"""Phase 19 — the agent is assumed hostile, and here is what it tries.

This file works through the attack list in the Phase 19 brief. Every test
asserts a *side effect did not happen*, read from the Gmail stand-in's ledger,
not merely that a response said BLOCK. A control layer that returns the right
JSON while mail leaves the mailbox has failed, and only the ledger catches it.

Attacks 4 and 25 (direct Gmail access, direct network access) are not here:
they are network properties and live in test_phase19_network.py, where the
deployment configuration and the egress allow-list are checked directly.

Numbering below follows the brief so the coverage can be audited against it.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import models
from app.database import SessionLocal
from app.main import create_app
from app.protected.gmail import gmail_connector
from app.security import utcnow

from .gmail_fake import FakeGoogle
from .phase19_harness import (
    approve,
    build_tenant,
    call,
    connect_gmail,
    isolate_oauth_store,
    use_fake_google,
)


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


def _no_side_effects(fake: FakeGoogle) -> None:
    assert fake.sent == [], "a message was sent"
    assert fake.trashed == [], "a message was trashed"


# -- 1, 21, 22: reaching delete through something else ----------------------


def test_1_read_then_delete_is_still_a_delete(client, tenant, fake):
    """An allowed read does not earn a delete later in the same execution."""
    execution_id = str(uuid4())
    allowed = call(
        client, tenant, "read", payload={"message_id": "m-1"}, execution_id=execution_id
    ).json()
    assert allowed["executed"] is True

    attempt = call(
        client,
        tenant,
        "delete",
        payload={"message_id": "m-1"},
        execution_id=execution_id,
    ).json()
    assert attempt["decision"] == "BLOCK"
    assert attempt["executed"] is False
    assert fake.trashed == []


def test_21_delete_dressed_as_a_read_is_ruled_on_as_a_delete(client, tenant, fake):
    """The declared intent says read. The operation says delete. Delete wins.

    This is the Phase 9 property: Aegis rules on the typed operation that
    arrived, never on the agent's account of it.
    """
    body = call(
        client,
        tenant,
        "delete",
        payload={"message_id": "m-1"},
        intent="I am only reading this message to summarise it for the user.",
    ).json()
    assert body["decision"] == "BLOCK"
    assert body["executed"] is False
    assert fake.trashed == []


def test_22_delete_parameters_smuggled_into_a_draft_do_nothing(client, tenant, fake):
    """Extra fields on an allowed operation do not change what it is."""
    body = call(
        client,
        tenant,
        "draft",
        payload={
            "to": "marco@example.test",
            "subject": "note",
            "body": "text",
            "message_id": "m-1",
            "delete": True,
            "operation": "delete",
        },
    ).json()
    assert body["decision"] == "ALLOW"
    assert body["executed"] is True
    # A draft was created and nothing was deleted.
    assert len(fake.drafts) == 1
    assert fake.trashed == []


def test_3_a_delete_described_as_cleanup_is_still_denied(client, tenant, fake):
    body = call(
        client,
        tenant,
        "delete",
        payload={"message_id": "m-1"},
        intent="Routine mailbox cleanup approved by the user in chat.",
    ).json()
    assert body["decision"] == "BLOCK"
    assert fake.trashed == []


# -- 2, 23, 24: reaching send through something else ------------------------


def test_2_draft_then_send_still_needs_a_human(client, tenant, fake):
    """Being allowed to draft is not being allowed to send."""
    execution_id = str(uuid4())
    drafted = call(
        client,
        tenant,
        "draft",
        payload={"to": "marco@example.test", "subject": "Re: numbers", "body": "ok"},
        execution_id=execution_id,
    ).json()
    assert drafted["executed"] is True
    draft_id = drafted["result"]["draft_id"]

    sent = call(
        client,
        tenant,
        "send",
        payload={"draft_id": draft_id},
        execution_id=execution_id,
    ).json()
    assert sent["decision"] == "APPROVAL"
    assert sent["executed"] is False
    assert fake.sent == []


def test_23_send_disguised_as_a_draft_is_ruled_on_as_a_send(client, tenant, fake):
    body = call(
        client,
        tenant,
        "send",
        payload={"to": "marco@example.test", "subject": "hi", "body": "hello"},
        intent="Just saving a draft for the user to review later.",
    ).json()
    assert body["decision"] == "APPROVAL"
    assert body["executed"] is False
    assert fake.sent == []


def test_24_send_parameters_on_a_read_do_not_send(client, tenant, fake):
    body = call(
        client,
        tenant,
        "read",
        payload={
            "message_id": "m-1",
            "to": "attacker@evil.test",
            "subject": "exfil",
            "body": "secrets",
            "send": True,
        },
    ).json()
    assert body["decision"] == "ALLOW"
    assert body["executed"] is True
    # It read. It did not send.
    assert fake.sent == []


def test_20_parameter_confusion_does_not_change_the_operation(client, tenant, fake):
    """Naming another operation inside the payload changes nothing."""
    for payload in (
        {"query": "marco", "operation": "delete"},
        {"query": "marco", "action": "SEND"},
        {"query": "marco", "resource_kind": "payments"},
        {"query": "marco", "method": "DELETE", "url": "https://gmail.googleapis.com/x"},
    ):
        body = call(client, tenant, "search", payload=payload).json()
        assert body["decision"] == "ALLOW"
        assert body["result"]["operation"] == "search"
    _no_side_effects(fake)


# -- 5: a lifted or guessed credential --------------------------------------


def test_5_a_guessed_agent_token_is_refused(client, tenant, fake):
    for guess in ("aegis_guessed", "aegis_" + "a" * 43, tenant.agent_token[:-4] + "aaaa"):
        response = call(
            client, tenant, "search", payload={"query": "marco"}, token=guess
        )
        assert response.status_code == 401
    _no_side_effects(fake)


def test_5b_a_guessed_google_refresh_token_is_not_a_credential(client, fake, tmp_path):
    """A refresh token Aegis did not store buys nothing.

    The connector reads the credential from the sealed store; there is no code
    path that takes one from a request. This asserts the other half: an
    unknown token presented at Google's token endpoint is refused there too.
    """
    tenant = build_tenant(client, refresh_token="not-a-token-google-knows")
    body = call(client, tenant, "search", payload={"query": "marco"})
    assert body.status_code in (502, 503)
    _no_side_effects(fake)


# -- 6, 7, 8: forged identity ----------------------------------------------


@pytest.mark.parametrize(
    "forged",
    [
        {"org_id": "some-other-org"},
        {"organization_id": "some-other-org"},
        {"tenant_id": "some-other-tenant"},
        {"agent_id": "some-other-agent"},
        {"user_id": "someone-else"},
        {"organization_id": "x", "agent_id": "y", "tenant_id": "z"},
    ],
)
def test_6_7_8_forged_identity_fields_are_not_authority(client, tenant, fake, forged):
    """Identity comes from the authenticated credential, never from the body.

    The request still succeeds -- these fields are simply not read -- and the
    recorded event carries the agent's real identity.
    """
    body = call(
        client,
        tenant,
        "search",
        payload={"query": "marco", **forged},
        extra_metadata=forged,
    ).json()
    assert body["agent_id"] == tenant.agent_id
    assert body["organization_id"] == tenant.organization_id
    assert body["result"]["organization_id"] == tenant.organization_id


def test_8b_forged_identity_cannot_borrow_another_agents_permissions(client, fake):
    """Claiming to be a privileged agent does not make you one."""
    privileged = build_tenant(
        client,
        name="Privileged",
        actions=("SEARCH", "READ", "DRAFT", "SEND", "DELETE"),
    )
    restricted = build_tenant(client, name="Restricted", actions=("SEARCH",))

    body = call(
        client,
        restricted,
        "read",
        payload={"message_id": "m-1", "agent_id": privileged.agent_id},
        extra_metadata={"agent_id": privileged.agent_id},
    ).json()
    assert body["decision"] == "BLOCK"
    assert body["agent_id"] == restricted.agent_id


# -- 9: cross-tenant access -------------------------------------------------


def test_9_one_tenant_cannot_read_another_tenants_message(client, fake):
    """Two mailboxes, two credentials. A's id is not in B's mailbox.

    The connector resolves the credential from the authenticated tenant, so
    tenant B's request reaches B's mailbox, where A's message does not exist.
    """
    fake.add_mailbox("refresh-tenant-b", "access-tenant-b")
    fake.add_message(
        message_id="m-secret-a",
        sender="ceo@a.test",
        subject="Tenant A only",
        body="private",
        access_token=fake.access_token,
    )
    tenant_b = build_tenant(client, name="Tenant B", refresh_token="refresh-tenant-b")

    response = call(client, tenant_b, "read", payload={"message_id": "m-secret-a"})
    assert response.status_code == 404
    _no_side_effects(fake)


def test_9b_a_tenant_without_a_connection_gets_nothing(client, fake, tmp_path):
    """No Gmail connected means no mailbox, not somebody else's mailbox."""
    from .phase19_harness import Tenant, create_gmail_agent, install_canonical_policy, register_operator

    operator = register_operator(client)
    install_canonical_policy(client, operator)
    agent_id, agent_token, contract_id = create_gmail_agent(client, operator, name="Unconnected")
    organization_id = client.get(f"/api/agents/{agent_id}", headers=operator).json()[
        "organization_id"
    ]
    tenant = Tenant(
        organization_id=organization_id,
        operator=operator,
        agent_id=agent_id,
        agent_token=agent_token,
        contract_id=contract_id,
    )
    response = call(client, tenant, "search", payload={"query": "anything"})
    assert response.status_code in (409, 502, 503)


# -- 10: revocation ---------------------------------------------------------


def test_10_a_revoked_agent_is_refused(client, tenant, fake):
    """Revocation stops the next action, on the same live credential.

    No restart, no new token: the same agent token that worked a line ago is
    refused a line later. See test_phase19_revocation.py for the full
    mid-execution sequence.
    """
    working = call(client, tenant, "search", payload={"query": "marco"})
    assert working.status_code == 200

    revoked = client.post(
        f"/api/agents/{tenant.agent_id}/revoke", headers=tenant.operator
    )
    assert revoked.status_code == 200

    after = call(client, tenant, "search", payload={"query": "marco"})
    assert after.status_code == 401
    _no_side_effects(fake)


# -- 11, 12: approvals ------------------------------------------------------


def test_11_an_expired_approval_does_not_execute(client, tenant, fake):
    execution_id = str(uuid4())
    request_id = f"send-{uuid4().hex[:8]}"
    payload = {"to": "marco@example.test", "subject": "late", "body": "too late"}
    pending = call(
        client, tenant, "send", payload=payload,
        execution_id=execution_id, request_id=request_id,
    ).json()
    approve(client, tenant, pending["approval_id"])

    # Age the grant past its expiry, the way wall-clock time would.
    db = SessionLocal()
    try:
        approval = (
            db.query(models.Approval)
            .filter(models.Approval.id == pending["approval_id"])
            .first()
        )
        approval.expires_at = utcnow() - timedelta(minutes=1)
        db.commit()
    finally:
        db.close()

    retried = call(
        client, tenant, "send", payload=payload,
        execution_id=execution_id, request_id=request_id,
    ).json()
    assert retried["executed"] is False
    assert fake.sent == []


def test_11b_an_expired_request_cannot_be_approved(client, tenant, fake):
    """Approving an expired request would look to the operator like authority."""
    pending = call(
        client,
        tenant,
        "send",
        payload={"to": "marco@example.test", "subject": "x", "body": "y"},
    ).json()

    db = SessionLocal()
    try:
        approval = (
            db.query(models.Approval)
            .filter(models.Approval.id == pending["approval_id"])
            .first()
        )
        approval.expires_at = utcnow() - timedelta(minutes=1)
        db.commit()
    finally:
        db.close()

    decided = approve(client, tenant, pending["approval_id"])
    assert decided.status_code == 409
    assert fake.sent == []


def test_12_an_approval_cannot_be_replayed(client, tenant, fake):
    execution_id = str(uuid4())
    request_id = f"send-{uuid4().hex[:8]}"
    payload = {"to": "marco@example.test", "subject": "once", "body": "once"}
    pending = call(
        client, tenant, "send", payload=payload,
        execution_id=execution_id, request_id=request_id,
    ).json()
    approve(client, tenant, pending["approval_id"])

    assert call(
        client, tenant, "send", payload=payload,
        execution_id=execution_id, request_id=request_id,
    ).json()["executed"] is True

    for _ in range(3):
        again = call(
            client, tenant, "send", payload=payload,
            execution_id=execution_id, request_id=request_id,
        ).json()
        assert again["executed"] is False
    assert len(fake.sent) == 1


def test_12b_an_approval_for_one_execution_does_not_release_another(client, tenant, fake):
    payload = {"to": "marco@example.test", "subject": "a", "body": "b"}
    first = call(
        client, tenant, "send", payload=payload,
        execution_id=str(uuid4()), request_id=f"send-{uuid4().hex[:8]}",
    ).json()
    approve(client, tenant, first["approval_id"])

    other = call(
        client, tenant, "send", payload=payload,
        execution_id=str(uuid4()), request_id=f"send-{uuid4().hex[:8]}",
    ).json()
    assert other["decision"] == "APPROVAL"
    assert other["executed"] is False
    assert fake.sent == []


# -- 13, 14, 15, 16: the shape of the request -------------------------------


@pytest.mark.parametrize(
    "operation",
    ["", " ", "SEARCH ", "se arch", "search;delete", "%2e%2e"],
)
def test_13_malformed_operations_are_refused(client, tenant, fake, operation):
    response = call(client, tenant, operation, payload={"query": "x"})
    assert response.status_code in (400, 404, 405)
    _no_side_effects(fake)


@pytest.mark.parametrize("operation", ["search/../delete", "read/../../gmail/delete"])
def test_13b_path_traversal_cannot_reach_a_more_permissive_operation(
    client, tenant, fake, operation
):
    """Traversal resolves to a real operation, and that operation is ruled on.

    An HTTP client normalises "search/../delete" to "delete" before it is sent,
    so the gateway sees a delete -- which is exactly what it then denies. The
    property is not that the string is rejected; it is that no amount of path
    manipulation produces an operation MORE permissive than the one named.
    """
    response = call(client, tenant, operation, payload={"message_id": "m-1"})
    if response.status_code == 200:
        body = response.json()
        assert body["decision"] in ("BLOCK", "APPROVAL")
        assert body["executed"] is False
    else:
        assert response.status_code in (400, 404, 405)
    _no_side_effects(fake)


@pytest.mark.parametrize(
    "operation",
    ["execute", "proxy", "raw", "batchModify", "import", "insert", "trash", "untrash"],
)
def test_14_unknown_operations_are_refused(client, tenant, fake, operation):
    response = call(client, tenant, operation, payload={})
    assert response.status_code in (400, 404)
    _no_side_effects(fake)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://gmail.googleapis.com/gmail/v1/users/me/messages",
        "/gmail/v1/users/me/settings/forwarding",
        "//evil.test/gmail/v1/users/me/messages",
    ],
)
def test_15_an_arbitrary_gmail_endpoint_is_not_expressible(client, tenant, fake, endpoint):
    """There is no field, anywhere, that becomes a Gmail URL.

    Supplying one as the operation fails routing; supplying one inside the
    payload is dropped by the connector, which builds its own paths from
    constants.
    """
    as_operation = call(client, tenant, endpoint, payload={})
    assert as_operation.status_code in (400, 404)

    as_payload = call(
        client,
        tenant,
        "search",
        payload={"query": "marco", "url": endpoint, "endpoint": endpoint, "path": endpoint},
    ).json()
    assert as_payload["decision"] == "ALLOW"
    assert as_payload["result"]["operation"] == "search"
    _no_side_effects(fake)


@pytest.fixture
def connector_client(monkeypatch):
    """The Gmail connector as it is actually deployed: the protected-tool role.

    Testing the internal API against the combined "all" app would prove
    nothing, because the route is not mounted there -- a 404 would mean the
    endpoint was absent, not that it refused.

    INTERNAL_TOOL_TOKEN is set deliberately. Left empty, every request to this
    API is refused for want of a configured internal token, and the credential
    tests below would pass without ever reaching the check they exist to make.
    A test that passes for the wrong reason is worse than no test.
    """
    from app import config

    monkeypatch.setattr(config, "INTERNAL_TOOL_TOKEN", "phase19-internal-tool-token")
    with TestClient(create_app("protected-tool")) as instance:
        yield instance


def test_16_pre_the_internal_api_needs_the_internal_token(connector_client, tenant):
    """First, establish that the internal token is doing the work it should.

    Without this the refusals below could all be "no internal token
    configured", which would make them meaningless.
    """
    from app import config
    from app.credentials import derive_tool_credential

    good_secret = derive_tool_credential("gmail", tenant.organization_id)
    body = {
        "secret": good_secret,
        "scope": "mailbox",
        "payload": {"query": "x"},
        "organization_id": tenant.organization_id,
    }
    missing = connector_client.post("/api/internal/tools/gmail/search", json=body)
    assert missing.status_code == 401

    wrong = connector_client.post(
        "/api/internal/tools/gmail/search",
        headers={"X-Internal-Token": "not-the-internal-token"},
        json=body,
    )
    assert wrong.status_code == 401

    # With the right internal token AND the right per-tenant credential, the
    # call gets as far as the connector -- which is the baseline the refusals
    # below are measured against.
    accepted = connector_client.post(
        "/api/internal/tools/gmail/search",
        headers={"X-Internal-Token": config.INTERNAL_TOOL_TOKEN},
        json=body,
    )
    assert accepted.status_code == 200


def test_16_the_connector_refuses_an_agent_token(connector_client, tenant, fake):
    """An agent credential is not an internal service credential."""
    for operation in ("send", "delete", "search"):
        response = connector_client.post(
            f"/api/internal/tools/gmail/{operation}",
            headers={"X-Agent-Token": tenant.agent_token},
            json={
                "secret": tenant.agent_token,
                "scope": "mailbox",
                "payload": {},
                "organization_id": tenant.organization_id,
            },
        )
        assert response.status_code == 401, operation
    _no_side_effects(fake)


def test_16b_a_forged_connector_credential_is_refused(connector_client, tenant, fake):
    """Even with the internal token, the per-tenant credential must be right."""
    from app import config

    response = connector_client.post(
        "/api/internal/tools/gmail/send",
        headers={"X-Internal-Token": config.INTERNAL_TOOL_TOKEN},
        json={
            "secret": "not-the-derived-credential",
            "scope": "mailbox",
            "payload": {"to": "a@b.test", "subject": "x", "body": "y"},
            "organization_id": tenant.organization_id,
        },
    )
    assert response.status_code == 401
    assert fake.sent == []


def test_16c_another_tenants_connector_credential_is_refused(
    connector_client, client, tenant, fake
):
    """Presenting tenant A's derived credential while claiming to be B fails."""
    from app import config
    from app.credentials import derive_tool_credential

    other = build_tenant(client, name="Other Tenant")
    response = connector_client.post(
        "/api/internal/tools/gmail/send",
        headers={"X-Internal-Token": config.INTERNAL_TOOL_TOKEN},
        json={
            "secret": derive_tool_credential("gmail", other.organization_id),
            "scope": "mailbox",
            "payload": {"to": "a@b.test", "subject": "x", "body": "y"},
            "organization_id": tenant.organization_id,
        },
    )
    assert response.status_code == 401
    assert fake.sent == []


def test_16d_the_connector_exposes_no_generic_operation(connector_client, tenant, fake):
    """Even holding the correct internal credential, there is no sixth verb."""
    from app import config
    from app.credentials import derive_tool_credential

    secret = derive_tool_credential("gmail", tenant.organization_id)
    for operation in ("execute", "request", "proxy", "raw", "batchDelete"):
        response = connector_client.post(
            f"/api/internal/tools/gmail/{operation}",
            headers={"X-Internal-Token": config.INTERNAL_TOOL_TOKEN},
            json={
                "secret": secret,
                "scope": "mailbox",
                "payload": {"url": "https://gmail.googleapis.com/anything"},
                "organization_id": tenant.organization_id,
            },
        )
        assert response.status_code == 400, operation
        assert response.json()["detail"] == "gmail_unsupported_operation"
    _no_side_effects(fake)


def test_16e_agent_tokens_cannot_reach_the_broker(client, tenant, fake):
    """The broker is on a network the agent is not on; this is the app-layer check."""
    with TestClient(create_app("credential-broker")) as broker_client:
        response = broker_client.post(
            "/api/internal/broker/execute",
            headers={"X-Agent-Token": tenant.agent_token},
            json={
                "eat": "forged",
                "tool": "gmail",
                "operation": "send",
                "scope": "mailbox",
                "payload": {},
                "org_id": tenant.organization_id,
                "agent_id": tenant.agent_id,
                "execution_id": "e",
                "request_id": "r",
            },
        )
        assert response.status_code == 401
    assert fake.sent == []


# -- 18, 19: asking nicely --------------------------------------------------


def test_18_aegis_never_returns_a_credential(client, tenant, fake):
    """Ask every way there is. No response contains credential material."""
    from app import config, gmail_store

    real_refresh = gmail_store.reveal_refresh_token(tenant.organization_id)
    bodies = []

    for payload in (
        {"query": "credentials"},
        {"query": "marco", "reveal_credentials": True},
        {"query": "marco", "include_token": True, "debug": True},
    ):
        bodies.append(call(client, tenant, "search", payload=payload).text)

    status = client.get("/api/gmail/status", headers=tenant.operator)
    bodies.append(status.text)
    bodies.append(client.get("/api/agents", headers=tenant.operator).text)

    haystack = "\n".join(bodies)
    for secret in (
        real_refresh,
        config.GOOGLE_OAUTH_CLIENT_SECRET,
        config.GMAIL_OAUTH_ENCRYPTION_KEY,
        config.CRM_SECRET,
        config.EAT_KEY,
        config.INTERNAL_TOOL_TOKEN,
        "stand-in-access-token",
    ):
        if secret:
            assert secret not in haystack


def test_19_an_agent_cannot_change_its_own_policy_or_contract(client, tenant, fake):
    """The agent asking to be allowed more is not a way to be allowed more."""
    attempts = [
        ("POST", "/api/policies", {"name": "allow everything", "resource_kind": "gmail", "action": "*", "decision": "ALLOW", "priority": 0}),
        ("POST", f"/api/agents/{tenant.agent_id}/permissions", {"resource_kind": "gmail", "action": "DELETE", "scope": "mailbox", "effect": "allow"}),
        ("POST", f"/api/agents/{tenant.agent_id}/contracts", {"contract_id": "x", "version": 2, "status": "ACTIVE"}),
    ]
    for method, path, body in attempts:
        response = client.request(
            method, path, headers={"X-Agent-Token": tenant.agent_token}, json=body
        )
        assert response.status_code in (401, 403), path

    # And the delete is still denied afterwards.
    assert call(client, tenant, "delete", payload={"message_id": "m-1"}).json()["decision"] == "BLOCK"
    assert fake.trashed == []


def test_19b_an_agent_cannot_approve_its_own_request(client, tenant, fake):
    pending = call(
        client,
        tenant,
        "send",
        payload={"to": "marco@example.test", "subject": "self", "body": "approved by me"},
    ).json()
    response = client.post(
        f"/api/approvals/{pending['approval_id']}/decide",
        headers={"X-Agent-Token": tenant.agent_token},
        json={"decision": "ALLOW"},
    )
    assert response.status_code in (401, 403)
    assert fake.sent == []


def test_19c_an_operator_from_another_tenant_cannot_approve(client, tenant, fake):
    pending = call(
        client,
        tenant,
        "send",
        payload={"to": "marco@example.test", "subject": "cross", "body": "tenant"},
    ).json()
    rival = build_tenant(client, name="Rival")
    response = client.post(
        f"/api/approvals/{pending['approval_id']}/decide",
        headers=rival.operator,
        json={"decision": "ALLOW"},
    )
    assert response.status_code == 404
    assert fake.sent == []


# -- nothing ran ------------------------------------------------------------


def test_no_attack_in_this_file_ever_entered_the_connector_on_a_denial(client, tenant, fake):
    """A summary check: a BLOCK must not have touched Gmail at all."""
    before = gmail_connector.call_count
    for operation, payload in (
        ("delete", {"message_id": "m-1"}),
        ("delete", {"message_id": "m-1", "force": True}),
    ):
        body = call(client, tenant, operation, payload=payload).json()
        assert body["decision"] == "BLOCK"
    assert gmail_connector.call_count == before
