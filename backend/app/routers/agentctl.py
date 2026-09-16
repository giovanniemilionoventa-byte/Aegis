"""Phase 18 — the agent's job channel, served by the enforcement gateway.

Why this lives on the gateway and not the control plane:

    control-plane networks : aegis_public_net
    gateway networks       : aegis_agent_net, aegis_broker_net
    control-plane -> gateway : DNS_BLOCK

The control plane shares no network with the gateway, and the agent shares no
network with the control plane. The only path the deployment boundary leaves
open is agent -> gateway, so that is the path this rides. The operator's request
is recorded in the database the control plane and gateway already share, and the
agent *pulls* it from here. Nothing is ever pushed at an agent.

What this endpoint is NOT:

  * it is not authority. A job names a scenario; it carries no code, no
    credential, no permission and no decision. Every action the agent takes
    afterwards goes through authorize_request exactly like any other.
  * it is not a second identity system. The caller is identified by the same
    agent token as every other agent request, and a job is only ever served to
    the agent it was created for.

So a compromised agent gains nothing here beyond learning that its operator
asked for a verification run.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import update
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..security import get_agent_from_token, utcnow

router = APIRouter(prefix="/agentctl", tags=["agent-control"])


class RunClaim(BaseModel):
    run_id: str
    scenario: str
    execution_id: str


class RunResult(BaseModel):
    status: str
    execution_id: Optional[str] = None
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None


@router.get("/next")
def claim_next_run(
    agent: models.Agent = Depends(get_agent_from_token),
    db: Session = Depends(get_db),
):
    """Claim the oldest pending verification run for *this* agent.

    Scoped by the authenticated agent's own id and organization, so an agent
    cannot discover or steal another agent's run. The claim is an atomic
    conditional UPDATE for the same reason the approval grant is (Phase 17
    finding A-1): two pollers must not both win the same row.
    """
    pending = (
        db.query(models.VerificationRun)
        .filter(
            models.VerificationRun.agent_id == agent.id,
            models.VerificationRun.organization_id == agent.organization_id,
            models.VerificationRun.status == "PENDING",
        )
        .order_by(models.VerificationRun.created_at.asc())
        .first()
    )
    if pending is None:
        return {"run": None}

    claimed = db.execute(
        update(models.VerificationRun)
        .where(
            models.VerificationRun.id == pending.id,
            models.VerificationRun.status == "PENDING",
        )
        .values(status="RUNNING", claimed_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        db.rollback()
        return {"run": None}
    db.commit()
    db.refresh(pending)

    return {
        "run": {
            "run_id": pending.id,
            "scenario": pending.scenario,
            "execution_id": pending.execution_id or pending.id,
        }
    }


@router.post("/runs/{run_id}/result")
def report_result(
    run_id: str,
    body: RunResult,
    agent: models.Agent = Depends(get_agent_from_token),
    db: Session = Depends(get_db),
):
    """Report the outcome of a claimed run.

    The run must belong to the calling agent. The agent's report is a
    *transcript*, not a verdict: the authoritative record of what was allowed
    or denied is the event chain the gateway wrote while authorizing each
    action. The dashboard shows both and never lets this field contradict the
    evidence.
    """
    run = (
        db.query(models.VerificationRun)
        .filter(
            models.VerificationRun.id == run_id,
            models.VerificationRun.agent_id == agent.id,
            models.VerificationRun.organization_id == agent.organization_id,
        )
        .first()
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status in {"COMPLETED", "FAILED"}:
        raise HTTPException(status_code=409, detail="Run already reported")

    status = (body.status or "").upper()
    if status not in {"COMPLETED", "FAILED"}:
        raise HTTPException(status_code=400, detail="status must be COMPLETED or FAILED")

    run.status = status
    run.result = body.result
    run.error = body.error
    if body.execution_id:
        run.execution_id = body.execution_id
    run.finished_at = utcnow()
    db.commit()
    return {"ok": True, "run_id": run.id, "status": run.status}
