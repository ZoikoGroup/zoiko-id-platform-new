# WF08 (Decision receipts and independently verifiable evidence): what already exists, what CR-170 added, what is left

Written 25 Nov 2026 for the Zoiko iD product owner. ZID-CP-WF08 v2.0 says to extend the existing verification and authority event estate and not to build a duplicate receipt or event engine. This is that reconciliation, from reading the code and running the suites. It makes no production, certification or legal claim.

**Headline.** The evidence module already is the "Evidence Receipt Service" the wireframe describes: pseudonymous decision receipts committed with the decision, a hash-chained append-only ledger, signed Merkle checkpoints, optional RFC 3161 timestamps checked against a trust list, signed export packages with chain of custody, replay with explicit reproducibility classes, signing-key compromise rules, retention that deletes bodies but keeps provable integrity, expiring bulk exports, a scheduled integrity checker, and an offline Python verifier (`python -m zid_evidence verify`). CR-170 fixes three real gaps: the verification result collapsed into one `ok`, there was no public way to check a proof you hold or to learn which keys to trust, and my own WF07 receipts were signed with the access-token key.

## Reconciliation by work package

| WF08 work package | Already in the repo (evidence) | Status | CR-170 / what is left |
|---|---|---|---|
| E01 canonical schema, event linkage | Receipt body, RFC 8785 canonicalization, `RECEIPT_SCHEMA_VERSION`, receipt per decision (`evidence/domain/receipts.py`, `libs/zid-evidence`) | Built | The receipt does not name its environment (see E04) |
| E02 snapshot and authority version resolver | Receipts reference the policy release and commitments to material inputs | Built (CR-170) | **A signed, append-only authority history is written in the same transaction as every allowed tool call**: grants, agent records, sponsor and mandate as they stood, each with source, version, issuer, when obtained, when in effect and an evidence digest. A missing, contradictory, not-yet-in-effect or later-reconstructed edge makes it INDETERMINATE. Only tool calls write one so far; other decision types have no producer |
| E03 issuer and idempotent outbox | Receipt commits in the decision's transaction; ledger append is idempotent per record (`test_worker_seals_and_offline_verifier_accepts_the_export`) | Built | None |
| E04 keys and issuer trust registry | Evidence signing key separate from token keys, historic keys kept, compromise rules (`test_signing_key_compromise_pg`) | Built | **CR-170 adds** a signed, versioned, expiring trust bundle (`GET /v1/trust/issuer`) listing each key's standing and compromise start, with rules that refuse a stale, substituted, rolled-back or other-environment copy. The receipt has no `environment` field; separation rests on environment-separate keys and the bundle |
| E05 independent proof verifier | Python offline verifier and CLI | **Partly** | **CR-170 adds a second verifier in TypeScript** (`sdks/typescript/src/evidence-verify.ts`, no runtime dependencies) that passes 32 shared conformance cases written by the Python one (valid bundles of four sizes, withheld record, tampered record, entry, proof path, root, range, signature, key, format and algorithm). The external design partner is not met |
| E06 privacy and disclosure tiers | Minimization on every body, purposes on exports, retention, legal holds, expiring bulk exports | Built (CR-170) | Three tiers (public proof, customer evidence, auditor dossier) with a named recipient, kind, contact, purpose, legal basis, retention and country; a dossier or any cross-border transfer needs a second person, kept by the database too; an anonymous caller can only ever be offered the public tier. The tenant's home country is read from `app.state.home_country`; **nothing sets it yet, so every private export reads as cross-border and needs a second person** (safe, but the owners must set it). Redaction is by withheld-field digests in the existing package builder; there is no separate redaction proof |
| E07 time-of-decision state UI | Evidence console: receipts, verification, replay, bulk exports, audit, holds, cases, provenance, keys | Built (CR-170) | Separate axes, plus tabs for authority at decision, forensic reconstruction, export tiers and requests, status list, trust inspector and the claims register |
| E08 receipt API and webhooks | `/v1/evidence/...` plus webhooks through the existing transport | Built | **CR-170** adds `POST /v1/receipts/verify` and `GET /v1/trust/issuer`. No second decision endpoint was added |
| E09 status and revocation freshness | Live, authenticated credential and agent status (CR-169) | Built (CR-170) | A signed Bitstring-Status-List-style list (131,072 bits minimum, random positions, one hour life, revoke and reinstate as recorded acts) with a public `GET /v1/status-lists/{id}` that takes no subject. Stale reads as UNKNOWN. **Nothing yet marks a slot when an agent is revoked in WF07**: an operator or caller must do it through the status routes |
| E10 append-only checkpoints and Merkle proof | Hash chain, signed checkpoints, inclusion proofs, scheduled re-verification (`test_ledger_tamper_is_detected`, `test_evidence_integrity_pg`) | Built | Witness / consistency proofs to an outside party are not offered |
| E11 optional trusted timestamp | RFC 3161 client, token verification against a trust list (`test_evidence_timestamp_pg`) | Built, optional | No timestamp authority contracted; no "qualified" label anywhere |
| E12 replay and counterfactual | Deterministic replay with MATCH, STRUCTURAL_ONLY, MISMATCH, INCOMPLETE_BY_LAWFUL_ERASURE, INTEGRITY_FAILURE | Built (CR-170) | `POST /v1/policy-releases/{id}:compare`: many scenarios under the draft and the active release, kept as a signed, append-only analysis that says it is analysis and never touches a receipt. Scenarios are supplied; past receipts hold input commitments, not inputs, so they cannot be re-run |
| E13 long-term preservation | Restore re-applies tombstones; retention keeps integrity (`test_restore_reapplies_tombstones`) | Built (CR-170) | Four executable drills and a runbook (`docs/runbooks/wf08-evidence-drills.md`); no production run yet |
| E14 external design partner | none | Not built | People |
| E15 claims and independent release review | none | **A claims register is below; nothing enforces it** | People and tooling |

## What CR-170 built

- **A result with no overall pass.** `verification_axes` separates signature integrity (VALID / INVALID / INDETERMINATE), issuer trust (TRUSTED / UNTRUSTED / UNKNOWN), proof completeness, time proof (an unchecked timestamp is UNVERIFIED), status at decision and status now with its freshness, execution status, and `real_world_claim_verification`, which is always NOT_ASSESSED. `success` is deliberately null. The authenticated verify route now returns `axes` beside its old `ok` (which means only that the integrity checks passed).
- **A public proof check** (`POST /v1/receipts/verify`): you hand it an exported evidence bundle; it checks it against the issuer's pinned keys, stores nothing, caps size, rate-limits, and answers a fabricated or unknown record exactly as it answers a tampered one, so it cannot be used to learn whether a receipt exists. A bundle's own keys are never trusted.
- **A signed trust bundle** (`GET /v1/trust/issuer`): signed with the evidence key; lists active, retired and compromised keys and from when; versioned (a rotation or compromise raises the version) and expiring (24 hours). It states that post-quantum or hybrid signing is not offered.
- **Key separation for the WF07 receipts.** Tool-action receipts and mandate confirmations were signed with the access-token key. They are now signed with the evidence key (`sign_evidence` / `verify_evidence` through the evidence ports), and a test shows the token keyring cannot verify them.
- **The console** shows the axes on the receipt verification instead of one green banner (green only when the receipt is intact and the signer is trusted), with each reason in words.

- **Second verifier** in TypeScript with 32 shared conformance cases.
- **Authority history**: `evidence.authority_snapshot`, written once per allowed tool call, signed with the evidence key, integrity re-checked on every read.
- **Status list**: `evidence.status_list`, `status_slot`, `status_change`, `status_publication`, and the public list route.
- **Export tiers**: `evidence.export_request` and routes to request, approve, refuse and issue; the issued manifest is signed and omits the recipient's contact.
- **What-if analysis**: `authz.policy_what_if` and the `:compare` route.
- **Console**: six new tabs on the evidence page (authority at decision, forensic reconstruction, export tiers, status list, trust inspector, claims register), a "keep as a signed comparison" button on the policy simulation, and a proxy entry for the trust bundle.
- **Drills** and a runbook.

## Found while doing this (not WF08 itself)

- **The web console's proxy blocked every `/v1/verify-api/...` call.** Its allowlist named no Verify API prefix, so the Verify API developer console and the case review screen could not reach the cell on a real stack, while every mocked test passed. Fixed: the console routes are now named one by one and pinned in `bff-allowlist.test.ts`; the partner-facing routes (token, whoami, hosted pages, OpenAPI) stay off the list and a test says so.
- **Fourteen WF07 routes had no route-map decision.** The route-map guard caught it. They are now mapped to existing role-matrix actions, or marked as protocol endpoints for the three that an agent or tool server calls with its own credential.
- **The wireframe says 60 QA cases and lists 48.** All 48 are in the acceptance register as `WF08-T-xx-xx` (31 with a real test, 17 not started).

## Claims register (what may be said, and what backs it)

| Claim | May we say it? | What backs it | What is missing |
|---|---|---|---|
| Receipts and ledger are tamper-evident | Yes, with its limits | Hash chain, signed Merkle checkpoints, scheduled re-verification, tamper tests | An independent review |
| A receipt can be checked offline | Yes, for the Python verifier | `python -m zid_evidence verify`; the public check | A second implementation |
| Independently verifiable by anyone | **No, not yet** | Two implementations that agree on shared vectors, written by the same team, no design partner | One external partner who verifies with their own code |
| A signed decision proves the decision was right | **No, never** | Every result says correctness is not assessed | n/a |
| A timestamp proves existence at a time | Only when an authority is contracted and its token verified | The checker exists | A contracted authority |
| Qualified, notarized, non-repudiable, court-admissible | **No** | none | Counsel-approved profile per jurisdiction |
| Post-quantum secure | **No** | The bundle says "not offered" | A designed, tested, approved suite |
| Revocation is reflected immediately everywhere | **No** | The gateway re-reads state on every call | Acknowledgement from other enforcement points |

## Not claimed

No certification, no external audit, no legal effect and no accuracy guarantee. A valid signature proves a record is intact and who signed it, and nothing about whether a match, an authority or a payment was right.

## Left to do

- **More producers of authority history.** Only tool calls write one. A mandate has no revision number, so its edge is version 0; a grant's end time is not recorded, so only live grants are listed.
- **Design-partner test** of the verifiers (the second verifier itself is built; it does not read RFC 3161 tokens and treats an integer beyond 2^53 as a float, both noted in its source).
- **Set the tenant home country** (`app.state.home_country`) so the cross-border rule can tell home from abroad; add a redaction proof if the owners want one. **Known weakness:** the existing package builder shows a digest of each withheld field, and a digest of a low-entropy value (an outcome, a yes or no) can be guessed. Acceptance row `WF08-T-10-03` ("cannot be reversed from a public payload") stays NOT_STARTED until those digests are salted or dropped for the public tier.
- **Connect WF07 revocation to the status list** so revoking an agent sets its slot by itself, and decide the publication schedule (a list lives one hour; nothing republishes it on a timer yet).
- **A production run of the drills** and a cryptographic test campaign by someone who did not write the code.
- A receipt `environment` field, if the owners want sandbox and production separation inside the receipt itself.
