# The DAARA reference agent (ZID-AG-WF-001 v1.0): the 24 acceptance tests against the repository

Written 2026-11-14 from the code and the tests. It decides nothing and passes nothing: the document requires every row to be executed independently on synthetic fixtures with a build SHA, policy hash, fixture, report and reviewer. "EVIDENCE" means a developer test exercises the behaviour; it is not a pass.

U = `backend/app/tests/unit/test_daara.py`, D = `backend/app/tests/component/test_daara_pg.py`, G = `backend/app/tests/component/test_agent_governance_pg.py`.

| State | Meaning | Rows |
|---|---|---|
| EVIDENCE | A test exercises the behaviour. | 16 |
| PARTIAL | Some of it is tested; the gap is named. | 6 |
| NOT_LINKED | No honest test exists; the reason is given. | 2 |

| ID | Pass condition (the document) | State | Test | Gap |
|---|---|---|---|---|
| AT-01 | Creator cannot see/create agent without authorized tenant role | EVIDENCE | D::test_daara_http_routes_and_the_authority_they_need | The role is an authority grant on the action, not one of the document's ten roles (CR-091 AG2-D02). |
| AT-02 | Agent cannot be saved without active accountable sponsor | EVIDENCE | D::test_a_sponsorless_agent_cannot_be_validated | The draft is saved; it cannot be validated or published. |
| AT-03 | Cross-tenant or wrong-region resource selector denied | PARTIAL | D::test_setup_is_gated_by_validation_a_passed_sandbox_and_two_independent_approvals | A region the company has not allowed is refused (REGION_DENIED). A cross-tenant read relies on row-level security and has no DAARA-specific test; the governance plane's cross-tenant test covers its own tables. |
| AT-04 | Unapproved blueprint, source connector or operation denied | EVIDENCE | D::test_setup_is_gated_by_validation_a_passed_sandbox_and_two_independent_approvals, U::test_only_the_three_template_tools_exist_and_each_is_narrow | |
| AT-05 | Draft modification invalidates approval digest | EVIDENCE | U::test_a_changed_setup_changes_the_hash_and_so_voids_validation_and_the_sandbox_pass, D::test_setup_is_gated_by_validation_a_passed_sandbox_and_two_independent_approvals | An approval already signed is voided by the governance plane's own hash binding (CR-091 T22). |
| AT-06 | Dry run writes nothing and uses synthetic fixtures | EVIDENCE | D::test_setup_is_gated_by_validation_a_passed_sandbox_and_two_independent_approvals | |
| AT-07 | Unattested worker cannot obtain execution token | NOT_LINKED | | The runner executes inside the platform and calls the governance gates directly; it does not request a workload token or a gateway grant per read, so there is no token to refuse. |
| AT-08 | Expired/revoked mandate prevents further authorized work | PARTIAL | D::test_a_run_finds_the_known_issues_proposes_once_and_changes_no_access | DAARA flags an expired mandate (R-01) and ignores a revoked one; stopping work under a revoked mandate is the mandate module's own behaviour and is not re-tested here. |
| AT-09 | Every read checks tenant, actor, purpose, resource and policy | PARTIAL | D::test_a_run_finds_the_known_issues_proposes_once_and_changes_no_access, G::test_policy_lifecycle_approval_and_every_gate | Each tool use goes through the governance gates, whose purpose, resource, region and approval denials are tested; no DAARA test drives each denial through a run. |
| AT-10 | Agent cannot grant roles or revoke credentials | EVIDENCE | U::test_only_the_three_template_tools_exist_and_each_is_narrow, D::test_a_run_finds_the_known_issues_proposes_once_and_changes_no_access | The mandates and agent registry are read back byte for byte before and after. |
| AT-11 | Review proposal writes once under retries/idempotency | EVIDENCE | D::test_a_run_finds_the_known_issues_proposes_once_and_changes_no_access | |
| AT-12 | Human approval handoff cannot bypass admin service | EVIDENCE | D::test_a_run_finds_the_known_issues_proposes_once_and_changes_no_access | A hand-off records a digest and changes nothing. The access-change service is not built here. |
| AT-13 | Rule outcomes replay identically from same version and input | EVIDENCE | U::test_the_same_input_gives_the_same_findings_in_the_same_order, D::test_a_run_finds_the_known_issues_proposes_once_and_changes_no_access | |
| AT-14 | Missing/invalid evidence results in unknown, not pass | EVIDENCE | U::test_missing_or_unverifiable_evidence_is_unknown_not_pass, D::test_unknown_state_makes_the_run_incomplete_never_healthy | |
| AT-15 | Optional model cannot select tools or change rule result | EVIDENCE | D::test_the_model_explanation_is_optional_checked_and_never_changes_a_result | The model is only ever called with counts, rule codes and masked targets; it has no tool list. |
| AT-16 | Prompt injection in metadata cannot alter instructions | EVIDENCE | D::test_the_model_explanation_is_optional_checked_and_never_changes_a_result, U::test_t15_t16_a_rewrite_that_invents_claims_numbers_or_obeys_text_is_refused | By construction: the projection has no free-text field. The checker is the second wall. |
| AT-17 | Model outage preserves deterministic result | EVIDENCE | D::test_the_model_explanation_is_optional_checked_and_never_changes_a_result | |
| AT-18 | Policy outage denies safely without broad privilege | EVIDENCE | G::test_policy_lifecycle_approval_and_every_gate | A missing policy source is POLICY_UNAVAILABLE (CR-091 T15). |
| AT-19 | Kill switch rejects new tokens, halts queued runs | PARTIAL | D::test_a_run_finds_the_known_issues_proposes_once_and_changes_no_access, G::test_a_pause_cancels_running_work_and_the_next_progress_is_refused | Runs are refused and running work cancelled. There are no queued runs, and token refusal after a pause is the registry's existing suspension, not re-tested here. |
| AT-20 | Run evidence links replay and verify hashes | EVIDENCE | D::test_a_run_finds_the_known_issues_proposes_once_and_changes_no_access | |
| AT-21 | Logs exclude token, document, biometric and raw PII | NOT_LINKED | | By construction the projection and the evidence rows hold ids, hashes, codes and counts. No test scans the run's logs for personal data. |
| AT-22 | Accessibility keyboard, screen reader, zoom and small screens pass | PARTIAL | frontend/apps/web/src/features/admin/agents/governance/daara-logic.test.ts | Logic is tested and a Playwright axe spec was written for all nine pages; it was not run (the local web stack was not up) and no screen reader was used. |
| AT-23 | Rollback does not duplicate review proposals | PARTIAL | D::test_a_run_finds_the_known_issues_proposes_once_and_changes_no_access | A unique key and an idempotent upsert make a repeat harmless, shown by a second run. No rollback of a release was exercised. |
| AT-24 | Unsupported region fails closed with truthful UI message | EVIDENCE | D::test_setup_is_gated_by_validation_a_passed_sandbox_and_two_independent_approvals | The message text is in `blockerText` and unit tested. |

## Not provable from code

Independent execution and sign-off (gates G0 to G4), the real release state and API inventory (D-06), the substrate decision (D-08), the privacy approvals, and measured latency. See CR-092.
