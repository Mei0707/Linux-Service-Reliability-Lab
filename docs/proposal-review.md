# Proposal review and implementation decisions

The proposal has a strong, bounded backend scope. Its most valuable requirement is that claims come from recorded evidence. The implementation preserves that requirement and the listed stack. The following details needed clarification before implementation.

| Proposal item | Review | Implemented decision |
|---|---|---|
| 1,000 concurrent connections at sub-120 ms p95 | Connections, virtual users and in-flight requests measure different things. Hardware and workload pacing were unspecified. | Save virtual users, Nginx established TCP socket samples, app in-flight metrics and load-generator utilization. Treat the latency number as an unverified target until measured on the declared environment. |
| Cache-aside and hit statistics | Updating one hot row on every read would hide much of the caching benefit behind lock contention. Redis-only counters could lose data on the very outage this lab injects. | Cache immutable URL lookups; persist each hit in one of 64 PostgreSQL counter buckets. Stats sum the committed buckets. Successful resolution waits for its write. This intentionally retains measurable write amplification. |
| TTL versus staleness | No update or delete endpoint exists, so URLs never change after creation. There is no update-driven staleness experiment in this scope. | TTL experiments measure hit rate and lookup cost. Writes invalidate the newly allocated code. Do not claim staleness improvements for immutable data. |
| Idempotent retries | In-memory keys would break across replicas; check-then-insert would race. | Store the key and normalized payload fingerprint on the link row under a database unique constraint. An insert conflict waits for a concurrent transaction, then checks the stored fingerprint. The record is retained with the link. |
| Baseline “no pooling” | Any async app needs a bounded connection policy. Removing that policy introduces another changed variable. | Every worker uses an identical bounded Psycopg client pool; “direct” versus “pooled” means PostgreSQL directly versus PgBouncer transaction pooling. |
| Readiness and Nginx draining | Compose health status does not remove an upstream from open-source Nginx during runtime. | Passive failure handling for crashes, DNS discovery for replica changes, and an explicit drain script that rewrites/reloads the upstream before stopping a replica. |
| Multiworker metrics | A default in-process Prometheus registry would report a randomly selected worker's counters. | Configure Prometheus multiprocess storage, clear it on container startup, and retire dead-worker gauges through Gunicorn's `child_exit` hook. Scrape every replica through DNS discovery. |
| One-variable tuning | A final configuration changes several settings and alone cannot support per-change attribution. | A parent-linked matrix isolates workers, replicas, caching, TTL, DB route, keepalive, timeout, file limits, backlog and Nginx connection limits. The report compares only available parent/child measurements for attribution. |
| Connection exhaustion drill | Restarting PostgreSQL to lower `max_connections` also injects a database outage and disrupts monitoring. | Exhaust the non-superuser app role by setting its connection limit to zero and terminating its old sessions. Restore the saved limit afterward. Clearly label this as **role connection exhaustion**, not global server exhaustion. |
| Linux fault tools | Host firewall changes would reach outside the lab. | A disposable tools container joins only PostgreSQL's network namespace. `tc`, `iptables`, `ss`, and `tcpdump` run there. |
| Diagnosis time | The injector knows the answer. A repeated failure is also easier through familiarity. | Record operator symptom and confirmation timestamps separately from injection time; randomize order; support a separate injector. Exclude automated validation rounds from comparison. |
| Reports and resume claims | Performance improvements and diagnosis gains cannot be supplied honestly before experiments are run. | Generate reports from raw results. Include negative outcomes and missing measurements. Never populate a target or an expected improvement as a result. |

## Deliverable mapping

| Deliverable | Implementation |
|---|---|
| API and PostgreSQL schema | `app/`, `sql/`, API and concurrency tests |
| Three stateless replicas and Nginx | `compose.yaml`, dynamic upstream, `scripts/drain.py` |
| Cache and PgBouncer | Redis timeout/cooldown fallback; transaction pool and disabled prepared statements |
| Observability | Structured request logs, bounded route labels, request IDs, multiworker metrics, 3 exporters, provisioned Grafana dashboard, Prometheus alert rules |
| Benchmarks | Versioned weighted Zipfian workload, warmup and fixed window, repeated medians, JSON/CSV, environment/config capture, charts |
| Failure drills | Six faults, exclusive active drill record, explicit cleanup, namespace packet captures, incident generation |
| Diagnosis comparison | Operator-driven two-round workflow and paired-fault timing report |
| Documentation | Architecture, benchmark methodology, measured report, runbook and incident evidence |

## Reference checks

Implementation decisions were checked against the official [Nginx upstream documentation](https://nginx.org/en/docs/http/ngx_http_upstream_module.html), [Nginx retry semantics](https://nginx.org/en/docs/http/ngx_http_proxy_module.html), [Compose startup ordering](https://docs.docker.com/compose/how-tos/startup-order/), [Prometheus Python multiprocess requirements](https://prometheus.github.io/client_python/multiprocess/), [Psycopg pool lifecycle](https://www.psycopg.org/psycopg3/docs/advanced/pool.html), [prepared statement compatibility](https://www.psycopg.org/psycopg3/docs/advanced/prepare.html), [PgBouncer configuration](https://www.pgbouncer.org/config), and [Locust's programmatic runner API](https://docs.locust.io/en/stable/api.html).
