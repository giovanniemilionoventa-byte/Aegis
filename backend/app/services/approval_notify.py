"""Phase 20 -- tell the reviewer, and let them answer from a phone.

An approval nobody sees is a request that times out. When an agent asks for a
human, the organization's admins get an email with a link. The link opens a page
with what is being approved and two buttons; no login, because the person is
standing in a corridor with a phone.

WHAT THE LINK IS. A credential for exactly one thing: it names one approval, one
organization and one admin, is signed with a key derived from the deployment
secret, and dies with the request. It can decide that approval once (a decided
request refuses a second answer) and nothing else. It is a bearer link: whoever
has it can answer, which is the same trust as any "reply to approve" email.

WHAT THE EMAIL IS NOT. It carries no message content: no recipient, no subject,
no body. It says which agent is waiting and gives the link; the content is shown
on the page, behind the link. Delivery is best effort and never blocks or fails a
request: with no mail server configured nothing is sent and the dashboard still
shows everything.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import smtplib
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Optional

from .. import config, models
from ..database import SessionLocal
from ..runtime_contract import coerce_utc
from ..security import utcnow

log = logging.getLogger("aegis.notify")


# ---------------------------------------------------------------------------
# The link
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LinkClaims:
    approval_id: str
    organization_id: str
    user_id: str
    expires_at: int


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _sign(body: str) -> bytes:
    key = hashlib.sha256(b"aegis-approval-link:" + config.SECRET_KEY.encode()).digest()
    return hmac.new(key, b"approval-link:" + body.encode(), hashlib.sha256).digest()


def make_link_token(
    approval_id: str, organization_id: str, user_id: str, expires_at: datetime
) -> str:
    claims = {
        "a": approval_id,
        "o": organization_id,
        "u": user_id,
        "e": int(coerce_utc(expires_at).timestamp()),
    }
    body = _b64(json.dumps(claims, separators=(",", ":")).encode())
    return f"{body}.{_b64(_sign(body))}"


def verify_link_token(token: str, now: Optional[datetime] = None) -> Optional[LinkClaims]:
    """The claims if the token is genuine and unexpired; otherwise None."""
    try:
        body, signature = token.split(".", 1)
        if not hmac.compare_digest(_b64(_sign(body)), signature):
            return None
        claims = json.loads(_b64decode(body))
        expires = int(claims["e"])
        if expires < (now or utcnow()).timestamp():
            return None
        return LinkClaims(
            approval_id=str(claims["a"]),
            organization_id=str(claims["o"]),
            user_id=str(claims["u"]),
            expires_at=expires,
        )
    except (ValueError, KeyError, TypeError):
        return None


# ---------------------------------------------------------------------------
# The email
# ---------------------------------------------------------------------------


def send_mail(*, to: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = config.SMTP_FROM or config.SMTP_USER
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    if config.SMTP_PORT == 465:
        smtp = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=10)
    else:
        smtp = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=10)
    with smtp:
        if config.SMTP_PORT != 465 and config.SMTP_STARTTLS:
            smtp.starttls()
        if config.SMTP_USER:
            smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
        smtp.send_message(message)


def _compose(agent_name: str, approval: models.Approval, link: str, expires: datetime):
    where = {"internal": " (destinatari interni)", "external": " (destinatari esterni)"}.get(
        (approval.destination or "").lower(), ""
    )
    subject = f"Aegis: {agent_name} aspetta il tuo OK"
    body = (
        f"{agent_name} vuole eseguire: {approval.resource_kind}.{approval.action}{where}.\n\n"
        "Apri il link per vedere cosa sta per fare e decidere (Approva o Rifiuta):\n"
        f"{link}\n\n"
        f"Il link vale fino alle {expires.strftime('%H:%M')} UTC e si usa una volta sola.\n"
        "Se non riconosci questa richiesta ignora il messaggio: scade da sola.\n"
    )
    return subject, body


def deliver_now(approval_id: str) -> int:
    """Email every admin of the organization. Returns how many were sent."""
    if not (config.SMTP_HOST and config.PUBLIC_APP_URL):
        return 0
    db = SessionLocal()
    try:
        approval = db.get(models.Approval, approval_id)
        if approval is None or (approval.status or "").lower() != "pending":
            return 0
        expires = coerce_utc(approval.expires_at) or utcnow() + timedelta(minutes=15)
        if utcnow() >= expires:
            return 0
        agent = db.get(models.Agent, approval.agent_id)
        admins = (
            db.query(models.User)
            .filter(
                models.User.organization_id == approval.organization_id,
                models.User.role == "admin",
            )
            .all()
        )
        sent = 0
        for user in admins:
            token = make_link_token(approval.id, approval.organization_id, user.id, expires)
            link = f"{config.PUBLIC_APP_URL}/a/{token}"
            subject, body = _compose(agent.name if agent else "Un agente", approval, link, expires)
            send_mail(to=user.email, subject=subject, body=body)
            sent += 1
        return sent
    finally:
        db.close()


def _deliver_quietly(approval_id: str) -> None:
    try:
        deliver_now(approval_id)
    except Exception:  # noqa: BLE001 - never let a mail problem reach a request
        # Deliberately no exception text: an SMTP error can quote an address.
        log.warning("approval notification failed for approval %s", approval_id)


def enqueue(approval_id: str) -> None:
    """Send in the background. Does nothing unless mail and a public URL are set."""
    if not (config.SMTP_HOST and config.PUBLIC_APP_URL):
        return
    threading.Thread(
        target=_deliver_quietly, args=(approval_id,), daemon=True, name="aegis-notify"
    ).start()
