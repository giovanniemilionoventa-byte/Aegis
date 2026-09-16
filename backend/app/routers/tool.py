from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..credentials import contains_any_tool_secret
from ..internal_auth import require_tool_token
from ..protected.crm import InvalidToolCredential, protected_crm
from ..protected.gmail import GmailConnectorError, gmail_connector
from ..protected.gmail import authenticate_connector_credential as gmail_authenticate

router = APIRouter(prefix="/internal/tools", tags=["protected-tool"])

SUPPORTED_TOOLS = ("crm", "gmail")


class ToolInvokeRequest(BaseModel):
    secret: str
    scope: str = "customers"
    payload: Optional[dict[str, Any]] = None
    organization_id: Optional[str] = None


def _invoke_gmail(
    operation: str, body: ToolInvokeRequest
) -> dict[str, Any]:
    """Phase 19 — the real Gmail connector, behind the same internal boundary.

    The route carries a fixed operation name, never a URL or a method. The
    connector rejects any operation outside its five supported ones before it
    touches a credential, so this endpoint cannot be turned into a Gmail proxy
    by sending a different path segment.
    """
    if not body.organization_id:
        raise HTTPException(status_code=400, detail="missing_organization")
    gmail_authenticate(body.secret, body.organization_id)
    return gmail_connector.execute(
        operation,
        organization_id=body.organization_id,
        payload=body.payload,
    )


@router.post("/{tool}/{operation}")
def invoke_tool(
    tool: str,
    operation: str,
    body: ToolInvokeRequest,
    _: None = Depends(require_tool_token),
):
    tool_name = tool.lower()
    if tool_name not in SUPPORTED_TOOLS:
        raise HTTPException(status_code=400, detail="Unknown protected tool")

    if tool_name == "gmail":
        try:
            result = _invoke_gmail(operation.lower(), body)
        except GmailConnectorError as exc:
            # The connector's own message is safe to surface: it is built from
            # fixed strings and never contains credential material or Google's
            # raw error body.
            raise HTTPException(status_code=exc.status, detail=exc.code) from exc
    else:
        try:
            result = protected_crm.execute(
                operation.lower(),
                body.secret,
                scope=body.scope,
                payload=body.payload,
                organization_id=body.organization_id,
            )
        except InvalidToolCredential as exc:
            raise HTTPException(status_code=401, detail="invalid_tool_credential") from exc

    cleaned = {key: value for key, value in result.items() if key != "secret"}
    if contains_any_tool_secret(cleaned, body.organization_id):
        raise HTTPException(status_code=502, detail="Protected tool returned unsafe payload")
    return cleaned
