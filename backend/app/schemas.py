from datetime import datetime, timezone
from typing import Annotated, Optional

from pydantic import BaseModel, Field, PlainSerializer


def _utc_z(value: datetime) -> str:
    """ISO 8601 with an explicit UTC marker.

    The database stores naive UTC, and a naive string ("2026-09-19T16:00:00")
    is read by a browser as *local* time, so an approval created a minute ago
    showed as 121 minutes old for anyone off UTC. Every datetime the API returns
    now says "Z".
    """
    aware = (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )
    return aware.isoformat().replace("+00:00", "Z")


# Used on output models only. Input documents keep plain datetime so that what a
# client sends, and what the contract validator parses, is unchanged.
UTCDatetime = Annotated[datetime, PlainSerializer(_utc_z, return_type=str, when_used="json")]


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    email: str
    password: str


# A deliberately simple shape check (something@domain.tld, no spaces, one @).
# Deliverability is proven by the first email, not by a regex.
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class RegisterRequest(BaseModel):
    organization_name: str = Field(min_length=1, max_length=120)
    full_name: str = Field(min_length=1, max_length=120)
    email: str = Field(max_length=254, pattern=EMAIL_PATTERN)
    password: str = Field(min_length=1, max_length=256)
    invite_code: Optional[str] = Field(default=None, max_length=200)


class UserOut(BaseModel):
    id: str
    email: str
    full_name: str
    role: str
    organization_id: str

    class Config:
        from_attributes = True


class OrganizationOut(BaseModel):
    id: str
    name: str
    slug: str
    created_at: UTCDatetime

    class Config:
        from_attributes = True


class MeResponse(BaseModel):
    user: UserOut
    organization: OrganizationOut


class AgentCreate(BaseModel):
    name: str
    provider: str = "demo"
    model: str = "local-demo"
    description: str = ""
    # "recommended" gives the agent a sensible starting authority (read freely,
    # send and update through a human, never delete, move money or export).
    # Absent, the agent starts with nothing, exactly as before.
    preset: Optional[str] = None


class AgentOut(BaseModel):
    id: str
    name: str
    provider: str
    model: str
    status: str
    description: str
    owner_id: str
    organization_id: str
    created_at: UTCDatetime
    revoked_at: Optional[UTCDatetime] = None
    last_seen_at: Optional[UTCDatetime] = None

    class Config:
        from_attributes = True


class AgentCredentialOut(BaseModel):
    agent: AgentOut
    token: str
    token_prefix: str
    expires_at: Optional[UTCDatetime] = None


class PermissionCreate(BaseModel):
    resource_kind: str
    action: str
    scope: str = "*"
    effect: str = "allow"


class PermissionOut(BaseModel):
    id: str
    agent_id: str
    resource_kind: str
    action: str
    scope: str
    effect: str

    class Config:
        from_attributes = True


class PolicyCreate(BaseModel):
    name: str
    description: str = ""
    resource_kind: str
    action: str
    scope_pattern: str = "*"
    destination_pattern: Optional[str] = None
    decision: str
    priority: int = 100
    enabled: bool = True


class PolicyOut(BaseModel):
    id: str
    organization_id: str
    name: str
    description: str
    resource_kind: str
    action: str
    scope_pattern: str
    destination_pattern: Optional[str] = None
    decision: str
    priority: int
    enabled: bool
    created_at: UTCDatetime

    class Config:
        from_attributes = True


class ResourceCreate(BaseModel):
    kind: str
    name: str
    identifier: str
    sensitivity: str = "internal"


class ResourceOut(BaseModel):
    id: str
    organization_id: str
    kind: str
    name: str
    identifier: str
    sensitivity: str

    class Config:
        from_attributes = True


class DeviceOut(BaseModel):
    id: str
    hostname: str
    platform: str
    status: str
    last_seen: UTCDatetime

    class Config:
        from_attributes = True


class AuthorizeRequest(BaseModel):
    resource_kind: str = Field(..., examples=["email", "files", "crm", "payments"])
    action: str = Field(..., examples=["READ", "SEND", "EXPORT", "DELETE", "TRANSFER"])
    scope: str = Field(..., examples=["customers", "/Sales", "external"])
    destination: Optional[str] = None
    payload: Optional[dict] = None
    metadata: Optional[dict] = None
    execution_id: Optional[str] = None
    request_id: Optional[str] = None
    client_request_id: Optional[str] = None


class AuthorizeResponse(BaseModel):
    request_id: str
    decision: str
    risk_score: float
    risk_level: str
    reason: str
    approval_id: Optional[str] = None
    agent_id: str
    organization_id: str


class GatewayRequest(BaseModel):
    scope: str = "customers"
    destination: Optional[str] = None
    payload: Optional[dict] = None
    metadata: Optional[dict] = None
    execution_id: Optional[str] = None
    request_id: Optional[str] = None
    client_request_id: Optional[str] = None


class GatewayResponse(BaseModel):
    request_id: str
    decision: str
    risk_score: float
    risk_level: str
    reason: str
    approval_id: Optional[str] = None
    agent_id: str
    organization_id: str
    execution_id: Optional[str] = None
    tool: str
    operation: str
    executed: bool
    result: Optional[dict] = None


class EventOut(BaseModel):
    id: str
    organization_id: str
    agent_id: Optional[str]
    seq: int = 0
    resource_kind: str
    action: str
    scope: str
    destination: Optional[str]
    decision: str
    risk_score: float
    risk_level: str
    reason: str
    request_id: str
    execution_id: Optional[str] = None
    evidence_hash: Optional[str] = None
    previous_evidence_hash: Optional[str] = None
    created_at: UTCDatetime

    class Config:
        from_attributes = True


class AlertOut(BaseModel):
    id: str
    severity: str
    title: str
    message: str
    status: str
    event_id: Optional[str]
    created_at: UTCDatetime

    class Config:
        from_attributes = True


class ApprovalOut(BaseModel):
    id: str
    agent_id: str
    resource_kind: str
    action: str
    scope: str
    destination: Optional[str]
    status: str
    reason: str
    reviewed_by: Optional[str]
    created_at: UTCDatetime
    reviewed_at: Optional[UTCDatetime]
    # Phase 17 - the binding that makes this grant authorize one request, once.
    execution_id: Optional[str] = None
    request_id: Optional[str] = None
    contract_id: Optional[str] = None
    contract_version: Optional[int] = None
    param_hash: Optional[str] = None
    expires_at: Optional[UTCDatetime] = None
    consumed_at: Optional[UTCDatetime] = None
    consumed_event_id: Optional[str] = None
    # Phase 20 - what a person needs to decide. `status` is what a human last
    # did; `effective_status` also accounts for time (a request nobody answered
    # is expired). `preview` is decrypted for the authenticated reviewer only.
    effective_status: Optional[str] = None
    agent_name: Optional[str] = None
    preview: Optional[dict] = None

    class Config:
        from_attributes = True


class ApprovalLinkOut(BaseModel):
    """What the one-tap page shows: the request and how to answer it, nothing
    of the internal binding (execution, request, contract, digest)."""

    id: str
    agent_name: Optional[str] = None
    resource_kind: str
    action: str
    scope: str
    destination: Optional[str] = None
    reason: str
    status: str
    effective_status: Optional[str] = None
    created_at: UTCDatetime
    expires_at: Optional[UTCDatetime] = None
    preview: Optional[dict] = None


class ApprovalStatusOut(BaseModel):
    """What an agent may learn about its own approval, and what to do next."""

    approval_id: str
    status: str
    next: str
    expires_at: Optional[UTCDatetime] = None
    decided_at: Optional[UTCDatetime] = None


class ApprovalDecision(BaseModel):
    decision: str


class DashboardStats(BaseModel):
    agents: int
    events: int
    blocked: int
    pending_approvals: int
    open_alerts: int
    allow_rate: float


class BehaviorPatternCreate(BaseModel):
    name: str
    description: str = ""
    type: str
    severity: str = "medium"
    definition: dict
    enabled: bool = True


class BehaviorPatternUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    type: Optional[str] = None
    severity: Optional[str] = None
    definition: Optional[dict] = None
    enabled: Optional[bool] = None


class BehaviorPatternOut(BaseModel):
    id: str
    organization_id: Optional[str] = None
    name: str
    description: str
    type: str
    severity: str
    definition: dict
    enabled: bool
    created_at: UTCDatetime
    updated_at: Optional[UTCDatetime] = None

    class Config:
        from_attributes = True


class RuntimeContractIntegrity(BaseModel):
    algorithm: Optional[str] = None
    digest: Optional[str] = None
    signature: Optional[str] = None
    key_id: Optional[str] = None
    signed_at: Optional[datetime] = None

    class Config:
        extra = "allow"


class RuntimeContractCapability(BaseModel):
    name: str
    actions: Optional[list[str]] = None
    description: Optional[str] = None

    class Config:
        extra = "allow"


class RuntimeContractResource(BaseModel):
    kind: str
    name: Optional[str] = None
    identifier: Optional[str] = None
    scope: Optional[str] = None
    sensitivity: Optional[str] = None

    class Config:
        extra = "allow"


class RuntimeContractDocument(BaseModel):
    organization_id: str
    agent_id: str
    contract_id: str
    version: int
    status: str = "DRAFT"
    purpose: str = ""
    capabilities: list[dict] = Field(default_factory=list)
    resources: list[dict] = Field(default_factory=list)
    constraints: dict = Field(default_factory=dict)
    data_constraints: dict = Field(default_factory=dict)
    workflow: Optional[dict] = None
    approval_rules: list[dict] = Field(default_factory=list)
    valid_from: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    integrity: Optional[RuntimeContractIntegrity] = None


class RuntimeContractOut(BaseModel):
    id: str
    organization_id: str
    agent_id: str
    contract_id: str
    version: int
    status: str
    purpose: str
    capabilities: list
    resources: list
    constraints: dict
    data_constraints: dict
    workflow: Optional[dict] = None
    approval_rules: list
    valid_from: Optional[UTCDatetime] = None
    expires_at: Optional[UTCDatetime] = None
    integrity: Optional[dict] = None
    created_at: UTCDatetime
    updated_at: Optional[UTCDatetime] = None

    class Config:
        from_attributes = True


class RuntimeContractStatusChange(BaseModel):
    status: str
