# WF02 company verification (KYB): what exists, what this work added, what is open

Prepared 2026-11-17 by engineering. Companion to `CR-109`. It states evidence honestly: **rules and tests exist; nothing has run against a real registry, a real reviewer population or a signed market policy.** Evidence scale: E0 idea, E1 referenced, E2 built and tested here, E3 proved on real inputs, E4 operated. Nothing below is above E2.

## The screens and epics, against the repository

| Item | Status | Where |
|---|---|---|
| K01 onboarding (country, entity, identifiers) | Exists; identifier rules per market pinned by tests | `onboarding/api/org_claims.py`, `organization/domain/kyb.py` (identifier rules, search, idempotent case). No market-pack-driven field validation yet |
| K02 registry lookup and matching | Exists | `organization/application/kyb.py`, `infrastructure/kyb_adapters.py` (exact identifier outranks name; an outage is UNAVAILABLE, never "not found") |
| K03 documents and authenticity | Done in rules and checks (trust list ships empty) | `organization/domain/proof.py` (type from first bytes, active-content refusal) and **new** `company_documents.py` (per-claim age rules). Not built: signed-registry-extract and QR authenticity checks |
| K04 ownership and control graph | Mostly | `organization/domain/ownership.py` (exact arithmetic, cycles, over 100%, unresolved chains, control by other means) plus voting, nominee, trust and arrangement, dated and disputed links (CR-109). No new screen |
| K05 representative and mandate | Part (service and API done) | registry roots and mandates exist (`roots.py`, `mandate`). **New** `kyb_grade.permitted_actions` ties a binding act to a verified person, a listed power and the second signatory. No mandate intake screen |
| K06 applicant status without false assurance | **Done** (company verification page: reasons and next steps in words) | claim status exists; **new** grade labels never say "verified company" for documents |
| K07 two-reviewer workbench | **Done** | `dual_review.py` wired into `kyb.review`; `organization.kyb.escalation.decide`; the operator ruling screen shows the position of a case after each ruling |
| K08 customer verification record | **Done** | `/admin/company-verification`: grade, reasons, allowed acts, history, mandates, appeals, signed summary |
| K09 market policy centre | **Done** | rules, store, six operator capabilities with a separation-of-duties rule, the operator routes and the operator console page "Company verification packs" |
| K10 audit, appeals, event-driven recheck | **Done** (appeals, recheck pass and events, webhooks, signed export) | evidence is signed, append-only and hashed; refresh exists. **New** `kyb_grade.event_invalidates`. No `kyb.*` events, no appeal flow |
| KYB-01 policy pack engine | **Done** | rules, store, operator route and screen (CR-109) |
| KYB-02 canonical entity schema | Exists | `kyb.py` |
| KYB-03 adapters (UK Companies House, GLEIF, gated INPI) | Exists in part | Companies House and GLEIF adapters; INPI is gated by credentials, not connected |
| KYB-04 provenance and outage semantics | Exists | signed evidence, source states |
| KYB-05 quarantined ingest | Exists; signed-extract and QR rules added | `proof_scan.py` and `proof.py` |
| KYB-06 dual-reviewer enforcement | **Done in service** | CR-109, proved on PostgreSQL |
| KYB-07 ownership graph validator | **Done** | see K04 |
| KYB-08 person link and mandate service | **Done in service and API** | mandate intake, second signatory, revocation (CR-109); no screen |
| KYB-09 grades and entitlements | **Done** | stored grade, read live, and applied to binding protected actions through a gate that is off by default (`ZID_KYB_GATE`) |
| KYB-10 retention and erasure | **Done** | vault holds and erasure exist; `retention.py` is the honest answer; the published pack's days now set proof retention (purpose and risk tier PROPOSED in `market-defaults.yaml`) |
| KYB-11 screening handoff | Exists | `screening` module |
| KYB-12 licensed vendor adapter | Open | procurement |
| KYB-13 customer report, appeal, recheck, notifications | **Done** | appeals, recheck pass, webhooks, an in-app alert on an appeal outcome |
| KYB-14 tamper-evident manifest and exports | **Done** | signed, watermarked summary |
| KYB-15 load, outage and failover suite | Outage and concurrency done; sustained load and regional failover not run | every register failure mode has a test and eight parallel assessments are safe; no deployed load or failover exercise |

## The acceptance tests, T01 to T40

Covered by a test in this repository: T08, T09 (document age), T10, T11 (two reviewers, escalation), T12 (policy approvals and digest), T13, T14, T15 (grade labels and entitlements), T21, T22 (dissolution and revoked mandate in the grade), T23 (retention required to activate), T25 (quarantine proves nothing), T33 (events that force a recheck), T39 (publish needs tests, sign-offs and a signature of the exact digest), and the registry outage and exact-match rules of T01, T02, T04, T34 from the existing company verification work. T17, T18 (indirect ownership arithmetic, cycles) are covered by the existing ownership tests.

**Not covered**: T03, T05, T06, T07, T16, T19, T20, T24, T26 to T32, T35 to T38, T40. They need either something not built (webhooks, the stored grade, retention jobs, erasure, regional gates, customer reports, the screens) or real inputs (forged files, a QR mirror, a population for the fairness test, a screen reader run).

## What only people can close

Signed policy decisions D-01 to D-10, each market's lawful source use and credentials (Companies House key, INPI account), retention and erasure schedules, who holds the escalation permission, a trained reviewer population, and the independent run of the acceptance tests with logs.

## Next, in order

1. People load the issuer trust list (keys and exact hosts from each issuer's own publication) and approve the first market packs through the operator screen.
2. The owners confirm the purpose and risk tier in `market-defaults.yaml`.
3. A sustained-load and regional-failover run against a deployed cell.
4. The independent acceptance run with logs, and the sign-offs D-01 to D-10.
