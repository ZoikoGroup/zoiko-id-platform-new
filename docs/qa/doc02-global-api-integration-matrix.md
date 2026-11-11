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
| Consumer bind (Zoikorum) | `POST /v2/zoikorum/engagement-checks` | IMPLEMENTED_UNVERIFIED as a route | The engagement logic is tested through the v1 service (`test_credential_trust_pg.py`); the `/v2` route itself has no HTTP test yet | One decision per requested service; the overall answer is the worst of them; signed for Zoikorum alone |
| Consumer bind (Rooms) | `POST /v2/rooms/property-checks` | SPECIFIED_ONLY | No property, title or mandate source or module exists |
| Cases | `POST /v2/verifications` | SPECIFIED_ONLY as a v2 route | The v1 verification module exists; a facade is not written |
| Evidence | `/v2/evidence/uploads`, manifest | SPECIFIED_ONLY | |
| Documents | `/v2/documents/validate` | PROVIDER_DEPENDENT | Signature and seal validation needs a PAdES/XAdES library and trust lists (DTV-02) |
| Issuer outreach | `/v2/issuer-checks` | IMPLEMENTED_UNVERIFIED as v1 | v1 outreach exists and is tested; no v2 facade |
| Business / authority | `/v2/organizations/verify`, `/v2/mandates/verify` | SPECIFIED_ONLY as v2 | v1 mandates exist; KYB sources do not |
| Presentations | `/v2/presentations/requests` | SPECIFIED_ONLY as v2 | A v1 OID4VP verifier exists in the credential module |
| Events | `/v2/webhook-endpoints`, `/v2/events/{id}` | SPECIFIED_ONLY as v2 | A v1 webhook module exists |

## Sections 05 to 14

| Area | Label | Evidence or gap |
|---|---|---|
| 05.1 SDK state machine | VERIFIED_IN_CODE (TypeScript) | `sdks/typescript/src/session.ts` and its tests: no capture before consent, a corrupt payload retries, OCR never completes a session, an outage stays pending, a human decision needs an authority, no FAILED state. Python mirror and the capture UI are not built |
| 06.1 outcome vocabulary | VERIFIED_IN_CODE | `v2_view.py` can only restate or lower a decision (a test runs every decision). Engagement eligibility is a separate vocabulary, proven disjoint |
| 07 pack contract and fail-closed rules | IMPLEMENTED_UNVERIFIED | The service pack and trust registries exist (CR-069). Dry-run scenario comparison and a tested rollback path are not built |
| 08 ProviderAdapter | PROVIDER_DEPENDENT | No provider contract, entitlement or sandbox is available, so no adapter was written (a stub would imply a capability that does not exist) |
| 09 documents and signatures | PROVIDER_DEPENDENT / REGULATORY_GATED | Hash integrity exists for evidence; document analysis, signature validation and QTSP status do not |
| 13 events | IMPLEMENTED_UNVERIFIED | `credential.status_changed` is emitted and signed events exist for status changes; the other event families and the outbound dead-letter redrive are owned by the webhook module and were not changed. Inbound source events that cannot be applied are dead-lettered (`credential_0009`) |
| 14 error model | VERIFIED_IN_CODE for invalid_request, idempotency_conflict, unsupported_market and not-found (tested over HTTP); the other mapped names (provider_unavailable, quota_exceeded, unauthenticated, scope_forbidden) are unit-tested only | The document's codes are mapped from this platform's own codes. A source that cannot be asked is returned as outcome UNAVAILABLE with a fault code, not as HTTP 424 |
| 14.2 SLOs | SPECIFIED_ONLY | Proposed targets; no measured baseline exists |

## Section 16 acceptance matrix

18 of 36 scenarios are linked to a real test in `docs/traceability/acceptance-tests.csv` (`INT2-T01`, T02, T03, T08 to T15, T21, T23, T26, T28,
T31, T33, T35), each IN_PROGRESS; none is VERIFIED because VERIFIED needs reviewer sign-off. Not linked, and why: T04 policy rollback, T05
lookalike issuer, T06 diploma without a conferral source, T07 accreditation at award date (logic exists, no test is named for the scenario), T16
forged webhook signature, T17 provider contract expiry, T18 failover reducing assurance, T19 to T20 provider-specific, T22 restrictions mapped to
activities, T24 and T25 Rooms, T27 certificate time, T29 selective disclosure, T30 LEI, T32 appeals access, T34 admin override, T36 provider
invoicing. Several of those have a unit test that nearly matches; linking requires a person to confirm the match rather than guess.

## Decisions Document 02 says to sign, not invent
Launch wave and source coverage by country and profession; whether Zoiko iD seeks regulated trust-service authorization; controller and
processor allocation for Zoikorum and Zoiko Rooms; public trust-label wording; issuer and registry contracts and prices; the approved SKU map
(see the CR-070 proposal); key custody, residency and retention periods.
