from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..engines.enforcement import authorize_request
from ..security import get_agent_from_token
from ..services import approval_grant, approval_notify
from ..services.evidence_verifier import EvidenceIntegrityError

router = APIRouter(tags=["enforcement"])


def _response(outcome) -> schemas.AuthorizeResponse:
    event = outcome.event
    return schemas.AuthorizeResponse(
        request_id=event.request_id,
        # A replayed request whose approval was denied or expired is told BLOCK
        # here, while the sealed event keeps saying what happened at the time.
        decision=outcome.final_decision or event.decision,
        risk_score=event.risk_score,
        risk_level=event.risk_level,
        reason=outcome.final_reason or event.reason,
        approval_id=outcome.approval_id,
        agent_id=event.agent_id,
        organization_id=event.organization_id,
    )


@router.post("/authorize", response_model=schemas.AuthorizeResponse)
def authorize(
    body: schemas.AuthorizeRequest,
    agent: models.Agent = Depends(get_agent_from_token),
    db: Session = Depends(get_db),
):
    try:
        outcome = authorize_request(db, agent, body)
    except EvidenceIntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Execution evidence integrity failure: {exc.reason}",
        ) from exc
    if (
        outcome.approval_id
        and not outcome.replayed
        and outcome.event.decision == "APPROVAL"
    ):
        # Tell the reviewers. Best effort and in the background: it can never
        # slow down or fail the agent's request.
        approval_notify.enqueue(outcome.approval_id)
    return _response(outcome)


@router.get("/authorize/approvals/{approval_id}", response_model=schemas.ApprovalStatusOut)
def approval_status(
    approval_id: str,
    agent: models.Agent = Depends(get_agent_from_token),
    db: Session = Depends(get_db),
):
    """Where this agent's own approval stands, and what to do next.

    Without this an agent can only find out by re-submitting its request and
    reading the answer. It is scoped to the calling agent: another agent's
    approval, or another organization's, is simply not found.
    """
    approval = (
        db.query(models.Approval)
        .filter(
            models.Approval.id == approval_id,
            models.Approval.agent_id == agent.id,
            models.Approval.organization_id == agent.organization_id,
        )
        .first()
    )
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    state = approval_grant.effective_status(approval)
    return schemas.ApprovalStatusOut(
        approval_id=approval.id,
        status=state,
        next=approval_grant.NEXT_STEP.get(state, "stop"),
        expires_at=approval.expires_at,
        decided_at=approval.reviewed_at,
    )
