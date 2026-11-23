# Sign-off pack: ZID-AG-WF-001

Generated 2026-11-21 by `tools/scripts/signoff_pack.py` from `docs/traceability/acceptance-tests.csv`. This is a checklist for the approvers. It does not approve anything: a test is done only when an approver changes its status in the traceability file.

- Acceptance tests: **26**; blockers: **1** (not started, no resolving evidence, or a measurement that says its gate is not met)

## Tests and evidence

| ID | Test | Status | Evidence | Resolves |
|---|---|---|---|---|
| AG1-AT01 | Agent setup and execution: Creator cannot see/create agent without authorized te | IN_PROGRESS | `backend/app/tests/component/test_daara_pg.py::test_daara_http_routes_and_the_authority_they_need` | yes |
| AG1-AT02 | Agent setup and execution: Agent cannot be saved without active accountable spon | IN_PROGRESS | `backend/app/tests/component/test_daara_pg.py::test_a_sponsorless_agent_cannot_be_validated` | yes |
| AG1-AT03 | Agent setup and execution: Cross-tenant or wrong-region resource selector denied | IN_PROGRESS | `backend/app/tests/component/test_daara_pg.py::test_setup_is_gated_by_validation_a_passed_sandbox_and_two_independent_approvals` | yes |
| AG1-AT04 | Agent setup and execution: Unapproved blueprint, source connector or operation d | IN_PROGRESS | `backend/app/tests/component/test_daara_pg.py::test_setup_is_gated_by_validation_a_passed_sandbox_and_two_independent_approvals` | yes |
| AG1-AT05 | Agent setup and execution: Draft modification invalidates approval digest | IN_PROGRESS | `backend/app/tests/unit/test_daara.py::test_a_changed_setup_changes_the_hash_and_so_voids_validation_and_the_sandbox_pass` | yes |
| AG1-AT06 | Agent setup and execution: Dry run writes nothing and uses synthetic fixtures | IN_PROGRESS | `backend/app/tests/component/test_daara_pg.py::test_setup_is_gated_by_validation_a_passed_sandbox_and_two_independent_approvals` | yes |
| AG1-AT07 | Agent setup and execution: Unattested worker cannot obtain execution token | IN_PROGRESS | `backend/app/tests/component/test_workload_attestation_pg.py::test_a_workload_that_needs_attestation_cannot_claim_it` | yes |
| AG1-AT08 | Agent setup and execution: Expired/revoked mandate prevents further authorized w | IN_PROGRESS | `backend/app/tests/component/test_daara_pg.py::test_a_run_finds_the_known_issues_proposes_once_and_changes_no_access` | yes |
| AG1-AT09 | Agent setup and execution: Every read checks tenant, actor, purpose, resource an | IN_PROGRESS | `backend/app/tests/component/test_daara_pg.py::test_a_run_finds_the_known_issues_proposes_once_and_changes_no_access` | yes |
| AG1-AT10 | Agent setup and execution: Agent cannot grant roles or revoke credentials | IN_PROGRESS | `backend/app/tests/unit/test_daara.py::test_only_the_three_template_tools_exist_and_each_is_narrow` | yes |
| AG1-AT11 | Agent setup and execution: Review proposal writes once under retries/idempotency | IN_PROGRESS | `backend/app/tests/component/test_daara_pg.py::test_a_run_finds_the_known_issues_proposes_once_and_changes_no_access` | yes |
| AG1-AT12 | Agent setup and execution: Human approval handoff cannot bypass admin service | IN_PROGRESS | `backend/app/tests/component/test_daara_pg.py::test_a_run_finds_the_known_issues_proposes_once_and_changes_no_access` | yes |
| AG1-AT13 | Agent setup and execution: Rule outcomes replay identically from same version an | IN_PROGRESS | `backend/app/tests/unit/test_daara.py::test_the_same_input_gives_the_same_findings_in_the_same_order` | yes |
| AG1-AT14 | Agent setup and execution: Missing/invalid evidence results in unknown, not pass | IN_PROGRESS | `backend/app/tests/unit/test_daara.py::test_missing_or_unverifiable_evidence_is_unknown_not_pass` | yes |
| AG1-AT15 | Agent setup and execution: Optional model cannot select tools or change rule res | IN_PROGRESS | `backend/app/tests/component/test_daara_pg.py::test_the_model_explanation_is_optional_checked_and_never_changes_a_result` | yes |
| AG1-AT16 | Agent setup and execution: Prompt injection in metadata cannot alter instruction | IN_PROGRESS | `backend/app/tests/component/test_daara_pg.py::test_the_model_explanation_is_optional_checked_and_never_changes_a_result` | yes |
| AG1-AT17 | Agent setup and execution: Model outage preserves deterministic result | IN_PROGRESS | `backend/app/tests/component/test_daara_pg.py::test_the_model_explanation_is_optional_checked_and_never_changes_a_result` | yes |
| AG1-AT18 | Agent setup and execution: Policy outage denies safely without broad privilege | IN_PROGRESS | `backend/app/tests/component/test_agent_governance_pg.py::test_policy_lifecycle_approval_and_every_gate` | yes |
| AG1-AT19 | Agent setup and execution: Kill switch rejects new tokens, halts queued runs | IN_PROGRESS | `backend/app/tests/component/test_daara_pg.py::test_a_run_finds_the_known_issues_proposes_once_and_changes_no_access` | yes |
| AG1-AT20 | Agent setup and execution: Run evidence links replay and verify hashes | IN_PROGRESS | `backend/app/tests/component/test_daara_pg.py::test_a_run_finds_the_known_issues_proposes_once_and_changes_no_access` | yes |
| AG1-AT21 | Agent setup and execution: Logs exclude token, document, biometric and raw PII | IN_PROGRESS | `backend/app/tests/component/test_daara_pg.py::test_at21_the_whole_wizard_and_a_run_log_no_token_document_biometric_or_full_identifier` | yes |
| AG1-AT22 | Agent setup and execution: Accessibility keyboard, screen reader, zoom and small | NOT_STARTED | none | NO |
| AG1-AT23 | Agent setup and execution: Rollback does not duplicate review proposals | IN_PROGRESS | `backend/app/tests/component/test_daara_pg.py::test_a_run_finds_the_known_issues_proposes_once_and_changes_no_access` | yes |
| AG1-AT24 | Agent setup and execution: Unsupported region fails closed with truthful UI mess | IN_PROGRESS | `backend/app/tests/component/test_daara_pg.py::test_setup_is_gated_by_validation_a_passed_sandbox_and_two_independent_approvals` | yes |
| AG1-T30 | An emergency stop of agent governance stops the readiness agent | IN_PROGRESS | `backend/app/tests/component/test_access_readiness_pg.py::test_an_emergency_stop_of_agent_governance_stops_the_readiness_agent_too` | yes |
| AG1-T31 | DAARA is retired by default and nothing is deleted | IN_PROGRESS | `backend/app/tests/component/test_agent_register_rows_pg.py::test_daara_is_retired_by_default_its_routes_answer_404_and_the_alert_routes_stay` | yes |

## Blockers

- **AG1-AT22** Agent setup and execution: Accessibility keyboard, screen reader, zoom and small (not started): Accessibility keyboard, screen reader, zoom and small screens pass

## Approvals

| Lead | Name | Date | Decision |
|---|---|---|---|
| Founder |  |  |  |
| Product |  |  |  |
| Security |  |  |  |
| Architecture |  |  |  |
| Privacy |  |  |  |
| QA |  |  |  |
