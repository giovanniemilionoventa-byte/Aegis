"""Phase 16.A orchestrator. Runs on the HOST.

1. Brings up the Aegis stack + bench-runner overlay (additive compose file,
   no production service is modified).
2. Waits for control-plane / enforcement-gateway health.
3. Bootstraps a fresh Sales Copilot agent token.
4. Starts a background resource sampler (docker stats).
5. Runs the latency phase (concurrency=1) and the throughput sweep
   (concurrency in CONCURRENCY_LEVELS) for each of the three scenarios,
   invoking bench_client.py inside the bench-runner container over
   `docker compose exec`.
6. Stops the sampler and writes benchmarks/results/index.json summarizing
   every run produced.

Reproduce with:
    python benchmarks/run_benchmark.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILES = ["-f", "docker-compose.yml", "-f", "benchmarks/docker-compose.bench.yml"]
RESULTS_DIR = ROOT / "benchmarks" / "results"

SCENARIOS = ["baseline", "aegis_allow", "aegis_block", "authorize_only"]
CONCURRENCY_LEVELS = [1, 2, 5, 10, 25, 50]

LATENCY_COUNT = 500
LATENCY_WARMUP = 50
THROUGHPUT_COUNT = 200
THROUGHPUT_WARMUP = 20


def sh(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd))
    return subprocess.run(cmd, cwd=ROOT, check=True, **kwargs)


def compose_up() -> None:
    sh(["docker", "compose", *COMPOSE_FILES, "up", "-d", "--build"])


def compose_down() -> None:
    sh(["docker", "compose", *COMPOSE_FILES, "down"])


def wait_health(url: str, timeout_s: float = 60.0) -> None:
    deadline = time.time() + timeout_s
    last_err = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if resp.status == 200:
                    print(f"healthy: {url}")
                    return
        except urllib.error.URLError as exc:
            last_err = exc
        time.sleep(1.5)
    raise SystemExit(f"timed out waiting for {url}: {last_err}")


def wait_container_healthy(container: str, timeout_s: float = 60.0) -> None:
    # enforcement-gateway is only reachable from agent_net/broker_net, not
    # published to the host, so we check Docker's own healthcheck state
    # instead of curling a host port.
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        out = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Health.Status}}", container],
            capture_output=True,
            text=True,
        )
        status = out.stdout.strip()
        if status == "healthy":
            print(f"healthy: {container}")
            return
        time.sleep(1.5)
    raise SystemExit(f"timed out waiting for container {container} to become healthy")


def bootstrap() -> None:
    sh([sys.executable, str(ROOT / "benchmarks" / "bootstrap.py")])


def exec_bench(scenario: str, concurrency: int, count: int, warmup: int, out_rel: str) -> dict:
    out_container = f"/bench/results/{out_rel}"
    sh(
        [
            "docker",
            "compose",
            *COMPOSE_FILES,
            "exec",
            "-T",
            "bench-runner",
            "python",
            "bench_client.py",
            "--scenario",
            scenario,
            "--concurrency",
            str(concurrency),
            "--count",
            str(count),
            "--warmup",
            str(warmup),
            "--out",
            out_container,
        ],
        stdout=subprocess.DEVNULL,
    )
    return json.loads((RESULTS_DIR / out_rel).read_text(encoding="utf-8"))


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    compose_up()
    wait_health("http://127.0.0.1:8000/api/health")
    wait_container_healthy("aegis-enforcement-gateway")

    bootstrap()

    monitor_out = RESULTS_DIR / "resource_usage.csv"
    monitor = subprocess.Popen(
        [
            sys.executable,
            str(ROOT / "benchmarks" / "resource_monitor.py"),
            "--out",
            str(monitor_out),
            "--interval",
            "2",
        ]
    )

    index = {"latency": {}, "throughput": {}}
    try:
        # Phase 1: latency baseline at concurrency=1
        for scenario in SCENARIOS:
            fname = f"latency_{scenario}.json"
            result = exec_bench(scenario, 1, LATENCY_COUNT, LATENCY_WARMUP, fname)
            index["latency"][scenario] = result
            print(f"[latency] {scenario}: p50={result['latency_ms']['p50']:.2f}ms "
                  f"p95={result['latency_ms']['p95']:.2f}ms p99={result['latency_ms']['p99']:.2f}ms")

        # Phase 2: throughput sweep
        for scenario in SCENARIOS:
            index["throughput"].setdefault(scenario, {})
            for c in CONCURRENCY_LEVELS:
                fname = f"throughput_{scenario}_c{c}.json"
                result = exec_bench(scenario, c, THROUGHPUT_COUNT, THROUGHPUT_WARMUP, fname)
                index["throughput"][scenario][str(c)] = result
                print(f"[throughput] {scenario} c={c}: rps={result['throughput_rps']:.1f} "
                      f"p95={result['latency_ms']['p95']:.2f}ms errors={result['error_count']}")
    finally:
        monitor.terminate()
        try:
            monitor.wait(timeout=10)
        except subprocess.TimeoutExpired:
            monitor.kill()

    (RESULTS_DIR / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    print(f"\nWrote {RESULTS_DIR / 'index.json'}")


if __name__ == "__main__":
    main()
