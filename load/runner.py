"""A single measured Locust run. Stdout is one JSON artifact; logs use stderr."""

from gevent import monkey

monkey.patch_all()

import argparse
import hashlib
import json
import os
import platform
import time
from datetime import datetime, timezone
from pathlib import Path

import gevent
import psutil
import requests
from locust.env import Environment
from locust.log import setup_logging

from load.metrics import cache_hit_rate
from load.workload import PROFILE_HASH, LinkUser

QUERIES = {
    "cache_hits": 'lab_cache_requests_total{job="app",result="hit"}',
    "cache_misses": 'lab_cache_requests_total{job="app",result="miss"}',
    "database_connections": 'sum(pg_stat_database_numbackends{datname="lab"})',
    "nginx_connections": "nginx_connections_active",
    "app_inflight": "sum(lab_http_inflight)",
}


def metric_snapshot(url):
    result = {"at": time.time(), "series": {}}
    for name, query in QUERIES.items():
        try:
            response = requests.get(
                url.rstrip("/") + "/api/v1/query", params={"query": query}, timeout=2
            )
            response.raise_for_status()
            values = response.json()["data"]["result"]
            if name in ["cache_hits", "cache_misses"]:
                result["series"][name] = {
                    json.dumps(value["metric"], sort_keys=True): float(value["value"][1])
                    for value in values
                }
            result[name] = sum(float(value["value"][1]) for value in values) if values else None
        except (requests.RequestException, ValueError, KeyError, TypeError):
            result[name] = None
    return result


def entry_result(entry, duration):
    return {
        "method": entry.method,
        "name": entry.name,
        "requests": entry.num_requests,
        "failures": entry.num_failures,
        "rps": entry.num_requests / duration,
        "p50_ms": entry.get_response_time_percentile(0.50),
        "p95_ms": entry.get_response_time_percentile(0.95),
        "p99_ms": entry.get_response_time_percentile(0.99),
    }


def run(args):
    setup_logging("WARNING", None)
    environment = Environment(user_classes=[LinkUser], host=args.host, stop_timeout=5)
    runner = environment.create_local_runner()
    runner.start(args.users, spawn_rate=args.spawn_rate)
    deadline = time.monotonic() + max(60, args.users / args.spawn_rate * 3)
    try:
        while runner.user_count < args.users:
            if time.monotonic() > deadline:
                raise RuntimeError("Load generator did not reach the requested user count")
            gevent.sleep(0.1)
        gevent.sleep(args.warmup)
        before = metric_snapshot(args.prometheus) if args.prometheus else {}
        environment.stats.reset_all()
        started = time.time()
        samples = []
        process = psutil.Process()
        process.cpu_percent()

        def sample():
            while True:
                sample_start = time.monotonic()
                samples.append(
                    {
                        "at": time.time(),
                        "users": runner.user_count,
                        "loadgen_cpu_percent": psutil.cpu_percent(),
                        "loadgen_process_cpu_percent": process.cpu_percent(),
                        "loadgen_rss_bytes": process.memory_info().rss,
                        "loadgen_memory_percent": psutil.virtual_memory().percent,
                        "dependencies": metric_snapshot(args.prometheus) if args.prometheus else {},
                    }
                )
                gevent.sleep(max(0.1, 5 - (time.monotonic() - sample_start)))

        sampler = gevent.spawn(sample)
        gevent.sleep(args.duration)
        duration = time.time() - started
        # Freeze counters at the measurement boundary, before draining in-flight requests.
        total = entry_result(environment.stats.total, duration)
        entries = [entry_result(entry, duration) for entry in environment.stats.entries.values()]
        users_at_end = runner.user_count
        sampler.kill()
        errors = [
            {
                "error": str(error.error),
                "count": error.occurrences,
                "method": error.method,
                "name": error.name,
            }
            for error in environment.stats.errors.values()
        ]
        runner.stop()
        after = metric_snapshot(args.prometheus) if args.prometheus else {}
    finally:
        runner.quit()
    hit_rate = cache_hit_rate(before, after)
    return {
        "schema_version": 2,
        "started_at": datetime.fromtimestamp(started, timezone.utc).isoformat(),
        "host": args.host,
        "profile_sha256": PROFILE_HASH,
        "load_source_sha256": hashlib.sha256(
            b"".join(
                Path(__file__).with_name(name).read_bytes()
                for name in ["profile.json", "workload.py", "runner.py", "metrics.py"]
            )
        ).hexdigest(),
        "http_client": "FastHttpUser",
        "users": args.users,
        "users_at_end": users_at_end,
        "warmup_seconds": args.warmup,
        "measurement_seconds": duration,
        "measurement_target_seconds": args.duration,
        "spawn_rate": args.spawn_rate,
        "total": total,
        "endpoints": entries,
        "error_rate": total["failures"] / total["requests"] if total["requests"] else 1.0,
        "cache_hit_rate": hit_rate,
        "metrics_before": before,
        "metrics_after": after,
        "samples": samples,
        "errors": errors,
        "load_generator": {
            "hostname": platform.node(),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="http://127.0.0.1:8088")
    parser.add_argument("--prometheus", default="http://127.0.0.1:9095")
    parser.add_argument("--users", type=int, default=1000)
    parser.add_argument("--spawn-rate", type=float, default=100)
    parser.add_argument("--warmup", type=float, default=30)
    parser.add_argument("--duration", type=float, default=120)
    parser.add_argument("--output")
    args = parser.parse_args()
    if min(args.users, args.spawn_rate, args.duration) <= 0 or args.warmup < 0:
        parser.error("Users, spawn rate and duration must be positive; warmup must be nonnegative")
    result = run(args)
    encoded = json.dumps(result, allow_nan=False)
    if args.output:
        from pathlib import Path

        Path(args.output).write_text(encoded + "\n")
    print(encoded)


if __name__ == "__main__":
    main()
