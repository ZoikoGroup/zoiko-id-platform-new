# CR-153: Verify API, slice 5: the go-live gate (opening production for a partner)

Status: slice 5 built 24 Nov 2026 (uncommitted). Source: `ZID-EXT-IDV-001` section 11, epic E10 (go-live gating engine), gates G1 to G4, QA Q02, Q08, Q43, Q44.

**Why.** The document's rule is that a company cannot reach production on its own: it needs its company verified, terms signed, a certified sandbox integration, an approved provider and billing, and a recorded approval. Until now production applications were simply refused. This slice builds the
gate that opens them, and the emergency stop that closes them.

**The company** (Developers page, "Go live"; routes `GET /v1/verify-api/go-live`, `POST /v1/verify-api/go-live-requests`, `:withdraw`):
- Three checks are made **automatically from its own sandbox work**: a test verification that passed, one that failed (so it handles a refusal), and a verified, active webhook endpoint. The company cannot ask until all three are met (`GO_LIVE_NOT_READY`, with what is missing in plain words).
- It then states the countries, checks, purpose, expected volume and its own description of the use, and accepts the **current** terms and data-processing terms (stale versions refused). One open request at a time; it can withdraw and ask again, or ask again after a decline.
- When approved it sees what was allowed and the **fingerprint (SHA-256 digest)** of that record, and may create PRODUCTION applications.

**The operator** (Commercial, "Verify API go-live"; routes `/operator/verify-api/...`, behind the operator gate): one person takes a request into review; a **different** person confirms five items one by one (company verified, terms signed, privacy reviewed, billing ready, provider approved for each
market and method) and approves. Nothing is assumed: a missing or false confirmation refuses the approval. Approval re-checks the sandbox checks at that moment (a company that retired its only webhook after asking is not approved). A decline needs a reason the company reads.
**Emergency stop:** `POST /operator/verify-api/grants/{tenant}:revoke` (a reason is required) ends the grant, suspends every production application of the company and ends their tokens at once (Q44).

**What approval records.** `verify.api_production_grant`: markets, checks, purpose, terms versions, the sandbox results, which confirmations were given, reviewer, approver and time, as one record with its SHA-256 digest, so what was approved can be shown and checked later. This is a digest, not a
cryptographic signature; signing it with the platform's key broker is a later hardening.

**What it does not do.** Approval says what the company is *allowed* to do. A production application can sign in, but creating a verification in production still answers `PRODUCT_NOT_ENTITLED` until the live verification engine is connected (a separate gate, G3/G4 provider certification). Company
verification, signed terms, privacy and billing are confirmed by a person, not derived by the platform: the company-verification result is not yet read automatically from the KYB module. No alert is sent to the company on a decision yet; it sees the state on the Go live card.

**Data.** Migration `verify_api_0003`: `verify.api_go_live_request` (one open per company by a partial unique index; the reviewer and decider cannot be the same person for an approval, enforced by a check constraint too) and `verify.api_production_grant` (one active per company). Tenant row-level
security plus an `operator` value of the existing `app.verify_api_scope` setting for the operator plane. The webhook module gained `active_endpoint_count` on its port.

**Tests.** Unit (16 with slice 1): request validation, terms versions, certification, state machine, confirmations, record digest. Component (5, real HTTP and database): not-ready refusal, certification progress, one open request, withdraw, review/self-approval/missing-confirmation/approval,
recomputing the stored record to its digest, production application creation and sign-in, session creation still refused, emergency revoke, decline with reason, isolation between companies, lapsed checks, and the operator routes refusing non-operators. Web: tsc 0 errors, eslint clean, 11 tests; operator: tsc 0 errors, eslint clean, 2 tests.
The operator HTTP gate itself (AAL-Z3, JIT grant) is exercised by the operator-plane tests, not re-tested here.
