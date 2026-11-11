# CR-069: Global credential trust core (ZID-GAP-001 v2.0 GID-036, GID-037)

Status: open (built and tested locally; owners ratify).

## Why
Document 01 v2.0 (Nov 10, 2026) adds 30 global packages (GID-036 to 065). The first two are the foundation the rest stand on: a canonical claim and
assertion model for academic, professional, certificate, CPD, employment and insurance claims (GID-037), and versioned country / profession service packs
that say where each check may be offered (GID-036). The Wave 0 map is `docs/qa/global-trust-wave0-matrix.md`.

## What changes
- **Claim taxonomy and assertion (pure domain, `credential/domain/claims.py`).** Seven claim classes with required, optional and enumerated fields; an unknown
  class, field or value fails closed. Evidence tiers (SELF_DECLARED, DOCUMENT_INTEGRITY, ISSUER_AUTHENTICATED, PRIMARY_SOURCE_CONFIRMED); eligibility and
  portability are not evidence tiers. The assertion carries five separate facts: credential status, issuer authenticity, primary-source verification,
  professional eligibility and consumer entitlement. One rule decides the outcome: nothing below a primary-source confirmation verifies, a source that
  cannot be reached is `SOURCE_UNAVAILABLE`, a source that cannot exist any more is `UNVERIFIABLE`, an ambiguous person match goes to review, an award the
  source records as ATTENDED is not a degree, and a credential that is not ACTIVE is not verified.
- **Service packs (pure domain, `verification/domain/service_packs.py`, config `backend/config/service-packs/service-packs.yaml`).** One versioned record per
  country, optional subdivision, optional profession and service, with feature state `LIVE_PROVEN | PARTNER_ONLY | STAGING | DESIGN | BLOCKED`. Anything
  unlisted, disabled, blocked, out of date, or unfinished in a deployed environment is `PACK_UNAVAILABLE`. The most specific scope decides, and a blocked
  specific pack never falls back to a broader one. A reliable pack needs its legal basis, owners, QA gate, retention and review rules, a consistent
  processing region, a matching content hash and (at publish) available sources. **Every pack in the shipped file is DESIGN, so nothing is available in a
  deployed environment.**
- **Claim checks.** Table `cred.claim_check` (migration `credential_0005`, tenant-scoped with row-level security, immutable rows, a later check supersedes an
  earlier one). It stores the assertion and a digest of the claim, never the claimed values or a document. Routes `POST /credentials/{academic,
  professional-licenses,certifications,experience,continuing-education,insurance}/checks`, `GET /credentials/assertions/{id}`, `GET /credential-packs`.
  A check needs a pack, then asks a source; any source fault is `SOURCE_UNAVAILABLE`. The only source is a simulator that local and test environments
  may build; a deployed environment has none, so there is no way to produce a verified claim there until owners add a real source and a LIVE pack.
- **Catalogues.** Action `credential.claim.verify` (R2, E2), event `credential.claim.checked` (ids and decision only), table ownership entry.

## What does not change
No existing route, table, action or event is altered. The country capability registry and its gate are untouched (a service pack complements it).
Eligibility stays `NOT_EVALUATED` in every assertion; it is a separate decision (GID-041, not built).

## Owner decisions still needed (none invented here)
Which sources (registrars, regulators, aggregators) are used and under what contract (GID-055); which countries and professions get a LIVE pack and who
signs the legal basis; recognition and equivalence rules (GID-045); the Zoikorum tier rules (GID-048); whether `credential.claim.verify` should be R2 or
higher; whether a check is billable and at what price (GID-064).

## Tests
`tests/unit/test_credential_claims.py`, `tests/unit/test_service_packs.py`, and the component test `tests/component/test_claim_checks_pg.py`.

## Addendum: second slice (GID-038 to 061, 065)

- **Migration `credential_0006`** (same cell database): `cred.consent_grant`, `cred.outreach_request`, `cred.eligibility_decision` (append-only), `cred.claim_appeal`, `cred.claim_monitor`; `claim_check.decision` admits INDETERMINATE and `source_result` admits NOT_QUERIED. All tenant-scoped with row-level security.
- **Configuration** `backend/config/credential-trust/`: `issuer-registry.yaml`, `source-contracts.yaml`, `eligibility-policies.yaml`. Every entry is a SAMPLE; the only source is a simulator marked `simulated: true`, which never counts as coverage.
- **Domain rules** (pure, in `credential/domain/`): `trust_graph`, `sources`, `standing`, `eligibility`, `lifecycle`, `portable`, `outreach`, `evidence_quality`; `application/identity_match.py` reuses the screening name scorer through `screening/ports.py`.
- **Routes**: `/v1/issuers/*`, `/v1/eligibility/{professional,engagement}/evaluate`, `/v1/consumers/zoikorum/decisions`, `/credentials/disclosures*`, `GET /credentials/assertions/{tenant}.{check}/verify` (public, grant-scoped), `/credentials/source-outreach*`, `/credentials/cases/{id}/appeals`, `/credentials/appeals/{id}:resolve`, `/credentials/monitoring`, `GET /credential-packs/{country}/{profession}`.
- **Catalogues**: actions `credential.eligibility.evaluate`, `credential.disclosure.manage`, `credential.outreach.manage`, `credential.appeal.manage`, `credential.monitor.manage` (all R2, E2); events `credential.eligibility.decided`, `credential.claim.status.changed`, `credential.claim.recheck.due`, `credential.claim.appealed`; route-map rules for the new `/v1/*` paths.
- **Worker step** `credential_recheck`: announces current checks that expire soon or have gone stale, once per day each.
- **Request models are prefixed `Cred`** because pydantic model names are global in the published OpenAPI files (a plain `ReviewIn` silently changed the verification and onboarding contracts).

### Not done (decisions or inputs the code cannot supply)
Real sources, issuers, packs and policies; issuer outreach keys; a price book and SKU for checks (GID-064); SDK packs (GID-063, `sdks/` is empty); accessibility and localization evidence (GID-062); the `vc/verify` route with signature checking and Open Badge / CLR / EDC parsing (GID-050, 051); a disciplinary check route (GID-046); wiring employment disclosure limits into the check (GID-044); turning an accepted outreach into a claim check (GID-054); an operator screen for the registries; a verifier web page; a scan of marketing text against coverage.

## Addendum: third slice

- **Migration `credential_0007`**: `cred.outreach_request` gains `subject_principal_id`, `claim` (dropped on conclusion) and `concluded_at`.
- **Routes**: `POST /credentials/disciplinary/checks` (action `credential.disciplinary.check`, R3, E3), `POST /credentials/vc/verify`, `POST /credentials/source-outreach/{id}:conclude`; the outreach create and review routes now carry an optional attached claim and return the resulting `check_id`.
- **Rules**: employment disclosure limits are applied in the check; a disciplinary result feeds the engagement decision (restriction, review hold) and the RESTRICTIONS dimension; reason codes have plain-language text (`domain/reasons.py`).
- **Web**: public verifier page `/verify/{ref}`. **SDK**: `zoiko_credentials` (Python). `zoiko_tool_server` gained `verify_signed` (a backward-compatible public wrapper over its existing JWS check).
- **Not done**: GID-064 (meter and price book), EDC seal validation, other-language SDKs, translations of the reason text.
