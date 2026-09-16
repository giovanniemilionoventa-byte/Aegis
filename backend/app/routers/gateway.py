from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import config, models, schemas
from ..contract_store import ContractResolutionError, assert_contract_current_for_dispatch
from ..database import get_db
from ..engines.enforcement import authorize_request
from ..services import connector_evidence
from ..services.evidence_verifier import (
    EvidenceIntegrityError,
    reseal_execution_event,
)
from ..remote import dispatch_via_broker
from ..runtime_contract import coerce_utc
from ..security import get_agent_from_token

router = APIRouter(prefix="/gateway", tags=["enforcement-plane"])

# The complete set of protected operations an agent can ask for, and the
# canonical (resource_kind, action) each one is ruled on as.
#
# Phase 19 adds gmail. Note the shape: an operation is a *name in this table*,
# never a method and a URL. There is no gmail entry that takes an endpoint, so
# "call this Gmail API for me" is not expressible at this boundary — the worst
# an agent can do is name one of the five operations, and each of those is
# authorized before anything runs.
#
# The canonical action is what the deterministic engines see. Whatever the
# model called it, whatever language the human used, and whatever the agent
# says it is doing, authorization happens on the action below.
TOOL_MAP = {
    "crm": {
        "kind": "crm",
        "operations": {
            "read": "READ",
            "update": "UPDATE",
            "delete": "DELETE",
        },
    },
    "gmail": {
        "kind": "gmail",
        "operations": {
            "search": "SEARCH",
            "read": "READ",
            "draft": "DRAFT",
            "send": "SEND",
            "delete": "DELETE",
        },
    },
}


def _claim_epoch(value) -> Optional[int]:
    if value is None:
        return None
    return int(coerce_utc(value).timestamp())


def _connector_call_count(tool: str) -> int:
    """How many times the in-process connector has been entered.

    Used to prove, rather than assert, that nothing ran on a non-ALLOW
    decision. Only meaningful in the single-container harness; with a broker
    configured the connector lives in another container and the boundary tests
    cover it instead.
    """
    if tool == "crm":
        from ..protected.crm import protected_crm

        return protected_crm.call_count
    if tool == "gmail":
        from ..protected.gmail import gmail_connector

        return gmail_connector.call_count
    return 0


def _harness_execute(
    tool: str, operation: str, scope: str, payload: dict | None, organization_id: str
) -> dict:
    from ..credentials import broker, contains_any_tool_secret

    cred = broker.issue(tool, organization_id=organization_id)

    if tool == "gmail":
        from ..protected.gmail import GmailConnectorError, gmail_connector
        from ..protected.gmail import (
            authenticate_connector_credential as gmail_authenticate,
        )

        try:
            gmail_authenticate(cred.secret, organization_id)
            result = gmail_connector.execute(
                operation, organization_id=organization_id, payload=payload
            )
        except GmailConnectorError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.code) from exc
    elif tool == "crm":
        from ..protected.crm import protected_crm

        result = protected_crm.execute(
            operation,
            cred.secret,
            scope=scope,
            payload=payload,
            organization_id=organization_id,
        )
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported tool '{tool}'")

    if contains_any_tool_secret(result, organization_id):
        raise HTTPException(status_code=502, detail="Protected tool returned unsafe payload")
    return result


@router.post("/tools/{tool}/{operation}", response_model=schemas.GatewayResponse)
def invoke_tool(
    tool: str,
    operation: str,
    body: schemas.GatewayRequest,
    agent: models.Agent = Depends(get_agent_from_token),
    db: Session = Depends(get_db),
):
    tool_name = tool.lower()
    op = operation.lower()
    spec = TOOL_MAP.get(tool_name)
    if not spec or op not in spec["operations"]:
        raise HTTPException(status_code=400, detail="Unknown protected tool or operation")

    # Phase 19: three separate facts, kept separate from here on.
    #   requested  — what arrived on the wire
    #   canonical  — what the deterministic engines will rule on
    #   intent     — what the agent says it is doing (untrusted, never read)
    requested_operation = f"{tool_name}/{op}"
    canonical_operation = f"{spec['kind']}.{spec['operations'][op]}"
    intent = connector_evidence.declared_intent(body.metadata)

    harness = not config.BROKER_URL
    before = _connector_call_count(tool_name) if harness else 0

    authorize_body = schemas.AuthorizeRequest(
        resource_kind=spec["kind"],
        action=spec["operations"][op],
        scope=body.scope,
        destination=body.destination,
        payload=body.payload,
        metadata=body.metadata,
        execution_id=body.execution_id,
        request_id=body.request_id,
        client_request_id=body.client_request_id,
    )
    try:
        outcome = authorize_request(db, agent, authorize_body)
    except EvidenceIntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Execution evidence integrity failure: {exc.reason}",
        ) from exc
    event = outcome.event
    executed = False
    tool_result = None
    error_code = None

    contract_status = None
    contract_valid_from = None
    contract_expires_at = None
    if event.decision == "ALLOW" and not outcome.replayed and outcome.contract_id is not None:
        try:
            current = assert_contract_current_for_dispatch(
                db,
                agent.organization_id,
                agent.id,
                outcome.contract_id,
                outcome.contract_version,
            )
        except ContractResolutionError:
            current = None
        if current is None:
            event.decision = "BLOCK"
            event.reason = "Runtime contract is no longer valid at execution time."
            reseal_execution_event(db, event)
            db.commit()
        else:
            contract_status = current.status
            contract_valid_from = _claim_epoch(current.valid_from)
            contract_expires_at = _claim_epoch(current.expires_at)

    if event.decision == "ALLOW" and not outcome.replayed:
        try:
            if config.BROKER_URL:
                tool_result = dispatch_via_broker(
                    tool=tool_name,
                    operation=op,
                    scope=event.scope,
                    destination=event.destination,
                    payload=outcome.authorized_payload,
                    org_id=agent.organization_id,
                    agent_id=agent.id,
                    execution_id=event.execution_id,
                    request_id=event.request_id,
                    contract_id=outcome.contract_id,
                    contract_version=outcome.contract_version,
                    contract_status=contract_status,
                    contract_valid_from=contract_valid_from,
                    contract_expires_at=contract_expires_at,
                )
            else:
                tool_result = _harness_execute(
                    tool_name,
                    op,
                    event.scope,
                    outcome.authorized_payload,
                    agent.organization_id,
                )
            executed = True
        except HTTPException as exc:
            # A connector refusal is evidence too: the decision was ALLOW and
            # the protected service still said no. Record it, then re-raise.
            error_code = str(exc.detail)[:120]
            connector_evidence.record(
                db,
                organization_id=agent.organization_id,
                agent_id=agent.id,
                execution_id=event.execution_id,
                request_id=event.request_id,
                event_id=event.id,
                tool=tool_name,
                requested_operation=requested_operation,
                canonical_operation=canonical_operation,
                intent=intent,
                decision=event.decision,
                approval_id=outcome.approval_id,
                approval_granted=outcome.approval_granted,
                executed=False,
                connector_operation=None,
                error_code=error_code,
            )
            raise
        except Exception as exc:
            raise HTTPException(status_code=502, detail="Tool dispatch failed") from exc
    else:
        if harness and _connector_call_count(tool_name) != before:
            # The connector was entered on a decision that was not ALLOW. That
            # would be the whole product failing, so it is a 500, not a log line.
            raise HTTPException(status_code=500, detail="Tool invoked after deny")

    connector_evidence.record(
        db,
        organization_id=agent.organization_id,
        agent_id=agent.id,
        execution_id=event.execution_id,
        request_id=event.request_id,
        event_id=event.id,
        tool=tool_name,
        requested_operation=requested_operation,
        canonical_operation=canonical_operation,
        intent=intent,
        decision=event.decision,
        approval_id=outcome.approval_id,
        approval_granted=outcome.approval_granted,
        executed=executed,
        connector_operation=op if executed else None,
        result=tool_result if executed else None,
    )

    return schemas.GatewayResponse(
        request_id=event.request_id,
        decision=event.decision,
        risk_score=event.risk_score,
        risk_level=event.risk_level,
        reason=event.reason,
        approval_id=outcome.approval_id,
        agent_id=event.agent_id,
        organization_id=event.organization_id,
        execution_id=event.execution_id,
        tool=tool_name,
        operation=op,
        executed=executed,
        result=tool_result,
    )
