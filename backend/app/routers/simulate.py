"""Phase 18 — "what would Aegis decide?", answered without deciding anything.

An operator needs to check that a contract says what they meant, before an
agent depends on it. They cannot do that from the dashboard by asking the real
authorization endpoint: `/api/authorize` lives on the enforcement gateway, which
sits on internal networks with no host port, so neither the browser nor the
control plane can reach it. (The old Playground page tried, and its 404 looked
like a policy result.)

So this endpoint evaluates the request here, on the control plane, **reusing the
exact same engines** the gateway uses:

    permission_engine.allows
    policy_engine.evaluate
    resolve_active_contract_for_agent + contract_engine.evaluate_contract

There is deliberately no second implementation of the rules. If these engines
change, the simulation changes with them.

Two things it is careful about:

  * It writes nothing. No execution, no event, no evidence, no approval. A
    simulation that left a trail would pollute the audit record with actions
    that never happened.
  * It does not model trajectory or workflow, because those depend on an
    execution's history and a simulated request has none. The response says so
    rather than implying a completeness it does not have.

It grants no authority: the caller is an authenticated operator, the answer is
advisory, and the gateway re-decides for real when an agent actually asks.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import config, models
from ..contract_store import ContractResolutionError, resolve_active_contract_for_agent
from ..database import get_db
from ..engines import contract as contract_engine
from ..engines import permission as permission_engine
from ..engines import policy as policy_engine
from ..engines import risk as risk_engine
from ..engines.behavior import TrajectoryStep
from ..security import get_current_user

router = APIRouter(tags=["simulation"])


class SimulateRequest(BaseModel):
    resource_kind: str
    action: str
    scope: str
    destination: Optional[str] = None
    payload: Optional[dict] = None


@router.post("/agents/{agent_id}/simulate")
def simulate(
    agent_id: str,
    body: SimulateRequest,
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

    kind = body.resource_kind.lower()
    action = body.action.upper()
    steps: list[dict] = []

    # 1. Identity. A revoked agent cannot authenticate at all.
    if agent.status != "active":
        steps.append(
            {
                "layer": "identity",
                "outcome": "BLOCK",
                "detail": "Agent is revoked and can no longer authenticate.",
            }
        )
        return {
            "decision": "BLOCK",
            "reason": "Agent is revoked.",
            "steps": steps,
            "models_trajectory": False,
            "advisory": True,
        }
    steps.append(
        {"layer": "identity", "outcome": "PASS", "detail": "Agent is active."}
    )

    # 2. Least privilege.
    permitted = permission_engine.allows(agent, kind, action, body.scope)
    steps.append(
        {
            "layer": "permission",
            "outcome": "PASS" if permitted else "BLOCK",
            "detail": (
                f"Agent holds a permission for {kind}.{action.lower()} at this scope."
                if permitted
                else "Agent holds no permission for this resource, action and scope."
            ),
        }
    )

    # 3. Organization policy.
    policy_result = policy_engine.evaluate(
        db, agent, kind, action, body.scope, body.destination, check_permission=False
    )
    steps.append(
        {
            "layer": "policy",
            "outcome": policy_result.decision,
            "detail": policy_result.reason,
        }
    )

    decision = "BLOCK" if not permitted else policy_result.decision
    reason = (
        "Agent lacks permission for this resource/action/scope (least privilege)."
        if not permitted
        else policy_result.reason
    )

    # 4. Runtime contract.
    contract_id = None
    contract_version = None
    try:
        contract = resolve_active_contract_for_agent(db, agent)
    except ContractResolutionError as exc:
        contract = None
        if exc.reason == "not_found" and config.REQUIRE_RUNTIME_CONTRACT:
            decision = "BLOCK"
            reason = "No runtime contract is active for this agent."
            steps.append(
                {
                    "layer": "contract",
                    "outcome": "BLOCK",
                    "detail": reason,
                }
            )
        elif exc.reason != "not_found":
            decision = "BLOCK"
            # Same wording the gateway would give, from the same mapping.
            reason = contract_engine.resolution_reason(exc.reason)
            steps.append(
                {"layer": "contract", "outcome": "BLOCK", "detail": reason}
            )
        else:
            steps.append(
                {
                    "layer": "contract",
                    "outcome": "SKIPPED",
                    "detail": "No contract, and this deployment allows the legacy pass-through.",
                }
            )
    else:
        contract_id = contract.contract_id
        contract_version = contract.version
        verdict = contract_engine.evaluate_contract(
            contract,
            kind=kind,
            action=action,
            scope=body.scope,
            destination=body.destination,
            payload=body.payload,
            previous=[],
            current=TrajectoryStep(
                resource_kind=kind,
                action=action,
                scope=body.scope,
                destination=body.destination,
                decision=None,
            ),
        )
        if not verdict.allowed:
            decision = "BLOCK"
            reason = verdict.reason
            steps.append(
                {"layer": "contract", "outcome": "BLOCK", "detail": verdict.reason}
            )
        elif verdict.requires_approval and decision == "ALLOW":
            decision = "APPROVAL"
            reason = verdict.approval_reason or reason
            steps.append(
                {
                    "layer": "contract",
                    "outcome": "APPROVAL",
                    "detail": verdict.approval_reason,
                }
            )
        else:
            steps.append(
                {
                    "layer": "contract",
                    "outcome": "PASS",
                    "detail": f"Within contract {contract.contract_id} v{contract.version}.",
                }
            )

    risk = risk_engine.evaluate(kind, action, body.scope, body.destination, decision)

    return {
        "decision": decision,
        "reason": reason,
        "risk_level": risk.level,
        "risk_score": risk.score,
        "contract_id": contract_id,
        "contract_version": contract_version,
        "steps": steps,
        # Stated so nobody reads this as a complete answer.
        "models_trajectory": False,
        "advisory": True,
        "note": (
            "Advisory only. Nothing was executed and no evidence was written. "
            "Trajectory and workflow rules depend on an execution's history and "
            "are not evaluated here; the gateway decides for real when an agent "
            "asks."
        ),
    }
