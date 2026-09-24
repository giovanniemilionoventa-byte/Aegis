from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import update
from sqlalchemy.orm import Session

from .. import config, models, schemas
from ..database import get_db
from ..ratelimit import enforce
from ..runtime_contract import coerce_utc
from ..security import get_current_user, utcnow
from ..services import approval_grant, approval_notify, approval_preview

router = APIRouter(prefix="/approvals", tags=["approvals"])


def _agent_names(db: Session, organization_id: str, agent_ids: set) -> dict:
    if not agent_ids:
        return {}
    rows = (
        db.query(models.Agent.id, models.Agent.name)
        .filter(
            models.Agent.organization_id == organization_id,
            models.Agent.id.in_(agent_ids),
        )
        .all()
    )
    return {agent_id: name for agent_id, name in rows}


def _out(approval: models.Approval, names: dict, now) -> schemas.ApprovalOut:
    out = schemas.ApprovalOut.model_validate(approval)
    out.effective_status = approval_grant.effective_status(approval, now)
    out.agent_name = names.get(approval.agent_id)
    out.preview = approval_preview.open_for(approval)
    return out


def claim_decision(
    db: Session, approval: models.Approval, new_status: str, reviewer_id: str
) -> bool:
    """Take the decision atomically. True only for the one caller that wins.

    Two reviewers (or a reviewer and a link) could both read "pending" and both
    write an answer. The condition lives in the UPDATE, so the database picks
    the winner and the loser gets False. The same statement starts the clock on
    the preview's retention.
    """
    now = utcnow()
    result = db.execute(
        update(models.Approval)
        .where(models.Approval.id == approval.id, models.Approval.status == "pending")
        .values(
            status=new_status,
            reviewed_by=reviewer_id,
            reviewed_at=now,
            preview_purge_at=now
            + timedelta(days=config.APPROVAL_PREVIEW_RETENTION_DAYS),
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        return False
    return True


@router.get("", response_model=list[schemas.ApprovalOut])
def list_approvals(
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
    status_filter: str | None = None,
):
    approval_preview.purge_expired(db)
    q = db.query(models.Approval).filter(
        models.Approval.organization_id == user.organization_id
    )
    if status_filter:
        q = q.filter(models.Approval.status == status_filter)
    rows = q.order_by(models.Approval.created_at.desc()).limit(100).all()
    names = _agent_names(db, user.organization_id, {row.agent_id for row in rows})
    now = utcnow()
    return [_out(row, names, now) for row in rows]


@router.post("/{approval_id}/decide", response_model=schemas.ApprovalOut)
def decide(
    approval_id: str,
    body: schemas.ApprovalDecision,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    approval = (
        db.query(models.Approval)
        .filter(
            models.Approval.id == approval_id,
            models.Approval.organization_id == user.organization_id,
        )
        .first()
    )
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")
    if approval.status != "pending":
        raise HTTPException(status_code=400, detail="Already reviewed")
    decision = body.decision.upper()
    if decision not in {"ALLOW", "BLOCK"}:
        raise HTTPException(status_code=400, detail="Decision must be ALLOW or BLOCK")
    # Phase 17: approving an expired request would mint a grant that can never
    # be used, which reads to the operator as if the action had been authorized.
    # Denying an expired request stays available, since that needs no authority.
    if decision == "ALLOW":
        expires_at = coerce_utc(approval.expires_at)
        if expires_at is not None and utcnow() >= expires_at:
            raise HTTPException(
                status_code=409,
                detail="Approval request has expired; the agent must resubmit",
            )
    new_status = "approved" if decision == "ALLOW" else "denied"
    if not claim_decision(db, approval, new_status, user.id):
        raise HTTPException(status_code=409, detail="Already reviewed")
    db.commit()
    db.refresh(approval)
    names = _agent_names(db, user.organization_id, {approval.agent_id})
    return _out(approval, names, utcnow())


# ---------------------------------------------------------------------------
# One-tap links (Phase 20)
#
# No login: the signed link in the notification email is the credential. It
# names one approval, one organization and one admin, and dies with the request.
# ---------------------------------------------------------------------------


def _link_target(db: Session, token: str):
    claims = approval_notify.verify_link_token(token)
    if claims is None:
        raise HTTPException(status_code=404, detail="Link not found or expired")
    approval = (
        db.query(models.Approval)
        .filter(
            models.Approval.id == claims.approval_id,
            models.Approval.organization_id == claims.organization_id,
        )
        .first()
    )
    reviewer = (
        db.query(models.User)
        .filter(
            models.User.id == claims.user_id,
            models.User.organization_id == claims.organization_id,
        )
        .first()
    )
    if approval is None or reviewer is None:
        raise HTTPException(status_code=404, detail="Link not found or expired")
    return approval, reviewer


def _link_out(db: Session, approval: models.Approval) -> schemas.ApprovalLinkOut:
    names = _agent_names(db, approval.organization_id, {approval.agent_id})
    out = _out(approval, names, utcnow())
    return schemas.ApprovalLinkOut(
        id=out.id,
        agent_name=out.agent_name,
        resource_kind=out.resource_kind,
        action=out.action,
        scope=out.scope,
        destination=out.destination,
        reason=out.reason,
        status=out.status,
        effective_status=out.effective_status,
        created_at=out.created_at,
        expires_at=out.expires_at,
        preview=out.preview,
    )


@router.get("/link/{token}", response_model=schemas.ApprovalLinkOut)
def link_summary(token: str, request: Request, db: Session = Depends(get_db)):
    enforce(request, "approval-link")
    approval_preview.purge_expired(db)
    approval, _ = _link_target(db, token)
    return _link_out(db, approval)


@router.post("/link/{token}/decide", response_model=schemas.ApprovalLinkOut)
def link_decide(
    token: str,
    body: schemas.ApprovalDecision,
    request: Request,
    db: Session = Depends(get_db),
):
    enforce(request, "approval-link")
    approval, reviewer = _link_target(db, token)
    decision = body.decision.upper()
    if decision not in {"ALLOW", "BLOCK"}:
        raise HTTPException(status_code=400, detail="Decision must be ALLOW or BLOCK")
    if approval.status != "pending":
        raise HTTPException(status_code=409, detail="Already reviewed")
    if decision == "ALLOW":
        expires_at = coerce_utc(approval.expires_at)
        if expires_at is not None and utcnow() >= expires_at:
            raise HTTPException(
                status_code=409,
                detail="Approval request has expired; the agent must resubmit",
            )
    new_status = "approved" if decision == "ALLOW" else "denied"
    if not claim_decision(db, approval, new_status, reviewer.id):
        raise HTTPException(status_code=409, detail="Already reviewed")
    db.commit()
    db.refresh(approval)
    return _link_out(db, approval)
