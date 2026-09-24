import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import relationship

from .database import Base


def utcnow():
    return datetime.now(timezone.utc)


def new_id():
    return str(uuid.uuid4())


class Organization(Base):
    __tablename__ = "organizations"

    id = Column(String, primary_key=True, default=new_id)
    name = Column(String, nullable=False)
    slug = Column(String, unique=True, nullable=False)
    # Phase 20: comma-separated mail domains that count as "inside" this
    # organization. Empty means nothing is internal, so every recipient is
    # external and a send needs a human. Defaults to the registering admin's domain.
    internal_domains = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    users = relationship("User", back_populates="organization")
    agents = relationship("Agent", back_populates="organization")
    devices = relationship("Device", back_populates="organization")
    resources = relationship("Resource", back_populates="organization")
    policies = relationship("Policy", back_populates="organization")
    events = relationship("Event", back_populates="organization")
    alerts = relationship("Alert", back_populates="organization")
    approvals = relationship("Approval", back_populates="organization")
    executions = relationship("Execution", back_populates="organization")
    behavior_patterns = relationship("BehaviorPattern", back_populates="organization")
    behavior_signals = relationship("BehaviorSignal", back_populates="organization")
    runtime_contracts = relationship("RuntimeContract", back_populates="organization")


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=new_id)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    full_name = Column(String, nullable=False)
    role = Column(String, nullable=False, default="admin")
    created_at = Column(DateTime, default=utcnow)

    organization = relationship("Organization", back_populates="users")
    owned_agents = relationship("Agent", back_populates="owner")


class Device(Base):
    __tablename__ = "devices"

    id = Column(String, primary_key=True, default=new_id)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    hostname = Column(String, nullable=False)
    platform = Column(String, nullable=False, default="linux")
    status = Column(String, nullable=False, default="online")
    last_seen = Column(DateTime, default=utcnow)
    created_at = Column(DateTime, default=utcnow)

    organization = relationship("Organization", back_populates="devices")


class Agent(Base):
    __tablename__ = "agents"

    id = Column(String, primary_key=True, default=new_id)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    owner_id = Column(String, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    provider = Column(String, nullable=False, default="demo")
    model = Column(String, nullable=False, default="local-demo")
    status = Column(String, nullable=False, default="active")
    description = Column(Text, default="")
    created_at = Column(DateTime, default=utcnow)
    revoked_at = Column(DateTime, nullable=True)
    # Phase 20: when this agent last presented a valid credential. It is what
    # lets the dashboard say "waiting for the first call" and then "connected".
    last_seen_at = Column(DateTime, nullable=True)

    organization = relationship("Organization", back_populates="agents")
    owner = relationship("User", back_populates="owned_agents")
    credentials = relationship("Credential", back_populates="agent")
    permissions = relationship("Permission", back_populates="agent")
    runtime_contracts = relationship("RuntimeContract", back_populates="agent")


class Credential(Base):
    __tablename__ = "credentials"

    id = Column(String, primary_key=True, default=new_id)
    agent_id = Column(String, ForeignKey("agents.id"), nullable=False)
    token_hash = Column(String, nullable=False, unique=True, index=True)
    token_prefix = Column(String, nullable=False)
    status = Column(String, nullable=False, default="active")
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    revoked_at = Column(DateTime, nullable=True)

    agent = relationship("Agent", back_populates="credentials")


class Resource(Base):
    __tablename__ = "resources"

    id = Column(String, primary_key=True, default=new_id)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    kind = Column(String, nullable=False)
    name = Column(String, nullable=False)
    identifier = Column(String, nullable=False)
    sensitivity = Column(String, nullable=False, default="internal")
    created_at = Column(DateTime, default=utcnow)

    organization = relationship("Organization", back_populates="resources")


class Permission(Base):
    __tablename__ = "permissions"

    id = Column(String, primary_key=True, default=new_id)
    agent_id = Column(String, ForeignKey("agents.id"), nullable=False)
    resource_kind = Column(String, nullable=False)
    action = Column(String, nullable=False)
    scope = Column(String, nullable=False, default="*")
    effect = Column(String, nullable=False, default="allow")
    created_at = Column(DateTime, default=utcnow)

    agent = relationship("Agent", back_populates="permissions")

    __table_args__ = (
        UniqueConstraint(
            "agent_id", "resource_kind", "action", "scope", name="uq_agent_perm"
        ),
    )


class Policy(Base):
    __tablename__ = "policies"

    id = Column(String, primary_key=True, default=new_id)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    name = Column(String, nullable=False)
    description = Column(Text, default="")
    resource_kind = Column(String, nullable=False)
    action = Column(String, nullable=False)
    scope_pattern = Column(String, nullable=False, default="*")
    destination_pattern = Column(String, nullable=True)
    decision = Column(String, nullable=False)
    priority = Column(Integer, nullable=False, default=100)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=utcnow)

    organization = relationship("Organization", back_populates="policies")


class Execution(Base):
    __tablename__ = "executions"

    id = Column(String, primary_key=True, default=new_id)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    agent_id = Column(String, ForeignKey("agents.id"), nullable=False)
    evidence_chain_tip = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    organization = relationship("Organization", back_populates="executions")
    events = relationship("Event", back_populates="execution")
    behavior_signals = relationship("BehaviorSignal", back_populates="execution")


class Event(Base):
    __tablename__ = "events"

    id = Column(String, primary_key=True, default=new_id)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    agent_id = Column(String, ForeignKey("agents.id"), nullable=True)
    execution_id = Column(String, ForeignKey("executions.id"), nullable=True, index=True)
    seq = Column(Integer, nullable=False, default=0)
    resource_kind = Column(String, nullable=False)
    action = Column(String, nullable=False)
    scope = Column(String, nullable=False)
    destination = Column(String, nullable=True)
    payload_hash = Column(String, nullable=True)
    evidence_hash = Column(String, nullable=True)
    previous_evidence_hash = Column(String, nullable=True)
    decision = Column(String, nullable=False)
    risk_score = Column(Float, nullable=False, default=0.0)
    risk_level = Column(String, nullable=False, default="low")
    reason = Column(Text, default="")
    request_id = Column(String, nullable=False, index=True)
    created_at = Column(DateTime, default=utcnow)

    organization = relationship("Organization", back_populates="events")
    execution = relationship("Execution", back_populates="events")
    behavior_signals = relationship("BehaviorSignal", back_populates="event")


class BehaviorPattern(Base):
    __tablename__ = "behavior_patterns"

    id = Column(String, primary_key=True, default=new_id)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=True, index=True)
    name = Column(String, nullable=False)
    description = Column(Text, default="")
    type = Column(String, nullable=False)
    severity = Column(String, nullable=False, default="medium")
    definition = Column(JSON, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    organization = relationship("Organization", back_populates="behavior_patterns")
    signals = relationship("BehaviorSignal", back_populates="pattern")


class BehaviorSignal(Base):
    __tablename__ = "behavior_signals"

    id = Column(String, primary_key=True, default=new_id)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    agent_id = Column(String, ForeignKey("agents.id"), nullable=False)
    execution_id = Column(String, ForeignKey("executions.id"), nullable=True)
    event_id = Column(String, ForeignKey("events.id"), nullable=True)
    pattern_id = Column(String, ForeignKey("behavior_patterns.id"), nullable=False)
    severity = Column(String, nullable=False, default="medium")
    title = Column(String, nullable=False)
    message = Column(Text, default="")
    created_at = Column(DateTime, default=utcnow)

    organization = relationship("Organization", back_populates="behavior_signals")
    execution = relationship("Execution", back_populates="behavior_signals")
    event = relationship("Event", back_populates="behavior_signals")
    pattern = relationship("BehaviorPattern", back_populates="signals")


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(String, primary_key=True, default=new_id)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    event_id = Column(String, ForeignKey("events.id"), nullable=True)
    severity = Column(String, nullable=False, default="medium")
    title = Column(String, nullable=False)
    message = Column(Text, default="")
    status = Column(String, nullable=False, default="open")
    created_at = Column(DateTime, default=utcnow)

    organization = relationship("Organization", back_populates="alerts")


class Approval(Base):
    __tablename__ = "approvals"

    id = Column(String, primary_key=True, default=new_id)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    agent_id = Column(String, ForeignKey("agents.id"), nullable=False)
    event_id = Column(String, ForeignKey("events.id"), nullable=True)
    resource_kind = Column(String, nullable=False)
    action = Column(String, nullable=False)
    scope = Column(String, nullable=False)
    destination = Column(String, nullable=True)
    status = Column(String, nullable=False, default="pending")
    reason = Column(Text, default="")
    reviewed_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=utcnow)
    reviewed_at = Column(DateTime, nullable=True)

    # Phase 17 — an approval authorizes one specific request, once.
    #
    # Before this, approving only set status='approved' and nothing read it: an
    # approved action could never execute. Now the grant carries the full
    # binding of the request it authorizes, so a human approving "send this
    # email to this address with this body" cannot be turned into authority for
    # a different destination, different parameters, a different execution, or a
    # second run.
    execution_id = Column(String, ForeignKey("executions.id"), nullable=True, index=True)
    request_id = Column(String, nullable=True, index=True)
    contract_id = Column(String, nullable=True)
    contract_version = Column(Integer, nullable=True)
    param_hash = Column(String, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    consumed_at = Column(DateTime, nullable=True)
    consumed_event_id = Column(String, nullable=True)

    # Phase 20 -- what the reviewer is shown. Sealed at rest (secretbox, bound to
    # this organization and approval), never part of the evidence chain, and
    # deleted once preview_purge_at passes: message content is kept only as long
    # as a person may still need it to decide.
    preview_sealed = Column(Text, nullable=True)
    preview_purge_at = Column(DateTime, nullable=True)

    organization = relationship("Organization", back_populates="approvals")


class RuntimeContract(Base):
    __tablename__ = "runtime_contracts"

    id = Column(String, primary_key=True, default=new_id)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)
    agent_id = Column(String, ForeignKey("agents.id"), nullable=False, index=True)
    contract_id = Column(String, nullable=False, index=True)
    version = Column(Integer, nullable=False)
    status = Column(String, nullable=False, default="DRAFT")
    purpose = Column(Text, default="")
    capabilities = Column(JSON, nullable=False)
    resources = Column(JSON, nullable=False)
    constraints = Column(JSON, nullable=False)
    data_constraints = Column(JSON, nullable=False)
    workflow = Column(JSON, nullable=True)
    approval_rules = Column(JSON, nullable=False)
    valid_from = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    integrity = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    organization = relationship("Organization", back_populates="runtime_contracts")
    agent = relationship("Agent", back_populates="runtime_contracts")

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "agent_id",
            "contract_id",
            "version",
            name="uq_runtime_contract_identity",
        ),
        Index(
            "uq_runtime_contract_one_active_per_agent",
            "organization_id",
            "agent_id",
            unique=True,
            sqlite_where=text("status = 'ACTIVE'"),
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )


class VerificationRun(Base):
    """Phase 18 — an operator's request for a runtime verification run.

    The control plane cannot reach the enforcement gateway (they share no
    network, by design), so the dashboard cannot push work at an agent. It
    records the request here instead, and the agent *claims* it from the
    gateway over agent_net -- the one path the deployment boundary leaves open.

    This row carries no authority. It names a scenario; it does not carry code,
    credentials or permissions. Every action the agent then takes is authorized
    exactly as any other agent action would be.
    """

    __tablename__ = "verification_runs"

    id = Column(String, primary_key=True, default=new_id)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)
    agent_id = Column(String, ForeignKey("agents.id"), nullable=False, index=True)
    requested_by = Column(String, ForeignKey("users.id"), nullable=True)
    scenario = Column(String, nullable=False, default="canonical")
    status = Column(String, nullable=False, default="PENDING", index=True)
    execution_id = Column(String, nullable=True)
    result = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    claimed_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)


class ConnectorCall(Base):
    """Phase 19 — what the agent said, what Aegis decided, what actually ran.

    The execution evidence chain already records the authorization decision for
    a typed request. It does not record two things Phase 19 needs to be able to
    show separately:

      * what the agent *claimed* it was doing, in its own words, and
      * what the connector *actually* did afterwards.

    Those are different facts, and conflating them is exactly the failure mode
    the phase is about. An agent that says "I'll just read this email" and then
    calls gmail.delete produces one row here with declared_intent describing a
    read and canonical_operation naming the delete. Aegis rules on the second.

    DECLARED_INTENT IS UNTRUSTED DATA. It is agent-supplied text, stored so an
    auditor can see the divergence, truncated, and never read by any
    authorization path. Nothing in the enforcement code branches on it.

    This table is deliberately outside the HMAC evidence chain: it is an
    observability record, not an authority record, and appending to it must
    never be able to invalidate the chain. The authoritative decision remains
    the sealed Event.

    NO MESSAGE CONTENT. resource_ref holds a Gmail message or draft id, never a
    subject, body or address.
    """

    __tablename__ = "connector_calls"

    id = Column(String, primary_key=True, default=new_id)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)
    agent_id = Column(String, ForeignKey("agents.id"), nullable=True, index=True)
    execution_id = Column(String, nullable=True, index=True)
    request_id = Column(String, nullable=True, index=True)
    event_id = Column(String, nullable=True)

    tool = Column(String, nullable=False)
    # What the client asked for, as it arrived: "gmail/send".
    requested_operation = Column(String, nullable=False)
    # The canonical typed operation Aegis actually ruled on: "gmail.SEND".
    canonical_operation = Column(String, nullable=False)
    # Agent-supplied, untrusted, never authoritative.
    declared_intent = Column(Text, nullable=True)

    decision = Column(String, nullable=False)
    approval_id = Column(String, nullable=True)
    approval_granted = Column(Boolean, nullable=False, default=False)

    executed = Column(Boolean, nullable=False, default=False)
    # The operation the connector ran, or NULL when nothing ran.
    connector_operation = Column(String, nullable=True)
    result_status = Column(String, nullable=False, default="not_executed")
    resource_ref = Column(String, nullable=True)
    error_code = Column(String, nullable=True)

    created_at = Column(DateTime, default=utcnow, index=True)



class GmailGrant(Base):
    """Which agents may use the tenant's connected mailbox.

    Phase 19.1. Before this, connecting Gmail made the mailbox usable by every
    agent in the tenant that happened to hold a gmail permission. That is a
    reasonable default and a bad one: connecting a mailbox is a single act by
    one person, and it silently widened the authority of agents they may never
    have thought about.

    So the connection stays tenant-scoped -- one mailbox, one credential, no
    duplicated refresh tokens -- and the *use* of it is granted per agent. An
    agent with gmail permissions, a contract that allows gmail, and no grant is
    refused. The gateway checks this before dispatch and the refusal is sealed
    into the evidence chain like any other.

    WHAT THIS IS NOT. It is not a second policy engine: it decides nothing about
    which operations are allowed, only whether this agent may touch this
    tenant's mailbox at all. Permissions, policy and the runtime contract still
    decide everything else, and a grant cannot widen any of them.

    TENANT BINDING. organization_id is stored and every lookup filters on it, so
    a grant row can only ever connect an agent to its own tenant's mailbox.
    There is no connection_id a caller could supply: the connection is resolved
    from the authenticated agent's organization, never from the request.
    """

    __tablename__ = "gmail_grants"

    id = Column(String, primary_key=True, default=new_id)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)
    agent_id = Column(String, ForeignKey("agents.id"), nullable=False, index=True)
    # Snapshot of the mailbox at grant time, for the operator's benefit. Not
    # authority: the live connection is resolved from the store at call time.
    google_email = Column(String, nullable=True)
    status = Column(String, nullable=False, default="active", index=True)
    granted_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=utcnow)
    revoked_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index(
            "uq_gmail_grant_one_active_per_agent",
            "organization_id",
            "agent_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )
