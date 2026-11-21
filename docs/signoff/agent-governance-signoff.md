# Sign-off pack: ZID-AG-WF-002

Generated 2026-11-21 by `tools/scripts/signoff_pack.py` from `docs/traceability/acceptance-tests.csv`. This is a checklist for the approvers. It does not approve anything: a test is done only when an approver changes its status in the traceability file.

- Acceptance tests: **46**; blockers: **0** (not started, no resolving evidence, or a measurement that says its gate is not met)

## Tests and evidence

| ID | Test | Status | Evidence | Resolves |
|---|---|---|---|---|
| AG2-T01 | Agent registry: Tenant cannot see agent from another tenant | IN_PROGRESS | `backend/app/tests/component/test_agent_governance_pg.py::test_another_tenant_sees_and_changes_nothing_of_this_tenants_governance` | yes |
| AG2-T02 | Agent registry: Company entity boundary does not pool policies or evidence | IN_PROGRESS | `backend/app/tests/component/test_agent_governance_pg.py::test_another_tenant_sees_and_changes_nothing_of_this_tenants_governance` | yes |
| AG2-T03 | Agent registry: Inactive sponsor blocks activation and run | IN_PROGRESS | `backend/app/tests/unit/test_agent_governance.py::test_t03_inactive_agent_or_sponsor_blocks_a_run` | yes |
| AG2-T05 | Authorization: Unknown tool denied by default with audit | IN_PROGRESS | `backend/app/tests/unit/test_agent_governance.py::test_t05_unknown_tool_is_denied_by_default` | yes |
| AG2-T06 | Authorization: Hard forbid wins over tenant allow | IN_PROGRESS | `backend/app/tests/unit/test_agent_governance.py::test_t06_t11_t12_t27_hard_deny_beats_every_allow` | yes |
| AG2-T07 | Authorization: Cross-region data call denied outside configured cell | IN_PROGRESS | `backend/app/tests/unit/test_agent_governance.py::test_t07_a_call_outside_the_leased_region_is_denied` | yes |
| AG2-T08 | Authorization: Wildcard grant and unconstrained connector rejected | IN_PROGRESS | `backend/app/tests/unit/test_agent_governance.py::test_t08_wildcards_and_empty_scopes_cannot_be_requested` | yes |
| AG2-T09 | Authorization: Expired lease and revoked session cannot call tool | IN_PROGRESS | `backend/app/tests/unit/test_agent_governance.py::test_t09_expired_lease_cannot_call` | yes |
| AG2-T10 | Authorization: Changed policy hash invalidates previous authorization | IN_PROGRESS | `backend/app/tests/unit/test_agent_governance.py::test_t10_t22_a_changed_policy_invalidates_the_previous_approval` | yes |
| AG2-T11 | Authorization: DAARA cannot grant or revoke roles | IN_PROGRESS | `backend/app/tests/unit/test_agent_governance.py::test_t06_t11_t12_t27_hard_deny_beats_every_allow` | yes |
| AG2-T12 | Authorization: DAARA cannot read secrets, documents or raw biometrics | IN_PROGRESS | `backend/app/tests/unit/test_agent_governance.py::test_t06_t11_t12_t27_hard_deny_beats_every_allow` | yes |
| AG2-T13 | Authorization: Model-generated requested tool cannot bypass signed catalog | IN_PROGRESS | `backend/app/tests/component/test_agent_governance_pg.py::test_wildcards_hard_denies_and_unregistered_tools_cannot_even_be_requested` | yes |
| AG2-T14 | Authorization: One-use tool token fails on replay | IN_PROGRESS | `backend/app/tests/component/test_tool_gateway_pg.py::test_the_tool_gateway` | yes |
| AG2-T15 | Authorization: No access with missing policy engine (fail closed) | IN_PROGRESS | `backend/app/tests/unit/test_agent_governance.py::test_t15_missing_policy_sources_fail_closed` | yes |
| AG2-T16 | Authorization: No privileged API by direct URL / IDOR | IN_PROGRESS | `backend/app/tests/component/test_agent_governance_pg.py::test_http_routes_redaction_and_the_gateway` | yes |
| AG2-T17 | Approval: Requesting developer cannot approve own change | IN_PROGRESS | `backend/app/tests/unit/test_agent_governance.py::test_t17_the_requester_and_the_authors_cannot_sign_their_own_change` | yes |
| AG2-T18 | Approval: Approval requires both Security and Authority signers | IN_PROGRESS | `backend/app/tests/unit/test_agent_governance.py::test_t18_publish_needs_both_security_and_authority_and_one_person_fills_one_slot` | yes |
| AG2-T19 | Approval: Privacy signoff required if processing class changes | IN_PROGRESS | `backend/app/tests/unit/test_agent_governance.py::test_t19_t20_slots_for_each_kind_of_change` | yes |
| AG2-T20 | Approval: Finance signoff required for above-baseline budget increase | IN_PROGRESS | `backend/app/tests/component/test_agent_governance_pg.py::test_budgets_are_hierarchical_atomic_exact_and_settle_once` | yes |
| AG2-T21 | Approval: Expired request cannot be signed or applied | IN_PROGRESS | `backend/app/tests/unit/test_agent_governance.py::test_t21_an_expired_request_cannot_be_signed_or_applied` | yes |
| AG2-T22 | Approval: Changed scope invalidates signatures | IN_PROGRESS | `backend/app/tests/unit/test_agent_governance.py::test_t22_a_signature_on_another_hash_is_refused_and_does_not_count` | yes |
| AG2-T23 | Approval: Approver role revoked mid-session fails at decision time | IN_PROGRESS | `backend/app/tests/unit/test_agent_governance.py::test_t23_an_approver_whose_role_was_revoked_or_who_left_fails_at_decision_time` | yes |
| AG2-T24 | Approval: Emergency pause does not wait for two approvals | IN_PROGRESS | `backend/app/tests/component/test_agent_governance_pg.py::test_runs_quotas_timeouts_and_the_emergency_stop` | yes |
| AG2-T25 | Approval: Restore requires fresh independent security validation | IN_PROGRESS | `backend/app/tests/unit/test_agent_governance.py::test_t25_restore_needs_two_independent_qualified_people_and_fresh_checks` | yes |
| AG2-T26 | Budget: AI cost remains zero while provider disabled | IN_PROGRESS | `backend/app/tests/unit/test_agent_governance.py::test_t26_ai_cost_stays_zero_while_the_provider_is_disabled` | yes |
| AG2-T27 | Budget: External purchasing tool is never accessible | IN_PROGRESS | `backend/app/tests/unit/test_agent_governance.py::test_t27_external_spend_and_paid_vendor_checks_are_never_reservable` | yes |
| AG2-T28 | Budget: 100 concurrent reservations cannot exceed cap | IN_PROGRESS | `backend/app/tests/component/test_agent_governance_pg.py::test_budgets_are_hierarchical_atomic_exact_and_settle_once` | yes |
| AG2-T29 | Budget: Same idempotency key cannot double-charge | IN_PROGRESS | `backend/app/tests/component/test_agent_governance_pg.py::test_budgets_are_hierarchical_atomic_exact_and_settle_once` | yes |
| AG2-T30 | Budget: Partial tool failure releases unused reservation | IN_PROGRESS | `backend/app/tests/component/test_agent_governance_pg.py::test_budgets_are_hierarchical_atomic_exact_and_settle_once` | yes |
| AG2-T31 | Budget: Late invoice is reconciled without history overwrite | IN_PROGRESS | `backend/app/tests/component/test_agent_governance_pg.py::test_budgets_are_hierarchical_atomic_exact_and_settle_once` | yes |
| AG2-T32 | Budget: Currency rounding uses decimal-safe arithmetic | IN_PROGRESS | `backend/app/tests/unit/test_agent_governance.py::test_t32_money_is_exact_and_never_a_float` | yes |
| AG2-T34 | Usage: Concurrency above limit queues/denies safely | IN_PROGRESS | `backend/app/tests/unit/test_agent_governance.py::test_t34_concurrency_waits_instead_of_retrying` | yes |
| AG2-T35 | Usage: Records/run and daily quota enforced server-side | IN_PROGRESS | `backend/app/tests/unit/test_agent_governance.py::test_t35_records_per_run_and_per_day_are_hard_refusals` | yes |
| AG2-T36 | Usage: Timeout kills run, releases resources and writes evidence | IN_PROGRESS | `backend/app/tests/unit/test_agent_governance.py::test_t36_the_run_deadline_is_two_minutes` | yes |
| AG2-T37 | Usage: Scheduled runs cannot be enabled without policy | IN_PROGRESS | `backend/app/tests/unit/test_agent_governance.py::test_t37_schedules_stay_off_unless_both_the_policy_and_the_company_allow_them` | yes |
| AG2-T38 | Monitoring: Every invocation yields trace and policy receipt | IN_PROGRESS | `backend/app/tests/component/test_agent_governance_pg.py::test_a_pause_cancels_running_work_and_the_next_progress_is_refused` | yes |
| AG2-T39 | Monitoring: Telemetry outage is shown as unknown, not healthy | IN_PROGRESS | `backend/app/tests/component/test_agent_governance_pg.py::test_the_evidence_chain_detects_tampering_and_cannot_be_edited` | yes |
| AG2-T40 | Monitoring: Role-based export redacts unauthorized fields | IN_PROGRESS | `backend/app/tests/component/test_agent_governance_pg.py::test_http_routes_redaction_and_the_gateway` | yes |
| AG2-T41 | Incident: Kill switch blocks queued and fresh tool invocations | IN_PROGRESS | `backend/app/tests/unit/test_agent_governance.py::test_t41_a_freeze_blocks_the_call_and_outranks_every_other_gate` | yes |
| AG2-T42 | Incident: In-flight run cancellation follows approved response target | IN_PROGRESS | `backend/app/tests/component/test_agent_governance_pg.py::test_a_pause_cancels_running_work_and_the_next_progress_is_refused` | yes |
| AG2-T43 | Incident: Revocation survives restart, replay and failover | IN_PROGRESS | `backend/app/tests/component/test_agent_governance_pg.py::test_a_pause_cancels_running_work_and_the_next_progress_is_refused` | yes |
| AG2-T44 | UI/UX: Five screens responsive, keyboard and screen-reader viable | IN_PROGRESS | `frontend/tests/e2e/agent-governance-a11y.spec.ts` | yes |
| AG2-T45 | UI/UX: Approval and pending states are never shown as active | IN_PROGRESS | `frontend/apps/web/src/features/admin/agents/governance/logic.test.ts` | yes |
| AG2-T46 | Global: No unapproved cross-region transfer / provider fallback | IN_PROGRESS | `backend/app/tests/unit/test_agent_governance.py::test_t07_a_call_outside_the_leased_region_is_denied` | yes |
| AG2-T04 | Agent registry: Agent ID immutable across display-name edits | IN_PROGRESS | `backend/app/tests/component/test_agent_register_rows_pg.py::test_an_agents_id_never_changes_when_its_display_name_is_edited` | yes |
| AG2-T33 | Budget: Billing alert outage never lifts hard cap | IN_PROGRESS | `backend/app/tests/component/test_agent_register_rows_pg.py::test_a_billing_or_alert_outage_never_lifts_a_hard_cap` | yes |

## Blockers

None.

## Approvals

| Lead | Name | Date | Decision |
|---|---|---|---|
| Founder |  |  |  |
| Global CTO |  |  |  |
| Product |  |  |  |
| Security |  |  |  |
| Finance |  |  |  |
| Privacy |  |  |  |
