"""Phase 18 — the capability catalogue the dashboard configures against.

The frontend must not invent a permission model. This endpoint reports what the
backend actually supports, assembled from the real sources:

  * the tools the enforcement gateway can execute (routers/gateway.TOOL_MAP);
  * the resources this organization has registered;
  * the actions the risk engine already knows about.

It also reports, per capability, whether Aegis can *enforce* it end to end or
can only *decide* on it. That distinction is real and easy to hide: only `crm`
is wired to a protected tool. For email, files and payments Aegis returns a
decision and nothing executes, because no tool is connected. A dashboard that
showed them identically would imply an enforcement that does not exist.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..engines.risk import IRREVERSIBLE
from ..security import get_current_user
from .gateway import TOOL_MAP

router = APIRouter(tags=["capabilities"])

# Actions Aegis understands per resource kind. Kinds present in TOOL_MAP are
# executable; the rest are decision-only until a tool is connected.
KNOWN_ACTIONS = {
    "crm": ["READ", "UPDATE", "DELETE"],
    # Phase 19. gmail is the second enforceable kind: unlike email/files/
    # payments, a real connector is wired to it, so enforceable=true here is a
    # statement about a real side effect and not a placeholder.
    "gmail": ["SEARCH", "READ", "DRAFT", "SEND", "DELETE"],
    "email": ["SEND"],
    "files": ["READ", "EXPORT"],
    "payments": ["TRANSFER"],
}


@router.get("/capabilities")
def list_capabilities(
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    registered = {
        row.kind
        for row in db.query(models.Resource)
        .filter(models.Resource.organization_id == user.organization_id)
        .all()
    }
    kinds = sorted(registered | set(KNOWN_ACTIONS) | set(TOOL_MAP))

    catalogue = []
    for kind in kinds:
        spec = TOOL_MAP.get(kind)
        executable_ops = {
            value.upper() for value in (spec or {}).get("operations", {}).values()
        }
        for action in KNOWN_ACTIONS.get(kind, []):
            catalogue.append(
                {
                    "resource_kind": kind,
                    "action": action,
                    # Can the gateway actually carry this out, or only rule on it?
                    "enforceable": action in executable_ops,
                    "irreversible": (kind, action) in IRREVERSIBLE,
                    "registered_resource": kind in registered,
                    "default_scope": {"crm": "customers", "gmail": "mailbox"}.get(
                        kind, "*"
                    ),
                }
            )
    return {
        "capabilities": catalogue,
        "executable_tools": sorted(TOOL_MAP),
        "note": (
            "enforceable=false means Aegis returns a decision but no protected "
            "tool is connected, so nothing executes either way."
        ),
    }
