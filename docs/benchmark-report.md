# Benchmark report — 2026-10-23

The lab is implemented and its experiment workflow runs end to end. **The proposal's 1,000-connection, sub-120 ms read p95 target was not met in this local validation.** Human diagnosis-time improvement has not been measured.

## Environment and protocol

The load generator ran on macOS arm64 beside Docker Desktop's Linux VM: 10 CPUs, approximately 8 GB RAM, kernel `6.10.14-linuxkit`, Docker Engine 28.0.4. Other projects were active; the recorded host had 36 running containers. This is a shared local environment, with a single-process load generator, rather than the separate Linux hosts proposed for the final capacity study.

Two datasets exercise the same weighted 90/8/2 Zipfian workload:

- [Full matrix smoke validation](evidence/matrix-validation/report.md): all 16 configurations, 20 users, one repetition, 3-second warmup after ramp-up, 10-second measurement. Every configuration completed with zero failed requests.
- [Repeated concurrency validation](evidence/concurrency-validation/report.md): baseline and tuned, 1,000 users, three repetitions, 10-second warmup after ramp-up, 30-second measurement. These windows are shorter than the default 30-second warmup and 120-second measurement intended for the controlled study.

Each repetition resets links, hit counters and Redis, then seeds the fixed 1,000-link corpus. Raw JSON/CSV, expanded configurations, image IDs, environment records, socket samples and charts accompany the reports. Expanded Compose evidence contains the validation machine's absolute paths; use the repository-root Compose file to deploy the lab.

## Repeated 1,000-user results

Values are medians across three trials. Latencies are milliseconds. Successful RPS excludes failed requests; errors are still part of the latency distribution. Socket ranges cover the measured samples across all trials.

| Configuration | Successful RPS | p50 | p95 | p99 | Read p95 | Errors | Established client sockets |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 565 | 1000 | 2000 | 2600 | 2000 | 40.91% | 1000–1012 |
| tuned | 1198 | 410 | 1600 | 2000 | 1600 | 31.00% | 1000–1001 |

The local combined configuration increased median successful throughput by about 112% and reduced p95 by 20%. The error fractions and tail latency remain unacceptable for the proposed operating point. The configuration name `tuned` identifies a versioned bundle; these observations do not establish that it is optimal or support a general performance claim.

Run-to-run variation is substantial: tuned successful throughput ranged from roughly 743 to 1,588 requests/sec, and its failure fraction ranged from about 0.12% to 52.47%. Failures included proxy and dependency 502/503/504 responses. Confirm the limiting resource with comparable high-load, one-variable runs and dependency/host signals before assigning a single root cause.

The socket samples establish that the proxy held approximately 1,000 connections. They also demonstrate why connection count alone does not establish acceptable service capacity.

## Cache measurements

The current runner preserves counter identities and rejects cache ratios if replicas appear/disappear or individual counters reset. Earlier schema-v1 snapshots contained only aggregate counters; their cache-ratio estimates are withheld from the curated reports because replica churn cannot be audited from those snapshots. Their request counts, latency histograms and socket observations remain available.

A [schema-v2 cache validation run](evidence/metrics-validation/report.md) checks the final counter handling independently. It recorded an 86.05% cache hit ratio with zero request failures at 20 users. This short functional measurement does not establish high-load cache effectiveness.

## Attribution and remaining study

The matrix report compares measured parent/child configurations. At 20 users, two workers improved throughput over one, while additional workers yielded little further gain at that offered load. The short, single-trial data cannot establish saturation points or causal gains at 1,000 users. Several changes show neutral or negative local effects; all outcomes are retained.

Run `make benchmark-all` on the declared Linux service host with a separate load VM, synchronized clocks, unchanged workload pacing and three full windows per case. Inspect successful throughput, per-endpoint latency, errors, generator CPU, database/pool signals, cache counters and socket samples together. Do not substitute the best trial or a target value for a measured result.

Automated failure validation and incident records verify injection and recovery, and are labeled `validation`. The two human diagnosis rounds remain an operator exercise; no before/after diagnosis gain or resume number is fabricated.
