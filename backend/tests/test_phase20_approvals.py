"""Phase 20 -- approvals a person can use and an agent can rely on.

Phase 17 made an approval real: one approved request runs once. What it left
undone is everything around the human: a denied or expired request kept
answering APPROVAL so the agent waited out its whole timeout, the reviewer saw a
digest instead of the message, nobody was told a request was waiting, and two
reviewers could both "decide" the same request.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import config, models, ratelimit
from app.database import SessionLocal
from app.main import create_app
from app.routers import approvals as approvals_router
from app.security import utcnow
from app.services import approval_notify, approval_preview

PASSWORD = "a-long-enough-password"
SUBJECT = "Fatture di settembre"


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app("all")) as instance:
        yield instance


@pytest.fixture(autouse=True)
def _fresh_limiter():
    ratelimit.RATE.reset()
    yield
    ratelimit.RATE.reset()


def _org(client: TestClient, domain: str = "pilot-a.test"):
    suffix = uuid4().hex[:8]
    email = f"boss-{suffix}@{domain}"
    registered = client.post(
        "/api/auth/register",
        json={
            "organization_name": f"Org {suffix}",
            "full_name": "Boss",
            "email": email,
            "password": PASSWORD,
        },
    )
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    client.post(
        "/api/policies",
        headers=headers,
        json={
            "name": "Approve CRM updates",
            "resource_kind": "crm",
            "action": "UPDATE",
            "scope_pattern": "*",
            "decision": "APPROVAL",
            "priority": 1,
        },
    )
    return headers, email


def _agent(client: TestClient, headers: dict, name: str = "Worker"):
    created = client.post(
        "/api/agents",
        headers=headers,
        json={"name": name, "provider": "d", "model": "m", "description": ""},
    ).json()
    agent_id, token = created["agent"]["id"], created["token"]
    for kind, action, scope in (
        ("crm", "READ", "customers"),
        ("crm", "UPDATE", "customers"),
        ("email", "SEND", "*"),
    ):
        client.post(
            f"/api/agents/{agent_id}/permissions",
            headers=headers,
            json={"resource_kind": kind, "action": action, "scope": scope, "effect": "allow"},
        )
    client.post(
        f"/api/agents/{agent_id}/contracts",
        headers=headers,
        json={
            "organization_id": "x",
            "agent_id": "x",
            "contract_id": f"worker-{uuid4().hex[:8]}",
            "version": 1,
            "status": "ACTIVE",
            "purpose": "approvals fixture",
            "capabilities": [
                {"name": "crm", "resource_kind": "crm", "actions": ["READ", "UPDATE"]},
                {"name": "email", "resource_kind": "email", "actions": ["SEND"]},
            ],
            "resources": [
                {"kind": "crm", "scope": "customers"},
                {"kind": "email", "scope": "*"},
            ],
            "constraints": {},
            "data_constraints": {},
            "approval_rules": [],
        },
    )
    return agent_id, token


def _ask(client, token, *, request_id=None, payload=None, kind="crm", action="UPDATE", scope="customers"):
    return client.post(
        "/api/authorize",
        headers={"X-Agent-Token": token},
        json={
            "resource_kind": kind,
            "action": action,
            "scope": scope,
            "payload": {"id": "c-1"} if payload is None else payload,
            "request_id": request_id or str(uuid4()),
        },
    ).json()


def _decide(client, headers, approval_id, decision):
    return client.post(
        f"/api/approvals/{approval_id}/decide", headers=headers, json={"decision": decision}
    )


def _row(client, headers, approval_id) -> dict:
    return next(
        r for r in client.get("/api/approvals", headers=headers).json() if r["id"] == approval_id
    )


def _expire(approval_id: str, **extra) -> None:
    db = SessionLocal()
    try:
        db.query(models.Approval).filter(models.Approval.id == approval_id).update(
            {"expires_at": utcnow() - timedelta(minutes=1), **extra}
        )
        db.commit()
    finally:
        db.close()


def _pending_email(client, *, name="Worker"):
    headers, email = _org(client)
    agent_id, token = _agent(client, headers, name)
    result = _ask(
        client,
        token,
        kind="email",
        action="SEND",
        scope="external",
        payload={
            "to": "stranger@evil.test",
            "cc": "x@evil.test",
            "subject": SUBJECT,
            "body": "Ciao, ecco le fatture. " + "x" * 400,
        },
    )
    assert result["decision"] == "APPROVAL", result
    return headers, email, agent_id, token, result["approval_id"]


# ---------------------------------------------------------------------------
# What the agent is told
# ---------------------------------------------------------------------------


def test_a_denied_request_replays_as_block_so_the_agent_stops_waiting(client):
    headers, _ = _org(client)
    _, token = _agent(client, headers)
    request_id = str(uuid4())
    first = _ask(client, token, request_id=request_id)
    assert first["decision"] == "APPROVAL"
    assert _decide(client, headers, first["approval_id"], "BLOCK").status_code == 200
    again = _ask(client, token, request_id=request_id)
    assert again["decision"] == "BLOCK"
    assert "denied" in again["reason"].lower()


def test_an_expired_request_replays_as_block(client):
    headers, _ = _org(client)
    _, token = _agent(client, headers)
    request_id = str(uuid4())
    first = _ask(client, token, request_id=request_id)
    _expire(first["approval_id"])
    again = _ask(client, token, request_id=request_id)
    assert again["decision"] == "BLOCK"
    assert "expired" in again["reason"].lower()


def test_an_approval_that_expired_unused_replays_as_block(client):
    headers, _ = _org(client)
    _, token = _agent(client, headers)
    request_id = str(uuid4())
    first = _ask(client, token, request_id=request_id)
    assert _decide(client, headers, first["approval_id"], "ALLOW").status_code == 200
    _expire(first["approval_id"])
    again = _ask(client, token, request_id=request_id)
    assert again["decision"] == "BLOCK"


def test_a_pending_request_keeps_answering_approval(client):
    headers, _ = _org(client)
    _, token = _agent(client, headers)
    request_id = str(uuid4())
    _ask(client, token, request_id=request_id)
    assert _ask(client, token, request_id=request_id)["decision"] == "APPROVAL"


def test_an_approved_request_still_runs_exactly_once(client):
    headers, _ = _org(client)
    _, token = _agent(client, headers)
    request_id = str(uuid4())
    first = _ask(client, token, request_id=request_id)
    _decide(client, headers, first["approval_id"], "ALLOW")
    assert _ask(client, token, request_id=request_id)["decision"] == "ALLOW"
    assert _ask(client, token, request_id=request_id)["decision"] != "ALLOW"


def test_the_block_answer_does_not_rewrite_the_recorded_event(client):
    headers, _ = _org(client)
    _, token = _agent(client, headers)
    request_id = str(uuid4())
    first = _ask(client, token, request_id=request_id)
    _decide(client, headers, first["approval_id"], "BLOCK")
    _ask(client, token, request_id=request_id)
    events = [e for e in client.get("/api/events", headers=headers).json() if e["request_id"] == request_id]
    assert [e["decision"] for e in events] == ["APPROVAL"], "the audit trail keeps what happened"


# ---------------------------------------------------------------------------
# The agent can ask where its request stands
# ---------------------------------------------------------------------------


def _status(client, token, approval_id):
    return client.get(f"/api/authorize/approvals/{approval_id}", headers={"X-Agent-Token": token})


def test_the_agent_can_follow_its_own_approval(client):
    headers, _ = _org(client)
    _, token = _agent(client, headers)
    request_id = str(uuid4())
    first = _ask(client, token, request_id=request_id)
    approval_id = first["approval_id"]
    assert _status(client, token, approval_id).json()["status"] == "pending"
    _decide(client, headers, approval_id, "ALLOW")
    body = _status(client, token, approval_id).json()
    assert body["status"] == "approved" and body["next"] == "resubmit"
    _ask(client, token, request_id=request_id)
    assert _status(client, token, approval_id).json()["status"] == "consumed"


def test_status_reports_denied_and_expired(client):
    headers, _ = _org(client)
    _, token = _agent(client, headers)
    denied = _ask(client, token)["approval_id"]
    _decide(client, headers, denied, "BLOCK")
    assert _status(client, token, denied).json()["status"] == "denied"
    late = _ask(client, token)["approval_id"]
    _expire(late)
    body = _status(client, token, late).json()
    assert body["status"] == "expired" and body["next"] == "stop"


def test_another_agents_approval_is_invisible(client):
    headers, _ = _org(client)
    _, mine = _agent(client, headers, "Mine")
    _, theirs = _agent(client, headers, "Theirs")
    approval_id = _ask(client, mine)["approval_id"]
    assert _status(client, theirs, approval_id).status_code == 404


def test_status_needs_an_agent_credential(client):
    headers, _ = _org(client)
    _, token = _agent(client, headers)
    approval_id = _ask(client, token)["approval_id"]
    assert client.get(f"/api/authorize/approvals/{approval_id}").status_code == 401
    assert client.get(f"/api/authorize/approvals/{approval_id}", headers=headers).status_code == 401


# ---------------------------------------------------------------------------
# Deciding
# ---------------------------------------------------------------------------


def test_only_one_decision_can_win(client):
    headers, email = _org(client)
    _, token = _agent(client, headers)
    approval_id = _ask(client, token)["approval_id"]
    first, second = SessionLocal(), SessionLocal()
    try:
        reviewer = first.query(models.User).filter(models.User.email == email).one().id
        row_a = first.get(models.Approval, approval_id)
        row_b = second.get(models.Approval, approval_id)
        assert approvals_router.claim_decision(first, row_a, "approved", reviewer) is True
        first.commit()
        assert approvals_router.claim_decision(second, row_b, "denied", reviewer) is False
    finally:
        first.close()
        second.close()
    assert _row(client, headers, approval_id)["status"] == "approved"


def test_deciding_twice_over_http_is_refused(client):
    headers, _ = _org(client)
    _, token = _agent(client, headers)
    approval_id = _ask(client, token)["approval_id"]
    assert _decide(client, headers, approval_id, "ALLOW").status_code == 200
    assert _decide(client, headers, approval_id, "BLOCK").status_code in (400, 409)


def test_expired_requests_show_as_expired_and_stop_counting_as_pending(client):
    headers, _ = _org(client)
    _, token = _agent(client, headers)
    approval_id = _ask(client, token)["approval_id"]
    assert client.get("/api/stats", headers=headers).json()["pending_approvals"] == 1
    _expire(approval_id)
    assert _row(client, headers, approval_id)["effective_status"] == "expired"
    assert client.get("/api/stats", headers=headers).json()["pending_approvals"] == 0


def test_one_agent_cannot_flood_the_queue(client, monkeypatch):
    monkeypatch.setattr(config, "MAX_PENDING_APPROVALS_PER_AGENT", 2)
    headers, _ = _org(client)
    _, token = _agent(client, headers)
    answers = [_ask(client, token) for _ in range(3)]
    assert [a["decision"] for a in answers] == ["APPROVAL", "APPROVAL", "BLOCK"]
    assert "waiting" in answers[2]["reason"].lower()


# ---------------------------------------------------------------------------
# What the reviewer sees
# ---------------------------------------------------------------------------


def test_the_reviewer_sees_what_is_being_approved(client):
    headers, _, _, _, approval_id = _pending_email(client)
    row = _row(client, headers, approval_id)
    preview = row["preview"]
    assert preview["to"] == ["stranger@evil.test"]
    assert preview["cc"] == ["x@evil.test"]
    assert preview["subject"] == SUBJECT
    assert preview["body_excerpt"].startswith("Ciao, ecco le fatture.")
    assert len(preview["body_excerpt"]) <= 280
    assert row["agent_name"] == "Worker"
    assert row["effective_status"] == "pending"


def test_preview_masks_sensitive_fields_and_clips_long_ones(client):
    headers, _ = _org(client)
    _, token = _agent(client, headers)
    approval_id = _ask(
        client,
        token,
        payload={"id": "c-1", "password": "hunter2", "Api_Token": "t-123", "note": "n" * 500},
    )["approval_id"]
    fields = _row(client, headers, approval_id)["preview"]["fields"]
    assert fields["password"] == fields["Api_Token"] == "•••"
    assert len(fields["note"]) <= 160
    assert fields["id"] == "c-1"


def test_preview_is_sealed_at_rest_and_absent_from_the_evidence(client):
    headers, _, _, _, approval_id = _pending_email(client)
    db = SessionLocal()
    try:
        row = db.get(models.Approval, approval_id)
        assert row.preview_sealed and SUBJECT not in row.preview_sealed
        assert approval_preview.open_for(row)["subject"] == SUBJECT
        everything = " ".join(
            str(getattr(e, column.name))
            for e in db.query(models.Event).filter(models.Event.organization_id == row.organization_id)
            for column in models.Event.__table__.columns
        )
        assert SUBJECT not in everything, "message content must never enter the evidence chain"
    finally:
        db.close()


def test_a_preview_moved_to_another_approval_reads_as_nothing(client):
    _, _, _, _, first = _pending_email(client)
    _, _, _, _, second = _pending_email(client)
    db = SessionLocal()
    try:
        a, b = db.get(models.Approval, first), db.get(models.Approval, second)
        b.preview_sealed = a.preview_sealed
        assert approval_preview.open_for(b) is None
    finally:
        db.rollback()
        db.close()


def test_preview_is_deleted_after_its_retention(client):
    headers, _, _, _, approval_id = _pending_email(client)
    db = SessionLocal()
    try:
        db.query(models.Approval).filter(models.Approval.id == approval_id).update(
            {"preview_purge_at": utcnow() - timedelta(minutes=1)}
        )
        db.commit()
    finally:
        db.close()
    assert _row(client, headers, approval_id)["preview"] is None
    db = SessionLocal()
    try:
        assert db.get(models.Approval, approval_id).preview_sealed is None
    finally:
        db.close()


def test_a_decision_starts_the_retention_clock(client, monkeypatch):
    monkeypatch.setattr(config, "APPROVAL_PREVIEW_RETENTION_DAYS", 3)
    headers, _, _, _, approval_id = _pending_email(client)
    _decide(client, headers, approval_id, "ALLOW")
    db = SessionLocal()
    try:
        purge_at = db.get(models.Approval, approval_id).preview_purge_at
    finally:
        db.close()
    wanted = utcnow() + timedelta(days=3)
    assert abs(approval_preview.as_utc(purge_at) - wanted) < timedelta(minutes=2)


# ---------------------------------------------------------------------------
# One-tap links
# ---------------------------------------------------------------------------


def _link_for(client, approval_id, email, *, org_of=None):
    db = SessionLocal()
    try:
        approval = db.get(models.Approval, approval_id)
        user = db.query(models.User).filter(models.User.email == email).one()
        return approval_notify.make_link_token(
            approval_id,
            org_of or approval.organization_id,
            user.id,
            utcnow() + timedelta(minutes=10),
        ), user.id
    finally:
        db.close()


def test_link_token_round_trips_and_rejects_tampering():
    expires = utcnow() + timedelta(minutes=5)
    token = approval_notify.make_link_token("appr-1", "org-1", "user-1", expires)
    claims = approval_notify.verify_link_token(token)
    assert (claims.approval_id, claims.organization_id, claims.user_id) == ("appr-1", "org-1", "user-1")
    body, signature = token.split(".", 1)
    flipped = signature[:-2] + ("AA" if signature[-2:] != "AA" else "BB")
    assert approval_notify.verify_link_token(f"{body}.{flipped}") is None
    assert approval_notify.verify_link_token(token + "x") is None
    assert approval_notify.verify_link_token("garbage") is None
    assert approval_notify.verify_link_token("") is None


def test_an_expired_link_token_is_rejected():
    token = approval_notify.make_link_token("a", "o", "u", utcnow() - timedelta(seconds=1))
    assert approval_notify.verify_link_token(token) is None


def test_the_link_shows_the_request_and_decides_it_once(client):
    headers, email, _, token, approval_id = _pending_email(client)
    link, user_id = _link_for(client, approval_id, email)
    seen = client.get(f"/api/approvals/link/{link}")
    assert seen.status_code == 200
    body = seen.json()
    assert body["agent_name"] == "Worker" and body["preview"]["subject"] == SUBJECT
    assert body["effective_status"] == "pending"
    assert client.post(f"/api/approvals/link/{link}/decide", json={"decision": "ALLOW"}).status_code == 200
    row = _row(client, headers, approval_id)
    assert row["status"] == "approved" and row["reviewed_by"] == user_id
    assert client.post(f"/api/approvals/link/{link}/decide", json={"decision": "BLOCK"}).status_code == 409


def test_a_link_cannot_reach_another_organizations_approval(client):
    _, email, _, _, approval_id = _pending_email(client)
    _org(client)
    other_org = client.get("/api/auth/me", headers=_org(client)[0]).json()["organization"]["id"]
    link, _ = _link_for(client, approval_id, email, org_of=other_org)
    assert client.get(f"/api/approvals/link/{link}").status_code == 404
    assert client.post(f"/api/approvals/link/{link}/decide", json={"decision": "ALLOW"}).status_code == 404


def test_an_expired_request_cannot_be_approved_through_its_link(client):
    _, email, _, _, approval_id = _pending_email(client)
    link, _ = _link_for(client, approval_id, email)
    _expire(approval_id)
    assert client.post(f"/api/approvals/link/{link}/decide", json={"decision": "ALLOW"}).status_code == 409


def test_a_bad_link_is_a_plain_404(client):
    assert client.get("/api/approvals/link/garbage").status_code == 404
    assert client.post("/api/approvals/link/garbage/decide", json={"decision": "ALLOW"}).status_code == 404


def test_link_endpoints_are_rate_limited(client, monkeypatch):
    monkeypatch.setattr(config, "AUTH_RATE_PER_MIN", 2)
    codes = [client.get("/api/approvals/link/garbage").status_code for _ in range(3)]
    assert codes == [404, 404, 429]


# ---------------------------------------------------------------------------
# Telling the reviewer
# ---------------------------------------------------------------------------


def test_admins_get_a_link_and_nothing_about_the_message(client, monkeypatch):
    # The request is created with no mail server set, so nothing is sent yet.
    _, email, _, _, approval_id = _pending_email(client, name="Mailer Bot")
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.pilot.test")
    monkeypatch.setattr(config, "PUBLIC_APP_URL", "https://app.pilot.test")
    sent: list[dict] = []
    monkeypatch.setattr(approval_notify, "send_mail", lambda **kwargs: sent.append(kwargs))
    assert approval_notify.deliver_now(approval_id) == 1
    mail = sent[0]
    assert mail["to"] == email
    assert "https://app.pilot.test/a/" in mail["body"]
    assert "Mailer Bot" in mail["body"]
    assert SUBJECT not in mail["subject"] + mail["body"], "no message content by email"
    assert "stranger@evil.test" not in mail["subject"] + mail["body"]


def test_no_email_is_attempted_without_a_mail_server(client, monkeypatch):
    monkeypatch.setattr(config, "SMTP_HOST", "")
    called: list[dict] = []
    monkeypatch.setattr(approval_notify, "send_mail", lambda **kwargs: called.append(kwargs))
    _, _, _, _, approval_id = _pending_email(client)
    assert approval_notify.deliver_now(approval_id) == 0
    approval_notify.enqueue(approval_id)
    assert called == []


def test_no_email_for_a_request_nobody_can_still_decide(client, monkeypatch):
    headers, _, _, _, approval_id = _pending_email(client)
    _decide(client, headers, approval_id, "BLOCK")
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.pilot.test")
    monkeypatch.setattr(config, "PUBLIC_APP_URL", "https://app.pilot.test")
    called: list[dict] = []
    monkeypatch.setattr(approval_notify, "send_mail", lambda **kwargs: called.append(kwargs))
    assert approval_notify.deliver_now(approval_id) == 0 and called == []


def test_creating_a_request_notifies_by_itself_when_mail_is_configured(client, monkeypatch):
    """The other half of the two tests above: nobody has to call anything."""
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.pilot.test")
    monkeypatch.setattr(config, "PUBLIC_APP_URL", "https://app.pilot.test")
    delivered: list[str] = []
    monkeypatch.setattr(approval_notify, "deliver_now", lambda approval_id: delivered.append(approval_id) or 1)
    headers, _ = _org(client)
    _, token = _agent(client, headers)
    approval_id = _ask(client, token)["approval_id"]
    import time

    for _ in range(50):
        if delivered:
            break
        time.sleep(0.05)
    assert delivered == [approval_id]


def test_a_new_approval_triggers_one_notification_and_replays_none(client, monkeypatch):
    queued: list[str] = []
    monkeypatch.setattr(approval_notify, "enqueue", lambda approval_id: queued.append(approval_id))
    headers, _ = _org(client)
    _, token = _agent(client, headers)
    request_id = str(uuid4())
    first = _ask(client, token, request_id=request_id)
    _ask(client, token, request_id=request_id)
    assert queued == [first["approval_id"]]
