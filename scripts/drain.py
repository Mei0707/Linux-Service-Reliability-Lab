"""Drain a chosen app replica through an explicit Nginx reload before stopping it."""

import argparse
import re
import sys
import time

from scripts.drill import ACTIVE, docker
from scripts.locking import exclusive_operation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replica", type=int, default=1)
    parser.add_argument("--restart", action="store_true")
    args = parser.parse_args()
    if ACTIVE.exists():
        raise RuntimeError("Heal the active failure drill before planned maintenance")
    replicas = sorted(docker("compose", "ps", "-q", "app").splitlines())
    if len(replicas) < 2 or not 1 <= args.replica <= len(replicas):
        parser.error("Choose a running replica from a group of at least two")
    victim = replicas[args.replica - 1]
    original = docker("compose", "exec", "-T", "nginx", "cat", "/etc/nginx/nginx.conf")
    remaining = []
    for container in replicas:
        if container != victim:
            ip = docker(
                "inspect",
                "--format",
                "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                container,
            )
            if not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", ip):
                raise RuntimeError("Expected one IPv4 Compose network")
            remaining.append(f"server {ip}:8000 max_fails=1 fail_timeout=5s;")
    replacement = re.sub(
        r"server app:8000 resolve max_fails=1 fail_timeout=5s;",
        "\n        ".join(remaining),
        original,
    )
    if replacement == original:
        raise RuntimeError("Nginx upstream differs from the managed lab configuration")
    docker("exec", victim, "touch", "/tmp/lab-draining")
    restarted_unready = False
    try:
        docker(
            "compose",
            "exec",
            "-T",
            "nginx",
            "sh",
            "-c",
            "cat > /etc/nginx/nginx.conf",
            input=replacement,
        )
        docker("compose", "exec", "-T", "nginx", "nginx", "-t")
        docker("compose", "exec", "-T", "nginx", "nginx", "-s", "reload")
        # Nginx bounds old worker lifetime at 20s; the app deadline is 5s.
        time.sleep(21)
        docker("stop", "--time", "25", victim)
        if args.restart:
            docker("start", victim)
            restarted_unready = True
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if docker("inspect", "--format", "{{.State.Health.Status}}", victim) == "healthy":
                    restarted_unready = False
                    break
                time.sleep(1)
            else:
                raise RuntimeError("Restarted replica did not become healthy")
        print(f"Drained {'and restarted' if args.restart else 'and stopped'} {victim}")
    finally:
        docker("exec", victim, "rm", "-f", "/tmp/lab-draining", check=False)
        docker(
            "compose",
            "exec",
            "-T",
            "nginx",
            "sh",
            "-c",
            "cat > /etc/nginx/nginx.conf",
            input=replacement if restarted_unready else original,
        )
        docker("compose", "exec", "-T", "nginx", "nginx", "-t")
        docker("compose", "exec", "-T", "nginx", "nginx", "-s", "reload")


if __name__ == "__main__":
    with exclusive_operation(skip="--help" in sys.argv or "-h" in sys.argv):
        main()
