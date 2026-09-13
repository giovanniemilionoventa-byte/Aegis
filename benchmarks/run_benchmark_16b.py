"""Phase 16.B orchestrator. Runs on the HOST.

Extends the Phase 16.A benchmark with: a wider/deeper concurrency sweep,
repeated-trial SQLite-contention testing with classified errors, a
same-execution concurrent-write race probe (evidence chain + trajectory),
a long-execution evidence-chain growth probe, and an EAT/replay-under-load
probe. Does not modify or optimize any production code.

Reproduce with:
    python benchmarks/run_benchmark_16b.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from log_errors import classify as classify_logs  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILES = ["-f", "docker-compose.yml", "-f", "benchmarks/docker-compose.bench.yml"]
RESULTS_DIR = ROOT / "benchmarks" / "results"

SWEEP_SCENARIOS = ["baseline", "aegis_allow", "aegis_block", "authorize_only"]
SWEEP_LEVELS = [1, 2, 5, 10, 25, 50, 75, 100]

CONTENTION_SCENARIOS = ["aegis_allow", "aegis_block", "authorize_only"]
CONTENTION_LEVELS = [5, 10, 25, 50]
CONTENTION_TRIALS = 3

CONTENTION_LOG_CONTAINERS = {
    "aegis_allow": ["aegis-enforcement-gateway", "aegis-credential-broker", "aegis-protected-tool"],
    "aegis_block": ["aegis-enforcement-gateway"],
    "authorize_only": ["aegis-enforcement-gateway"],
}


def sh(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd))
    return subprocess.run(cmd, cwd=ROOT, check=True, **kwargs)


def compose_up() -> None:
    sh(["docker", "compose", *COMPOSE_FILES, "up", "-d", "--build"])


def compose_down() -> None:
    sh(["docker", "compose", *COMPOSE_FILES, "down"])


def compose_down_v() -> None:
    sh(["docker", "compose", *COMPOSE_FILES, "down", "-v"])


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
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        out = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Health.Status}}", container],
            capture_output=True,
            text=True,
        )
        if out.stdout.strip() == "healthy":
            print(f"healthy: {container}")
            return
        time.sleep(1.5)
    raise SystemExit(f"timed out waiting for container {container} to become healthy")


def bootstrap() -> None:
    sh([sys.executable, str(ROOT / "benchmarks" / "bootstrap.py")])


def exec_bench(
    scenario: str,
    concurrency: int,
    count: int,
    warmup: int,
    out_rel: str,
    execution_id: str | None = None,
) -> dict:
    out_container = f"/bench/results/{out_rel}"
    cmd = [
        "docker", "compose", *COMPOSE_FILES, "exec", "-T", "bench-runner",
        "python", "bench_client.py",
        "--scenario", scenario,
        "--concurrency", str(concurrency),
        "--count", str(count),
        "--warmup", str(warmup),
        "--out", out_container,
    ]
    if execution_id:
        cmd += ["--execution-id", execution_id]
    sh(cmd, stdout=subprocess.DEVNULL)
    return json.loads((RESULTS_DIR / out_rel).read_text(encoding="utf-8"))


def docker_cp_and_run(container: str, script_name: str, args: list[str] | None = None) -> str:
    local = ROOT / "benchmarks" / script_name
    remote = f"/tmp/{script_name}"
    sh(["docker", "cp", str(local), f"{container}:{remote}"])
    cmd = [
        "docker", "exec", "-w", "/app", "-e", "PYTHONPATH=/app",
        container, "python", remote,
    ] + (args or [])
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(f"{script_name} failed in {container}")
    return proc.stdout


def container_restart_counts() -> dict:
    containers = [
        "aegis-control-plane",
        "aegis-enforcement-gateway",
        "aegis-credential-broker",
        "aegis-protected-tool",
        "aegis-bench-runner",
        "aegis-agent",
    ]
    out = {}
    for c in containers:
        proc = subprocess.run(
            ["docker", "inspect", "--format", "{{.RestartCount}}", c],
            capture_output=True, text=True,
        )
        out[c] = proc.stdout.strip()
    return out


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    print("=== fresh environment (down -v, up --build) ===")
    try:
        compose_down_v()
    except subprocess.CalledProcessError:
        pass
    compose_up()
    wait_health("http://127.0.0.1:8000/api/health")
    wait_container_healthy("aegis-enforcement-gateway")
    bootstrap()

    monitor_out = RESULTS_DIR / "16b_resource_usage.csv"
    monitor = subprocess.Popen(
        [sys.executable, str(ROOT / "benchmarks" / "resource_monitor.py"),
         "--out", str(monitor_out), "--interval", "2"]
    )

    index: dict = {"sweep": {}, "contention": {}, "race": {}, "chain_growth": None, "eat_replay": None}

    try:
        print("\n=== Section 7: concurrency sweep ===")
        for scenario in SWEEP_SCENARIOS:
            index["sweep"].setdefault(scenario, {})
            for c in SWEEP_LEVELS:
                count = 100 if c >= 75 else 200
                warmup = 15 if c >= 75 else 20
                fname = f"16b_sweep_{scenario}_c{c}.json"
                result = exec_bench(scenario, c, count, warmup, fname)
                index["sweep"][scenario][str(c)] = result
                print(f"[sweep] {scenario:16s} c={c:>3d} rps={result['throughput_rps']:7.2f} "
                      f"p50={result['latency_ms']['p50']:8.2f} p95={result['latency_ms']['p95']:8.2f} "
                      f"p99={result['latency_ms']['p99']:8.2f} err={result['error_count']}")

        print("\n=== Section 8: SQLite contention repeated trials ===")
        for scenario in CONTENTION_SCENARIOS:
            index["contention"].setdefault(scenario, {})
            for c in CONTENTION_LEVELS:
                index["contention"][scenario].setdefault(str(c), [])
                for trial in range(1, CONTENTION_TRIALS + 1):
                    fname = f"16b_contention_{scenario}_c{c}_t{trial}.json"
                    result = exec_bench(scenario, c, 200, 20, fname)
                    logs = {}
                    for container in CONTENTION_LOG_CONTAINERS[scenario]:
                        logs[container] = classify_logs(
                            container, result["started_at"], result["finished_at"]
                        )
                    log_fname = f"16b_contention_{scenario}_c{c}_t{trial}_logs.json"
                    (RESULTS_DIR / log_fname).write_text(json.dumps(logs, indent=2), encoding="utf-8")
                    entry = {"result": result, "logs": logs}
                    index["contention"][scenario][str(c)].append(entry)
                    print(f"[contention] {scenario:16s} c={c:>3d} trial={trial} "
                          f"errors={result['error_count']} rps={result['throughput_rps']:7.2f} "
                          f"p99={result['latency_ms']['p99']:8.2f}")

        print("\n=== Section 9/12: same-execution race probe ===")
        exec_a = f"bench-16b-race-a-{uuid.uuid4()}"
        race_a = exec_bench("authorize_only", 30, 30, 0, "16b_race_authorize_only_burst.json", execution_id=exec_a)
        logs_a = classify_logs("aegis-enforcement-gateway", race_a["started_at"], race_a["finished_at"])
        probe_a = docker_cp_and_run("aegis-control-plane", "chain_probe.py", [exec_a])
        (RESULTS_DIR / "16b_race_authorize_only_chain_probe.json").write_text(probe_a, encoding="utf-8")
        followup_a = exec_bench("authorize_only", 1, 1, 0, "16b_race_authorize_only_followup.json", execution_id=exec_a)

        exec_b = f"bench-16b-race-b-{uuid.uuid4()}"
        race_b = exec_bench("aegis_mixed", 20, 40, 0, "16b_race_mixed_burst.json", execution_id=exec_b)
        logs_b = classify_logs("aegis-enforcement-gateway", race_b["started_at"], race_b["finished_at"])
        probe_b = docker_cp_and_run("aegis-control-plane", "chain_probe.py", [exec_b])
        (RESULTS_DIR / "16b_race_mixed_chain_probe.json").write_text(probe_b, encoding="utf-8")
        followup_b = exec_bench("aegis_mixed", 1, 1, 0, "16b_race_mixed_followup.json", execution_id=exec_b)

        index["race"] = {
            "authorize_only_same_execution": {
                "execution_id": exec_a,
                "burst": race_a,
                "burst_logs": logs_a,
                "chain_probe": json.loads(probe_a),
                "followup_request": followup_a,
            },
            "mixed_allow_block_same_execution": {
                "execution_id": exec_b,
                "burst": race_b,
                "burst_logs": logs_b,
                "chain_probe": json.loads(probe_b),
                "followup_request": followup_b,
            },
        }
        print(f"[race] authorize_only same-execution: dup_seq={index['race']['authorize_only_same_execution']['chain_probe']['duplicate_seq']} "
              f"gaps={index['race']['authorize_only_same_execution']['chain_probe']['seq_gaps']} "
              f"verifier_pass={index['race']['authorize_only_same_execution']['chain_probe']['verifier_result']['pass']}")
        print(f"[race] mixed allow/block same-execution: dup_seq={index['race']['mixed_allow_block_same_execution']['chain_probe']['duplicate_seq']} "
              f"gaps={index['race']['mixed_allow_block_same_execution']['chain_probe']['seq_gaps']} "
              f"verifier_pass={index['race']['mixed_allow_block_same_execution']['chain_probe']['verifier_result']['pass']}")

        print("\n=== Section 13: long-execution evidence-chain growth ===")
        sh(
            ["docker", "compose", *COMPOSE_FILES, "exec", "-T", "bench-runner",
             "python", "chain_growth.py", "--count", "100",
             "--out", "/bench/results/16b_chain_growth.json"],
            stdout=subprocess.DEVNULL,
        )
        chain_growth = json.loads((RESULTS_DIR / "16b_chain_growth.json").read_text(encoding="utf-8"))
        growth_exec_id = chain_growth["execution_id"]
        growth_probe = docker_cp_and_run("aegis-control-plane", "chain_probe.py", [growth_exec_id])
        (RESULTS_DIR / "16b_chain_growth_chain_probe.json").write_text(growth_probe, encoding="utf-8")
        index["chain_growth"] = {"growth": chain_growth, "final_chain_probe": json.loads(growth_probe)}
        print(f"[chain_growth] execution={growth_exec_id} verifier_pass={index['chain_growth']['final_chain_probe']['verifier_result']['pass']}")

        print("\n=== Section 15: EAT / replay under load ===")
        eat_out = docker_cp_and_run("aegis-enforcement-gateway", "eat_replay_probe.py")
        index["eat_replay"] = json.loads(eat_out)
        print(json.dumps(index["eat_replay"], indent=2))

        print("\n=== Section 17: resource saturation (restart counts) ===")
        index["restart_counts"] = container_restart_counts()
        print(index["restart_counts"])

    finally:
        monitor.terminate()
        try:
            monitor.wait(timeout=10)
        except subprocess.TimeoutExpired:
            monitor.kill()

    (RESULTS_DIR / "16b_index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    print(f"\nWrote {RESULTS_DIR / '16b_index.json'}")


if __name__ == "__main__":
    main()
