# CR-071: the /v2 facade of the global trust API (ZID-INT-002)

Status: built and tested locally, uncommitted. Needs reviewer approval like every change request.

## What changes
- **Routes** (new, `credential/api/v2.py`): `GET /v2/capabilities`, `GET /v2/coverage`, `POST /v2/credentials/verify`, `GET /v2/credentials/{ref}`,
  `POST /v2/eligibility/evaluate`, `GET /v2/eligibility/{ref}`, `POST /v2/zoikorum/engagement-checks`, `POST|GET /v2/issuer-checks`, `GET /v2/evidence/{ref}/manifest`, `POST|GET /v2/webhook-endpoints`, `GET /v2/events/{id}`, `POST /v2/events/{id}/replay`, `POST|GET /v2/presentations/...`, `POST|GET /v2/verifications` with `/tasks/{check}` and `/cancel`, `POST /v2/documents/validate`. Route-map rules `GET /v2/*` and `POST /v2/*` (mode R).
- **Errors**: on paths under `/v2/`, errors are RFC 9457 problems (`application/problem+json`, `code`, `trace_id`). `zid_errors.problem_document`
  holds the mapping; `zid_app` renders it. `/v1` is unchanged.
- **Views** (pure, `credential/application/v2_view.py`): the eight evidence statuses of section 06.1, a separate engagement vocabulary, the section 02.1 envelope.
- **Refactor**: `perform_check` in `credential/api/claim_checks.py` is now shared by the v1 routes and the facade (same behavior; it can run in a caller-held
  transaction so the idempotency record and the check commit together). `_engage` in `credential/api/trust.py` gained an optional connection.
- **Migrations**: `credential_0009` `cred.source_dead_letter` (GID-060); `credential_0010` `cred.verification_case` and `cred.verification_case_task`, and a replaced `cred.erase_subject` so governed erasure reaches the case tables. Table ownership entries added; the privacy data map registers the cases.
- **SDK**: `sdks/typescript/src/session.ts`, the section 05.1 state machine.
- **Contracts**: `contracts/openapi/public/credentials.json` regenerated (adds `/v2/...` paths).

## What does not change
No existing route, event, action or table is altered. The v2 routes reuse the existing actions (`credential.claim.verify`, `credential.read`,
`credential.eligibility.evaluate`); no new action is added.

## Deliberate choices to review
- `PROFESSIONAL_ONBOARDING` was added to the permitted purposes of the sample `simulator` source contract on the requester's instruction (2026-11-11), so the document's own example purpose works and is the v2 default. It is a SAMPLE row: a real source contract must list its own permitted purposes, and Legal owns that list.
- A source that cannot be asked returns outcome UNAVAILABLE with a fault code, not HTTP 424.
- The facade does not invent readiness: every capability is PROVIDER_DEPENDENT and coverage lists only contracted non-simulated sources (none today).

## Also added
- `verification/domain/pack_diff.py` (dry-run comparison and rollback proof) and `verification/domain/provider_adapter.py` (adapter contract, entitlement routing, failover guard, webhook verification). Pure; no adapter for any real provider.
- `credential.status_changed` is now a subscribable webhook event type (it was emitted but could not be subscribed to).
- `sdks/python/src/zoiko_credentials/session.py`, the Python mirror of the session state machine.

## Behavior changes to review
- `decide()` now returns INDETERMINATE (not NOT_VERIFIED) when no source was asked. A document alone is "pending or limited", never a negative verdict (ZID-INT-002 section 15 and T06).
- `verifier.create` takes an optional `ttl` that can only shorten the request's life (default unchanged at 10 minutes).
- `verification.case.completed` is a new proposed event, and is subscribable.

## Not built (needs a component, a library, a data source or a contract that does not exist)
Evidence uploads (malware scanning and an object store with deletion controls, plus a retention decision from the DPO for stored personal documents), signature, seal and issuer VALIDATION of documents (a PAdES/XAdES validator, trust lists and an issuer source, DTV-02; only integrity, type and signature detection exist), organization and mandate verification (no KYB source), Rooms property checks (no property or title source), every real provider adapter (no contract or sandbox), and a signed `resume_token` for cases (the document says only "if applicable"; a token that resumed a case without the caller's own authority would be a bearer capability that weakens the tenant and authorization checks, so none is issued).

## Known test gaps
The facade has no load or latency measurement. The case identity link is tested against a session row inserted directly; no real provider session exists.

## Tests
`tests/unit/test_v2_view.py` (26), `tests/unit/test_pack_diff_and_provider_guards.py` (20), `tests/component/test_v2_facade_pg.py` and `test_webhook_v2_pg.py` (full app on PostgreSQL), `tests/component/test_source_dead_letter_pg.py`,
`tests/unit/test_documents.py` (22), `tests/unit/test_verification_cases.py` (21), `tests/component/test_v2_cases_pg.py` (3, including erasure of a person's cases), `tests/component/test_presentations_v2_pg.py`, `sdks/typescript/test/session.test.ts` (8), `sdks/python/tests/test_session.py` (6).
