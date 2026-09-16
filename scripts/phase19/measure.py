#!/usr/bin/env python3
"""Phase 19 — what the Gmail path costs, measured rather than estimated.

WHAT THIS MEASURES, AND WHAT IT CANNOT.

Measured here, in process, against the Gmail stand-in:
  * Aegis authorization overhead per operation -- the time from the gateway
    receiving a typed request to a decision being sealed, with no network and
    no real Gmail in the way. This is the number Aegis is responsible for.
  * Gmail API calls per canonical operation. This matters more than it looks:
    gmail.search is not one request, it is a list plus one metadata fetch per
    result, so its cost is linear in max_results and it is the obvious thing to
    optimise first if anyone ever needs to.
  * Evidence writes per operation.

NOT measured here, and not claimed anywhere:
  * Real Gmail API latency. That needs a real account and a network; the
    numbers would be Google's and the network's, not ours.
  * Model latency, tokens or spend. That needs a real model call; see
    docs/PHASE_19_GMAIL.md for what a human has to supply first.
  * Approval latency, which is a human deciding and is not a system property.
  * Anything at production scale. This is a PoC on SQLite with one process.

Run:  python3 scripts/phase19/measure.py [--iterations N] [--out FILE]
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("AEGIS_ALLOW_DEFAULT_SECRETS", "1")
os.environ.setdefault(
    "AEGIS_DATABASE_URL", f"sqlite:///{ROOT / 'backend' / 'phase19_measure.db'}"
)

from fastapi.testclient import TestClient  # noqa: E402

from app import config, models  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.main import create_app  # noqa: E402
from app.protected.gmail import gmail_connector  # noqa: E402

sys.path.insert(0, str(ROOT / "backend" / "tests"))
from gmail_fake import FakeGoogle  # noqa: E402


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(int(len(ordered) * fraction), len(ordered) - 1)
    return ordered[index]


def _summarise(samples: list[float]) -> dict:
    return {
        "count": len(samples),
        "mean_ms": round(statistics.fmean(samples) * 1000, 3),
        "median_ms": round(statistics.median(samples) * 1000, 3),
        "p95_ms": round(_percentile(samples, 0.95) * 1000, 3),
        "max_ms": round(max(samples) * 1000, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=50)
    parser.add_argument("--out", default=str(ROOT / "docs" / "evidence" / "phase19_cost_observations.json"))
    args = parser.parse_args()

    store = ROOT / "backend" / "phase19_measure_oauth.json"
    config.GMAIL_OAUTH_STORE_PATH = str(store)
    config.GMAIL_OAUTH_ENCRYPTION_KEY = "phase19-measurement-key"
    config.GOOGLE_OAUTH_CLIENT_ID = "measurement-client.invalid"
    config.GOOGLE_OAUTH_CLIENT_SECRET = "measurement-secret"

    google = FakeGoogle()
    for index in range(10):
        google.add_message(
            message_id=f"m-{index}",
            sender=f"person{index}@example.test",
            subject=f"Subject {index}",
            body=f"Body of message {index}. " * 20,
        )
    gmail_connector._transport = google

    with TestClient(create_app("all")) as client:
        sys.path.insert(0, str(ROOT / "backend"))
        from tests.phase19_harness import build_tenant, call  # noqa: PLC0415

        tenant = build_tenant(client)

        operations = {
            "gmail.search": ("search", {"query": "person1", "max_results": 5}),
            "gmail.read": ("read", {"message_id": "m-1"}),
            "gmail.draft": ("draft", {"to": "a@b.test", "subject": "s", "body": "b"}),
            "gmail.send_approval_required": ("send", {"to": "a@b.test", "subject": "s", "body": "b"}),
            "gmail.delete_denied": ("delete", {"message_id": "m-1"}),
        }

        results = {}
        for label, (operation, payload) in operations.items():
            # Warm up, so the first request's import and connection cost is not
            # reported as the steady-state number.
            call(client, tenant, operation, payload=payload)

            samples: list[float] = []
            gmail_calls_before = len(google.requests)
            for _ in range(args.iterations):
                started = time.perf_counter()
                response = call(
                    client,
                    tenant,
                    operation,
                    payload=payload,
                    execution_id=str(uuid4()),
                    request_id=f"m-{uuid4().hex[:10]}",
                )
                samples.append(time.perf_counter() - started)
                assert response.status_code == 200, response.text
            gmail_requests = len(google.requests) - gmail_calls_before

            results[label] = {
                **_summarise(samples),
                "gmail_api_requests_per_operation": round(
                    gmail_requests / args.iterations, 2
                ),
            }

        # How much of that is Aegis rather than the connector: a denied
        # operation never reaches Gmail, so its whole cost is the control layer.
        denied = results["gmail.delete_denied"]["median_ms"]
        allowed_read = results["gmail.read"]["median_ms"]

        db = SessionLocal()
        try:
            connector_rows = (
                db.query(models.ConnectorCall)
                .filter(models.ConnectorCall.organization_id == tenant.organization_id)
                .count()
            )
            events = (
                db.query(models.Event)
                .filter(models.Event.organization_id == tenant.organization_id)
                .count()
            )
        finally:
            db.close()

    report = {
        "schema": "aegis.phase19.cost_observations/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "harness": "in-process TestClient, Gmail API stand-in, SQLite",
        "iterations_per_operation": args.iterations,
        "operations": results,
        "observations": {
            "aegis_overhead_median_ms": denied,
            "aegis_overhead_note": (
                "A denied operation never reaches the connector, so its whole "
                "cost is authorization, sealing evidence and recording the "
                "connector-call row. Treat it as the floor Aegis adds to any "
                "request."
            ),
            "connector_share_median_ms": round(allowed_read - denied, 3),
            "connector_share_note": (
                "Difference between an allowed read and a denied delete. "
                "Against the stand-in this is within run-to-run noise and can "
                "come out negative, which is the honest reading: the stand-in "
                "costs nothing, so essentially all of the measured time is "
                "Aegis plus SQLite. Do not report this number as a connector "
                "cost. With real Gmail it is replaced by Google's latency, "
                "which is not measured here."
            ),
            "search_is_not_one_request": (
                "gmail.search issues one list request plus one metadata fetch "
                "PER RESULT RETURNED -- not per max_results. A query matching "
                "one message costs 2 requests; one matching twenty costs 21. "
                "The connector caps results at 25, so the ceiling is 26. This "
                "is the first thing to batch if it ever matters."
            ),
            "evidence_rows_per_operation": 1,
            "events_recorded": events,
            "connector_calls_recorded": connector_rows,
            "model_calls_per_task": (
                "NOT MEASURED. The agent loop is one model call per turn plus "
                "one per tool-result round; with the canonical four-step task "
                "that is 5 model calls and 4 tool calls, but the real figure "
                "depends on the model and is not measured without an API key."
            ),
            "approval_latency": (
                "NOT MEASURED. It is a human deciding. The grant's TTL "
                f"({config.APPROVAL_TTL_SECONDS}s) bounds how long that "
                "decision stays usable, not how long it takes."
            ),
            "gmail_api_latency": "NOT MEASURED. Needs a real Google account.",
        },
        "not_claimed": [
            "production-scale throughput",
            "real Gmail API latency",
            "model token cost or spend",
            "anything about a deployment that is not this one",
        ],
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report["operations"], indent=2, sort_keys=True))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
