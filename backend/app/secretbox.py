"""Authenticated encryption for secrets held at rest, using only the stdlib.

Phase 19 stores Google OAuth refresh tokens server-side. They must not sit in
plaintext in a file that a backup, a stray `docker cp` or a mounted volume
would expose, so they are sealed with a key that lives only in the environment.

WHAT THIS IS. Encrypt-then-MAC built from HMAC-SHA256:

    keystream = HMAC(k_enc, nonce || counter) blocks, XORed with the plaintext
    tag       = HMAC(k_mac, context || nonce || ciphertext)

k_enc and k_mac are separately derived from the master key, the tag covers the
nonce and the context string, and `open` verifies the tag with a constant-time
compare before it decrypts anything. Those are the standard properties, built
from a primitive the standard library actually ships.

WHAT THIS IS NOT. It is not AES-GCM and it is not a key-management system. The
key is an environment variable, so anyone who can read the process environment
can read the tokens; there is no rotation, no envelope encryption and no HSM.
The threat it addresses is a leaked file, not a compromised host. A deployment
that needs more than that should put the refresh tokens in a real secret store
and hand this module a handle instead.

Nothing here is ever logged. Callers must not log what they pass in either.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct

VERSION = b"aegis-secretbox-v1"
NONCE_BYTES = 16
TAG_BYTES = 32


class SecretBoxError(Exception):
    """Sealing or opening failed. Carries no secret material."""


def _subkey(master: bytes, label: bytes) -> bytes:
    return hmac.new(master, VERSION + b":" + label, hashlib.sha256).digest()


def _keystream(k_enc: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hmac.new(
            k_enc, nonce + struct.pack(">I", counter), hashlib.sha256
        ).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def _master(key: str) -> bytes:
    if not key:
        raise SecretBoxError("No encryption key configured")
    return hashlib.sha256(key.encode("utf-8")).digest()


def seal(plaintext: str, key: str, *, context: str = "") -> str:
    """Encrypt and authenticate. Returns an opaque base64 string."""
    master = _master(key)
    k_enc = _subkey(master, b"enc")
    k_mac = _subkey(master, b"mac")
    nonce = os.urandom(NONCE_BYTES)
    raw = plaintext.encode("utf-8")
    ciphertext = bytes(a ^ b for a, b in zip(raw, _keystream(k_enc, nonce, len(raw))))
    tag = hmac.new(
        k_mac, context.encode("utf-8") + b"|" + nonce + ciphertext, hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(nonce + tag + ciphertext).decode("ascii")


def open_sealed(sealed: str, key: str, *, context: str = "") -> str:
    """Verify then decrypt. Raises SecretBoxError on any mismatch.

    The tag is checked before the plaintext is produced, so a wrong key, a
    different context or a modified file yields an error rather than garbage.
    """
    master = _master(key)
    k_enc = _subkey(master, b"enc")
    k_mac = _subkey(master, b"mac")
    try:
        blob = base64.urlsafe_b64decode(sealed.encode("ascii"))
    except Exception as exc:  # noqa: BLE001 - malformed input, no detail to leak
        raise SecretBoxError("Malformed sealed value") from exc
    if len(blob) < NONCE_BYTES + TAG_BYTES:
        raise SecretBoxError("Malformed sealed value")
    nonce = blob[:NONCE_BYTES]
    tag = blob[NONCE_BYTES : NONCE_BYTES + TAG_BYTES]
    ciphertext = blob[NONCE_BYTES + TAG_BYTES :]
    expected = hmac.new(
        k_mac, context.encode("utf-8") + b"|" + nonce + ciphertext, hashlib.sha256
    ).digest()
    if not hmac.compare_digest(tag, expected):
        raise SecretBoxError("Sealed value failed authentication")
    raw = bytes(
        a ^ b for a, b in zip(ciphertext, _keystream(k_enc, nonce, len(ciphertext)))
    )
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SecretBoxError("Sealed value is not valid UTF-8") from exc
