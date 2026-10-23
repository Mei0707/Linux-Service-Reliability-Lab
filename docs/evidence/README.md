# Recorded evidence

- [All 16 configurations](matrix-validation/report.md): one short smoke measurement each, 20 users.
- [Repeated 1,000-user study](concurrency-validation/report.md): three 30-second measurements per configuration; the latency target was missed.
- [Cache-counter validation](metrics-validation/report.md): schema v2, individual counter identities retained, 20 users, zero failures.
- [Failure verification index](drills/validation.json): six verified fault/recovery records, with raw state and a packet capture in the same directory.
- [Complete verification log](verification.log): local tests and configuration/rule checks.

Each benchmark directory includes raw trial JSON/CSV, image IDs, expanded Compose configuration, environment information, a generated summary and SVG/PNG charts. Image IDs and source hashes identify the versions actually measured. Absolute mount paths in expanded configurations describe the validation machine; deploy with the repository-root Compose file.

Schema-v1 trial JSON retains its original aggregate cache estimates for audit, but the curated summaries withhold those ratios because individual counter identities were not recorded. Schema v2 rejects ratios across series churn or counter resets. Request counts, latency measurements and socket samples remain unchanged.
