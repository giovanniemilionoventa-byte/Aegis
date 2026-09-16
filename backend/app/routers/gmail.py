"""Connecting a real Gmail account, on the control plane, by a human.

Phase 19. The model here is deliberate and is the whole point of the phase:

    a human operator consents to Google
      -> Google gives Aegis a refresh token
      -> Aegis seals it server-side
      -> the connector uses it
      -> the agent never sees any of it

The agent is not in that list. There is no endpoint in this router an agent
token can reach: the control plane rejects agent credentials wholesale (see
main.py), and every route here additionally requires an authenticated operator,
except the OAuth callback, which requires a signed state parameter instead
because it is entered by Google through the operator's browser.

WHAT IS NEVER RETURNED. No route returns a refresh token, an access token, an
authorization code or the client secret. /status returns the connected address
and the granted scopes and nothing else. If you are adding a route here, the
rule is: metadata out, credentials never.

WHAT IS NEVER LOGGED. The authorization code and the token response are not
logged, not echoed in an error, and not stored outside the sealed record.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import config, gmail_store, models
from ..database import get_db
from ..security import get_current_user

router = APIRouter(prefix="/gmail", tags=["gmail"])

STATE_TTL_SECONDS = 600
_STATE_CONTEXT = b"aegis-gmail-oauth-state:v1"


class ConnectStart(BaseModel):
    authorization_url: str
    expires_in: int


def _b64(raw: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    import base64

    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _sign_state(organization_id: str, user_id: str) -> str:
    """A state parameter Google hands back, that only Aegis could have made.

    Carries the tenant and the operator so the callback knows whose mailbox is
    being connected without trusting a query parameter, and an expiry so a
    stale authorization link cannot be replayed a day later.
    """
    payload = {
        "org": organization_id,
        "user": user_id,
        "nonce": secrets.token_urlsafe(16),
        "exp": int(time.time()) + STATE_TTL_SECONDS,
    }
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signature = hmac.new(
        config.SECRET_KEY.encode("utf-8"),
        _STATE_CONTEXT + body.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{body}.{_b64(signature)}"


def _verify_state(state: str) -> dict:
    try:
        body, signature = state.split(".", 1)
    except (ValueError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail="invalid_state") from exc
    expected = hmac.new(
        config.SECRET_KEY.encode("utf-8"),
        _STATE_CONTEXT + body.encode("ascii"),
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(_b64(expected), signature):
        raise HTTPException(status_code=400, detail="invalid_state")
    try:
        payload = json.loads(_b64decode(body))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid_state") from exc
    if int(payload.get("exp", 0)) < time.time():
        raise HTTPException(status_code=400, detail="state_expired")
    if not payload.get("org"):
        raise HTTPException(status_code=400, detail="invalid_state")
    return payload


def _require_oauth_client() -> None:
    if not config.GOOGLE_OAUTH_CLIENT_ID or not config.GOOGLE_OAUTH_CLIENT_SECRET:
        raise HTTPException(
            status_code=503,
            detail=(
                "Google OAuth client is not configured. A human must create one "
                "in Google Cloud Console and put it in .env; see "
                "docs/PHASE_19_GMAIL.md."
            ),
        )


def _page(title: str, message: str) -> HTMLResponse:
    """The operator's browser lands here after Google. Deliberately plain."""
    safe_title = title.replace("<", "&lt;")
    safe_message = message.replace("<", "&lt;")
    return HTMLResponse(
        "<!doctype html><meta charset='utf-8'>"
        f"<title>{safe_title}</title>"
        "<body style=\"font-family:system-ui;margin:3rem;max-width:40rem\">"
        f"<h1>{safe_title}</h1><p>{safe_message}</p>"
        "<p>You can close this tab and return to the Aegis dashboard.</p>"
        "</body>"
    )


@router.get("/status")
def gmail_status(
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Is a mailbox connected for this tenant, and which one.

    Metadata only. There is no shape of this response that includes a token.
    """
    try:
        info = gmail_store.get_connection(user.organization_id)
    except gmail_store.GmailStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    configured = bool(
        config.GOOGLE_OAUTH_CLIENT_ID and config.GOOGLE_OAUTH_CLIENT_SECRET
    )
    return {
        "oauth_client_configured": configured,
        "encryption_key_configured": bool(config.GMAIL_OAUTH_ENCRYPTION_KEY),
        "connected": info is not None,
        "connection": info.as_dict() if info else None,
        "requested_scopes": list(config.GMAIL_OAUTH_SCOPES),
        "redirect_uri": config.GOOGLE_OAUTH_REDIRECT_URI,
    }


@router.post("/oauth/start", response_model=ConnectStart)
def start_oauth(user: models.User = Depends(get_current_user)):
    """Build the Google consent URL the operator will visit.

    access_type=offline and prompt=consent are both required to be handed a
    refresh token: without them Google returns an access token only, and the
    connector would stop working within the hour.
    """
    _require_oauth_client()
    params = {
        "client_id": config.GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": config.GOOGLE_OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(config.GMAIL_OAUTH_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "false",
        "state": _sign_state(user.organization_id, user.id),
    }
    return ConnectStart(
        authorization_url=f"{config.GOOGLE_AUTH_ENDPOINT}?{urlencode(params)}",
        expires_in=STATE_TTL_SECONDS,
    )


@router.get("/oauth/callback")
def oauth_callback(
    code: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    error: Optional[str] = Query(default=None),
):
    """Google's redirect. Exchanges the code for a refresh token and seals it.

    Entered by the operator's browser, so there is no Authorization header to
    check; the signed state is what establishes which tenant this is for. The
    code is used once, here, and is never logged or stored.
    """
    if error:
        return _page("Gmail not connected", f"Google reported: {error[:120]}")
    if not code or not state:
        return _page("Gmail not connected", "Google did not return an authorization code.")

    claims = _verify_state(state)
    _require_oauth_client()

    try:
        response = httpx.post(
            config.GOOGLE_TOKEN_ENDPOINT,
            data={
                "code": code,
                "client_id": config.GOOGLE_OAUTH_CLIENT_ID,
                "client_secret": config.GOOGLE_OAUTH_CLIENT_SECRET,
                "redirect_uri": config.GOOGLE_OAUTH_REDIRECT_URI,
                "grant_type": "authorization_code",
            },
            timeout=config.GMAIL_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        return _page("Gmail not connected", "Aegis could not reach Google to exchange the code.")

    if response.status_code >= 400:
        # Google's body can contain the code and the client_id. Not surfaced,
        # not logged -- only the status is reported.
        return _page(
            "Gmail not connected",
            f"Google refused the authorization code (HTTP {response.status_code}).",
        )

    body = response.json()
    refresh_token = body.get("refresh_token")
    if not refresh_token:
        return _page(
            "Gmail not connected",
            "Google returned no refresh token. This usually means the account "
            "has already granted access; revoke Aegis at "
            "myaccount.google.com/permissions and connect again.",
        )

    granted = str(body.get("scope") or "").split()
    email = _lookup_email(body.get("access_token"))

    try:
        info = gmail_store.save_connection(
            organization_id=claims["org"],
            google_email=email,
            refresh_token=refresh_token,
            scopes=granted or list(config.GMAIL_OAUTH_SCOPES),
            connected_by=claims.get("user"),
        )
    except gmail_store.GmailStoreError as exc:
        return _page("Gmail not connected", str(exc))

    # The connector may be holding an access token from a previous connection.
    from ..protected.gmail import gmail_connector

    gmail_connector.forget_tokens(claims["org"])

    return _page(
        "Gmail connected",
        f"Aegis is connected to {info.google_email or 'the selected account'}. "
        "The credential is held server-side; no agent has access to it.",
    )


def _lookup_email(access_token: Optional[str]) -> str:
    """Which mailbox was connected, for the operator's benefit.

    Best effort: a failure here costs a display name, not the connection.
    """
    if not access_token:
        return ""
    try:
        response = httpx.get(
            f"{config.GMAIL_API_BASE_URL.rstrip('/')}/gmail/v1/users/me/profile",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=config.GMAIL_TIMEOUT_SECONDS,
        )
        if response.status_code >= 400:
            return ""
        return str(response.json().get("emailAddress") or "")
    except (httpx.HTTPError, ValueError):
        return ""


@router.post("/disconnect")
def disconnect(user: models.User = Depends(get_current_user)):
    """Forget the stored credential for this tenant.

    This deletes Aegis's copy. It does not revoke the grant at Google -- the
    operator does that at myaccount.google.com/permissions, and the response
    says so rather than implying a revocation that did not happen.
    """
    from ..protected.gmail import gmail_connector

    try:
        removed = gmail_store.disconnect(user.organization_id)
    except gmail_store.GmailStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    gmail_connector.forget_tokens(user.organization_id)
    return {
        "disconnected": removed,
        "note": (
            "Aegis has deleted its copy of the credential. To revoke the grant "
            "at Google as well, visit myaccount.google.com/permissions."
        ),
    }
