"""Phase 19.1 — the onboarding path an operator actually walks, attacked.

The dashboard added a relationship that did not exist before: an agent is
granted access to its tenant's mailbox. A new relationship is a new place for
a cross-tenant mistake to live, so this file works through the fifteen checks
in the brief by number.

As everywhere in Phase 19, a denial is asserted against the Gmail stand-in's
ledger of what was actually sent, drafted or trashed — not against a response
that said the right word.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import models
from app.database import SessionLocal
from app.main import create_app
from app.services import gmail_access

from .gmail_fake import FakeGoogle
from .phase19_harness import (
    approve,
    build_tenant,
    call,
    connect_gmail,
    create_gmail_agent,
    install_canonical_policy,
    isolate_oauth_store,
    register_operator,
    use_fake_google,
)


@pytest.fixture
def client():
    with TestClient(create_app("all")) as instance:
        yield instance


@pytest.fixture
def store_path(tmp_path, monkeypatch):
    return isolate_oauth_store(tmp_path, monkeypatch)


@pytest.fixture
def fake(store_path, monkeypatch):
    google = FakeGoogle()
    google.add_message(
        message_id="m-1", sender="marco@example.test", subject="Numbers", body="text"
    )
    use_fake_google(monkeypatch, google)
    return google


@pytest.fixture
def tenant(client, fake):
    return build_tenant(client)


def _quiet(fake: FakeGoogle) -> None:
    assert fake.sent == []
    assert fake.trashed == []


# -- 1, 2: cross-tenant -----------------------------------------------------


def test_1_an_agent_cannot_use_another_tenants_connection(client, fake):
    """Tenant B connects a mailbox. Tenant A's agent still cannot use one.

    A has no connection of its own and no grant. The point is that B's
    connection is not reachable from A by any route: the connection is resolved
    from the authenticated agent's organization, and there is no field in the
    request that names a connection.
    """
    tenant_b = build_tenant(client, name="Tenant B")

    operator_a = register_operator(client)
    install_canonical_policy(client, operator_a)
    agent_id, agent_token, contract_id = create_gmail_agent(client, operator_a, name="Agent A")
    from .phase19_harness import Tenant

    org_a = client.get(f"/api/agents/{agent_id}", headers=operator_a).json()["organization_id"]
    tenant_a = Tenant(
        organization_id=org_a,
        operator=operator_a,
        agent_id=agent_id,
        agent_token=agent_token,
        contract_id=contract_id,
    )
    assert tenant_a.organization_id != tenant_b.organization_id

    body = call(client, tenant_a, "search", payload={"query": "marco"}).json()
    assert body["decision"] == "BLOCK"
    assert body["executed"] is False
    _quiet(fake)


def test_2_an_operator_cannot_grant_another_tenants_agent(client, fake):
    """Operator B tries to grant mailbox access to tenant A's agent."""
    tenant_a = build_tenant(client, name="Tenant A")
    tenant_b = build_tenant(client, name="Tenant B")

    stolen = client.post(
        f"/api/gmail/agents/{tenant_a.agent_id}/grant", headers=tenant_b.operator
    )
    assert stolen.status_code == 404

    read = client.get(f"/api/gmail/agents/{tenant_a.agent_id}", headers=tenant_b.operator)
    assert read.status_code == 404

    revoked = client.post(
        f"/api/gmail/agents/{tenant_a.agent_id}/revoke", headers=tenant_b.operator
    )
    assert revoked.status_code == 404

    # A's agent is untouched.
    assert call(client, tenant_a, "search", payload={"query": "marco"}).json()["executed"] is True


def test_2b_a_grant_row_is_always_bound_to_its_own_tenant(client, fake):
    """There is no agent_id an operator can send that crosses a tenant."""
    tenant_a = build_tenant(client, name="Tenant A")
    tenant_b = build_tenant(client, name="Tenant B")

    db = SessionLocal()
    try:
        for row in gmail_access.agents_with_access(db, tenant_a.organization_id):
            assert row.organization_id == tenant_a.organization_id
        # B's agent is not reachable through A's organization.
        assert gmail_access.active_grant(db, tenant_a.organization_id, tenant_b.agent_id) is None
    finally:
        db.close()


# -- 3, 4, 5, 6, 7: nothing leaks ------------------------------------------


def test_3_the_agent_never_receives_a_refresh_token(client, tenant, fake):
    from app import gmail_store

    refresh = gmail_store.reveal_refresh_token(tenant.organization_id)
    responses = [
        call(client, tenant, "search", payload={"query": "marco"}).text,
        call(client, tenant, "read", payload={"message_id": "m-1"}).text,
        call(client, tenant, "draft", payload={"to": "a@b.test", "subject": "s", "body": "b"}).text,
    ]
    for text in responses:
        assert refresh not in text
        assert "refresh_token" not in text
        assert "stand-in-access-token" not in text


def test_4_the_dashboard_never_receives_a_refresh_token(client, tenant, fake):
    """Every control-plane route the dashboard calls, checked."""
    from app import config, gmail_store

    refresh = gmail_store.reveal_refresh_token(tenant.organization_id)
    texts = [
        client.get("/api/gmail/status", headers=tenant.operator).text,
        client.get(f"/api/gmail/agents/{tenant.agent_id}", headers=tenant.operator).text,
        client.post("/api/gmail/oauth/start", headers=tenant.operator).text,
        client.get(f"/api/agents/{tenant.agent_id}", headers=tenant.operator).text,
        client.get("/api/agents", headers=tenant.operator).text,
        client.get(f"/api/agents/{tenant.agent_id}/permissions", headers=tenant.operator).text,
    ]
    haystack = "\n".join(texts)
    for secret in (refresh, config.GOOGLE_OAUTH_CLIENT_SECRET, config.GMAIL_OAUTH_ENCRYPTION_KEY):
        if secret:
            assert secret not in haystack


def test_4b_the_dashboard_is_not_handed_the_google_client_id(client, tenant, fake):
    """oauth/start returns an Aegis route, not a Google URL with our client id.

    The client id is still visible in the address bar during consent, because
    it is a parameter of Google's own endpoint and Google treats it as public.
    What this asserts is narrower and real: the dashboard neither receives it
    nor constructs the Google URL.
    """
    from app import config

    body = client.post("/api/gmail/oauth/start", headers=tenant.operator).json()
    # Phase 20: the route now carries a one-use ticket (see the test_20 series).
    assert body["authorization_url"].startswith("/api/gmail/oauth/start?ticket=")
    assert config.GOOGLE_OAUTH_CLIENT_ID not in json.dumps(body)
    assert "accounts.google.com" not in json.dumps(body)


def test_5_6_no_token_or_code_is_written_to_logs(client, tenant, fake, capfd, monkeypatch):
    """Drive a full call path and read everything the process printed."""
    from app import config, gmail_store

    refresh = gmail_store.reveal_refresh_token(tenant.organization_id)
    fake_code = "4/0AY0e-authorization-code-" + uuid4().hex

    call(client, tenant, "search", payload={"query": "marco"})
    client.get("/api/gmail/status", headers=tenant.operator)
    # A callback with a bad state, so a code is handled and rejected.
    client.get(f"/api/gmail/oauth/callback?code={fake_code}&state=forged")

    captured = capfd.readouterr()
    printed = captured.out + captured.err
    assert refresh not in printed
    assert fake_code not in printed
    assert "stand-in-access-token" not in printed
    if config.GOOGLE_OAUTH_CLIENT_SECRET:
        assert config.GOOGLE_OAUTH_CLIENT_SECRET not in printed


def test_7_no_gmail_token_appears_in_events_or_evidence(client, tenant, fake):
    from app import gmail_store

    refresh = gmail_store.reveal_refresh_token(tenant.organization_id)
    execution_id = str(uuid4())
    call(client, tenant, "search", payload={"query": "marco"}, execution_id=execution_id)

    texts = [
        client.get("/api/events", headers=tenant.operator).text,
        client.get(f"/api/executions/{execution_id}/evidence", headers=tenant.operator).text,
        client.get(f"/api/executions/{execution_id}/connector-calls", headers=tenant.operator).text,
    ]
    for text in texts:
        assert refresh not in text
        assert "stand-in-access-token" not in text


# -- 8, 9, 10: revocation ---------------------------------------------------


def test_8_revoking_the_grant_invalidates_access_immediately(client, tenant, fake):
    assert call(client, tenant, "search", payload={"query": "marco"}).json()["executed"] is True

    revoked = client.post(
        f"/api/gmail/agents/{tenant.agent_id}/revoke", headers=tenant.operator
    )
    assert revoked.status_code == 200
    assert revoked.json()["revoked"] is True

    after = call(client, tenant, "search", payload={"query": "marco"}).json()
    assert after["decision"] == "BLOCK"
    assert after["executed"] is False
    _quiet(fake)


def test_9_a_revoked_agent_cannot_use_gmail_even_with_a_grant(client, tenant, fake):
    """The grant is not a way around credential revocation."""
    client.post(f"/api/agents/{tenant.agent_id}/revoke", headers=tenant.operator)
    assert call(client, tenant, "search", payload={"query": "marco"}).status_code == 401
    _quiet(fake)


def test_9b_a_revoked_agent_cannot_be_granted_access(client, tenant, fake):
    client.post(f"/api/agents/{tenant.agent_id}/revoke", headers=tenant.operator)
    response = client.post(
        f"/api/gmail/agents/{tenant.agent_id}/grant", headers=tenant.operator
    )
    assert response.status_code == 409


def test_10_disconnect_leaves_nothing_usable(client, tenant, fake, store_path):
    client.post("/api/gmail/disconnect", headers=tenant.operator)

    blocked = call(client, tenant, "search", payload={"query": "marco"}).json()
    assert blocked["decision"] == "BLOCK"
    assert tenant.organization_id not in json.loads(store_path.read_text())["connections"]

    db = SessionLocal()
    try:
        assert gmail_access.agents_with_access(db, tenant.organization_id) == []
    finally:
        db.close()
    _quiet(fake)


# -- 11, 12: the OAuth callback --------------------------------------------


@pytest.mark.parametrize(
    "state",
    ["", "forged", "a.b", "notbase64.notbase64", "eyJvcmciOiAiaGFjayJ9.zzzz"],
)
def test_11_an_invalid_oauth_state_is_rejected(client, fake, state):
    response = client.get(f"/api/gmail/oauth/callback?code=irrelevant&state={state}")
    # Either a 400 from state verification, or the plain "not connected" page.
    assert response.status_code in (200, 400)
    if response.status_code == 200:
        assert "not connected" in response.text.lower()


def test_11b_an_expired_state_is_rejected(client, tenant, fake, monkeypatch):
    from app.routers import gmail as gmail_router

    monkeypatch.setattr(gmail_router, "STATE_TTL_SECONDS", -1)
    state = gmail_router._sign_state(tenant.organization_id, "user-1")
    with pytest.raises(Exception):
        gmail_router._verify_state(state)


def test_12_the_callback_cannot_be_steered_to_another_tenant(client, fake):
    """Tenant is read from the signed state, never from a query parameter."""
    from app.routers import gmail as gmail_router

    tenant_a = build_tenant(client, name="Tenant A")
    tenant_b = build_tenant(client, name="Tenant B")

    state = gmail_router._sign_state(tenant_a.organization_id, "user-a")
    claims = gmail_router._verify_state(state)
    assert claims["org"] == tenant_a.organization_id

    # Appending another tenant's ids changes nothing: the callback reads org
    # from the verified state and ignores everything else on the query string.
    response = client.get(
        "/api/gmail/oauth/callback"
        f"?code=irrelevant&state={state}"
        f"&org_id={tenant_b.organization_id}"
        f"&organization_id={tenant_b.organization_id}"
        f"&agent_id={tenant_b.agent_id}"
    )
    assert response.status_code == 200
    # The exchange fails (there is no real Google here), and crucially no
    # connection was written for tenant B.
    from app import gmail_store

    b_connection = gmail_store.get_connection(tenant_b.organization_id)
    assert b_connection is not None
    assert b_connection.google_email == "mailbox@example.test"


def test_12b_a_state_signed_with_the_wrong_key_is_rejected(client, tenant, fake, monkeypatch):
    from app import config
    from app.routers import gmail as gmail_router

    state = gmail_router._sign_state(tenant.organization_id, "user-1")
    monkeypatch.setattr(config, "SECRET_KEY", "a-different-signing-key")
    with pytest.raises(Exception):
        gmail_router._verify_state(state)


# -- 20: starting OAuth carries a ticket, finishing it wants the browser ----------
#
# Phase 20. The session token used to travel in the start URL, and the state
# alone was enough to finish the flow from any browser. Neither is true now.


def _ticket_url(client, tenant) -> str:
    return client.post("/api/gmail/oauth/start", headers=tenant.operator).json()[
        "authorization_url"
    ]


def _start_flow(client, tenant):
    """What the dashboard does: ask for a ticket, then navigate to it.

    Returns the state Google would hand back to the callback, and the redirect
    (whose Set-Cookie went into the test client's jar, like a browser's).
    """
    from urllib.parse import parse_qs, urlparse

    redirect = client.get(_ticket_url(client, tenant), follow_redirects=False)
    assert redirect.status_code == 302, redirect.text
    state = parse_qs(urlparse(redirect.headers["location"]).query)["state"][0]
    return state, redirect


def _stub_exchange(monkeypatch):
    """Replace the call to Google's token endpoint. Reaching it means the
    callback accepted the state and the cookie; the list records each reach."""
    import httpx

    calls = []

    def post(*args, **kwargs):
        calls.append(1)
        raise httpx.ConnectError("no network in tests")

    monkeypatch.setattr(httpx, "post", post)
    return calls


def test_20a_the_session_token_is_not_in_the_start_url(client, tenant, fake):
    url = _ticket_url(client, tenant)
    session_token = tenant.operator["Authorization"].split(" ", 1)[1]
    assert session_token not in url
    assert url.startswith("/api/gmail/oauth/start?ticket=")


def test_20b_a_ticket_works_once(client, tenant, fake):
    from app import config

    url = _ticket_url(client, tenant)
    first = client.get(url, follow_redirects=False)
    assert first.status_code == 302
    assert first.headers["location"].startswith(config.GOOGLE_AUTH_ENDPOINT)
    assert client.get(url, follow_redirects=False).status_code == 401


def test_20c_a_ticket_expires(client, tenant, fake, monkeypatch):
    from app.routers import gmail as gmail_router

    monkeypatch.setattr(gmail_router, "TICKET_TTL_SECONDS", -1)
    assert client.get(_ticket_url(client, tenant), follow_redirects=False).status_code == 401


def test_20d_the_old_token_parameter_is_gone_and_a_forged_ticket_is_refused(client, tenant, fake):
    session_token = tenant.operator["Authorization"].split(" ", 1)[1]
    by_token = client.get(f"/api/gmail/oauth/start?token={session_token}", follow_redirects=False)
    assert by_token.status_code == 422  # `ticket` is required; a session token is not accepted
    forged = client.get("/api/gmail/oauth/start?ticket=not-a-ticket", follow_redirects=False)
    assert forged.status_code == 401


def test_20e_the_start_sets_an_httponly_lax_cookie(client, tenant, fake):
    _, redirect = _start_flow(client, tenant)
    cookie = redirect.headers["set-cookie"].lower()
    assert "aegis_oauth_nonce=" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "path=/api/gmail/oauth" in cookie


def test_20f_the_callback_needs_the_browser_that_started_the_flow(client, tenant, fake, monkeypatch):
    calls = _stub_exchange(monkeypatch)
    state, _ = _start_flow(client, tenant)

    client.cookies.clear()  # another browser, holding the same state
    response = client.get(f"/api/gmail/oauth/callback?code=irrelevant&state={state}")
    assert response.status_code == 200
    assert "not started from this browser" in response.text
    assert calls == []  # the code was never exchanged


def test_20g_with_the_cookie_the_flow_proceeds_once(client, tenant, fake, monkeypatch):
    calls = _stub_exchange(monkeypatch)
    state, _ = _start_flow(client, tenant)  # the cookie is now in the client's jar

    first = client.get(f"/api/gmail/oauth/callback?code=irrelevant&state={state}")
    # Past the state and cookie checks; it stopped at the (stubbed) exchange.
    assert "could not reach google" in first.text.lower()
    assert calls == [1]

    replay = client.get(f"/api/gmail/oauth/callback?code=irrelevant&state={state}")
    assert "not started from this browser" in replay.text
    assert calls == [1]  # a state is accepted once


def test_20h_a_cookie_from_another_flow_does_not_match(client, tenant, fake, monkeypatch):
    calls = _stub_exchange(monkeypatch)
    first_state, _ = _start_flow(client, tenant)
    _start_flow(client, tenant)  # the jar now holds the second flow's nonce

    response = client.get(f"/api/gmail/oauth/callback?code=irrelevant&state={first_state}")
    assert "not started from this browser" in response.text
    assert calls == []


# -- 13, 14, 15: the policy is unchanged ------------------------------------


def test_13_an_agent_can_only_use_the_capabilities_it_was_granted(client, fake):
    """A search-only agent, with a mailbox grant, still cannot read."""
    search_only = build_tenant(client, name="Search Only", actions=("SEARCH",))

    assert call(client, search_only, "search", payload={"query": "marco"}).json()["executed"] is True

    denied = call(client, search_only, "read", payload={"message_id": "m-1"}).json()
    assert denied["decision"] == "BLOCK"
    assert denied["executed"] is False


def test_13b_a_mailbox_grant_does_not_widen_any_capability(client, tenant, fake):
    """The grant decides access to the mailbox, never which operations run."""
    denied = call(client, tenant, "delete", payload={"message_id": "m-1"}).json()
    assert denied["decision"] == "BLOCK"
    assert fake.trashed == []


def test_14_gmail_send_still_requires_approval(client, tenant, fake):
    pending = call(
        client, tenant, "send",
        payload={"to": "marco@example.test", "subject": "s", "body": "b"},
    ).json()
    assert pending["decision"] == "APPROVAL"
    assert pending["executed"] is False
    assert pending["approval_id"]
    assert fake.sent == []


def test_14b_an_approved_send_still_works_after_the_grant_flow(client, tenant, fake):
    execution_id = str(uuid4())
    request_id = f"send-{uuid4().hex[:8]}"
    payload = {"to": "marco@example.test", "subject": "s", "body": "b"}
    pending = call(
        client, tenant, "send", payload=payload,
        execution_id=execution_id, request_id=request_id,
    ).json()
    approve(client, tenant, pending["approval_id"])
    done = call(
        client, tenant, "send", payload=payload,
        execution_id=execution_id, request_id=request_id,
    ).json()
    assert done["executed"] is True
    assert len(fake.sent) == 1


def test_14c_a_send_by_an_ungranted_agent_never_reaches_a_human(client, fake):
    """No grant means no approval request, not an approval a human might grant.

    Queueing a decision that cannot execute whatever the answer is how a real
    request gets approved by mistake in a busy queue.
    """
    tenant = build_tenant(client, name="Ungranted", grant_mailbox=False)
    body = call(
        client, tenant, "send",
        payload={"to": "marco@example.test", "subject": "s", "body": "b"},
    ).json()
    assert body["decision"] == "BLOCK"
    assert body["approval_id"] is None

    pending = client.get("/api/approvals?status_filter=pending", headers=tenant.operator).json()
    assert all(row["agent_id"] != tenant.agent_id for row in pending)
    assert fake.sent == []


def test_15_gmail_delete_is_still_denied(client, tenant, fake):
    body = call(client, tenant, "delete", payload={"message_id": "m-1"}).json()
    assert body["decision"] == "BLOCK"
    assert body["executed"] is False
    assert fake.trashed == []


# -- the grant API itself ---------------------------------------------------


def test_the_grant_endpoint_refuses_when_no_mailbox_is_connected(client, fake):
    tenant = build_tenant(client, name="Ungranted", grant_mailbox=False)
    from app import gmail_store

    gmail_store.disconnect(tenant.organization_id)
    response = client.post(
        f"/api/gmail/agents/{tenant.agent_id}/grant", headers=tenant.operator
    )
    assert response.status_code == 409


def test_an_agent_cannot_grant_itself_mailbox_access(client, tenant, fake):
    for path in (
        f"/api/gmail/agents/{tenant.agent_id}/grant",
        f"/api/gmail/agents/{tenant.agent_id}/revoke",
    ):
        response = client.post(path, headers={"X-Agent-Token": tenant.agent_token})
        assert response.status_code in (401, 403), path


def test_granting_twice_is_idempotent(client, tenant, fake):
    first = client.post(f"/api/gmail/agents/{tenant.agent_id}/grant", headers=tenant.operator)
    second = client.post(f"/api/gmail/agents/{tenant.agent_id}/grant", headers=tenant.operator)
    assert first.status_code == second.status_code == 200

    db = SessionLocal()
    try:
        rows = (
            db.query(models.GmailGrant)
            .filter(
                models.GmailGrant.organization_id == tenant.organization_id,
                models.GmailGrant.agent_id == tenant.agent_id,
                models.GmailGrant.status == "active",
            )
            .count()
        )
        assert rows == 1
    finally:
        db.close()


def test_status_lists_exactly_the_agents_with_access(client, tenant, fake):
    other = create_gmail_agent(client, tenant.operator, name="Second Agent")
    status = client.get("/api/gmail/status", headers=tenant.operator).json()
    granted = {row["agent_id"] for row in status["agents_with_access"]}
    assert tenant.agent_id in granted
    assert other[0] not in granted

    client.post(f"/api/gmail/agents/{other[0]}/grant", headers=tenant.operator)
    status = client.get("/api/gmail/status", headers=tenant.operator).json()
    granted = {row["agent_id"] for row in status["agents_with_access"]}
    assert other[0] in granted


# -- the agent setup endpoint ----------------------------------------------


def test_setup_lists_only_the_operations_this_agent_may_name(client, fake):
    search_only = build_tenant(client, name="Search Only Setup", actions=("SEARCH",))
    setup = client.get(
        f"/api/agents/{search_only.agent_id}/setup", headers=search_only.operator
    ).json()
    canonical = {row["canonical"] for row in setup["operations"]}
    assert canonical == {"gmail.SEARCH"}
    assert setup["auth_header"] == "X-Agent-Token"


def test_setup_never_contains_a_credential(client, tenant, fake):
    from app import config, gmail_store

    setup = client.get(f"/api/agents/{tenant.agent_id}/setup", headers=tenant.operator)
    text = setup.text
    for secret in (
        tenant.agent_token,
        gmail_store.reveal_refresh_token(tenant.organization_id),
        config.GOOGLE_OAUTH_CLIENT_SECRET,
        config.GOOGLE_OAUTH_CLIENT_ID,
        config.CRM_SECRET,
        config.EAT_KEY,
        config.INTERNAL_TOOL_TOKEN,
        config.INTERNAL_GATEWAY_TOKEN,
    ):
        if secret:
            assert secret not in text
    # And it does not leak the internal topology either.
    assert "credential-broker" not in text
    assert "protected-tool" not in text
    assert "gmail-connector" not in text


def test_setup_is_tenant_scoped_and_not_readable_by_an_agent(client, tenant, fake):
    rival = build_tenant(client, name="Rival Setup")
    assert (
        client.get(f"/api/agents/{tenant.agent_id}/setup", headers=rival.operator).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/agents/{tenant.agent_id}/setup",
            headers={"X-Agent-Token": tenant.agent_token},
        ).status_code
        in (401, 403)
    )


# -- the simulator agrees with the gateway ----------------------------------


def test_the_simulator_and_the_gateway_agree_about_mailbox_access(client, fake):
    """An operator must not be told ALLOW for something the gateway blocks."""
    tenant = build_tenant(client, name="Simulated", grant_mailbox=False)
    body = {"resource_kind": "gmail", "action": "SEARCH", "scope": "mailbox"}

    simulated = client.post(
        f"/api/agents/{tenant.agent_id}/simulate", headers=tenant.operator, json=body
    ).json()
    real = call(client, tenant, "search", payload={"query": "marco"}).json()
    assert simulated["decision"] == real["decision"] == "BLOCK"
    assert any(step["layer"] == "mailbox access" for step in simulated["steps"])

    client.post(f"/api/gmail/agents/{tenant.agent_id}/grant", headers=tenant.operator)

    simulated = client.post(
        f"/api/agents/{tenant.agent_id}/simulate", headers=tenant.operator, json=body
    ).json()
    real = call(client, tenant, "search", payload={"query": "marco"}).json()
    assert simulated["decision"] == real["decision"] == "ALLOW"


def test_the_simulator_still_reports_send_as_approval_and_delete_as_block(client, tenant, fake):
    for action, expected in (("SEND", "APPROVAL"), ("DELETE", "BLOCK")):
        simulated = client.post(
            f"/api/agents/{tenant.agent_id}/simulate",
            headers=tenant.operator,
            json={"resource_kind": "gmail", "action": action, "scope": "mailbox"},
        ).json()
        assert simulated["decision"] == expected, action


# -- the operator walkthrough ----------------------------------------------


def test_the_whole_onboarding_path_in_order(client, fake, store_path):
    """The exact sequence the dashboard walks, with the state at each step.

    Not a convenience wrapper: every call is one the UI makes, in the order it
    makes them, and the assertions are what the operator is shown in between.
    A step that silently made the agent ready early would fail here.
    """
    # 1. Sign in (register, for a fresh tenant).
    operator = register_operator(client)

    # 2. Create the agent. It starts with no authority at all.
    created = client.post(
        "/api/agents",
        headers=operator,
        json={"name": "Sales Assistant", "provider": "custom", "model": "external", "description": ""},
    )
    assert created.status_code == 200
    agent_id = created.json()["agent"]["id"]
    agent_token = created.json()["token"]
    # The token is returned exactly once, here.
    assert agent_token.startswith("aegis_")

    setup = client.get(f"/api/agents/{agent_id}/setup", headers=operator).json()
    assert setup["operations"] == [], "a new agent may name nothing"

    # 3. Grant capabilities.
    install_canonical_policy(client, operator)
    for action in ("SEARCH", "READ", "DRAFT", "SEND"):
        assert client.post(
            f"/api/agents/{agent_id}/permissions",
            headers=operator,
            json={"resource_kind": "gmail", "action": action, "scope": "mailbox", "effect": "allow"},
        ).status_code == 200

    contract = client.post(
        f"/api/agents/{agent_id}/contracts",
        headers=operator,
        json={
            "organization_id": "ignored",
            "agent_id": "ignored",
            "contract_id": f"walkthrough-{uuid4().hex[:8]}",
            "version": 1,
            "status": "ACTIVE",
            "purpose": "Mail assistant",
            "capabilities": [
                {"name": "gmail", "resource_kind": "gmail", "actions": ["SEARCH", "READ", "DRAFT", "SEND"]}
            ],
            "resources": [{"kind": "gmail", "scope": "mailbox"}],
            "constraints": {},
            "data_constraints": {},
            "approval_rules": [{"resource_kind": "gmail", "action": "SEND", "require": "human"}],
        },
    )
    assert contract.status_code in (200, 201)

    organization_id = client.get(f"/api/agents/{agent_id}", headers=operator).json()[
        "organization_id"
    ]
    from .phase19_harness import Tenant

    tenant = Tenant(
        organization_id=organization_id,
        operator=operator,
        agent_id=agent_id,
        agent_token=agent_token,
        contract_id="walkthrough",
    )

    # 4. Capabilities alone are not enough: no mailbox, so nothing works.
    status = client.get(f"/api/gmail/agents/{agent_id}", headers=operator).json()
    assert status["connected"] is False
    assert status["allowed"] is False
    blocked = call(client, tenant, "search", payload={"query": "marco"}).json()
    assert blocked["decision"] == "BLOCK"

    # 5. Connect the mailbox. The dashboard is handed an Aegis route.
    start = client.post("/api/gmail/oauth/start", headers=operator).json()
    assert start["authorization_url"].startswith("/api/gmail/oauth/start?ticket=")
    connect_gmail(organization_id)  # stands in for the Google round trip

    # 6. Connected, and STILL not ready: the grant is a separate act.
    status = client.get(f"/api/gmail/agents/{agent_id}", headers=operator).json()
    assert status["connected"] is True
    assert status["granted"] is False
    assert status["allowed"] is False
    still_blocked = call(client, tenant, "search", payload={"query": "marco"}).json()
    assert still_blocked["decision"] == "BLOCK"

    # 7. Grant this agent the mailbox.
    assert client.post(f"/api/gmail/agents/{agent_id}/grant", headers=operator).status_code == 200
    status = client.get(f"/api/gmail/agents/{agent_id}", headers=operator).json()
    assert status["allowed"] is True
    assert status["google_email"] == "mailbox@example.test"

    # 8. Agent ready. The posture is the canonical one.
    assert call(client, tenant, "search", payload={"query": "marco"}).json()["executed"] is True
    assert call(client, tenant, "read", payload={"message_id": "m-1"}).json()["executed"] is True
    assert (
        call(client, tenant, "draft", payload={"to": "m@e.test", "subject": "s", "body": "b"})
        .json()["executed"]
        is True
    )
    assert (
        call(client, tenant, "send", payload={"to": "m@e.test", "subject": "s", "body": "b"})
        .json()["decision"]
        == "APPROVAL"
    )
    assert call(client, tenant, "delete", payload={"message_id": "m-1"}).json()["decision"] == "BLOCK"
    assert fake.sent == []
    assert fake.trashed == []

    # 9. The setup card now lists exactly the four granted operations.
    setup = client.get(f"/api/agents/{agent_id}/setup", headers=operator).json()
    assert {row["canonical"] for row in setup["operations"]} == {
        "gmail.SEARCH",
        "gmail.READ",
        "gmail.DRAFT",
        "gmail.SEND",
    }
