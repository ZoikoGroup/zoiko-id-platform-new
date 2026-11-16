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

## Status update, sixth pass (2026-11-12)

| Item | State | What changed | What is still open |
|---|---|---|---|
| DTV-02 | OCSP and XAdES built | `evidence/application/cms.py` reads operator-saved OCSP responses (`config/trust/ocsp/`); `xades.py` validates one XAdES / XML-DSig signature (signed properties must be signed, signing-certificate digest, chain, timestamp over the canonical SignatureValue, OCSP/CRL revocation). `POST /v2/documents/validate` now accepts `application/xml` for validation only (never an upload type) | Standalone CAdES files, long-term archive timestamp chains, more than one signature per XML document |
| GID-051 | Built for sealed XML | `POST /v2/credentials/edc/verify`: seal result and qualified-seal result kept separate from the claim (awarding body, title, issue date); nothing about the holder is returned; `primary_source_verified` and `statutory_license` always false; issuer binding NOT_ASSESSED | JSON-LD EDC, a check that the sealer is the named awarding body, a rescinded-degree source check (GLB-36), anchors and trusted lists (ship empty) |

Tests: `test_xades.py` (16), `test_edc_seal.py` (7), OCSP cases in `test_pdf_signatures.py`; guards in `test_baseline.py`, `test_architecture.py` and `test_injection_controls.py` pass. No HTTP-level component test of the two new routes has been run against Postgres.

## Status update, seventh pass (2026-11-12, CR-073 first slice)

| Item | State | What changed | What is still open |
|---|---|---|---|
| DTV-02 standalone CAdES | Built | `cms.inspect_cades` (detached or attached `.p7s` over caller-supplied content; the per-signature assessment is now shared with PDF and also checks the `signingCertificateV2` attribute); `POST /v2/documents/validate` takes an optional `signature_base64` | Long-term archive timestamp chains, multi-signature XML, CAdES-A |
| DTV-26..28, 32..34 | Gate built, lines OFF | `verification/domain/product_lines.py`, `config/product-lines/product-lines.yaml`: all six lines DESIGN; a line switched on without legal basis, partner contract (not source of funds), approver or countries does not load; unlisted country unavailable; `ports.require_available` | Every real line needs a partner, a legal basis and an owner approval; no route uses the gate yet |
| GID-062 locale review | Built | `reasons.LOCALE_REVIEWS`: es and fr are DRAFT; REVIEWED needs a reviewer and date (a test enforces it) and `describe` returns `reviewed_by` and `reviewed_on` | The reviews themselves, RTL fixtures, accessible-route policy, WCAG report |

Tests: `test_cades.py` (11), `test_product_lines.py` (14), `test_reasons.py` (+2); guard tests pass.

## Status update, eighth pass (2026-11-12, CR-073 second slice)

| Item | State | What changed | What is still open |
|---|---|---|---|
| DTV-02 more than one XML signature | Built | `evidence/application/dsig.py`: a strict XML-DSig engine (signxml only ever verifies the first signature found); each signature judged on its own; references resolve only to the whole document or one unique `#id` (duplicate ids refused), only enveloped and canonicalization transforms, no external URIs; signxml kept as an independent cross-check on the first signature (disagreement fails it); the result reports `covered_ids` and never calls a partial signature a whole-document one | Long-term archive timestamp chains; the whole-document case with two enveloped signatures needs an XPath filter transform that is refused on purpose |
| GID-051 issuer binding | Built (XML) | `trust_graph.bind_seal`: the awarding body is resolved through the issuer registry (exact after folding, former names by period, lookalikes refused), then bound by a recorded seal-signer certificate fingerprint or organization identifier; `/v2/credentials/edc/verify` returns `issuer_binding` (BOUND, NOT_BOUND, ISSUER_UNKNOWN, AMBIGUOUS, ISSUER_REVOKED) and the tier is ISSUER_AUTHENTICATED only when sealed and BOUND, otherwise DOCUMENT_INTEGRITY | JSON-LD EDC; no real issuer has recorded seal signers (the registry ships samples with none) |
| DTV-16 MRZ | Built (first part) | `evidence/domain/mrz.py` (TD1, TD2, TD3 with ICAO 9303 check digits, tested on the ICAO specimen lines) and `POST /v2/documents/extract`; always `authenticity: NOT_ASSESSED`, `assurance_ceiling: SELF_DECLARED` | PDF form fields, signed barcodes, templates, OCR, tamper signals |

Tests against Docker Postgres: `test_v2_xml_cades_edc_pg.py` (5 route tests). Unit: `test_xades_multi.py` (14), `test_mrz.py` (10), binding cases in `test_edc_seal.py`.
CR-073 Part 2 designs what is still open (long-term validation, JSON-LD EDC, GID-064 implementation plan, document intelligence, accessibility, source of funds).

## Status update, ninth pass (2026-11-12, CR-073 third slice)

| Item | State | What changed | What is still open |
|---|---|---|---|
| DTV-04 embedded-evidence long-term validation | Built for XAdES | `xades.py` reads `CertificateValues` and `RevocationValues`; embedded revocation counts only when a verified timestamp fixes the signing time and the evidence was produced after it, read as it stood when produced; embedded certificates complete a chain and never add an anchor; operator evidence still wins; the finding says `revocation_source` (OPERATOR, EMBEDDED or NONE). The cross-check library now judges the certificate at the signing time | PDF `/DSS` (needs a PDF object reader); the archive timestamp chain (`ArchiveTimeStamp`), whose imprint rules need test vectors from a real validator |
| GID-051 JSON-LD EDC (JWS-secured) | Built | `vc_verify` no longer refuses an EDC delivered as a vc+jwt: the JWS under the issuer's pinned key is the seal; the response says `qualified_seal: NOT_ASSESSED`, never primary-source, never a licence. Data Integrity proofs (RDF canonicalization) are not supported and are not a vc+jwt, so they are refused as malformed | RDF canonicalization (needs a reviewed dependency) |
| GID-062 right-to-left | Built (component level) | `web/src/lib/user-text.tsx`: `UserText` isolates free-text names with `<bdi dir="auto">`, `textDirection` for places without markup; used for issuer names in the trust registry; tested with Arabic, Hebrew, Devanagari, Thai, CJK and mixed names | Applying it to every screen that shows a name; a browser (axe) run on right-to-left pages; WCAG audit report; accessible selfie alternative; translations |

Tests: `test_xades_ltv.py` (10), `test_vc_verify_pg.py` (7, against Docker Postgres), `user-text.test.tsx` (7); tsc and eslint clean on the touched web files.

## Status update, tenth pass (2026-11-12, CR-073 fourth slice)

| Item | State | What changed | What is still open |
|---|---|---|---|
| DTV-04 PDF validation data (PAdES-LT) | Built, deliberately narrow | `evidence/application/pdfobjects.py`: a strict PDF object reader (dictionaries, arrays, names, strings, references, plain and Flate streams; no object streams, no cross-reference streams; limits on nesting, object count and decompressed size) and `dss_update`. A signed PDF that later gets a `/DSS` update is no longer reported as "content added after signing", but ONLY when every object in the update is validation data: the catalog (changed by adding /DSS and nothing else), the /DSS dictionary, its /VRI entries, and the certificate, CRL and OCSP streams. One extra, redefined or changed object and the whole update is refused. Embedded evidence is used by the same shared rule as XAdES (`cms.merge_embedded_revocation`): trusted timestamp required, evidence must postdate signing, read as it stood when produced, never an anchor | Document timestamps (`DocTimeStamp`) and the archive timestamp chain: they add page content, fields and annotations to the file and need the full incremental-update rules, so they stay "content added after signing" |
| DTV-16 PDF form fields | Built (first part) | `pdfobjects.form_fields` and `POST /v2/documents/extract` with `pdf_base64`: AcroForm fields, fully qualified, last revision wins, signature fields skipped; `document_signed_and_unchanged` is true only when every signature is sound, chained to a configured anchor and nothing was added after signing; the ceiling is DOCUMENT_INTEGRITY then, SELF_DECLARED otherwise; authenticity is always NOT_ASSESSED | Signed barcodes and QR payloads, layout templates, OCR, tamper signals |

Tests: `test_pdf_ltv.py` (19: reader limits, one-extra-object refusals, object streams and document timestamps refused, end-to-end validation of an old signature on an expired certificate), a PDF form route test in `test_v2_xml_cades_edc_pg.py`, against Docker Postgres.

Incident, same day: a lint command ran with an empty file list and reformatted 114 unrelated tracked files. It was undone by restoring exactly those files from HEAD after proving, in a clean temporary checkout of HEAD, that ruff reproduces each current file byte for byte (so none held a real edit). The 25 tracked files with real changes were not touched. Lint is now run only with an explicit, checked file list.

## Status update, eleventh pass (2026-11-12, CR-073 fifth slice)

| Item | State | What changed | What is still open |
|---|---|---|---|
| DTV-16 signed barcodes | Built (EU health certificate shape) | `evidence/application/barcode.py`: `HC1:` text, base45 (RFC 9285 vectors tested), zlib with a size cap, COSE_Sign1; ES256 and PS256 verified only against certificates in `config/trust/barcode-keys/`, chosen by key id; a key nobody configured is INDETERMINATE, a bad signature NOT_VERIFIED, unsigned or other algorithms never accepted; the payload is returned as signed; reached through `POST /v2/documents/extract` with `barcode_text`. Found and fixed by the tests: cbor2 returns a tuple for a tagged array and a read-only mapping for a map, which a naive reader refuses | ICAO VDS-NC (JSON) and other barcode families; which issuers' keys to configure is an operator task |
| GID-064 (CR-070) | Definitions added, nothing priced | `credential.check` and `credential.sensitive_check` are in the meter register: unpriced, no quota, `bill_on_commit: false`, no billable meter or SKU behind them, and the file says plainly they are not part of the owner-ratified eight; the pinned test now names the two. GLB-35 rule: `PRICE_BOOK_CURRENCY_MISMATCH` (hard) when the account is billed in a different currency than the price book, with the account currency now passed in by `create_quote` | Prices per region (CFO, CCO), the recording point, the billable meter and SKU migrations: all still wait for those numbers and rulings |

Tests: `test_barcode.py` (20), the barcode route test in `test_v2_xml_cades_edc_pg.py`, GLB-35 and the register in `test_order_rules.py` and `test_capability_bands.py`.

## Status update, twelfth pass (2026-11-12, CR-073 sixth slice)

| Item | State | What changed | What is still open |
|---|---|---|---|
| GID-062 accessible capture alternative (GLB-30) | Built (platform side) | `verification/domain/capture_alternatives.py`, `config/accessibility/capture-alternatives.yaml` and `GET /v2/capture-alternatives?country=XX[&reason=...]`: three routes (document check in person, assisted remote check, manual review of other evidence), each with an honest assurance note; no reason or proof is required; a reason only reorders the routes; asking or choosing is never a failed attempt and cannot reject (`counts_as_failed_attempt` and `rejection_possible` are always false); a configuration that leaves any country, or the default, with no route does not load | The provider contract clause and staffed in-person or assisted channels that make a route real; the SDK session machines do not yet call the endpoint; translations; the manual WCAG audit |
| GID-051 Data Integrity proofs | NOT built | Needs the pinned library `pyld==3.3.0` (BSD, W3C JSON-LD reference authors; brings `cachetools` and `frozendict`) added with `uv add --package zoiko-id-platform pyld==3.3.0` and then installed. `uv` is not on this machine. I put it in a scratch environment and updated `uv.lock` correctly (34 lines, nothing else changed), but installing into the project environment was refused by the permission system, so I reverted the lock edit rather than leave an unused dependency, and wrote no verification code I could not run | Your decision to add the dependency, then: offline context loader with hash-pinned bundled contexts, `eddsa-rdfc-2022` verification, and the W3C test vector |
| DTV-16 templates and OCR | NOT built | No OCR library or Tesseract is installed and no OCR container image is present | The list of issuers; a Tesseract sidecar image |
| DTV-26..28, 32..34 | Gate only | unchanged: every line is DESIGN | Partners, legal basis, owner approval |

## Status update, thirteenth pass (2026-11-12, libraries and OCR installed on the owner's instruction)

| Item | State | What changed | What is still open |
|---|---|---|---|
| GID-051 Data Integrity proofs (`eddsa-rdfc-2022`) | Built, verified against the W3C test vector | Dependency added with an exact pin: `pyld==3.3.0` (BSD, W3C JSON-LD reference authors) plus `cachetools==8.0.0` and `frozendict==2.5.1` in `app/pyproject.toml` and `uv.lock` (only those packages changed). `credential/application/data_integrity.py`: offline hash-pinned context loader (`config/jsonld-contexts/`, SHA-256 per file; any context not bundled is refused, nothing is fetched), URDNA2015 requested explicitly, a round-trip guard so a property the context does not define can never be silently dropped, one proof, assertionMethod, base58-btc multibase, Ed25519. It reproduces the specification's canonical forms, every hash, the combined hash and the signature, and verifies its signed credential (`test_data_integrity.py`, 30 tests). `POST /v1/credentials/vc/verify-data-integrity` verifies against the issuer's PINNED Ed25519 key from the trust table (`verify_vc_di`), same five separate facts, never a primary-source confirmation, never a licence, never accepted as a claim on its own without a holder presentation | `ecdsa-rdfc-2019` and JCS suites; a bundled Europass EDC context (the real one is not bundled: a credential whose type no bundled context defines is refused); holder binding for JSON credentials |
| DTV-16 OCR and templates | Built, running | `tools/ocr-sidecar` (Tesseract 5.5.0, Pillow 12.3.0 pinned, languages eng fra spa deu ara) as a sandboxed container behind `docker compose --profile ocr` (read-only filesystem, all capabilities dropped, no-new-privileges, memory and process caps, localhost only; only PNG and JPEG, size, pixel, region and language limits). `evidence/domain/templates.py` + `config/document-templates/templates.yaml` (one SAMPLE template; APPROVED needs a named approver and date), `evidence/infrastructure/ocr_client.py`, and `POST /v2/documents/extract` with `image_base64` and `template_id`: fields carry text, value, confidence, matched, usable and reasons; a field that does not match, or was read with low confidence, is reported as such and never repaired; authenticity always NOT_ASSESSED, ceiling SELF_DECLARED; no service configured answers 503 and nothing is read. Tested end to end against the real container (surname, document number and birth date read correctly) | Real issuers' templates (needs the list of issuers and specimens); tamper signals |

Tests: `test_data_integrity.py` (30), `test_ocr_templates.py` (23), `test_vc_verify_pg.py` (11, four new for Data Integrity), OCR route tests in `test_v2_xml_cades_edc_pg.py` including the real-container test.

## Status update, fourteenth pass (2026-11-12, the last buildable items)

| Item | State | What changed | What is still open |
|---|---|---|---|
| DTV-04 document timestamps and the archive chain (PAdES-LTA) | Built, fail-closed | A document timestamp (`/SubFilter /ETSI.RFC3161`) is no longer read as a broken signature: `cms.slot_subfilters` tells them apart. `pdfobjects.validation_update` (which replaced the narrower `dss_update`, kept as a wrapper) now accepts a later update that is ONLY validation data: a `/DSS` and/or timestamps, where each timestamp comes with exactly one invisible signature field (zero rectangle, no appearance), the form entry gains exactly that field, and the page's annotation list gains exactly that field. A visible stamp, any other object, a changed catalog, page or form entry, or a field that is not one of the timestamps makes the whole update "content added after signing". `cms.inspect_document_timestamps` verifies every stamp: it must cover everything before it, each later one reaching further, its token must verify against a configured TSA root with an imprint of exactly those bytes (the authority's certificate is judged at the token's own time, so an old stamp survives its certificate's expiry), and it may not be dated before the stamp it follows. `archive_state` reports NONE, ARCHIVE_VALID, ARCHIVE_BROKEN or ARCHIVE_UNVERIFIED; `/v2/documents/validate` returns `document_timestamps` and `ltv_state` | The timestamps do not yet fix the SIGNING time of a signature (only the signature's own timestamp does); visible stamps; object streams; the shape was checked against my own fixtures, not a third-party validator, and anything unfamiliar is refused rather than accepted |
| GID-051 second Data Integrity suite | Built, verified against the W3C vector | `ecdsa-rdfc-2019` (P-256, SHA-256): canonical forms, every hash, the combined hash and the signature of the specification's P-256 vector are reproduced and its signed credential verifies; `p256_public_key` decodes a Multikey and refuses a point that is not on the curve; a key whose type does not belong to the proof's suite is refused, never tried; `verify_vc_di` picks the issuer's pinned Ed25519 or EC P-256 key by the proof's suite | `ecdsa-rdfc-2019` P-384, the JCS suites |

Tests: `test_pdf_doc_timestamps.py` (18), `test_data_integrity.py` (39 for both suites), `test_vc_verify_pg.py` (12).

## Status update, fifteenth pass (2026-11-13, the small follow-ups)

| Item | State | What changed | What is still open |
|---|---|---|---|
| GID-051 `ecdsa-rdfc-2019` on P-384 | Built, verified against the W3C vector | `p384_public_key` (Multikey 0x8124, compressed point checked on the curve), SHA-384 for both hashes AND for the RDF canonicalization itself (the library's URDNA2015 is SHA-256 only, so a subclass supplies SHA-384; the blank node labels differ, which is why a wrong hash cannot verify), 96-byte signature. The vector is Examples 38 to 48 of the W3C ECDSA specification; every canonical form, hash and the signature are reproduced. A P-256 key never checks a P-384 proof and the reverse. Issuer JWK lookup accepts an EC P-384 key | |
| GID-051 JCS suites `eddsa-jcs-2022` and `ecdsa-jcs-2019` (P-256 and P-384) | Built, verified against three W3C vectors | RFC 8785 canonical JSON (`c14n`, shipped inside the pinned pyld wheel, so no new dependency). No JSON-LD context is read and nothing is expanded, so every member present is signed: adding one breaks the proof (tested). Key family checked against the suite | Holder binding for JSON credentials |
| DTV-04 signing time from a document timestamp | Built | A signature with no timestamp of its own is judged as of the earliest VERIFIED document timestamp that reaches past it (`cms.existence_time_for`; `Finding.time_source` is SIGNATURE_TIMESTAMP, DOCUMENT_TIMESTAMP or NONE). The result says `SIGNING_TIME_BOUNDED_BY_DOCUMENT_TIMESTAMP`, not "trusted", because the stamp proves the signature existed by then, not when it was made. An unconfigured TSA, a stamp over other bytes, or a signature whose own token is present but invalid never rescue it; a stamp after the certificate expired does not either | the signing TIME itself (a stamp bounds it, never proves it) |
| SDK call to capture-alternatives | All five clients (Python, TypeScript, Go, Java, .NET) | see `docs/qa/doc02-global-api-integration-matrix.md`; Go, Java and .NET compiled and tested in Docker | Not exercised against a running platform |
| Europass EDC JSON-LD contexts | Bundled and pinned | `config/jsonld-contexts/`: `https://www.w3.org/2018/credentials/v1` (7,687 bytes) and `https://data.europa.eu/snb/model/context/edc-ap` (22,723 bytes, served as text/plain by the EU; the bytes are what is pinned). Fetched 2026-11-13, read (no `@import`, no remote reference beyond vocabulary namespaces), SHA-256 in `MANIFEST.yaml`. An EDC-shaped credential canonicalizes to the Europass vocabulary and a property the context does not define is still caught, not dropped | A real signed EDC as a vector (none was available); the context is the EU's current published one and may change: re-fetch and compare the hash before relying on it |
| RCG-G02 register reconciliation | Built as a pure rule | `certification.reconcile` downgrades a record the official register does not list, or lists as suspended or withdrawn, with a suspension proof; never upgrades | A fetcher for the real registers |
| DTV-04 visible timestamp stamps | Built, bounded | A document timestamp may be drawn on a page when its rectangle is valid and at most a quarter of the page, its appearance is one plain Form XObject stream (only drawing keys; no scripts, actions, optional content or resources that did not exist in the signed file) and the update is otherwise exactly the invisible timestamp update. `Finding.visible_timestamp_stamps` and the signature detail report it. A visible field with no appearance, an oversized or degenerate one, a hostile appearance, an action on the field or any other extra object is refused (`test_pdf_visible_timestamps.py`, 19) | A third-party validator has not seen these files; the fixtures are my own |
| DTV-04 object streams and cross-reference streams | Built, fail-closed | `pdfobjects.parse_objects` opens an object stream (PDF 1.5) and sees the objects inside, and ignores a cross-reference stream (an index, not content). If an object stream cannot be read in full (bad header, short data, wrong counts, a member that does not parse, `/Extends`) it is NOT expanded and stays as an object nothing may add, so content can never ride in unread. A hardening found on the way: an object that is defined but unreadable now counts as an object, so an update holding one is no longer accepted as "only validation data" (`test_pdf_object_streams.py`, 18) | Object streams that use predictors or a filter chain are not read (the update is refused); an object stream in the SIGNED revision of a real-world file is read, but no third-party specimen has been tried |
| Holder binding for JSON-LD credentials | Built (design decision taken, flagged) | `vc_verify.holder_bound_di` and the optional `holder_presentation` field of `/v1/credentials/vc/verify-data-integrity`. The decision: the holder is a self-certifying `did:key` (Ed25519, P-256, P-384; nothing is resolved over a network, any other DID method is not binding); the presentation is a VerifiablePresentation carrying exactly this credential, whose subject id is that DID, with a Data Integrity proof of `proofPurpose: authentication`, `domain` equal to this verifier, a `challenge`, and `created` within five minutes (the same window as the JWT form). A copied credential, another verifier's presentation, a stale one or a wrong purpose is not bound (`test_holder_binding_di.py`, 15, signed for real). It proves possession of the key the credential was issued to, not who the person is | Replay inside the five-minute window is bounded only by `domain` and freshness, as in the JWT form; a stored one-time challenge would close it. Counsel or product may prefer another DID method or a wallet protocol |

Tests: `test_data_integrity.py` (65), `test_pdf_doc_timestamps.py` (25), `test_pdf_signatures.py`, SDK `test_v2_client.py` (14), TypeScript `v2.test.ts`.


## Status update, sixteenth pass (2026-11-13, merge of origin/akarsh, then the chip verifier)

| Item | State | What changed | What is still open |
|---|---|---|---|
| Merge of `origin/akarsh` | Done locally, not pushed | 10 commits, 163 files (Document 03 G02 to G12). The text merged cleanly. The database migrations did not: both branches defined `verification_0006` and both created `verify.provider_entitlement` (mine tenant-scoped, theirs platform-level). Mine is renamed `verify.provider_route_entitlement` and moved to `verification_0013`; the ownership registry entry is renamed to match | `akarsh`'s own chain still has `verification_0005` twice (`0005_policy_packs` and `0005_provider_entitlement`) in the branch as published, and `0005_provider_entitlement` and `0008_provider_entitlement` both create the same table; the merge removed the first because of my earlier rename, but the owners of `akarsh` should confirm |
| PERSON_IDENTITY from a passport or eID chip (Document 03 section 06, Document 04 "signed chip checks") | Built: server side of passive authentication | `evidence/application/epassport.py`, `POST /v2/documents/chip-verify`, `config/trust/csca/` (ships empty). Verifies the EF.SOD CMS signature, the Document Signer chain to a CSCA the deployment configured, every supplied data group against the signed hashes (a group the SOD does not list is refused; SHA-1 is refused), then reads DG1 only from a verified group. The best answer is `VERIFIED_LIMITED`; `holder_bound` is always false and the answer says the holder is not bound and a clone is not detected. An expired Document Signer is judged inside its own validity window and says so; a not-yet-valid one is never rescued. The photo (DG2) is never read or kept (`test_epassport.py`, 30, with a real CMS signed over real data-group hashes) | The phone side (reading the chip over NFC) needs a native app or SDK; no CSCA certificates are configured (a country publishes them in a master list: the operator extracts and verifies them); Active and Chip Authentication (clone detection) happen between chip and reader and are not shown to the server; no third-party passport specimen has been tried |

## Status update, seventeenth pass (2026-11-13, merge repairs and PERSON_IDENTITY from a wallet or eID credential)

| Item | State | What changed | What is still open |
|---|---|---|---|
| Merge repairs found by running the whole-tree checks | Fixed | (1) `verification/infrastructure/entitlement_store.py`: `akarsh` added its G03 store (`decide_scope`, `scopes_of`, `record`) at the same path as my routing store, and in the published `akarsh` tip mine had overwritten theirs, so `country_gate`, `suspensions` and `right_to_rent` called functions that did not exist. Theirs is restored from commit f8dd0e0; mine is `route_entitlement_store.py`. (2) `credential/api/trust.py` used `current_issuer_registry` without importing it (a NameError at runtime on the issuer routes). (3) `verification/ports.py` imported eleven names it never exported. (4) `credential_0012` was defined twice. Ruff and mypy are clean across all 821 source files | `akarsh` should take these fixes: its own published tip has the same defects |
| PERSON_IDENTITY from a wallet or eID credential (Document 03 section 06) | Built | `credential/domain/person_identity.py`, `POST /v2/identity/credential-verify`, `config/identity-credentials/` (one SAMPLE type, trusts nothing). Needs an issuer pinned for the exact credential type, a credential type the owners accept as identity, and holder binding (JWT proof or a did:key presentation); a copied credential is INDETERMINATE, an unreadable status is not acceptance, a revoked one is NOT_VERIFIED. Only name and birth date are returned. An optional `expected` person is matched across accents and word order; a different person is reported, not accepted, and a script the scorer cannot read goes to a person. Best answer VERIFIED_LIMITED: an issuer's attestation, not a population-register check, and key possession, not a live person (`test_person_identity.py` 28 and `test_identity_credential_pg.py`) | Which issuers and credential types count as identity is the owners' decision; no wallet scheme is connected; the result is not yet fed into the engagement check's IDENTITY dimension (that reads the verification module's person assurance) |

## Status update, eighteenth pass (2026-11-13, the two open identity items checked against the documents)

| Item | State | What the documents settle | What stays with the owners |
|---|---|---|---|
| Which issuers and credential types count as proof of identity | Partly closed from the documents | Document 03 RCG-G11: a service is accepted only when the exact listed provider held the status on the relevant date. So an EU PID counts only when an official-register attestation (CR-073) lists its issuer as a PID provider for the Member State on the issuance date. This is now enforced on `POST /v2/identity/credential-verify` (`PID_MEMBER_STATE_REQUIRED`, `PID_ISSUE_DATE_UNKNOWN`, `PID_PROVIDER_NOT_LISTED`); the PID type names are listed in `config/identity-credentials`; the typed trust table still has to pin the issuer. Tested against a real recorded attestation (`test_identity_credential_pg.py`, 5) | The list of non-PID issuers and types (a national eID, a bank scheme): Document 01 section 15 says "Issuer network partnerships: Legal + Procurement, NOT VERIFIED", "no invented policy". The evidence required before an issuer is pinned is in `docs/signoff/identity-evidence-decisions.md` |
| Feeding a credential-based identity into the engagement check's IDENTITY dimension | Not built, on purpose; the decision is written up | Document 03: name matching alone is insufficient, so a credential cannot be tied to a Zoiko principal by name. The platform's own tier rule needs binding and liveness "or an accepted equivalent", and that equivalence is an owner approval. The record proposes the profile, the binding (the principal presents it inside their own authenticated session) and the expiry rule | Product, Privacy, Security and Legal approval; then engineering builds the session and assertion bridge |


## Status update, nineteenth pass (2026-11-16, buildable items closed and stale lines corrected)

| Item | State | What changed | What is still open |
|---|---|---|---|
| DTV-19 age assurance | Built (this row was stale: `POST /v2/age-checks` already existed and was tested) | The route now has one source of VERIFIED birth dates: a passport or eID `chip` (the same input as `/v2/documents/chip-verify`), passively authenticated; the date is read only from a verified data group whose check digits hold. The answer says HOLDER_NOT_BOUND and CLONE_NOT_DETECTED. A failed chip is UNDETERMINED with the reason and never hides a clear estimate (`age.combine` no longer lets an unusable verified method shadow a decisive one). No date is echoed | A government age-verification service or an estimation provider; a country signing CA must be configured for any chip to verify |
| DTV-28 source of funds and wealth | Built as machinery (CR-096); the line stays DESIGN | `verification/domain/source_of_funds.py`, `config/questionnaires/source_of_funds/0.1.0-SAMPLE.yaml`, migration `verification_0015` (`verify.sof_case`, `verify.sof_decision`, row-level security, append-only decisions), seven `/v2/source-of-funds` routes behind the product line gate, three actions, privacy adapter (export, governed erasure). The system never clears anyone; a reviewer is never the submitter; an adverse first decision is never final; an appeal goes to a third person; no score and no free text. 48 unit and 5 component tests | The compliance officer's real questionnaire and legal basis, the data protection officer's retention and DPIA entry, the owners' decision to move the line to STAGING |
| GID-062 right-to-left | Built for the screens that show names | `UserText` now wraps 19 places in 16 screens where a company, person, integration, connector or file name is shown as text (agents, identity providers, provisioning, onboarding, organizations, team, overview). Template-string titles and attributes are not wrapped | A browser (axe) run on right-to-left pages, the WCAG report, translations |
| GID-063 SDKs | Corrected and exercised | Go, Java and .NET are built (the fourth-pass lines saying they are empty are stale). The Python and TypeScript SDKs are now run against a REAL server over HTTP (`test_sdk_live_pg.py`): capabilities, coverage, a credential check with replay, an evidence upload through the returned upload URL, a webhook, and a wrong token | Go, Java and .NET against a running platform (their toolchains are only in Docker here) |
| CR-072 market releases console | Corrected: built | `frontend/apps/operator/src/features/regulatory/releases.tsx` exists (open, freeze, four approvals, launch, withdraw); CR-072's "no console yet" line was stale | Owners confirm the points in CR-072 |
| Contract hygiene | Fixed | Two OpenAPI schema-name collisions removed (`RevokeBody` in the API-agent routes, and a new model of mine named `CaseIn`); the workload-identity TypeScript types were stale and are regenerated | |


## Status update, twentieth pass (2026-11-16, the remaining buildable engineering)

| Item | State | What changed | What is still open |
|---|---|---|---|
| DTV-28 console screens | Built | `/admin/source-of-funds` (the queue, which shows no answers) and `/admin/source-of-funds/{caseId}` (answers in words, evidence references, what is missing, the decision record, a reviewer's decision form and the person's appeal). A switched-off line reads as Not available, a role without the read permission reads as Withheld, a lapsed clearance reads as expired. The BFF allowlist and the API client carry the routes. 50 web tests (logic, screens, structural accessibility, the allowlist) | A navigation entry (it needs a role-matrix mapping, which is a product decision, and the line is DESIGN); the keyboard-and-screen-reader pass |
| DTV-33 Travel Rule shell | Built (CR-097) | IVMS101 payload validation, the counterparty VASP check (LEI check digits, listed, ACTIVE for the jurisdiction), the threshold question (UNKNOWN unless configured), `POST /v2/travel-rule/validate` behind the product line gate. The registry ships empty | A partner, a legal basis, the real registry and thresholds; the message record belongs with the partner adapter |
| DTV-20, DTV-21, DTV-22 | Rules and the provider interface built (CR-098); no provider and no route | Phone and email shape, a keyed hash, shared devices, a deterministic link graph with hub handling, `SignalProvider` with a provider that fails treated as unavailable | A provider, a purpose and lawful basis, a retention value, a DPIA entry; wiring into the signal queue |
| DTV-30 workflow studio | Rules built (CR-099); no editor, no executor | A workflow is data. Static analysis before publish: no loop, every path ends, every DECISION has an else, no DECLINED outcome, APPROVED guarded by the required checks being VERIFIED, a regulated check needs a person after it, a product line that is off blocks publish. Simulation and the maker-checker gate. A brute-force test proves the approval guard | The editor, a store for drafts and approvals, the executor |
| DTV-04 predictors and filter chains | Built | PNG predictors 10 to 15 and the TIFF predictor, chains of up to four of Flate, ASCIIHex and ASCII85, each checked against independent encoders. Anything else is not read and the object stays one nothing may add | A third-party specimen; LZW and image filters stay unread on purpose |
| Access Readiness and the governance stop | Built | The company-wide and per-agent emergency stop of agent governance now stops the readiness agent too, before every page, and a stop that cannot be read refuses the run. DAARA's routes are NOT retired: that waits for the owners' decision (`adr-two-implementations-of-wf-001.md`) | The decision; a budget and quota hook if the owners want one |
| GID-063 SDKs | All five exercised against a real server | Python, TypeScript, Go, .NET and Java each run the same calls over HTTP (the last three from Docker). The Java SDK failed its first POST over plain http (the default client offered an HTTP/2 upgrade the server does not take) and is fixed by pinning HTTP/1.1: a fault no fake transport could show | |
| AG2-T04, T33, T44, T45 | Linked, IN_PROGRESS | Tests for the agent id surviving name edits and for an alert or billing outage never lifting a hard cap; T44 and T45 link to the existing accessibility spec and the wording tests | An independent run |
| GID-064 credential-check meter | Not built, on purpose | The meter register is an owner-ratified record pinned by a test, and a price book is a CFO and CCO decision. Building a billable meter now would contradict both | The decision |


## Status update, twenty-first pass (2026-11-16, the two items left for the owners, done on the engineering owner's instruction)

| Item | State | What changed | What is still open |
|---|---|---|---|
| GID-064 credential-check meter | Usage metering built; billing NOT built (CR-070) | Two usage-only meters, `CREDENTIAL_CHECK` and `CREDENTIAL_SENSITIVE_CHECK`, exactly as CR-090 did for screening: `commercial_0043` widens the closed meter set, the credential module reports each completed check through a sink the composition root wires, the key is derived from the check id (a repeat is one event). Nothing is counted when nothing was checked (refused before a source, or source unavailable). No SKU, no price, never rated or invoiced. The register entries now name their meters, and the pinned register test says so | Whether checks are billed at all, the unit and the price per market (CFO, CCO), provider pass-through, the recording point; a price book line per market |
| DAARA retired in favour of Access Readiness | Applied (option 1 of `adr-two-implementations-of-wf-001.md`), unsigned | `ZID_DAARA_ENABLED` (default false): the eleven DAARA routes answer 404 naming the Access Readiness agent, its worker steps do nothing, the console shows "Replaced by the Access readiness agent". Nothing deleted: code, `machine_0009` tables and records stay, and the flag brings it back unchanged. The governance alert routes that shared the file are untouched | The platform and security owners' signatures; whether Access Readiness should also reserve a budget; dropping the code and archiving the tables, later, under the privacy officer's retention rules |


## Status update, twenty-second pass (2026-11-16, the rest of the buildable list, on the engineering owner's instruction)

| Item | State | What changed | What is still open |
|---|---|---|---|
| DTV-30 workflow studio | Built (CR-099, second slice) | Migration `verification_0016`, store, executor, `/v2/workflows` and `/v2/workflow-runs`, and the console (list, editor, version page with simulate, approve, retire). A different person approves; an unsafe workflow cannot be approved; a run follows only the event it awaits | A runs screen; who may author and approve; an independent run |
| DTV-17 address rules | Rules built (CR-100) | Compare, policy as data (SAMPLE), proof assessment that never rises above document presented | A provider; the approved policy |
| DTV-26, 27, 32, 34 shells | Rules built (CR-100) | Open banking, payment-method ownership (card numbers refused), transaction monitoring (alerts are a reason to look), on-chain screening (unavailable with no provider) | Providers, rule sets, thresholds |
| DTV-24 ownership chains | Rules built (CR-100) | Exact shares, loops refused, gaps reported, candidates only | The threshold per market; a screen and a store |
| Navigation | Built | Source of funds and Verification workflows are in the console menu and the guide, shown to administrators only | The role matrix for other roles |
| DTV-31 unified compliance console | Not built, on purpose | It would put operator-side and company-side records in one screen, which crosses the plane boundary | A design decision on which plane owns it |
| Signal-queue wiring, Travel Rule message record | Not built | Need the role matrix and the partner message format | Those decisions |


## Status update, twenty-third pass (2026-11-16, the three items that waited for decisions, taken as the engineering owner's decisions)

| Item | State | What changed | What is still open |
|---|---|---|---|
| DTV-31 compliance console | Built (CR-101), company plane | `/admin/compliance` lists the company's own source of funds cases and workflow runs waiting for a person, each under its own permission; no decision control; the reviewer signal queue stays on the operator side | Whether reviewer queues ever join it |
| Identity signals into the queue | Built (CR-101) | Link groups and shared devices open one review item each, counts only; `screening_0005` allows the source | Which events feed observations |
| Travel Rule message record | Built (CR-101), line still DESIGN | `verification_0017`, append-only, findings and hashes and company identifiers only, three routes, two actions | Retention of message contents (Legal and the partner) |
