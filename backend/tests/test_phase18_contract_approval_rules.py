"""Phase 18 — a contract can require a human, by itself.

`approval_rules` were validated and stored from Phase 11 onward and never read.
Approval could therefore only be expressed as an organization-wide policy, so
"changes to customer records need a person" applied to every agent in the tenant
or to none. Phase 17 recorded this as an open limitation.

The rule is one-directional and these tests are mostly about that: a contract
may raise ALLOW to APPROVAL, and may never turn BLOCK or APPROVAL into ALLOW.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.orm import sessionmaker

from app import models, schemas
from app.contract_store import save_contract
from app.database import Base
from app.engines.contract import requires_human
from app.engines.enforcement import authorize_request

ORG = "org-ar"
AGENT = "agent-ar"


def _db():
    engine = create_engine("sqlite:///:memory:")

    @sa_event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(models.Organization(id=ORG, name="AR", slug="ar"))
    session.flush()
    session.add(
        models.User(
            id="u-ar", organization_id=ORG, email="ar@acme.test",
            password_hash="x", full_name="AR",
        )
    )
    session.flush()
    session.add(
        models.Agent(id=AGENT, organization_id=ORG, owner_id="u-ar", name="AR")
    )
    session.flush()
    for kind, action, scope in (
        ("crm", "READ", "customers"),
        ("crm", "UPDATE", "customers"),
        ("crm", "DELETE", "*"),
    ):
        session.add(
            models.Permission(
                agent_id=AGENT, resource_kind=kind, action=action,
                scope=scope, effect="allow",
            )
        )
    session.commit()
    return session


def _contract(session, approval_rules):
    save_contract(
        session,
        {
            "organization_id": ORG,
            "agent_id": AGENT,
            "contract_id": "ar-contract",
            "version": 1,
            "status": "ACTIVE",
            "purpose": "approval rules",
            "capabilities": [
                {
                    "name": "crm",
                    "resource_kind": "crm",
                    "actions": ["READ", "UPDATE", "DELETE"],
                }
            ],
            "resources": [{"kind": "crm", "scope": "*"}],
            "constraints": {},
            "data_constraints": {},
            "approval_rules": approval_rules,
        },
    )
    session.commit()


def _authorize(session, **body):
    request = {
        "resource_kind": "crm",
        "action": "READ",
        "scope": "customers",
        "request_id": str(uuid4()),
    }
    request.update(body)
    return authorize_request(
        session,
        session.query(models.Agent).filter_by(id=AGENT).one(),
        schemas.AuthorizeRequest(**request),
    )


# ---------------------------------------------------------------------------
# Raising the requirement
# ---------------------------------------------------------------------------


def test_contract_rule_raises_allow_to_approval():
    db = _db()
    _contract(db, [{"resource_kind": "crm", "action": "UPDATE", "require": "human"}])

    allowed = _authorize(db, action="READ")
    assert allowed.event.decision == "ALLOW"

    needs_human = _authorize(db, action="UPDATE")
    assert needs_human.event.decision == "APPROVAL"
    assert "requires human approval" in needs_human.event.reason
    assert needs_human.approval_id


def test_rule_is_scoped_to_the_named_action():
    db = _db()
    _contract(db, [{"resource_kind": "crm", "action": "UPDATE", "require": "human"}])
    assert _authorize(db, action="READ").event.decision == "ALLOW"


def test_rule_with_no_selector_matches_nothing():
    """A catch-all would silently send everything to a human."""
    db = _db()
    _contract(db, [{"require": "human"}])
    assert _authorize(db, action="READ").event.decision == "ALLOW"


def test_unrecognised_requirement_still_demands_a_human():
    """The safe reading of a rule we do not understand is not to skip it."""
    db = _db()
    _contract(
        db, [{"resource_kind": "crm", "action": "UPDATE", "require": "two-of-three"}]
    )
    assert _authorize(db, action="UPDATE").event.decision == "APPROVAL"


def test_action_only_rule_matches_any_resource():
    db = _db()
    _contract(db, [{"action": "UPDATE", "require": "human"}])
    assert _authorize(db, action="UPDATE").event.decision == "APPROVAL"


# ---------------------------------------------------------------------------
# Never lowering it
# ---------------------------------------------------------------------------


def test_contract_cannot_turn_a_policy_block_into_allow():
    db = _db()
    _contract(db, [{"resource_kind": "crm", "action": "DELETE", "require": "human"}])
    db.add(
        models.Policy(
            organization_id=ORG, name="no deletes", resource_kind="crm",
            action="DELETE", scope_pattern="*", decision="BLOCK", priority=1,
        )
    )
    db.commit()
    assert _authorize(db, action="DELETE", scope="all").event.decision == "BLOCK"


def test_contract_cannot_turn_a_permission_block_into_allow():
    db = _db()
    _contract(db, [{"resource_kind": "crm", "action": "EXPORT", "require": "human"}])
    # No permission for EXPORT at all.
    outcome = _authorize(db, action="EXPORT", scope="customers")
    assert outcome.event.decision == "BLOCK"


def test_contract_rule_does_not_downgrade_an_existing_approval():
    db = _db()
    _contract(db, [{"resource_kind": "crm", "action": "UPDATE", "require": "human"}])
    db.add(
        models.Policy(
            organization_id=ORG, name="updates reviewed", resource_kind="crm",
            action="UPDATE", scope_pattern="*", decision="APPROVAL", priority=1,
        )
    )
    db.commit()
    assert _authorize(db, action="UPDATE").event.decision == "APPROVAL"


def test_no_rules_means_no_change():
    db = _db()
    _contract(db, [])
    assert _authorize(db, action="UPDATE").event.decision == "ALLOW"


# ---------------------------------------------------------------------------
# The helper itself
# ---------------------------------------------------------------------------


def test_requires_human_matching():
    db = _db()
    _contract(
        db,
        [
            {"resource_kind": "crm", "action": "UPDATE", "require": "human"},
            {"resource_kind": "files", "action": "EXPORT", "decision": "APPROVAL"},
        ],
    )
    contract = db.query(models.RuntimeContract).one()
    assert requires_human(contract, "crm", "UPDATE") is not None
    assert requires_human(contract, "files", "EXPORT") is not None
    assert requires_human(contract, "crm", "READ") is None
    assert requires_human(contract, "payments", "TRANSFER") is None


def test_rule_marked_allow_is_not_an_approval_rule():
    db = _db()
    _contract(
        db,
        [{"resource_kind": "crm", "action": "UPDATE", "decision": "ALLOW"}],
    )
    contract = db.query(models.RuntimeContract).one()
    assert requires_human(contract, "crm", "UPDATE") is None
    assert _authorize(db, action="UPDATE").event.decision == "ALLOW"


def test_approval_from_a_contract_rule_still_binds_and_executes_once():
    """The grant machinery is the same regardless of where APPROVAL came from."""
    db = _db()
    _contract(db, [{"resource_kind": "crm", "action": "UPDATE", "require": "human"}])
    first = _authorize(
        db, action="UPDATE", execution_id="e-ar", request_id="r-ar",
        payload={"id": "1"},
    )
    assert first.event.decision == "APPROVAL"

    approval = db.query(models.Approval).filter_by(id=first.approval_id).one()
    assert approval.param_hash == first.event.payload_hash
    assert approval.contract_id == "ar-contract"
    assert approval.contract_version == 1
    assert approval.expires_at is not None
