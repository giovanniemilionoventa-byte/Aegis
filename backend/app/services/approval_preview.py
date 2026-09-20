"""Phase 20 -- what the human is actually approving.

Until now an approval showed the reviewer a hash. "Approve this request" meant
trusting that the agent's description of it was honest, and an approver who
cannot see what they approve is a rubber stamp; users of permission prompts
approve almost all of them. The reviewer needs the recipients, the subject and
the start of the body in front of them.

That is message content, and the rest of Aegis deliberately keeps none. So the
preview is kept the way sensitive material should be:

  * built by the server from the payload it is about to authorize, never taken
    from anything the agent labels as a summary;
  * sealed at rest, bound to this organization and this approval, so a preview
    copied onto another row reads as nothing;
  * kept OUT of the evidence chain (the chain hashes the payload, not this);
  * deleted a fixed time after the decision, or after the request expired.

It is short on purpose: recipients, subject and 280 characters of body. Anything
that looks like a credential is masked.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import update
from sqlalchemy.orm import Session

from .. import config, models
from ..engines import destination
from ..runtime_contract import coerce_utc
from ..secretbox import SecretBoxError, open_sealed, seal
from ..security import utcnow

BODY_EXCERPT = 280
SUBJECT_MAX = 200
FIELD_VALUE_MAX = 160
MAX_FIELDS = 8
MASK = "•••"

# A field whose name contains one of these is shown as MASK, never its value.
SENSITIVE_NAMES = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "ssn",
    "iban",
    "card",
)
BODY_FIELDS = ("body", "text", "content", "message", "html")


def as_utc(value: Any) -> Optional[datetime]:
    return coerce_utc(value)


def _key() -> str:
    # Its own key, derived from the deployment secret, so nothing else that uses
    # SECRET_KEY can be confused with (or open) a preview.
    return hashlib.sha256(b"aegis-approval-preview:" + config.SECRET_KEY.encode()).hexdigest()


def _context(organization_id: str, approval_id: str) -> str:
    return f"approval-preview:{organization_id}:{approval_id}"


def _clip(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _first_text(payload: dict, names: tuple[str, ...]) -> Optional[str]:
    lowered = {str(key).lower(): value for key, value in payload.items()}
    for name in names:
        if isinstance(lowered.get(name), str) and lowered[name].strip():
            return lowered[name]
    return None


def build_preview(kind: str, action: str, payload: Any) -> Optional[dict]:
    """A short, safe description of a payload for the person deciding on it."""
    if not isinstance(payload, dict) or not payload:
        return None
    if (kind.lower(), action.upper()) in destination.SEND_ACTIONS:
        preview: dict = {
            field: found
            for field, found in destination.recipients_by_field(payload).items()
            if found
        }
        subject = _first_text(payload, ("subject",))
        if subject:
            preview["subject"] = _clip(subject, SUBJECT_MAX)
        body = _first_text(payload, BODY_FIELDS)
        if body:
            preview["body_excerpt"] = _clip(body, BODY_EXCERPT)
        return preview or None

    fields: dict[str, str] = {}
    for name, value in list(payload.items())[:MAX_FIELDS]:
        if any(marker in str(name).lower() for marker in SENSITIVE_NAMES):
            fields[str(name)] = MASK
        else:
            fields[str(name)] = _clip(value, FIELD_VALUE_MAX)
    return {"fields": fields}


def attach(approval: models.Approval, kind: str, action: str, payload: Any) -> None:
    """Seal a preview onto a freshly created approval (id and expiry already set)."""
    preview = build_preview(kind, action, payload)
    if preview is None:
        return
    approval.preview_sealed = seal(
        json.dumps(preview, ensure_ascii=False, separators=(",", ":")),
        _key(),
        context=_context(approval.organization_id, approval.id),
    )
    expires = coerce_utc(approval.expires_at) or utcnow()
    approval.preview_purge_at = expires + timedelta(
        days=config.APPROVAL_PREVIEW_RETENTION_DAYS
    )


def open_for(approval: models.Approval) -> Optional[dict]:
    """The preview, or None if there is none, it was purged, or it does not verify."""
    if not approval.preview_sealed:
        return None
    try:
        return json.loads(
            open_sealed(
                approval.preview_sealed,
                _key(),
                context=_context(approval.organization_id, approval.id),
            )
        )
    except (SecretBoxError, ValueError):
        return None


def purge_expired(db: Session, now: Optional[datetime] = None) -> int:
    """Delete every preview whose retention has run out. Returns how many."""
    result = db.execute(
        update(models.Approval)
        .where(
            models.Approval.preview_sealed.isnot(None),
            models.Approval.preview_purge_at.isnot(None),
            models.Approval.preview_purge_at < (now or utcnow()),
        )
        .values(preview_sealed=None)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount:
        db.commit()
    return result.rowcount or 0
