# CR-149: Verify API, slice 1: a company's applications, credentials and server-to-server token

Status: slice 1 built 24 Nov 2026 (uncommitted). Source: `ZID-EXT-IDV-001` (External Identity Verification API, Partner Platform), work packages WF-05, section 10, SEC-01 to SEC-04, epics E02 and the start of E03.

**Why.** The document describes how an outside company uses Zoiko iD to verify its own customers: it is approved, creates an application, gets credentials, and its server creates verification sessions. Nothing of that existed. The closest piece, the
integration application registry, is for the platform's own authorization integrations (redirect allowlists, PEP profiles), not for a partner's server calling a verification API, so this is a new module, `verify_api`, rather than a stretch of that one.

**What slice 1 gives a company.**
- **Applications.** One per environment, with the scopes it holds and the exact `https` origins its hosted flow may return to. Only SANDBOX can be created: PRODUCTION answers `PRODUCTION_NOT_APPROVED` until the production gate (business verification,
  signed terms, certified sandbox integration; epic E10) exists. A name is unique per environment ignoring case; at most 25 live applications; a retired one frees its name.
- **Credentials.** A client id (`zpc_...`) and a secret (`zps_...`, 256 bits) shown **once**, in the answer that creates it, and stored only as a SHA-256 hash. A lost secret means a new credential. At most 4 usable credentials per application.
- **Rotation without downtime.** Rotating gives a new credential at once and lets the old one keep working for an overlap the company chooses (1 hour to 7 days, default 24 hours). Revoking ends a credential and every token it issued, immediately.
- **Tokens.** `POST /v1/verify-api/oauth2/token` (client credentials, JSON, form or HTTP Basic) returns a 15-minute opaque bearer token bound to the application, environment and scopes. It carries the scopes the application holds or a subset the caller asks for, never more.
  Every failure gets one answer (`AUTH_INVALID`, 401) so nothing says whether the client id, the secret or the state was wrong; a hash is compared even for an unknown client so timing does not either. Ten wrong secrets for one client id in five minutes
  answer `RATE_LIMITED` (429) (a per-process floor; a shared counter belongs with the platform's rate limiting). Responses carry `Cache-Control: no-store`.
- **Immediate stop.** A token ends when its own 15 minutes are up, when its credential is revoked or past its overlap, or when its application is suspended or retired. Suspending or retiring an application stops its tokens at once.
- **`GET /v1/verify-api/whoami`** shows what a token is (application, environment, scopes, expiry, an opaque company reference) so a partner can prove its credentials work. It is the guard every later partner route uses (`verify_api_caller`).

**Who may do what.** Reads (`verify_api.application.read`, R1) are in the administrator list; creating and governing applications and credentials (`verify_api.application.manage`, R2) is not, because it commits the company, so it stays with the owner (who holds every
catalogue action, CR-137) and anyone the owner delegates it to. The action sits in the step-up tier for "privileged API clients" (one-time secret display, no re-display, rotate and audit).

**Data.** Migration stream `verify_api` (`verify_api_0001`): `verify.api_application`, `verify.api_credential`, `verify.api_access_token`, tenant-scoped under row-level security. A server has no signed-in person, so the token exchange and the bearer check read by an unguessable key
inside a transaction that has put itself into `auth` scope (a second policy that opens only then). Outside it, no company's credentials or tokens can be read, whoever asks.

**Routes** (`contracts/openapi/public/verify-api.json`): `GET|POST /v1/verify-api/applications`, `GET /v1/verify-api/applications/{id}`, `POST ...{id}:suspend|:resume|:retire`, `POST .../credentials`, `POST .../credentials/{cid}:rotate|:revoke`, `POST /v1/verify-api/oauth2/token`, `GET /v1/verify-api/whoami`.

**Tests.** `tests/unit/test_verify_api_model.py` (names, environments, scopes only narrow, exact origins, secrets and hashing, the limiter); `tests/component/test_verify_api_pg.py` (the whole lifecycle against the database and the real HTTP endpoints, a signed-in owner through the
permission check, isolation between companies, expiry, rotation overlap, revoke and suspend ending tokens, JSON/form/Basic, no caching, guessing limits).

**Not built (the rest of the document).** Production gate and operator approval (E10); partner onboarding state machine on top of company verification (E01); the create-session, session read and decision APIs with idempotency (E03, E04, E06); the applicant launch token and hosted flow (E05); signed webhooks
for the partner (E07); the sandbox simulator (E09); the "Verify API" console screens (E11 to E16); usage metering and billing (E17); published SDKs and documentation (E13). Expired token rows are not yet cleaned up. No claim of parity with any competitor or of production readiness is made.
