"""Phase 17 — turning a human approval into one bounded execution authority.

Before Phase 17 `POST /api/approvals/{id}/decide` set `status='approved'` and
nothing in the codebase ever read that value. An operator could click Allow in
the dashboard and the action would never run: the gateway executes only on
`decision == "ALLOW"`, and replaying the original request returned the stored
APPROVAL event. The human-in-the-loop promise was not implemented.

Closing that loop safely is the hard part. An approval is a grant of authority,
so it has to be as tightly bound as an EAT, and for the same reason: whatever is
not bound can be mutated after the human said yes.

A grant authorizes exactly one request:

    organization, agent, execution, request id,
    resource kind, action, scope, destination,
    payload digest, contract id and version

and it is valid only while it is approved, unexpired and unconsumed. Consumption
is recorded on the row, so the same approval cannot drive a second execution.

Single use has to survive concurrency, which the first implementation did not:
`evaluate_grant` only reads `consumed_at`, so two simultaneous redemptions could
both pass that read before either wrote it back. Measured on SQLite, that fired
in 11 of 25 runs and one human approval authorized two executions.

So the read and the take are separate operations with different jobs.
`evaluate_grant` never mutates: it answers "does this grant authorize this exact
request?". `claim` then decides the winner in the database with a conditional
UPDATE, and the caller keeps it in the same transaction as the execution event,
so grant and event commit together and a loser cannot execute.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from sqlalchemy import update
from sqlalchemy.orm import Session

from .. import config, models
from ..runtime_contract import coerce_utc
from ..security import utcnow


@dataclass(frozen=True)
class GrantVerdict:
    granted: bool
    reason: str
    approval: Optional[models.Approval] = None


def approval_for_event(db: Session, event_id: str) -> Optional[models.Approval]:
    return (
        db.query(models.Approval)
        .filter(models.Approval.event_id == event_id)
        .order_by(models.Approval.created_at.asc())
        .first()
    )


def default_expiry():
    return utcnow() + timedelta(seconds=config.APPROVAL_TTL_SECONDS)


def effective_status(approval: models.Approval, now=None) -> str:
    """Where this approval stands right now.

    The stored status only moves when a human decides. Time also decides: a
    pending request nobody answered, or an approval nobody used, is over once
    its expiry passes, whatever the row still says.
    """
    if approval.consumed_at is not None:
        return "consumed"
    status = (approval.status or "").lower()
    if status in {"pending", "approved"}:
        expires_at = coerce_utc(approval.expires_at)
        clock = coerce_utc(now) if now is not None else utcnow()
        if expires_at is not None and clock >= expires_at:
            return "expired"
    return status


# What an agent should do next, given the effective status.
NEXT_STEP = {
    "pending": "wait",
    "approved": "resubmit",
    "denied": "stop",
    "expired": "stop",
    "consumed": "done",
}


def _mismatch(field: str) -> GrantVerdict:
    return GrantVerdict(
        granted=False,
        reason=f"Approval does not authorize this request ({field} differs).",
    )


def evaluate_grant(
    db: Session,
    *,
    agent: models.Agent,
    event: models.Event,
    resource_kind: str,
    action: str,
    scope: str,
    destination: Optional[str],
    payload_hash: str,
    contract_id: Optional[str],
    contract_version: Optional[int],
    now=None,
) -> GrantVerdict:
    """Decide whether an approved approval authorizes this exact request."""
    approval = approval_for_event(db, event.id)
    if approval is None:
        return GrantVerdict(False, "No approval exists for this request.")

    status = (approval.status or "").lower()
    if status == "pending":
        return GrantVerdict(False, "Approval is still pending human review.", approval)
    if status != "approved":
        return GrantVerdict(
            False, f"Approval was not granted (status={status}).", approval
        )

    if approval.consumed_at is not None:
        return GrantVerdict(
            False, "Approval has already authorized an execution.", approval
        )

    clock = coerce_utc(now) if now is not None else utcnow()
    expires_at = coerce_utc(approval.expires_at)
    if expires_at is not None and clock >= expires_at:
        return GrantVerdict(False, "Approval has expired.", approval)

    # Identity
    if approval.organization_id != agent.organization_id:
        return _mismatch("organization")
    if approval.agent_id != agent.id:
        return _mismatch("agent")

    # The execution and request this grant was issued against
    if approval.execution_id is not None and approval.execution_id != event.execution_id:
        return _mismatch("execution")
    if approval.request_id is not None and approval.request_id != event.request_id:
        return _mismatch("request")

    # The action itself
    if approval.resource_kind != resource_kind:
        return _mismatch("resource_kind")
    if approval.action != action:
        return _mismatch("action")
    if approval.scope != scope:
        return _mismatch("scope")
    if (approval.destination or None) != (destination or None):
        return _mismatch("destination")

    # Parameters. A grant for one payload is not a grant for another.
    if approval.param_hash is not None and approval.param_hash != payload_hash:
        return _mismatch("parameters")

    # The contract in force when the human approved must still be the one in
    # force now, at the same version.
    if (approval.contract_id or None) != (contract_id or None):
        return _mismatch("contract")
    if approval.contract_version != contract_version:
        return _mismatch("contract_version")

    return GrantVerdict(True, "Authorized by human approval.", approval)


def claim(db: Session, approval: models.Approval, now=None) -> bool:
    """Atomically take ownership of the grant. True if this caller won it.

    evaluate_grant only *reads* consumed_at, so two concurrent redemptions of
    the same approval could both pass that check before either wrote it back.
    Measured on SQLite, that race fired in 11 of 25 runs: one human approval
    authorized two executions.

    The winner is therefore decided by the database, not by the earlier read.
    This is a conditional UPDATE whose WHERE clause carries the precondition:

        UPDATE approvals SET consumed_at = ... WHERE id = ? AND consumed_at IS NULL

    The first writer takes the row lock and matches one row; the second blocks
    until that transaction commits and then matches zero, so it loses and must
    not execute. Callers keep this in the same transaction as the execution
    event they are about to write, so the grant and the event commit together
    and there is no window where one exists without the other.
    """
    timestamp = coerce_utc(now) if now is not None else utcnow()
    result = db.execute(
        update(models.Approval)
        .where(
            models.Approval.id == approval.id,
            models.Approval.consumed_at.is_(None),
        )
        .values(consumed_at=timestamp)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        return False
    db.refresh(approval)
    return True


def record_consuming_event(
    db: Session, approval: models.Approval, execution_event_id: str
) -> None:
    """Attach the event that used the grant. Same transaction as claim()."""
    approval.consumed_event_id = execution_event_id
    db.flush()


def consume(
    db: Session, approval: models.Approval, execution_event_id: str, now=None
) -> None:
    """Non-atomic burn, kept for callers that already hold exclusivity.

    Prefer claim() + record_consuming_event() on any concurrent path.
    """
    approval.consumed_at = coerce_utc(now) if now is not None else utcnow()
    approval.consumed_event_id = execution_event_id
    db.flush()
