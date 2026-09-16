"""Recording what was asked, what was authorized, and what actually ran.

Phase 19. See models.ConnectorCall for why this is a separate record from the
sealed Event: the Event is the authority, this is the observation.

The one rule this module exists to enforce is that `connector_operation` is
written from what the dispatch code actually did, not from what the caller said
it wanted. A row where requested_operation and canonical_operation disagree, or
where the decision is BLOCK and connector_operation is NULL, is the evidence
that the divergence was caught.
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from .. import models

INTENT_LIMIT = 500

# Result fields that identify a Gmail object without revealing its content.
_REFERENCE_KEYS = ("message_id", "draft_id", "thread_id")


def declared_intent(metadata: Optional[dict]) -> Optional[str]:
    """Pull the agent's own description of what it is doing, if it gave one.

    Untrusted text. Truncated. Recorded so an auditor can compare it against
    the canonical operation; never consulted by any authorization path.
    """
    if not isinstance(metadata, dict):
        return None
    value = metadata.get("declared_intent") or metadata.get("intent")
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:INTENT_LIMIT]


def resource_reference(result: Optional[dict]) -> Optional[str]:
    """An id for what the connector touched. Never subject, body or address."""
    if not isinstance(result, dict):
        return None
    for key in _REFERENCE_KEYS:
        value = result.get(key)
        if isinstance(value, str) and value:
            return f"{key}={value[:128]}"
    return None


def record(
    db: Session,
    *,
    organization_id: str,
    agent_id: Optional[str],
    execution_id: Optional[str],
    request_id: Optional[str],
    event_id: Optional[str],
    tool: str,
    requested_operation: str,
    canonical_operation: str,
    intent: Optional[str],
    decision: str,
    approval_id: Optional[str],
    approval_granted: bool,
    executed: bool,
    connector_operation: Optional[str],
    result: Optional[dict[str, Any]] = None,
    error_code: Optional[str] = None,
) -> models.ConnectorCall:
    if executed:
        result_status = "ok"
    elif error_code:
        result_status = "error"
    else:
        result_status = "not_executed"

    row = models.ConnectorCall(
        organization_id=organization_id,
        agent_id=agent_id,
        execution_id=execution_id,
        request_id=request_id,
        event_id=event_id,
        tool=tool,
        requested_operation=requested_operation,
        canonical_operation=canonical_operation,
        declared_intent=intent,
        decision=decision,
        approval_id=approval_id,
        approval_granted=bool(approval_granted),
        executed=bool(executed),
        # NULL unless something ran. This is the field that distinguishes
        # "Aegis decided" from "the protected service was touched".
        connector_operation=connector_operation if executed else None,
        result_status=result_status,
        resource_ref=resource_reference(result) if executed else None,
        error_code=error_code,
    )
    db.add(row)
    db.commit()
    return row
