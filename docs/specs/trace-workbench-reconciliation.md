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
