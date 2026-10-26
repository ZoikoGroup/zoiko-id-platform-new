# Decision path: measured baseline (not the DEC-B3 gate)

**Result: the gate is NOT met.** This is the first measurement of the real decision path (`POST /access/v1/evaluation`: authentication, placement, security state, authority chain, Cedar policy, evidence handles, receipt). It is evidence for [AT-16 / P0-09 / BG-06](../../../contracts/baseline/gates.yaml), and it says the decision path needs work before a PERF run can pass.

| | |
|---|---|
| Date | 2026-10-24 |
| Where | One developer machine (12 logical CPUs), Docker Desktop, the compose stack: 1 API container with 1 uvicorn process for the cell role, PostgreSQL 17 in a container on the same host |
| Tool | `tools/scripts/decision_baseline.py` (serial latency, then a closed-loop throughput ceiling); k6 smoke run of `decision-gate.js` for the harness |
| Caveats | Laptop, shared CPU, Docker networking, a single API process, local PostgreSQL with default settings, rate limit off. Numbers are a floor for planning, never a certification figure. |

## Serial latency (200 requests per path, warmed up)

| Path | p50 | p95 | p99 | Budget (p95) |
|---|---|---|---|---|
| R0/R1 `application.access` | 72 ms | 80 ms | 95 ms | 40 ms |
| R2/R3 `invoice.approve` | 71 ms | 77 ms | 83 ms | 60 ms |

R0/R1 is no faster than R2/R3: the cost is the work every decision does, not the policy.

## Throughput ceiling (R2/R3, closed loop)

| Concurrency | Decisions/s | p50 | p95 |
|---|---|---|---|
| 1 | 14 | 70 ms | 78 ms |
| 2 | 28 | 72 ms | 85 ms |
| 4 | 39 | 93 ms | 150 ms |
| 8 | 42 | 186 ms | 257 ms |
| 16 | 36 | 394 ms | 852 ms |
| 32 | 42 | 750 ms | 1,295 ms |

One API process tops out near **40 decisions/s** with no errors, and latency then grows with the queue. The gate asks for **10,000 decisions/s per cell** (2x Stage A) at p95 under 40/60 ms: roughly 250x more throughput and about 1.5x lower latency.

The k6 smoke run (`SMOKE=1`, 50/s for 1 minute) confirms it: above the ceiling every request queued until it timed out (p95 2.7 s).

## Where the time goes

A single decision issues about **60 SQL statements** in about **8 separate transactions**:

- authentication: session, principal, placement and residency checks, security state, each in its own transaction;
- decision: principal again, the authority chain (grant, root), active policy release, relation revision;
- evidence: subject handle map and subject key (looked up three times), evidence-scope switches, the receipt write;
- every transaction opens with two `set_config` round trips.

Each statement costs round-trip time plus Python and SQLAlchemy overhead, which is why latency is flat across risk classes.

## Order of work (DEC-B3: tune Python first, then profile, then extract)

1. **Cheap and safe, expect 1.5 to 2x:** one `set_config` statement per transaction instead of two; one transaction per request for authentication facts; cache the ACTIVE policy release per tenant (immutable, keyed by content hash); look the evidence subject key up once.
2. **Use the cores:** several uvicorn workers per cell process (the limiter and caches are per process; the rate limit is then per worker, so move it to a shared counter first).
3. **Cut the decision to one read transaction:** gather principal, authority, relation and mandate facts in one transaction, and write the receipt asynchronously through the outbox (the receipt is still guaranteed, the response no longer waits for it).
4. **Only then**, if the gate still fails on a PERF cell: extract the decision path and rewrite it in Go, as DEC-B3 plans.

## Reproduce

```
docker compose up -d
# a tenant and principal on the local cell, and a short-lived token for them:
backend/.venv/Scripts/python tools/scripts/decision_baseline.py <tenant_id> <principal_id>
# the k6 harness (turn the per-tenant limit off for a single-tenant run):
ZID_API_RATE_LIMIT_PER_MINUTE=0 docker compose up -d backend
docker run --rm -v "$PWD/tests/load/decision-gate:/scripts:ro" -e SMOKE=1 -e ZID_CELL_URL=http://host.docker.internal:8000 -e ZID_TOKEN=<token> grafana/k6:0.54.0 run /scripts/decision-gate.js
```
