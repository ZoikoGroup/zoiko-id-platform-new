# Sign-off pack: CR-093

Generated 2026-11-21 by `tools/scripts/signoff_pack.py` from `docs/traceability/acceptance-tests.csv`. This is a checklist for the approvers. It does not approve anything: a test is done only when an approver changes its status in the traceability file.

- Acceptance tests: **5**; blockers: **0** (not started, no resolving evidence, or a measurement that says its gate is not met)

## Tests and evidence

| ID | Test | Status | Evidence | Resolves |
|---|---|---|---|---|
| AG3-T01 | API client registration refuses private or remote or weak keys | IN_PROGRESS | `backend/app/tests/unit/test_api_agents.py::test_a_key_carrying_any_private_member_is_refused` | yes |
| AG3-T02 | API client descriptor digest is exact and bound to keys and audiences | IN_PROGRESS | `backend/app/tests/unit/test_api_agents.py::test_the_descriptor_digest_names_the_client_its_keys_and_its_audiences` | yes |
| AG3-T03 | API-bound agent is capped to read or reversible low-risk tools | IN_PROGRESS | `backend/app/tests/unit/test_api_agents.py::test_an_api_bound_agent_may_hold_only_tools_that_read_or_can_be_undone` | yes |
| AG3-T04 | An agent reaches ACTIVE with no image through an approved API client binding | IN_PROGRESS | `backend/app/tests/component/test_api_agents_pg.py::test_an_api_agent_reaches_active_with_no_image_and_is_capped_at_the_gateway` | yes |
| AG3-T05 | Revoking the client build suspends its binding and agent | IN_PROGRESS | `backend/app/tests/component/test_api_agents_pg.py::test_revoking_the_client_build_suspends_its_binding_and_its_agent` | yes |

## Blockers

None.

## Approvals

| Lead | Name | Date | Decision |
|---|---|---|---|
| Security owner |  |  |  |
| Platform |  |  |  |
