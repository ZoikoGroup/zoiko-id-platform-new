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

## Closing pass (2026-11-12, own processes only)

This section supersedes the rows above where they disagree. Each item names its test.

| Item | State | Evidence and honest limits |
|---|---|---|
| PR-16 model and vendor lifecycle | BUILT | `privacy/domain/models.py`, `application/model_governance.py`, migration `privacy_0004`, routes `POST /v2/privacy/models`, `/model-evaluations`, `GET .../models/{ref}/versions/{v}`, `:promote`, `:rollback`, `POST .../models/{ref}:observe`. A protected characteristic or proxy is refused as a live feature (AT-017); a version is promoted only on an independent, comparative, accessible, current evaluation with the document's measures and a confidence interval each (AT-015); the version in service is pinned and any other reported version is DRIFT, which holds the model and blocks a release naming it (AT-016); a rollback needs its own evidence. A DPIA package's release state includes the model. `test_model_governance.py`, `test_model_governance_pg.py`. **Not done:** the evaluations themselves (cohort data and an independent evaluator); no threshold figure is invented |
| PR-05 key and media security | BUILT for uploaded evidence | Migration `evidence_0009`: every new upload is AES-256-GCM under a fresh data key wrapped by the tenant's key through the evidence runtime, fails closed without a key, and erasing an upload erases the wrapped key. `test_upload_envelope_pg.py`. Rows written earlier stay readable (`content_encrypted` false). **Not done:** no biometric media is kept, and there is no HSM/KMS proof in this environment (development runtime) |
| PR-14 fraud graph constraints | GUARD ONLY | No graph exists. `test_no_cross_brand_graph.py` fails if any link-like table is not tenant-scoped or a migration builds an unscoped table joining two subjects (AT-018 as a guard, not as a staged attack) |
| `/v2/verification-sessions` create, consent-records, cancel, subject-status (section 07) | BUILT | `privacy/application/sessions.py`, migration `privacy_0005`. Capture is enabled only while the purpose decision, the exact notice acknowledgment and the live consent or lawful-basis check all hold, and is recomputed on every read, so a withdrawal stops capture at once (AT-002, AT-003). A chosen alternative is never a failure and never called fraud (AT-012). Consent is the person's own and only for a consent-based activity. `test_v2_privacy_facades_pg.py`. No image, template or score is stored or returned |
| `/v2/privacy/requests` with status, proof-of-authority, review-decisions, processor-tasks, export (section 11) | BUILT over the /v1 rules | `privacy/api/v2_rights.py`. The actor is the authenticated caller; a representative's request waits for proof (AT-021); neither requester nor subject decides; a refusal names its evidence; the export is the person's own with step-up. **Not exercised end to end here:** a positive decision by a third reviewer and the package download (covered by the /v1 tests) |
| `/v2/privacy/consent-records`, `retention-policies`, `retention-evaluations` | BUILT | `v2_rights.py`, `retention_v2.py`. An evaluation reports HELD, DUE or RETAIN and changes nothing (AT-024, AT-027) |
| `/v2/privacy/holds` and `.../release` | BUILT as the one hold model | `evidence/api/v2_holds.py` delegates to `/v1/evidence/legal-holds`, so the second administrator's approval of exactly this scope, the scope rules and the audit are enforced in one place. The positive path (a granted approval request) is covered by the evidence tests; the /v2 test proves the approval is required |
| Acceptance links | 29 of 50 | PRI4-AT-001, 002, 003, 004, 006, 007, 008, 009, 011, 012, 015, 016, 017, 018, 020, 021, 022, 023, 024, 025, 026, 027, 031, 038, 039, 040, 041, 048, 049 in `docs/traceability/acceptance-tests.csv` (plus PRI4-PR05) |

### Added later the same day

AT-005 (identifier in no log, event or stored result), AT-028 and AT-029 (no published route returns raw media or a template, enforced as a contract guard), AT-030 (an independent override with a rationale and a new `privacy.review.decided` notification event that carries the outcome and never the rationale) and AT-045 (a close name alone is only a possible match) are linked, plus a positive decision on a rights request by a reviewer who is neither requester nor subject. That makes 34 of 50 acceptance tests linked.

### Mechanisms built around what only others can supply (2026-11-12)

These make the platform behave correctly around counsel's rules and a provider's bands. They do NOT supply the rules, the engine or the measurements, and each acceptance row says "MECHANISM ONLY" where that applies.

| Item | What was built | What it does not claim |
|---|---|---|
| AT-032, AT-033, AT-036 automated-decision guards | `privacy/domain/adm.py`, `config/privacy/adm-rules.yaml` (every pack `COUNSEL`), `POST /v2/privacy/adm-evaluations`. With no approved, in-force rule set a significant solely automated decision is BLOCKED and routed to a person; with one, every safeguard counsel names must be offered and a special-category decision needs the recorded condition | It writes no legal rule. The UK, EU and India rule content is counsel's; tests use fixture rules |
| AT-010, AT-012 to AT-014 outcome routing | `privacy/domain/outcome_routing.py`, `POST /v2/privacy/outcome-routing`: a match only continues to the next separate check, a doubtful PAD band or injected feed goes to a person, a high-impact decision is held for a person, a non-match or poor capture is a retry or alternative with no penalty. No route reaches approval or refusal | No PAD engine, no face match and no measured error rates exist here; the bands come from a provider |
| AT-042, AT-043, AT-044, AT-046, AT-050 | Linked to the tests that already prove them or to new ones (`test_doc04_controls_pg.py`): listing authority is never granted by identity, England's Right to Rent route needs a registered provider, metadata is stripped, an undeclared purpose is denied, a kill switch closes a provider path and a model while every record stays | AT-050 has no region or traffic switch; there is no deployed canary |

| AT-047 marked export | `privacy/domain/export_mark.py`, `POST /v2/privacy/exports` and `exports:verify`: only the claims named, raw biometric and document claims refused by name, signed under the evidence key with its own type, recipient and purpose named, lifetime capped at seven days | The caller supplies the source claims; nothing is stored. It is a watermark and signature, not a legal basis to disclose |
| AT-034, AT-035, AT-037 collection requirements | `privacy/domain/collection.py`, `config/privacy/collection-rules.yaml` (every field `COUNSEL`), enforced inside the purpose decision for sensitive data under a pack counsel has an entry for: each requirement counsel switches on (written notice, release, retention schedule, necessity assessment) is demanded by name | It writes no Illinois, California or Australian rule, and no such packs exist yet. Rows say MECHANISM ONLY |
| Owner pack | `docs/signoff/privacy-biometric-owner-pack.md`: each gate's signer, what in the code they can inspect, what is theirs to supply; DPIA, penetration-test, red-team and breach-drill outlines | It is not a DPIA, a test result or a sign-off |

That makes 49 of 50 acceptance tests linked, 7 of them mechanism-only (AT-010, 032, 033, 034, 035, 036, 037; the go-live board counts them from the traceability file). AT-019 is added below, making 50 of 50 linked and 8 mechanism-only.

| AT-019 correction of a link | `screening.signal.corrected` (proposed event) is emitted when a person clears a signal raised earlier, naming `screening.signal.raised` as what it supersedes and the action required (recompute what relied on it); confirming or escalating a risk emits nothing; ids and codes only. `test_signal_correction_pg.py` | **No linked-identity graph exists and none was built.** This corrects the signals the platform raises (repeated sign-ups from one hashed key). A consumer must act on the event; none is wired to recompute a decision automatically |
| Counsel worksheet | `tools/scripts/counsel_worksheet.py` writes `docs/signoff/counsel-worksheet.md`: all 38 open `COUNSEL` fields with the question and what each unblocks; a unit test fails if a new COUNSEL field has no question or the file is stale | It fills nothing in |
| Go-live board | `tools/scripts/go_live_board.py` now has a privacy section: the eight gates and signers, open counsel fields, acceptance linkage. Per-market gate state stays in the DPIA package | It signs and decides nothing |

### Still not done, and why

| Item | Why |
|---|---|
| The counsel content itself (every `COUNSEL` field in the packs, ADM, collection and breach-clock files) | A legal statement for each market, not code |
| A real PAD engine and its ISO/IEC 30107-3 assessment; the independent fairness evaluation with cohort data | Third parties and measured data; the records and routing are ready to receive them |
| Controller DPIAs, the G0 to G7 signatures, the penetration test, the red team and a staged breach drill | People and sign-off, outlined in the owner pack |


## Evaluation tooling (2026-11-13)

`privacy/domain/trial_metrics.py` and `tools/scripts/trial_report.py` turn an independent evaluator's RAW trials into the measures the model gate (PR-16, AT-015) reads: each measure as `{value, ci_low, ci_high}` with the counts behind it, exact binomial (Clopper-Pearson) intervals checked against textbook values, APCER per presentation-attack species with the worst as the headline (never averaged), BPCER, failure to acquire reported on its own, FMR and FNMR for face matching, and per-cohort rates and pairwise differences for a fairness review (PR-15). A small sample is never reported as "no difference". `test_trial_metrics.py` (24).

This is the CALCULATOR only. It defines no threshold, makes no verdict and cannot make an evaluation independent. PR-06 (the PAD engine), PR-15 (cohort data and an independent evaluator) and the DPIA and gate sign-offs still need a vendor or engine, real consented cohorts, an independent evaluator and named signatories.
