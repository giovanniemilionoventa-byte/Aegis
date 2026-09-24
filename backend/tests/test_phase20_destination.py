"""Phase 20 -- a send is judged on who it is going to, not on what the agent says.

Before this, `destination` and `scope` on an email send were whatever the agent
declared. The policy "email outside the company needs a human" matched on that
declaration, so an agent that wrote `"scope": "internal"` while sending to a
stranger was allowed through, and the human approving a real external send saw
only a digest. Now the server reads the recipients out of the request itself.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import config
from app.engines import destination
from app.main import create_app

INTERNAL = "acme-int.test"


# ---------------------------------------------------------------------------
# Reading recipients
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload, expected",
    [
        ({"to": "a@x.test"}, ["a@x.test"]),
        ({"to": "Ada <A@X.Test>, b@y.test"}, ["a@x.test", "b@y.test"]),
        ({"to": ["a@x.test", "B@y.test"], "cc": "c@z.test"}, ["a@x.test", "b@y.test", "c@z.test"]),
        ({"to": "a@x.test; b@y.test"}, ["a@x.test", "b@y.test"]),
        ({"To": "a@x.test"}, ["a@x.test"]),
        ({"to": '"Doe, John" <j@x.test>'}, ["j@x.test"]),
        ({"to": "a@x.test", "bcc": "h@y.test"}, ["a@x.test", "h@y.test"]),
        ({"to": "a@x.test", "cc": "A@X.TEST"}, ["a@x.test"]),
        ({}, []),
        (None, []),
        ({"to": ""}, []),
        ({"to": None}, []),
        ({"to": 5}, []),
    ],
)
def test_recipients(payload, expected):
    assert destination.recipients(payload) == expected


# ---------------------------------------------------------------------------
# Inside or outside
# ---------------------------------------------------------------------------


def test_internal_only_when_every_recipient_is_inside():
    inside = f"{INTERNAL}, Mail.Acme-Int.test"
    assert destination.classify(["a@acme-int.test"], inside) == "internal"
    assert destination.classify(["a@acme-int.test", "b@mail.acme-int.test"], inside) == "internal"
    assert destination.classify(["a@acme-int.test", "b@evil.test"], inside) == "external"


@pytest.mark.parametrize(
    "address",
    [
        "a@acme-int.test.evil.com",  # inside is only a prefix of the real domain
        "a@evilacme-int.test",  # inside is only a suffix
        "a@sub.acme-int.test",  # a subdomain is not listed
        "acme-int.test@evil.test",  # the domain sits in the local part
        "a@acme-int.test@evil.test",  # two @: the last one decides
        "junk",  # not an address at all
    ],
)
def test_lookalikes_and_junk_are_external(address):
    assert destination.classify([address], INTERNAL) == "external"


def test_case_is_ignored():
    assert destination.classify(["A@ACME-INT.TEST"], INTERNAL) == "internal"


@pytest.mark.parametrize("inside", [None, "", "   ,  "])
def test_nothing_is_internal_when_no_domain_is_configured(inside):
    assert destination.classify(["a@acme-int.test"], inside) == "external"


def test_only_sends_are_derived():
    assert destination.derive("gmail", "READ", {"to": "a@x.test"}, INTERNAL) is None
    assert destination.derive("crm", "SEND", {"to": "a@x.test"}, INTERNAL) is None
    assert destination.derive("email", "SEND", {"to": "a@x.test"}, INTERNAL) is not None
    assert destination.derive("gmail", "send", {"to": "a@x.test"}, INTERNAL) is not None


def test_no_recipients_means_no_classification():
    derived = destination.derive("email", "SEND", {}, INTERNAL)
    assert derived is not None and derived.classification is None


# ---------------------------------------------------------------------------
# Through the real decision path
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app("all")) as instance:
        yield instance


def _mailer(client: TestClient):
    """A new organization whose only 'inside' domain is acme-int.test."""
    suffix = uuid4().hex[:8]
    registered = client.post(
        "/api/auth/register",
        json={
            "organization_name": f"Mail {suffix}",
            "full_name": "Boss",
            "email": f"boss-{suffix}@{INTERNAL}",
            "password": "a-long-enough-password",
        },
    )
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    created = client.post(
        "/api/agents",
        headers=headers,
        json={"name": "Mailer", "provider": "d", "model": "m", "description": ""},
    ).json()
    agent_id, token = created["agent"]["id"], created["token"]
    for kind, scope in (("email", "*"), ("gmail", "mailbox")):
        client.post(
            f"/api/agents/{agent_id}/permissions",
            headers=headers,
            json={"resource_kind": kind, "action": "SEND", "scope": scope, "effect": "allow"},
        )
    client.post(
        f"/api/agents/{agent_id}/contracts",
        headers=headers,
        json={
            "organization_id": "x",
            "agent_id": "x",
            "contract_id": f"mailer-{suffix}",
            "version": 1,
            "status": "ACTIVE",
            "purpose": "send mail",
            "capabilities": [
                {"name": "email", "resource_kind": "email", "actions": ["SEND"]},
                {"name": "gmail", "resource_kind": "gmail", "actions": ["SEND"]},
            ],
            "resources": [
                {"kind": "email", "scope": "*"},
                {"kind": "gmail", "scope": "mailbox"},
            ],
            "constraints": {},
            "data_constraints": {},
            "approval_rules": [],
        },
    )
    return headers, token


def _send(client, token, payload, *, kind="email", scope="internal", declared="internal"):
    return client.post(
        "/api/authorize",
        headers={"X-Agent-Token": token},
        json={
            "resource_kind": kind,
            "action": "SEND",
            "scope": scope,
            "destination": declared,
            "payload": payload,
        },
    ).json()


def test_a_false_internal_declaration_cannot_hide_an_outside_recipient(client):
    _, token = _mailer(client)
    result = _send(client, token, {"to": "stranger@evil.test"})
    assert result["decision"] == "APPROVAL", "declared 'internal' must not win"


def test_recipients_inside_the_organization_go_through(client):
    _, token = _mailer(client)
    result = _send(client, token, {"to": f"colleague@{INTERNAL}"}, scope="external", declared="external")
    assert result["decision"] == "ALLOW", "the recipients, not the declaration, decide"


def test_cc_is_judged_as_well(client):
    _, token = _mailer(client)
    result = _send(client, token, {"to": f"colleague@{INTERNAL}", "cc": "stranger@evil.test"})
    assert result["decision"] == "APPROVAL"


def test_a_display_name_cannot_pose_as_an_inside_address(client):
    _, token = _mailer(client)
    result = _send(client, token, {"to": f'"boss@{INTERNAL}" <stranger@evil.test>'})
    assert result["decision"] == "APPROVAL"


def test_the_event_records_the_derived_destination(client):
    headers, token = _mailer(client)
    _send(client, token, {"to": "stranger@evil.test"})
    newest = client.get("/api/events", headers=headers).json()[0]
    assert newest["destination"] == "external"
    assert newest["scope"] == "external"


def test_gmail_send_gets_a_derived_destination_and_keeps_its_scope(client):
    headers, token = _mailer(client)
    result = _send(client, token, {"to": "stranger@evil.test"}, kind="gmail", scope="mailbox", declared="internal")
    assert result["decision"] == "APPROVAL"
    newest = client.get("/api/events", headers=headers).json()[0]
    assert newest["destination"] == "external"
    assert newest["scope"] == "mailbox", "the mailbox scope is not a recipient class"


def test_without_recipients_development_keeps_the_declared_destination(client, monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_DERIVED_DESTINATION", False)
    _, token = _mailer(client)
    result = _send(client, token, {}, scope="external", declared="external")
    assert result["decision"] == "APPROVAL"


def test_without_recipients_production_blocks(client, monkeypatch):
    monkeypatch.setattr(config, "REQUIRE_DERIVED_DESTINATION", True)
    _, token = _mailer(client)
    result = _send(client, token, {}, scope="internal", declared="internal")
    assert result["decision"] == "BLOCK"
    assert "recipient" in result["reason"].lower()


def test_replaying_a_request_gives_the_same_answer(client):
    _, token = _mailer(client)
    request_id = str(uuid4())
    body = {
        "resource_kind": "email",
        "action": "SEND",
        "scope": "internal",
        "destination": "internal",
        "payload": {"to": "stranger@evil.test"},
        "request_id": request_id,
    }
    first = client.post("/api/authorize", headers={"X-Agent-Token": token}, json=body).json()
    again = client.post("/api/authorize", headers={"X-Agent-Token": token}, json=body).json()
    assert first["decision"] == again["decision"] == "APPROVAL"
    assert first["approval_id"] == again["approval_id"]
