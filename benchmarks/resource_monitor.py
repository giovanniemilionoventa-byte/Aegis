"""Phase 16.A resource sampler.

Runs on the HOST. Samples `docker stats --no-stream` for the named
containers at a fixed interval and appends CSV rows until told to stop
(SIGTERM / SIGINT, or --duration seconds elapses). One process, no
dependencies beyond the docker CLI already required for the benchmark.
"""

from __future__ import annotations

import argparse
import csv
import signal
import subprocess
import sys
import time
from pathlib import Path

CONTAINERS = [
    "aegis-control-plane",
    "aegis-enforcement-gateway",
    "aegis-credential-broker",
    "aegis-protected-tool",
    "aegis-bench-runner",
]

FIELDS = ["timestamp", "container", "cpu_percent", "mem_usage", "mem_percent", "net_io", "block_io"]


def _sample() -> list[dict]:
    fmt = "{{.Name}},{{.CPUPerc}},{{.MemUsage}},{{.MemPerc}},{{.NetIO}},{{.BlockIO}}"
    try:
        out = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", fmt] + CONTAINERS,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception as exc:  # pragma: no cover - diagnostic only
        print(f"resource_monitor: docker stats failed: {exc}", file=sys.stderr)
        return []
    rows = []
    ts = time.time()
    for line in out.stdout.strip().splitlines():
        parts = line.split(",")
        if len(parts) != 6:
            continue
        name, cpu, mem_usage, mem_pct, net_io, block_io = parts
        rows.append(
            {
                "timestamp": ts,
                "container": name,
                "cpu_percent": cpu,
                "mem_usage": mem_usage,
                "mem_percent": mem_pct,
                "net_io": net_io,
                "block_io": block_io,
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--duration", type=float, default=0, help="0 = run until killed")
    args = parser.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    stop = {"flag": False}

    def _handle(_sig, _frame):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)

    start = time.time()
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        fh.flush()
        while not stop["flag"]:
            for row in _sample():
                writer.writerow(row)
            fh.flush()
            if args.duration and (time.time() - start) >= args.duration:
                break
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
