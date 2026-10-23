# Linux Service Reliability Lab

A URL-shortener API built to measure scaling, tune dependencies, and diagnose injected failures. Implements [the proposal](proposal.pdf); design corrections and acceptance mapping are in [the proposal review](docs/proposal-review.md).

## Run it

Requires Docker Engine / Docker Desktop with Compose v2.24+ and Python 3.11 or 3.12. Linux is the reference platform for performance measurements; Docker Desktop runs the network drills inside its Linux VM.

```sh
make setup
make up
make verify
```

| Service | Address | Credentials |
|---|---|---|
| API / OpenAPI docs | http://localhost:8088/docs | None |
| Grafana dashboard | http://localhost:3300/d/reliability-lab | `admin` / `lab-grafana` |
| Prometheus / alerts | http://localhost:9095 | None |
| PostgreSQL, for local inspection | `localhost:55432`, database `lab` | `lab` / `lab` |
| Redis, for local inspection | `localhost:56379` | None |

Ports bind to loopback by default. Copy `.env.example` to `.env` to change ports and tuning parameters. Fixed database credentials are for this disposable lab. PostgreSQL, Grafana and Prometheus use persistent named volumes; `make down` preserves them.

```sh
curl -i http://localhost:8088/links \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: demo-1' \
  -d '{"url":"https://example.com/demo"}'
# Substitute the returned code:
curl http://localhost:8088/links/CODE_FROM_RESPONSE
curl http://localhost:8088/links/CODE_FROM_RESPONSE/stats
```

`POST /links` returns 201 on creation and 200 on an identical idempotent replay. Reusing a key with a different normalized URL returns 409. Resolution returns JSON and commits one hit before responding. Missing links return 404. Redis failures fall back to PostgreSQL. Liveness stays independent of dependencies; readiness requires the database and reports Redis as degraded when unavailable. `/metrics` is available internally to Prometheus; Nginx does not publish it.

## Measure it

```sh
make smoke                         # Short local pipeline check
make benchmark                     # Baseline + tuned, 1,000 users, 3 repetitions
make benchmark-all                 # All 16 configurations, 3 repetitions each
# Override settings:
make benchmark ARGS='--experiments baseline cache pooled --users 100 --duration 30'
```

Each repetition **resets the lab's links, hit counts, and Redis cache**, seeds 1,000 deterministic links, ramps users, warms up, and measures a fixed window. It saves raw JSON, CSV, resolved Compose configuration, image IDs, dependency samples, Nginx socket counts, a median report, and an SVG chart under `results/`. The runner restores the default configuration afterward. Run `make up` after an interrupted run if needed.

The workload is versioned in [load/profile.json](load/profile.json): approximately 90% Zipfian reads, 8% writes, 2% stats. Default warmup is 30 seconds **after all users spawn**; measurement is 120 seconds. [The benchmark guide](docs/benchmarking.md) explains attribution, remote load generation, and interpreting results. [The benchmark report](docs/benchmark-report.md) records the evidence collected during implementation and its limits.

The target is 1,000 established connections with read p95 below 120 ms. A Locust user count alone does not establish that claim. Reports keep measured results separate from this target.

[Local verification](docs/verification.md) passed 30 tests and all 16 configuration smoke checks. The repeated 1,000-user runs missed the latency target; the report includes the measured errors and limits.

## Break it and diagnose it

```sh
.venv/bin/python -m scripts.drill plan
.venv/bin/python -m scripts.drill start redis-outage --round logs-only
# Copy the printed drill ID into the next commands:
.venv/bin/python -m scripts.drill symptom DRILL_ID --symptoms 'Cache errors and slower reads'
.venv/bin/python -m scripts.drill confirm DRILL_ID \
  --root-cause 'Redis container stopped' --evidence 'Container exited; connection errors in app logs'
# Emergency or untimed cleanup:
.venv/bin/python -m scripts.drill heal DRILL_ID
```

Repeat the randomized schedule with `--round observability`, then run `.venv/bin/python -m scripts.drill report`. Diagnosis timing starts when the operator records the first observed symptoms, and ends on explicit confirmation. The injector's knowledge is not counted as diagnosis. [The runbook](docs/runbook.md) covers all six faults, packet capture, cleanup, and incident records.

`make drills-verify` automatically checks all six injections and recovery. Its incident records are labeled `validation` and excluded from human timing comparisons. The fault tools receive network capabilities only inside the database container's network namespace. Scripts do not change host firewall rules.

For planned maintenance, `.venv/bin/python -m scripts.drain --replica 1 --restart` fails that replica's readiness, removes it from Nginx routing, drains old workers, then stops/restarts it. Nginx OSS does not automatically consult Docker readiness. See [architecture](docs/architecture.md).

## Repository

| Path | Purpose |
|---|---|
| `app/`, `sql/` | FastAPI service, async DB pool, cache, idempotency and durable counts |
| `compose.yaml`, `infra/` | Replicas, proxy, pools, dependencies, exporters and Grafana dashboard |
| `load/`, `experiments/` | Versioned workload and single-variable experiment matrix |
| `scripts/` | Benchmark/report runners, fault lifecycle, packet capture and draining |
| `tests/` | API correctness, concurrent retries/counts, failure semantics and report checks |
| `docs/incidents/` | Recorded drills and recovery evidence |
| `results/`, `artifacts/` | Local generated evidence; selected evidence is preserved in `docs/evidence/` |

No authentication, Kubernetes, multi-region deployment, or frontend is included. The next work is operating the lab on controlled Linux hosts and collecting defensible tuning and diagnosis results.
