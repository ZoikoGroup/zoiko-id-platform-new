# Document 02 v2.0 (ZID-INT-002) reconciliation against the code

Written 2026-11-11 from the code as built (CR-071). Labels follow the document's own section 01: VERIFIED_IN_CODE (repository plus test),
IMPLEMENTED_UNVERIFIED, SPECIFIED_ONLY, PROVIDER_DEPENDENT, REGULATORY_GATED, OUT_OF_SCOPE. Nothing here is a certification, a coverage
claim or a production result: every source in the repository is a sample or a simulator.

## Section 02.1 contract requirements

| Requirement | Label | Evidence | Gap |
|---|---|---|---|
| `/v2` path for new global contracts; v1 unchanged | VERIFIED_IN_CODE | `credential/api/v2.py`; `test_v2_facade_pg.py` proves `/v1` keeps the canonical envelope | The v2 facade covers the credential and eligibility routes only (section 03 table below) |
| Tenant comes from the token, never from `X-Tenant-ID` | VERIFIED_IN_CODE (existing) | `API-01` in `acceptance-tests.csv` | none |
| POST mutation needs `Idempotency-Key`; a changed body under one key is 409 `idempotency_conflict` | VERIFIED_IN_CODE for the four v2 POST routes | `test_v2_facade_pg.py` (one check, one receipt after a replay) | The v1 routes of other modules keep `IDEMPOTENCY_KEY_REUSED`; the v2 renderer maps it to the document's name |
| Every decision returns request id, correlation id, pack id and version, manifest reference, `checked_at`, `valid_until`, dimension outcome | VERIFIED_IN_CODE | `application/v2_view.py`, `test_v2_view.py`, `test_v2_facade_pg.py` | The manifest reference names the evidence receipt of the check; there is no separate manifest object yet |
| RFC 9457 Problem Details | VERIFIED_IN_CODE on `/v2` | `zid_errors.problem_document`, `zid_app` handlers | `/v1` stays on the canonical envelope on purpose |
| Forward-compatible consumers; HSM-held keys; SDK holds no provider secret | SPECIFIED_ONLY / existing key-management work | none new | Not a change in this slice |

## Section 03 API registry

| Family | Route | Label | Note |
|---|---|---|---|
| Discovery | `GET /v2/capabilities`, `GET /v2/coverage` | VERIFIED_IN_CODE | Readiness is stated honestly (PROVIDER_DEPENDENT); coverage counts only contracted, in-force, non-simulated sources, so today it is empty |
| Credential checks | `POST /v2/credentials/verify`, `GET /v2/credentials/{ref}` | VERIFIED_IN_CODE | Issuer claim and award claim are kept apart in the request; the signed five-fact assertion is returned unchanged |
| License eligibility | `POST /v2/eligibility/evaluate`, `GET /v2/eligibility/{ref}` | VERIFIED_IN_CODE | No policy for the exact scope means no decision (404), never a default |
| Consumer bind (Zoikorum) | `POST /v2/zoikorum/engagement-checks` | VERIFIED_IN_CODE | `test_v2_facade_pg.py`: signed per service, never ELIGIBLE without verified identity, replay returns the same signature, a changed body is a conflict, an unknown service has no policy | One decision per requested service; the overall answer is the worst of them; signed for Zoikorum alone |
| Consumer bind (Rooms) | `POST /v2/rooms/property-checks` | SPECIFIED_ONLY | No property, title or mandate source or module exists |
| Cases | `POST /v2/verifications`, `GET`, `/tasks/{check}`, `/cancel` | VERIFIED_IN_CODE | `credential/api/v2_cases.py`, `credential_0010`, `test_v2_cases_pg.py`. Each requested check is its own task with its own outcome; the case is never "passed". A market with no pack for any requested check is 422 `service_unavailable_in_market` and creates nothing. An identity task links to a /v1 identity verification session and completes only when that session is final. A finished case answers 409 `incompatible_state`. `callback_ref` must be an active webhook endpoint of the tenant; deliveries follow that endpoint's subscriptions (`verification.case.completed`). A signed `resume_token` is not issued (the document says only "if applicable") |
| Evidence | `GET /v2/evidence/{ref}/manifest` | VERIFIED_IN_CODE | Commitments and a verification result; no claimed value. Uploads (`POST /v2/evidence/uploads`) are PROVIDER_DEPENDENT: they need malware scanning, content sniffing and an object store with deletion controls |
| Documents | `POST /v2/documents/validate` | Integrity, type and signature DETECTION are VERIFIED_IN_CODE; issuer, signature and seal VALIDATION are PROVIDER_DEPENDENT | `evidence/domain/documents.py`, `evidence/api/v2_documents.py`, `test_documents.py` and an HTTP test. A small document (2 MB) is checked in memory and discarded: nothing is stored. Integrity says unchanged bytes only. A PDF signature is detected but not validated (no validator, chain, revocation, signing-time check or trust list), the issuer is UNAVAILABLE (no registry lookup ran), and the seal is UNAVAILABLE with qualified status NOT_ASSESSED, so no check about authority is ever VERIFIED and there is no overall "authentic". `GET /v2/documents/{id}` and validation by `evidence_ref` need stored evidence, which needs the upload store below |
| Issuer outreach | `/v2/issuer-checks` | VERIFIED_IN_CODE (`/v2/issuer-checks`) | The nonce is shown once and the stored replay record never holds it; an unknown or lookalike issuer is never contacted |
| Business / authority | `/v2/organizations/verify`, `/v2/mandates/verify` | SPECIFIED_ONLY as v2 | v1 mandates exist; KYB sources do not |
| Presentations | `POST /v2/presentations/requests`, `GET /v2/presentations/{ref}` | VERIFIED_IN_CODE | `v2_presentations.py`, `test_presentations_v2_pg.py`. `configuration_id` is an explicit extra field (never inferred). `expires_in_seconds` can only SHORTEN the request (1 to 600); a longer life is refused. Same verifier rules as v1 |
| Events | `/v2/webhook-endpoints`, `/v2/events/{id}` | VERIFIED_IN_CODE (`/v2/webhook-endpoints`, `/v2/events/{id}`, replay) | `webhook/api/v2.py`, `test_webhook_v2_pg.py`; same destination rules as v1. `credential.status_changed` is now subscribable |

## Sections 05 to 14

| Area | Label | Evidence or gap |
|---|---|---|
| 05.1 SDK state machine | VERIFIED_IN_CODE (TypeScript and Python) | `sdks/typescript/src/session.ts` and its tests: no capture before consent, a corrupt payload retries, OCR never completes a session, an outage stays pending, a human decision needs an authority, no FAILED state. The Python mirror is tested and checked against the TypeScript state list. The capture UI is not built |
| 06.1 outcome vocabulary | VERIFIED_IN_CODE | `v2_view.py` can only restate or lower a decision (a test runs every decision). Engagement eligibility is a separate vocabulary, proven disjoint |
| 07 pack contract and fail-closed rules | VERIFIED_IN_CODE for dry run and rollback | `verification/domain/pack_diff.py`: compares two registries over every scenario, lists widening changes for the approver, proves a rollback answers identically. The maker/checker approval workflow around it is existing CR-069 work |
| 08 ProviderAdapter | Contract and guards VERIFIED_IN_CODE; every adapter PROVIDER_DEPENDENT | `verification/domain/provider_adapter.py`: the adapter Protocol, entitlement checks (expiry, revocation, country, purpose, region, assurance), routing that ranks only lawful providers, a failover that cannot lower assurance without an approval, webhook verification (exact bytes, window, replay, key rotation) and a deletion statement that waits for the provider. No Veriff, Yoti or Sumsub adapter exists: that needs a contract and a sandbox |
| 09 documents and signatures | PROVIDER_DEPENDENT / REGULATORY_GATED | Hash integrity exists for evidence; document analysis, signature validation and QTSP status do not |
| 13 events | IMPLEMENTED_UNVERIFIED | `credential.status_changed` is emitted and signed events exist for status changes; the other event families and the outbound dead-letter redrive are owned by the webhook module and were not changed. Inbound source events that cannot be applied are dead-lettered (`credential_0009`) |
| 14 error model | VERIFIED_IN_CODE for invalid_request, idempotency_conflict, unsupported_market and not-found (tested over HTTP); the other mapped names (provider_unavailable, quota_exceeded, unauthenticated, scope_forbidden) are unit-tested only | The document's codes are mapped from this platform's own codes. A source that cannot be asked is returned as outcome UNAVAILABLE with a fault code, not as HTTP 424 |
| 14.2 SLOs | SPECIFIED_ONLY | Proposed targets; no measured baseline exists |

## Section 16 acceptance matrix

29 of 36 scenarios are linked to a real test in `docs/traceability/acceptance-tests.csv` (`INT2-T01` to T18 except T19 and T20, then T21 to T23, T26, T28, T29, T31 to T33, T35, T36), each IN_PROGRESS; none is VERIFIED because VERIFIED needs reviewer sign-off. Not linked, and why: T19 and T20 are provider-specific (Sumsub, Yoti) and no adapter exists; T24 and T25 are Rooms (no module); T27 needs certificate time-of-signing validation (DTV-02); T30 needs LEI/vLEI sources; T34 (cross-tenant raw evidence export and broad admin override) has route-level tests in the evidence module but none I could name for this exact scenario without guessing. T06, T32 and T36 are linked to the nearest real test and the row says what that test does and does not cover.

## Decisions Document 02 says to sign, not invent
Launch wave and source coverage by country and profession; whether Zoiko iD seeks regulated trust-service authorization; controller and
processor allocation for Zoikorum and Zoiko Rooms; public trust-label wording; issuer and registry contracts and prices; the approved SKU map
(see the CR-070 proposal); key custody, residency and retention periods.
