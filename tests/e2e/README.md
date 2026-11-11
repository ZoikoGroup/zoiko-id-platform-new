# End-to-end tests

A whole flow through the real app and a real database, as different people.

This folder holds no test code of its own: the suites run with the backend (`backend/.venv` pytest with `ZID_TEST_PG_URL` set, see `backend/app/tests/component/conftest.py`) so they share its fixtures. Where they are:

- `backend/app/tests/component/test_agent_flow_e2e_pg.py`: from an approved build's digest to a workload token and back to a kill (needs Docker and the demo image; skips itself without them)
- `backend/app/tests/component/test_decision_e2e_pg.py`: a decision from request to receipt
- `backend/app/tests/component/test_tool_gateway_pg.py`: an agent asks for a tool grant and the tool's server redeems it
- `frontend/tests/e2e`: browser journeys
