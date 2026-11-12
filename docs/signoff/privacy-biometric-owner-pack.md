# Privacy and biometric release: owner pack (ZID-PRI-004)

Status: **PENDING. Nothing in this file is a signed DPIA, a legal opinion, a penetration-test result or a certification.** It lists what each accountable
person must do, and for each item the code and tests that give them something to inspect. Document 04 section 23: "a document spec is not evidence of
compliance." The release authority stays BLOCKED UNTIL EVIDENCE for every market, service and model.

## G0 to G7: who signs, what they can inspect, what is theirs to supply

| Gate | Signer | In the code today (inspect, do not trust) | Theirs to supply |
|---|---|---|---|
| G0 Scope | Product and data owner | DPIA package per market, product and version (`POST /v2/privacy/dpia-packages`), data map, purpose and activity registry | The real country, controller, purpose, vendor and version for each launch |
| G1 Legal privacy | Legal, Privacy, DPO | Jurisdiction packs (`config/jurisdiction-packs`), notices, processor approvals, transfer decisions, `config/privacy/adm-rules.yaml`, `collection-rules.yaml`, `breach-clocks.yaml` (every counsel field reads `COUNSEL`) | Counsel's rules, approval references and effective dates, replacing every `COUNSEL`; lawful basis and special-category condition per market |
| G2 DPIA | Controller with DPO advice | Risk register with the document's L x I scoring, residual-risk blocker, release gate (`GET /v2/privacy/release-gates/...`) | The factual processing map, balancing, residual risk and any regulator consultation |
| G3 Biometric security | CISO | Per-upload envelope encryption (`evidence_0009`), tenant isolation tests, no raw-media route (contract guard), log and event leak test (AT-005) | Penetration test, PAD and injection testing, key-compromise and rollback exercise, backup and erasure test (below) |
| G4 Quality and fairness | ML lead and independent QA | Model inventory, independent-evaluation requirement, drift hold, rollback (`/v2/privacy/models`), outcome routing | The evaluations themselves: cohort data, FMR/FNMR, APCER/BPCER with intervals, accessible-alternative evidence, from an independent evaluator |
| G5 Supplier | Vendor manager and Legal | Provider entitlements and routing, deletion receipts with signed callbacks, subprocessor change blocking | Contracts, DPAs, audit rights, subprocessor lists, country and product scope per provider |
| G6 Ecosystem | Zoikorum and Rooms product owners | Negative tests: no listing authority from identity, no licence from identity, consumer contract tests | Their own acceptance of the contracts in `contracts/consumers/` |
| G7 Launch | CTO and release manager | Provider and model kill switch (AT-050), SLO probe | Canary, alert thresholds, on-call, a kill-switch exercise in a deployed environment |

## DPIA: what to complete per controller, product and region

Section 02 stages, each with the record that holds it: context (DPIA package header), processing map (data map, `GET /v1/privacy/admin/data-map`),
lawfulness (packs and notices), necessity (alternatives are offered: `GET /v2/verification-sessions/{id}/alternatives`), threats (risk register, R01 to
R12 are INITIAL proposals from the document), controls (named owners per risk), residual risk, decision (`POST .../dpia-packages/{id}/decisions`, the author
cannot decide). Scores in the register are not measured residual risk until a controller reassesses them.

## Penetration test: suggested scope

Every `/v2` route in `contracts/openapi/public/*.json`; cross-tenant object access (negative IDOR) on evidence, privacy requests, holds, models and
sessions; upload parsing (PDF, XML signature, image) and the scanner path; token and idempotency handling; webhook and deletion-receipt signature checks;
key custody and the evidence runtime; the admin consoles. Out of scope here: any provider's own systems. Acceptance: AT-006, AT-011, AT-025, AT-028, AT-029.

## Red team: suggested cases

Injected or replayed camera feeds and synthetic faces against whichever provider is used (AT-010; the platform only routes the provider's bands); session
hijack of a verification session; consent withdrawn mid-session (AT-003); a reviewer trying to reach raw media (AT-028, AT-029); an attempt to build a
cross-tenant link (AT-018); a forged provider deletion receipt (AT-025).

## Breach drill: runbook outline

1. Open a case: `POST /v2/privacy/breaches` with severity, jurisdictions and the detection time.
2. Read the clocks from `GET /v2/privacy/breaches/{id}`: a jurisdiction counsel has not set reports NOT_SET_BY_COUNSEL, never a guessed deadline.
3. Record each notification (`.../notifications`); the case cannot be closed while one is outstanding.
4. Exercise the provider kill switch (revoke the entitlement, hold the model) and confirm nothing is deleted (AT-050).
5. Close, then keep the drill record as the evidence for AT-049. The staged part, with real people and real clocks, is the drill itself and cannot be run by code.

## What stays blocked until a person acts

Every `COUNSEL` field in the three config files above; every gate sign-off; the independent evaluation; the PAD assessment; the penetration test; the
red team; the staged drill. The platform defaults to deny or route-to-a-person for each of them until the evidence exists.
