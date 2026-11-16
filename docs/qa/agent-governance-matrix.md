# Agent governance (ZID-AG-WF-002 v1.0): the 46 acceptance tests against the repository

Written 2026-11-14 from the code and the tests. It decides nothing and passes nothing: the document requires each row to be executed independently with a build SHA, policy hash, fixture, report and reviewer. "EVIDENCE" below means a developer test exercises the behaviour; it is not a pass.

U = `backend/app/tests/unit/test_agent_governance.py`, C = `backend/app/tests/component/test_agent_governance_pg.py`, F = `frontend/apps/web/src/features/admin/agents/governance/logic.test.ts`, GW = `backend/app/tests/component/test_tool_gateway_pg.py`.

| State | Meaning | Rows |
|---|---|---|
| EVIDENCE | A test exercises the behaviour. | 37 |
| PARTIAL | Some of it is tested; the gap is named. | 7 |
| NOT_LINKED | No honest test exists; the reason is given. | 2 |

| ID | Domain | Pass condition (the document) | State | Test | Gap |
|---|---|---|---|---|---|
| T01 | Agent registry | Tenant cannot see agent from another tenant | EVIDENCE | C::test_another_tenant_sees_and_changes_nothing_of_this_tenants_governance, U::test_t01_t16_a_session_for_another_tenant_is_denied_without_detail | |
| T02 | Agent registry | Company entity boundary does not pool policies or evidence | PARTIAL | C::test_another_tenant_sees_and_changes_nothing_of_this_tenants_governance | Tenants are separated. Separate legal entities inside one group (the document's multi-entity case) are not modelled. |
| T03 | Agent registry | Inactive sponsor blocks activation and run | EVIDENCE | U::test_t03_inactive_agent_or_sponsor_blocks_a_run, C::test_policy_lifecycle_approval_and_every_gate | Activation itself is the registry's own gate (CR-021). |
| T04 | Agent registry | Agent ID immutable across display-name edits | NOT_LINKED | | Every governance row is keyed by `agent_principal_id`, never the name. No test renames an agent, and no rename route exists to test. |
| T05 | Authorization | Unknown tool denied by default with audit | EVIDENCE | U::test_t05_unknown_tool_is_denied_by_default, C::test_policy_lifecycle_approval_and_every_gate | |
| T06 | Authorization | Hard forbid wins over tenant allow | EVIDENCE | U::test_t06_t11_t12_t27_hard_deny_beats_every_allow | |
| T07 | Authorization | Cross-region data call denied outside configured cell | EVIDENCE | U::test_t07_a_call_outside_the_leased_region_is_denied, C::test_policy_lifecycle_approval_and_every_gate | |
| T08 | Authorization | Wildcard grant and unconstrained connector rejected | EVIDENCE | U::test_t08_wildcards_and_empty_scopes_cannot_be_requested, C::test_wildcards_hard_denies_and_unregistered_tools_cannot_even_be_requested | Connectors are tools here; there is no separate connector object. |
| T09 | Authorization | Expired lease and revoked session cannot call tool | EVIDENCE | U::test_t09_expired_lease_cannot_call | A revoked session is the identity layer's test (CR-021, CR-042). |
| T10 | Authorization | Changed policy hash invalidates previous authorization | EVIDENCE | U::test_t10_t22_a_changed_policy_invalidates_the_previous_approval, C::test_policy_lifecycle_approval_and_every_gate | |
| T11 | Authorization | DAARA cannot grant or revoke roles | EVIDENCE | U::test_t06_t11_t12_t27_hard_deny_beats_every_allow | There is no DAARA to run yet; the rule is on the action. |
| T12 | Authorization | DAARA cannot read secrets, documents or raw biometrics | EVIDENCE | U::test_t06_t11_t12_t27_hard_deny_beats_every_allow | Secrets are on the prohibition list; documents and biometrics are kept out by the allowed data classes (metadata, status, evidence reference). |
| T13 | Authorization | Model-generated requested tool cannot bypass signed catalog | EVIDENCE | C::test_wildcards_hard_denies_and_unregistered_tools_cannot_even_be_requested, backend/app/tests/component/test_tool_gateway_redteam_pg.py::test_prompt_injection_in_every_free_field_changes_nothing | |
| T14 | Authorization | One-use tool token fails on replay | EVIDENCE | GW::test_the_tool_gateway | Existing WP-11 behaviour (single-use grant); not changed here. |
| T15 | Authorization | No access with missing policy engine (fail closed) | EVIDENCE | U::test_t15_missing_policy_sources_fail_closed, C::test_policy_lifecycle_approval_and_every_gate | A policy-service outage is modelled as "a source is missing". |
| T16 | Authorization | No privileged API by direct URL / IDOR | EVIDENCE | C::test_http_routes_redaction_and_the_gateway, C::test_another_tenant_sees_and_changes_nothing_of_this_tenants_governance | |
| T17 | Approval | Requesting developer cannot approve own change | EVIDENCE | U::test_t17_the_requester_and_the_authors_cannot_sign_their_own_change, C::test_policy_lifecycle_approval_and_every_gate | |
| T18 | Approval | Approval requires both Security and Authority signers | EVIDENCE | U::test_t18_publish_needs_both_security_and_authority_and_one_person_fills_one_slot, C::test_policy_lifecycle_approval_and_every_gate | |
| T19 | Approval | Privacy signoff required if processing class changes | PARTIAL | U::test_t19_t20_slots_for_each_kind_of_change | The rule (a new data class adds PRIVACY) is tested; no end-to-end request adds a data class. |
| T20 | Approval | Finance signoff required for above-baseline budget increase | EVIDENCE | C::test_budgets_are_hierarchical_atomic_exact_and_settle_once, U::test_t19_t20_slots_for_each_kind_of_change | |
| T21 | Approval | Expired request cannot be signed or applied | EVIDENCE | U::test_t21_an_expired_request_cannot_be_signed_or_applied, C::test_policy_lifecycle_approval_and_every_gate | |
| T22 | Approval | Changed scope invalidates signatures | EVIDENCE | U::test_t22_a_signature_on_another_hash_is_refused_and_does_not_count, C::test_policy_lifecycle_approval_and_every_gate | |
| T23 | Approval | Approver role revoked mid-session fails at decision time | EVIDENCE | U::test_t23_an_approver_whose_role_was_revoked_or_who_left_fails_at_decision_time, C::test_policy_lifecycle_approval_and_every_gate | The "role" is the signer slot (decision AG2-D02). |
| T24 | Approval | Emergency pause does not wait for two approvals | EVIDENCE | C::test_runs_quotas_timeouts_and_the_emergency_stop | |
| T25 | Approval | Restore requires fresh independent security validation | EVIDENCE | U::test_t25_restore_needs_two_independent_qualified_people_and_fresh_checks, C::test_runs_quotas_timeouts_and_the_emergency_stop | |
| T26 | Budget | AI cost remains zero while provider disabled | EVIDENCE | U::test_t26_ai_cost_stays_zero_while_the_provider_is_disabled, C::test_budgets_are_hierarchical_atomic_exact_and_settle_once | |
| T27 | Budget | External purchasing tool is never accessible | EVIDENCE | U::test_t27_external_spend_and_paid_vendor_checks_are_never_reservable, C::test_budgets_are_hierarchical_atomic_exact_and_settle_once | |
| T28 | Budget | 100 concurrent reservations cannot exceed cap | EVIDENCE | C::test_budgets_are_hierarchical_atomic_exact_and_settle_once | One database, one machine. The document's own caveat: not a load run. |
| T29 | Budget | Same idempotency key cannot double-charge | EVIDENCE | C::test_budgets_are_hierarchical_atomic_exact_and_settle_once | |
| T30 | Budget | Partial tool failure releases unused reservation | EVIDENCE | C::test_budgets_are_hierarchical_atomic_exact_and_settle_once, C::test_a_stale_reservation_is_released_by_the_sweep | |
| T31 | Budget | Late invoice is reconciled without history overwrite | EVIDENCE | C::test_budgets_are_hierarchical_atomic_exact_and_settle_once | |
| T32 | Budget | Currency rounding uses decimal-safe arithmetic | EVIDENCE | U::test_t32_money_is_exact_and_never_a_float, F::money (exact decimal strings) | |
| T33 | Budget | Billing alert outage never lifts hard cap | NOT_LINKED | | By construction the reservation reads no alert or notification state, so an alert outage cannot change it. There is no test that simulates the outage. |
| T34 | Usage | Concurrency above limit queues/denies safely | EVIDENCE | U::test_t34_concurrency_waits_instead_of_retrying, C::test_runs_quotas_timeouts_and_the_emergency_stop | It denies with `WAITING_CAPACITY` semantics; there is no queue. |
| T35 | Usage | Records/run and daily quota enforced server-side | EVIDENCE | U::test_t35_records_per_run_and_per_day_are_hard_refusals, C::test_runs_quotas_timeouts_and_the_emergency_stop | |
| T36 | Usage | Timeout kills run, releases resources and writes evidence | EVIDENCE | U::test_t36_the_run_deadline_is_two_minutes, C::test_runs_quotas_timeouts_and_the_emergency_stop | The run is ended in the ledger on the next call or sweep; a tool server is not interrupted mid-call. |
| T37 | Usage | Scheduled runs cannot be enabled without policy | PARTIAL | U::test_t37_schedules_stay_off_unless_both_the_policy_and_the_company_allow_them | No scheduler exists, so there is nothing that could start a scheduled run. |
| T38 | Monitoring | Every invocation yields trace and policy receipt | EVIDENCE | C::test_a_pause_cancels_running_work_and_the_next_progress_is_refused, C::test_policy_lifecycle_approval_and_every_gate | Each governed call and run start writes a row with the policy hash it was judged under. |
| T39 | Monitoring | Telemetry outage is shown as unknown, not healthy | EVIDENCE | C::test_the_evidence_chain_detects_tampering_and_cannot_be_edited, F::levels and refusal wording | |
| T40 | Monitoring | Role-based export redacts unauthorized fields | EVIDENCE | C::test_the_evidence_export_is_redacted_by_role_audited_and_safe_to_open, C::test_http_routes_redaction_and_the_gateway | The CSV export drops the detail columns for a role without `agent.audit.read_detail`, makes spreadsheet formulas inert, and writes an evidence row for each export. |
| T41 | Incident | Kill switch blocks queued and fresh tool invocations | EVIDENCE | U::test_t41_a_freeze_blocks_the_call_and_outranks_every_other_gate, C::test_runs_quotas_timeouts_and_the_emergency_stop | There is no queue; new calls and redemptions are refused. |
| T42 | Incident | In-flight run cancellation follows approved response target | PARTIAL | C::test_a_pause_cancels_running_work_and_the_next_progress_is_refused | Running work is cancelled and a spent-later grant is refused. The 5-second target is not timed and was not tested with 20 agents. |
| T43 | Incident | Revocation survives restart, replay and failover | PARTIAL | C::test_a_pause_cancels_running_work_and_the_next_progress_is_refused | The freeze is a database row and a fresh connection sees it. No process restart, replay or failover was exercised. |
| T44 | UI/UX | Five screens responsive, keyboard and screen-reader viable | PARTIAL | F, tsc and eslint | Built on the design system's accessible components with headings, labelled tables and live regions, but no real-browser, keyboard or screen-reader test was run. |
| T45 | UI/UX | Approval and pending states are never shown as active | EVIDENCE | F::never shows an approved request as live | |
| T46 | Global | No unapproved cross-region transfer / provider fallback | PARTIAL | U::test_t07_a_call_outside_the_leased_region_is_denied | No cross-region data path and no AI provider adapter exist, so there is no fallback to test. |

## Not provable from code

Independent execution and sign-off (gates G0 to G5), DPIAs and retention approvals (AG2-D08), the penetration review, failure injection at scale, the emergency-stop drill, and measured SLOs. See CR-105 for the decisions still open.
