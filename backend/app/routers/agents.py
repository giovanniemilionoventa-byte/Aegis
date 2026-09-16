from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from .. import config
from ..security import create_agent_token, get_current_user, hash_token

router = APIRouter(prefix="/agents", tags=["agents"])


def _credential_expiry():
    """When a newly issued agent token stops working. None means never."""
    if config.AGENT_TOKEN_TTL_DAYS <= 0:
        return None
    return datetime.now(timezone.utc) + timedelta(days=config.AGENT_TOKEN_TTL_DAYS)


@router.get("", response_model=list[schemas.AgentOut])
def list_agents(
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return (
        db.query(models.Agent)
        .filter(models.Agent.organization_id == user.organization_id)
        .order_by(models.Agent.created_at.desc())
        .all()
    )


@router.post("", response_model=schemas.AgentCredentialOut)
def create_agent(
    body: schemas.AgentCreate,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    agent = models.Agent(
        organization_id=user.organization_id,
        owner_id=user.id,
        name=body.name,
        provider=body.provider,
        model=body.model,
        description=body.description,
    )
    db.add(agent)
    db.flush()
    token = create_agent_token()
    cred = models.Credential(
        agent_id=agent.id,
        token_hash=hash_token(token),
        token_prefix=token[:16],
        status="active",
        expires_at=_credential_expiry(),
    )
    db.add(cred)
    db.commit()
    db.refresh(agent)
    return schemas.AgentCredentialOut(
        agent=agent,
        token=token,
        token_prefix=cred.token_prefix,
        expires_at=cred.expires_at,
    )


@router.get("/{agent_id}", response_model=schemas.AgentOut)
def get_agent(
    agent_id: str,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
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


@router.post("/{agent_id}/revoke", response_model=schemas.AgentOut)
def revoke_agent(
    agent_id: str,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
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
    now = datetime.now(timezone.utc)
    agent.status = "revoked"
    agent.revoked_at = now
    for cred in agent.credentials:
        cred.status = "revoked"
        cred.revoked_at = now
    db.commit()
    db.refresh(agent)
    return agent


@router.post("/{agent_id}/rotate", response_model=schemas.AgentCredentialOut)
def rotate_credential(
    agent_id: str,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
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
    if agent.status != "active":
        raise HTTPException(status_code=400, detail="Agent is revoked")
    now = datetime.now(timezone.utc)
    for cred in agent.credentials:
        if cred.status == "active":
            cred.status = "rotated"
            cred.revoked_at = now
    token = create_agent_token()
    cred = models.Credential(
        agent_id=agent.id,
        token_hash=hash_token(token),
        token_prefix=token[:16],
        status="active",
        expires_at=_credential_expiry(),
    )
    db.add(cred)
    db.commit()
    db.refresh(agent)
    return schemas.AgentCredentialOut(
        agent=agent,
        token=token,
        token_prefix=cred.token_prefix,
        expires_at=cred.expires_at,
    )


@router.get("/{agent_id}/permissions", response_model=list[schemas.PermissionOut])
def list_permissions(
    agent_id: str,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
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
    return agent.permissions


@router.post("/{agent_id}/permissions", response_model=schemas.PermissionOut)
def add_permission(
    agent_id: str,
    body: schemas.PermissionCreate,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
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
    perm = models.Permission(
        agent_id=agent.id,
        resource_kind=body.resource_kind.lower(),
        action=body.action.upper(),
        scope=body.scope,
        effect=body.effect.lower(),
    )
    db.add(perm)
    db.commit()
    db.refresh(perm)
    return perm


@router.get("/{agent_id}/setup")
def agent_setup(
    agent_id: str,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Everything an external AI agent needs to talk to Aegis. And nothing else.

    Phase 19.1. An operator who has just created an agent has to connect their
    own runtime to it, and until now the dashboard told them nothing about how.
    The gap was filled by reading the source, which is not a product.

    WHAT IS HERE: the enforcement endpoint, the request shape, the canonical
    operations this agent is actually allowed to name, and the tool schema an
    OpenAI-style function-calling model expects.

    WHAT IS NOT, AND WILL NOT BE:
      * the agent's token. It is shown once, at creation or rotation, because
        Aegis stores only its hash and genuinely cannot show it again. An
        endpoint that could re-display it would mean Aegis was keeping it.
      * any Gmail credential, OAuth client, or internal service token. Those
        are not the agent's to hold, and this endpoint is exactly where someone
        would be tempted to leak one for convenience.
      * the broker URL, the tool URL, or any internal address. An agent has one
        address it may use.
    """
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

    from .gateway import TOOL_MAP

    operations = []
    for permission in agent.permissions:
        if permission.effect.lower() != "allow":
            continue
        spec = TOOL_MAP.get(permission.resource_kind)
        if not spec:
            continue
        for wire_name, canonical in spec["operations"].items():
            if canonical == permission.action:
                operations.append(
                    {
                        "canonical": f"{permission.resource_kind}.{canonical}",
                        "path": f"/api/gateway/tools/{permission.resource_kind}/{wire_name}",
                        "scope": permission.scope,
                    }
                )

    return {
        "agent_id": agent.id,
        "agent_name": agent.name,
        "status": agent.status,
        # The single address an agent uses. Not the control plane, which agent
        # credentials are refused from outright, and not the broker or the tool.
        "gateway_base_url_env": "AEGIS_BASE_URL",
        "gateway_path_pattern": "/api/gateway/tools/{tool}/{operation}",
        "auth_header": "X-Agent-Token",
        "request_shape": {
            "scope": "string",
            "payload": "object — the typed parameters for this operation",
            "metadata": {"declared_intent": "string, optional, recorded and untrusted"},
            "execution_id": "string, optional — groups related calls",
            "request_id": "string, optional — idempotency key",
        },
        "operations": sorted(operations, key=lambda row: row["canonical"]),
        "credential": {
            "shown_once": True,
            "note": (
                "Aegis stores only a hash of the agent token and cannot show it "
                "again. Rotate the credential if it was lost."
            ),
        },
        "never_supplied_to_agents": [
            "Google OAuth client id or secret",
            "Gmail refresh or access tokens",
            "the credential broker's address or credentials",
            "internal service tokens",
        ],
    }
