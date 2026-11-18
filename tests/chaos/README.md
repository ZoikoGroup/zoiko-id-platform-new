# Chaos and degradation tests

A dependency disappears under a running cell. The rule: high-risk enforcement fails closed, a failure is never an approval, and the platform recovers on its own.

This folder holds no test code of its own: the suites run with the backend (`backend/.venv` pytest with `ZID_TEST_PG_URL` set, see `backend/app/tests/component/conftest.py`) so they share its fixtures. Where they are:

- `backend/app/tests/component/test_chaos_degradation_pg.py`: database taken away from a running cell: the decision API answers a retryable 503 (never 200, never an allow) and recovers; the tool gateway signs no grant when the decision point fails
- `backend/libs/zid-app/tests/test_dependency_outage.py`: a lost dependency is a retryable 503, and a bug is not called an outage
- `backend/libs/zid-app/tests/test_app_resilience.py`: a slow dependency cannot take every worker; a failing one is cut off, probed and closed again
- `backend/app/tests/component/test_broker_degradation_pg.py`: event broker degraded: events are kept and delivered once it is back
- `backend/app/tests/component/test_spicedb_projection_pg.py`: authorization store outage fails closed for R2 and R3
- `backend/app/tests/component/test_recovery_fencing_pg.py`: failover cutover is atomic with a single writer, under fault injection

Screening (WF06) degradation, covered by the same rule (a failure is never an approval):

- `backend/app/tests/component/test_screening_requests_pg.py`: a stale or missing source makes the case unavailable, never clear; a required class that could not run is not dropped
- `backend/app/tests/component/test_screening_licensed_pg.py` and `backend/app/tests/unit/test_wf06_provider_controls.py`: a failing or over-budget provider opens its circuit and the case is not cleared, with no failover to another provider
- `backend/app/tests/component/test_screening_provider_events_pg.py`: a replayed, stale or wrongly signed provider callback changes nothing
- `backend/app/tests/component/test_erasure_retry_pg.py`: a store that is down fails its own erasure task, is retried with a growing delay, and is never reported complete

Not covered, and needing a deployed cell: killing a worker mid-pass, a regional partition during a watch run, and a provider's real outage.
