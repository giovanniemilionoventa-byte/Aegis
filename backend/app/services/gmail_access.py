"""May this agent use its tenant's mailbox?

Phase 19.1. One question, one place to answer it, so the gateway and the
control plane cannot drift apart on it.

The answer is derived entirely from server-side state: the authenticated
agent's own organization and a grant row. Nothing a caller sends is consulted —
there is deliberately no connection_id parameter anywhere in this module,
because a caller-supplied connection identifier is exactly the field a
cross-tenant attack would use.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from .. import gmail_store, models
from ..security import utcnow


@dataclass(frozen=True)
class GmailAccess:
    allowed: bool
    reason: str
    connected: bool
    granted: bool
    google_email: Optional[str] = None


def active_grant(
    db: Session, organization_id: str, agent_id: str
) -> Optional[models.GmailGrant]:
    """The agent's grant, scoped to its own tenant. No cross-tenant lookup."""
    if not organization_id or not agent_id:
        return None
    return (
        db.query(models.GmailGrant)
        .filter(
            models.GmailGrant.organization_id == organization_id,
            models.GmailGrant.agent_id == agent_id,
            models.GmailGrant.status == "active",
        )
        .first()
    )


def evaluate(db: Session, agent: models.Agent) -> GmailAccess:
    """Fail closed. Both the connection and the grant must exist."""
    try:
        connection = gmail_store.get_connection(agent.organization_id)
    except gmail_store.GmailStoreError:
        connection = None
    connected = connection is not None and connection.status == "connected"

    grant = active_grant(db, agent.organization_id, agent.id)
    granted = grant is not None

    if not connected:
        return GmailAccess(
            allowed=False,
            reason=(
                "No Gmail mailbox is connected for this organization. An "
                "operator must connect one before any agent can use it."
            ),
            connected=False,
            granted=granted,
        )
    if not granted:
        return GmailAccess(
            allowed=False,
            reason=(
                "This agent has not been granted access to the connected "
                "mailbox. Permissions and a contract are not enough: an "
                "operator grants mailbox access per agent."
            ),
            connected=True,
            granted=False,
            google_email=connection.google_email if connection else None,
        )
    return GmailAccess(
        allowed=True,
        reason="Agent is granted access to the connected mailbox.",
        connected=True,
        granted=True,
        google_email=connection.google_email if connection else None,
    )


def grant(
    db: Session,
    *,
    organization_id: str,
    agent_id: str,
    granted_by: Optional[str],
    google_email: Optional[str],
) -> models.GmailGrant:
    existing = active_grant(db, organization_id, agent_id)
    if existing is not None:
        existing.google_email = google_email
        db.commit()
        db.refresh(existing)
        return existing
    row = models.GmailGrant(
        organization_id=organization_id,
        agent_id=agent_id,
        google_email=google_email,
        status="active",
        granted_by=granted_by,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def revoke(db: Session, *, organization_id: str, agent_id: str) -> bool:
    row = active_grant(db, organization_id, agent_id)
    if row is None:
        return False
    row.status = "revoked"
    row.revoked_at = utcnow()
    db.commit()
    return True


def revoke_all_for_organization(db: Session, organization_id: str) -> int:
    """Called when a mailbox is disconnected.

    Leaving grants behind would mean that reconnecting a mailbox silently
    re-armed every agent that had access to the previous one. Disconnect means
    disconnect.
    """
    rows = (
        db.query(models.GmailGrant)
        .filter(
            models.GmailGrant.organization_id == organization_id,
            models.GmailGrant.status == "active",
        )
        .all()
    )
    now = utcnow()
    for row in rows:
        row.status = "revoked"
        row.revoked_at = now
    db.commit()
    return len(rows)


def agents_with_access(db: Session, organization_id: str) -> list[models.GmailGrant]:
    return (
        db.query(models.GmailGrant)
        .filter(
            models.GmailGrant.organization_id == organization_id,
            models.GmailGrant.status == "active",
        )
        .order_by(models.GmailGrant.created_at.asc())
        .all()
    )
