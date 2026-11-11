# Tenant isolation tests

A caller in one tenant, or in none, cannot read or change another tenant's data.

This folder holds no test code of its own: the suites run with the backend (`backend/.venv` pytest with `ZID_TEST_PG_URL` set, see `backend/app/tests/component/conftest.py`) so they share its fixtures. Where they are:

- `backend/app/tests/component/test_tenant_isolation_pg.py`: every table the ownership registry marks tenant-scoped forces row-level security, the runtime role cannot bypass it, and with real data in two tenants other callers see and change nothing (a new table that forgets its policy fails it)
- `backend/app/tests/component/test_runtime_isolation_pg.py`: a full cache flush changes latency only, never who sees what
- `backend/app/tests/component/test_noisy_tenant_pg.py`: rate isolation between tenants
