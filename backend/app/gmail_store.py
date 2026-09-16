"""Where Gmail's protected credentials live.

Phase 19. The agent must never hold a Gmail credential, so something on the
server side has to. This is that something.

DELIBERATE SHAPE. The store is a file, not a database table, and the file is
mounted on exactly two containers: the control plane, which writes it when an
operator completes the OAuth consent, and the Gmail connector, which reads it
to call Google. It is mounted on neither the enforcement gateway nor the
credential broker, and certainly not on the agent. `docker-compose.yml` is the
enforcement of that statement; tests/test_phase19_credential_isolation.py is
the check that it stays true.

The refresh token is sealed with AEGIS_OAUTH_ENCRYPTION_KEY (see secretbox.py)
and bound to the organization it belongs to, so lifting org A's record into org
B's slot produces an authentication failure rather than a working credential.

Access tokens are never written here. They are fetched from Google when needed
and held in memory only, so the file never accumulates short-lived bearer
tokens, and a process restart cannot resurrect one.

NOTHING IN THIS MODULE LOGS TOKEN MATERIAL, and callers must keep it that way:
the public read path returns a ConnectionInfo without the secret, and the only
function that returns a refresh token is named so that its use is obvious.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import config
from .secretbox import SecretBoxError, open_sealed, seal

SCHEMA = "aegis.gmail_connections/v1"


class GmailStoreError(Exception):
    """A store operation failed. Never carries token material."""


@dataclass(frozen=True)
class ConnectionInfo:
    """Everything about a Gmail connection *except* the credential."""

    organization_id: str
    google_email: str
    scopes: tuple[str, ...]
    connected_at: str
    connected_by: Optional[str]
    status: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "organization_id": self.organization_id,
            "google_email": self.google_email,
            "scopes": list(self.scopes),
            "connected_at": self.connected_at,
            "connected_by": self.connected_by,
            "status": self.status,
        }


def _path() -> Path:
    return Path(config.GMAIL_OAUTH_STORE_PATH)


def _read_all() -> dict[str, Any]:
    path = _path()
    if not path.exists():
        return {"schema": SCHEMA, "connections": {}}
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise GmailStoreError("Gmail connection store is unreadable") from exc
    if not isinstance(data, dict) or "connections" not in data:
        raise GmailStoreError("Gmail connection store is malformed")
    return data


def _write_all(data: dict[str, Any]) -> None:
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write a new file and rename it into place, so a crash mid-write
        # cannot leave a half-written credential store behind.
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=str(path.parent), delete=False
        )
        try:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            handle.close()
        os.chmod(handle.name, 0o600)
        os.replace(handle.name, path)
    except OSError as exc:
        raise GmailStoreError("Gmail connection store is not writable") from exc


def _context(organization_id: str) -> str:
    """Seal context binds a record to its tenant.

    Moving org A's sealed blob into org B's entry makes the open fail, so a
    cross-tenant copy inside the store is not a usable credential.
    """
    return f"aegis-gmail-refresh-token:v1:{organization_id}"


def save_connection(
    *,
    organization_id: str,
    google_email: str,
    refresh_token: str,
    scopes: list[str],
    connected_by: Optional[str],
) -> ConnectionInfo:
    if not organization_id:
        raise GmailStoreError("Missing organization")
    if not refresh_token:
        raise GmailStoreError("Google did not return a refresh token")
    if not config.GMAIL_OAUTH_ENCRYPTION_KEY:
        raise GmailStoreError("AEGIS_OAUTH_ENCRYPTION_KEY is not configured")
    try:
        sealed = seal(
            refresh_token,
            config.GMAIL_OAUTH_ENCRYPTION_KEY,
            context=_context(organization_id),
        )
    except SecretBoxError as exc:
        raise GmailStoreError("Could not seal the Google credential") from exc

    data = _read_all()
    connected_at = datetime.now(timezone.utc).isoformat()
    data["connections"][organization_id] = {
        "organization_id": organization_id,
        "google_email": google_email,
        "scopes": sorted({str(item) for item in scopes}),
        "connected_at": connected_at,
        "connected_by": connected_by,
        "status": "connected",
        "sealed_refresh_token": sealed,
    }
    _write_all(data)
    return ConnectionInfo(
        organization_id=organization_id,
        google_email=google_email,
        scopes=tuple(sorted({str(item) for item in scopes})),
        connected_at=connected_at,
        connected_by=connected_by,
        status="connected",
    )


def get_connection(organization_id: str) -> Optional[ConnectionInfo]:
    """Public read. Returns metadata only — never the credential."""
    if not organization_id:
        return None
    record = _read_all()["connections"].get(organization_id)
    if not isinstance(record, dict):
        return None
    return ConnectionInfo(
        organization_id=record.get("organization_id", organization_id),
        google_email=record.get("google_email", ""),
        scopes=tuple(record.get("scopes") or ()),
        connected_at=record.get("connected_at", ""),
        connected_by=record.get("connected_by"),
        status=record.get("status", "connected"),
    )


def reveal_refresh_token(organization_id: str) -> str:
    """Unseal the refresh token. Connector-side use only.

    Named to make every call site obvious in review. The result must never be
    returned to an HTTP caller, written to a response, or logged.
    """
    record = _read_all()["connections"].get(organization_id)
    if not isinstance(record, dict):
        raise GmailStoreError("No Gmail connection for this organization")
    if record.get("status") != "connected":
        raise GmailStoreError("Gmail connection is not active")
    sealed = record.get("sealed_refresh_token")
    if not sealed:
        raise GmailStoreError("Gmail connection has no stored credential")
    if not config.GMAIL_OAUTH_ENCRYPTION_KEY:
        raise GmailStoreError("AEGIS_OAUTH_ENCRYPTION_KEY is not configured")
    try:
        return open_sealed(
            sealed,
            config.GMAIL_OAUTH_ENCRYPTION_KEY,
            context=_context(organization_id),
        )
    except SecretBoxError as exc:
        # Wrong key, tampered file, or a record copied from another tenant.
        raise GmailStoreError("Stored Gmail credential failed authentication") from exc


def disconnect(organization_id: str) -> bool:
    """Forget the credential entirely. Returns True if there was one."""
    data = _read_all()
    if organization_id not in data["connections"]:
        return False
    del data["connections"][organization_id]
    _write_all(data)
    return True


def connected_organizations() -> list[str]:
    return sorted(_read_all()["connections"].keys())
