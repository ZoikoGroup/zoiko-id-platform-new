# Document 01 (ZID-ENG-TRC-001): traceability workbench, what exists and what was added

Written 25 Nov 2026. The document asks for one canonical register that extends the existing traceability files, not a second tracker. This is built as a generated report; no table, service or screen was added.

## Built: `tools/scripts/trace_workbench.py` (tests: `backend/app/tests/unit/test_trace_workbench.py`)

| Document ask | Where |
|---|---|
| Section 16 response sheet, one row per requirement, unknown says NOT ASSESSED | `trace_workbench.py sheet` writes `docs/traceability/response-sheet.csv` (2047 rows; 265 have no evidence, E0). Build digest, environment, market, cohort, provider, CI run, owner and due date are NOT ASSESSED for every row: this repository does not hold them. |
| TRC-23 / AT-04: changed code expires a PASS | `record` freezes a digest of every evidence file (`docs/traceability/evidence-digests.json`, 419 files); `stale` lists EXPIRED, MISSING and UNRECORDED and exits 1 on EXPIRED or MISSING. Run `record` only when results have been re-verified. |
| Release tuple and gates G0 to G4, AT-27 | `assess MANIFEST.json`: market, tenant class, provider and method must be set; a market needs its own approval; 19 missing DTV definitions, P0 without evidence, no build digest, cross-tenant bypass, unqualified source, expired evidence, no partner E2E, missing or self-signed G3 roles, one person in two roles, unsupported public claims, unrehearsed rollback and open incidents each block; a later gate cannot pass while an earlier one is blocked. |
| 34 DTV ids, 19 undefined | `dtv`: 19 MISSING_SOURCE, 6 CONDITIONAL_HOLD, none countable as done. |
| 30 acceptance tests | `docs/traceability/engineering-acceptance.csv`, prefix `ENG-AT-` (the older `AT-01..30` ids are another set). 8 linked to real tests with a note on how far each goes; 22 NOT_STARTED because no existing test exercises them. Regenerate with `docs/traceability/build_engineering_acceptance.py`. |

## Not built, deliberately

- **The eight TRC screens and the `/internal/v1/trace/*` APIs.** The document warns against a register becoming another product. The sheet, stale check and gate assessment cover the same facts. Build screens only if the CSV workflow proves insufficient.
- **Real inputs for the manifest** (build digests, CI runs, provider contracts, partner E2E result, signatures): they come from pipelines and people. `register_build.py` and `deploy/release/` are the existing sources to wire in.
- **The 19 DTV definitions, independent QA sign-off, DEC-01..10:** owner work.
- **Stale detection is file-level.** It does not yet see a provider, model or dependency change, nor a build digest.

## Document 02 (ZID-ENG-REL-002): `tools/scripts/release_cell.py`

Built on the existing `release_gate.py` (artifact promotion) and `trace_workbench.py assess` (G0 to G4). Tests: `backend/app/tests/unit/test_release_cell.py` (12).

| Package | What it does |
|---|---|
| REL-01 | `fingerprint`: one hash over build, schema, config hash, policy versions, providers, market, region, cohort and window. A missing dimension means no fingerprint, so no approval. `drift` compares a recorded fingerprint with the cell now. |
| REL-02 | Evidence ledger: hash-chained, append-only; an artifact id cannot be re-recorded with other bytes; `ledger-verify` finds an edit, removal or reorder. Evidence counts only for its own cell. |
| REL-05 | Sign-offs bind to the fingerprint (a change voids them). Three assessor roles and two functional roles are required; anyone who worked on the release cannot sign; one person cannot hold two roles. |
| REL-13 | Claims interlock: the Trust Center monitoring report must be under 24 hours old, its release gate open and no unsupported live claim; a missing report blocks. |
| REL-04, REL-12 | `release_problems` joins all of the above with the workbench gates; `issue` signs an Ed25519 token (fingerprint, ledger head, signers, expiry) only when nothing blocks; `verify` is what a deploy step runs and refuses a forged, expired, drifted, re-evidenced or other-market token. |

Not done: the deploy pipeline does not call `verify` yet (add it to the promotion workflow), the release key is the company's to create, the monitoring report must be fetched with an operator credential and saved as JSON, no operator capability for the assessor roles exists (roles are named in the sign-off file), and the ten REL screens are not built.
