#!/usr/bin/env python3
"""Phase 18 — the long-lived runtime-verification agent.

Runs inside the agent container. Its whole life is:

    ask the gateway whether my operator requested a run
      -> if yes, execute the scenario through the real authorization path
      -> report the transcript back
      -> go back to asking

It pulls; nothing pushes at it. That is forced by the deployment boundary: the
control plane shares no network with the gateway, and this container shares no
network with the control plane. agent -> gateway is the only open path, so the
job channel rides it.

It holds exactly one secret: its own Aegis agent token, supplied as
AEGIS_AGENT_TOKEN, exactly as any real agent would hold its credential. It has
no tool credential, no EAT key and no internal service token, and the boundary
proof asserts that from inside this container.

WHAT THIS IS. A controlled test harness for runtime verification, so an
operator can watch Aegis authorize and refuse real actions without a terminal.
It is not a product runtime and not an AI agent: the scenario is a fixed list
of actions, not a model deciding what to do. Production agent onboarding is a
separate concept in the dashboard and is labelled as such.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

GATEWAY = os.environ.get("AEGIS_BASE_URL", "http://enforcement-gateway:8000")
TOKEN = os.environ.get("AEGIS_AGENT_TOKEN", "")
POLL_SECONDS = float(os.environ.get("AEGIS_RUNNER_POLL_SECONDS", "3"))


def _log(message: str) -> None:
    print(f"[runner {datetime.now(timezone.utc).isoformat()}] {message}", flush=True)


def _call(method: str, path: str, body=None, timeout: float = 30.0) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        f"{GATEWAY}{path}",
        data=data,
        headers={"Content-Type": "application/json", "X-Agent-Token": TOKEN},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return {"status": int(response.status), "body": json.loads(raw or "{}")}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(raw or "{}")
        except ValueError:
            parsed = {"raw": raw[:300]}
        return {"status": int(exc.code), "body": parsed}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"status": None, "body": {"transport_error": str(exc)}}


def run_scenario(scenario: str, execution_id: str) -> dict:
    """Execute a scenario through the real gateway and return its transcript."""
    import reference_agent

    if scenario == "read_only":
        result = reference_agent.invoke(
            "crm",
            "read",
            scope="customers",
            execution_id=execution_id,
            request_id=f"readonly-{execution_id}",
            payload={},
        )
        transcript = {
            "schema": "aegis.reference_agent.transcript/v1",
            "agent_kind": "REFERENCE_AGENT",
            "scenario": scenario,
            "execution_id": execution_id,
            "steps": [
                reference_agent._step(
                    "allowed::crm_read",
                    "read customer records",
                    "ALLOW and the tool runs",
                    result,
                )
            ],
        }
        transcript["evaluation"] = {
            "verdict": "PASS"
            if (result.get("body") or {}).get("executed")
            else "FAIL",
            "passed": 1 if (result.get("body") or {}).get("executed") else 0,
            "total": 1,
            "failed": []
            if (result.get("body") or {}).get("executed")
            else ["permitted read executed"],
            "checks": [],
        }
        return transcript

    transcript = reference_agent.run_workflow(execution_id)
    transcript["evaluation"] = reference_agent.evaluate(transcript)
    transcript["scenario"] = scenario
    return transcript


def poll_once() -> bool:
    """Claim and run one job. True if work was done."""
    claim = _call("GET", "/api/agentctl/next")
    if claim["status"] != 200:
        if claim["status"] in (401, 403):
            _log(f"gateway refused the agent token: {claim['body']}")
        elif claim["status"] is None:
            _log(f"gateway unreachable: {claim['body']}")
        return False

    run = (claim["body"] or {}).get("run")
    if not run:
        return False

    run_id = run["run_id"]
    scenario = run.get("scenario", "canonical")
    execution_id = run.get("execution_id") or run_id
    _log(f"claimed run {run_id} scenario={scenario}")

    try:
        transcript = run_scenario(scenario, execution_id)
        verdict = (transcript.get("evaluation") or {}).get("verdict", "UNKNOWN")
        _log(f"run {run_id} finished, agent self-check={verdict}")
        _call(
            "POST",
            f"/api/agentctl/runs/{run_id}/result",
            {
                "status": "COMPLETED",
                "execution_id": execution_id,
                "result": transcript,
            },
        )
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        _log(f"run {run_id} failed: {exc!r}")
        _call(
            "POST",
            f"/api/agentctl/runs/{run_id}/result",
            {
                "status": "FAILED",
                "execution_id": execution_id,
                "error": repr(exc),
            },
        )
    return True


def main() -> int:
    if not TOKEN:
        _log(
            "AEGIS_AGENT_TOKEN is not set. This container has no identity, so "
            "it cannot claim verification runs. Generate one with "
            "scripts/init-env.sh and restart the stack."
        )
        while True:
            time.sleep(3600)

    # Print the boundary evidence once at startup, so `docker compose logs
    # agent` shows what this container can and cannot reach.
    try:
        import probe

        _log("startup boundary probe:")
        print(json.dumps(probe.evidence(), indent=2, sort_keys=True), flush=True)
    except Exception as exc:  # noqa: BLE001
        _log(f"startup probe unavailable: {exc!r}")

    _log(f"polling {GATEWAY} every {POLL_SECONDS}s for verification runs")
    while True:
        try:
            worked = poll_once()
        except Exception as exc:  # noqa: BLE001 - a poller must not die
            _log(f"poll error: {exc!r}")
            worked = False
        time.sleep(0.5 if worked else POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
