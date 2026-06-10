# API Pressure Lab Implementation Plan

**Goal:** Deliver a working Go/Python API capacity and security testing project.

**Architecture:** Go runs paced stages and exports results. Independent Python modules implement demo API, SQLi checks, and HTML reporting against the schema in design.md.

**Tech Stack:** Go 1.23+, Python 3.11+, standard libraries only.

**Spec:** [design.md](design.md)

## Global constraints

- HTTP GET only, bounded workers/timeouts/bodies; no redirect following.
- Real measurements only; failed, interrupted, and generator-limited stages cannot be capacity passes.
- No dependencies; no authentication secrets in output.
- Feature commits use actual dates.

## Tasks

- [x] Go runner: create go.mod, internal/loadtest/runner.go and runner_test.go, cmd/pressure/main.go. Tests cover nearest-rank percentiles, rate validation, accounting, worker saturation, non-2xx, timeout and cancellation. Run `go test -race ./...` and `go vet ./...`; commit a usable CLI.
- [x] Python tools: create pressurelab/{demo,security,report}.py and tests. Run `python3 -m unittest discover -s tests -v`; commit demo, probes, and report.
- [x] Integration: run a real local demo, generate JSON/HTML and security report, test the CLI and evaluate threshold behavior. Add README, license, workflow, example evidence and methodology. Commit documentation and CI.
- [x] Publish and pin: rename the existing Card-Game repository to api-pressure-lab, retain its original commits and files, publish verified files, and confirm the profile pin. The separate new starter was renamed api-pressure-lab-starter.
