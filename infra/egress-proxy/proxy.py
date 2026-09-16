#!/usr/bin/env python3
"""The agent's only way out, and it goes exactly one place.

Phase 19. The agent needs the internet for one reason: to talk to its model
provider. It must not be able to use that reason to reach Gmail.

So the agent container sits on an internal Docker network with no route off the
host, and its HTTPS_PROXY points here. This process is on both that internal
network and an external one. It speaks HTTP CONNECT and refuses every host that
is not on the allow-list, which is supplied as AEGIS_EGRESS_ALLOWLIST and
contains the model API and nothing else.

WHY A PROXY AND NOT AN INSTRUCTION. "Never call Gmail directly" in a system
prompt is a request. This is a refusal: a CONNECT to gmail.googleapis.com is
answered 403 by a process the agent does not control, on the only route it has.
The Phase 19 network tests assert that by attempting it.

WHAT THIS IS NOT. It is not a TLS-terminating proxy and it does not inspect
traffic: once a CONNECT to an allowed host is accepted, bytes are relayed
blind. It cannot tell you what the agent said to its model. It is an egress
control, not a DLP.

A NOTE ON SNI. The host checked is the one in the CONNECT line. A client that
sends an allowed CONNECT host and then a different TLS SNI would defeat this on
its own; the deployment's defence for that is that the allowed host is the only
route to anything at all, and the agent has no DNS or IP route to Gmail
regardless. Do not describe this proxy as the sole control.
"""

from __future__ import annotations

import os
import select
import socket
import sys
import threading
from datetime import datetime, timezone

LISTEN_HOST = os.environ.get("AEGIS_EGRESS_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("AEGIS_EGRESS_PORT", "8888"))
ALLOWLIST = {
    entry.strip().lower()
    for entry in os.environ.get("AEGIS_EGRESS_ALLOWLIST", "api.deepseek.com").split(",")
    if entry.strip()
}
ALLOWED_PORTS = {443}
BUFFER = 65536
CONNECT_TIMEOUT = float(os.environ.get("AEGIS_EGRESS_CONNECT_TIMEOUT", "10"))

# Counters a test or an operator can read off the logs.
_stats = {"allowed": 0, "refused": 0}
_stats_lock = threading.Lock()


def _log(message: str) -> None:
    print(f"[egress {datetime.now(timezone.utc).isoformat()}] {message}", flush=True)


def _host_allowed(host: str) -> bool:
    """Exact match, or a subdomain of an allow-listed domain.

    Deliberately not a substring test: "api.deepseek.com.evil.test" must not
    match "api.deepseek.com", and it does not, because the check is on label
    boundaries.
    """
    host = host.lower().strip().rstrip(".")
    if host in ALLOWLIST:
        return True
    return any(host.endswith("." + allowed) for allowed in ALLOWLIST)


def _refuse(client: socket.socket, host: str, port: int, reason: str) -> None:
    with _stats_lock:
        _stats["refused"] += 1
    _log(f"REFUSED CONNECT {host}:{port} ({reason}) [refused={_stats['refused']}]")
    try:
        client.sendall(
            b"HTTP/1.1 403 Forbidden\r\n"
            b"Content-Type: text/plain\r\n"
            b"Connection: close\r\n\r\n"
            b"Blocked by the Aegis egress policy. This agent may reach its "
            b"model provider and nothing else; protected services are reached "
            b"through Aegis, never directly.\n"
        )
    except OSError:
        pass


def _relay(a: socket.socket, b: socket.socket) -> None:
    sockets = [a, b]
    try:
        while True:
            readable, _, errored = select.select(sockets, [], sockets, 60)
            if errored:
                break
            if not readable:
                break
            for source in readable:
                destination = b if source is a else a
                data = source.recv(BUFFER)
                if not data:
                    return
                destination.sendall(data)
    except OSError:
        return


def _handle(client: socket.socket, peer) -> None:
    client.settimeout(CONNECT_TIMEOUT)
    try:
        request = b""
        while b"\r\n\r\n" not in request:
            chunk = client.recv(BUFFER)
            if not chunk:
                return
            request += chunk
            if len(request) > 16384:
                _refuse(client, "?", 0, "request line too long")
                return

        first_line = request.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        parts = first_line.split()
        if len(parts) < 2 or parts[0].upper() != "CONNECT":
            # Plain HTTP forward-proxying is not offered at all: allowing it
            # would give the agent an unencrypted path to arbitrary hosts.
            _refuse(client, first_line[:60], 0, "only CONNECT is supported")
            return

        target = parts[1]
        host, _, port_text = target.rpartition(":")
        if not host:
            host, port_text = target, "443"
        try:
            port = int(port_text)
        except ValueError:
            _refuse(client, host, 0, "malformed port")
            return

        if port not in ALLOWED_PORTS:
            _refuse(client, host, port, "port not allowed")
            return
        if not _host_allowed(host):
            _refuse(client, host, port, "host not on the egress allow-list")
            return

        try:
            upstream = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)
        except OSError as exc:
            _log(f"upstream connect failed {host}:{port} ({type(exc).__name__})")
            try:
                client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
            except OSError:
                pass
            return

        with _stats_lock:
            _stats["allowed"] += 1
        _log(f"ALLOWED CONNECT {host}:{port} [allowed={_stats['allowed']}]")
        client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        client.settimeout(None)
        upstream.settimeout(None)
        try:
            _relay(client, upstream)
        finally:
            upstream.close()
    except (OSError, socket.timeout):
        return
    finally:
        try:
            client.close()
        except OSError:
            pass


def main() -> int:
    if not ALLOWLIST:
        _log("refusing to start: AEGIS_EGRESS_ALLOWLIST is empty")
        return 2
    _log(f"allow-list: {sorted(ALLOWLIST)} (port 443 only)")
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((LISTEN_HOST, LISTEN_PORT))
    server.listen(64)
    _log(f"listening on {LISTEN_HOST}:{LISTEN_PORT}")
    try:
        while True:
            client, peer = server.accept()
            threading.Thread(target=_handle, args=(client, peer), daemon=True).start()
    except KeyboardInterrupt:
        return 0
    finally:
        server.close()


if __name__ == "__main__":
    raise SystemExit(main())
