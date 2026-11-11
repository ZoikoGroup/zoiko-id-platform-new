# Chaos and degradation tests

A dependency disappears under a running cell. The rule: high-risk enforcement fails closed, a failure is never an approval, and the platform recovers on its own.

This folder holds no test code of its own: the suites run with the backend (`backend/.venv` pytest with `ZID_TEST_PG_URL` set, see `backend/app/tests/component/conftest.py`) so they share its fixtures. Where they are:

- `backend/app/tests/component/test_chaos_degradation_pg.py`: database taken away from a running cell: the decision API answers a retryable 503 (never 200, never an allow) and recovers; the tool gateway signs no grant when the decision point fails
- `backend/libs/zid-app/tests/test_dependency_outage.py`: a lost dependency is a retryable 503, and a bug is not called an outage
- `backend/libs/zid-app/tests/test_resilience.py`: a slow dependency cannot take every worker; a failing one is cut off, probed and closed again
- `backend/app/tests/component/test_broker_degradation_pg.py`: event broker degraded: events are kept and delivered once it is back
- `backend/app/tests/component/test_spicedb_projection_pg.py`: authorization store outage fails closed for R2 and R3
- `backend/app/tests/component/test_recovery_fencing_pg.py`: failover cutover is atomic with a single writer, under fault injection
