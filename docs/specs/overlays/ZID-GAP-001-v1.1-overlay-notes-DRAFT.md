# ZID-GAP-001 v1.1 overlay notes for Documents 02 and 03 (DRAFT, not approved)

Status: draft for owner review. This is written from the code as built (CR-069, CR-070 proposal). It does not replace Documents 02 or 03: it is the list of
changes a v1.1 of each document would need so that they match what exists. An owner must approve it before it is treated as a controlled document.
The source text of Document 05 scenarios is not in this repository, so the de-duplication of Document 05 against the GLB cases cannot be done here.

## Changes that apply to both documents

| # | Change | Where it is in the build |
|---|---|---|
| 1 | Public paths are versioned: every credential route is under `/v1/credentials/...` or `/v1/credential-packs...` (checks, assertions, disclosures, source outreach, appeals, monitoring, signed-credential verification). | `credential/api/claim_checks.py`, `credential/api/trust.py`; `contracts/openapi/public/credentials.json` |
| 2 | The assertion carries the full section 08.1 field set: `check_id`, `assertion_id`, `actor_ref`, `consumer`, `consent_ref`, `policy_ref`, `revocation_status`, `evidence_receipts`, `subject_match`, and a `signature`. | `credential/application/assertion_contract.py` |
| 3 | The signature is a compact JWS (`typ` `zid-assertion+jwt`) over the assertion id, check id, issue time and the SHA-256 of the canonical body, so it binds the exact body without containing it. The digest is pinned by a golden vector shared with the Python and TypeScript SDKs. When no signing key is configured the field is `null` and the assertion says so. | same file; `sdks/python`, `sdks/typescript` |
| 4 | Every check writes an evidence receipt (decision type CREDENTIAL, class E2) in the same transaction, holding commitments only. A refused check writes none. | `credential/application/claim_checks.py` (`_finalize`) |
| 5 | The status-change event is named `credential.status_changed` (CloudEvents type `com.zoiko.id.credential.status_changed.v1`). | `contracts/asyncapi/proposed-events.yaml` |

## Document 02 (technical) specific

- Source calls pass through retry with backoff and jitter, a per-source rate limit and a circuit breaker (`credential/application/resilience.py`). A source that cannot be asked yields `SOURCE_UNAVAILABLE` with a fault code and an attempt count, never the source's error text and never a verdict on the person.
- A source status event for an unknown or already-replaced check is kept in `cred.source_dead_letter` (migration `credential_0009`), once per check and event, and can be replayed (`GET /v1/credentials/monitoring/dead-letters`, `POST .../{id}/replay`). The old 404 for those cases is gone.
- Name matching folds letters Unicode does not decompose (for example L with stroke, O with stroke), and a name in a script the scorer cannot compare (Chinese, Korean, Devanagari, Hebrew and others) is routed to a person with `SCRIPT_NOT_COMPARABLE`; it is never reported as a different person.
- Two read-only views: `GET /v1/credentials/registry` (issuers, source contracts, policies, certifications; scope and state only) and `GET /v1/credentials/dossiers/{subject}` (a person's current checks, decisions and appeals).
- Governed erasure of credential-check data is `cred.erase_subject` (`credential_0008`).

## Document 03 (assurance and test) specific

- Section 11 fairness: a synthetic cohort test (`tests/unit/test_name_matching_fairness.py`) pins that no true pair is ever refused, no different-person pair is ever matched, and unreadable scripts always go to review. It is a regression harness, not a validated fairness evaluation: the cohorts are small and invented. Known disparity: a person whose name is in an unreadable script always waits for a human.
- The 36 GLB cases are linked to tests in `docs/traceability/acceptance-tests.csv`; all are IN_PROGRESS, none VERIFIED. GLB-25 is proved structurally by `tests/unit/test_glb_invariants.py`.
- The DPIA draft is `docs/privacy/dpia-credential-trust-DRAFT.md`. It is unsigned.

## Open items this overlay cannot close
Real sources and issuers; a price book and meter (CR-070 proposal); EU e-seal validation; other-language SDKs; translations and a WCAG audit; DPIA sign-off; independent UAT; release authorization.
