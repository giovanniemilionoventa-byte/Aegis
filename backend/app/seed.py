import os
from datetime import timedelta

from sqlalchemy.orm import Session

from . import models
from . import config
from .security import create_agent_token, hash_password, hash_token, utcnow


def _seed_expiry():
    if config.AGENT_TOKEN_TTL_DAYS <= 0:
        return None
    return utcnow() + timedelta(days=config.AGENT_TOKEN_TTL_DAYS)


DEMO_EMAIL = "admin@acme.test"
DEMO_PASSWORD = "aegis-demo"


def seed_if_empty(db: Session) -> None:
    if db.query(models.Organization).first():
        return

    org = models.Organization(name="Acme Corp", slug="acme")
    db.add(org)
    db.flush()

    user = models.User(
        organization_id=org.id,
        email=DEMO_EMAIL,
        password_hash=hash_password(DEMO_PASSWORD),
        full_name="Ada Admin",
        role="admin",
    )
    db.add(user)
    db.flush()

    db.add(
        models.Device(
            organization_id=org.id,
            hostname="acme-gateway-01",
            platform="linux",
            status="online",
        )
    )

    resources = [
        models.Resource(
            organization_id=org.id,
            kind="crm",
            name="Customers CRM",
            identifier="crm://customers",
            sensitivity="internal",
        ),
        models.Resource(
            organization_id=org.id,
            kind="email",
            name="Corporate Mail",
            identifier="email://smtp",
            sensitivity="internal",
        ),
        models.Resource(
            organization_id=org.id,
            kind="files",
            name="Shared Drive",
            identifier="files://drive",
            sensitivity="confidential",
        ),
        models.Resource(
            organization_id=org.id,
            kind="payments",
            name="Treasury",
            identifier="payments://treasury",
            sensitivity="restricted",
        ),
    ]
    db.add_all(resources)

    policies = [
        models.Policy(
            organization_id=org.id,
            name="Block CRM mass delete",
            description="CRM DELETE on all customers is never allowed.",
            resource_kind="crm",
            action="DELETE",
            scope_pattern="*",
            decision="BLOCK",
            priority=10,
        ),
        models.Policy(
            organization_id=org.id,
            name="Approve external email",
            description="Sending email outside the company requires a human.",
            resource_kind="email",
            action="SEND",
            scope_pattern="external",
            destination_pattern="external",
            decision="APPROVAL",
            priority=20,
        ),
        models.Policy(
            organization_id=org.id,
            name="Allow internal email",
            description="Internal email is permitted.",
            resource_kind="email",
            action="SEND",
            scope_pattern="internal",
            decision="ALLOW",
            priority=30,
        ),
        models.Policy(
            organization_id=org.id,
            name="Allow sales files read",
            description="Agents may read /Sales.",
            resource_kind="files",
            action="READ",
            scope_pattern="/Sales*",
            decision="ALLOW",
            priority=40,
        ),
        models.Policy(
            organization_id=org.id,
            name="Block finance export",
            description="Finance files cannot leave the perimeter.",
            resource_kind="files",
            action="EXPORT",
            scope_pattern="/Finance*",
            decision="BLOCK",
            priority=15,
        ),
        models.Policy(
            organization_id=org.id,
            name="Hard-block payments",
            description="Payment transfers are never autonomous.",
            resource_kind="payments",
            action="TRANSFER",
            scope_pattern="*",
            decision="BLOCK",
            priority=5,
        ),
        models.Policy(
            organization_id=org.id,
            name="Allow CRM read",
            description="Customer records may be read.",
            resource_kind="crm",
            action="READ",
            scope_pattern="*",
            decision="ALLOW",
            priority=50,
        ),
    ]
    db.add_all(policies)

    sales_agent = models.Agent(
        organization_id=org.id,
        owner_id=user.id,
        name="Sales Copilot",
        provider="demo",
        model="local-demo",
        description="Reads CRM and sales files; may send internal email.",
    )
    db.add(sales_agent)
    db.flush()

    for kind, action, scope in [
        ("crm", "READ", "customers"),
        ("crm", "DELETE", "*"),
        ("email", "SEND", "internal"),
        ("email", "SEND", "external"),
        ("files", "READ", "/Sales"),
        ("files", "EXPORT", "/Finance"),
        ("payments", "TRANSFER", "*"),
    ]:
        db.add(
            models.Permission(
                agent_id=sales_agent.id,
                resource_kind=kind,
                action=action,
                scope=scope,
                effect="allow",
            )
        )

    token = create_agent_token()
    db.add(
        models.Credential(
            agent_id=sales_agent.id,
            token_hash=hash_token(token),
            token_prefix=token[:16],
            status="active",
            expires_at=_seed_expiry(),
        )
    )

    token_path = os.environ.get("AEGIS_DEMO_TOKEN_PATH", "/tmp/aegis_demo_token.txt")
    try:
        with open(token_path, "w", encoding="utf-8") as handle:
            handle.write(token)
    except OSError:
        pass

    reader = models.Agent(
        organization_id=org.id,
        owner_id=user.id,
        name="Research Reader",
        provider="demo",
        model="local-demo",
        description="Least-privilege reader for sales files only.",
    )
    db.add(reader)
    db.flush()
    db.add(
        models.Permission(
            agent_id=reader.id,
            resource_kind="files",
            action="READ",
            scope="/Sales",
            effect="allow",
        )
    )
    rtoken = create_agent_token()
    db.add(
        models.Credential(
            agent_id=reader.id,
            token_hash=hash_token(rtoken),
            token_prefix=rtoken[:16],
            status="active",
            expires_at=_seed_expiry(),
        )
    )

    # Phase 17: the demo agents get an ACTIVE runtime contract.
    #
    # Enforcement is fail-closed on a missing contract, so without this the
    # seeded demo would deny everything. Only these two seeded agents are given
    # a contract: an agent created later through /api/agents has none, and is
    # correctly denied until an operator writes one.
    _seed_demo_contracts(db, org.id, sales_agent.id, reader.id)

    _seed_verification_agent(db, org.id, user.id)
    _seed_gmail(db, org.id, user.id)

    db.commit()
    print(f"[aegis] seeded org=acme user={DEMO_EMAIL} password={DEMO_PASSWORD}")
    print(f"[aegis] sales copilot token written to /tmp/aegis_demo_token.txt")


def _seed_verification_agent(db: Session, org_id: str, owner_id: str) -> None:
    """Register the agent that lives in the agent container.

    Phase 18. The container is handed AEGIS_VERIFY_AGENT_TOKEN at start; this
    registers the matching identity so the token is a real, enforced agent
    credential rather than a special case in the auth path. Without the env var
    nothing is created, and the dashboard reports the verification agent as not
    configured instead of pretending it exists.
    """
    from .contract_store import save_contract

    token = (config.VERIFY_AGENT_TOKEN or "").strip()
    if not token:
        return

    agent = models.Agent(
        organization_id=org_id,
        owner_id=owner_id,
        name="Runtime Verification Agent",
        provider="aegis-reference",
        model="deterministic",
        description=(
            "Controlled test harness running in the agent container. Not a "
            "production agent: it executes a fixed scenario so an operator can "
            "watch Aegis authorize and refuse real actions."
        ),
    )
    db.add(agent)
    db.flush()

    for kind, action, scope in [
        ("crm", "READ", "customers"),
        ("crm", "UPDATE", "customers"),
    ]:
        db.add(
            models.Permission(
                agent_id=agent.id,
                resource_kind=kind,
                action=action,
                scope=scope,
                effect="allow",
            )
        )

    db.add(
        models.Credential(
            agent_id=agent.id,
            token_hash=hash_token(token),
            token_prefix=token[:16],
            status="active",
            expires_at=_seed_expiry(),
        )
    )

    save_contract(
        db,
        {
            "organization_id": org_id,
            "agent_id": agent.id,
            "contract_id": "runtime-verification",
            "version": 1,
            "status": "ACTIVE",
            "purpose": "Read customer records; correct one with human approval.",
            "capabilities": [
                {"name": "crm", "resource_kind": "crm", "actions": ["READ", "UPDATE"]}
            ],
            "resources": [{"kind": "crm", "scope": "customers"}],
            "constraints": {"payload_size": {"max_bytes": 4096}},
            "data_constraints": {"denied_fields": ["ssn", "secret", "password"]},
            "approval_rules": [],
        },
    )

    db.add(
        models.Policy(
            organization_id=org_id,
            name="Customer record changes need a human",
            description="CRM updates are reviewed before they run.",
            resource_kind="crm",
            action="UPDATE",
            scope_pattern="*",
            decision="APPROVAL",
            priority=2,
        )
    )


GMAIL_SCOPE = "mailbox"


def _seed_gmail(db: Session, org_id: str, owner_id: str) -> None:
    """The canonical Phase 19 Gmail posture, expressed in the existing engines.

        gmail.search  ALLOW
        gmail.read    ALLOW
        gmail.draft   ALLOW
        gmail.send    APPROVAL_REQUIRED
        gmail.delete  DENY

    Nothing here is a new policy mechanism. search/read/draft are permitted and
    have no restrictive policy, so the policy engine's default applies. send has
    an organization policy of APPROVAL *and* a contract approval rule, so it
    needs a human whichever way it is reached. delete is denied three times over,
    deliberately:

      1. the agent holds no DELETE permission, so least privilege refuses it;
      2. an organization policy BLOCKs gmail.DELETE at the highest priority,
         which catches an agent that *was* granted the permission;
      3. the runtime contract does not list DELETE as a capability.

    Each layer is asserted separately in tests/test_phase19_policy.py, because
    "it is denied" is a weaker claim than "it is denied even when the layer
    above it is wrong". A fourth floor sits outside Aegis: the OAuth scope
    requested does not grant permanent deletion at all.
    """
    from .contract_store import save_contract

    db.add(
        models.Resource(
            organization_id=org_id,
            kind="gmail",
            name="Gmail Mailbox",
            identifier="gmail://mailbox",
            sensitivity="confidential",
        )
    )

    db.add_all(
        [
            models.Policy(
                organization_id=org_id,
                name="Gmail delete is never allowed",
                description=(
                    "Deleting mail is not an action an agent may take, with or "
                    "without approval."
                ),
                resource_kind="gmail",
                action="DELETE",
                scope_pattern="*",
                decision="BLOCK",
                priority=1,
            ),
            models.Policy(
                organization_id=org_id,
                name="Sending mail needs a human",
                description="An agent may compose; a person decides to send.",
                resource_kind="gmail",
                action="SEND",
                scope_pattern="*",
                decision="APPROVAL",
                priority=3,
            ),
        ]
    )

    token = (config.GMAIL_AGENT_TOKEN or "").strip()
    if not token:
        # No identity configured, so no agent is registered. The dashboard
        # reports it as not configured rather than implying one exists.
        return

    agent = models.Agent(
        organization_id=org_id,
        owner_id=owner_id,
        name="Gmail Assistant",
        provider="external-llm",
        model=config.AGENT_LLM_MODEL or "unconfigured",
        description=(
            "An untrusted external AI agent with a real model behind it. It "
            "holds this Aegis token and nothing else: no Google credential, no "
            "route to Gmail, no way to act except by asking Aegis."
        ),
    )
    db.add(agent)
    db.flush()

    # Least privilege: four of the five canonical operations. DELETE is absent.
    for action in ("SEARCH", "READ", "DRAFT", "SEND"):
        db.add(
            models.Permission(
                agent_id=agent.id,
                resource_kind="gmail",
                action=action,
                scope=GMAIL_SCOPE,
                effect="allow",
            )
        )

    db.add(
        models.Credential(
            agent_id=agent.id,
            token_hash=hash_token(token),
            token_prefix=token[:16],
            status="active",
            expires_at=_seed_expiry(),
        )
    )

    save_contract(
        db,
        {
            "organization_id": org_id,
            "agent_id": agent.id,
            "contract_id": "gmail-assistant",
            "version": 1,
            "status": "ACTIVE",
            "purpose": (
                "Find and read mail, draft replies, and send only what a human "
                "has approved."
            ),
            "capabilities": [
                {
                    "name": "gmail",
                    "resource_kind": "gmail",
                    "actions": ["SEARCH", "READ", "DRAFT", "SEND"],
                }
            ],
            "resources": [{"kind": "gmail", "scope": GMAIL_SCOPE}],
            "constraints": {"payload_size": {"max_bytes": 16384}},
            "data_constraints": {},
            "approval_rules": [
                {"resource_kind": "gmail", "action": "SEND", "require": "human"}
            ],
        },
    )


def _seed_demo_contracts(
    db: Session, org_id: str, sales_agent_id: str, reader_agent_id: str
) -> None:
    """ACTIVE contracts for the two seeded demo agents.

    The Sales Copilot contract deliberately spans the seven blueprint tools so
    the demo's ALLOW / APPROVAL / BLOCK mix is still decided by policy: the
    contract states what the agent is *for*, policy states what it may do right
    now. The Research Reader contract is narrow on purpose, so the difference
    between "permitted by policy" and "inside the contract" is visible.
    """
    from .contract_store import save_contract

    save_contract(
        db,
        {
            "organization_id": org_id,
            "agent_id": sales_agent_id,
            "contract_id": "sales-copilot",
            "version": 1,
            "status": "ACTIVE",
            "purpose": "Sales assistant: read customer data, work sales files, send internal mail.",
            "capabilities": [
                {"name": "crm", "resource_kind": "crm", "actions": ["READ", "UPDATE", "DELETE"]},
                {"name": "email", "resource_kind": "email", "actions": ["SEND"]},
                {"name": "files", "resource_kind": "files", "actions": ["READ", "EXPORT"]},
                {"name": "payments", "resource_kind": "payments", "actions": ["TRANSFER"]},
            ],
            "resources": [
                {"kind": "crm", "scope": "*"},
                {"kind": "email", "scope": "*"},
                {"kind": "files", "scope": "*"},
                {"kind": "payments", "scope": "*"},
            ],
            "constraints": {},
            "data_constraints": {},
            "approval_rules": [
                {"resource_kind": "email", "action": "SEND", "require": "human"}
            ],
        },
    )

    save_contract(
        db,
        {
            "organization_id": org_id,
            "agent_id": reader_agent_id,
            "contract_id": "research-reader",
            "version": 1,
            "status": "ACTIVE",
            "purpose": "Least-privilege reader for sales files only.",
            "capabilities": [
                {"name": "files", "resource_kind": "files", "actions": ["READ"]}
            ],
            "resources": [{"kind": "files", "scope": "/Sales*"}],
            "constraints": {},
            "data_constraints": {},
            "approval_rules": [],
        },
    )


BUILTIN_PATTERNS = [
    {
        "name": "CRM read then files export then external email",
        "description": "SEQUENCE: CRM READ followed by FILES EXPORT followed by EMAIL SEND external.",
        "type": "SEQUENCE",
        "severity": "high",
        "definition": {
            "steps": [
                {"resource_kind": "crm", "action": "READ"},
                {"resource_kind": "files", "action": "EXPORT"},
                {
                    "resource_kind": "email",
                    "action": "SEND",
                    "scope": "external",
                },
            ]
        },
    },
    {
        "name": "Repeated external email send",
        "description": "THRESHOLD: multiple EMAIL SEND to external destinations in one execution.",
        "type": "THRESHOLD",
        "severity": "medium",
        "definition": {
            "resource_kind": "email",
            "action": "SEND",
            "scope": "external",
            "count": 5,
        },
    },
]


def seed_builtin_patterns(db: Session) -> None:
    created = 0
    for item in BUILTIN_PATTERNS:
        exists = (
            db.query(models.BehaviorPattern)
            .filter(
                models.BehaviorPattern.organization_id.is_(None),
                models.BehaviorPattern.name == item["name"],
            )
            .first()
        )
        if exists:
            continue
        db.add(
            models.BehaviorPattern(
                organization_id=None,
                name=item["name"],
                description=item["description"],
                type=item["type"],
                severity=item["severity"],
                definition=item["definition"],
                enabled=True,
            )
        )
        created += 1
    if created:
        db.commit()
