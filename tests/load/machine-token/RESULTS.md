# Machine-token exchange: measured baseline (not the MACH-26 gate)

**Result: the gate is NOT met, and has not been run at the gate rate.** This is what one process on one developer machine shows for `POST /v1/machine/workload-token`, with a verified attestation behind it (CR-042).

| | |
|---|---|
| Date | 2026-10-28 |
| Where | One developer machine, the app in process (httpx ASGI transport, no network), PostgreSQL in a local container, one worker process |
| Tests | `test_mesh_verified_workload_gets_a_bound_token` (serial), `test_concurrent_token_exchanges_are_served_and_measured` (concurrent) |
| Caveats | Laptop, shared CPU, one process, rate limit off, no TLS or mesh hop. A floor for planning, never a certification figure. |

## Serial (one client, 100 exchanges)

| p50 | p95 | p99 | Budget (p95) |
|---|---|---|---|
| 18 ms | 26 ms | 38 ms | 150 ms |

## Concurrent (400 exchanges, 20 in flight)

| Exchanges/s | p50 | p95 | p99 | Failures |
|---|---|---|---|---|
| 49 | 374 ms | 651 ms | 792 ms | 0 |

One process serves about **50 exchanges/s** and latency then grows with the queue. The gate asks for **20,000/s** (2x Stage A) at p95 under 150 ms: about 400 times the throughput of one process, and latency that only holds below saturation. The decision path on the same kind of machine tops out near 40/s ([decision gate results](../decision-gate/RESULTS.md)); the two probably share causes, but this has not been profiled: each exchange does several database reads, writes an E3 audit record and an outbox event, and writes a billing record, all before the token is signed.

## What this does and does not tell us

- Correctness under concurrency holds: 400 concurrent exchanges, no failures, every one backed by the same verified attestation record.
- The latency budget is met only while the process is not saturated. Meeting it at the gate rate is a capacity-and-tuning problem (more replicas, fewer transactions per exchange, caching the verified level and binding for the attestation lifetime), not a correctness one.
- Not measured: the mesh and TLS hop, a multi-replica cell, a warm cache, the attestation renewal load (one verify per workload every few minutes, small next to the exchanges).

## Next

1. Profile one exchange the way `tools/scripts/decision_baseline.py` profiles a decision: count statements and transactions.
2. Cache the binding and the verified attestation level per workload for the shorter of the statement's remaining life and a few seconds; revocation and quarantine already bump `security_revision`, which can invalidate it.
3. Run `machine-token.js` against a PERF cell with real SVIDs and attestor statements, and keep the k6 summary as the MACH-26 evidence.
