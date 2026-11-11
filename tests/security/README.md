# Security and adversarial tests

Attacks from the plan's threat catalog (ZID-GAP-001 section 6). The tests run with the backend suite; the list below is where they are.

This folder holds no test code of its own: the suites run with the backend (`backend/.venv` pytest with `ZID_TEST_PG_URL` set, see `backend/app/tests/component/conftest.py`) so they share its fixtures. Where they are:

- `backend/app/tests/component/test_tool_gateway_redteam_pg.py`: prompt injection in every free field, cross-tenant tools and grants, forged delegation, a write tool with no mandate
- `backend/app/tests/component/test_tool_gateway_pg.py and test_tool_grants_pg.py`: a person asking in an agent's name, forged bodies, unlisted tools, stolen and replayed grants, approvals bound to exact arguments
- `backend/app/tests/component/test_pipeline_signin_pg.py and backend/app/tests/unit/test_machine_callable_actions.py`: a build pipeline can propose a build and nothing else
- `backend/app/tests/component/test_residency_drill_pg.py`: wrong cell, rogue webhook, stale epoch and foreign support access are refused
- `frontend/tests/security`: browser-side checks
