"""Phase 15 — Tamper-evident execution evidence chain.

HMAC-SHA256 hash chaining per execution_id with fail-closed verification.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app import models, schemas
from app.contract_store import save_contract
from app.database import Base
from app.engines.enforcement import authorize_request
from app.services.evidence_verifier import (
    EvidenceIntegrityError,
    GENESIS_EVIDENCE_HASH,
    assert_execution_evidence_integrity,
    compute_evidence_digest,
)


def _engine():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    return engine


def _db():
    session = sessionmaker(bind=_engine())()
    session.add(models.Organization(id="org-1", name="Acme", slug="acme"))
    session.flush()
    session.add(
        models.User(
            id="user-1",
            organization_id="org-1",
            email="a@acme.test",
            password_hash="x",
            full_name="Ada",
        )
    )
    session.flush()
    session.add(
        models.Agent(
            id="agent-1",
            organization_id="org-1",
            owner_id="user-1",
            name="Sales",
        )
    )
    session.flush()
    session.add(
        models.Permission(
            agent_id="agent-1",
            resource_kind="crm",
            action="READ",
            scope="customers",
            effect="allow",
        )
    )
    session.add(
        models.Permission(
            agent_id="agent-1",
            resource_kind="email",
            action="SEND",
            scope="internal",
            effect="allow",
        )
    )
    session.commit()
    return session


def _agent(db, agent_id="agent-1"):
    return db.query(models.Agent).filter_by(id=agent_id).one()


def _contract(**overrides):
    payload = {
        "organization_id": "org-1",
        "agent_id": "agent-1",
        "contract_id": "sales-contract",
        "version": 1,
        "status": "ACTIVE",
        "purpose": "bounded sales access",
        "capabilities": [
            {"name": "crm.read", "actions": ["READ"]},
            {"name": "email.send", "actions": ["SEND"]},
        ],
        "resources": [
            {"kind": "crm", "scope": "customers"},
            {"kind": "email", "scope": "internal"},
        ],
        "constraints": {
            "destination_restrictions": {"allow": ["internal"], "deny": ["external"]},
        },
        "data_constraints": {"allowed_fields": ["id", "name", "to"]},
        "workflow": {
            "initial_steps": ["read_crm"],
            "steps": [
                {"id": "read_crm", "resource_kind": "crm", "action": "READ"},
                {"id": "send_email", "resource_kind": "email", "action": "SEND"},
            ],
            "transitions": [{"from": "read_crm", "to": "send_email"}],
            "terminal_steps": ["send_email"],
        },
    }
    payload.update(overrides)
    return payload


def _authorize(db, agent, **body):
    request = {
        "resource_kind": "crm",
        "action": "READ",
        "scope": "customers",
        "request_id": str(uuid4()),
    }
    request.update(body)
    return authorize_request(db, agent, schemas.AuthorizeRequest(**request))


def _seed_chain(db, execution_id="exec-1"):
    save_contract(db, _contract())
    db.commit()
    first = _authorize(
        db,
        _agent(db),
        execution_id=execution_id,
        payload={"id": "1", "name": "Ada"},
    )
    second = _authorize(
        db,
        _agent(db),
        resource_kind="email",
        action="SEND",
        scope="internal",
        destination="internal",
        payload={"id": "1", "to": "ada@acme.test"},
        execution_id=execution_id,
    )
    return first, second


def test_intact_chain_validates():
    db = _db()
    first, second = _seed_chain(db, "exec-ok")
    assert first.event.evidence_hash
    assert first.event.previous_evidence_hash == GENESIS_EVIDENCE_HASH
    assert second.event.previous_evidence_hash == first.event.evidence_hash
    assert second.event.evidence_hash == compute_evidence_digest(second.event)
    execution = db.query(models.Execution).filter_by(id="exec-ok").one()
    assert execution.evidence_chain_tip == second.event.evidence_hash
    assert_execution_evidence_integrity(db, "exec-ok")


def test_modified_decision_is_detected_and_blocks():
    db = _db()
    first, _second = _seed_chain(db, "exec-mod")
    event = db.query(models.Event).filter_by(id=first.event.id).one()
    event.decision = "BLOCK"
    event.reason = "tampered"
    db.commit()

    with pytest.raises(EvidenceIntegrityError) as exc:
        assert_execution_evidence_integrity(db, "exec-mod")
    assert "mismatch" in str(exc.value).lower() or "hash" in str(exc.value).lower()

    with pytest.raises(EvidenceIntegrityError):
        _authorize(
            db,
            _agent(db),
            execution_id="exec-mod",
            payload={"id": "1", "name": "Ada"},
        )
    alerts = db.query(models.Alert).filter_by(
        title="Execution evidence integrity failure"
    ).all()
    assert alerts


def test_deleted_event_is_detected_and_blocks():
    db = _db()
    first, second = _seed_chain(db, "exec-del")
    db.delete(db.query(models.Event).filter_by(id=first.event.id).one())
    db.commit()

    with pytest.raises(EvidenceIntegrityError):
        assert_execution_evidence_integrity(db, "exec-del")

    remaining = db.query(models.Event).filter_by(id=second.event.id).one()
    assert remaining.previous_evidence_hash != GENESIS_EVIDENCE_HASH

    with pytest.raises(EvidenceIntegrityError):
        _authorize(
            db,
            _agent(db),
            resource_kind="email",
            action="SEND",
            scope="internal",
            destination="internal",
            payload={"id": "1", "to": "ada@acme.test"},
            execution_id="exec-del",
        )


def test_forged_unsigned_event_on_sealed_chain_is_blocked():
    db = _db()
    _seed_chain(db, "exec-forge")
    db.add(
        models.Event(
            organization_id="org-1",
            agent_id="agent-1",
            execution_id="exec-forge",
            seq=99,
            resource_kind="crm",
            action="READ",
            scope="customers",
            decision="ALLOW",
            request_id="forged-req",
        )
    )
    db.commit()

    with pytest.raises(EvidenceIntegrityError) as exc:
        assert_execution_evidence_integrity(db, "exec-forge")
    assert "missing" in str(exc.value).lower() or "hash" in str(exc.value).lower()

    with pytest.raises(EvidenceIntegrityError):
        _authorize(
            db,
            _agent(db),
            execution_id="exec-forge",
            payload={"id": "1", "name": "Ada"},
        )


def test_reordered_seq_breaks_chain():
    db = _db()
    first, second = _seed_chain(db, "exec-ord")
    first_row = db.query(models.Event).filter_by(id=first.event.id).one()
    second_row = db.query(models.Event).filter_by(id=second.event.id).one()
    first_row.seq, second_row.seq = second_row.seq, first_row.seq
    db.commit()

    with pytest.raises(EvidenceIntegrityError):
        assert_execution_evidence_integrity(db, "exec-ord")


def test_replaced_evidence_hash_is_rejected():
    db = _db()
    first, _second = _seed_chain(db, "exec-hash")
    event = db.query(models.Event).filter_by(id=first.event.id).one()
    event.evidence_hash = "0" * 64
    db.commit()

    with pytest.raises(EvidenceIntegrityError):
        assert_execution_evidence_integrity(db, "exec-hash")


def test_hmac_covers_execution_id_and_previous_hash():
    db = _db()
    first, second = _seed_chain(db, "exec-bind")
    assert first.event.execution_id == "exec-bind"
    assert first.event.previous_evidence_hash == GENESIS_EVIDENCE_HASH
    assert second.event.previous_evidence_hash == first.event.evidence_hash
    original = first.event.evidence_hash
    first.event.execution_id = "exec-other"
    assert compute_evidence_digest(first.event) != original
