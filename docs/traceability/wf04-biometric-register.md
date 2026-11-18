# ZID-DTV-WF-04 Biometric Security, Privacy & DPIA: traceability register

Where each requirement of Wireframe 04 stands in the code, and the test that shows it. Built by CR-107, CR-108, CR-109, CR-110 and CR-113 on top of the
existing privacy control plane (ZID-PRI-004). Status words:

- **Covered**: built, and an automated test named here shows the behaviour.
- **Existing**: the platform already did this before WF-04 work; the test is in the existing suite.
- **Partly**: built in part; what is missing is said.
- **Not engineering**: needs people, a lab, a contract or a legal decision; the code waits for it and fails closed.
- **Not done**: engineering work that is not built.
- **N/A**: does not apply to the platform as it is (the reason is given).

Test files: `U-M` `backend/app/tests/unit/test_biometric_methods.py`; `U-E` `.../unit/test_biometric_eligibility.py`; `U-C`
`.../unit/test_biometric_containment_retention.py`; `PG` `.../component/test_biometric_governance_pg.py` (PostgreSQL); `W` web
`features/admin/privacy/biometric-governance.test.tsx`; `WA` web `features/account/verification-session.test.tsx`; `OP` operator
`features/regulatory/biometric-methods.test.tsx`; `G` `.../unit/test_biometric_gap_closure.py`; `GP` `.../component/test_biometric_gap_closure_pg.py`; `LC` `.../component/test_biometric_load_chaos_pg.py`; `CK` `.../component/test_biometric_class_keys_pg.py`; `WG` web `features/admin/privacy/wf04-gap-closure.test.tsx`.

## QA-001 to QA-045

| QA | Requirement (short) | Status | Evidence |
|---|---|---|---|
| QA-001 | No or expired DPIA disables the method | Covered | U-E `test_dpia_not_ready_refuses_and_names_blockers`; PG `test_eligibility_names_every_refusing_gate_over_real_tables` |
| QA-002 | No Article 9 condition blocks face match despite consent | Existing | ADM special-category guard (`privacy/domain/adm.py`), country overlay consent check (`test_privacy_overlay*`); no biometric-specific Article 9 registry |
| QA-003 | DPIA author cannot approve their own | Existing | `test_v2_privacy_pg` (SIGNER_IS_AUTHOR) |
| QA-004 | A policy change after build suspends the route | Covered | The DPIA stores the digest of the policy it assessed; a change names DPIA_POLICY_CHANGED and the method stops (CR-113): GP `test_a_dpia_is_refused_once_the_policy_it_assessed_changes`, G `test_a_policy_change_after_approval_is_called_changed` |
| QA-005 | Failover to an unapproved region denied, no data sent | Existing | Overlay CROSS_BORDER_ROUTE_DENIED (T32); transfer decisions default deny |
| QA-006 | No pre-selected consent; notice version bound | Covered | WA `explains each blocker in words and never pre-ticks consent`, `acknowledges the notice separately from consent` |
| QA-007 | Withdrawn consent stops new biometric processing at once | Existing | Session status re-checks processing live (AT-003) |
| QA-008 | Withdrawal does not erase statutory records | Existing / Covered | Consent receipts are terminal, not deleted; WA `explains what withdrawing a consent does before it is done` |
| QA-009 | Low-literacy, keyboard, screen-reader, non-camera routes usable | Partly | Alternatives and the assisted route are built (WA, W assisted tests); automated axe matrix (WG); a screen-reader run on real devices is manual |
| QA-010 | No disability or medical reason required | Covered | WA `chooses another way without asking why`; W `records a case that could not be completed with a reason code, never a personal reason`; no column stores a reason for the choice |
| QA-011 | Printed photo, replay, virtual camera, deepfake, injection exercised | Not engineering | Independent lab or supplier test; the engine gate requires ISO/IEC 30107-3 measured rates |
| QA-012 | Inconclusive PAD never a full pass after a face match | Existing | `outcome_routing` (AT-010) |
| QA-013 | Unsupported phone, chip or document has an explicit unavailable state | Existing | Self-run and capture alternatives |
| QA-014 | Provider timeout never becomes a pass or a hidden region | Existing | Self-run engine error is PENDING; overlay region rules |
| QA-015 | A model upgrade without a lab test and signed approval is refused | Covered | U-M `test_scope_must_match_engine_version_exactly`; U-E `test_evaluation_not_ready_refuses_and_names_blockers`, `test_a_provider_without_an_active_model_is_refused`; model drift hold (existing) |
| QA-016 | Device, camera and lighting cohorts sampled with uncertainty | Partly | Evaluation harness (Wilson bounds per cohort, existing); B07 records each measure with its interval; the sampling itself is the evaluator's |
| QA-017 | Undersampled cohorts give DATA_INSUFFICIENT, never pass | Covered | U-C `test_fairness_states`; harness INSUFFICIENT_DATA (existing) |
| QA-018 | Protected attributes never reach a live score | Existing | `models.protected_features` (AT-017); audit attributes may not be live features |
| QA-019 | Severe disparity triggers mitigation, retest and pause | Covered | U-C `test_fairness_states` (disparity flagged is FAIL); a failed or stale latest evaluation refuses the method at run time (gate 7); reauthorization holds a stale model (PG) |
| QA-020 | A false rejection can be contested and corrected | Covered | `test_doc04_closing_pg` (review, checker, decision.changed); W review tests |
| QA-021 | Raw media, template, claim and audit each have their own TTL | Covered | Five asset classes, each needs its own retention policy (U-C, PG); each biometric class is encrypted under its own key per subject (CR-113): CK `test_each_biometric_class_is_under_its_own_key`, G `test_a_class_key_does_not_open_another_class` |
| QA-022 | Vendor receipt before a class is purged | Covered | U-C `test_a_class_is_purged_only_when_its_tasks_and_the_backups_are_acknowledged` |
| QA-023 | Provider deletion error is retried and alerted, never completed | Covered | Worker step `erasure_retry` asks again with growing waits, then FAILED and one alert (CR-113): GP `test_an_overdue_provider_deletion_is_retried_then_alerted_and_never_completed`, WG |
| QA-024 | A legal hold keeps only what it covers | Existing / Covered | Erasure holds per entry (existing); U-C `test_a_held_or_failed_task_shows_on_its_class` |
| QA-025 | Restoring a backup replays erasure tombstones | Existing | `evidence/application/vault.py` `reapply_tombstones` |
| QA-026 | Cross-tenant reads denied, even privileged | Existing | Row-level security on every tenant table |
| QA-027 | Owner or Admin has no implicit raw-media access | N/A | The platform keeps no raw biometric media and has no media read path (BIO-14) |
| QA-028 | One product's case not retrievable by another product | Covered | U-C `test_one_products_approval_never_serves_anothers_purpose`, `test_shipped_use_cases_are_not_certified_until_signed`; tenant RLS |
| QA-029 | Cross-region fallback fails closed with a non-PII event | Covered | Overlay refuses (existing); `verification.biometric.region_blocked` (CR-113): GP `test_a_biometric_route_refused_for_its_region_writes_its_own_event` |
| QA-030 | Only redacted claims returned; no embedding in a webhook | Existing | `export_mark`; every new event carries codes and hashes only |
| QA-031 | An appeal reviewer cannot overwrite the first result or delete audit | Covered | Reviews keep the original decision reference; audit is append-only; checker step (U-C, `test_doc04_closing_pg`) |
| QA-032 | Two-person sign-off for high-risk overrides | Covered | U-C `test_the_checker_is_a_third_person`; W `never offers the requester their own review or check` |
| QA-033 | A provider incident permits an emergency pause | Covered | U-M `test_pause_is_one_person_at_once`; OP `pauses a scope at once with a reason and evidence`; PG breach pause |
| QA-034 | Incident report has regions, data classes, owner, legal triage | Covered | Jurisdictions, clocks, owner (opened_by), capture timeline, audit trail and affected data classes (CR-113): GP `test_a_breach_records_its_affected_data_classes_and_never_drops_one`, WG; legal triage is the notification record per jurisdiction |
| QA-035 | Event payload scan finds no raw media or templates | Covered | G `test_no_biometric_event_or_audit_payload_names_media_or_personal_data` (every WF-04 emitter), GP `test_no_event_these_flows_write_names_a_person_or_media`, LC (under load); a SIEM run on deployed traffic is the operator's |
| QA-036 | A rights request without a face capture can be verified otherwise | Existing | Proportionate verification (PRIV-14) |
| QA-037 | A processor's rights request goes to the controller | Existing | PENDING_PROCESSOR_ACK items |
| QA-038 | Consent record shows notice, purpose, locale, action, withdrawal | Existing | Consent receipts and notice acknowledgments |
| QA-039 | Retiring a market policy blocks new sessions, keeps dispositions | Covered | Retire a market in one action (CR-113): GP `test_retiring_a_market_turns_every_scope_off_and_keeps_history`; operator test |
| QA-040 | A partial deletion names kept classes and the hold | Covered | Per-class HELD (U-C); WA `says what a limited answer means` |
| QA-041 | Every P0 gate has signed evidence for the exact provider, model, purpose, region, build | Covered | `decision_hash` (U-M) plus the release attestation of build and capture SDK (CR-113): GP `test_an_approval_records_the_build_it_was_signed_for`, G release tests, WG |
| QA-042 | Load and chaos tests leak nothing sensitive | Covered | LC: concurrent answers agree and fail closed; faulted gates give a plain error with nothing personal in the body or logs; events under faults stay minimal. A production-scale load run is the operator's |
| QA-043 | A disabled method appears nowhere: UI, eligibility, callback | Covered | U-E, PG eligibility; self-run and provider start gated (`test_self_run_session_pg`, `test_privacy_overlay_http_pg`) |
| QA-044 | Localized journeys pass the accessibility matrix | Partly | Automated axe matrix in en/LTR, ar/RTL, hi/LTR (WG, CR-113); contrast, zoom and a screen-reader run on real devices are manual |
| QA-045 | Rollback to an approved model keeps consistency | Covered | GP `test_rolling_a_provider_model_back_moves_every_gate_to_that_version`; model rollback endpoint (existing) |

## Work packages BIO-01 to BIO-20

| BIO | Status | Where |
|---|---|---|
| 01 DPIA case and approval graph | Covered | Existing control plane; DPIA tab (B03); live binding (CR-107) |
| 02 Legal basis and special-category registry | Partly | Purposes, activities, overlays, ADM rules exist; values are counsel's (COUNSEL) |
| 03 Method kill switch and states | Covered | CR-107, operator page |
| 04 Notice, scope and consent UX | Covered | Session page (B01) |
| 05 Alternative and assisted workflows | Covered | Route choice (B02), assisted queue (CR-110) |
| 06 Provider, subprocessor, transfer inventory | Covered | Transfers tab (B04) |
| 07 Regional encryption and tenant separation | Existing | Residency and vault; penetration test is people work |
| 08 Asset class retention and holds | Covered | CR-108 |
| 09 Erasure and vendor deletion proof | Covered | CR-108 per-class erasure, dry run |
| 10 PAD, match, deepfake independent assessment | Not engineering | Lab; evidence gate enforces it (CR-109) |
| 11 Fairness evaluation governance | Covered | B07 states and screen (CR-110) |
| 12 Appeals and reviewer permissions | Covered | CR-109 checker, reviews tab, my reviews (CR-110) |
| 13 DPA and transfer attestation | Partly | Processor approval and transfer decisions; DPA documents are people work |
| 14 Media ABAC | N/A | No raw biometric media is kept or readable |
| 15 Go/no-go release console | Covered | Readiness tab, operator states, decision hash, exposure (CR-110) |
| 16 Incident, notification, pause | Covered | Breaches tab, capture pause, dossier, processor suspension |
| 17 Rights processing and delegation | Existing | Rights requests |
| 18 Observability and minimized metrics | Covered | Oversight tab (B12) |
| 19 Zoiko Rooms and Zoikorum certification | Covered | Use cases, gate 9 (CR-110); acceptance values are COUNSEL |
| 20 Scheduled reauthorization | Covered | Worker step `biometric_reauthorization` (CR-110) |

## Screens B01 to B12

B01 session page; B02 route choice and assisted queue; B03 DPIA tab; B04 transfers tab; B05 erasure tab; B06 privacy centre; B07 models tab;
B08 appeals tab and my reviews; B09 breaches tab; B10 N/A (no media); B11 readiness tab and operator biometric methods; B12 oversight tab.

## Not engineering (owners)

DEC-01 to DEC-12 of WF-04; the proposed values in CR-107 to CR-110; real DPIA content; independent PAD, match and fairness lab reports; vendor DPAs
and transfer instruments; the ecosystem integration acceptances; G0 to G3 sign-offs.
