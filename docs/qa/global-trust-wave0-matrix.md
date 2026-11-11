# ZID-GAP-001 v2.0 Wave 0 baseline (DTV-01..35, GID-036..065)

## What this is

The "Wave 0 · Baseline" deliverable of Document 01 v2.0 (Nov 10, 2026): each of the 35 earlier packages and the 30 new global packages mapped to what exists in this repository. It was built by reading files and searching for names. It is a map, not an assurance statement.

Rules used:

- No test was run and no server was started. "Test seen" means a test file or test name exists, not that it passes.
- Statuses are the document's own: `PROVEN_REUSE` (code and a test exist for the expected contract), `DELTA` (part exists; the gap is named), `PROPOSAL` (nothing found in code; only the document), `BLOCKED` (needs an external provider, legal status or certification, so code alone cannot close it).
- "Nothing found" means a keyword search of `backend/app/src` found nothing. It is not proof of absence, so confirm before building.
- Paths are relative to `backend/app/src/zid_platform/modules/` unless they start with `docs/`, `tests/` or `frontend/`. Unit tests are under `backend/app/tests/unit/`.
- Owner and version columns the document asks for are left to the architect and engineering lead (§14.1 "Architect + engineering lead attest actual scope").

## What the repository is, in one paragraph

Zoiko iD today is an authority, authentication, verification-orchestration and evidence platform. Identity proofing is delegated to providers (Veriff, Sumsub, a generic signed-JSON adapter) and company checks run against registers (`organization/domain/kyb.py`). The `credential` module is a generic verifiable-credential stack (OID4VCI issuance, OID4VP presentation, SD-JWT VC, mdoc, W3C VCDM 2.0, Token Status Lists, typed issuer trust). There is **no** academic, professional-license, certification, CPD, employment, insurance, disciplinary or "Zoikorum" code anywhere in the backend. That whole credential-trust domain (GID-037 to 049) is the real Wave 1 build.

## Part A: DTV-01..35

| ID | Capability | Status | Evidence found | What is missing |
|---|---|---|---|---|
| DTV-01 | Document ingest | DELTA | `organization/infrastructure/proof_scan.py` (clamd INSTREAM scanner; UNAVAILABLE refuses in deployed envs); `tests/unit/test_kyb_proof.py` | Covers company proof uploads only. No general tenant document store with content-type allow-list and hashing for credential or identity documents. |
| DTV-02 | Signature validation (PAdES/XAdES/CAdES/ASiC) | PROPOSAL | Nothing found. JWS and SD-JWT proof checking in `credential/` is a different thing. | Document signature, chain and revocation validation. |
| DTV-03 | Qualified sign/seal | BLOCKED | `credential/domain/credentials.py` lists `QTSP` in `DEFERRED_ROLES` (refused by design, FR-VC-008) | Needs an authorized partner. |
| DTV-04 | Long-term preservation | DELTA | `evidence/` (ledger, retention, vault, `evidence/infrastructure/tsa.py` timestamp authority) | A validation archive for signed documents; depends on DTV-02. |
| DTV-05 | UK Right to Rent rules | PROPOSAL | Nothing found | Market pack (Document says: not the default global policy). |
| DTV-06 | Identity document / liveness | DELTA | `verification/domain/providers.py`, `verification/infrastructure/providers.py` (Veriff, Sumsub, generic; callbacks authenticated on the raw body; unknown status is PENDING); `tests/unit/test_idv_providers.py` | OCR, NFC and liveness happen provider-side. The file itself says the Sumsub webhook digest header and fraud-label meanings are NOT confirmed against a sandbox. No abuse tests seen. |
| DTV-07 | Certified provider registry | DELTA | `verification/api/verification.py` `/verification-adapters` (register, quarantine); certified marker only per configured country (providers.py header) | No entity + service + jurisdiction + scheme-version + current-status record (Doc 03 "exact-service registry"). |
| DTV-08 | Right to Rent evidence | PROPOSAL | Nothing found | Market pack. |
| DTV-09 | Human review | DELTA | `verification/api/verification.py` `/verification-review-cases/{id}/decisions`; `verification/application/service.py` ("one decision per reviewer, dual control per profile"); org-claim appeals in `onboarding/api/org_claims.py`; screening review; operator console | Test names for dual control and appeal not individually confirmed. No appeal route for credential or eligibility decisions (see GID appeals API). |
| DTV-10 | Zoiko Rooms authority | PROPOSAL | No property, landlord or title code. Generic `mandate/` ledger is a reuse candidate. | Property title and permission-to-let checks (consumer scope). |
| DTV-11 | API / SDK / events | DELTA | `contracts/{openapi,asyncapi}`; `webhook/` (signed deliveries, replay of FAILED/EXHAUSTED, key rotation); `tests/unit/test_webhooks.py`, `test_webhook_key_rotation.py` | `sdks/` is empty. Connector set is small. |
| DTV-12 | Privacy / fairness | DELTA | `privacy/` (retention, data rights, residency profile SRP-0..3); `docs/privacy/device-cookie.md` | No fairness or cohort-testing code, no DPIA artifact for the verification flows (that is Doc 04's scope). |
| DTV-13 | Usage / billing / SLA | PROVEN_REUSE | `metering/`, `commercial/` (63 files: rating, entitlements, reservations, orders); `tests/component/test_entitlement_bands_pg.py` | Provider cost reconciliation not confirmed. |
| DTV-14 | Own UK DVS status | BLOCKED | n/a | Independent certification. |
| DTV-15 | Own EU QTSP status | BLOCKED | n/a | Authorization and trust-list entry. |
| DTV-16 | General document intelligence | PROPOSAL | Nothing found (delegated to providers) | Templates, extraction, issuer check, tamper detection. |
| DTV-17 | Non-document identity / address | PROPOSAL | Nothing found | Licensed sources. |
| DTV-18 | Video / NFC / eID | DELTA | `credential/infrastructure/mdoc.py` (ISO 18013-5 presentation verification) | No video-assisted or NFC flow, no operator flow. |
| DTV-19 | Age assurance | PROPOSAL | Nothing found | Age-range or threshold assertions. |
| DTV-20 | Phone / email intelligence | DELTA | Email proof at registration; `verification/domain/tiers.py` states phone and email are never identity proof | No phone-intelligence signal. |
| DTV-21 | Device intelligence | DELTA | Device cookie to `X-Zid-Device` (`docs/privacy/device-cookie.md`); velocity signals in `screening/domain/signals.py` (CR-068) | Default mode is `shadow`; basis for the cookie awaits DPO/Legal. |
| DTV-22 | Linked fraud graph | DELTA | `screening/domain/signals.py`, reviewer routes `/v1/reviews/signals` (CR-068); tenant isolation tests | Velocity counters only, no graph. Nothing consumes the `screening.signal.raised` event. |
| DTV-23 | Reusable credentials | PROVEN_REUSE | `credential/` (purpose and recipient-class registered disclosure, `minimize()`, revoke / suspend / reinstate, status lists); `tests/unit/test_vc_verify_rules.py` | Freshness per recipient is not modelled (see GID-053). |
| DTV-24 | Advanced KYB / UBO | DELTA | `organization/domain/kyb.py`, `screening/domain/ownership.py`; `tests/unit/test_kyb*.py` | Official-register routing seen for one namespace (`GB-COH` in `country_gate.py`); supplier coverage per country not proven. |
| DTV-25 | AML screening | DELTA | `screening/` (lists, fuzzy name matching, cases, re-screen on list change, CR-068 PEP rules); `tests/unit/test_screening*.py` | No licensed PEP source or adverse-media vendor (Document: licensed watchlists). |
| DTV-26 | Open banking | PROPOSAL | Nothing found | Authorized provider. |
| DTV-27 | Payment ownership | PROPOSAL | Stripe is wired for billing only (see `stripe-integration` notes); no payment-method ownership verification | Tokenized ownership check. |
| DTV-28 | Questionnaires and funds | PROPOSAL | Nothing found | Versioned SoF/SoW evidence. |
| DTV-29 | Continuous re-verification | DELTA | Worker steps: tenant `reverify_due`, screening re-screen, agent lifecycle (`entrypoints/worker.py`); `/verifications/{id}:reverify`; credential revoke and status lists | No issuer or registry change trigger, because no issuer sources exist yet. |
| DTV-30 | Workflow studio | DELTA | `policy_authoring/` (versioned policies), `/verification-profiles` with activate | Simulation, dual approval and rollback not confirmed. |
| DTV-31 | Compliance case operations | DELTA | Review cases, screening cases, operator console | Assignments and reports not confirmed. |
| DTV-32 | Transaction monitoring | PROPOSAL | Nothing found | Separately gated product line. |
| DTV-33 | Crypto Travel Rule | BLOCKED | n/a | VASP partner and jurisdiction. |
| DTV-34 | On-chain screening | BLOCKED | n/a | Analytics partner and legal gating. |
| DTV-35 | Agent controls | PROVEN_REUSE | `machine/` tool gateway (CR-067); tests listed in `docs/qa/gap-implementation-matrix.md` Part A rows 6 and 7 | No MCP-specific scope seen; no red-team prompt corpus; `sdks/` empty. |

## Part B: GID-036..065

| ID | Capability | Status | Evidence found | What is missing |
|---|---|---|---|---|
| GID-036 | Global operating policy packs | DELTA | `verification/domain/countries.py` + `country_gate.py` + `/v1/countries`: default DISABLED, six go/no-go approvals, never inferred from IP; `tests/unit/test_country_capabilities.py` | Registry is country x capability only. The document's `CountryServicePack` needs profession, service code, legal basis, source registry ids, residency region, retention and review rule ids, policy hash, and states `LIVE_PROVEN / PARTNER_ONLY / STAGING / DESIGN / BLOCKED` (repo has `DISABLED / SANDBOX / ENABLED`). Stale-policy and old-pack negative tests absent. |
| GID-037 | Credential taxonomy and claims | DELTA | `credential/` `Configuration{credential_type, required_claims, ...}` and `/credential-configurations` are a container for types | No academic, license, certificate, CPD, employment, insurance or disciplinary schema. No evidence-tier vocabulary (SELF_DECLARED ... CONSENTED_PORTABLE_ASSERTION). No "unrecognized class fails closed" test. |
| GID-038 | Issuer / registry trust graph | DELTA | `credential/domain/credentials.py` `IssuerTrust` (per credential type and jurisdiction, status, `valid_to`; unknown issuer is untrusted even with a valid signature); `/credential-issuer-trust` incl. key rotation | Models VC issuers only. No regulator or registrar entities, accreditation history, renamed or historical identity, coverage, or lookalike resolution (`/v1/issuers/resolution`). |
| GID-039 | Academic qualification verification | PROPOSAL | Nothing found | Whole feature. |
| GID-040 | Professional license standing | PROPOSAL | Nothing found | Whole feature. |
| GID-041 | Jurisdiction practice eligibility | PROPOSAL | The PDP in `decision/` decides authorization, not legal practice eligibility | Country / state / profession / service decision with its own attestation. |
| GID-042 | Certification and vendor credential | PROPOSAL | Nothing found | Whole feature. |
| GID-043 | CPD | PROPOSAL | Nothing found | Whole feature. |
| GID-044 | Employment and experience | PROPOSAL | `principal/` has workforce HR lifecycle data, which is internal, not verification of a candidate's history | Whole feature. |
| GID-045 | Accreditation / recognition | PROPOSAL | Nothing found | Recognition vs equivalence rules (needs legal input). |
| GID-046 | Regulatory disciplinary checks | PROPOSAL | `screening/domain/matching.py` is a reuse candidate (a fuzzy hit is an alert, not a finding) | Regulator sources, sanction scope, appeal flag. |
| GID-047 | Professional indemnity / insurance | PROPOSAL | Nothing found | Whole feature. |
| GID-048 | Zoikorum trust dimensions, Tier A/B/C | PROPOSAL | No Zoikorum code. `verification/domain/tiers.py` (R0..R3) is an unrelated risk-tier ladder | Separate per-dimension status and tier rules. |
| GID-049 | Zoikorum engagement check | PROPOSAL | `decision/` PDP and `authority/` are reuse candidates | Credential + role + risk + jurisdiction + insurance evaluation. |
| GID-050 | W3C VC / Open Badges / CLR | DELTA | `credential/domain/vcdm.py` (VCDM 2.0), SD-JWT VC, mdoc, bitstring status list, holder binding, typed issuer trust; `tests/unit/test_vc_verify_rules.py` | Open Badges 3.0 and CLR 2.0 handling not found. A `vc.verify` route in the document's shape (`/v1/credentials/vc/verify`) is not there; verification runs through `/presentations`. |
| GID-051 | EU learning credentials (EDC) | PROPOSAL | Nothing found | Whole feature. |
| GID-052 | Global identifier matching | DELTA | `screening/domain/matching.py` (NFKD normalization, deterministic transliteration of common scripts, aliases, reviewer checks stay mandatory); `organization/domain/kyb.py` (unicodedata) | Not wired to credential holders; no human-appeal path for a name mismatch; no Arabic / old-name fixtures (GLB-08, GLB-24). |
| GID-053 | Credential reuse and portable dossier | DELTA | `credential/application/verifier.py` registered purpose and recipient class (PRIV-01), `minimize()`; `credential/infrastructure/privacy_adapter.py` | No per-recipient consent receipt, no derived-grant expiry on withdrawal, no portable dossier object. |
| GID-054 | Issuer outreach / manual verification | PROPOSAL | Nothing found | Signed request/response, contact validation, dual review, deadline. |
| GID-055 | Global source and contract registry | DELTA | KYB routing uses a supplier only where its country coverage is proven; country registry has a `supplier_coverage` approval | No registry of rights, permitted purposes, regions, refresh SLA, lawful transfer, price. |
| GID-056 | Cross-border privacy and residency | DELTA | Tenant residency profile SRP-0..3; processor approval refused on residency mismatch (`privacy/application/registry.py`, `PRIVACY_PROCESSOR_RESIDENCY`); cell-residency spec | No routing guard for source queries because there are no sources yet; no per-country transfer mechanism data. |
| GID-057 | Sanctions and sensitive source gating | DELTA | Screening review actions are permission-gated (`verification.screening.signal.read/review`) | No sector consent or legal-basis model; no criminal / disciplinary entitlement gate. |
| GID-058 | Credential lifecycle and alerts | DELTA | Revoke / suspend / reinstate with status lists; outbox event `credential.revoked` / `credential.status.changed` (`credential/application/issuer.py`); alert template in `notification/domain/alerts.py` | Event is spelled `credential.status.changed`, the document says `credential.status_changed`: reconcile in the contract. No source-change trigger, no decision recompute, no consumer invalidation (Tier A downgrade). |
| GID-059 | Evidence quality and explainability | DELTA | `evidence/domain/receipts.py` (classes, material inputs, minimization), `evidence/application/provenance.py` | No source tier, confidence, methodology or limitation fields; no rule that OCR alone cannot be shown as issuer confirmation. |
| GID-060 | Partner and issuer API integration | DELTA | Provider callbacks authenticated on the raw body, unknown status is PENDING; webhook retries, EXHAUSTED state, replay (`webhook/`) | Inbound ordering ("newest valid status wins") not seen; no rate controls on source calls; no issuer API client. |
| GID-061 | Public certificate and verifier portal | PROPOSAL | Presentation flow exists for relying parties but no public verifier page | Signed limited disclosure and a no-account verify page. |
| GID-062 | Accessibility and localization | DELTA | `docs/qa/device-accessibility-test-pack.md`; `communication/domain/render.py` (locale) | No WCAG 2.2 AA evidence, no RTL / non-Latin name tests, no accessible alternative to selfie capture confirmed. |
| GID-063 | Consumer SDK domain packs | PROPOSAL | `sdks/` is empty; no Zoikorum / Rooms consumer pack | Whole feature. |
| GID-064 | Service packaging and metering | DELTA | `commercial/` price book, rating, entitlements; operator price-book editor; `/pricing` page and calculator in `frontend/apps/web` | GLB-35 (missing signed price book / FX / territory blocks activation) not confirmed as a test. Prices need CFO/CCO approval. |
| GID-065 | Claims and market readiness gates | DELTA | Country registry six-approval gate blocks an unapproved country | No gate on public claims; no `PARTNER_ONLY / NO_COVERAGE` labels. |

## Count

| Status | DTV (35) | GID (30) | Total |
|---|---|---|---|
| PROVEN_REUSE | 3 | 0 | 3 |
| DELTA | 16 | 15 | 31 |
| PROPOSAL | 11 | 15 | 26 |
| BLOCKED | 5 | 0 | 5 |

## What this means for Wave 1

1. **Credential trust is greenfield.** GID-037 to 049 have no code. The cheapest home is the existing `credential` module (configuration registry, typed issuer trust, status lists, evidence receipts), not a new module, as the document requires.
2. **The reusable core is strong.** VC verification, issuer trust, status, minimization, country gate, residency profile, evidence receipts, matching, metering are built and have tests. Wave 1 extends them.
3. **Nothing in Wave 1 can go live without owner inputs.** Source and regulator contracts, an approved price book, legal recognition rules and the Zoikorum tier definitions are decisions in Document 01 section 15, not engineering work.
4. **Tests are the second gap.** The 36 GLB cases (section 13 of Document 01) have no fixtures yet. Each Wave 1 package should land with its GLB fixtures.

## Suggested Wave 1 order (for approval, not yet started)

1. GID-037 credential taxonomy and the five-field assertion contract (section 8.1), as claim types on the existing configuration registry.
2. GID-036 extend the country registry toward `CountryServicePack` with `feature_state` (additive; keep the current states working).
3. GID-038 issuer trust: add regulator / registrar issuers, accreditation history and alias resolution.
4. GID-039 / 040 / 041 academic, license and eligibility checks against a **simulator source** (as `verification/api/simulator.py` does for IDV), with GLB-01 to 05 as the tests. No real source can be added until a contract exists.

## Build progress (Wave 1)

Updated as slices land. A slice is "domain" when only the pure rules and unit tests exist; it is not "done" for a package until persistence, API, events, the contract catalogues and the GLB fixtures exist too.

| Package | Slice | State | Files | Tests |
|---|---|---|---|---|
| GID-037 | 1. Claim taxonomy, evidence tiers, five-field assertion, fail-closed decision (domain) | Built, 2026-11-11 | `credential/domain/claims.py` | `tests/unit/test_credential_claims.py` (29): GLB-02, 04, 05, 07, 13, 21, 22 rules |
| GID-036 | 1. `ServicePack` with feature states, resolve (most specific, version, dates, disabled country), publish checks (evidence, residency, source availability, content hash) (domain) | Built, 2026-11-11 | `verification/domain/service_packs.py` | `tests/unit/test_service_packs.py` (28): GID-036 negative proofs, GLB-28, GLB-29 |
| GID-037 | 2. `cred.claim_check` (migration `credential_0005`, RLS, immutable, supersession), claim check service with a local-only simulator source, six `POST /credentials/*/checks` routes, `GET /credentials/assertions/{id}`, action `credential.claim.verify`, event `credential.claim.checked`, CR-069 | Built, 2026-11-11 | `credential/{api,application,infrastructure}/claim*.py`, `migrations/credential/versions/0005_claim_check.py` | `tests/component/test_claim_checks_pg.py` (15, real Postgres) |
| GID-036 | 2. Config `backend/config/service-packs/service-packs.yaml` (all DESIGN, so nothing is live), loader, `GET /credential-packs` | Built, 2026-11-11 | `verification/infrastructure/pack_registry.py` | same component test |

Still open for these two: a LIVE or PARTNER_ONLY pack and a real source (owner decisions, GID-055); `GET /v1/credential-packs/{country}/{profession}` in the document's exact shape (today `GET /credential-packs` lists all); the six check routes are not in `docs/traceability/acceptance-tests.csv` yet; no operator console screen for packs.

Database: one database (`zid_cell`). `credential_0005` was applied to it. The old `zid_global` database was empty and was dropped on 2026-11-11.

## Status after the 2026-11-11 build (supersedes the Wave 0 statuses for these rows)

"Built" means code, migration where needed, tests that pass, catalogue entries and a CR exist. It does NOT mean live: every source is a simulator, every service pack is DESIGN, and every issuer and policy is a SAMPLE. Nothing here can return VERIFIED in a deployed environment until owners add real sources, issuers, packs and policies.

| Package | State | What exists | What is still open |
|---|---|---|---|
| GID-036 | Built | `service_packs.py`, config, `GET /credential-packs`, `GET /credential-packs/{country}/{profession}` | A LIVE pack needs a legal basis, owners, QA gate, sources and a content hash (owners) |
| GID-037 | Built | `claims.py`, `cred.claim_check`, six check routes, `GET /credentials/assertions/{id}` | none in code |
| GID-038 | Built | `trust_graph.py`, issuer registry config, `POST /v1/issuers/resolution`, `GET /v1/issuers/{id}/trust` | Real issuers and their legal authority (owners); no operator screen to edit the registry |
| GID-039 | Built (simulator) | Academic check: conferred vs attended, accreditation at award date flagged | A real registrar source |
| GID-040 | Built (simulator) | `standing.license_standing`, used by every license check | A real regulator source |
| GID-041 | Built | `eligibility.evaluate`, `POST /v1/eligibility/professional/evaluate`, append-only `cred.eligibility_decision` | Real policies per country, state, profession, service (Legal) |
| GID-042 | Built (simulator) | Certificate expiry and retired-program handling | Vendor sources |
| GID-043 | Built | CPD gives INDETERMINATE unless the requirement and approved providers are configured | Regulator CPD rules (Legal) |
| GID-044 | Partial | Employment claim class, vanished employer is UNVERIFIABLE, `employment_disclosure` rule | The disclosure rule is not wired into the check; no employer source |
| GID-045 | Built | `trust_graph.recognition` (never automatic), used by eligibility | Real recognition rules (Compliance) |
| GID-046 | Partial | `standing.disciplinary_result` rules and tests | No check route and no regulator source; engagement treats "restricted" as false until one exists |
| GID-047 | Built | `insurance_result`, insurance check stores facts, engagement uses them | A real insurer source |
| GID-048 | Built | `tier_of`, tier returned by engagement and Zoikorum decisions | Zoikorum owner confirms the rule |
| GID-049 | Built | `POST /v1/eligibility/engagement/evaluate`, `POST /v1/consumers/zoikorum/decisions` (signed) | Identity input is the existing verification; no Zoikorum client exists |
| GID-050 | Partial | `evidence_quality.evaluate_vc` (Open Badge, CLR, VC rules) | No `vc/verify` route: it needs the signature check wired in; no Open Badge or CLR parser beyond field extraction |
| GID-051 | Partial | Same rules for a European Digital Credential (valid seal is never a license) | No route, no seal validation |
| GID-052 | Built | `identity_match.match_holder` (transliteration, former names need evidence) used when a source returns a subject name | Not exposed as its own route |
| GID-053 | Built | `consent_grant`, create / withdraw / read routes, children end with the parent | none in code |
| GID-054 | Built | `outreach.py`, `cred.outreach_request`, open / response / review routes | Issuer keys must be registered (`app.state.outreach_verifiers`); until then no answer is accepted. An accepted outreach is not yet turned into a claim check |
| GID-055 | Built | `sources.py`, `source-contracts.yaml`, enforced before every query | Real contracts (Legal, Procurement) |
| GID-056 | Built | Region and lawful-transfer check before every query | Real per-source region data |
| GID-057 | Built | Sensitive categories need a basis, an authorized user and a reviewer | No sensitive source exists |
| GID-058 | Built | Status event supersedes the check, `credential.claim.status.changed`, worker step `credential_recheck` | No real source pushes events yet |
| GID-059 | Built | Explanation stored with every check, appeal routes | none in code |
| GID-060 | Partial | Newest status wins, duplicates ignored, late events recorded | No outbound partner client, dead-letter or rate control (no partner exists) |
| GID-061 | Partial | `GET /credentials/assertions/{tenant}.{check}/verify` under a PUBLIC_VERIFIER grant, bounded view | No verifier web page |
| GID-062 | Not built | Name matching handles accents and Arabic / Cyrillic / Greek | No WCAG evidence, no RTL screens, no localization of reason codes, no accessible alternative to selfie capture |
| GID-063 | Not built | `sdks/` is still empty | Zoikorum, Rooms and partner SDK packs |
| GID-064 | Not built | Existing commercial module is untouched | No SKU or meter for credential checks; needs an approved price book (CFO, CCO) |
| GID-065 | Built | `coverage`, `refuse_unbacked_claim`; the simulator never counts; shipped config backs no coverage claim | No automated scan of marketing text |

Tests: unit 2782 passed, 1 skipped (full suite, before the last small edits; the affected files were re-run after); component `test_claim_checks_pg.py` 15 and `test_credential_trust_pg.py` 18 passed on Postgres. DTV-01..35 were not changed except where the GID work above reuses them.

Database: one database, `zid_cell`. Migrations `credential_0005` and `credential_0006` are applied to it.

## Status update, third pass (2026-11-11, later)

These rows replace the matching rows in the table above. Everything is still simulator or sample data; nothing is live.

| Package | State | What changed | What is still open |
|---|---|---|---|
| GID-044 | Built | The check keeps only the employment fields the employer permits (`Gate.employment_permitted_fields`); with no limit configured nothing about the job is kept | A real employer source |
| GID-046 | Built (simulator) | `POST /credentials/disciplinary/checks`: needs the `credential.disciplinary.check` action (R3), a consent or legal basis, a reviewer who is not the caller, a registered regulator and a source contract for the sensitive category; an adverse finding blocks an engagement, one under appeal holds it | A real regulator source and the sensitive-source legal basis (Legal) |
| GID-050 | Built | `POST /credentials/vc/verify` verifies a vc+jwt (VCDM 2.0, Open Badges 3.0 and CLR 2.0 when issued as vc+jwt) with the existing issuer keys, typed issuer trust, holder proof and bitstring status; the result is a claim, never a primary-source confirmation | Issuers must be added to the trust table; JSON-LD data-integrity proofs are not handled |
| GID-051 | Partial | A European Digital Credential is refused with `EDC_SEAL_VALIDATION_NOT_AVAILABLE` instead of being guessed | Qualified e-seal validation |
| GID-054 | Built | An accepted outreach becomes a primary-source claim check, a denial a no-record check, silence or a rejection UNVERIFIABLE; the claimed values are dropped when it is concluded (`credential_0007`, `POST /credentials/source-outreach/{id}:conclude`) | Issuer keys must be registered before any answer is accepted |
| GID-061 | Built | Public page `/verify/{tenant}.{check}?grant=...` in the web app; one message for every failure; wording never exceeds the evidence | none in code |
| GID-062 | Partial | `domain/reasons.py`: plain-language text for every reason code, a source scan that fails if a new code has no text, English fallback that reports the locale it really used; returned as `reason_text` | No translations (an owner task), no WCAG audit evidence, no accessible alternative to selfie capture (identity flow, not credentials) |
| GID-063 | Built | `zoiko_credentials` Python SDK in `sdks/python` with a Zoikorum pack (signed decisions verified offline) and a Rooms pack (never accepts a Zoikorum decision) | TypeScript, Go, Java and .NET SDKs are still empty folders |
| GID-064 | Not built | none | A credential-check meter needs a new meter in the commercial module and an approved price book; both are owner decisions |

Tests added in this pass: component `test_vc_verify_pg.py` (7), `test_credential_disciplinary_pg.py`, `test_credential_outreach_check_pg.py` (5); unit `test_reasons.py`; SDK `test_credentials_sdk.py`; web `features/verify/view.test.ts` (9). `credential_0007` is applied to the single `zid_cell` database.

### DTV rows changed in the third pass

| Package | State | What exists | What is still open |
|---|---|---|---|
| DTV-07 | Built | `verification/domain/certification.py` and `config/certifications/certifications.yaml`: a record per legal entity, scheme and version, place, exact service and period; a component certification never supports a full-service claim; `resolve(..., certifications=...)` makes a pack that names a regulatory scheme unavailable when no entity holds it in force, and only that pack | A real record needs independent evidence (owners); no operator screen |
| DTV-14 | Built (as a gate) | A claim by Zoiko iD of UK DVS status is refused: the shipped registry lists nothing | Actual certification is a business decision |
| DTV-15 | Built (as a gate) | The same for an EU qualified trust service provider claim | Actual authorization and trust-list entry |

Not touched and not buildable from code alone: DTV-02 (signature validation needs a PAdES/XAdES library and trust lists), DTV-03, DTV-16 to 19, DTV-26 to 28, DTV-32 to 34, DTV-05, 08 and 10 (market and consumer packs). Documents 03 to 05 beyond DTV-07 (DPIA sign-off, independent UAT, release authorization) are people and evidence, not code.

## Status update, fourth pass (2026-11-11, later still)

| Package | State | What changed | What is still open |
|---|---|---|---|
| GID-063 | Built for Python and TypeScript | `sdks/typescript` (`@zoiko/credentials`, no runtime dependencies, WebCrypto signature checks, 15 tests, tsc clean) | Go, Java and .NET: not built. Those toolchains are not installed on this machine, so nothing could be compiled or tested; I did not write untested code |
| GID-064 | Proposal only | `docs/specs/change-requests/CR-070-credential-check-meter-PROPOSAL.md` | The meter register is an owner-ratified record pinned by a test; a price book is a CFO/CCO decision |
| DTV-19 | Domain built, not wired to a route | `verification/domain/age.py`: threshold answers (at least / under / undetermined) with no birth date out, a self-declared date never passes, an estimate decides only when its margin plus a 2-year buffer clears the line, and a documented check is offered instead of a refusal | A route and a provider for estimation |
| Privacy | Gap found and fixed | The privacy module's erasure did not cover any `cred.*` data, and the claim-check tables are immutable by trigger. `credential_0008` adds `cred.erase_subject` (SECURITY DEFINER, current tenant only, idempotent) and the credential privacy adapter registers all six tables for discovery, export and erasure. Tested, including that ordinary deletes stay refused | Retention periods and rectification sources are DPO decisions |
| Documents 04/05 | Evidence prepared, sign-off pending | `docs/privacy/dpia-credential-trust-DRAFT.md` (from the code as built); the document's 36 GLB cases are in `docs/traceability/acceptance-tests.csv` (GLB-01 to 36, each linked to a real test, all IN_PROGRESS, none VERIFIED); `test_glb_invariants.py` proves GLB-25 structurally | DPIA sign-off and an independent UAT run are human steps |

Still not built, and why: DTV-02 (document signature validation needs a PAdES/XAdES library that is not installed and a trust-list source), GID-051 (EU e-seal validation, same reason), DTV-16 (document intelligence), DTV-26 to 28 and 32 to 34 (external providers or regulated product lines gated by Legal), translations and WCAG audit evidence (need translators and an audit), Go/Java/.NET SDKs (no toolchains here).

## Status update, fifth pass (2026-11-11, end of day)

| Item | State | What changed | What is still open |
|---|---|---|---|
| PDF gap 1 to 4 (paths, full 08.1 contract, signed binding, receipts, event name) | Built and tested | `/v1/credentials/...` paths; complete signed assertion; receipt per check and per status change; `credential.status_changed`; SDK `verify_assertion` with a shared golden digest | Signing needs a configured key; without one the signature is `null` |
| GID-060 | Built | Retry, rate limit and circuit breaker around source calls; dead-letter store `cred.source_dead_letter` (`credential_0009`, applied to `zid_cell`) with list and replay routes | No real source exists to tune the limits against |
| Section 11 fairness | Built as a regression harness | Name matching no longer refuses people for stroke letters or for scripts it cannot read (they go to review as `SCRIPT_NOT_COMPARABLE`) | Synthetic data only; not a validated fairness evaluation |
| WF-02 and WF-04 | Built, read only | `GET /v1/credentials/dossiers/{subject}` and `GET /v1/credentials/registry`; two new tabs on the Credentials admin page ("Person dossier", "Trust registry") | No editing screens by design: registries change through reviewed configuration. No browser test of the new tabs; the web type check is clean |
| Section 16 | Draft | `docs/specs/overlays/ZID-GAP-001-v1.1-overlay-notes-DRAFT.md` | Owner approval; Document 05 de-duplication needs its source scenarios |
| DTV-19 | Still domain only | Not wired to a route | Needs a route and an estimation provider |
