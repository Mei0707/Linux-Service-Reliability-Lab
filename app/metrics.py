import os

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    multiprocess,
)

REQUESTS = Counter(
    "lab_http_requests_total", "Completed HTTP requests", ["method", "route", "status"]
)
LATENCY = Histogram(
    "lab_http_request_duration_seconds",
    "HTTP duration",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.12, 0.2, 0.5, 1, 2, 5, 10),
)
INFLIGHT = Gauge("lab_http_inflight", "Active HTTP requests", multiprocess_mode="livesum")
CACHE = Counter("lab_cache_requests_total", "Cache lookup outcomes", ["result"])
for outcome in ("hit", "miss", "disabled"):
    CACHE.labels(outcome)
CACHE_ERRORS = Counter("lab_cache_errors_total", "Redis failures", ["operation"])
DB_DURATION = Histogram(
    "lab_db_operation_duration_seconds",
    "DB operation including pool wait",
    ["operation"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.5, 1, 2, 5),
)
DB_ERRORS = Counter("lab_db_errors_total", "Database failures", ["type"])
POOL = Gauge("lab_db_pool_connections", "Application pool connections", multiprocess_mode="livesum")
WAITING = Gauge("lab_db_pool_waiting", "Application pool waiters", multiprocess_mode="livesum")
EXPECTED = Gauge("lab_expected_replicas", "Configured number of replicas", multiprocess_mode="max")


def exposition():
    if os.getenv("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        return generate_latest(registry)
    return generate_latest()
