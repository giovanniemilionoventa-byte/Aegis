"""A stand-in for Google, so the real connector can be driven in tests.

READ THIS BEFORE CITING ANY TEST THAT USES IT.

What this replaces is the HTTP call and nothing else. The connector under test
is app/protected/gmail.py — the same request construction, the same parameter
validation, the same five-operation dispatch, the same credential path, the
same result shaping. This object answers those requests the way Google answers
them, and records what it was asked to do so a test can assert on real side
effects instead of on return values.

What it is NOT: it is not a second connector, and a test that uses it is NOT
real-Gmail verification. Every claim proved with this harness is labelled
"connector verified against a Gmail API stand-in" and never "verified against
real Gmail". Phase 19's real-Gmail verification requires a human-supplied
Google account and is tracked separately in docs/PHASE_19_GMAIL.md.
"""

from __future__ import annotations

import base64
import json
from typing import Any, Optional

import httpx


def _response(status: int, body: Any) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        content=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        request=httpx.Request("POST", "https://stand-in.invalid/"),
    )


class FakeGoogle:
    """A mailbox, an OAuth token endpoint, and a ledger of real side effects."""

    DEFAULT_REFRESH_TOKEN = "stand-in-refresh-token"

    def __init__(self) -> None:
        self.access_token = "stand-in-access-token"
        self.refresh_calls = 0
        self.rejected_refresh_tokens: set[str] = set()
        # Side effects a test can assert actually happened.
        self.sent: list[dict] = []
        self.drafts: dict[str, dict] = {}
        self.trashed: list[str] = []
        self.requests: list[tuple[str, str]] = []
        self._draft_seq = 0
        self._message_seq = 0
        # One mailbox per credential. A message id in mailbox A is simply not
        # present in mailbox B, so a cross-tenant read fails the way it fails
        # at Google -- 404 -- rather than by a check this harness invented.
        self._mailboxes: dict[str, dict[str, dict]] = {self.access_token: {}}
        self._access_for_refresh: dict[str, str] = {
            self.DEFAULT_REFRESH_TOKEN: self.access_token
        }
        self._active = self.access_token

    def add_mailbox(self, refresh_token: str, access_token: str) -> None:
        """Register a second credential with a mailbox of its own."""
        self._access_for_refresh[refresh_token] = access_token
        self._mailboxes.setdefault(access_token, {})

    def mailbox(self, access_token: Optional[str] = None) -> dict[str, dict]:
        return self._mailboxes.setdefault(access_token or self.access_token, {})

    @property
    def messages(self) -> dict[str, dict]:
        """The default mailbox, for the single-tenant tests."""
        return self.mailbox(self.access_token)

    # -- mailbox setup -----------------------------------------------------

    def add_message(
        self,
        *,
        message_id: str,
        sender: str,
        to: str = "me@example.test",
        subject: str = "",
        body: str = "",
        thread_id: Optional[str] = None,
        access_token: Optional[str] = None,
    ) -> str:
        self.mailbox(access_token)[message_id] = {
            "id": message_id,
            "threadId": thread_id or f"t-{message_id}",
            "snippet": body[:100],
            "labelIds": ["INBOX"],
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": sender},
                    {"name": "To", "value": to},
                    {"name": "Subject", "value": subject},
                    {"name": "Date", "value": "Tue, 16 Sep 2026 10:00:00 +0000"},
                ],
                "body": {
                    "data": base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
                },
            },
        }
        return message_id

    # -- transport ---------------------------------------------------------

    def __call__(
        self,
        *,
        method: str,
        url: str,
        headers: dict,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
        data: Optional[dict] = None,
    ) -> httpx.Response:
        self.requests.append((method, url))

        if "oauth2" in url or url.endswith("/token"):
            return self._token(data or {})

        bearer = (headers or {}).get("Authorization", "")
        presented = bearer[7:] if bearer.startswith("Bearer ") else ""
        if presented not in self._mailboxes:
            return _response(401, {"error": "unauthorized"})
        self._active = presented

        path = url.split("gmail/v1/users/me", 1)[-1] if "gmail/v1" in url else url

        if path == "/profile":
            return _response(
                200,
                {
                    "emailAddress": "mailbox@example.test",
                    "messagesTotal": len(self.mailbox(self._active)),
                },
            )
        if path == "/messages" and method == "GET":
            return self._list(params or {})
        if path == "/messages/send":
            return self._send(json_body or {})
        if path == "/drafts" and method == "POST":
            return self._create_draft(json_body or {})
        if path == "/drafts/send":
            return self._send_draft(json_body or {})
        if path.startswith("/messages/") and path.endswith("/trash"):
            return self._trash(path.split("/")[2])
        if path.startswith("/messages/") and method == "GET":
            return self._get(path.split("/")[2], params or {})

        # Anything else is an endpoint the connector is not supposed to reach.
        return _response(404, {"error": "no such endpoint in the stand-in"})

    # -- endpoints ---------------------------------------------------------

    def _token(self, form: dict) -> httpx.Response:
        self.refresh_calls += 1
        supplied = form.get("refresh_token")
        if supplied in self.rejected_refresh_tokens:
            return _response(400, {"error": "invalid_grant"})
        granted = self._access_for_refresh.get(supplied)
        if granted is None:
            # An unknown refresh token is not a working credential. This is how
            # a guessed or lifted token fails.
            return _response(400, {"error": "invalid_grant"})
        return _response(200, {"access_token": granted, "expires_in": 3600})

    def _list(self, params: dict) -> httpx.Response:
        query = str(params.get("q") or "").lower()
        matches = []
        for message in self.mailbox(self._active).values():
            headers = {
                h["name"].lower(): h["value"] for h in message["payload"]["headers"]
            }
            haystack = " ".join(
                [headers.get("from", ""), headers.get("subject", ""), message["snippet"]]
            ).lower()
            if not query or all(term in haystack for term in query.split()):
                matches.append({"id": message["id"], "threadId": message["threadId"]})
        return _response(200, {"messages": matches, "resultSizeEstimate": len(matches)})

    def _get(self, message_id: str, params: dict) -> httpx.Response:
        message = self.mailbox(self._active).get(message_id)
        if message is None:
            return _response(404, {"error": "not found"})
        if params.get("format") == "full":
            return _response(200, message)
        stripped = dict(message)
        stripped["payload"] = {
            "mimeType": message["payload"]["mimeType"],
            "headers": message["payload"]["headers"],
        }
        return _response(200, stripped)

    def _decode_raw(self, raw: str) -> str:
        return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode(
            "utf-8", "replace"
        )

    def _create_draft(self, body: dict) -> httpx.Response:
        self._draft_seq += 1
        draft_id = f"d-{self._draft_seq}"
        raw = ((body.get("message") or {}).get("raw")) or ""
        self.drafts[draft_id] = {"raw": self._decode_raw(raw), "sent": False}
        return _response(
            200, {"id": draft_id, "message": {"id": f"m-draft-{self._draft_seq}"}}
        )

    def _send_draft(self, body: dict) -> httpx.Response:
        draft_id = body.get("id")
        draft = self.drafts.get(draft_id)
        if draft is None:
            return _response(404, {"error": "no such draft"})
        self._message_seq += 1
        message_id = f"m-sent-{self._message_seq}"
        draft["sent"] = True
        self.sent.append({"message_id": message_id, "raw": draft["raw"], "from_draft": draft_id})
        return _response(200, {"id": message_id, "threadId": f"t-{message_id}"})

    def _send(self, body: dict) -> httpx.Response:
        self._message_seq += 1
        message_id = f"m-sent-{self._message_seq}"
        self.sent.append(
            {"message_id": message_id, "raw": self._decode_raw(body.get("raw") or "")}
        )
        return _response(200, {"id": message_id, "threadId": f"t-{message_id}"})

    def _trash(self, message_id: str) -> httpx.Response:
        if message_id not in self.mailbox(self._active):
            return _response(404, {"error": "not found"})
        self.trashed.append(message_id)
        return _response(200, {"id": message_id, "labelIds": ["TRASH"]})
