"""Inject one reversible failure in this Compose lab; record human diagnosis evidence."""

import argparse
import json
import os
import random
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from scripts.locking import exclusive_operation

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
ACTIVE = ARTIFACTS / "active-drill.json"
FAULTS = ["db-latency", "packet-loss", "redis-outage", "db-exhaustion", "app-crash", "blocked-port"]
RULE = [
    "INPUT",
    "-p",
    "tcp",
    "--dport",
    "5432",
    "-m",
    "comment",
    "--comment",
    "lab-drill",
    "-j",
    "DROP",
]


def docker(*args, input=None, check=True):
    result = subprocess.run(
        ["docker", *args], cwd=ROOT, input=input, text=True, capture_output=True, check=False
    )
    if check and result.returncode:
        raise RuntimeError(
            f"Docker operation failed ({result.returncode}): {result.stderr[-4000:]}"
        )
    return result.stdout.strip()


def sql(statement):
    return docker(
        "compose",
        "exec",
        "-T",
        "postgres",
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        "postgres",
        "-d",
        "lab",
        "-c",
        statement,
    )


def net(*args, check=True):
    return docker("compose", "run", "--rm", "--no-deps", "nettools", *args, check=check)


def save(state):
    path = ARTIFACTS / f"{state['id']}.json"
    path.write_text(json.dumps(state, indent=2) + "\n")
    if state.get("status") == "active":
        ACTIVE.write_text(json.dumps(state, indent=2) + "\n")


def load(identifier):
    if not re.fullmatch(r"[A-Za-z0-9-]+", identifier):
        raise ValueError("Invalid drill identifier")
    return json.loads((ARTIFACTS / f"{identifier}.json").read_text())


def inject(state):
    fault = state["fault"]
    if fault in ["db-latency", "packet-loss", "blocked-port"]:
        docker("compose", "build", "nettools")
    if fault == "db-latency":
        existing = net("tc", "qdisc", "show", "dev", "eth0")
        if "netem" in existing:
            raise RuntimeError("A netem qdisc already exists; inspect and heal it first")
        state["cleanup_network"] = True
        save(state)
        net("tc", "qdisc", "add", "dev", "eth0", "root", "netem", "delay", "200ms")
    elif fault == "packet-loss":
        existing = net("tc", "qdisc", "show", "dev", "eth0")
        if "netem" in existing:
            raise RuntimeError("A netem qdisc already exists; inspect and heal it first")
        state["cleanup_network"] = True
        save(state)
        net("tc", "qdisc", "add", "dev", "eth0", "root", "netem", "loss", "10%")
    elif fault == "blocked-port":
        if "lab-drill" in net("iptables", "-S", "INPUT"):
            raise RuntimeError(
                "A lab-drill firewall rule already exists; inspect and heal it first"
            )
        state["cleanup_network"] = True
        save(state)
        net("iptables", "-I", *RULE)
    elif fault == "redis-outage":
        docker("compose", "stop", "redis")
    elif fault == "db-exhaustion":
        state["previous_connection_limit"] = int(
            sql("SELECT rolconnlimit FROM pg_roles WHERE rolname='lab'").splitlines()[2].strip()
        )
        save(state)
        sql(
            "ALTER ROLE lab CONNECTION LIMIT 0; SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename='lab'"
        )
    elif fault == "app-crash":
        replicas = docker("compose", "ps", "-q", "app").splitlines()
        if len(replicas) < 2:
            raise RuntimeError("App-crash drill needs at least two replicas")
        state["container"] = replicas[0]
        save(state)
        docker("update", "--restart=no", state["container"])
        docker("kill", state["container"])


def heal(state):
    if ACTIVE.exists() and json.loads(ACTIVE.read_text())["id"] != state["id"]:
        raise RuntimeError("A different drill is active; do not change its fault state")
    if state.get("status") == "healed":
        return
    fault = state["fault"]
    if fault in ["db-latency", "packet-loss"] and state.get("cleanup_network"):
        # Absence of the root netem qdisc is already a healed state.
        net("tc", "qdisc", "del", "dev", "eth0", "root", check=False)
        if "netem" in net("tc", "qdisc", "show", "dev", "eth0"):
            raise RuntimeError("Netem cleanup failed; the active journal has been retained")
    elif fault == "blocked-port" and state.get("cleanup_network"):
        net("iptables", "-D", *RULE, check=False)
        if "lab-drill" in net("iptables", "-S", "INPUT"):
            raise RuntimeError("Firewall cleanup failed; the active journal has been retained")
    elif fault == "redis-outage":
        docker("compose", "start", "redis")
    elif fault == "db-exhaustion" and "previous_connection_limit" in state:
        previous = int(state["previous_connection_limit"])
        sql(f"ALTER ROLE lab CONNECTION LIMIT {previous}")
    elif fault == "app-crash" and state.get("container"):
        docker("update", "--restart=unless-stopped", state["container"])
        docker("start", state["container"])
    state["healed_at"] = time.time()
    state["status"] = "healed"
    save(state)
    if ACTIVE.exists() and json.loads(ACTIVE.read_text())["id"] == state["id"]:
        ACTIVE.unlink()


def write_incident(state):
    def stamp(value):
        return datetime.fromtimestamp(value, timezone.utc).isoformat() if value else "Not recorded"

    content = f"""# Incident {state["id"]}

- Round: {state["round"]}
- Injected failure: {state["fault"]}
- Injection completed: {stamp(state.get("injected_at"))}
- Symptoms first observed: {stamp(state.get("symptom_at"))}
- Root cause confirmed: {stamp(state.get("confirmed_at"))}
- Recovery command completed: {stamp(state.get("healed_at"))}
- Diagnosis seconds: {state.get("diagnosis_seconds", "Not measured")}
- Automated verification: {stamp(state.get("validation_verified_at"))}

## Symptoms and impact

{state.get("symptoms", "Not recorded; add observed errors, latency and affected endpoints.")}

## Root cause and evidence

{state.get("root_cause", "Not confirmed by an operator.")}

{state.get("evidence", "No diagnosis evidence recorded.")}

## Recovery

The lab cleanup command for {state["fault"]} completed. Verify readiness, correctness,
error rates and latency after recovery; command completion alone is not proof of service recovery.

{state.get("recovery_evidence", "Post-recovery service verification has not been recorded.")}

## Follow-up

Record the recovery checks, mitigation, and the signal that would have shortened diagnosis.
Validation rounds are excluded from the diagnosis comparison.
"""
    path = ROOT / "docs/incidents" / f"{state['id']}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def compare():
    rounds = {"logs-only": {}, "observability": {}}
    for path in sorted(ARTIFACTS.glob("*.json")):
        state = json.loads(path.read_text())
        if not isinstance(state, dict):
            continue
        if state.get("round") in rounds and state.get("diagnosis_seconds") is not None:
            rounds[state["round"]][state["fault"]] = state["diagnosis_seconds"]
    paired = sorted(set(rounds["logs-only"]) & set(rounds["observability"]))
    if not paired:
        raise ValueError("No paired human diagnosis times; run and confirm both rounds first")
    before = sum(rounds["logs-only"][fault] for fault in paired) / len(paired)
    after = sum(rounds["observability"][fault] for fault in paired) / len(paired)
    result = {
        "paired_faults": paired,
        "logs_only_mean_seconds": before,
        "observability_mean_seconds": after,
        "reduction_percent": (1 - after / before) * 100 if before else None,
        "limitation": "Repeated exposure, operator differences, and order effects can bias this comparison.",
    }
    (ARTIFACTS / "diagnosis-comparison.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--seed", type=int, default=20261023)
    start = sub.add_parser("start")
    start.add_argument("fault", choices=FAULTS)
    start.add_argument(
        "--round", choices=["logs-only", "observability", "validation"], required=True
    )
    symptom = sub.add_parser("symptom")
    symptom.add_argument("id")
    symptom.add_argument("--symptoms", required=True)
    confirm = sub.add_parser("confirm")
    confirm.add_argument("id")
    confirm.add_argument("--root-cause", required=True)
    confirm.add_argument("--evidence", required=True)
    cleanup = sub.add_parser("heal")
    cleanup.add_argument("id")
    capture = sub.add_parser("capture")
    capture.add_argument("--seconds", type=int, default=10)
    sub.add_parser("report")
    args = parser.parse_args()
    ARTIFACTS.mkdir(exist_ok=True)
    if args.command == "plan":
        rng = random.Random(args.seed)
        plan = {"seed": args.seed, "rounds": {}}
        for name in ["logs-only", "observability"]:
            order = FAULTS.copy()
            rng.shuffle(order)
            plan["rounds"][name] = order
        path = ARTIFACTS / "drill-plan.json"
        path.write_text(json.dumps(plan, indent=2))
        print(path)
    elif args.command == "start":
        # Exclusive reservation prevents two injectors racing in the same checkout.
        descriptor = os.open(ACTIVE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        state = {
            "id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + "-"
            + uuid.uuid4().hex[:8],
            "fault": args.fault,
            "round": args.round,
            "status": "active",
        }
        with os.fdopen(descriptor, "w") as file:
            json.dump(state, file)
        save(state)
        try:
            inject(state)
            state["injected_at"] = time.time()
            save(state)
        except BaseException:
            heal(state)
            raise
        print(state["id"])
        print(f"Cleanup: .venv/bin/python -m scripts.drill heal {state['id']}")
    elif args.command == "symptom":
        state = load(args.id)
        if state["status"] != "active" or state.get("symptom_at"):
            raise ValueError("Symptoms can be recorded once, while the drill is active")
        state.update(symptom_at=time.time(), symptoms=args.symptoms)
        save(state)
    elif args.command == "confirm":
        state = load(args.id)
        if state["status"] != "active" or not state.get("symptom_at"):
            raise ValueError("Record symptoms on an active drill before confirming the diagnosis")
        now = time.time()
        state.update(
            confirmed_at=now,
            diagnosis_seconds=now - state["symptom_at"],
            root_cause=args.root_cause,
            evidence=args.evidence,
        )
        save(state)
        (ARTIFACTS / f"{args.id}.logs.jsonl").write_text(
            docker("compose", "logs", "--no-color", "--tail", "500")
        )
        heal(state)
        print(write_incident(state))
    elif args.command == "heal":
        state = load(args.id)
        heal(state)
        print(write_incident(state))
    elif args.command == "capture":
        if not 1 <= args.seconds <= 60:
            parser.error("Capture duration must be 1–60 seconds")
        docker("compose", "build", "nettools")
        filename = datetime.now(timezone.utc).strftime("capture-%Y%m%dT%H%M%SZ.pcap")
        net(
            "timeout",
            str(args.seconds),
            "tcpdump",
            "-i",
            "eth0",
            "-U",
            "-w",
            f"/artifacts/{filename}",
            "tcp",
            "port",
            "5432",
            check=False,
        )
        path = ARTIFACTS / filename
        if not path.exists() or path.stat().st_size < 24:
            raise RuntimeError("Packet capture did not produce a valid artifact")
        print(path)
    elif args.command == "report":
        compare()


if __name__ == "__main__":
    with exclusive_operation(skip="--help" in sys.argv or "-h" in sys.argv):
        main()
