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
import threading
import time
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import config, gmail_store, models
from ..database import get_db
from ..security import get_current_user
from ..services import gmail_access

router = APIRouter(prefix="/gmail", tags=["gmail"])

STATE_TTL_SECONDS = 600
_STATE_CONTEXT = b"aegis-gmail-oauth-state:v1"

# Phase 20. Starting OAuth is a browser NAVIGATION, which carries no
# Authorization header, so the session token used to travel in the URL, where it
# lands in history, proxy logs and Referer headers. Now the signed-in dashboard
# asks for a TICKET: random, good for one use and a minute, held in memory. The
# URL carries the ticket and never the token. (In-process on purpose: the
# control plane is one process; a restart inside that minute just means "start
# again".)
TICKET_TTL_SECONDS = 60
NONCE_COOKIE = "aegis_oauth_nonce"
_tickets: dict[str, tuple[str, float]] = {}  # ticket -> (user id, expires at)
_used_nonces: dict[str, float] = {}  # nonce -> remembered until
_memory_lock = threading.Lock()


def _prune(now: float) -> None:
    for ticket in [t for t, (_, expires) in _tickets.items() if expires <= now]:
        del _tickets[ticket]
    for nonce in [n for n, expires in _used_nonces.items() if expires <= now]:
        del _used_nonces[nonce]


def _issue_ticket(user_id: str) -> str:
    ticket = secrets.token_urlsafe(32)
    now = time.time()
    with _memory_lock:
        _prune(now)
        _tickets[ticket] = (user_id, now + TICKET_TTL_SECONDS)
    return ticket


def _redeem_ticket(ticket: str) -> Optional[str]:
    """The user a ticket was issued to, once. A second use, or a late one, gets None."""
    now = time.time()
    with _memory_lock:
        _prune(now)
        entry = _tickets.pop(ticket, None)
    return entry[0] if entry else None


def _consume_nonce(nonce: str, remember_until: float) -> bool:
    """True the first time a nonce is seen, False on a replay of the same state."""
    now = time.time()
    with _memory_lock:
        _prune(now)
        if nonce in _used_nonces:
            return False
        _used_nonces[nonce] = max(remember_until, now)
    return True


class ConnectStart(BaseModel):
    authorization_url: str
    expires_in: int


def _b64(raw: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    import base64

    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _sign_state(organization_id: str, user_id: str, nonce: Optional[str] = None) -> str:
    """A state parameter Google hands back, that only Aegis could have made.

    Carries the tenant and the operator so the callback knows whose mailbox is
    being connected without trusting a query parameter, and an expiry so a
    stale authorization link cannot be replayed a day later.

    The nonce is also set as a cookie in the browser that started the flow, and
    the callback wants both. The signature proves Aegis made the state; the
    cookie proves this browser is the one that asked for it. Without that, anyone
    holding a state could finish the flow in someone else's session.
    """
    payload = {
        "org": organization_id,
        "user": user_id,
        "nonce": nonce or secrets.token_urlsafe(16),
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
    grants = gmail_access.agents_with_access(db, user.organization_id)
    names = {
        row.id: row.name
        for row in db.query(models.Agent)
        .filter(models.Agent.organization_id == user.organization_id)
        .all()
    }
    return {
        "oauth_client_configured": configured,
        "encryption_key_configured": bool(config.GMAIL_OAUTH_ENCRYPTION_KEY),
        "connected": info is not None,
        "connection": info.as_dict() if info else None,
        "requested_scopes": list(config.GMAIL_OAUTH_SCOPES),
        # Phase 19.1: connecting a mailbox gives no agent access to it. This
        # list is who actually has it, so an operator is never guessing.
        "agents_with_access": [
            {
                "agent_id": row.agent_id,
                "agent_name": names.get(row.agent_id, "(unknown agent)"),
                "granted_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in grants
        ],
    }


def _authorization_url(user: models.User, nonce: str) -> str:
    """Build the Google consent URL.

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
        "state": _sign_state(user.organization_id, user.id, nonce),
    }
    return f"{config.GOOGLE_AUTH_ENDPOINT}?{urlencode(params)}"


@router.get("/oauth/start")
def start_oauth_redirect(
    ticket: str = Query(..., description="Single-use ticket from POST /oauth/start."),
    db: Session = Depends(get_db),
):
    """Send the operator's browser to Google, from the server.

    Phase 19.1. The dashboard previously received the authorization URL as JSON
    and redirected itself, which meant the Google client id passed through the
    frontend. It does not any more: the browser is sent here and the server
    answers 302.

    HONEST LIMIT. The client id is still visible in the address bar during
    consent, because it is a query parameter of Google's own authorization
    endpoint — that is how OAuth works, and Google treats the client id as
    public. What changed is that the dashboard neither receives nor configures
    it. The client SECRET has never left the server and still does not.

    Phase 20. What proves who is asking is a ticket, not the session token: a
    browser navigation carries no Authorization header, and a session token in a
    URL is a session token in history and logs. The ticket was issued to an
    authenticated operator a moment ago, works once, and is worthless after a
    minute. Agent credentials cannot reach this router at all.
    """
    user_id = _redeem_ticket(ticket)
    user = db.query(models.User).filter(models.User.id == user_id).first() if user_id else None
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    nonce = secrets.token_urlsafe(16)
    response = RedirectResponse(url=_authorization_url(user, nonce), status_code=302)
    response.set_cookie(
        NONCE_COOKIE,
        nonce,
        max_age=STATE_TTL_SECONDS,
        httponly=True,
        samesite="lax",  # sent on Google's top-level redirect back, not on cross-site requests
        secure=config.is_production(),
        path="/api/gmail/oauth",
    )
    return response


@router.post("/oauth/start", response_model=ConnectStart)
def start_oauth(user: models.User = Depends(get_current_user)):
    """The URL the dashboard should navigate to: an Aegis route, not Google's.

    Kept so the dashboard has one call that both checks configuration and
    returns somewhere to go. What it returns is this deployment's own
    /api/gmail/oauth/start with a one-use ticket, which then redirects.
    """
    _require_oauth_client()
    return ConnectStart(
        authorization_url=f"/api/gmail/oauth/start?ticket={_issue_ticket(user.id)}",
        expires_in=TICKET_TTL_SECONDS,
    )


@router.get("/oauth/callback")
def oauth_callback(
    request: Request,
    code: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    error: Optional[str] = Query(default=None),
):
    """Google's redirect. Exchanges the code for a refresh token and seals it.

    Entered by the operator's browser, so there is no Authorization header to
    check; the signed state is what establishes which tenant this is for. The
    code is used once, here, and is never logged or stored.

    Phase 20. The state alone was not enough: it is a bearer value, so whoever
    held one could finish the flow in a different browser and attach their own
    mailbox to someone else's organization. The callback now also requires the
    cookie the start route set in the browser that began the flow, and a state
    is accepted once.
    """
    if error:
        return _page("Gmail not connected", f"Google reported: {error[:120]}")
    if not code or not state:
        return _page("Gmail not connected", "Google did not return an authorization code.")

    claims = _verify_state(state)
    nonce = str(claims.get("nonce") or "")
    cookie = request.cookies.get(NONCE_COOKIE, "")
    if (
        not nonce
        or not cookie
        or not hmac.compare_digest(cookie, nonce)
        or not _consume_nonce(nonce, float(claims.get("exp", 0)))
    ):
        return _page(
            "Gmail not connected",
            "This authorization was not started from this browser, or it was already "
            "used. Start again from the Aegis dashboard.",
        )
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
def disconnect(
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
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
    # Phase 19.1: drop every agent's access too. Leaving grants behind would
    # mean that connecting a *different* mailbox later silently re-armed every
    # agent that had access to the old one.
    dropped = gmail_access.revoke_all_for_organization(db, user.organization_id)
    return {
        "disconnected": removed,
        "agent_grants_revoked": dropped,
        "note": (
            "Aegis has deleted its copy of the credential and revoked every "
            "agent's access to it. To revoke the grant at Google as well, "
            "visit myaccount.google.com/permissions."
        ),
    }


# ---------------------------------------------------------------------------
# Per-agent mailbox access
#
# The connection is tenant-scoped; permission to use it is per agent. Every
# route below resolves the agent through the caller's own organization, so
# there is no agent_id an operator could send that reaches another tenant's
# agent — the lookup simply returns nothing and the route 404s.
#
# There is deliberately no connection_id parameter anywhere. The connection is
# the tenant's, resolved server-side; a caller-supplied connection identifier
# is precisely the field a cross-tenant attack would aim at.
# ---------------------------------------------------------------------------


def _agent_or_404(db: Session, user: models.User, agent_id: str) -> models.Agent:
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


@router.get("/agents/{agent_id}")
def agent_gmail_status(
    agent_id: str,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Can this specific agent use the mailbox, and why or why not."""
    agent = _agent_or_404(db, user, agent_id)
    access = gmail_access.evaluate(db, agent)
    return {
        "agent_id": agent.id,
        "agent_name": agent.name,
        "agent_status": agent.status,
        "connected": access.connected,
        "granted": access.granted,
        "allowed": access.allowed,
        "reason": access.reason,
        "google_email": access.google_email,
    }


@router.post("/agents/{agent_id}/grant")
def grant_agent_access(
    agent_id: str,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Let this agent use the tenant's connected mailbox.

    Refuses when there is nothing to grant access to, rather than recording a
    grant that would mislead an operator into thinking the agent is ready.
    """
    agent = _agent_or_404(db, user, agent_id)
    try:
        connection = gmail_store.get_connection(user.organization_id)
    except gmail_store.GmailStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if connection is None or connection.status != "connected":
        raise HTTPException(
            status_code=409,
            detail="No Gmail mailbox is connected for this organization",
        )
    if agent.status != "active":
        raise HTTPException(
            status_code=409, detail="Agent is revoked and cannot be granted access"
        )
    row = gmail_access.grant(
        db,
        organization_id=user.organization_id,
        agent_id=agent.id,
        granted_by=user.id,
        google_email=connection.google_email,
    )
    return {
        "agent_id": agent.id,
        "granted": True,
        "google_email": row.google_email,
        "granted_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.post("/agents/{agent_id}/revoke")
def revoke_agent_access(
    agent_id: str,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Take the mailbox away from this agent, leaving the connection intact."""
    agent = _agent_or_404(db, user, agent_id)
    revoked = gmail_access.revoke(
        db, organization_id=user.organization_id, agent_id=agent.id
    )
    return {"agent_id": agent.id, "granted": False, "revoked": revoked}
