# Machine-identity token gate (MACH-26)

ZID-MACHINE-001 §25 and acceptance test MACH-26 ask that **2x the Stage A machine-auth/token load** meets the latency, error and rotation objectives.

| Check | Limit | Source |
|---|---|---|
| Rate | 20,000 token exchanges/s (2x Stage A `token_issuance_per_s`, 10,000) | `contracts/baseline/capacity.yaml` |
| Workload token exchange p95 | < 150 ms in-region | MACHINE-001 §25 |
| Error rate (transport/5xx; a refusal is not an error) | < 0.01% | DEC-B3 |
| Report | p50, p95, p99, max (no averages alone) | NFR-PERF-006 |

The limits live in [`contracts/baseline/slo.yaml`](../../../contracts/baseline/slo.yaml) (`machine_identity_gate`). `backend/app/tests/unit/test_load_gate.py` fails CI if the constants in `machine-token.js` drift from it.

## Running

```
k6 run -e SMOKE=1 -e ZID_CELL_URL=<cell> -e ZID_SVID_CERT=client.pem -e ZID_SVID_KEY=client.key \
       -e ZID_ENVIRONMENT_ID=<env> -e ZID_ARTIFACT_DIGEST=sha256:<digest> tests/load/machine-token/machine-token.js
```

- Point it at a **cell** in the PERF environment, never the global plane and never production.
- Every workload token needs a verified attestation (CR-042): before the run, post an attestor-signed statement for the workload, environment and artifact digest to `POST /v1/machine/attestations:verify` (statements last at most an hour, so the soak needs them renewed). Without one every call is refused.
- The workload must be registered for a PERF tenant (`POST /v1/machine/workload-bindings`) and the certificate must be its SVID, or every call is refused.
- Run from load generators in the same region as the cell.

## Status

The harness is ready and **has not been run at the gate rate**: that needs a PERF cell, real mesh certificates and load generators. What has been measured is in [RESULTS.md](RESULTS.md): on one developer machine, in process, p95 about 26 ms for a single client, but only about 50 exchanges/s per process, with p95 651 ms at 20 in flight. That says the code path is not the bottleneck at one client; it does not show 20,000/s. The decision gate on the same machine did not meet its rate either (see [`../decision-gate/RESULTS.md`](../decision-gate/RESULTS.md)), so expect the same tuning work here.

The rotation objective (rotation completes before half of the overlap window is used) is covered by the unit tests for rotation; the load run only has to confirm it holds while the exchange is under load.
