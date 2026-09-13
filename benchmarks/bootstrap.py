"""Phase 16.A benchmark bootstrap.

Runs on the HOST against the published control-plane port. Logs in as the
seeded demo admin, rotates the Sales Copilot agent token, and writes it to
benchmarks/runtime/token.json for the containerized bench-runner to use.

This is one-time setup, not part of any measured latency. Uses only the
Python standard library so it runs on the host interpreter without extra
dependencies.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

CONTROL_PLANE_URL = "http://127.0.0.1:8000"
DEMO_EMAIL = "admin@acme.test"
DEMO_PASSWORD = "aegis-demo"
AGENT_NAME = "Sales Copilot"

OUT_PATH = Path(__file__).resolve().parent / "runtime" / "token.json"


def _post(path: str, body: dict, token: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        CONTROL_PLANE_URL + path,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get(path: str, token: str) -> dict:
    req = urllib.request.Request(
        CONTROL_PLANE_URL + path,
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    try:
        login = _post("/api/auth/login", {"email": DEMO_EMAIL, "password": DEMO_PASSWORD})
    except urllib.error.URLError as exc:
        print(f"ERROR: could not reach control-plane at {CONTROL_PLANE_URL}: {exc}", file=sys.stderr)
        sys.exit(1)

    admin_token = login["access_token"]
    agents = _get("/api/agents", admin_token)
    agent = next((a for a in agents if a["name"] == AGENT_NAME), None)
    if agent is None:
        print(f"ERROR: agent '{AGENT_NAME}' not found in seeded data", file=sys.stderr)
        sys.exit(1)

    rotated = _post(f"/api/agents/{agent['id']}/rotate", {}, token=admin_token)
    agent_token = rotated["token"]

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(
            {
                "agent_id": agent["id"],
                "agent_name": AGENT_NAME,
                "agent_token": agent_token,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Wrote agent token for '{AGENT_NAME}' to {OUT_PATH}")


if __name__ == "__main__":
    main()
