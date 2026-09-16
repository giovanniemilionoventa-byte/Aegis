"""Phase 19 — where the Google credential is, and everywhere it is not.

AGENT != AUTHORITY means the agent never holds the protected credential. That
is a claim about several separate things, and each is checked here on its own:

  * the stored form (sealed, not plaintext, bound to its tenant);
  * the code paths that can produce it (one function, named to be greppable);
  * the API surface (no response of any shape contains it);
  * the process boundary (the gateway and broker never receive it).

The deployment half -- which containers mount the store -- is in
test_phase19_network.py.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import config, gmail_store
from app.main import create_app
from app.secretbox import SecretBoxError, open_sealed, seal

from .gmail_fake import FakeGoogle
from .phase19_harness import build_tenant, call, isolate_oauth_store, use_fake_google

REAL_REFRESH_TOKEN = "stand-in-refresh-token"


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
    google.add_message(message_id="m-1", sender="marco@example.test", subject="Hi", body="text")
    use_fake_google(monkeypatch, google)
    return google


@pytest.fixture
def tenant(client, fake):
    return build_tenant(client)


# -- the stored form --------------------------------------------------------


def test_the_refresh_token_is_not_stored_in_plaintext(tenant, store_path):
    raw = store_path.read_text()
    assert REAL_REFRESH_TOKEN not in raw
    record = json.loads(raw)["connections"][tenant.organization_id]
    assert "refresh_token" not in record
    assert record["sealed_refresh_token"]
    assert REAL_REFRESH_TOKEN not in record["sealed_refresh_token"]


def test_the_stored_record_keeps_the_operator_facing_metadata(tenant, store_path):
    """Metadata is not secret and an operator needs it. Only the token is sealed."""
    record = json.loads(store_path.read_text())["connections"][tenant.organization_id]
    assert record["google_email"] == "mailbox@example.test"
    assert record["scopes"] == ["https://www.googleapis.com/auth/gmail.modify"]
    assert record["status"] == "connected"


def test_a_record_moved_between_tenants_stops_working(tenant, client, store_path, fake):
    """Lifting A's sealed blob into B's slot does not make it B's credential.

    The seal is bound to the organization, so the unseal fails rather than
    yielding a usable token.
    """
    other = build_tenant(client, name="Other")
    data = json.loads(store_path.read_text())
    stolen = data["connections"][tenant.organization_id]["sealed_refresh_token"]
    data["connections"][other.organization_id]["sealed_refresh_token"] = stolen
    store_path.write_text(json.dumps(data))

    with pytest.raises(gmail_store.GmailStoreError):
        gmail_store.reveal_refresh_token(other.organization_id)


def test_a_tampered_record_is_rejected_rather_than_decrypted(tenant, store_path):
    data = json.loads(store_path.read_text())
    sealed = data["connections"][tenant.organization_id]["sealed_refresh_token"]
    # Flip one character of the ciphertext.
    tampered = sealed[:-4] + ("A" if sealed[-4] != "A" else "B") + sealed[-3:]
    data["connections"][tenant.organization_id]["sealed_refresh_token"] = tampered
    store_path.write_text(json.dumps(data))

    with pytest.raises(gmail_store.GmailStoreError):
        gmail_store.reveal_refresh_token(tenant.organization_id)


def test_the_wrong_key_does_not_open_the_record(tenant, store_path, monkeypatch):
    monkeypatch.setattr(config, "GMAIL_OAUTH_ENCRYPTION_KEY", "a-different-key")
    with pytest.raises(gmail_store.GmailStoreError):
        gmail_store.reveal_refresh_token(tenant.organization_id)


def test_sealing_is_not_deterministic(tenant):
    """Two seals of the same token differ, so the file leaks no equality."""
    first = seal("same-token", "k", context="ctx")
    second = seal("same-token", "k", context="ctx")
    assert first != second
    assert open_sealed(first, "k", context="ctx") == open_sealed(second, "k", context="ctx")


# -- the API surface --------------------------------------------------------


def test_no_gmail_endpoint_returns_the_credential(client, tenant, fake):
    status = client.get("/api/gmail/status", headers=tenant.operator)
    assert status.status_code == 200
    body = status.json()
    assert body["connected"] is True
    assert body["connection"]["google_email"] == "mailbox@example.test"
    assert REAL_REFRESH_TOKEN not in status.text
    assert "refresh_token" not in status.text
    assert "access_token" not in status.text


def test_the_oauth_status_does_not_leak_the_client_secret(client, tenant, fake):
    status = client.get("/api/gmail/status", headers=tenant.operator)
    assert config.GOOGLE_OAUTH_CLIENT_SECRET not in status.text
    # It reports whether a client is configured, not what it is.
    assert status.json()["oauth_client_configured"] is True


def test_an_agent_cannot_reach_the_gmail_status_endpoint(client, tenant, fake):
    response = client.get(
        "/api/gmail/status", headers={"X-Agent-Token": tenant.agent_token}
    )
    assert response.status_code in (401, 403)


def test_an_agent_cannot_start_or_undo_an_oauth_flow(client, tenant, fake):
    for path in ("/api/gmail/oauth/start", "/api/gmail/disconnect"):
        response = client.post(path, headers={"X-Agent-Token": tenant.agent_token})
        assert response.status_code in (401, 403), path


def test_no_gmail_operation_result_contains_the_credential(client, tenant, fake):
    """Every allowed operation, checked against every secret in the deployment."""
    execution_id = str(uuid4())
    responses = [
        call(client, tenant, "search", payload={"query": "marco"}, execution_id=execution_id).text,
        call(client, tenant, "read", payload={"message_id": "m-1", "include_body": True}, execution_id=execution_id).text,
        call(client, tenant, "draft", payload={"to": "a@b.test", "subject": "s", "body": "b"}, execution_id=execution_id).text,
    ]
    haystack = "\n".join(responses)
    for secret in (
        REAL_REFRESH_TOKEN,
        "stand-in-access-token",
        config.GOOGLE_OAUTH_CLIENT_SECRET,
        config.GOOGLE_OAUTH_CLIENT_ID,
        config.GMAIL_OAUTH_ENCRYPTION_KEY,
        config.CRM_SECRET,
        config.EAT_KEY,
    ):
        if secret:
            assert secret not in haystack


# -- the process boundary ---------------------------------------------------


def test_the_gateway_never_receives_the_google_credential(client, tenant, fake, monkeypatch):
    """The gateway dispatches; it does not carry the credential.

    Recorded by capturing everything the gateway hands to the broker. The
    Google refresh token appears nowhere in it -- the connector reads it
    independently, one hop further in.
    """
    from app import remote

    captured = []

    def recording_dispatch(**kwargs):
        captured.append(kwargs)
        return {"ok": True, "operation": "search", "messages": []}

    monkeypatch.setattr(config, "BROKER_URL", "http://broker.test")
    monkeypatch.setattr("app.routers.gateway.dispatch_via_broker", recording_dispatch)

    call(client, tenant, "search", payload={"query": "marco"})
    assert captured, "the gateway did not dispatch"
    serialized = json.dumps(captured, default=str)
    assert REAL_REFRESH_TOKEN not in serialized
    assert "refresh_token" not in serialized
    assert "access_token" not in serialized


def test_the_broker_issues_a_connector_credential_not_a_google_one(tenant):
    """What the broker hands the connector is derived, per-tenant, and not Google's."""
    from app.credentials import broker

    issued = broker.issue("gmail", organization_id=tenant.organization_id)
    assert issued.secret != REAL_REFRESH_TOKEN
    assert len(issued.secret) == 64  # HMAC-SHA256 hex
    # And it is this tenant's, not another's.
    other = broker.issue("gmail", organization_id="some-other-org")
    assert issued.secret != other.secret


def test_only_one_function_can_produce_the_refresh_token():
    """A greppable invariant: one named unseal path, and the tests know it.

    If a second way to obtain the token appears, this fails and whoever added
    it has to say so out loud.
    """
    import inspect

    source = inspect.getsource(gmail_store)
    # The only place open_sealed is called.
    assert source.count("open_sealed(") == 1
    assert "def reveal_refresh_token" in source


def test_the_connector_holds_the_access_token_only_in_memory(client, tenant, fake, store_path):
    """Using Gmail does not write a bearer token anywhere."""
    call(client, tenant, "search", payload={"query": "marco"})
    assert fake.refresh_calls >= 1
    raw = store_path.read_text()
    assert "stand-in-access-token" not in raw
    assert "access_token" not in raw


def test_disconnecting_removes_the_credential_entirely(client, tenant, fake, store_path):
    response = client.post("/api/gmail/disconnect", headers=tenant.operator)
    assert response.status_code == 200
    assert response.json()["disconnected"] is True
    # The note must not imply a revocation at Google that did not happen.
    assert "myaccount.google.com" in response.json()["note"]

    assert tenant.organization_id not in json.loads(store_path.read_text())["connections"]
    with pytest.raises(gmail_store.GmailStoreError):
        gmail_store.reveal_refresh_token(tenant.organization_id)

    # And the agent can no longer act on that mailbox.
    assert call(client, tenant, "search", payload={"query": "marco"}).status_code in (409, 502, 503)
