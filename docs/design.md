# API Pressure Lab

Build a reproducible API capacity experiment and a separate SQL-injection regression check. Go generates paced HTTP GET traffic; Python provides a local demo, security probes, and a standalone HTML report. Use standard libraries only (Go 1.23+, Python 3.11+).

## First release

`pressure` accepts a URL, ascending comma-separated request rates, stage duration, worker limit, per-request timeout, p95 latency limit, and error-rate limit. It emits schema-versioned JSON. Workers never exceed the limit; scheduled work that cannot start is counted as dropped, not silently queued. Late scheduler slots are dropped rather than released as a burst. Request latency includes reading the response body, bounded at 1 MiB. Redirects are not followed. Only 2xx responses with a complete body count as success. Each stage waits for outstanding requests before the next stage.

Stop at the first failing stage. A pass requires no dropped requests, nonempty results, latency and error limits met. Report the highest passing *tested offered rate*, not a universal capacity. Record achieved throughput, sample size, failures, dropped arrivals, status codes, timestamps, and generator OS/architecture/CPU count. Interrupted runs are marked interrupted and never establish a passing capacity for the partial stage. Latencies use exact nearest-rank quantiles, with a maximum of one million scheduled requests per stage to bound memory.

JSON contract: top-level `schema_version: 1`, `started_at`, `target` (scheme/host/path only; redact query and credentials), `environment` object, `thresholds: {p95_ms, max_error_rate}`, `stages` array, `highest_passing_rps` integer, `stop_reason` string. Stage fields: `offered_rps`, `duration_seconds`, `elapsed_seconds`, `scheduled`, `completed`, `failed`, `dropped`, `achieved_rps`, `error_rate`, `p50_ms`, `p95_ms`, `p99_ms`, `passed`, `status_codes` object. Durations are seconds and latencies milliseconds.

Python report accepts this JSON and generates escaped standalone HTML with stage comparisons and methodology. Python security CLI issues bounded read-only GET probes for one named query parameter, refuses redirects, uses timeouts and response-size limits, distinguishes potential findings from proof, and never extracts data. Stable baselines and repeated controls reduce noise. Only run against owned/authorized environments. Demo binds to 127.0.0.1 and uses in-memory SQLite fixtures; vulnerable behavior requires an explicit demo flag. Demo `/work` endpoint simulates a limited worker pool; `/items?q=apple` demonstrates safe/vulnerable query handling.

## Validation and delivery

Write behavior tests first. Verify pacing and accounting, concurrency limit, HTTP failures/timeouts, body limits, cancellation, validation, SQLi positive/negative controls, HTML escaping, and an end-to-end local demo. Include GitHub Actions checks and reproducible commands. Commit complete features with current timestamps. Publish to ShubhamSingh047/api-pressure-lab and pin once usable. No invented benchmark or resume claims.

<!-- Documentation polish (2026-05-29 20:49:01) -->
