"""Phase 20 -- who may create an organization, and what a new one starts with.

Registration used to be open to anyone with no limit, and a fresh organization
started with no policies at all, so its first request was always a BLOCK and the
"human approves external email" behaviour existed only for the seeded demo org.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import config, models, ratelimit
from app.database import SessionLocal
from app.main import create_app
from app.security import create_access_token, decode_access_token
from app.services import starter_pack

GOOD_PASSWORD = "a-long-enough-password"


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app("all")) as instance:
        yield instance


@pytest.fixture(autouse=True)
def _fresh_limiter():
    ratelimit.RATE.reset()
    yield
    ratelimit.RATE.reset()


@pytest.fixture
def production(monkeypatch):
    """Flip only the runtime checks; the app in `client` is already started."""
    monkeypatch.setattr(config, "ENV", "production")
    monkeypatch.setattr(config, "INVITE_CODES", ["pilot-code-1", "pilot-code-2"])
    monkeypatch.setattr(config, "AUTH_RATE_PER_MIN", 0)
    return monkeypatch


def _body(**over) -> dict:
    body = {
        "organization_name": f"Org {uuid4().hex[:6]}",
        "full_name": "Ada",
        "email": f"ada-{uuid4().hex[:8]}@pilot.test",
        "password": GOOD_PASSWORD,
    }
    body.update(over)
    return body


def _register(client: TestClient, **over):
    return client.post("/api/auth/register", json=_body(**over))


def _operator(client: TestClient) -> dict:
    token = _register(client).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Who may register
# ---------------------------------------------------------------------------


def test_registration_is_closed_in_production_when_no_invites_exist(client, production):
    production.setattr(config, "INVITE_CODES", [])
    assert _register(client, invite_code="anything").status_code == 403


def test_registration_needs_a_valid_invite_in_production(client, production):
    assert _register(client).status_code == 403
    assert _register(client, invite_code="nope").status_code == 403
    accepted = _register(client, invite_code="pilot-code-2")
    assert accepted.status_code == 200
    assert accepted.json()["access_token"]


def test_production_rejects_short_passwords(client, production):
    response = _register(client, invite_code="pilot-code-1", password="short-pass")
    assert response.status_code == 400


def test_development_needs_neither_an_invite_nor_a_long_password(client):
    assert _register(client, password="rival-pass").status_code == 200


@pytest.mark.parametrize(
    "email", ["not-an-email", "a@b", "two@@x.test", "spaces in@x.test", ""]
)
def test_email_shape_is_validated(client, email):
    assert _register(client, email=email).status_code == 422


def test_field_lengths_are_bounded(client):
    assert _register(client, organization_name="x" * 500).status_code == 422
    assert _register(client, full_name="").status_code == 422


# ---------------------------------------------------------------------------
# What a new organization starts with
# ---------------------------------------------------------------------------


def test_new_organization_gets_its_internal_domain_from_the_admin_email(client):
    email = f"Boss-{uuid4().hex[:6]}@Acme-Two.TEST"
    assert _register(client, email=email).status_code == 200
    db = SessionLocal()
    try:
        user = db.query(models.User).filter(models.User.email == email.lower()).one()
        org = db.get(models.Organization, user.organization_id)
        assert org.internal_domains == "acme-two.test"
    finally:
        db.close()


def test_new_organization_starts_with_the_safety_policies(client):
    headers = _operator(client)
    names = {p["name"] for p in client.get("/api/policies", headers=headers).json()}
    assert {
        "Hard-block payments",
        "Block CRM mass delete",
        "Block finance export",
        "Approve external email",
        "Allow internal email",
        "Gmail delete is never allowed",
        "Sending mail needs a human",
    } <= names


def test_starter_pack_is_idempotent(client):
    headers = _operator(client)
    org_id = client.get("/api/auth/me", headers=headers).json()["organization"]["id"]
    db = SessionLocal()
    try:
        before = (
            db.query(models.Policy).filter(models.Policy.organization_id == org_id).count()
        )
        added = starter_pack.install(db, org_id)
        db.commit()
        after = (
            db.query(models.Policy).filter(models.Policy.organization_id == org_id).count()
        )
    finally:
        db.close()
    assert added == 0
    assert before == after > 0


def test_each_organization_gets_its_own_copy_of_the_policies(client):
    first = client.get("/api/policies", headers=_operator(client)).json()
    second = client.get("/api/policies", headers=_operator(client)).json()
    assert {p["id"] for p in first}.isdisjoint({p["id"] for p in second})
    assert {p["organization_id"] for p in first}.isdisjoint(
        {p["organization_id"] for p in second}
    )


# ---------------------------------------------------------------------------
# Brute force and session lifetime
# ---------------------------------------------------------------------------


def _bad_login(client: TestClient):
    return client.post(
        "/api/auth/login", json={"email": "nobody@pilot.test", "password": "wrong"}
    )


def test_auth_endpoints_are_rate_limited(client, monkeypatch):
    monkeypatch.setattr(config, "AUTH_RATE_PER_MIN", 3)
    codes = [_bad_login(client).status_code for _ in range(5)]
    assert codes == [401, 401, 401, 429, 429]
    assert _bad_login(client).headers["retry-after"] == "60"


def test_rate_limit_is_off_when_set_to_zero(client, monkeypatch):
    monkeypatch.setattr(config, "AUTH_RATE_PER_MIN", 0)
    assert {_bad_login(client).status_code for _ in range(20)} == {401}


def test_login_and_register_are_counted_separately(client, monkeypatch):
    monkeypatch.setattr(config, "AUTH_RATE_PER_MIN", 2)
    assert [_bad_login(client).status_code for _ in range(2)] == [401, 401]
    assert _register(client).status_code == 200


def test_sliding_window_forgets_old_hits():
    window = ratelimit.SlidingWindow()
    assert window.allow("k", 2, 60, now=0)
    assert window.allow("k", 2, 60, now=1)
    assert not window.allow("k", 2, 60, now=2)
    assert window.allow("k", 2, 60, now=61), "the first hit is older than the window"


def test_access_token_lifetime_follows_config(monkeypatch):
    monkeypatch.setattr(config, "ACCESS_TOKEN_EXPIRE_MINUTES", 5)
    payload = decode_access_token(create_access_token("user-1", "org-1"))
    assert payload["exp"] - payload["iat"] == 300
