# Release ratification and go-live checklist (ZID-COM-RAT-011)

Generated 2026-11-04 by `tools/scripts/ratification.py` from `deploy/release/ratification.json`. This is a checklist for the signatories, not a decision: only the owners fill in the register. The default is NO-GO, and a recorded GO is refused by the checker unless everything below supports it.

- Recorded disposition: **NO-GO**
- What the register supports today: **NO-GO** (39 open items)
- Acceptance rows: IN_PROGRESS 902, NOT_STARTED 104

## Ratification register

| ID | Closure | Decision | Decision record | Policy version | Owner | Effective |
|---|---|---|---|---|---|---|
| CL-001 | Commercial roles | — | — | — | — | — |
| CL-002 | Billing rights | — | — | — | — | — |
| CL-003 | Discount bands and delegated commercial authority | — | — | — | — | — |
| CL-004 | Authority thresholds (commitment, credit note, pilot credit) | — | — | — | — | — |
| CL-005 | Finance console | — | — | — | — | — |
| CL-006 | Availability | — | — | — | — | — |

## Gates

| Gate | Name | Status | Reviewer | Evidence |
|---|---|---|---|---|
| G0 | Ratification | PENDING | — | — |
| G1 | As-built inventory | PENDING | — | — |
| G2 | Commercial eligibility | PENDING | — | — |
| G3 | Security engineering | PENDING | — | — |
| G4 | Money evidence | PENDING | — | — |
| G5 | UX and QA | PENDING | — | — |
| G6 | Controlled release | PENDING | — | — |

## No-go conditions

| ID | Condition | State | Why it is still open |
|---|---|---|---|
| NG-01 | Unratified monetary authority: discount bands, commitment, credit-note or pilot thresholds in force without a signed policy. | OPEN | CL-003 is not Approved; CL-004 is not Approved |
| NG-02 | Unsigned catalog, or a missing jurisdiction or merchant approval. | OPEN | gate G2 is not PASS |
| NG-03 | A critical tenant-isolation or authorization flaw, a wrong-issuer charge path or unsafe card handling. | OPEN | gate G3 is not PASS |
| NG-04 | A failing replay, double-refund, double-charge, reconciliation, evidence or rollback test. | OPEN | gate G4 is not PASS; acceptance row CL04-C14 is IN_PROGRESS, not VERIFIED; acceptance row WF09-OP-05 is IN_PROGRESS, not VERIFIED; acceptance row WF09-OP-09 is IN_PROGRESS, not VERIFIED; acceptance row WF09-OP-10 is IN_PROGRESS, not VERIFIED |
| NG-05 | Incomplete approver assignment, or an untested dual-signature rule. | OPEN | acceptance row CL04-C12 is IN_PROGRESS, not VERIFIED; acceptance row WF09-OP-03 is IN_PROGRESS, not VERIFIED; acceptance row WF10-C00-11 is IN_PROGRESS, not VERIFIED |

## Policy flags

| Flag | File | Key | Shipped | Disabled until |
|---|---|---|---|---|
| FLAG-commitment-enforcement | `backend/config/commercial/finance-policy.yaml` | `enforcement` | REPORT_ONLY | CL-004 |
| FLAG-band-matrix-enforcement | `backend/config/entitlements/capability-bands.yaml` | `band_matrix_enforcement` | REPORT_ONLY | CL-001 |
| FLAG-readiness-enforcement | `backend/config/onboarding/readiness-policy.yaml` | `enforcement` | REPORT_ONLY | — |
| FLAG-billing-access-enforcement | `backend/config/roles/role-policy.yaml` | `billing_access_enforcement` | ENFORCED | — |
| FLAG-bill-on-commit | `backend/config/entitlements/meter-register.yaml` | `bill_on_commit` | false | owners' switch |

## Release targeting

| Field | Value |
|---|---|
| release_id | — |
| policy_version | — |
| approved_countries | — |
| approved_products | — |
| approved_issuers | — |
| environment | — |
| date | — |

## Evidence register

| ID | Reference |
|---|---|
| E01 | — |
| E02 | — |
| E03 | — |
| E04 | — |
| E05 | — |
| E06 | — |
| E07 | — |

## Signatories

| Role | Name | Date | Decision |
|---|---|---|---|
| Founder |  |  |  |
| CFO |  |  |  |
| CCO |  |  |  |
| CTO |  |  |  |
| Security and Privacy |  |  |  |
| Finance Controller |  |  |  |
| Legal |  |  |  |

## Open items

- CL-001 has no decision
- CL-002 has no decision
- CL-003 has no decision
- CL-004 has no decision
- CL-005 has no decision
- CL-006 has no decision
- gate G0 is PENDING
- gate G1 is PENDING
- gate G2 is PENDING
- gate G3 is PENDING
- gate G4 is PENDING
- gate G5 is PENDING
- gate G6 is PENDING
- evidence E01 is not referenced
- evidence E02 is not referenced
- evidence E03 is not referenced
- evidence E04 is not referenced
- evidence E05 is not referenced
- evidence E06 is not referenced
- evidence E07 is not referenced
- release field release_id is blank
- release field policy_version is blank
- release field approved_countries is blank
- release field approved_products is blank
- release field approved_issuers is blank
- release field environment is blank
- release field date is blank
- Founder has not signed GO
- CFO has not signed GO
- CCO has not signed GO
- CTO has not signed GO
- Security and Privacy has not signed GO
- Finance Controller has not signed GO
- Legal has not signed GO
- NG-01 is open (CL-003 is not Approved; CL-004 is not Approved)
- NG-02 is open (gate G2 is not PASS)
- NG-03 is open (gate G3 is not PASS)
- NG-04 is open (gate G4 is not PASS; acceptance row CL04-C14 is IN_PROGRESS, not VERIFIED; acceptance row WF09-OP-05 is IN_PROGRESS, not VERIFIED; acceptance row WF09-OP-09 is IN_PROGRESS, not VERIFIED; acceptance row WF09-OP-10 is IN_PROGRESS, not VERIFIED)
- NG-05 is open (acceptance row CL04-C12 is IN_PROGRESS, not VERIFIED; acceptance row WF09-OP-03 is IN_PROGRESS, not VERIFIED; acceptance row WF10-C00-11 is IN_PROGRESS, not VERIFIED)
