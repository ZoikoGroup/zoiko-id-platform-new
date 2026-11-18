# WF01 (ZID-DTV-WF-01) coverage matrix: Global Identity Verification and Provider Orchestration

Prepared 2026-11-17 from `Zoiko_iD_Wireframe_01_...docx` and the repository; updated the same day after CR-107. A reconciliation, not an approval. Evidence level follows the register's scale (ZID-DTV-REG-001): E1 code referenced, E2 automated tests pass, E3 signed staging evidence, E4 production. **Nothing here is above E2.**

Rule from the register: WF01 is RETAINED as the single verification control plane. Its route names (`/v1/identity-proofing/...`) are illustrative, so no second router was created. The existing verification module is the implementation, and this table maps WF01 onto it.

## Route and state mapping (no duplicate API)

| WF01 | Existing implementation |
|---|---|
| `POST /v1/identity-proofing/sessions` | `POST /verifications`, `/provider/sessions`, `/v2/self-run/verifications` |
| `GET .../sessions/{id}` | `GET /verifications/{id}`; the WF01 result facts are `GET /verifications/{id}/proofing` (CR-107) |
| `POST .../review` | `POST /verification-review-cases/{case_id}/decisions` (`review_approvals` 1 to 3 distinct reviewers, `REVIEWER_IS_SUBJECT`, `REVIEW_DUPLICATE_REVIEWER`) |
| provider webhooks | `POST /verification-provider-callbacks/{adapter_ref}`: pinned keys, 600 s freshness, dedupe, forged/stale/conflict refused, recorded and alerted |
| policy router / O1 | `/verification-profiles`, `/v2/workflows` (author, simulate, approve, retire), `/v2/providers/route`, provider entitlements; engine mode `DISABLED` / `LAB_ONLY` / `APPROVED_FOR_SCOPE` with countries (CR-107) |
| O2 trust store | `/v2/trust-store/csca/...` and the console page `/admin/trust-store` (CR-107) |
| `proofing.completed.v1` / `proofing.indeterminate.v1` | `verification.proofing.completed` / `verification.proofing.indeterminate` (CR-107, proposed events) |
| relying-party minimized result | `GET /verifications/{id}/proofing`: facts and words only |
| usage and cost | `GET /verification-usage` (counts) with the vendor cost report in commercial (CR-029) |

WF01 states against the platform's `SessionStatus`: the proofing facts name them in WF01's words (CREATED, CAPTURE_IN_PROGRESS, PROVIDER_PENDING, RESUBMIT_REQUIRED, MANUAL_REVIEW, VERIFIED_FOR_SCOPE, REJECTED, INDETERMINATE, CANCELED, EXPIRED). `REVOKED` and `SUPERSEDED` are carried by assertion revocation and by new verifications, not by session states.

## Epics (WF01 section 18)

| Story | Status | Evidence or reason |
|---|---|---|
| WF01-001 canonical state and assertion model | BUILT (E2) | `verification/domain/proofing.py`: seven of the nine assertions derived from recorded facts; the two COMPANY_* assertions are WF02's. `test_proofing_facts.py` |
| WF01-002 provider registry, disabled by default | BUILT | adapters, quarantine, entitlements and impact routes |
| WF01-003 applicant A1-A5 with capture SDK | PARTIAL (E2 for the web part) | Web: start, status and alternatives were already on `/account/verification`; **A5 is now the evidence result** (`proofing-result.tsx`: what was checked, each check as passed, did not pass, could not be judged or not part of this check, what is held true, and a headline that reads as verified only for the platform's own decision). A3 and A4 appear there as status rows. **The chip-reading protocol is now ours** (CR-108: BAC, secure messaging and file reading, proved against the ICAO appendix D vectors and a simulated chip; no PACE; never run on a real passport). **The transport is built too**: a stateless HTTP relay (`/v2/self-run/verifications/{id}/chip-link`, CR-108: the protocol state travels in a sealed token, so any server process can take any step) so a device only carries card commands, with Active Authentication run by the server, limits on time and size, and a tested TypeScript device-side loop (`sdks/typescript/src/chip-relay.ts`). **PACE is built and proved against the standard** (ECDH and DH, generic and integrated mapping, AES-128/192/256, 3DES with the generic mapping, MRZ or CAN as the password: every value of the four worked examples and the ECDH exchange byte for byte); the AES secure channel after it has no printed vector. **Console screens now exist for the trust store, the supplier spend limit and usage, and the face engine readiness checklist (read only: licence readings, the lab evaluation and the DPIA stay records that their owners make in the versioned configuration). A selfie capture component exists (browser camera; offered only where an engine is approved for the person's country; its two captures go to the finish call, which the platform ignores unless an engine is approved); it has not been run in a browser with a camera. The Android and iOS bridge sources are written but have never been compiled. Still not built or not proved: the native bridge (reference source in `sdks/native-bridge/README.md`, never compiled or run), camera capture and a selfie screen, the chip-authentication mapping, and any test on a real passport or phone.** A browser cannot read a passport chip (Web NFC only reads NDEF tags), and a web selfie has nothing valid to bind to under our rules (only a chip-verified photo binds). Options are the wallet route, a provider hand-off or a thin wrapper app; none is chosen. |
| WF01-004 policy author/review/publish, version pinning | BUILT | `/v2/workflows` approve/retire; sessions pin `profile_id`; the facts and events carry profile id, version and a policy hash |
| WF01-005 reviewer C1, maker-checker | BUILT (E2) | distinct reviewers per profile, subject cannot review, a reviewer cannot make a missing claim appear; proofing facts agree (`test_verification_pg.py`). The earlier version of this matrix said otherwise: that was wrong. |
| WF01-006 signed callback adapter, reconciliation, outbox | BUILT | service.py callback path and tests |
| WF01-007 per-jurisdiction biometric purpose guard | BUILT | `privacy_gate`, country overlay, consent; the engine now also runs only in its approved countries |
| WF01-008 NFC conditional method, trust store O2, tests | BUILT for the software (E2); the reader itself is CR-108 | governed trust store with two-person approval, rollback and retire; chip routes use it. **No real list has been imported**: `config/trust/csca/` and the store ship empty. No phone app. |
| WF01-009 procurement scorecard | EXTERNAL | People and contracts |
| WF01-010 independent IDV/PAD/fairness evaluation | EXTERNAL (harness built) | `biometric_evaluation.py` computes bounds; the lab report is not code |
| WF01-011 OSS evaluation sandbox, licence/SBOM register | BUILT as a register (E2); **nobody has verified a licence** | `engine-register.yaml` lists each component with its licences and a verification date; an approved engine must name its components and all must be confirmed. The shipped register verifies nothing, so no engine can go live. No per-version dependency list yet (`sbom_ref` is null until a version is adopted). |
| WF01-012 hybrid routing analytics | BUILT (E2); no outage drill | every routing decision explains its refusals (tested), a failover never lowers assurance (tested), the usage report counts failovers |
| WF01-013 relying-party minimized verifiable result endpoint | BUILT (E2) | `GET /verifications/{id}/proofing` and the `completed` event: facts, scope, expiry; no evidence, value or score |
| WF01-014 usage budget, retry caps, cost reports | BUILT (E2); no company has set a budget | per-profile `max_attempts` (429), per-company monthly money budget (`/verification-budget`, 429 at the limit, own currency only, no default amount), `GET /verification-usage` counts, vendor cost report (CR-029). The over-the-limit path is tested with a zero limit and on the rule; no test seeds a real supplier cost. |

## Test vectors T01-T30

"Linked" means a test in the acceptance register (`docs/traceability/acceptance-tests.csv`, rows `WF01-*`) proves it. "External" needs a lab, an engine or a person, not code.

| Vector | Status |
|---|---|
| T01 no engine approved | Linked (WF01-T02) |
| T02 open-source engine marked lab-only | Linked (WF01-T02, WF01-011a/b) |
| T03 unsupported country/doc | Linked (WF01-T03) |
| T04 no NFC | Linked (WF01-T04) |
| T05 empty CSCA trust store | Linked (WF01-T05, WF01-FACTS-1) |
| T06 CSCA present but unapproved / list that does not verify | Linked (WF01-T06, WF01-O2-RULES) |
| T07 DSC not chained / invalid / revoked | Linked (WF01-T07a, WF01-T07b). Revocation is checked only when a CRL is configured; without one the answer says `REVOCATION_NOT_CHECKED` |
| T08 MRZ mismatch | Linked (WF01-T08) |
| T09, T10, T11, T27 screen replay, PAD, low light, lab demographics | External: needs an engine and a lab |
| T12 face match untested engine | Linked (WF01-T02, WF01-SCOPE, WF01-011a) |
| T13 two tenants same hash | Linked (WF01-T13) |
| T14 forged webhook | Linked (WF01-T14) |
| T15 replayed, reordered or late webhook | Linked (WF01-T15) |
| T16 supplier outage | Linked (WF01-T16, WF01-012) |
| T17 author self-approves / maker checks own case | Linked in part (WF01-O2-RULES for the trust store, WF01-FACTS-3 for reviewers); a profile author approving their own profile is not re-proved |
| T18 reviewer edits crypto result | Linked in part (WF01-FACTS-3): a reviewer cannot make a claim appear. No route lets anyone edit a chip result at all |
| T19, T20 company certificate / signatory | WF02 scope |
| T21 erasure with a legal hold | Linked (WF01-T21) |
| T22 retention or region pin | Linked in part (WF01-T22): erasure removes derived copies; a backup lifecycle proof is an operations record |
| T23 token copied to another tenant/purpose | Linked (WF01-T23) for purpose; the trust store and proofing reads are tenant-scoped (tested) |
| T24 policy changes during session | Linked (WF01-T24) |
| T25 assisted route equal assurance | Policy decision, not code |
| T26 trust-store rollback | Linked (WF01-T26) |
| T28 no DPIA, switch stays off | Covered by the gate: it needs a DPIA reference, an evaluation and, now, a verified licence register (WF01-T02, WF01-011a) |
| T29 no phone/email invented | WF05 scope |
| T30 raw media absent from logs | Linked (WF01-T30; the proofing view and events carry no evidence, tested in WF01-FACTS-1) |

## What a real launch still needs (not code)

- **A real list in the trust store.** Staging, approving and activating a country's master list needs a named custodian and an independent approver (decision D) and the countries' own anchor fingerprints.
- **Camera and NFC capture** (WF01-003): a native chip reader (a phone app, a provider's app, or a thin wrapper), or the wallet route. The server side for the app exists (`/v2/self-run/...`, the mobile contract); a browser cannot read the chip.
- **Someone to read each component's licence** and sign the register (WF01-011), and each company's budget and currency (WF01-014).
- **A provider contract or an independently evaluated engine** (WF01-009, WF01-010), the lab PAD and fairness results (T09 to T11, T27), the DPIA and the approved countries and thresholds in `self-run.yaml`.
- The owner decisions A to J in section 18.2 and the signed CR-107 table.
