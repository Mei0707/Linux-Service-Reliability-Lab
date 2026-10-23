# Benchmark methodology

## Reproduce a run

`make benchmark` runs baseline and tuned configurations, three repetitions each, with 1,000 virtual users. `make benchmark-all` runs every matrix entry. Both commands reset only the named Compose lab's links, counters and Redis database. They retain monitoring history and generated evidence. Do not inject faults or run other load during a benchmark.

For each configuration the runner recreates the affected services, waits for readiness, records the expanded Compose config and image IDs, resets data, seeds exactly 1,000 known links, then starts a separate Locust process. Ramp-up is excluded. A warmup at full user count precedes resetting counters, and a fixed measurement window follows. Completion of outstanding work is excluded from the measured counters. A report uses the median of each metric across repetitions; this is not a pooled percentile or a confidence interval.

The versioned profile selects task types with weights 90:8:2. The realized finite-run mix is approximate; JSON/CSV endpoint counts let you check it. Read/stat keys follow a bounded Zipf distribution with exponent 1.2. Per-user key selection is seeded, while scheduling and creation identifiers are nondeterministic. Newly created load-test links do not enter the fixed read corpus. This keeps the hot-key distribution stable throughout a repetition.

Users pause 1–10 ms between tasks. This is a closed-loop load test: slow responses reduce generated request rate. A low error rate alone does not prove capacity; inspect throughput and tail latency together. Locust percentiles use its response-time histogram and are rounded at larger durations.

## One-variable comparisons

[matrix.json](../experiments/matrix.json) declares each configuration's parent and changed variable. The baseline has one worker/replica, cache disabled, direct PostgreSQL, no upstream keepalive, and conservative descriptor/backlog limits. Each worker always has the same client pool limit. PgBouncer is a distinct database-routing experiment, not the removal of all application-side connection management.

Worker counts 1/2/4/8 and replica counts 1/2/3 form their own branches. Cache TTL branches compare to cache enabled at 60 seconds. The cumulative path is baseline → cache → pooled → keepalive → file-limit → backlog → nginx-connections → tuned (three replicas). `timeout-3s` branches off keepalive. Attribution is reported only when both parent and child are measured. Baseline-to-tuned improvement combines changes and cannot establish an individual change's effect.

For a focused comparison:

```sh
make benchmark ARGS='--experiments baseline cache pooled --users 1000'
```

Keep the same CPU/memory limits, host kernel, network, task profile, warmup, duration and load generator between comparisons. Trials retain warm PostgreSQL buffers; record that fact rather than claiming cold-start performance. Default order is deterministic; repeat the study with randomized order if thermal load or host drift may matter.

## Separate load-generator host

Copy the same project to a second VM and run `make setup` there. On the service VM, expose only the API to that VM using `BIND_ADDRESS` in `.env` and a private network/firewall rule. Run from the service VM:

```sh
make benchmark ARGS='--load-host user@load-vm --remote-dir /home/user/reliability-lab --target http://SERVICE_PRIVATE_IP:8088 --prometheus http://127.0.0.1:19095'
```

Beforehand, forward Prometheus from the load VM to the service VM so metrics remain private. For example, on the load VM keep this connection open:

```sh
ssh -N -L 127.0.0.1:19095:127.0.0.1:9095 user@service-vm
```

`--prometheus` is interpreted on the load-generator VM; socket sampling and Compose operations run on the service VM. Both machines need synchronized clocks for correlating socket samples. The runner checks both the remote profile hash and the combined load runner/workload source hash before accepting results. Deploy the same dependency versions on both hosts as well.

For an independently managed service, run `make seed` on the service host to reset the lab data and populate the corpus, then run `.venv/bin/python -m load.runner --host http://SERVICE:8088 --output run.json` from the load host. This single-run command does not reconfigure or reset the service. The runner uses Locust's `FastHttpUser` and records both total host CPU and its own process CPU; a saturated load-generator core can invalidate a capacity conclusion even when overall host CPU is low.

## Evidence and acceptance

Each result includes measurement start/duration, target, load-host OS/Python/CPU count, per-endpoint counts and quantiles, errors, metric snapshots, utilization samples and Nginx established client sockets. The orchestration record includes Docker host information, exact expanded Compose settings and image IDs. `report.md`, `summary.json`, CSVs and `benchmark.svg` can be regenerated with `.venv/bin/python -m scripts.report results/RUN_DIRECTORY`.

Cache hit rates are counter deltas at Prometheus scrape boundaries. A missing counter or reset is reported as unavailable. Database connection count is sampled, so report its distribution rather than assuming it is constant. Monitoring history persists; inspect timestamps around recreation and do not combine old instances with new ones. Measurements much shorter than a few scrape intervals give weak dependency evidence.

The proposal target should be accepted only after three full runs on a declared Linux environment, with the intended workload mix, read p95 below 120 ms, a stated acceptable error fraction, and evidence for at least 1,000 established connections during the measurement window. Inspect the full run distribution, not just the best trial. In-flight requests need their own metric; established keepalive sockets are not simultaneous active requests. The implementation does not assert that this target is met merely because a command finishes.

Resume claims about tuning require comparable baseline/parent/child results. Claims about diagnosis require genuine operator timing in both rounds. Local validation, especially with other Docker workloads active, is useful functional evidence but not a clean performance study.
