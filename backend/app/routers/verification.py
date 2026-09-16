"""Phase 18 — operator-facing runtime verification runs (control plane).

An operator asks for a verification run here; the agent claims it from the
gateway (routers/agentctl.py) and reports back. This split exists because the
control plane and the gateway share no network, which is the deployment
boundary working as intended, not a limitation to route around.

Honesty rules baked into this surface:

  * A run's `status` describes the *request*, not the security outcome. Whether
    each action was allowed or denied is recorded in the evidence chain by the
    gateway while authorizing it, and the dashboard reads it from there.
  * `PENDING` means nothing has happened yet. If no agent is running, a run
    stays PENDING forever and the UI says so rather than implying failure.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..security import get_current_user

router = APIRouter(tags=["verification"])

SCENARIOS = {
    "canonical": (
        "Runs the full authorization story: a permitted read, a forbidden "
        "delete, an update that needs a human, the approved execution, a replay "
        "attempt, a mutated request, and direct-access attempts against the "
        "broker, tool and control plane."
    ),
    "read_only": "A single permitted read, to confirm connectivity and authority.",
}


class VerificationRunRequest(BaseModel):
    scenario: str = "canonical"


class VerificationRunOut(BaseModel):
    id: str
    agent_id: str
    scenario: str
    status: str
    execution_id: Optional[str] = None
    result: Optional[dict] = None
    error: Optional[str] = None
    created_at: Optional[str] = None
    claimed_at: Optional[str] = None
    finished_at: Optional[str] = None


def _serialize(row: models.VerificationRun) -> dict:
    return {
        "id": row.id,
        "agent_id": row.agent_id,
        "scenario": row.scenario,
        "status": row.status,
        "execution_id": row.execution_id,
        "result": row.result,
        "error": row.error,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "claimed_at": row.claimed_at.isoformat() if row.claimed_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


def _agent_or_404(db: Session, user: models.User, agent_id: str) -> models.Agent:
    agent = (
        db.query(models.Agent)
        .filter(
            models.Agent.id == agent_id,
            models.Agent.organization_id == user.organization_id,
        )
        .first()
    )
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.get("/verification/scenarios")
def list_scenarios(_: models.User = Depends(get_current_user)):
    return [{"id": key, "description": value} for key, value in SCENARIOS.items()]


@router.post("/agents/{agent_id}/verification-runs", status_code=201)
def request_run(
    agent_id: str,
    body: VerificationRunRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    agent = _agent_or_404(db, user, agent_id)
    scenario = (body.scenario or "canonical").strip()
    if scenario not in SCENARIOS:
        raise HTTPException(status_code=400, detail=f"Unknown scenario '{scenario}'")
    if agent.status != "active":
        raise HTTPException(
            status_code=409,
            detail="Agent is revoked; it can no longer authenticate to the gateway",
        )

    existing = (
        db.query(models.VerificationRun)
        .filter(
            models.VerificationRun.agent_id == agent.id,
            models.VerificationRun.status.in_(["PENDING", "RUNNING"]),
        )
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"A run is already {existing.status.lower()} for this agent",
        )

    run = models.VerificationRun(
        organization_id=agent.organization_id,
        agent_id=agent.id,
        requested_by=user.id,
        scenario=scenario,
        status="PENDING",
    )
    db.add(run)
    db.flush()
    run.execution_id = f"verify-{run.id}"
    db.commit()
    db.refresh(run)
    return _serialize(run)


@router.get("/verification-runs")
def list_runs(
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
    agent_id: Optional[str] = None,
    limit: int = 50,
):
    query = db.query(models.VerificationRun).filter(
        models.VerificationRun.organization_id == user.organization_id
    )
    if agent_id:
        query = query.filter(models.VerificationRun.agent_id == agent_id)
    rows = (
        query.order_by(models.VerificationRun.created_at.desc())
        .limit(min(limit, 200))
        .all()
    )
    return [_serialize(row) for row in rows]


@router.get("/verification-runs/{run_id}")
def get_run(
    run_id: str,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = (
        db.query(models.VerificationRun)
        .filter(
            models.VerificationRun.id == run_id,
            models.VerificationRun.organization_id == user.organization_id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return _serialize(row)


@router.post("/verification-runs/{run_id}/cancel")
def cancel_run(
    run_id: str,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cancel a run that no agent has claimed.

    A RUNNING run cannot be cancelled from here: the agent is already acting,
    and pretending otherwise would put the dashboard out of step with what the
    runtime is actually doing.
    """
    row = (
        db.query(models.VerificationRun)
        .filter(
            models.VerificationRun.id == run_id,
            models.VerificationRun.organization_id == user.organization_id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if row.status != "PENDING":
        raise HTTPException(
            status_code=409, detail=f"Run is {row.status} and cannot be cancelled"
        )
    row.status = "CANCELLED"
    db.commit()
    db.refresh(row)
    return _serialize(row)
