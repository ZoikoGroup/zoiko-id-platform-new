# CR-155: Verify API, slice 7: results search, decision history and rechecks

Status: slice 7 built 24 Nov 2026 (uncommitted). Source: `ZID-EXT-IDV-001` epic E16 (cases, results and reviewer console: scoped search, minimized decisions, rechecks), WF-12, QA Q24 (a revision supersedes the original with an audit trail) and Q25 (a partner cannot see raw media).

**Why.** A company could create a verification and read its decision by id, but had no way to look back across its applications, see how a result changed, or check a customer again.

**Results** (Developers page, "Results" card; `GET /v1/verify-api/sessions`, `GET /v1/verify-api/sessions/{id}`; `verify_api.application.read`):
- Search across **all** the company's applications by application, environment, state and an **exact** customer reference (never a text search, so a partial reference finds nothing and the reference cannot be probed). Newest first, paged by a cursor.
- Open one session to see **every decision** made about it (a later one supersedes the earlier one and the earlier one is kept), the rechecks that follow it, and whether a recheck can start. Only the minimized result is shown: which checks passed, reason codes, policy version, provenance. No photograph, document image, score or reviewer note exists in this API.

**Rechecks.** A recheck is a **new session** that follows a finished one (`recheck_of`; migration `verify_api_0004`), with its own link and its own decision. The original and its decisions are never changed.
- It is made from the original's own customer reference, workflow, market and purpose, so a recheck cannot widen what was agreed.
- Allowed after a session finished, ended with no answer, or ran out of time. Not while it is in progress, with a reviewer (wait for the decision), or after it was canceled.
- The partner's server: `POST /v1/verify-api/verification-sessions/{id}:recheck` with an `Idempotency-Key` and scope `verifications:recheck`. The same key is the same session; the same key for a different original is refused. Another application of the same company gets `NOT_FOUND`.
- The console: `POST /v1/verify-api/sessions/{id}:recheck` (`verify_api.application.manage`), acting as the application that made the session; the link is shown once.
- Rechecks go through the one session-creation path, so production rechecks are metered as usage facts (CR-154) like any production session start.

**Not done.** Appeal hooks (the document's "appeal" link to a rights case) wait for the privacy rights module integration (E18); reviewer assignment is not built because no real manual-review operations exist yet (sandbox reviews are simulated); rechecks are not billed or priced (D12 open). Export of results is not built.

**Tests.** Component (4, real HTTP and database): recheck links to the original and leaves its decision exactly as it was, is idempotent and refuses key reuse for another original, needs a key, works from finished/declined/no-answer/ran-out-of-time and is refused from in-progress, with-a-reviewer and canceled; scope and application isolation;
company search with every filter, exact-match behaviour, paging, bad input refused, detail with both decisions of a resolved review, console recheck and its parent/child links; another company sees nothing. Earlier Verify API and webhook suites (30) still pass. Web: tsc 0 errors, eslint clean, 25 tests in the developers folder.
