# Verify API acceptance map (ZID-EXT-IDV-001 section 14, Q01 to Q50)

Written 25 Nov 2026 against the code and tests in the repository. It says what each of the 50 acceptance scenarios is held by today. It is evidence of coverage, not of readiness: the document's own rule is that production readiness is claimed only from signed G0 to G4 evidence.

**Status words**
- **COVERED**: an automated test in this repository fails if the behaviour breaks.
- **PARTIAL**: some of the scenario is tested; the part that is not is named.
- **INHERITED**: the behaviour belongs to another module (webhooks, onboarding, privacy) and is held by that module's tests, not by a Verify API test.
- **NEEDS ENGINE**: cannot be tested until the live check engine is connected (production sessions are refused today).
- **NOT COVERED**: nothing tests it yet.

| Q | Scenario | Status | Held by |
|---|---|---|---|
| Q01 | Duplicate entity and domain takeover refused at partner registration | INHERITED | Partner onboarding is the existing company signup and KYB (organization and onboarding modules). No Verify API test. |
| Q02 | Sandbox signup cannot issue a production credential | COVERED | `test_only_the_sandbox_can_be_created_until_the_production_gate_has_passed`; the go-live test asserts `PRODUCTION_NOT_APPROVED` before approval. |
| Q03 | Unknown client id and revoked secret return 401 | COVERED | `test_every_failure_gets_the_same_answer_and_a_token_cannot_carry_more_than_the_application_holds`; `test_revoking_a_credential_or_suspending_an_application_ends_its_tokens_at_once`. |
| Q04 | Client A cannot read client B's session under one company | COVERED | `test_an_application_sees_only_its_own_sessions_and_a_token_only_what_its_scopes_allow`. |
| Q05 | Company A cannot reach company B by id substitution | COVERED | `test_one_company_never_sees_or_changes_another_companys_applications_or_credentials`; `test_another_company_sees_none_of_it`; the cases and go-live tests repeat it for their rows. |
| Q06 | A token from one environment cannot create or touch a session in the other | COVERED | The go-live approval test (a production token gets not-found for a sandbox session, on read and on simulate) and `test_a_caller_may_touch_only_a_session_of_its_own_environment` for the `ENVIRONMENT_MISMATCH` check itself. |
| Q07 | An application bound to one region cannot force another by payload | PARTIAL | Region is validated and pinned on the session (`test_verify_api_sessions_pg.py`). No test with two regions, because only one cell region is deployed. |
| Q08 | An expired or pending KYB partner cannot invoke live verification | COVERED | Approval needs a registry-verified company (`test_a_company_without_a_registry_verified_check_is_not_approved_whatever_the_operators_confirm`); production sessions are refused without a grant. |
| Q09 | No price entitlement cannot launch a billable line | PARTIAL | Production is refused without a grant. A price check is covered once pricing exists (see the pricing slice). |
| Q10 | Unsupported country or method fails cleanly before any personal data is captured | PARTIAL | A bad request creates nothing (`test_a_bad_request_is_refused_before_anything_is_created`). A market with no published policy needs the engine. |
| Q11 | Repeating an identical request returns the same session, not a duplicate charge | COVERED | `test_creating_a_session_is_safe_to_repeat_and_a_changed_request_under_the_same_key_is_refused`. |
| Q12 | The same key with a changed body is a conflict | COVERED | Same test (`INVALID_IDEMPOTENCY_REUSE`). |
| Q13 | An invalid workflow version refuses the session | COVERED | `WORKFLOW_VERSION_UNAVAILABLE` in the sessions tests. |
| Q14 | An expired launch token cannot capture or resume | COVERED | `test_a_session_can_be_canceled_resumed_and_expires_on_its_own`; `test_guessing_launch_links_is_limited_and_the_catalogue_is_readable`. |
| Q15 | Tampered redirect target, origin and callback domain are rejected | PARTIAL | Return address must be a registered origin (`RETURN_ORIGIN_NOT_REGISTERED`). The callback domain rule is the webhook module's (INHERITED). |
| Q16 | The browser or mobile page carries no server secret | PARTIAL | The hosted page receives only the one-time launch token by design; there is no automated scan of the shipped bundle. |
| Q17 | A posted "verified" flag cannot bypass capture | PARTIAL | Covered for the sandbox, needs the engine for live: `test_the_normal_path_goes_through_capture_and_cannot_skip_to_a_result`. The sandbox simulator is the only way to a result, and is refused on production. |
| Q18 | Text reading alone never grants authenticity | NEEDS ENGINE | Held today by the engine's own decision rules (`idv_decision`); not exercised through the partner API. |
| Q19 | An unapproved market rejects the liveness or face check before collection | NEEDS ENGINE | |
| Q20 | An untrusted or absent chip certificate gives indeterminate, not approved | NEEDS ENGINE | The chip path is not in the chosen launch method set. |
| Q21 | A provider timeout gives pending or inconclusive, never verified | NEEDS ENGINE | |
| Q22 | A forged provider callback never changes a decision | NEEDS ENGINE | No provider callback exists: the engine is self-run. |
| Q23 | A reviewer cannot self-approve a conflicted case | PARTIAL | Held for go-live approval and for dispute and erasure cases (different operator). Reviewer conflict on a verification itself needs the engine. |
| Q24 | A revised decision supersedes the original with an audit trail | COVERED | `test_every_simulated_outcome_gives_its_own_session_state_and_a_review_resolved_later_supersedes_the_first_decision`; `test_a_recheck_is_a_new_session_that_follows_a_finished_one_and_leaves_the_original_untouched`. |
| Q25 | A partner cannot view raw media by default | COVERED | The decision and session views carry a fixed field set with no media (`test_verify_api_sessions_pg.py`, the field-list assertion); no route returns media. |
| Q26 | A bad, missing or stale webhook signature is rejected by the sample receiver | INHERITED | The webhook module signs with a published key (RFC 9421), not an HMAC secret as the document sketches. `test_rfc9421_signature_verifies_with_the_public_key` and the webhook component tests. |
| Q27 | Out-of-order and duplicate events reconcile by decision version | PARTIAL | Every decision event carries `decision_version` and `supersedes_decision_id` (tested). Receiver-side reconciliation is the partner's. |
| Q28 | A test webhook is clearly tagged sandbox | COVERED | The event data carries `sandbox` (`test_a_decision_or_an_ended_session_reaches_the_companys_webhook_endpoint_without_personal_data`). |
| Q29 | A webhook URL aimed at localhost, link-local or a rebinding address is blocked | INHERITED | `unit/test_webhooks.py` and `component/test_webhooks_pg.py` (resolver and address-class checks). |
| Q30 | Rotating a webhook secret with overlap and expiry works | INHERITED | Signing keys rotate with an overlap window (`ZID_WEBHOOK_RETIRING_KEYS`); there is no per-endpoint secret to rotate. |
| Q31 | Repeated delivery failures are retried and a dead letter alert is raised | INHERITED | `test_retry_schedule_and_limits` and the webhook delivery tests. |
| Q32 | Data residency holds in logs, backups, metrics and media | NOT COVERED | A platform property, not a Verify API one. Metrics carry no company or session label (`test_every_counter_is_defined_with_labels_the_platform_allows`). |
| Q33 | A customer reference cannot reveal a person in another company | PARTIAL | References are per company under row-level security, and an erasure matches by a company-scoped hash (`test_a_reference_is_kept_only_as_a_hash_that_differs_between_companies`). |
| Q34 | Cross-company list, search, export and cache leaks fail a negative test | PARTIAL | List and search are covered per company (`test_the_company_searches_its_sessions_across_applications_and_reads_one_with_its_history`). There is no export route and no shared cache. |
| Q35 | Unknown JSON fields are tolerated by consumers | NOT COVERED | A client-side property; the published contract adds fields without removing any (contract drift test). |
| Q36 | P95 and P99 load and noisy-tenant isolation | NOT COVERED | Needs ratified targets (decision D13) and a load environment. |
| Q37 | No card number or CVC in integration or billing logs | NOT COVERED | The Verify API handles no card data. Held by the commercial module. |
| Q38 | Meter reconciliation shows no unexplained double billing | COVERED | `test_a_production_session_is_one_usage_fact_at_start_and_one_at_its_final_decision_never_twice`; the usage reconciliation in `test_verify_api_usage_pg.py`. |
| Q39 | Declining optional biometric consent stops only the unauthorized processing | NEEDS ENGINE | |
| Q40 | Deletion and legal hold keep the audit trail minimal but whole | PARTIAL | Erasure removes the reference and keeps decisions (cases test). A legal hold is handled by rejecting the request with a note; there is no automated hold flag. |
| Q41 | A case update triggers the right late or superseded event | COVERED | The supersession test above; the decision event fires on every new version. |
| Q42 | WCAG 2.2 AA keyboard, screen reader, language and alternative flow | NOT COVERED | No automated accessibility run on the hosted page. |
| Q43 | Production activation is blocked until every signed gate is satisfied | COVERED | `test_an_operator_reviews_a_different_operator_approves_and_production_opens_with_a_record` (five confirmations, company check, signature, a second person). |
| Q44 | Emergency suspend stops tokens, sessions and processing | COVERED | The go-live revoke test (production application suspended, token stops at once); `test_a_suspended_application_stops_creating_sessions_and_its_links_stop_opening`. |
| Q45 | Disaster recovery keeps session, event and ledger invariants | NOT COVERED | Platform backup and restore drills (RB-14). |
| Q46 | A provider or market method not certified in this deployment is denied | NEEDS ENGINE | |
| Q47 | A webhook destination must match the partner and the approved region | PARTIAL | Endpoints belong to one company and must pass the webhook checks. Region matching waits for a second region. |
| Q48 | A clean-room developer can finish the first sandbox flow from the documentation | PARTIAL | The documentation is held to the contract by tests; no outside developer has run it yet. |
| Q49 | A wrong purpose or data policy limits the result fields | NOT COVERED | The result is always the same minimized set; purpose-based narrowing is not built. |
| Q50 | A retention timer prevents stale raw document delivery | NEEDS ENGINE | No raw document is held or delivered by the Verify API. |

**Counts (50 scenarios):** COVERED 17, PARTIAL 13, INHERITED 5, NEEDS ENGINE 8, NOT COVERED 7.

The gaps that can be closed without the engine are Q35, Q36, Q42 and Q49 (client behaviour, load, accessibility and purpose-based narrowing). The 8 that need the engine are the verification-quality scenarios (Q18 to Q22, Q39, Q46, Q50): they are the acceptance list for the engine slice.
