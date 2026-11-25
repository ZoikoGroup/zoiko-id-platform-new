# CR-150: Verify API, slice 2: verification sessions, minimized decision and the sandbox simulator

Status: slice 2 built 24 Nov 2026 (uncommitted). Source: `ZID-EXT-IDV-001`, work packages WF-04, WF-09 to WF-14, section 10, epics E03 to E06 and E09.

**Why.** Slice 1 (CR-149) lets a partner's server prove who it is. This slice lets it ask for a verification and read the result, which is the point of the product. Everything here runs in the SANDBOX: nothing real is checked, every result is labelled as simulated, and no personal
data of the person is collected or stored. The real engine and the production gate are later slices.

**What a partner gets.**
- **Create a session** (`POST /v1/verify-api/verification-sessions`, scope `verifications:create`) with a required `Idempotency-Key`. Repeating the same key and request returns the same session (200, not a second one) with a fresh launch link, because the first link is shown once; the same key with a
  different request answers `INVALID_IDEMPOTENCY_REUSE` (409); a key older than 24 hours stands for a new request. A bad request is refused before anything is created (`VALIDATION_FAILED`, `WORKFLOW_VERSION_UNAVAILABLE`, `RETURN_ORIGIN_NOT_REGISTERED` for a return address the application never registered).
- **Launch link** `zvl_...`, shown once, kept only as a SHA-256 hash, valid 30 minutes, ends when the session ends. A new link is issued by `POST .../{id}/resume-token` (the old one stops).
- **Read, list, cancel**: `GET` one, `GET` a page (newest first, cursor paging with no repeats or gaps, fixed filters status, customer reference, created range), `POST ...{id}:cancel`. A session belongs to the application that created it: another application of the same company, or another company, gets `NOT_FOUND`.
  A session nobody finished within 24 hours becomes `expired` the next time it is looked at.
- **Minimized decision** (`GET .../{id}/decision`, scope `decisions:read`): outcome (`verified`, `not_verified`, `inconclusive`, `review_required`), per-check results, reason codes, policy version, region, a decision version and the decision it supersedes. Nothing that identifies or measures the person. Sandbox
  decisions say `assurance.level = SANDBOX_SIMULATED` and carry no evidence receipt. A review resolved later adds a new version that supersedes the first; the first is kept.
- **Sandbox simulator**: `POST /v1/verify-api/sandbox/fixtures/{approved|declined|inconclusive|review|expired}/sessions` creates a session already at that outcome (so a test suite needs no person), and `POST .../sandbox/sessions/{id}:simulate` drives an open one. Both only allowed in the sandbox.
- **Catalogue**: `GET /v1/verify-api/workflows` and `/products` (the approved workflows, versions and checks; every product says it is sandbox only).

**The person's page.** `GET /v1/verify-api/hosted/{token}` (no sign-in; the link is the grant) says who is asking, why, and that this is a test, never the partner's customer reference or the session id; `:open` records the open; `:simulate` lets the sandbox test page pick an outcome. Every wrong link gets one
answer (`LAUNCH_INVALID`) and ten wrong ones from one address in five minutes answer 429. A suspended application's links stop opening. The launch address comes from configuration (`verify_hosted_origin`), never from the request.

**State machine.** 12 states; final states never reopen; READY and OPENED cannot jump to a result except by the narrow sandbox shortcut; a manual review may be resolved to completed.

**Data.** Migration `verify_api_0002`: `verify.api_session`, `verify.api_decision`, `verify.api_idempotency` under tenant row-level security plus the `auth` scope policy for the launch lookup.

**Tests.** Unit `test_verify_api_sessions_model.py` (10), component `test_verify_api_sessions_pg.py` (9, real HTTP): link shown once and hashed, decision shape, idempotency, validation, every outcome and supersession, isolation between applications and companies, scopes, paging, cancel/resume/expiry, suspension, guessing limit.

**Not in this slice.** The applicant-facing page in the web app (`/verify/session/[token]`), console Sessions and Test Lab screens, partner webhooks, production gate, usage billing, real engine, SDKs. The sandbox only: no production session can be created.
