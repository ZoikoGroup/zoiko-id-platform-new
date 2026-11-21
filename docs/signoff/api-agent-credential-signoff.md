# Sign-off pack: CR-094

Generated 2026-11-21 by `tools/scripts/signoff_pack.py` from `docs/traceability/acceptance-tests.csv`. This is a checklist for the approvers. It does not approve anything: a test is done only when an approver changes its status in the traceability file.

- Acceptance tests: **4**; blockers: **0** (not started, no resolving evidence, or a measurement that says its gate is not met)

## Tests and evidence

| ID | Test | Status | Evidence | Resolves |
|---|---|---|---|---|
| AG3-T06 | API agent credential path | IN_PROGRESS | `backend/app/tests/component/test_api_agent_token_pg.py::test_the_signed_assertion_path_issues_a_bound_short_lived_token_and_refuses_everything_else` | yes |
| AG3-T07 | API credential claims and attestation cap | IN_PROGRESS | `backend/app/tests/unit/test_api_agent_token.py::test_the_attestation_cap_is_read_by_the_gate_and_the_default_class_passes_it` | yes |
| AG3-T08 | API agent assurance is Z1 only with a verified key proof | IN_PROGRESS | `backend/app/tests/unit/test_api_agent_assurance.py::test_only_a_proven_api_agent_credential_counts` | yes |
| AG3-T09 | An API agent works end to end over the API only | IN_PROGRESS | `backend/app/tests/component/test_agents_api_client_pg.py::test_an_api_agent_goes_from_nothing_to_signed_in_over_the_api_only` | yes |

## Blockers

None.

## Approvals

| Lead | Name | Date | Decision |
|---|---|---|---|
| Security owner |  |  |  |
| Platform |  |  |  |
