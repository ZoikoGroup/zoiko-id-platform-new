# CR-074 (design proposal): the independent assurance plane for ZID-UAT-005 (Document 05 v2.0)

Status: **design for approval. Built 2026-11-12: S0 (the 104-case register, `tools/scripts/uat_register.py`, report, board section) and S1 (the pure rules in `assurance/domain/rules.py`, 30 tests). Everything else here is not built (S2 onward needs decisions D1 to D6).** Written 2026-11-12 from Document 05 and from what the repository already holds. Document 05 is itself a design: every case it lists
is NOT_RUN until independent evidence is attached, and this CR keeps that rule at the centre. The plane makes cases runnable, records evidence, and decides release **from evidence only**.
It can never make a case PASS by itself, and a passing developer test is not a UAT PASS.

---

## 0. Principles (each becomes a test)

1. **No evidence, no green.** A case with no independent run, a missing manifest or a missing trace link is NOT_RUN or INCONCLUSIVE, and a release containing one is NO_GO. (Doc 05 P0 assertions on WF-01, WF-09, WF-10.)
2. **Developer evidence is not independent evidence.** The register shows two separate columns: "developer test passes" (from the existing evidence index) and "independent verdict" (only from an assurance run). Only the second counts toward a gate.
3. **Separation by role, enforced by the server.** Code can enforce that the engineering owner, fixture author, tester and approver are different principals holding different roles. It cannot prove real-world independence; that is a signed statement by the QA lead and stays a person's claim.
4. **Immutable and append-only.** Run events, manifests, fixture revisions and signatures are never edited. Changing a golden outcome creates a new fixture revision and never rewrites a historic FAIL.
5. **Extend, do not duplicate.** Reuse the evidence ledger and its signing key, the release register and go-live board, the Document 04 privacy release gates, the provider entitlement registry and kill switches, country flags, webhooks and the existing consoles. No parallel dashboard.
6. **Scope is the release unit.** Everything is keyed by entity + service + country/subdivision + profession/purpose + provider exact-service + build and policy version. No cross-market roll-up, no global default.

## 1. What already exists (reused, not rebuilt)

| Need in Document 05 | Existing piece |
|---|---|
| Signed, hash-chained evidence, trusted time, replay | `evidence` module: ledger, receipts, checkpoints, `EVIDENCE_SIGNING` key, `replay` |
| Requirement-to-test traceability | `docs/traceability/{requirements,acceptance-tests,evidence-results}.csv`, `tools/scripts/evidence.py` |
| Release register with NO-GO default, signatories, evidence files with checksums | `deploy/release/ratification.json`, `ratification.py`, `go_live_board.py` |
| Per-market privacy gates G0 to G7 (Document 04's gates) | `privacy` DPIA packages, `GET /v2/privacy/release-gates/...` |
| Per-country capability flags and approvals | `config/country-capabilities`, board |
| Provider entitlements, routing, kill switch, drift hold | `verify.provider_entitlement`, `/v2/providers/*`, model governance |
| Webhooks and event catalogue | `webhook` module, `contracts/asyncapi` |
| Counsel-blank pattern (fail closed until a person fills it) | `COUNSEL` config files, `counsel_worksheet.py` |

Document 05's G0 to G7 are **different gates** from Document 04's. They are named `UAT-G0..G7` in code to avoid confusion; the Document 04 gate state is read as an input to `UAT-G3`.

## 2. Module and storage

New module `assurance` (`backend/app/src/zid_platform/modules/assurance/{api,application,domain,infrastructure}`, `ports.py`). Cross-module reads go through other modules' `ports.py` only, and the module is checked by the existing architecture, cycle, ownership and domain-purity guards.

Tenancy decision (open, section 9, D1): the proposal is that all assurance rows belong to **the operating-entity tenant** that runs the release, with row-level security like every cell table. Synthetic test tenants appear as data inside fixtures, never as the owner of a row. This keeps one isolation rule and means a release board for entity A can never read entity B's runs.

Tables (schema `assure`, all cell role, append-only triggers where noted; each needs a `table-ownership.yaml` entry):

| Table | Purpose | Notes |
|---|---|---|
| `requirement_baseline` | A signed, versioned import of the requirement registers (DTV, GID, PR, INT, UAT cases) | Immutable per version; hash of the source |
| `uat_case` | The 104 cases: id, suite, risk (P0/P1/P2), input, assertion, proof needed, requirement links, oracle version, dependency tag | Versioned; a case is never deleted |
| `fixture` and `fixture_revision` | Synthetic fixtures: content hash, status DRAFT/APPROVED/REJECTED, author, reviewer, rationale | Reviewer must differ from author; revisions append-only; approved content is locked by hash |
| `test_run` | One fixed run: release unit, environment id, build sha, config digest, test-set hash, fixture hashes, tester principal and role | Inputs frozen at creation; a mismatch is refused |
| `test_event` | Signed raw-safe events of a run | Append-only, monotonic sequence, hash chain, idempotency key |
| `evidence_manifest` | The TestEvidenceManifest of Document 06.1: digests, refs, tester, oracle version, result, signature | Append-only; one per (run, case); signed under the evidence key |
| `case_result` | The current verdict per (case, release unit) and what it rests on | Derived, never typed in; EXPIRED is computed (section 4) |
| `defect` | Severity (BLOCKER/CRITICAL/MAJOR/MINOR), owner, scope, status | A FAIL requires a defect link |
| `waiver` | Non-P0 only, with approver, reason and expiry | Cannot cover a P0 (database check as well as code) |
| `release_candidate` | A release request for one release unit, built from evidence-backed state | Snapshot of the case results it was created from |
| `release_signature` | One signature per role per candidate: role, principal, decision, rationale, policy version | Distinct roles and principals enforced; append-only |
| `release_flag` | Per-scope enabled/disabled with thresholds | Changes are audited events |
| `rollback_event` | Scoped disable, reason, consumers notified | Append-only; never deletes evidence |

No biometric media, document, name or claim value is stored; manifests hold digests and references (Document 05: "no unfiltered biometric export").

## 3. Pure domain rules (`assurance/domain`, standard library only, each with unit tests)

- **Status machine.** NOT_RUN, BLOCKED, PASS, FAIL, INCONCLUSIVE, EXPIRED, WAIVED with the allowed moves. PASS needs a complete manifest; a screenshot-only or manual pass with no manifest is INCONCLUSIVE (WF-09 assertion).
- **Aging.** A PASS becomes EXPIRED when any of its pinned inputs changes: build, policy pack, model version, supplier or certificate status, fixture revision, or trust-list snapshot. The rule takes the run's pinned digests and the current ones.
- **Waiver.** Non-P0 only, expiring, never a hidden PASS; a waived case counts as WAIVED in every report.
- **Release gates `UAT-G0..G7`** exactly as Document 05 section 05, as functions over case results:
  - G0: every applicable P0 case has an independent PASS; none FAIL/BLOCKED/INCONCLUSIVE/EXPIRED; no P0 waived; every requirement link resolves to live code or a valid NOT_APPLICABLE rationale.
  - G1: official-source legal and service status present and in date for the release unit (input from the registry snapshot).
  - G2: no known critical false PASS (identity, qualification, practice, property, Right to Rent, signature).
  - G3: no open critical or high issue; reads the Document 04 release gate for the same market.
  - G4: interoperability and event replay cases pass where the service is enabled.
  - G5 and G6: accessibility, fairness, load, recovery, outage, rollback and incident drills PASS against **thresholds that a signed SLO and assurance policy supplies before execution**. The code never defaults a number.
  - G7: five-party signatures (QA lead, CTO, Security/GRC, Privacy, country legal as applicable), a feature flag and a named post-launch owner.
- **No-go conditions.** The six mandatory conditions of section 05.2 as predicates over the candidate; any true predicate forces NO_GO for that scope only (OPS-01).
- **Separation of duties.** One principal cannot hold two release roles on a candidate; fixture author is not reviewer; tester is not in the commit-author set of the build under test; the engineering owner cannot sign QA. A CEO, product owner or operator role has no route that skips a failed gate.
- **Preview.** `release preview` is computed from the above and returns NO_GO with the exact missing artifact; there is no state in which missing evidence renders as ready.

## 4. Routes (all `/v2/assurance`, RFC 9457 problems, idempotency where creating)

| Route | Behaviour |
|---|---|
| `GET /requirements` | Filtered by tenant, role and market; package ids, status, signed baseline version |
| `POST /test-runs` | Freeze release unit, environment, test set, build, config and fixture digests; refuse mutable or unmatched inputs |
| `POST /test-runs/{id}/events` | Append signed raw-safe events: monotonic order, idempotent, rate limited |
| `POST /fixtures/{id}/review` | Second-person approval or rejection with rationale; version bump; prior revision referenced |
| `GET /test-runs/{id}/evidence` | The manifest with scoped signed references; no raw biometric export; redaction by audience (auditor, regulator, customer) |
| `POST /evidence:verify` | Third-party verification of a manifest bundle: signature, digests, tamper detection; a mismatch locks the release and raises an alert |
| `POST /release-candidates` | Build a candidate for one release unit from evidence-backed state |
| `POST /releases/{id}/decisions` | One role's signed decision; server enforces role, policy and that no P0 bypass exists |
| `POST /releases/{id}/rollback` | Disable only the affected scope, notify relying parties by event, keep all evidence |

New catalogued actions are needed (read, run manage, fixture review, release decide, rollback) and must be added to the action catalogue and to the owner packs; the route-map guard fails otherwise.

Signing: manifests and decisions use the evidence runtime signer as compact JWS with explicit types (`zid-uat-manifest+jwt`, `zid-uat-decision+jwt`), verified offline against the published evidence keys, like the other signed artifacts.

Events (proposed, each added to the catalogue and to the consumer contracts): `assurance.case.expired`, `assurance.release.decided`, `assurance.release.rolled_back`, `assurance.evidence.tampered`.

## 5. The case register (the first thing to build)

`docs/traceability/uat-cases.csv`, generated once from Document 05 section 04 and then maintained by the QA lead. Columns: case id, suite, requirement ids, risk, input, assertion, proof needed, **dependency** (OWN, THIRD_PARTY, PEOPLE), **developer evidence** (the existing test that exercises the scenario, or blank), **independent status** (default NOT_RUN).

- A case links to a developer test only where the test truly exercises that scenario; the link is reviewed, not guessed. Cases that need a provider, registry, engine or independent party are BLOCKED with the missing input named.
- `tools/scripts/uat_register.py --check` fails when a case is added, removed or renumbered without the register changing, when a developer link points at a test that no longer exists, or when any row says PASS without a manifest id.
- It feeds the go-live board (counts by suite and status) and the `requirements` route.
- Risk assignment is a decision (D2): the proposal is that every case Document 05 marks as a release-blocking negative assertion, or whose failure matches a stop-ship criterion, is P0, and the QA lead confirms the list.

## 6. Third-party seams (built as interfaces, shipped disabled)

Nothing here claims a provider or register exists. Each seam is a port with a disabled default and a fake used in tests:

| Seam | What the code provides | What a person or vendor supplies |
|---|---|---|
| Provider Lab (WF-04) | Fault mixer wrapping any provider adapter: 429/502, timeout, replay, duplicate, bad HMAC, wrong market, stale authorization; parity matrix over normalized claims; failover only when the exact regulated scope, purpose, privacy, quality and source scope are equivalent | Sandbox credentials and contracts per provider |
| Registry watcher | A watcher port with a source config (URL, cadence, parser) and the auto-disable path through the entitlement registry; drift raises `assurance.case.expired` for dependent PASS results | The official registers: UK digital verification register, Right to Rent supplier register, EU trusted lists, regulator registers |
| Conformance vectors | A vector loader with pinned digests and an offline mode; results become developer evidence until an independent run repeats them | The official vector sets: EU eSignature validation tests, ETSI, W3C VC, OpenID4VC, NIST |
| Evidence storage and time | A storage port (immutable bucket or object lock) and a trusted-time port, with local fakes | The deployment's WORM store and timestamp authority |
| Load, restore, canary | Test-run recording and threshold checks | A deployed environment, an approved SLO policy, an independent recovery witness |

## 7. Console (WF-01 to WF-10), on the existing consoles

| Screen | Where it lives | Server rule it shows |
|---|---|---|
| WF-01 Test Command Center | A new "Assurance" area of the admin console: scope selector, heat map of requirement x test x evidence, blocker queue, release preview | Missing link or evidence never renders green |
| WF-02 Test Author | Fixture list and review | Draft until a second person approves; hash shown |
| WF-03 Case Replay | Extends the existing evidence tab (replay, human review) | Suspended licence overrides a genuine degree without erasing it |
| WF-04 Provider Lab | Extends the provider entitlement view | Uncertified fallback never completes a regulated check as certified |
| WF-05 to WF-08 | Read views over Zoikorum, document-trust, Rooms and privacy results | Scoped claims only; Right to Rent from an unregistered service never shows compliant |
| WF-09 Evidence Builder | Manifest browser and bundle verifier | Screenshot-only is INCONCLUSIVE; tamper locks the release |
| WF-10 Release Board | Extends the go-live board and release register | NO_GO, CANARY, ROLLBACK; no bypass |

The go-live board and release register stay as repo-level views for the owners; the console reads the same facts through the API.

## 8. Build order

| Slice | Contents | Needs |
|---|---|---|
| S0 | `uat-cases.csv`, `uat_register.py`, reconciliation of existing tests, board counts, guard | Only code and a review of each link |
| S1 | Pure domain rules and their unit tests (status machine, aging, gates, no-go, separation, waiver) | Only code; thresholds read from a policy object, none defaulted |
| S2 | Migration, tables, ownership entries, append-only triggers; `test-runs`, events, manifests, `evidence:verify`, tamper lock | Code |
| S3 | Fixture builder and review; frozen golden outcomes | Code |
| S4 | Release candidates, decisions (five roles), rollback, flags, events, consumer notices | Code; role to principal mapping from you |
| S5 | `requirements` route, replay and redacted export | Code |
| S6 | Console screens WF-01 to WF-10 | Code |
| S7 | Provider Lab fault mixer, registry watcher seam, vector loader (all disabled by default) | Code, then vendors and registers |
| S8 | Independent execution of the 104 cases and the sign-offs | People and third parties only |

Each slice ships with its tests, traceability rows, matrix lines and the existing guards green. S0 to S5 are verifiable here; S6 needs a browser run; S7 can only be tested with fakes; S8 is not code.

## 9. Decisions needed before S2

- **D1 Tenancy of assurance data:** operating-entity tenant (proposed) or a separate operator plane.
- **D2 P0 list:** confirm which of the 104 cases are P0, starting from the document's release-blocking assertions.
- **D3 Identities:** which principals hold the roles QA lead, tester, Security/GRC, Privacy, country legal, CTO and release authority, and how an "independent" tester is provisioned so they are not in the engineering team.
- **D4 Thresholds:** the signed SLO and assurance policy (availability, p50/p95/p99, error budgets, false-match limits) that G5 and G6 read. None will be invented.
- **D5 Where evidence lives:** the immutable store and timestamp authority for real runs.
- **D6 Register ownership:** who maintains `uat-cases.csv` after S0 (the QA lead, proposed), so engineering cannot edit its own oracle.

## 10. What this design does not claim

It does not make any case PASS, certify a country, approve a provider, replace independent QA, a penetration test, a WCAG audit with assistive technology, a bias study, load or restore exercises in a deployed environment, or any signature. It makes each of those recordable, checkable and impossible to skip.

## 11. Built so far

| Slice | What exists | Evidence |
|---|---|---|
| S0 | `docs/traceability/uat-cases.csv` (104 cases, every one NOT_RUN; 61 with a developer test, 35 needing a third party, 6 needing people; risks are PROPOSED until the QA lead confirms), `tools/scripts/uat_register.py --check/--report`, `docs/qa/uat-register-report.md`, and a section on the go-live board | `test_uat_register.py`; the check verifies every developer link resolves to a real test |
| S1 | `assurance/domain/rules.py`: status machine, manifest completeness (a screenshot-only pass is INCONCLUSIVE), expiry of a PASS when pinned inputs change, waivers (non-P0, independent approver, expiry), fixture review and locking, gates UAT-G0 to G7, the six no-go conditions, separation of duties, and the release decision. The module is registered in the plane map and capability baseline | `test_assurance_release_rules.py` (30 tests) |

Honest limits: the register's developer links are first-pass reconciliation by one engineer and are partial where noted; the 41 cases that need a third party or people cannot be settled by code; no storage, routes or screens exist yet.

### Added 2026-11-12 (the rest of what code can do)

| Item | What exists | Honest limits |
|---|---|---|
| S2 to S5 | Migration `assurance_0001` (12 `evidence.uat_*` tables, append-only triggers, row-level security), 7 actions, the `/v2/assurance` routes (roles, baselines and requirements, fixtures, runs, events, signed manifests, evidence view by audience, `evidence:verify`, defects, release candidates, decisions, rollback), 4 events | Decisions D1 (operating-entity tenant), D3 (roles are assigned principals) and D4 (thresholds from a signed policy, never defaulted) were taken as proposed |
| Pinned inputs | A run pins any of build, policy pack and version, model version, provider certification, trust-list snapshot, fixture hash and environment; a candidate supplies the current values; a changed pin expires the PASS and names the input | The pins are supplied by the caller; nothing here reads a live register |
| Risk confirmation | `POST /v2/assurance/baselines/risk-confirmations`: only the QA lead, into a new baseline version; the earlier baseline is untouched | Which cases are P0 is still the QA lead's decision, not made here |
| Registry watcher wired | `POST /v2/assurance/registry-watch`: two snapshots in, drift out, and the exact provider routes that drifted closed through the entitlement registry (REVOKED for removed or expired, SUSPENDED for suspended) | Nothing is fetched; `config/assurance/registry-sources.yaml` ships every source disabled |
| Consumer wiring | A rollback that names a provider and service closes that route in the same transaction; the four events are subscribable by webhook | No external consumer's own handling is claimed |
| ASiC | `evidence/application/asic.py`: ASiC-S and ASiC-E carrying CAdES, safe ZIP reading, manifest digests, unsigned-file detection; validation type only, never an upload type | XAdES inside an ASiC and ASiC time-stamp tokens are reported unsupported |
| Console | Assurance tab: coverage, release preview, provider lab, fixture authoring and review, case replay, evidence builder, release signing and rollback | Typechecked and tested at the helper level; not exercised in a browser |

Still not code: the independent execution of the 104 cases (about 41 need a provider, register, source, partner, audit or exercise), the penetration test and red team, the WCAG audit with assistive technology, the bias study, load and restore in a deployed environment, the signed SLO and assurance policy, and every sign-off.

### Added after that: what can be prepared for the parts only others can do

| Item | What exists | What it does not do |
|---|---|---|
| Signed SLO and assurance policy | `uat_policy` and `uat_policy_signature` (append-only), `/v2/assurance/policies`: proposed by the QA lead or the CTO, signed by the QA lead, the CTO and Security/GRC as three different people. A release candidate reads whether it is signed from the record; a caller's claim is ignored | It supplies no number: every threshold is a person's, in a fixed set of keys |
| Threshold judgement | Measurements on a manifest are checked against the signed policy for the release unit: a breach cannot be signed as PASS, a missing measurement is a breach, and with no signed policy nothing is judged | It does not take the measurements: a tester or the SLO probe does |
| Independent tester client | `tools/scripts/uat_client.py`: a results file becomes a frozen run, idempotent events and signed manifests; the token comes from the environment only | It records what the tester saw; the platform decides the verdict, and a thin PASS comes back INCONCLUSIVE |
| Independent execution pack | `docs/signoff/uat-independent-execution-pack.md`, generated from the register: for each independent party (QA, penetration test and red team, accessibility audit, fairness study, load and restore, redress exercise, provider conformance, official registers), what the platform gives them, what they must bring and what makes the case accepted. A check fails if a case that needs an outside party is in no brief | It is a brief, not a result. Every case remains NOT_RUN |

