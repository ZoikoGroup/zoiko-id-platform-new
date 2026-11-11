# Concurrency tests

Many callers at once: no double spend, no oversubscription, no lost update.

This folder holds no test code of its own: the suites run with the backend (`backend/.venv` pytest with `ZID_TEST_PG_URL` set, see `backend/app/tests/component/conftest.py`) so they share its fixtures. Where they are:

- `backend/app/tests/component/test_tool_grants_pg.py`: eight parallel redemptions of one grant: exactly one wins
- `backend/app/tests/component/test_mandate_ledger_pg.py and backend/app/tests/property/test_mandate_ledger.py`: mandate reservations cannot exceed the limit; a revocation between reserve and commit blocks the commit
- `backend/app/tests/component/test_noisy_tenant_pg.py`: one tenant floods concurrently: it is refused with 429 while another tenant is not affected
- `backend/app/tests/component/test_propagation_load_pg.py`: revocation reaches enforcement inside the budget while many readers are active
- `backend/app/tests/component/test_agent_revocation_propagation_pg.py`: the same, at the agent tool gateway
