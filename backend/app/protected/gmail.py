"""The real Gmail connector.

Phase 19. This is the first protected service in Aegis that is not a mock: it
talks to https://gmail.googleapis.com with a real OAuth credential and produces
real side effects in a real mailbox.

FIVE OPERATIONS, AND NO SIXTH. The connector exposes exactly:

    search  read  draft  send  delete

Each one builds its own request from constants in this file. There is no
`execute(method, url, payload)`, no passthrough of a caller-supplied path, and
no way to reach a Gmail endpoint that is not written out below. That is the
point: an agent that can ask for "an operation" can only ask for one of five,
and the request Aegis authorized is the request Google receives.

WHAT THE AGENT NEVER SEES. The OAuth client secret, the refresh token and the
access token exist only inside this process. The access token is held in memory
for its lifetime and never written anywhere. Results returned to the caller are
built field by field from the Gmail response — the raw Google payload is never
forwarded, so a token cannot ride back out inside it.

TENANT BOUNDARY. Every call takes an organization_id and resolves *that
tenant's* credential. A message id belonging to another tenant's mailbox is not
reachable with this tenant's token: Google answers 404, and the connector
reports a clean not-found rather than reaching for another credential.

CONTENT. search and read return metadata and a bounded snippet, not whole
message bodies, because the evidence path and the model context are both places
private mail should not accumulate. read exposes the body only when the caller
explicitly asks, and truncates it.

LOGGING. Nothing in this module logs a token, an authorization code, a client
secret, or message content.
"""

from __future__ import annotations

import base64
import re
import threading
import time
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any, Callable, Optional

import httpx

from .. import config, gmail_store

# Every Gmail path this connector will ever request. Nothing is assembled from
# caller input except the id segments, which are validated first.
_PATH_SEARCH = "/gmail/v1/users/me/messages"
_PATH_MESSAGE = "/gmail/v1/users/me/messages/{message_id}"
_PATH_DRAFTS = "/gmail/v1/users/me/drafts"
_PATH_DRAFT_SEND = "/gmail/v1/users/me/drafts/send"
_PATH_SEND = "/gmail/v1/users/me/messages/send"
_PATH_TRASH = "/gmail/v1/users/me/messages/{message_id}/trash"
_PATH_PROFILE = "/gmail/v1/users/me/profile"

# Gmail ids are opaque but well-formed. Validating them keeps a crafted id from
# turning a fixed path into a different endpoint.
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

SNIPPET_LIMIT = 300
BODY_LIMIT = 4000
MAX_RESULTS_CAP = 25

SUPPORTED_OPERATIONS = ("search", "read", "draft", "send", "delete")


class GmailConnectorError(Exception):
    """A connector-level failure. Carries no credential material."""

    def __init__(self, message: str, *, status: int = 502, code: str = "gmail_error"):
        super().__init__(message)
        self.status = status
        self.code = code


class GmailNotConnected(GmailConnectorError):
    def __init__(self, message: str = "No Gmail account is connected for this organization"):
        super().__init__(message, status=409, code="gmail_not_connected")


class GmailNotFound(GmailConnectorError):
    def __init__(self, message: str = "Message not found in this mailbox"):
        super().__init__(message, status=404, code="gmail_not_found")


@dataclass
class _AccessToken:
    value: str
    expires_at: float

    def valid(self) -> bool:
        # 60s of slack so a token cannot expire mid-request.
        return bool(self.value) and time.time() < (self.expires_at - 60)


def _validate_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise GmailConnectorError(
            f"Invalid {label}", status=400, code="gmail_invalid_parameter"
        )
    return value


def _require_str(payload: dict, key: str, *, max_length: int) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise GmailConnectorError(
            f"Missing required parameter '{key}'",
            status=400,
            code="gmail_invalid_parameter",
        )
    if len(value) > max_length:
        raise GmailConnectorError(
            f"Parameter '{key}' is too long",
            status=400,
            code="gmail_invalid_parameter",
        )
    return value


def _headers_map(message: dict) -> dict[str, str]:
    headers = ((message.get("payload") or {}).get("headers")) or []
    wanted = {"from", "to", "cc", "subject", "date", "message-id"}
    out: dict[str, str] = {}
    for header in headers:
        if not isinstance(header, dict):
            continue
        name = str(header.get("name") or "").lower()
        if name in wanted:
            out[name] = str(header.get("value") or "")[:512]
    return out


def _decode_part(part: dict) -> str:
    data = ((part or {}).get("body") or {}).get("data")
    if not isinstance(data, str):
        return ""
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode(
            "utf-8", "replace"
        )
    except Exception:  # noqa: BLE001 - a body we cannot decode is simply absent
        return ""


def _plain_text_body(message: dict) -> str:
    payload = message.get("payload") or {}
    if str(payload.get("mimeType") or "").startswith("text/"):
        return _decode_part(payload)
    stack = list(payload.get("parts") or [])
    while stack:
        part = stack.pop(0)
        if not isinstance(part, dict):
            continue
        if str(part.get("mimeType") or "") == "text/plain":
            text = _decode_part(part)
            if text:
                return text
        stack.extend(part.get("parts") or [])
    return ""


def _build_mime(*, to: str, subject: str, body: str, cc: Optional[str] = None) -> str:
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject
    if cc:
        message["Cc"] = cc
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")


class GmailConnector:
    """Typed Gmail operations over a server-held OAuth credential.

    `transport` exists so tests can drive the connector without a live Google
    account. It replaces the HTTP call only: request construction, parameter
    validation, operation dispatch, result shaping and the credential path are
    the same code in both cases. A test using it is a test of this connector,
    not of a substitute for it, and no test that uses it may be described as
    real-Gmail verification.
    """

    def __init__(self, transport: Optional[Callable[..., httpx.Response]] = None):
        self._transport = transport
        self._tokens: dict[str, _AccessToken] = {}
        self._lock = threading.Lock()
        self.calls: list[dict[str, Any]] = []

    # -- credential path ---------------------------------------------------

    def _access_token(self, organization_id: str) -> str:
        """Exchange this tenant's refresh token for an access token.

        Returned to the caller of this private method only. Never persisted,
        never logged, never placed in a response.
        """
        with self._lock:
            cached = self._tokens.get(organization_id)
            if cached and cached.valid():
                return cached.value

        try:
            refresh_token = gmail_store.reveal_refresh_token(organization_id)
        except gmail_store.GmailStoreError as exc:
            raise GmailNotConnected(str(exc)) from exc

        if not config.GOOGLE_OAUTH_CLIENT_ID or not config.GOOGLE_OAUTH_CLIENT_SECRET:
            raise GmailConnectorError(
                "Google OAuth client is not configured",
                status=503,
                code="gmail_oauth_unconfigured",
            )

        response = self._request(
            "POST",
            config.GOOGLE_TOKEN_ENDPOINT,
            absolute=True,
            data={
                "client_id": config.GOOGLE_OAUTH_CLIENT_ID,
                "client_secret": config.GOOGLE_OAUTH_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        if response.status_code >= 400:
            # Google's error body can echo request parameters; it is not logged
            # and not forwarded.
            raise GmailConnectorError(
                "Google refused the stored credential",
                status=502,
                code="gmail_token_refresh_failed",
            )
        body = response.json()
        token = body.get("access_token")
        if not isinstance(token, str) or not token:
            raise GmailConnectorError(
                "Google returned no access token",
                status=502,
                code="gmail_token_refresh_failed",
            )
        expires_in = body.get("expires_in")
        lifetime = float(expires_in) if isinstance(expires_in, (int, float)) else 3600.0
        with self._lock:
            self._tokens[organization_id] = _AccessToken(
                value=token, expires_at=time.time() + lifetime
            )
        return token

    def forget_tokens(self, organization_id: Optional[str] = None) -> None:
        with self._lock:
            if organization_id is None:
                self._tokens.clear()
            else:
                self._tokens.pop(organization_id, None)

    # -- HTTP --------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        absolute: bool = False,
        token: Optional[str] = None,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
        data: Optional[dict] = None,
    ) -> httpx.Response:
        url = path if absolute else f"{config.GMAIL_API_BASE_URL.rstrip('/')}{path}"
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if self._transport is not None:
            return self._transport(
                method=method,
                url=url,
                headers=headers,
                params=params,
                json_body=json_body,
                data=data,
            )
        try:
            return httpx.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json_body,
                data=data,
                timeout=config.GMAIL_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise GmailConnectorError(
                "Gmail is unreachable", status=503, code="gmail_unreachable"
            ) from exc

    def _api(
        self,
        method: str,
        path: str,
        organization_id: str,
        *,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
    ) -> dict:
        token = self._access_token(organization_id)
        response = self._request(
            method, path, token=token, params=params, json_body=json_body
        )
        if response.status_code == 404:
            raise GmailNotFound()
        if response.status_code in (401, 403):
            self.forget_tokens(organization_id)
            raise GmailConnectorError(
                "Google refused the request for this mailbox",
                status=502,
                code="gmail_permission_denied",
            )
        if response.status_code >= 400:
            raise GmailConnectorError(
                f"Gmail returned {response.status_code}",
                status=502,
                code="gmail_error",
            )
        if not response.content:
            return {}
        try:
            body = response.json()
        except ValueError as exc:
            raise GmailConnectorError(
                "Gmail returned a malformed response", status=502
            ) from exc
        return body if isinstance(body, dict) else {"result": body}

    # -- typed operations --------------------------------------------------

    def search(self, organization_id: str, payload: dict) -> dict:
        """gmail.search — list message ids and headers matching a query."""
        query = payload.get("query") or payload.get("q") or ""
        if not isinstance(query, str) or len(query) > 512:
            raise GmailConnectorError(
                "Invalid search query", status=400, code="gmail_invalid_parameter"
            )
        raw_max = payload.get("max_results", 10)
        try:
            max_results = int(raw_max)
        except (TypeError, ValueError):
            max_results = 10
        max_results = max(1, min(max_results, MAX_RESULTS_CAP))

        listing = self._api(
            "GET",
            _PATH_SEARCH,
            organization_id,
            params={"q": query, "maxResults": max_results},
        )
        messages = listing.get("messages") or []
        results = []
        for item in messages[:max_results]:
            if not isinstance(item, dict):
                continue
            message_id = item.get("id")
            if not isinstance(message_id, str) or not _ID_RE.match(message_id):
                continue
            detail = self._api(
                "GET",
                _PATH_MESSAGE.format(message_id=message_id),
                organization_id,
                params={
                    "format": "metadata",
                    "metadataHeaders": ["From", "To", "Subject", "Date"],
                },
            )
            headers = _headers_map(detail)
            results.append(
                {
                    "message_id": message_id,
                    "thread_id": detail.get("threadId"),
                    "from": headers.get("from", ""),
                    "to": headers.get("to", ""),
                    "subject": headers.get("subject", ""),
                    "date": headers.get("date", ""),
                    "snippet": str(detail.get("snippet") or "")[:SNIPPET_LIMIT],
                }
            )
        return {
            "ok": True,
            "operation": "search",
            "organization_id": organization_id,
            "query": query,
            "count": len(results),
            "messages": results,
        }

    def read(self, organization_id: str, payload: dict) -> dict:
        """gmail.read — one message's headers, snippet and optional body."""
        message_id = _validate_id(payload.get("message_id"), "message_id")
        include_body = bool(payload.get("include_body"))
        detail = self._api(
            "GET",
            _PATH_MESSAGE.format(message_id=message_id),
            organization_id,
            params={"format": "full" if include_body else "metadata"}
            | (
                {}
                if include_body
                else {"metadataHeaders": ["From", "To", "Cc", "Subject", "Date"]}
            ),
        )
        headers = _headers_map(detail)
        result = {
            "ok": True,
            "operation": "read",
            "organization_id": organization_id,
            "message_id": message_id,
            "thread_id": detail.get("threadId"),
            "from": headers.get("from", ""),
            "to": headers.get("to", ""),
            "cc": headers.get("cc", ""),
            "subject": headers.get("subject", ""),
            "date": headers.get("date", ""),
            "label_ids": [
                str(label) for label in (detail.get("labelIds") or [])[:20]
            ],
            "snippet": str(detail.get("snippet") or "")[:SNIPPET_LIMIT],
        }
        if include_body:
            body = _plain_text_body(detail)
            result["body"] = body[:BODY_LIMIT]
            result["body_truncated"] = len(body) > BODY_LIMIT
        return result

    def draft(self, organization_id: str, payload: dict) -> dict:
        """gmail.draft — create a draft. Creates nothing that leaves the mailbox."""
        to = _require_str(payload, "to", max_length=320)
        subject = _require_str(payload, "subject", max_length=512)
        body = _require_str(payload, "body", max_length=BODY_LIMIT)
        cc = payload.get("cc") if isinstance(payload.get("cc"), str) else None
        raw = _build_mime(to=to, subject=subject, body=body, cc=cc)
        created = self._api(
            "POST",
            _PATH_DRAFTS,
            organization_id,
            json_body={"message": {"raw": raw}},
        )
        draft_id = created.get("id")
        return {
            "ok": True,
            "operation": "draft",
            "organization_id": organization_id,
            "draft_id": draft_id,
            "message_id": (created.get("message") or {}).get("id"),
            "to": to,
            "subject": subject,
            "sent": False,
        }

    def send(self, organization_id: str, payload: dict) -> dict:
        """gmail.send — actually send. The irreversible one.

        Two shapes: send an existing draft by id, or send a message built here.
        Both are ordinary sends to Google; neither accepts a caller-supplied
        endpoint.
        """
        draft_id = payload.get("draft_id")
        if isinstance(draft_id, str) and draft_id:
            _validate_id(draft_id, "draft_id")
            sent = self._api(
                "POST", _PATH_DRAFT_SEND, organization_id, json_body={"id": draft_id}
            )
            return {
                "ok": True,
                "operation": "send",
                "organization_id": organization_id,
                "message_id": sent.get("id"),
                "thread_id": sent.get("threadId"),
                "draft_id": draft_id,
                "sent": True,
            }
        to = _require_str(payload, "to", max_length=320)
        subject = _require_str(payload, "subject", max_length=512)
        body = _require_str(payload, "body", max_length=BODY_LIMIT)
        cc = payload.get("cc") if isinstance(payload.get("cc"), str) else None
        raw = _build_mime(to=to, subject=subject, body=body, cc=cc)
        sent = self._api("POST", _PATH_SEND, organization_id, json_body={"raw": raw})
        return {
            "ok": True,
            "operation": "send",
            "organization_id": organization_id,
            "message_id": sent.get("id"),
            "thread_id": sent.get("threadId"),
            "to": to,
            "subject": subject,
            "sent": True,
        }

    def delete(self, organization_id: str, payload: dict) -> dict:
        """gmail.delete — move a message to Trash.

        Implemented because the canonical operation exists and must be
        *refused* somewhere real, not because anything should reach it. Policy
        denies gmail.delete before the gateway dispatches, so in the canonical
        deployment this method is never entered; the Phase 19 tests assert that
        by counting connector calls, not by trusting the claim.

        Even if it were entered it uses trash, not permanent delete, and the
        OAuth scope Aegis requests does not grant messages.delete at all.
        """
        message_id = _validate_id(payload.get("message_id"), "message_id")
        self._api(
            "POST", _PATH_TRASH.format(message_id=message_id), organization_id
        )
        return {
            "ok": True,
            "operation": "delete",
            "organization_id": organization_id,
            "message_id": message_id,
            "trashed": True,
        }

    def profile(self, organization_id: str) -> dict:
        """Connection health. Used by the status endpoint, not by agents."""
        detail = self._api("GET", _PATH_PROFILE, organization_id)
        return {
            "ok": True,
            "email_address": detail.get("emailAddress", ""),
            "messages_total": detail.get("messagesTotal"),
        }

    # -- dispatch ----------------------------------------------------------

    def execute(
        self,
        operation: str,
        *,
        organization_id: str,
        payload: Optional[dict] = None,
    ) -> dict:
        """Dispatch one of the five supported operations. Nothing else.

        The name mirrors ProtectedCRM.execute so the broker path is unchanged,
        but the operation argument is matched against a fixed tuple: an
        unsupported name is rejected here, before any credential is touched.
        """
        op = (operation or "").strip().lower()
        if op not in SUPPORTED_OPERATIONS:
            raise GmailConnectorError(
                f"Unsupported Gmail operation '{op[:40]}'",
                status=400,
                code="gmail_unsupported_operation",
            )
        if not organization_id:
            raise GmailConnectorError(
                "Missing organization", status=400, code="gmail_missing_organization"
            )
        body = payload if isinstance(payload, dict) else {}
        handler = {
            "search": self.search,
            "read": self.read,
            "draft": self.draft,
            "send": self.send,
            "delete": self.delete,
        }[op]
        self.calls.append(
            {
                "operation": op,
                "organization_id": organization_id,
                # Parameter *names* only. Values can be message content.
                "parameters": sorted(str(key) for key in body.keys()),
            }
        )
        return handler(organization_id, body)

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def reset(self) -> None:
        self.calls.clear()
        self.forget_tokens()


gmail_connector = GmailConnector()


class InvalidConnectorCredential(GmailConnectorError):
    def __init__(self) -> None:
        super().__init__(
            "Gmail connector rejected credential for this organization",
            status=401,
            code="gmail_invalid_connector_credential",
        )


def authenticate_connector_credential(secret: str, organization_id: str) -> None:
    """Verify the broker's per-tenant credential before doing any Gmail work.

    Same rule as the CRM tool: the credential must be the one derived for the
    organization the call claims to be for, and the master credential is not
    accepted for calling. A broker presenting tenant A's credential while
    claiming tenant B fails here, before a Google token is ever unsealed.
    """
    import hmac as _hmac

    from ..credentials import CredentialAccessDenied, derive_tool_credential

    if not organization_id:
        raise GmailConnectorError(
            "Missing organization", status=400, code="gmail_missing_organization"
        )
    try:
        expected = derive_tool_credential("gmail", organization_id)
    except CredentialAccessDenied as exc:
        raise GmailConnectorError(
            "Gmail connector credential is not configured",
            status=503,
            code="gmail_credential_unconfigured",
        ) from exc
    if not _hmac.compare_digest(secret or "", expected):
        raise InvalidConnectorCredential()
