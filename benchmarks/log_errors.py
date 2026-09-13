"""Phase 16.B error-classification helper. Runs on the HOST.

Given a container name and an ISO8601 time window (matching the
started_at/finished_at fields bench_client.py writes into its result
JSON), tails that container's Docker logs for the window and counts known
error signatures. Used to correlate a benchmark trial's HTTP-level errors
with the actual server-side exception that produced them.

Usage:
  python benchmarks/log_errors.py --container aegis-enforcement-gateway \
      --since 2026-01-01T00:00:00+00:00 --until 2026-01-01T00:01:00+00:00
"""

from __future__ import annotations

import argparse
import re
import subprocess

PATTERNS = [
    ("database_is_locked", re.compile(r"database is locked", re.IGNORECASE)),
    ("integrity_error", re.compile(r"IntegrityError")),
    ("evidence_integrity_failure", re.compile(r"Execution evidence integrity failure|EvidenceIntegrityError")),
    ("eat_rejected", re.compile(r"eat_rejected")),
    ("contract_rejected", re.compile(r"contract_rejected")),
    ("http_500", re.compile(r'"[A-Z]+ [^"]+" 500')),
    ("http_409", re.compile(r'"[A-Z]+ [^"]+" 409')),
    ("http_401", re.compile(r'"[A-Z]+ [^"]+" 401')),
    ("http_502", re.compile(r'"[A-Z]+ [^"]+" 502')),
    ("http_503", re.compile(r'"[A-Z]+ [^"]+" 503')),
    ("traceback", re.compile(r"^Traceback")),
    ("operational_error", re.compile(r"sqlite3\.OperationalError")),
]


def classify(container: str, since: str, until: str) -> dict:
    cmd = ["docker", "logs", "--since", since, "--until", until, container]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    text = proc.stdout + proc.stderr
    counts = {}
    for name, pattern in PATTERNS:
        matches = pattern.findall(text)
        if matches:
            counts[name] = len(matches)
    return {
        "container": container,
        "since": since,
        "until": until,
        "log_lines": len(text.splitlines()),
        "counts": counts,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", required=True)
    parser.add_argument("--since", required=True)
    parser.add_argument("--until", required=True)
    args = parser.parse_args()
    import json

    print(json.dumps(classify(args.container, args.since, args.until), indent=2))


if __name__ == "__main__":
    main()
