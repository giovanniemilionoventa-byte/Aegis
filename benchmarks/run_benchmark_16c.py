"""Phase 16.C orchestrator. Runs on the HOST.

Multi-tenant Cloud/API capacity validation, built on top of Phase 16.A/16.B
tooling. Provisions a pool of independent tenants through the REAL public
API (register/agents/policies -- no invented multi-tenant logic), then
measures how a shared Aegis instance behaves under workloads distributed
across 1..100 of those tenants, including cross-tenant isolation
adversarial probes, execution_id reuse patterns, and a noisy-neighbor
fairness test. Does not modify or optimize any production code.

Reproduce with:
    python benchmarks/run_benchmark_16c.py
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

ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILES = ["-f", "docker-compose.yml", "-f", "benchmarks/docker-compose.bench.yml"]
RESULTS_DIR = ROOT / "benchmarks" / "results"

TENANT_POOL_SIZE = 100
MIX = "60,30,10"


def sh(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd))
    return subprocess.run(cmd, cwd=ROOT, check=True, **kwargs)


def compose_up() -> None:
    sh(["docker", "compose", *COMPOSE_FILES, "up", "-d", "--build"])


def compose_down(volumes: bool = False) -> None:
    cmd = ["docker", "compose", *COMPOSE_FILES, "down"]
    if volumes:
        cmd.append("-v")
    sh(cmd)


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
            capture_output=True, text=True,
        )
        if out.stdout.strip() == "healthy":
            print(f"healthy: {container}")
            return
        time.sleep(1.5)
    raise SystemExit(f"timed out waiting for container {container} to become healthy")


def provision_tenants(count: int) -> None:
    sh([sys.executable, str(ROOT / "benchmarks" / "provision_tenants.py"), "--count", str(count)])


def exec_multitenant(
    label: str,
    out_rel: str,
    *,
    clients: int | None = None,
    requests_per_client: int | None = None,
    weights: str | None = None,
    concurrency: int,
    mix: str = MIX,
    warmup_per_client: int = 0,
) -> dict:
    out_container = f"/bench/results/{out_rel}"
    cmd = [
        "docker", "compose", *COMPOSE_FILES, "exec", "-T", "bench-runner",
        "python", "multitenant_client.py",
        "--concurrency", str(concurrency),
        "--mix", mix,
        "--label", label,
        "--warmup-per-client", str(warmup_per_client),
        "--out", out_container,
    ]
    if weights:
        cmd += ["--weights", weights]
    else:
        cmd += ["--clients", str(clients), "--requests-per-client", str(requests_per_client)]
    sh(cmd, stdout=subprocess.DEVNULL)
    return json.loads((RESULTS_DIR / out_rel).read_text(encoding="utf-8"))


def exec_bench_client(
    scenario: str, concurrency: int, count: int, warmup: int, out_rel: str,
    execution_id: str | None = None, token: str | None = None,
) -> dict:
    out_container = f"/bench/results/{out_rel}"
    cmd = [
        "docker", "compose", *COMPOSE_FILES, "exec", "-T", "bench-runner",
        "python", "bench_client.py",
        "--scenario", scenario, "--concurrency", str(concurrency),
        "--count", str(count), "--warmup", str(warmup), "--out", out_container,
    ]
    if execution_id:
        cmd += ["--execution-id", execution_id]
    if token:
        cmd += ["--token", token]
    sh(cmd, stdout=subprocess.DEVNULL)
    return json.loads((RESULTS_DIR / out_rel).read_text(encoding="utf-8"))


def exec_multitenant_bg(label: str, out_rel: str, *, clients: int, requests_per_client: int, concurrency: int, mix: str = MIX):
    out_container = f"/bench/results/{out_rel}"
    cmd = [
        "docker", "compose", *COMPOSE_FILES, "exec", "-T", "bench-runner",
        "python", "multitenant_client.py",
        "--clients", str(clients), "--requests-per-client", str(requests_per_client),
        "--concurrency", str(concurrency), "--mix", mix, "--label", label,
        "--out", out_container,
    ]
    print("+ (background)", " ".join(cmd))
    return subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def docker_cp_and_run(container: str, script_name: str, args: list[str] | None = None) -> str:
    local = ROOT / "benchmarks" / script_name
    remote = f"/tmp/{script_name}"
    sh(["docker", "cp", str(local), f"{container}:{remote}"])
    cmd = ["docker", "exec", "-w", "/app", "-e", "PYTHONPATH=/app", container, "python", remote] + (args or [])
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(f"{script_name} failed in {container}")
    return proc.stdout


def db_snapshot(label: str) -> dict:
    script = (
        "import sqlite3, os, json;"
        "con=sqlite3.connect('/data/aegis.db');cur=con.cursor();"
        "cur.execute('select count(*) from events');ev=cur.fetchone()[0];"
        "cur.execute('select count(*) from executions');ex=cur.fetchone()[0];"
        "cur.execute('select count(*) from organizations');org=cur.fetchone()[0];"
        "print(json.dumps({'events':ev,'executions':ex,'organizations':org,'bytes':os.path.getsize('/data/aegis.db')}))"
    )
    proc = subprocess.run(["docker", "exec", "aegis-control-plane", "python", "-c", script], capture_output=True, text=True)
    data = json.loads(proc.stdout.strip())
    data["label"] = label
    return data


def container_restart_counts() -> dict:
    containers = [
        "aegis-control-plane", "aegis-enforcement-gateway", "aegis-credential-broker",
        "aegis-protected-tool", "aegis-bench-runner", "aegis-agent",
    ]
    out = {}
    for c in containers:
        proc = subprocess.run(["docker", "inspect", "--format", "{{.RestartCount}}", c], capture_output=True, text=True)
        out[c] = proc.stdout.strip()
    return out


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    index: dict = {
        "distribution_scenarios": {}, "size_variants": {}, "sweep": {}, "noisy_neighbor": {},
        "execution_id_pattern": {}, "isolation": {}, "db_snapshots": [],
    }

    print("=== fresh environment (down -v, up --build) ===")
    try:
        compose_down(volumes=True)
    except subprocess.CalledProcessError:
        pass
    compose_up()
    wait_health("http://127.0.0.1:8000/api/health")
    wait_container_healthy("aegis-enforcement-gateway")

    print(f"\n=== provisioning {TENANT_POOL_SIZE} tenants via real /api/auth/register etc. ===")
    provision_tenants(TENANT_POOL_SIZE)
    index["db_snapshots"].append(db_snapshot("after_provisioning"))

    monitor_out = RESULTS_DIR / "16c_resource_usage.csv"
    monitor = subprocess.Popen(
        [sys.executable, str(ROOT / "benchmarks" / "resource_monitor.py"),
         "--out", str(monitor_out), "--interval", "2"]
    )

    try:
        print("\n=== Section 7/4: workload distribution scenarios A-E (total=50, concurrency=50) ===")
        scenario_defs = [
            ("A_1x50", 1, 50, 3),
            ("B_5x10", 5, 10, 1),
            ("C_10x5", 10, 5, 3),
            ("D_25x2", 25, 2, 1),
            ("E_50x1", 50, 1, 3),
        ]
        for name, clients, rpc, trials in scenario_defs:
            index["distribution_scenarios"].setdefault(name, [])
            for trial in range(1, trials + 1):
                fname = f"16c_dist_{name}_t{trial}.json"
                result = exec_multitenant(name, fname, clients=clients, requests_per_client=rpc, concurrency=50)
                index["distribution_scenarios"][name].append(result)
                print(f"[dist] {name} trial={trial} rps={result['throughput_rps']:.2f} "
                      f"p95={result['latency_ms']['p95']:.2f} errors={result['error_count']}/{result['actual_count']}")

        print("\n=== Section 4: size variants (total=25 and total=100) ===")
        # total=25: 1 client x25, 25 clients x1
        for name, clients, rpc in [("A25_1x25", 1, 25), ("E25_25x1", 25, 1)]:
            fname = f"16c_size_{name}.json"
            result = exec_multitenant(name, fname, clients=clients, requests_per_client=rpc, concurrency=25)
            index["size_variants"][name] = result
            print(f"[size] {name} rps={result['throughput_rps']:.2f} p95={result['latency_ms']['p95']:.2f} errors={result['error_count']}")
        # total=100: 50 clients x2 (priority #4), 3 trials
        index["size_variants"]["E100_50x2"] = []
        for trial in range(1, 4):
            fname = f"16c_size_E100_50x2_t{trial}.json"
            result = exec_multitenant("E100_50x2", fname, clients=50, requests_per_client=2, concurrency=50)
            index["size_variants"]["E100_50x2"].append(result)
            print(f"[size] E100_50x2 trial={trial} rps={result['throughput_rps']:.2f} "
                  f"p95={result['latency_ms']['p95']:.2f} errors={result['error_count']}")

        print("\n=== Section 7: concurrency sweep (1 request/client model) ===")
        for c in [1, 5, 10, 25, 75, 100]:
            fname = f"16c_sweep_c{c}.json"
            result = exec_multitenant(f"sweep_c{c}", fname, clients=c, requests_per_client=1, concurrency=c)
            index["sweep"][str(c)] = result
            print(f"[sweep] c={c:>3d} rps={result['throughput_rps']:.2f} p95={result['latency_ms']['p95']:.2f} "
                  f"p99={result['latency_ms']['p99']:.2f} errors={result['error_count']}/{result['actual_count']}")
        index["sweep"]["50_reused_from_E_50x1_trial1"] = index["distribution_scenarios"]["E_50x1"][0]

        index["db_snapshots"].append(db_snapshot("after_distribution_and_sweep"))

        print("\n=== Section 8: noisy neighbor (total=100, concurrency=50) ===")
        index["noisy_neighbor"]["80_20"] = []
        for trial in range(1, 4):
            fname = f"16c_noisy_80_20_t{trial}.json"
            result = exec_multitenant("noisy_80_20", fname, weights="0:80,1:20", concurrency=50)
            index["noisy_neighbor"]["80_20"].append(result)
            print(f"[noisy 80/20] trial={trial} tenant0={result['per_tenant'].get('0',{}).get('p95')} "
                  f"tenant1={result['per_tenant'].get('1',{}).get('p95')}")
        fname = "16c_noisy_95_5.json"
        index["noisy_neighbor"]["95_5"] = exec_multitenant("noisy_95_5", fname, weights="2:95,3:5", concurrency=50)

        print("\n=== Section 10: execution_id pattern (embedded in background multi-tenant load) ===")
        bg1 = exec_multitenant_bg("bg_noise_1", "16c_execid_bg1.json", clients=30, requests_per_client=2, concurrency=30)
        tenants = json.loads((RESULTS_DIR.parent / "runtime" / "tenants_16c.json").read_text(encoding="utf-8"))
        serial_exec_id = f"16c-serial-{uuid.uuid4()}"
        serial_result = exec_bench_client(
            "authorize_only", 1, 20, 0, "16c_execid_serial_same.json",
            execution_id=serial_exec_id, token=tenants[10]["agent_token"],
        )
        bg1.wait(timeout=120)
        serial_probe = docker_cp_and_run("aegis-control-plane", "chain_probe.py", [
            serial_exec_id, tenants[11]["org_id"], tenants[11]["agent_id"],
        ])
        (RESULTS_DIR / "16c_execid_serial_same_chain_probe.json").write_text(serial_probe, encoding="utf-8")

        bg2 = exec_multitenant_bg("bg_noise_2", "16c_execid_bg2.json", clients=30, requests_per_client=2, concurrency=30)
        concurrent_exec_id = f"16c-concurrent-{uuid.uuid4()}"
        concurrent_result = exec_bench_client(
            "authorize_only", 20, 20, 0, "16c_execid_concurrent_same.json",
            execution_id=concurrent_exec_id, token=tenants[12]["agent_token"],
        )
        bg2.wait(timeout=120)
        concurrent_probe = docker_cp_and_run("aegis-control-plane", "chain_probe.py", [
            concurrent_exec_id, tenants[13]["org_id"], tenants[13]["agent_id"],
        ])
        (RESULTS_DIR / "16c_execid_concurrent_same_chain_probe.json").write_text(concurrent_probe, encoding="utf-8")

        index["execution_id_pattern"] = {
            "serial_same": {"result": serial_result, "chain_probe": json.loads(serial_probe)},
            "concurrent_same": {"result": concurrent_result, "chain_probe": json.loads(concurrent_probe)},
        }
        print(f"[execid] serial_same errors={serial_result['error_count']} "
              f"verifier_pass={index['execution_id_pattern']['serial_same']['chain_probe']['verifier_result']['pass']}")
        print(f"[execid] concurrent_same errors={concurrent_result['error_count']} "
              f"verifier_pass={index['execution_id_pattern']['concurrent_same']['chain_probe']['verifier_result']['pass']}")

        print("\n=== Section 6: cross-tenant isolation adversarial probe ===")
        iso_out = f"/bench/results/16c_isolation_probe.json"
        sh([
            "docker", "compose", *COMPOSE_FILES, "exec", "-T", "bench-runner",
            "python", "tenant_isolation_probe.py", "--tenant-a", "20", "--tenant-b", "21",
            "--out", iso_out,
        ], stdout=subprocess.DEVNULL)
        isolation_result = json.loads((RESULTS_DIR / "16c_isolation_probe.json").read_text(encoding="utf-8"))

        eat_out = docker_cp_and_run("aegis-enforcement-gateway", "eat_cross_tenant_probe.py")
        (RESULTS_DIR / "16c_eat_cross_tenant.json").write_text(eat_out, encoding="utf-8")
        index["isolation"] = {"tenant_isolation_probe": isolation_result, "eat_cross_tenant": json.loads(eat_out)}
        print(f"[isolation] test1={isolation_result['test1_execution_id_cross_tenant']['verdict']} "
              f"test2={isolation_result['test2_shared_request_id']['verdict']}")

        index["db_snapshots"].append(db_snapshot("end_of_phase"))
        index["restart_counts"] = container_restart_counts()

    finally:
        monitor.terminate()
        try:
            monitor.wait(timeout=10)
        except subprocess.TimeoutExpired:
            monitor.kill()

    (RESULTS_DIR / "16c_index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    print(f"\nWrote {RESULTS_DIR / '16c_index.json'}")


if __name__ == "__main__":
    main()
