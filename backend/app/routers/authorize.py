from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..engines.enforcement import authorize_request
from ..security import get_agent_from_token
from ..services.evidence_verifier import EvidenceIntegrityError

router = APIRouter(tags=["enforcement"])


def _response(event: models.Event, approval_id: str | None) -> schemas.AuthorizeResponse:
    return schemas.AuthorizeResponse(
        request_id=event.request_id,
        decision=event.decision,
        risk_score=event.risk_score,
        risk_level=event.risk_level,
        reason=event.reason,
        approval_id=approval_id,
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
    return _response(outcome.event, outcome.approval_id)
