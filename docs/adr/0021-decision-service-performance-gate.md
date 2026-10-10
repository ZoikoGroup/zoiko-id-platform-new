# ADR-0021: Decision-service performance gate in W0, with a Go fallback

- **Status:** Proposed
- **Owner:** Authorization Lead + SRE Lead (name to be recorded at ratification)
- **Date:** 2026-10-10
- **Implements:** ADR-0010 §2 (2x qualification)

## Context
NFR targets: authorization p95 under 40 ms (R0/R1) and under 60 ms (R2/R3), 5k decisions/s per cell, qualified at 2×. Python handles I/O well but has per-process CPU limits (the GIL).

## Decision
- Build the real decision path in Python during W0 (FastAPI → SIG + SpiceDB + AUTHY + cedarpy + PG decision record).
- Load-test it with k6 at **10k decisions/s per cell for a 1-hour soak**.
- **Pass:** p95 targets met, p99 within the agreed bound, error rate under 0.01%, and cost per million decisions within the agreed envelope.
- **If it fails:** tune Python (workers, pools, batching), then profile and fix the hot spot. Only if both fail, **extract the `decision` module from the monolith into its own service and rewrite that path in Go (the one sanctioned extraction under ADR-0031)** behind the unchanged AuthZEN and internal contracts.
- Contracts for this service are frozen in OpenAPI before W1, so a rewrite is invisible to callers.

## Consequences
- The language risk is settled with measurements early, before W1 builds on top of it.
