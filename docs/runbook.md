# Failure drills and diagnosis

Start the healthy default stack with `make up`. Keep a small read-heavy workload running in another terminal if the drill needs traffic. Do not combine a drill with a benchmark measurement. There may be one active drill per checkout; its recovery journal is `artifacts/active-drill.json`.

Before starting a drill, `make seed` resets the lab's links/counters/cache and populates the read corpus. In a second terminal, `.venv/bin/python -m load.runner --users 20 --warmup 3 --duration 300 --output artifacts/drill-load.json` generates traffic. This traffic run records symptoms under intentional failure and is not a clean capacity benchmark.

## Human measurement protocol

1. Generate a randomized schedule with `.venv/bin/python -m scripts.drill plan --seed 20261023`. A separate injector should keep the failure identity hidden where practical.
2. Run all `logs-only` trials using container logs and command-line health checks. Do not use the dashboard for these trials. The structured log format remains the same in both rounds; this comparison measures the additional observability tools available to the operator.
3. At the first observed symptom, record it with `scripts.drill symptom`. This is an operator timestamp; injection time is stored separately. Delayed marking will understate diagnosis time, so record promptly.
4. Investigate. On confirmation, record the root cause and concrete evidence with `scripts.drill confirm`. This stores the confirmation timestamp, captures logs, heals the fault, and writes `docs/incidents/DRILL_ID.md`.
5. Check readiness, correct resolution and error recovery. Add recovery checks and mitigation to the incident record.
6. Repeat the randomized `observability` round using Grafana, request IDs, exporters, health checks, `ss`, and packet captures. Run `scripts.drill report` for paired-fault mean times and the relative reduction. Negative reduction is a valid result.

Only confirmed human rounds enter the comparison. Automated verification records use `validation`, with no invented diagnosis duration. A report with fewer than all six paired faults is incomplete. Repeating the same scenario can improve speed through familiarity; keep that limitation with any reported gain and consider different operators or a counterbalanced schedule.

## Faults

All commands below have this form:

```sh
.venv/bin/python -m scripts.drill start FAULT --round observability
.venv/bin/python -m scripts.drill symptom DRILL_ID --symptoms 'Observed symptoms'
.venv/bin/python -m scripts.drill confirm DRILL_ID --root-cause 'Confirmed cause' --evidence 'Signals that establish it'
```

| Fault | Injection and scope | Useful evidence | Recovery |
|---|---|---|---|
| `db-latency` | `tc netem delay 200ms` on PostgreSQL `eth0` egress | DB operation p95 rises; packet timing; `tc qdisc show` | Delete netem root qdisc |
| `packet-loss` | `tc netem loss 10%` on PostgreSQL egress | Retransmissions, long-tail DB timings, qdisc counters | Delete netem root qdisc |
| `redis-outage` | Stop only this lab's Redis container | `redis_up=0`, cache errors/misses, degraded readiness, continued correct resolutions | Start Redis; verify cache repopulates |
| `db-exhaustion` | Set app role's connection limit to zero and terminate its sessions | DB login errors, pool waiters, app role limit, failed readiness | Restore the saved role limit; let pools reconnect |
| `app-crash` | Disable auto-restart on one replica, then kill it | Nginx failed upstream log, replica count alert, healthy peer traffic | Restore restart policy and start that same container |
| `blocked-port` | Add a commented INPUT DROP rule for TCP 5432 only in the DB network namespace | Readiness fails, SYN retransmits, listening socket still present, exact rule match | Delete the exact rule |

The role exhaustion drill leaves the admin/observer roles available. It does not raise the global `max_connections` utilization ratio; use login/pool signals for this scenario. The netem scenarios also affect exporter traffic and existing connections from that namespace. `200ms` is an **egress delay**, not a promise of exactly 200 ms extra per API call: pool validation and multiple statements can multiply it.

The app usually returns 503 for database unavailability. Nginx may return 502 when every peer has been passively disabled, or 504 on an upstream timeout. A DROP rule can also cause the client to time out before the proxy responds, while the database driver attempts cancellation cleanup. Both application and proxy logs matter. Liveness is independent in the app, but probing it through a proxy with no live upstreams can still fail; query a replica directly to distinguish those cases.

## Inspect and capture

```sh
docker compose logs --since 5m app nginx pgbouncer postgres
docker compose exec -T app python -c 'import urllib.request; print(urllib.request.urlopen("http://localhost:8000/health/live").read())'
docker compose exec -T postgres psql -U postgres -d lab -c "SELECT usename, state, count(*) FROM pg_stat_activity GROUP BY 1,2"
docker compose run --rm --no-deps nettools ss -tnp
docker compose run --rm --no-deps nettools tc -s qdisc show dev eth0
.venv/bin/python -m scripts.drill capture --seconds 10
```

Packet captures appear in `artifacts/*.pcap`. Inspect with Wireshark or `docker compose run --rm --no-deps nettools tcpdump -nn -r /artifacts/FILENAME.pcap`. Captures can include SQL, so keep them as lab artifacts. Packet loss is probabilistic; absence of an application error in a short window does not prove that loss was not applied.

Open Grafana's **Linux Service Reliability Lab — RED / USE** dashboard and Prometheus's Alerts view. Alerts have persistence windows from 15 to 60 seconds; brief tests can validate a symptom before an alert fires. Prometheus notification routing is not configured.

## Cleanup and recovery

```sh
.venv/bin/python -m scripts.drill heal DRILL_ID
curl -i http://localhost:8088/health/ready
make integration
```

`heal` restores the matching fault and produces an incident record even when diagnosis was not timed. It rejects cleanup of an old drill while a different one is active. The scripts keep the active journal if cleanup fails, so fix the underlying error and retry. For a hard-interrupted start, read the active journal for the ID and use `heal`.

If the tools were interrupted independently, inspect the DB namespace before reapplying faults. Remove only the lab netem qdisc or the rule marked `lab-drill`; never copy these firewall operations to the host. Restarting the PostgreSQL container discards its network namespace, but it is a separate outage, so record that recovery action rather than hiding it in diagnosis time.

For a hard-interrupted planned drain, restore normal routing with `docker compose restart nginx`, then `make up` to restore the desired replica count. Check each replica's readiness and Prometheus target discovery. `make down` stops the lab while keeping its named data volumes.
