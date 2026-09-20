"""Phase 20 -- what an organization starts with.

Until now the safety policies ("email outside the company needs a human", "never
move money", "mail is never deleted") existed only for the seeded demo
organization. A real customer registered, created an agent, and found that
nothing restricted it: the policy engine allows whatever it has no rule for. So
the rules that make Aegis worth using are installed for every new organization.

The pack is org-level policy, so it applies to every agent the organization ever
creates, whichever way the agent was created. It is installed idempotently by
name, so running it twice, or after the organization added rules of its own,
changes nothing that already exists.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from .. import config, models

STARTER_POLICIES: tuple[dict, ...] = (
    {
        "name": "Gmail delete is never allowed",
        "description": "Deleting mail is not an action an agent may take, with or without approval.",
        "resource_kind": "gmail",
        "action": "DELETE",
        "scope_pattern": "*",
        "decision": "BLOCK",
        "priority": 1,
    },
    {
        "name": "Sending mail needs a human",
        "description": "An agent may compose; a person decides to send.",
        "resource_kind": "gmail",
        "action": "SEND",
        "scope_pattern": "*",
        "decision": "APPROVAL",
        "priority": 3,
    },
    {
        "name": "Hard-block payments",
        "description": "Payment transfers are never autonomous.",
        "resource_kind": "payments",
        "action": "TRANSFER",
        "scope_pattern": "*",
        "decision": "BLOCK",
        "priority": 5,
    },
    {
        "name": "Block CRM mass delete",
        "description": "CRM DELETE on all customers is never allowed.",
        "resource_kind": "crm",
        "action": "DELETE",
        "scope_pattern": "*",
        "decision": "BLOCK",
        "priority": 10,
    },
    {
        "name": "Block finance export",
        "description": "Finance files cannot leave the perimeter.",
        "resource_kind": "files",
        "action": "EXPORT",
        "scope_pattern": "/Finance*",
        "decision": "BLOCK",
        "priority": 15,
    },
    {
        "name": "Approve external email",
        "description": "Sending email outside the company requires a human.",
        "resource_kind": "email",
        "action": "SEND",
        "scope_pattern": "external",
        "destination_pattern": "external",
        "decision": "APPROVAL",
        "priority": 20,
    },
    {
        "name": "Allow internal email",
        "description": "Internal email is permitted.",
        "resource_kind": "email",
        "action": "SEND",
        "scope_pattern": "internal",
        "decision": "ALLOW",
        "priority": 30,
    },
)


RECOMMENDED_PRESET = "recommended"

# (resource kind, action, scope) an agent may use under the recommended preset.
# Reading is open. Sending goes through the organization's policies (a send to
# anyone outside needs a human), and crm.UPDATE needs a human by contract rule.
# Deliberately absent: deleting, moving money, exporting. An agent that needs
# them is given them on purpose, one at a time.
_RECOMMENDED_GRANTS: tuple[tuple[str, str, str], ...] = (
    ("email", "SEND", "*"),
    ("gmail", "SEARCH", "mailbox"),
    ("gmail", "READ", "mailbox"),
    ("gmail", "DRAFT", "mailbox"),
    ("gmail", "SEND", "mailbox"),
    ("crm", "READ", "customers"),
    ("crm", "UPDATE", "customers"),
    ("files", "READ", "*"),
)
_NEEDS_A_HUMAN = (("crm", "UPDATE"),)


def apply_recommended(db: Session, agent: models.Agent) -> None:
    """Give a new agent a sensible starting authority: permissions and an ACTIVE
    runtime contract, which is what makes them usable (no contract, no authority)."""
    from ..contract_store import save_contract

    kinds: dict[str, dict] = {}
    for kind, action, scope in _RECOMMENDED_GRANTS:
        # Gmail is a pilot-only connector. Where it is not offered there is
        # nothing behind these permissions, so do not present them as authority.
        if kind == "gmail" and not config.ENABLE_GMAIL:
            continue
        db.add(
            models.Permission(
                agent_id=agent.id,
                resource_kind=kind,
                action=action,
                scope=scope,
                effect="allow",
            )
        )
        entry = kinds.setdefault(kind, {"actions": [], "scope": scope})
        entry["actions"].append(action)

    save_contract(
        db,
        {
            "organization_id": agent.organization_id,
            "agent_id": agent.id,
            "contract_id": f"recommended-{agent.id[:8]}",
            "version": 1,
            "status": "ACTIVE",
            "purpose": (
                "Recommended starting authority: read freely; sending, updating and "
                "anything irreversible go through a person."
            ),
            "capabilities": [
                {"name": kind, "resource_kind": kind, "actions": entry["actions"]}
                for kind, entry in kinds.items()
            ],
            "resources": [
                {"kind": kind, "scope": entry["scope"]} for kind, entry in kinds.items()
            ],
            "constraints": {},
            "data_constraints": {},
            "approval_rules": [
                {"resource_kind": kind, "action": action, "require": "human"}
                for kind, action in _NEEDS_A_HUMAN
            ],
        },
    )


def install(db: Session, organization_id: str) -> int:
    """Add any missing starter policy. Returns how many were added."""
    existing = {
        name
        for (name,) in db.query(models.Policy.name).filter(
            models.Policy.organization_id == organization_id
        )
    }
    added = 0
    for spec in STARTER_POLICIES:
        if spec["name"] in existing:
            continue
        db.add(models.Policy(organization_id=organization_id, **spec))
        added += 1
    if added:
        db.flush()
    return added
