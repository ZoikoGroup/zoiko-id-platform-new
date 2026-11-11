# CR-071: the /v2 facade of the global trust API (ZID-INT-002)

Status: built and tested locally, uncommitted. Needs reviewer approval like every change request.

## What changes
- **Routes** (new, `credential/api/v2.py`): `GET /v2/capabilities`, `GET /v2/coverage`, `POST /v2/credentials/verify`, `GET /v2/credentials/{ref}`,
  `POST /v2/eligibility/evaluate`, `GET /v2/eligibility/{ref}`, `POST /v2/zoikorum/engagement-checks`, `POST|GET /v2/issuer-checks`, `GET /v2/evidence/{ref}/manifest`, `POST|GET /v2/webhook-endpoints`, `GET /v2/events/{id}`, `POST /v2/events/{id}/replay`. Route-map rules `GET /v2/*` and `POST /v2/*` (mode R).
- **Errors**: on paths under `/v2/`, errors are RFC 9457 problems (`application/problem+json`, `code`, `trace_id`). `zid_errors.problem_document`
  holds the mapping; `zid_app` renders it. `/v1` is unchanged.
- **Views** (pure, `credential/application/v2_view.py`): the eight evidence statuses of section 06.1, a separate engagement vocabulary, the section 02.1 envelope.
- **Refactor**: `perform_check` in `credential/api/claim_checks.py` is now shared by the v1 routes and the facade (same behavior; it can run in a caller-held
  transaction so the idempotency record and the check commit together). `_engage` in `credential/api/trust.py` gained an optional connection.
- **Migration** `credential_0009`: `cred.source_dead_letter` (GID-060). Table ownership entry added.
- **SDK**: `sdks/typescript/src/session.ts`, the section 05.1 state machine.
- **Contracts**: `contracts/openapi/public/credentials.json` regenerated (adds `/v2/...` paths).

## What does not change
No existing route, event, action or table is altered. The v2 routes reuse the existing actions (`credential.claim.verify`, `credential.read`,
`credential.eligibility.evaluate`); no new action is added.

## Deliberate choices to review
- The v2 default purpose is `professional_engagement`, because that is a permitted purpose in the sample source contracts. The document's example
  `professional_onboarding` is refused today (SOURCE_PURPOSE_NOT_PERMITTED) until an owner adds it to a contract.
- A source that cannot be asked returns outcome UNAVAILABLE with a fault code, not HTTP 424.
- The facade does not invent readiness: every capability is PROVIDER_DEPENDENT and coverage lists only contracted non-simulated sources (none today).

## Also added
- `verification/domain/pack_diff.py` (dry-run comparison and rollback proof) and `verification/domain/provider_adapter.py` (adapter contract, entitlement routing, failover guard, webhook verification). Pure; no adapter for any real provider.
- `credential.status_changed` is now a subscribable webhook event type (it was emitted but could not be subscribed to).
- `sdks/python/src/zoiko_credentials/session.py`, the Python mirror of the session state machine.

## Not built (needs providers, a library, or an orchestrator that does not exist)
`/v2/verifications` (a multi-check case needs a case orchestrator), evidence uploads (scanning and an object store), document validation (DTV-02), organization and mandate verification (no KYB source), presentations (the request lacks the configuration the verifier needs), Rooms property checks, every real provider adapter.

## Known test gaps
None for the routes listed above. The facade has no load or latency measurement.

## Tests
`tests/unit/test_v2_view.py` (26), `tests/unit/test_pack_diff_and_provider_guards.py` (20), `tests/component/test_v2_facade_pg.py` and `test_webhook_v2_pg.py` (full app on PostgreSQL), `tests/component/test_source_dead_letter_pg.py`,
`sdks/typescript/test/session.test.ts` (8), `sdks/python/tests/test_session.py` (6).
