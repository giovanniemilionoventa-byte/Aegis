"""Phase 17 — evidence a customer can actually verify.

Before Phase 17 `assert_execution_evidence_integrity` had exactly one caller in
production code: `authorize_request`. The chain was therefore only ever checked
as a side effect of the *next* authorization on the same execution. There was no
endpoint, no command and no UI, so an auditor, a customer or a reviewer had no
way to ask whether an execution's evidence was intact.

This router is that surface. It recomputes every digest from the stored rows and
reports a verdict, including which event first breaks the chain.

Deliberately absent: an endpoint that re-seals or backfills a chain.
`backfill_execution_evidence` exists as an offline migration helper for a
pre-Phase-15 database, and it must stay offline — an operator-facing re-seal
would let anyone who tampered with the database launder the result by asking
Aegis to sign the altered rows.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..engines.trajectory import owned_execution_events
from ..security import get_current_user
from ..services.evidence_verifier import (
    EvidenceIntegrityError,
    assert_execution_evidence_integrity,
    compute_evidence_digest,
)

router = APIRouter(tags=["evidence"])


def _execution_or_404(
    db: Session, user: models.User, execution_id: str
) -> models.Execution:
    execution = (
        db.query(models.Execution)
        .filter(
            models.Execution.id == execution_id,
            models.Execution.organization_id == user.organization_id,
        )
        .first()
    )
    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")
    return execution


@router.get("/executions")
def list_executions(
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = 100,
):
    rows = (
        db.query(models.Execution)
        .filter(models.Execution.organization_id == user.organization_id)
        .order_by(models.Execution.created_at.desc())
        .limit(min(limit, 500))
        .all()
    )
    return [
        {
            "id": row.id,
            "agent_id": row.agent_id,
            "created_at": row.created_at,
            "evidence_chain_tip": row.evidence_chain_tip,
            "event_count": db.query(models.Event)
            .filter(models.Event.execution_id == row.id)
            .count(),
        }
        for row in rows
    ]


@router.get("/executions/{execution_id}/evidence")
def execution_evidence(
    execution_id: str,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Full chain for one execution, with an independently recomputed verdict."""
    execution = _execution_or_404(db, user, execution_id)
    events = owned_execution_events(db, execution_id)

    verdict = {"valid": True, "reason": None, "first_bad_event": None}
    try:
        assert_execution_evidence_integrity(db, execution_id)
    except EvidenceIntegrityError as exc:
        verdict["valid"] = False
        verdict["reason"] = exc.reason

    chain = []
    for event in events:
        recomputed = None
        matches = None
        if event.evidence_hash:
            try:
                recomputed = compute_evidence_digest(event)
                matches = recomputed == event.evidence_hash
            except EvidenceIntegrityError:
                recomputed = None
                matches = False
        else:
            matches = False
        if verdict["valid"] is False and verdict["first_bad_event"] is None and not matches:
            verdict["first_bad_event"] = event.id
        chain.append(
            {
                "event_id": event.id,
                "seq": event.seq,
                "request_id": event.request_id,
                "resource_kind": event.resource_kind,
                "action": event.action,
                "scope": event.scope,
                "destination": event.destination,
                "decision": event.decision,
                "reason": event.reason,
                "payload_hash": event.payload_hash,
                "previous_evidence_hash": event.previous_evidence_hash,
                "evidence_hash": event.evidence_hash,
                "recomputed_evidence_hash": recomputed,
                "digest_matches": matches,
                "created_at": event.created_at,
            }
        )

    approvals = (
        db.query(models.Approval)
        .filter(models.Approval.execution_id == execution_id)
        .all()
    )

    return {
        "execution": {
            "id": execution.id,
            "organization_id": execution.organization_id,
            "agent_id": execution.agent_id,
            "created_at": execution.created_at,
            "evidence_chain_tip": execution.evidence_chain_tip,
        },
        "verdict": verdict,
        "event_count": len(chain),
        "chain": chain,
        "approvals": [
            {
                "id": row.id,
                "status": row.status,
                "resource_kind": row.resource_kind,
                "action": row.action,
                "scope": row.scope,
                "destination": row.destination,
                "contract_id": row.contract_id,
                "contract_version": row.contract_version,
                "param_hash": row.param_hash,
                "reviewed_by": row.reviewed_by,
                "reviewed_at": row.reviewed_at,
                "expires_at": row.expires_at,
                "consumed_at": row.consumed_at,
                "consumed_event_id": row.consumed_event_id,
            }
            for row in approvals
        ],
    }


@router.get("/executions/{execution_id}/connector-calls")
def execution_connector_calls(
    execution_id: str,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Phase 19 — what was asked, what was authorized, what actually ran.

    The sealed evidence chain answers "what did Aegis decide". This answers the
    two questions Phase 19 adds:

      * did the agent's account of what it was doing match the operation it
        actually requested (declared_intent vs canonical_operation), and
      * did anything actually happen at the protected service
        (connector_operation, which is NULL unless it did).

    A row where those disagree is the evidence of a divergence, not a bug in
    the recording. declared_intent is agent-supplied text and is labelled as
    untrusted in the response so nobody reading this mistakes it for a finding
    of fact.

    Tenant-scoped: the execution must belong to the caller's organization.
    """
    _execution_or_404(db, user, execution_id)
    rows = (
        db.query(models.ConnectorCall)
        .filter(
            models.ConnectorCall.organization_id == user.organization_id,
            models.ConnectorCall.execution_id == execution_id,
        )
        .order_by(models.ConnectorCall.created_at.asc())
        .all()
    )
    return {
        "execution_id": execution_id,
        "count": len(rows),
        "note": (
            "declared_intent is text supplied by the agent. It is recorded for "
            "comparison and is not read by any authorization path. The "
            "authoritative record is the sealed event chain."
        ),
        "calls": [
            {
                "id": row.id,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "agent_id": row.agent_id,
                "request_id": row.request_id,
                "event_id": row.event_id,
                "tool": row.tool,
                # The four facts, kept apart.
                "requested_operation": row.requested_operation,
                "canonical_operation": row.canonical_operation,
                "declared_intent_untrusted": row.declared_intent,
                "decision": row.decision,
                "approval_id": row.approval_id,
                "approval_granted": row.approval_granted,
                "executed": row.executed,
                "connector_operation": row.connector_operation,
                "result_status": row.result_status,
                "resource_ref": row.resource_ref,
                "error_code": row.error_code,
                "intent_matches_operation": _intent_matches(
                    row.declared_intent, row.canonical_operation
                ),
            }
            for row in rows
        ],
    }


# Words an operator would expect to see in an honest description of each
# canonical operation. A mismatch is a hint for a human, never a control:
# nothing branches on it, and a "False" here has never prevented anything.
_INTENT_HINTS = {
    "gmail.SEARCH": ("search", "find", "look", "cerca", "trova"),
    "gmail.READ": ("read", "open", "leggi", "apri", "review"),
    "gmail.DRAFT": ("draft", "compose", "write", "prepare", "bozza", "scrivi"),
    "gmail.SEND": ("send", "reply", "forward", "invia", "manda", "rispondi"),
    "gmail.DELETE": ("delete", "remove", "trash", "elimina", "cancella"),
}


def _intent_matches(intent: str | None, canonical: str | None):
    if not intent or not canonical:
        return None
    hints = _INTENT_HINTS.get(canonical)
    if not hints:
        return None
    return any(word in intent.lower() for word in hints)
