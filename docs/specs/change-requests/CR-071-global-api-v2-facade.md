# CR-071: the /v2 facade of the global trust API (ZID-INT-002)

Status: built and tested locally, uncommitted. Needs reviewer approval like every change request.

## What changes
- **Routes** (new, `credential/api/v2.py`): `GET /v2/capabilities`, `GET /v2/coverage`, `POST /v2/credentials/verify`, `GET /v2/credentials/{ref}`,
  `POST /v2/eligibility/evaluate`, `GET /v2/eligibility/{ref}`, `POST /v2/zoikorum/engagement-checks`, `POST|GET /v2/issuer-checks`, `GET /v2/evidence/{ref}/manifest`, `POST|GET /v2/webhook-endpoints`, `GET /v2/events/{id}`, `POST /v2/events/{id}/replay`, `POST|GET /v2/presentations/...`, `POST|GET /v2/verifications` with `/tasks/{check}` and `/cancel`, `POST /v2/documents/validate`, `POST /v2/evidence/uploads`, `PUT /v2/evidence/uploads/{id}/content`, `DELETE /v2/evidence/{ref}`. Route-map rules `GET /v2/*` and `POST /v2/*` (mode R).
- **Errors**: on paths under `/v2/`, errors are RFC 9457 problems (`application/problem+json`, `code`, `trace_id`). `zid_errors.problem_document`
  holds the mapping; `zid_app` renders it. `/v1` is unchanged.
- **Views** (pure, `credential/application/v2_view.py`): the eight evidence statuses of section 06.1, a separate engagement vocabulary, the section 02.1 envelope.
- **Refactor**: `perform_check` in `credential/api/claim_checks.py` is now shared by the v1 routes and the facade (same behavior; it can run in a caller-held
  transaction so the idempotency record and the check commit together). `_engage` in `credential/api/trust.py` gained an optional connection.
- **Signature validation (DTV-02)**: `evidence/application/cms.py`, `trust.py`, `signatures.py` verify PDF signatures on `cryptography` only (no new dependency; `lxml`, `signxml` and `pyasn1` were already installed), reuse `zid_evidence.timestamp` for trusted time, and read `config/trust/` (anchors, trusted lists, TSA roots, CRLs, pinned list signers). The folder ships empty so nothing is trusted by default. A reviewed `lxml` exception is registered in `test_injection_controls.py` (defusedxml gate first).
- **Final pass**: `evidence_0008` (`evidence.document_validation`, results only, cascades with its upload), `GET /v2/documents/{ref}`, `GET /v2/evidence/uploads`, new events `eligibility.recomputed` and `evidence.retention_action` (proposed, subscribable), `retry_guidance` on an UNAVAILABLE credential answer (the section 14 424 is deliberately not used), Python and TypeScript `/v2` clients (`v2.py`, `v2.ts`), web Evidence tab, RFC 9457 support in the web API client, and a guard that no route can edit a service pack.
- **Closing pass (2026-11-12)**: `credential_0013` (`cred.property_check_state`: references and outcomes only) and `verification_0006` (`verify.provider_entitlement`: scope and status, revoked is final by trigger), new events `property.authority_changed` and `provider.entitlement_revoked` (proposed, subscribable), `/v2/providers/*` (record, list, suspend, revoke, routing dry run), the browser widget `session.mount` (`sdks/typescript/src/widget.ts`), generated types in the TypeScript SDK, consumer contracts for Zoikorum and Rooms (`contracts/consumers/`), and `zid_platform/slo_probe.py` with a local latency baseline. Also earlier in the same pass: `/v2/mandates/verify`, `/v2/rooms/property-checks`, the `issuer_claim` check and the `analysis` check.
- **Evidence uploads**: `evidence_0005` (`evidence.upload`, bytes in the cell database; a CHECK allows bytes only for READY and scanned CLEAN), `evidence_0006` and `0007` (`evidence.erase_uploads`, a SECURITY DEFINER erasure function with an explicit tenant filter), new action `evidence.upload.manage` (R2/E2), settings `ZID_UPLOAD_RETENTION_DAYS` (required to accept uploads) and the existing `ZID_PROOF_SCANNER_ADDRESS` (clamd), worker step `evidence_purge`, privacy wiring in `zid_platform/evidence_privacy_adapter.py` (top level, because an adapter inside the evidence module would make an evidence/privacy module cycle).
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
- `credential_0011` replaces `cred.erase_subject` so every DELETE also filters on the tenant it was called for. A test on the matching evidence function showed a SECURITY DEFINER function owned by a role that bypasses row-level security deletes one person's rows in every tenant; the credential function had the same exposure (in practice only if two tenants shared a person id). Behavior for the right tenant is unchanged.
- `decide()` now returns INDETERMINATE (not NOT_VERIFIED) when no source was asked. A document alone is "pending or limited", never a negative verdict (ZID-INT-002 section 15 and T06).
- `verifier.create` takes an optional `ttl` that can only shorten the request's life (default unchanged at 10 minutes).
- `verification.case.completed` is a new proposed event, and is subscribable.

## Not built (needs a component, a data source or a contract that does not exist)
Issuer AUTHENTICATION of a document (an issuer source), OCSP, name and policy constraints, XAdES and standalone CAdES, and long-term-validation archives (DTV-02 is built for PDF signatures; qualified status needs the operator's authenticated trusted list), organization and mandate verification (no KYB source), Rooms property checks (no property or title source), every real provider adapter (no contract or sandbox), a signed case `resume_token` (the document says only "if applicable"; a token that resumed a case without the caller's own authority would be a bearer capability that weakens the tenant and authorization checks), and a stored validation result (`GET /v2/documents/{id}`).

## Needs people before use
A running clamd and `ZID_PROOF_SCANNER_ADDRESS` in each deployed cell; the retention period (`ZID_UPLOAD_RETENTION_DAYS`) from the privacy officer; a DPIA addendum for holding D3 documents in the cell database (databases and backups now hold identity documents: encryption at rest and backup retention are an infrastructure decision, not code).

## Decisions recorded
- Section 14 `424 source_unavailable`: not used. The check is recorded, so the answer is 201 with outcome UNAVAILABLE plus `retry_guidance`.
- DTV-19: no route until a trustworthy birth-date source exists.

## Known test gaps
The facade has no load or latency measurement. The case identity link is tested against a session row inserted directly; no real provider session exists.

## Tests
`tests/unit/test_v2_view.py` (26), `tests/unit/test_pack_diff_and_provider_guards.py` (20), `tests/component/test_v2_facade_pg.py` and `test_webhook_v2_pg.py` (full app on PostgreSQL), `tests/component/test_source_dead_letter_pg.py`,
`tests/unit/test_documents.py` (22), `tests/unit/test_verification_cases.py` (21), `tests/component/test_v2_cases_pg.py` (3, including erasure of a person's cases), `tests/component/test_presentations_v2_pg.py`, `sdks/typescript/test/session.test.ts` (8), `sdks/python/tests/test_session.py` (6).
