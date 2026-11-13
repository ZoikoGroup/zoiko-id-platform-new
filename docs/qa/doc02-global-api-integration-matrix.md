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
| Consumer bind (Rooms) | `POST /v2/rooms/property-checks` | VERIFIED_IN_CODE (own process), no property source | Superseded: see the Rooms row further down (`credential/api/v2_authority.py`); no land register or marketplace source is connected |
| Cases | `POST /v2/verifications`, `GET`, `/tasks/{check}`, `/cancel` | VERIFIED_IN_CODE | `credential/api/v2_cases.py`, `credential_0010`, `test_v2_cases_pg.py`. Each requested check is its own task with its own outcome; the case is never "passed". A market with no pack for any requested check is 422 `service_unavailable_in_market` and creates nothing. An identity task links to a /v1 identity verification session and completes only when that session is final. A finished case answers 409 `incompatible_state`. `callback_ref` must be an active webhook endpoint of the tenant; deliveries follow that endpoint's subscriptions (`verification.case.completed`). A signed `resume_token` is not issued (the document says only "if applicable") |
| Evidence | `GET /v2/evidence/{ref}/manifest`; `POST /v2/evidence/uploads`, `PUT .../uploads/{id}/content`, `DELETE /v2/evidence/{ref}` | VERIFIED_IN_CODE (needs a configured scanner and retention to accept anything) | `evidence/application/uploads.py`, `evidence_0005` to `0007`, `test_v2_uploads_pg.py`, `test_evidence_intake.py`. Bytes are kept in the cell database (`evidence.upload`), only after a CLEAN scan, and the table CHECK refuses bytes for any other state. A short-lived signed token binds the upload to its exact size and SHA-256. Type is sniffed from the bytes, JPEG/PNG metadata is stripped, and the ORIGINAL bytes are scanned (a test found that scanning the cleaned copy let a marker hidden after an image's end through). Scanning is real clamd (`ZID_PROOF_SCANNER_ADDRESS`, the same address proof documents use); local and test use an EICAR-only simulator; a deployed cell with no scanner refuses uploads. Retention comes from `ZID_UPLOAD_RETENTION_DAYS` and an unset value refuses uploads (the privacy officer's number is never invented). Delete and expiry erase the bytes; erasure of a person removes their uploads. The check and document routes accept an `evidence_ref` only when it is READY and CLEAN. Still needed from people: the actual clamd, the retention value, and a privacy-officer review of D3 document storage |
| Documents | `POST /v2/documents/validate`, `GET /v2/documents/{ref}` | Integrity, type and PDF signature validation VERIFIED_IN_CODE; qualified status VERIFIED_IN_CODE only against an operator-supplied authenticated trusted list; issuer authentication still PROVIDER_DEPENDENT | `evidence/application/cms.py` (PAdES/CMS on `cryptography` only), `trust.py`, `signatures.py`, `test_pdf_signatures.py` (27), `test_trusted_lists.py` (14), an end-to-end HTTP test. A signature is verified for real: byte ranges cover the file, the CMS digest matches, the signature value verifies (RSA, ECDSA; SHA-256 and up; SHA-1 and PSS are refused), and the chain builds to an anchor the operator configured. An RFC 3161 timestamp verified by the repo's own verifier fixes the signing time (so a later expiry is not held against an old signature, and T27 passes); operator-supplied CRLs and saved OCSP responses (config/trust/ocsp, issuer or delegated OCSP-signing certificate, current, SHA-1 refused) check revocation. The result is plain VERIFIED only with every proof, otherwise VERIFIED_LIMITED naming what is missing; with nothing configured (the shipped state) it is INDETERMINATE `NO_TRUST_ANCHORS_CONFIGURED`. Qualified status needs a trusted list whose own XML signature verifies against a pinned signer, a granted CA service at the signing time and the certificate's QC statements; otherwise NOT_ASSESSED. Not done: name and policy constraints, standalone CAdES files (XAdES now validates through application/xml; see xades.py), long-term-validation archives (DTV-04), and any claim that a valid signature is lawful authority. `config/trust/` ships empty: someone must supply anchors, trusted lists, TSA roots and CRLs |
| Issuer outreach | `/v2/issuer-checks` | VERIFIED_IN_CODE (`/v2/issuer-checks`) | The nonce is shown once and the stored replay record never holds it; an unknown or lookalike issuer is never contacted |
| Business / authority | `/v2/organizations/verify` | VERIFIED_IN_CODE (register lookup only) | `onboarding/api/v2_organizations.py` over the existing company-verification rules and sources (Companies House with a key, GLEIF, Sirene without one); stateless, one 06.1 outcome, no person named, no authority implied; `test_v2_organizations_pg.py`. Countries with no configured source are refused; Creditsafe stays DISABLED_UNTIL_CONTRACT |
| Business / authority | `/v2/mandates/verify` | VERIFIED_IN_CODE (own process) | Superseded: see the mandates row further down |
| Presentations | `POST /v2/presentations/requests`, `GET /v2/presentations/{ref}` | VERIFIED_IN_CODE | `v2_presentations.py`, `test_presentations_v2_pg.py`. `configuration_id` is an explicit extra field (never inferred). `expires_in_seconds` can only SHORTEN the request (1 to 600); a longer life is refused. Same verifier rules as v1 |
| Events | `/v2/webhook-endpoints`, `/v2/events/{id}` | VERIFIED_IN_CODE (`/v2/webhook-endpoints`, `/v2/events/{id}`, replay) | `webhook/api/v2.py`, `test_webhook_v2_pg.py`; same destination rules as v1. `credential.status_changed` is now subscribable |

## Sections 05 to 14

| Area | Label | Evidence or gap |
|---|---|---|
| 05.1 SDK state machine | VERIFIED_IN_CODE (TypeScript, Python, Go, Java and .NET) | `sdks/typescript/src/session.ts`, `sdks/python/.../session.py`, `sdks/go/session.go`, `sdks/java/.../Session.java`, `sdks/dotnet/src/Session.cs` and their tests: no capture before consent, a corrupt payload retries, OCR never completes a session, an outage stays pending, a human decision needs an authority, withdrawing consent closes capture. The Go, Java and .NET clients were compiled and tested in Docker (golang:1.23, maven/temurin 21, dotnet sdk 8.0): 10, 9 and 9 tests. They have not been used against a running platform |
| 06.1 outcome vocabulary | VERIFIED_IN_CODE | `v2_view.py` can only restate or lower a decision (a test runs every decision). Engagement eligibility is a separate vocabulary, proven disjoint |
| 07 pack contract and fail-closed rules | VERIFIED_IN_CODE for dry run and rollback | `verification/domain/pack_diff.py`: compares two registries over every scenario, lists widening changes for the approver, proves a rollback answers identically. The maker/checker approval workflow around it is existing CR-069 work |
| 08 ProviderAdapter | Contract and guards VERIFIED_IN_CODE; every adapter PROVIDER_DEPENDENT | `verification/domain/provider_adapter.py`: the adapter Protocol, entitlement checks (expiry, revocation, country, purpose, region, assurance), routing that ranks only lawful providers, a failover that cannot lower assurance without an approval, webhook verification (exact bytes, window, replay, key rotation) and a deletion statement that waits for the provider. No Veriff, Yoti or Sumsub adapter exists: that needs a contract and a sandbox |
| 09 documents and signatures | PROVIDER_DEPENDENT / REGULATORY_GATED | Hash integrity exists for evidence; document analysis, signature validation and QTSP status do not |
| 13 events | IMPLEMENTED_UNVERIFIED | `credential.status_changed` is emitted and signed events exist for status changes; the other event families and the outbound dead-letter redrive are owned by the webhook module and were not changed. Inbound source events that cannot be applied are dead-lettered (`credential_0009`) |
| 14 error model | VERIFIED_IN_CODE for invalid_request, idempotency_conflict, unsupported_market and not-found (tested over HTTP); the other mapped names (provider_unavailable, quota_exceeded, unauthenticated, scope_forbidden) are unit-tested only | The document's codes are mapped from this platform's own codes. A source that cannot be asked is returned as outcome UNAVAILABLE with a fault code, not as HTTP 424 |
| 14.2 SLOs | SPECIFIED_ONLY | Proposed targets; no measured baseline exists |

## Section 16 acceptance matrix

33 of 36 scenarios are linked (the final pass below adds T19 as an own-process half, T24, T25 and T30; this paragraph is the older count) to a real test in `docs/traceability/acceptance-tests.csv` (`INT2-T01` to T18 except T19 and T20, then T21 to T23, T26, T28, T29, T31 to T33, T35, T36), each IN_PROGRESS; none is VERIFIED because VERIFIED needs reviewer sign-off. Not linked, and why: T19 and T20 are provider-specific (Sumsub, Yoti) and no adapter exists; T24 and T25 are Rooms (no module); T27 needs certificate time-of-signing validation (DTV-02); T30 needs LEI/vLEI sources; T34 (cross-tenant raw evidence export and broad admin override) has route-level tests in the evidence module but none I could name for this exact scenario without guessing. T06, T32 and T36 are linked to the nearest real test and the row says what that test does and does not cover.

## Decisions Document 02 says to sign, not invent
Launch wave and source coverage by country and profession; whether Zoiko iD seeks regulated trust-service authorization; controller and
processor allocation for Zoikorum and Zoiko Rooms; public trust-label wording; issuer and registry contracts and prices; the approved SKU map
(see the CR-070 proposal); key custody, residency and retention periods.

## Added in the final Document 02 pass

| Item | Label | Evidence |
|---|---|---|
| `/v2` SDK clients | VERIFIED_IN_CODE | `sdks/python/src/zoiko_credentials/v2.py`, `sdks/typescript/src/v2.ts`: 25 routes, a REQUIRED idempotency key on every creating POST, RFC 9457 errors, upload helper that computes the exact size and digest. A test proves each route exists in the server's published OpenAPI, and the two clients are checked against each other |
| `eligibility.recomputed` event (section 13) | VERIFIED_IN_CODE | Emitted only when a decision for the same person, policy and kind differs from the previous one; ids, the two outcomes, reasons and the policy reference only (`test_credential_trust_pg.py`) |
| `evidence.retention_action` event (section 13) | VERIFIED_IN_CODE | Emitted when an upload is deleted or expires, once; it states only that THIS cell erased ITS copy and never claims a provider or backup did |
| `property.authority_changed`, `provider.entitlement_revoked` | `provider.entitlement_revoked` VERIFIED_IN_CODE (runtime registry `verify.provider_entitlement`); `property.authority_changed` announced from persisted property checks | Superseded by the rows further down |
| Section 14 `424 source_unavailable` | Decided: keep 201 | The check is recorded (signed result and receipt), so the answer stays 201 with outcome UNAVAILABLE and now carries `retry_guidance` (retryable, reason, fault, attempts, "nothing was decided about the person"). A 424 would discard a recorded check |
| Section 07 "no runtime rule edit without dual approval" | VERIFIED_IN_CODE by absence | Packs are versioned configuration reviewed with the code. `test_packs_have_no_runtime_edit.py` fails if any route can change a pack, so a future route must bring its own maker/checker first |
| Web console | VERIFIED_IN_CODE | Credentials admin gains an Evidence tab (upload, scan result, check, delete); `GET /v2/evidence/uploads` lists metadata only; the web BFF forwards `/v2/evidence/` and `/v2/documents/`; the API client reads RFC 9457 problems |
| DTV-19 age assurance route | VERIFIED_IN_CODE (self-declared dates never pass) | `POST /v2/age-checks` over `verification/domain/age.py`, `test_v2_age_pg.py`: a threshold answer (AT_LEAST, UNDER, UNDETERMINED) with no birth date, age or estimate returned or stored. A birth date typed by a caller is SELF_DECLARED and gives UNDETERMINED (`BIRTH_DATE_NOT_VERIFIED`) because no attested source is connected; an estimate from the caller's own model decides only when its margin plus a 2-year buffer is clear of the line. Still needed for AT_LEAST from a date: an attested birth-date source (a passport chip check, a government age service or a verified identity case) |


## Added in the open-source pass (everything here uses free components; nothing needs a vendor contract)

| Item | Status | Evidence and honest limits |
|---|---|---|
| PDF signature validation, trusted lists, TSA time, CRLs | VERIFIED_IN_CODE | See the Documents row. `trust_refresh.py` fills `config/trust/` from the EU lists once the operator confirms the list-of-lists signer against the Official Journal; tested with synthetic lists (`test_trust_refresh.py`). It has NOT been run against the live EU server: the network was unreliable when tried. Nothing is trusted until the operator runs it |
| Malware scanning | VERIFIED_IN_CODE against real ClamAV | `docker compose --profile scan up -d clamd`, then `ZID_TEST_CLAMD=127.0.0.1:3310`: `test_clamd_real.py` passed against the genuine daemon (clean bytes, the EICAR file as a whole, a 3 MB multi-chunk stream, a dead port is SCAN_ERROR). Finding: ClamAV flags the EICAR file as a whole, whereas the local simulator flags the string anywhere; the simulator is stricter. The container's signature database could not be refreshed in this environment, so it ran on the image's built-in copy |
| Upload retention | Engineering default only | The local compose file sets 30 days. It is NOT the privacy officer's decision: a deployed cell with none set still refuses uploads |
| `/v2/age-checks` (DTV-19) | VERIFIED_IN_CODE | See the DTV-19 row |
| `/v2/organizations/verify` | VERIFIED_IN_CODE | See the Business row |
| Go, Java and .NET SDKs | VERIFIED_IN_CODE (Docker-built, unit tests only) | `sdks/go`, `sdks/java`, `sdks/dotnet`: the /v2 client and session state machine, same rules as the Python and TypeScript clients |
| Spanish and French reason text | MACHINE DRAFT, unreviewed | All 60 reason codes have drafts in `credential/domain/reasons_es.py` and `reasons_fr.py`. `describe` reports `translation: MACHINE_DRAFT_UNREVIEWED` until a native-speaker and legal review adds the locale to `REVIEWED_LOCALES`. Dynamic codes (claim and status patterns) are still English only |

| Automated WCAG 2.2 AA audit of the public pages | VERIFIED_IN_CODE (automated only) | `frontend/tests/e2e/public-pages-a11y.spec.ts`: 8 pages, desktop and 320 px, Chromium and axe-core with colour contrast, 16 of 16 pass after two real defects were fixed. It does not replace a screen-reader run on real devices, a manual keyboard review or testing with disabled users |
| DPIA addendum | DRAFT, unsigned | `docs/privacy/dpia-addendum-v2-evidence-DRAFT.md` with proposed retention periods for the DPO. No sign-off is claimed |

Still not done, and not buildable from code: a human accessibility audit, the DPIA sign-off and the real retention value (the privacy officer), the regulated product lines, the price book, real provider adapters and the Rooms property sources.


## Completed without any third party (final pass, 2026-11-12)

This section supersedes the lines above where they disagree. The document's own rule is that a design is not evidence, so every row names its test.

| Item | Label | Evidence and honest limits |
|---|---|---|
| `POST /v2/mandates/verify` (section 03 and 10) | VERIFIED_IN_CODE (own process) | `credential/api/v2_authority.py`, `credential/domain/authority_check.py`, `test_authority_check.py` (19) and `test_v2_authority_pg.py` over HTTP with really signed, really uploaded PDFs. A mandate is read from evidence the caller uploaded and put through the same signature checks as `/v2/documents/validate`; the strongest result is VERIFIED_LIMITED (the document is as signed by a certificate this deployment trusts), never VERIFIED. A LEI is checked only for its own check digits (ISO 17442). **T30**: a well-formed LEI with no mandate leaves authority INDETERMINATE. Not done: an officer register to match the signatory (needs a source) |
| `POST /v2/rooms/property-checks` (section 11) | VERIFIED_IN_CODE (own process), no property source | Dimensions are separate: ownership from title evidence, mandate (required for agents), permission to list (required for sublets), tenant legal route. **T24**: an official title without a mandate leaves an agent's listing authority UNKNOWN; with a mandate it is REVIEW_REQUIRED, never ELIGIBLE. **T25**: England's route is UNAVAILABLE (needs a registered provider; Zoiko iD is not one) and no other place, Scotland and Wales included, borrows it. No land register is configured, so title is at best a signed document. The overall answer is capped at REVIEW_REQUIRED |
| `issuer` check of `/v2/documents/validate` (section 09) | VERIFIED_IN_CODE | An optional `issuer_claim` resolves the name in the issuer registry (lookalikes are not resolved) and binds the signer only through a certificate the owners recorded; a good signature from an unrecorded certificate is INDETERMINATE (T26 over HTTP). With no claim the answer is unchanged (UNAVAILABLE, NO_ISSUER_SOURCE). Needs: real issuers recorded with their seal certificates |
| `analysis` check (section 09, "document analysis") | VERIFIED_IN_CODE | `evidence/domain/analysis.py`: deterministic signals (incremental updates, script and launch actions, embedded files, editing tool named, modified after creation, data after the image end, EXIF). Always INDETERMINATE: no signals is not genuine, signals are not forgery. This is the own-process half of T19; a provider's document intelligence remains PROVIDER_DEPENDENT |
| T27 | Linked | `test_pdf_signatures.py::test_t27_a_verified_timestamp_fixes_the_signing_time`. The earlier text that says it is not linked is out of date |
| Veriff and Sumsub | Clients exist, never run against a vendor | `verification/infrastructure/providers.py` (identity-session creation with signing and an egress guard) from the earlier ZID-EDR-005 work. They are not behind the new `ProviderAdapter` interface, there is no Yoti client, and nothing here claims a contract or a sandbox run. The earlier line that says no adapter exists is out of date |
| Section 09 signatures, archive and LTV | VERIFIED_IN_CODE | PAdES, XAdES (more than one signature), CAdES, OCSP and CRL revocation, embedded long-term evidence, document timestamps and the archive chain; see the Wave 0 matrix, eleventh to fourteenth passes. The Documents row near the top of this file is superseded |

### Still open in Document 02, and why

| Item | Why |
|---|---|
| Rooms `property.authority_changed` event | Needs persisted checks to detect a change; the Rooms routes are stateless |
| `provider.entitlement_revoked` event | Needs a runtime provider registry, which exists only as config and guards |
| Browser SDK widget (`session.mount`) | Not built: client libraries and the state machine exist, not a mountable accessible widget with localization |
| Section 14.2 SLOs | No measured baseline; needs load tests and probes against a deployed environment |
| Section 16.1 release evidence | Generated client types, Zoikorum and Rooms consumer contract tests, an independent accessibility audit, a signed DPIA, a security report and an incident drill |
| T20 (Yoti and Right to Rent record the provider) and T34 (cross-tenant raw export) | T20 needs a registered provider; T34 has route-level tests but no one named for this exact scenario, and linking one without being sure would be guessing |
| Real sources: land and title registers, officer registers, regulators, universities, employers, insurers | Contracts and entitlements; every one is a configured source, none is built in |

## Closed after the final pass (2026-11-12, own processes only)

The items the previous section listed as open and that need no third party or deployed environment are now built and tested. What is still open is at the end.

| Item | Label | Evidence and honest limits |
|---|---|---|
| `property.authority_changed` (section 13) | VERIFIED_IN_CODE | `credential/application/property_state.py`, migration `credential_0012` (`cred.property_check_state`: references and outcomes only). The property-check route now stores its outcomes per property and host and emits one event when the overall or any dimension moved; a first, repeated or other-property check emits nothing. `test_v2_events_pg.py`. The response states `authority_changed_event_emitted` |
| `provider.entitlement_revoked` (section 13) | VERIFIED_IN_CODE | Runtime registry `verify.provider_entitlement` (migration `verification_0006`), routes in `verification/api/v2_providers.py`: record, list, suspend, revoke, and a routing dry run over the existing `provider_adapter.route`. Revoked is final (database trigger) and a new period is a new record; the event carries ids, provider, service and previous status, never free text. This is a SCOPE registry: it holds no contract or credential and does not make a provider usable (no real adapter is connected) |
| Browser SDK widget `session.mount` (section 05) | VERIFIED_IN_CODE | `sdks/typescript/src/widget.ts`, 13 tests in `widget.test.ts` under jsdom. Consent first, no capture before it, withdrawal while capture is open, an issuer outage is pending and never "failed", native labelled controls, polite status and alert regions, focus moves to the heading, text only (never markup), English plus Spanish and French DRAFTS marked unreviewed. **Not done:** an independent accessibility audit with assistive technology, and browser testing beyond jsdom |
| Generated client types (section 16.1) | VERIFIED_IN_CODE | `frontend/packages/api-client/openapi/codegen.mjs` regenerates the console types and now also writes `sdks/typescript/src/generated/{credentials,evidence,verification}.ts` from the published contracts. Rerunning it also refreshed console types that had drifted behind the contracts (authentication, onboarding, privacy, webhooks, workload identity) |
| Zoikorum and Rooms consumer contract tests (section 16.1) | VERIFIED_IN_CODE (against the published contract) | `contracts/consumers/*.yaml` state what each marketplace relies on; `test_consumer_contracts.py` fails if the published OpenAPI or AsyncAPI stops providing a route, request field, required idempotency key or event, and the Rooms response shape is checked against a real answer. These are written by us from the document, not agreed with or run by the marketplaces' own teams |
| T34 | Linked | `test_t34_another_tenants_administrator_cannot_read_export_or_remove_raw_evidence` (two real tenants over HTTP) |
| Section 14.2 SLOs | Probe built; LOCAL baseline only | `zid_platform/slo_probe.py` (standard library; p95 <= 400 ms and availability >= 99.95%, exits 1 on a miss) and `test_v2_latency_baseline_pg.py`. Local in-process p95 on this machine: coverage 51 ms, age check 155 ms, property check 172 ms, overall 155 ms. **That is not release evidence:** it needs load at expected traffic and the probe run continuously from two regions against a deployed environment with burn alerts |

### Still open after this

| Item | Why |
|---|---|
| T20 (a registered Right to Rent provider is recorded) | Needs a real registered provider; the registry above can record an entitlement but nothing here is, or pretends to be, one |
| Deployed SLO evidence | A deployed environment, two probe regions, burn alerts, load tests |
| Independent accessibility audit, signed DPIA, security report, incident drill | People and sign-off, not code |
| Marketplaces running the consumer contracts themselves | Zoikorum and Rooms teams |
| Real sources and vendor runs (land, title and officer registers, regulators, universities, employers, insurers; Yoti, Veriff, Sumsub sandboxes) | Contracts, entitlements and sandboxes |


## Refresh (2026-11-13)

- SDK: `capture_alternatives` (Python) and `captureAlternatives` (TypeScript) call `GET /v2/capture-alternatives`; both clients list the route and the Python test checks it against the published OpenAPI. The Go (`CaptureAlternatives`), Java (`captureAlternatives`) and .NET (`CaptureAlternatives`) clients have it too, compiled and tested in Docker (golang:1.23, maven/temurin 21, dotnet sdk 8.0): Go ok, Java 10 tests, .NET 10 tests. Not used against a running platform.
- Provider kill switch: `GET /v2/providers/{provider_id}/entitlements/{service}/impact` (read-only) says, per country, purpose and region, whether suspending or revoking an entitlement would leave the scope LOST, DEGRADED or REROUTED (Document 03 RCG-G08).
- The three "SPECIFIED_ONLY" rows near the top for Rooms, mandates and the two events were already out of date; they are corrected above.
