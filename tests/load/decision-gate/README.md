# Decision-service performance gate (DEC-B3)

The W0 gate from [Phase B](../../../docs/phases/phase-b-requirements-contract.md): the real decision path must sustain **2× the Stage A per-cell peak (10,000 decisions/s) for a 1-hour soak** with:

| Check | Limit | Source |
|---|---|---|
| p95, R0/R1 actions | < 40 ms | NFR-PERF-001 |
| p95, R2/R3 actions | < 60 ms | NFR-PERF-001 |
| Error rate (transport/5xx; a DENY is not an error) | < 0.01% | DEC-B3 |
| Report | p50, p95, p99, max (no averages alone) | NFR-PERF-006 |

The limits live in [`contracts/baseline/slo.yaml`](../../../contracts/baseline/slo.yaml). `backend/app/tests/unit/test_load_gate.py` fails CI if the constants in `decision-gate.js` drift from it.

## Running

```
k6 run -e SMOKE=1 -e ZID_CELL_URL=<cell endpoint> -e ZID_TOKEN=<token> tests/load/decision-gate/decision-gate.js
k6 run            -e ZID_CELL_URL=<cell endpoint> -e ZID_TOKEN=<token> tests/load/decision-gate/decision-gate.js
```

- Point it at a **cell** endpoint in the PERF environment, never the global plane and never production.
- Run from load generators in the same region as the cell, so network distance doesn't eat the budget.
- Keep the k6 summary and the cell's saturation, queue-lag and cost dashboards for the run: together they are the evidence that closes BG-06/AT-16 in [`gates.yaml`](../../../contracts/baseline/gates.yaml).

**Status:** the harness is ready; the decision path it measures (AuthZEN PDP with Cedar, SpiceDB and the authority module) is built in Phases C–D. If the gate fails: tune Python, then profile and fix the hot spot, and only then extract the `decision` module and rewrite that path in Go (DEC-B3).
