# Security-state propagation under load (FR-SIG-003/004/006)

Shows how long it takes from an authoritative security-state commit (for example, suspending a principal) until a policy enforcement point (PEP) acts on it. The PEP's view comes through the same PIP the API uses (`signals` `security_view`), while many readers run at once.

| Risk class | p99 target | How the PEP reads the state |
|---|---|---|
| R3 in-cell | ≤ 2 s | always the committed row, inside the action's own transaction (`protected.py`) |
| R3 cross-party | ≤ 5 s | SSF push/poll to receivers that support real-time signals |
| R2 | ≤ 10 s | always the committed row |
| R1 | ≤ 60 s | process cache, TTL 5 s, revision-aware |
| R0 | ≤ 15 min | process cache, TTL 30 s |

The targets live in [`contracts/baseline/slo.yaml`](../../../contracts/baseline/slo.yaml) under `revocation_propagation_seconds`. `backend/app/tests/unit/test_load_gate.py` fails if a cache TTL (`STATE_CACHE_TTL`) stops fitting inside its budget.

## The demonstration

`backend/app/tests/component/test_propagation_load_pg.py` runs in CI against PostgreSQL:

- Concurrent readers keep asking for the state of a population of principals, half at R1 and half at R3.
- A separate engine suspends every principal. It stands in for another process, so it never invalidates the readers' cache, which is the worst case for R1.
- For each principal and risk class, the test records commit → first enforcing observation. It asserts p99 against the SLO and prints p99 and max.

The commit time is stamped before the write commits, so reported lags are upper bounds.

For a soak on a performance database, scale it up:

```
ZID_TEST_PG_URL=<perf database> \
ZID_PROPAGATION_PRINCIPALS=5000 ZID_PROPAGATION_READERS=64 ZID_PROPAGATION_SECONDS=60 \
  pytest backend/app/tests/component/test_propagation_load_pg.py -s
```

Keep the printed line with the run's database saturation dashboards. Together they are the FR-SIG-004 evidence. In deployed cells, the same lag is logged on every enforcing read as `commit_to_observation_ms` (component `signals.propagation`), which feeds the `zid_revocation_propagation_seconds` SLI.

Cross-party propagation (SSF transmitter to receivers) depends on the receiver. The transmitter's own delivery lag per stream is reported by `GET /ssf/streams/{id}/propagation`.

## The agent channel (ZID-GAP-001 WP-17)

`backend/app/tests/component/test_agent_revocation_propagation_pg.py` measures the same thing where an agent acts: at the tool gateway
(`POST /v1/machine/tool-calls:authorize`). Concurrent callers keep asking for grants for a sub-agent while another engine commits a
revocation, and the test records commit -> first refusal and checks p99 against the R3 in-cell budget (the strictest, 2 s). Three
triggers: the sponsor is disabled, the parent agent is suspended, the tool is suspended. The gateway reads committed rows in every request
(no cache between), and a grant is checked again when the tool's server redeems it, so a revocation between signing and acting also stops the call.

A run on a developer machine (local PostgreSQL, 6 concurrent callers, one process) measured: sponsor disabled p99 about 290 ms,
parent suspended about 250 ms, tool suspended about 180 ms. These are single-machine numbers, not a performance-environment result;
`ZID_AGENT_PROPAGATION_READERS` scales the callers up for a soak. Not measured here: the UI, queue/webhook and wallet-verification channels
(the plan asks for those too), and cross-region propagation.

