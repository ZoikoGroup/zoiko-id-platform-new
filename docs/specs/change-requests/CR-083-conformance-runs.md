# CR-083: Independent conformance runs of the 48 acceptance cases (ZID-RCG-003 sections 12 and 12.1)

Status: open (matrix, runner, storage, review, operator API and tests built; the owners confirm the points below).

## Why
Section 12: execute all 48 cases; each test stores the code commit, environment, test run, actual trace, expected results and an independent reviewer.
Section 12.1 P0 exit: all 48 required tests executed with signed evidence and no open critical defect. Before this change nothing said which test
proves which case, and no run was recorded or reviewed.

## What changes
- **The matrix** `backend/config/regulatory/conformance-matrix.yaml`: every case T01..T48 with the tests that prove it, matched by what each test
  asserts (several test names carry T-numbers from other documents). COVERED, PARTIAL (the note says what is missing) or GAP. A guard test checks every
  named test exists and every non-covered case says why. At the time of writing: 31 COVERED, 16 PARTIAL, 1 GAP (T45).
- **The runner** `tools/scripts/conformance_run.py`: runs exactly the matrix's tests with the CI flags, maps each result to its case, and writes the run
  body. A case PASSES only when COVERED and every test passed; a failed test FAILS it; a missing test is NOT_RUN; PARTIAL and GAP never pass. A working
  tree with uncommitted changes is marked `+dirty`.
- **Storage** (migration `verification_0011`): `verify.conformance_run` and `verify.conformance_review`, append-only. The run hash covers the commit,
  environment, matrix hash and every outcome; a review signs that stored hash and is never by whoever recorded the run (the tables enforce both).
- **Operator API** (`operator_access/api/regulatory_conformance.py`): record a run (`regulatory.conformance.record`), review it
  (`regulatory.conformance.review`; the two capabilities are a separation-of-duties pair), list, read, and the matrix in force. Each answer carries
  the P0 exit status: met only when all 48 cases PASS and an independent reviewer ACCEPTED the run, with nobody rejecting it.

## Open PARTIAL and GAP cases (owned outside the regulatory work)
T10 subtype exclusion, T12 403 with audit on cross-tenant access, T19 employer-confirmation collision, T20 explicit EXPIRED/RECHECK_REQUIRED, T21 one
combined test, T26 one combined test, T30 withdrawal-triggered erasure, T35 log and alert on a rejected webhook, T38 refusal for a missing purpose,
T40 re-evaluation of pending sessions by region, T41 quarantine and the credential document path, T42 recheck and review of issued proofs, T43
role and legal-basis filtering, T44 hold of an automated path on a detected disparity, T45 notification deadline clock (GAP), T48 logging the consumer
purpose on a denied release. Each is named in the matrix with what is missing; the P0 exit cannot be met until they are closed.

## Owner decisions (proposed values, none ratified)
1. The independent reviewer is QA (the RG-08 "Independent QA" role), holding `regulatory.conformance.review`.
2. A run on a dirty working tree is evidence about that tree only, never about the commit.
