# Verification — 2026-10-23

`make verify` passed against the running default three-replica stack. The [complete local log](evidence/verification.log) records the checks.

| Check | Result |
|---|---|
| Python tests | 30 passed: 23 unit/report/lifecycle tests and 7 real-service integration tests |
| Ruff lint and formatting | Passed |
| Expanded Compose configuration | Valid |
| Nginx configuration | Valid |
| Prometheus configuration and seven alert rules | Valid |
| Alert behavior fixtures | Missing replica, Redis outage and global DB connection saturation passed |
| Grafana provisioning | 16 panels loaded; 28 PromQL expressions checked |
| Experiment configurations | All 16 smoke runs completed with zero request failures |
| Failure injection and recovery | All six faults have successful validation records and post-cleanup resolution checks |
| Final active fault journal | Absent; no injected failure remains active |

Integration tests exercise real PostgreSQL/Redis operations through Nginx, including 32 concurrent retries using one idempotency key and 100 parallel resolutions with an exact durable count. Unit tests also verify dependency-independent liveness, Redis degradation, draining, cancellation of cooperative work, input validation, cleanup-journal retention, paired human-timing selection and cache-counter reset detection.

The [six incident records](evidence/drills/validation.json) contain measured fault evidence and verified recovery. A database-namespace packet capture accompanies latency validation. Automated verification does not count as human diagnosis timing.

Planned drain validation recorded 1,164 requests, one idle-keepalive transport disconnect, and no HTTP 5xx response. Readiness returned 200 afterward. Clients should retry safe reads and retain idempotency keys across write retries; this edge case is documented in the architecture notes.

The [benchmark report](benchmark-report.md) preserves the failed 1,000-user latency target and observed error fractions. The short local runs do not substitute for the proposed controlled Linux study. The GitHub Actions workflow is provided for future pushes and pull requests; these verification results are local.
