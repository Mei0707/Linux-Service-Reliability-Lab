"""Verify failure injection and recovery. This does not measure human diagnosis skill."""

import argparse
import json
import os
import subprocess
import sys
import time
import uuid

import httpx

from scripts.drill import ARTIFACTS, FAULTS, ROOT, docker, net, sql, write_incident

CAUSES = {
    "db-latency": "PostgreSQL egress had an injected 200 ms netem delay; DB-backed resolutions slowed.",
    "packet-loss": "PostgreSQL egress had injected 10% packet loss, causing retransmission-sensitive tail latency.",
    "redis-outage": "The Redis container was stopped; the service used its durable PostgreSQL fallback.",
    "db-exhaustion": "The non-superuser app role had connection limit zero and its existing sessions were terminated.",
    "app-crash": "One app replica was killed with restart disabled; Nginx continued routing reads to its peers.",
    "blocked-port": "A namespace-local INPUT DROP rule blocked TCP 5432 while the database listener remained running.",
}


def cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "scripts.drill", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def wait_ready(client):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            if client.get("/health/ready").status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise RuntimeError("Service failed to recover readiness")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--faults", nargs="+", choices=FAULTS, default=FAULTS)
    args = parser.parse_args()
    summary_path = ARTIFACTS / "drill-validation.json"
    results = (
        [
            record
            for record in json.loads(summary_path.read_text())
            if record["fault"] not in args.faults
        ]
        if summary_path.exists()
        else []
    )

    def unavailable(client, path):
        try:
            response = client.get(path)
            assert response.status_code in [502, 503, 504], response.text
            return {"status": response.status_code}
        except httpx.TimeoutException:
            return {"client_timeout": True, "timeout_seconds": 10}

    with httpx.Client(base_url=os.getenv("LAB_URL", "http://127.0.0.1:8088"), timeout=10) as client:
        for fault in args.faults:
            wait_ready(client)
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                peers = docker("compose", "ps", "-q", "app").splitlines()
                if peers and all(
                    docker("inspect", "--format", "{{.State.Health.Status}}", peer) == "healthy"
                    for peer in peers
                ):
                    break
                time.sleep(1)
            else:
                raise RuntimeError("Not all app peers recovered before the next drill")
            time.sleep(6)  # Expire passive failure state left by the preceding drill.
            response = client.post(
                "/links",
                json={"url": "https://example.com/drill"},
                headers={"Idempotency-Key": uuid.uuid4().hex},
            )
            response.raise_for_status()
            code = response.json()["code"]
            client.get(f"/links/{code}").raise_for_status()
            identifier = cli("start", fault, "--round", "validation").splitlines()[0]
            state_path = ARTIFACTS / f"{identifier}.json"
            try:
                assert client.get("/health/live").status_code == 200
                evidence = {}
                if fault == "db-latency":
                    start = time.monotonic()
                    client.get(f"/links/{code}").raise_for_status()
                    evidence["resolve_seconds"] = time.monotonic() - start
                    assert evidence["resolve_seconds"] >= 0.18
                    evidence["qdisc"] = net("tc", "qdisc", "show", "dev", "eth0")
                    assert "delay 200ms" in evidence["qdisc"]
                    evidence["capture"] = cli("capture", "--seconds", "3").strip()
                elif fault == "packet-loss":
                    evidence["qdisc"] = net("tc", "qdisc", "show", "dev", "eth0")
                    assert "loss 10%" in evidence["qdisc"]
                    evidence["responses"] = [
                        client.get(f"/links/{code}").status_code for _ in range(10)
                    ]
                elif fault == "redis-outage":
                    ready = client.get("/health/ready")
                    assert ready.status_code == 200
                    assert ready.json()["cache"] == "degraded"
                    evidence["readiness"] = ready.json()
                    for _ in range(10):
                        client.get(f"/links/{code}").raise_for_status()
                    assert client.get(f"/links/{code}/stats").json()["hits"] == 11
                    evidence["exact_hits_after_fallback"] = 11
                elif fault == "db-exhaustion":
                    evidence["role_limit"] = sql(
                        "SELECT rolconnlimit FROM pg_roles WHERE rolname='lab'"
                    )
                    evidence["readiness"] = unavailable(client, "/health/ready")
                    evidence["resolution"] = unavailable(client, f"/links/{code}")
                elif fault == "app-crash":
                    state = json.loads(state_path.read_text())
                    evidence["container_running"] = docker(
                        "inspect", "--format", "{{.State.Running}}", state["container"]
                    )
                    assert evidence["container_running"] == "false"
                    evidence["responses"] = [
                        client.get(f"/links/{code}").status_code for _ in range(20)
                    ]
                    time.sleep(6)
                    evidence["responses_after_failover"] = [
                        client.get(f"/links/{code}").status_code for _ in range(10)
                    ]
                    assert all(status == 200 for status in evidence["responses_after_failover"])
                elif fault == "blocked-port":
                    evidence["readiness"] = unavailable(client, "/health/ready")
                    evidence["resolution"] = unavailable(client, f"/links/{code}")
                    evidence["rules"] = net("iptables", "-S", "INPUT")
                    assert "lab-drill" in evidence["rules"]
                state = json.loads(state_path.read_text())
                state.update(
                    root_cause=CAUSES[fault]
                    + " Automated validation, excluded from human diagnosis timing.",
                    symptoms="Automated probes observed the responses and dependency state recorded below.",
                    evidence=json.dumps(evidence, indent=2),
                    validation="passed",
                    validation_verified_at=time.time(),
                )
                state_path.write_text(json.dumps(state, indent=2))
            finally:
                cli("heal", identifier)
            wait_ready(client)
            client.get(f"/links/{code}").raise_for_status()
            state = json.loads(state_path.read_text())
            state["recovery_verified_at"] = time.time()
            state["recovery_evidence"] = (
                "Readiness 200 and successful link resolution after cleanup."
            )
            state_path.write_text(json.dumps(state, indent=2))
            write_incident(state)
            results.append(
                {"fault": fault, "incident": f"docs/incidents/{identifier}.md", "status": "passed"}
            )
            summary_path.write_text(json.dumps(results, indent=2))
            print(f"{fault}: verified and recovered", flush=True)
    (ARTIFACTS / "drill-validation.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
