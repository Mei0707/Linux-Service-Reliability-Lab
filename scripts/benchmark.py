"""Apply versioned configurations, reset lab data, run Locust, and save real evidence."""

import argparse
import csv
import hashlib
import json
import os
import platform
import shlex
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from scripts.locking import exclusive_operation

ROOT = Path(__file__).resolve().parents[1]
MATRIX = json.loads((ROOT / "experiments/matrix.json").read_text())


def config_for(name):
    case = MATRIX[name]
    config = config_for(case["parent"]) if "parent" in case else {}
    return config | case["changes"]


def command(args, *, env=None, capture=False, input=None):
    return subprocess.run(
        args,
        cwd=ROOT,
        env=env,
        check=True,
        text=True,
        input=input,
        stdout=subprocess.PIPE if capture else None,
    )


def compose(args, env, **kwargs):
    return command(["docker", "compose", *args], env=env, **kwargs)


def compose_logged(args, env, path):
    with path.open("a") as file:
        result = subprocess.run(
            ["docker", "compose", *args], cwd=ROOT, env=env, stdout=file, stderr=subprocess.STDOUT
        )
    if result.returncode:
        raise RuntimeError(f"Docker Compose failed; see {path}")


def reset_and_seed(env):
    profile = json.loads((ROOT / "load/profile.json").read_text())
    sql = f"""TRUNCATE links CASCADE;
    INSERT INTO links (code, url, request_hash)
      SELECT 'seed' || lpad(i::text, 8, '0'), 'https://example.com/seed/' || i, repeat('0', 64)
      FROM generate_series(1, {int(profile["key_count"])}) i;
    """
    compose(
        ["exec", "-T", "postgres", "psql", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "lab"],
        env,
        input=sql,
        capture=True,
    )
    compose(["exec", "-T", "redis", "redis-cli", "FLUSHDB"], env, capture=True)


def wait_ready(host):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(host.rstrip("/") + "/health/ready", timeout=3) as response:
                if response.status == 200:
                    return
        except (OSError, ValueError):
            pass
        time.sleep(1)
    raise RuntimeError("Service did not become ready")


def sample_connections(env, stop, samples):
    while not stop.is_set():
        try:
            result = subprocess.run(
                [
                    "docker",
                    "compose",
                    "exec",
                    "-T",
                    "nginx",
                    "sh",
                    "-c",
                    "awk '$2 ~ /:0050$/ && $4 == \"01\" {n++} END {print n+0}' /proc/net/tcp /proc/net/tcp6",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                timeout=4,
                check=True,
            )
            samples.append(
                {"at": time.time(), "established_client_connections": int(result.stdout.strip())}
            )
        except (subprocess.SubprocessError, ValueError):
            samples.append({"at": time.time(), "established_client_connections": None})
        stop.wait(5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--experiments", nargs="+", default=["baseline", "tuned"], choices=[*MATRIX, "all"]
    )
    parser.add_argument("--users", type=int, default=1000)
    parser.add_argument("--warmup", type=float, default=30)
    parser.add_argument("--duration", type=float, default=120)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--max-error-rate", type=float, help="Optional failure threshold for CI smoke runs"
    )
    parser.add_argument("--target", default="http://127.0.0.1:8088")
    parser.add_argument("--prometheus", default="http://127.0.0.1:9095")
    parser.add_argument("--load-host", help="Optional SSH host for a separate load-generator VM")
    parser.add_argument(
        "--remote-dir", default="~/reliability-lab", help="Use an absolute path with --load-host"
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.repeats < 1 or args.users < 1 or args.duration <= 0 or args.warmup < 0:
        parser.error("Invalid repetition, user count or timing")
    if args.max_error_rate is not None and not 0 <= args.max_error_rate <= 1:
        parser.error("--max-error-rate must be between 0 and 1")
    if args.load_host and (
        not args.remote_dir.startswith("/")
        or "127.0.0.1" in args.target
        or "localhost" in args.target
    ):
        parser.error(
            "Remote runs need an absolute --remote-dir and a target reachable from the remote VM"
        )
    names = list(MATRIX) if "all" in args.experiments else args.experiments
    if (ROOT / "artifacts/active-drill.json").exists():
        parser.error("Heal the active failure drill before benchmarking")
    output = args.output or ROOT / "results" / datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )
    output.mkdir(parents=True, exist_ok=False)
    print(
        f"Resetting ONLY this Compose lab's links/cache for each repetition; results: {output}",
        flush=True,
    )
    base_env = os.environ.copy()
    compose_logged(["build", "app", "pgbouncer"], base_env, output / "setup.log")
    compose_logged(
        [
            "up",
            "-d",
            "--wait",
            "postgres",
            "redis",
            "pgbouncer",
            "prometheus",
            "grafana",
            "redis-exporter",
            "postgres-exporter",
        ],
        base_env,
        output / "setup.log",
    )
    metadata = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "experiments": names,
        "arguments": vars(args) | {"output": str(output)},
        "separate_load_host": bool(args.load_host),
        "docker": json.loads(
            command(
                [
                    "docker",
                    "info",
                    "--format",
                    '{"server_version":{{json .ServerVersion}},"os":{{json .OperatingSystem}},'
                    '"kernel":{{json .KernelVersion}},"architecture":{{json .Architecture}},'
                    '"cpus":{{.NCPU}},"memory_bytes":{{.MemTotal}},"running_containers":{{.ContainersRunning}}}',
                ],
                capture=True,
            ).stdout
        ),
    }
    (output / "environment.json").write_text(json.dumps(metadata, indent=2, default=str))
    try:
        for name in names:
            env = base_env | config_for(name)
            compose_logged(
                [
                    "up",
                    "-d",
                    "--wait",
                    "--scale",
                    f"app={env['REPLICAS']}",
                    "app",
                    "nginx",
                    "nginx-exporter",
                ],
                env,
                output / f"{name}.log",
            )
            (output / f"{name}.compose.yaml").write_text(
                compose(["config"], env, capture=True).stdout
            )
            (output / f"{name}.images.json").write_text(
                compose(["images", "--format", "json"], env, capture=True).stdout
            )
            wait_ready(args.target)
            for repeat in range(1, args.repeats + 1):
                reset_and_seed(env)
                # Let Prometheus discover recreated replicas and collect initial counters.
                time.sleep(6)
                runner_args = [
                    "-m",
                    "load.runner",
                    "--host",
                    args.target,
                    "--prometheus",
                    args.prometheus,
                    "--users",
                    str(args.users),
                    "--warmup",
                    str(args.warmup),
                    "--duration",
                    str(args.duration),
                ]
                if args.load_host:
                    remote = (
                        "cd "
                        + shlex.quote(args.remote_dir)
                        + " && "
                        + shlex.join([".venv/bin/python", *runner_args])
                    )
                    invocation = ["ssh", args.load_host, remote]
                else:
                    invocation = [sys.executable, *runner_args]
                print(f"{name}, repetition {repeat}/{args.repeats}", flush=True)
                stop = threading.Event()
                server_samples = []
                sampler = threading.Thread(
                    target=sample_connections, args=(env, stop, server_samples), daemon=True
                )
                sampler.start()
                try:
                    result = json.loads(command(invocation, capture=True).stdout)
                finally:
                    stop.set()
                    sampler.join(timeout=5)
                started = datetime.fromisoformat(result["started_at"]).timestamp()
                result["server_samples"] = [
                    sample
                    for sample in server_samples
                    if started <= sample["at"] <= started + result["measurement_seconds"]
                ]
                expected_hash = hashlib.sha256(
                    (ROOT / "load/profile.json").read_bytes()
                ).hexdigest()
                if result["profile_sha256"] != expected_hash:
                    raise RuntimeError(
                        "Load-generator profile differs from the versioned server profile"
                    )
                expected_source = hashlib.sha256(
                    b"".join(
                        (ROOT / "load" / source).read_bytes()
                        for source in ["profile.json", "workload.py", "runner.py", "metrics.py"]
                    )
                ).hexdigest()
                if result.get("load_source_sha256") != expected_source:
                    raise RuntimeError("Load-generator source differs from this checkout")
                result.update(experiment=name, repeat=repeat, configuration=config_for(name))
                prefix = output / f"{name}-{repeat}"
                prefix.with_suffix(".json").write_text(json.dumps(result, indent=2))
                with prefix.with_suffix(".csv").open("w") as file:
                    rows = [*result["endpoints"], result["total"]]
                    writer = csv.DictWriter(file, fieldnames=list(result["total"]))
                    writer.writeheader()
                    writer.writerows(rows)
                if args.max_error_rate is not None and result["error_rate"] > args.max_error_rate:
                    raise RuntimeError(
                        f"{name}: error rate {result['error_rate']:.4f} exceeds {args.max_error_rate}"
                    )
        command([sys.executable, "-m", "scripts.report", str(output)])
    finally:
        # Restore the documented default config without deleting evidence or volumes.
        defaults = base_env | config_for("tuned")
        compose_logged(
            ["up", "-d", "--wait", "--scale", "app=3", "app", "nginx", "nginx-exporter"],
            defaults,
            output / "restore.log",
        )


if __name__ == "__main__":
    with exclusive_operation(skip="--help" in sys.argv or "-h" in sys.argv):
        main()
