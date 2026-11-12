# Document 04 (ZID-PRI-004 v2.0): privacy, biometric security and DPIA, mapped to the code

Status vocabulary as in the document: VERIFIED_IN_CODE needs a code version, a test artifact, deployed configuration, independent verification and an
owner; here a test artifact exists for each item marked BUILT and the rest is not claimed. Nothing in this file is a signed DPIA, a legal opinion or a
certification. Prepared 2026-11-11 from the code, not from the design documents.

## Work packages

| WP | State | What exists | What is not done |
|---|---|---|---|
| PR-01 processing registry | BUILT (v1) | purposes, activities, role assignments, data map, release check | per-country and per-provider reconciliation is a data task |
| PR-02 purpose and lawful-basis engine | BUILT | `POST /v2/privacy/purpose-decisions` and `/authorization-evaluations`: default deny, named reasons, append-only record, policy version | counsel-approved pack content (the packs are drafts) |
| PR-03 notices and alternatives | BUILT for the API | notice acknowledgments per session and exact version, `GET .../alternatives` (non-biometric routes from config) | this app has no camera step: capture is in the identity providers' screens, so no capture-side UI exists to build |
| PR-04 regional consent and release | BUILT (v1) | consent receipts, withdrawal, jurisdiction packs | BIPA-style written release states are a pack decision for counsel |
| PR-05 biometric key and media security | NOT APPLICABLE TO US TODAY | we store no biometric media; log redaction exists | evidence uploads are not envelope-encrypted per tenant: needed before any selfie is ever kept |
| PR-06 PAD and injection defence | NOT BUILDABLE HERE | none | needs a PAD engine or provider and an ISO/IEC 30107-3 assessment |
| PR-07 provider data minimization | BUILT | provider controls per service and country; transfer decisions | real provider contracts |
| PR-08 lineage and evidence receipts | BUILT (v1) | evidence ledger and receipts | |
| PR-09 individual rights workbench | BUILT (v1) | rights requests, verification, fulfilment, package | a person-facing screen for the /v2 routes |
| PR-10 review, appeal and redress | BUILT | `POST /v2/decisions/{id}/appeals` and `/human-reviews`, reviewer decisions with rationale, never by the requester | a reviewer console screen |
| PR-11 retention engine | BUILT (v1) | policies, sweeps, holds | the real retention values are the privacy officer's |
| PR-12 provider and backup erasure | BUILT | erasure jobs, per-target tasks, proof that never claims completion early, Ed25519-signed provider deletion receipts, operator attestation for backups | no provider sends receipts yet; a held task needs its hold released outside this API |
| PR-13 cross-border egress | BUILT | transfer decisions (processor status, data category, location, remote-access location, mechanism, pending subprocessor change) | a transfer impact assessment is a legal document, not code |
| PR-14 fraud graph constraints | NOT STARTED | no fraud graph exists, so there is nothing to constrain | a guard test if a graph is ever added |
| PR-15 face and PAD fairness | NOT BUILDABLE HERE | name-matching fairness tests only | cohort data and an independent evaluator |
| PR-16 model and vendor lifecycle | NOT STARTED | `dpia_package.model_version` is recorded | a model inventory with drift and rollback |
| PR-17 DPIA governance | BUILT | DPIA packages, risk register with the document's scoring, G0 to G7 sign-offs, release gate, decisions | the controller's real assessments and signatures |
| PR-18 incident and breach response | BUILT for clocks | breach cases with per-jurisdiction clocks from counsel's config, notification records, no quiet closure | `config/privacy/breach-clocks.yaml` ships with every deadline COUNSEL; no regulator submission |
| PR-19 retention and compliance reporting | BUILT | `GET /v2/privacy/kpis` measured from the records, with what it does not measure | the false-rejection cohort and fraud measures need data we do not hold |
| PR-20 market release gates | BUILT | `GET /v2/privacy/release-gates/{market}/{product}/{version}` names every blocker | the approvals |

## Acceptance tests AT-001 to AT-050

Mapped to a real test: AT-001, 004, 007 (decision only), 023, 025, 026, 031, 038, 039, 048 and 049 (a clock, not a staged drill) in
`docs/traceability/acceptance-tests.csv` as PRI4-AT-nnn. Partly covered by earlier tests and not yet mapped one by one: AT-005 (log redaction), AT-006
(tenant isolation), AT-011 (replay), AT-020 to AT-022 (rights), AT-024 (hold), AT-027 (retention), AT-040 and AT-041 (credential eligibility), AT-045
(name matching). Not coverable without a biometric engine or live providers: AT-010, 012 to 016, 017 to 019, 034 to 037, 042 to 044, 046, 047 and 050.

## What only people can do

Controller DPIAs per market, counsel's jurisdiction content and deadlines, the G0 to G7 approvals by CISO, DPO, legal and product, a PAD red team, a
penetration test and any certification.
