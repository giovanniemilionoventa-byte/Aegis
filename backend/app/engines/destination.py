"""Phase 20 -- read the destination of a send out of the request itself.

An email send used to be classified by whatever `scope` and `destination` the
agent declared. The policy "email outside the company needs a human" matched on
that declaration, so an agent (or an injected prompt steering it) could write
"internal" while sending to a stranger and walk straight through, and the human
who was asked to approve saw only a digest.

This module is the fix at its root: the recipients are read from the payload the
connector will actually use, and classified against the organization's own
domain list. Whatever the agent declared is discarded for a send.

FAIL-SAFE, EVERYWHERE. The only way to be "internal" is for every recipient to
be a well-formed address whose domain is listed exactly. A lookalike domain, a
subdomain nobody listed, a display name that contains an inside address, an
address with two @, or text that does not parse at all is external.
"""

from __future__ import annotations

from dataclasses import dataclass
from email.utils import getaddresses
from typing import Any, Optional

# (resource kind, action) pairs that send something to a person.
SEND_ACTIONS = {("email", "SEND"), ("gmail", "SEND")}

RECIPIENT_FIELDS = ("to", "cc", "bcc")

# Stands in for a recipient field that had text in it but yielded no address.
# It has no "@", so it can only ever classify as external.
UNPARSEABLE = "<unparseable>"


@dataclass(frozen=True)
class Derivation:
    recipients: tuple[str, ...]
    # "internal" | "external", or None when the request named no recipient.
    classification: Optional[str]


def _values(payload: Any, field: str) -> list[str]:
    if not isinstance(payload, dict):
        return []
    found: list[str] = []
    for key, value in payload.items():
        if str(key).lower() != field:
            continue
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, (list, tuple)):
            found.extend(item for item in value if isinstance(item, str))
    return found


def recipients(payload: Any) -> list[str]:
    """Every recipient in to/cc/bcc, lowercased, in order, without duplicates."""
    seen: list[str] = []
    for field in RECIPIENT_FIELDS:
        for chunk in _values(payload, field):
            if not chunk.strip():
                continue
            # getaddresses understands "Name <a@b>" and quoted names that
            # contain commas. Outlook-style ";" separators are commas to it.
            parsed = [
                address.strip().lower()
                for _, address in getaddresses([chunk.replace(";", ",")])
                if address.strip()
            ]
            if not parsed:
                parsed = [UNPARSEABLE]
            for address in parsed:
                if address not in seen:
                    seen.append(address)
    return seen


def recipients_by_field(payload: Any) -> dict[str, list[str]]:
    """The same recipients, kept apart by to / cc / bcc (for showing a reviewer)."""
    return {
        field: recipients({field: _values(payload, field)})
        for field in RECIPIENT_FIELDS
    }


def _domains(configured: Optional[str]) -> set[str]:
    if not configured:
        return set()
    return {
        part.strip().lower().rstrip(".")
        for part in configured.replace(";", ",").split(",")
        if part.strip()
    }


def classify(addresses: list[str], internal_domains: Optional[str]) -> str:
    """'internal' only if every address is well formed and its domain is listed."""
    inside = _domains(internal_domains)
    if not addresses or not inside:
        return "external"
    for address in addresses:
        if address.count("@") != 1:
            return "external"
        domain = address.rsplit("@", 1)[1].strip().rstrip(".").lower()
        if not domain or domain not in inside:
            return "external"
    return "internal"


def derive(
    kind: str, action: str, payload: Any, internal_domains: Optional[str]
) -> Optional[Derivation]:
    """None for anything that is not a send; otherwise what the payload says."""
    if (kind.lower(), action.upper()) not in SEND_ACTIONS:
        return None
    found = recipients(payload)
    if not found:
        return Derivation((), None)
    return Derivation(tuple(found), classify(found, internal_domains))
