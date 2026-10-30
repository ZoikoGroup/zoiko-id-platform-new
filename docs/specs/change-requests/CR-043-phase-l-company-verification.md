# CR-043: Company verification (ZID-ED-03) tables, event and routing configuration

- **Target:** ZID-DATA-001 (organization tables), ZID-EVENTS-001 §22, and the Phase L decision ZID-ED-03 Global Company Verification (28 Oct 2026, proposed) · **Raised by:** Phase L slice L-3 · **Status:** Open (service, tables, three source adapters and tests built; Creditsafe is unverified against a live account; the suppliers' contracts, coverage proof and the country routing sign-off are the owners')

Later Phase L slices append their own tables, events and actions to this CR.

## Tables (organization module, `org` schema)

| Table | Scope | Note |
|---|---|---|
| `org.kyb_case` | tenant-scoped (RLS) | One request to verify one legal entity: jurisdiction, registration number as written, the name the applicant gave, optional entity form and LEI, an idempotency key unique per tenant, the latest status and expiry. Status is the latest evidence's decision. |
| `org.kyb_evidence` | tenant-scoped (RLS) | Append-only (a trigger refuses UPDATE; the runtime role has no DELETE). One signed row per decision, refresh or reviewer ruling: body, evidence hash, JWS, policy version, reasons, reviewer and rationale when a person ruled, observed time and expiry. History is never rewritten. |

## Event

`organization.kyb.decided` (in `contracts/asyncapi/proposed-events.yaml`): case id, decision status, reasons, evidence hash, policy version and country. Ids and codes only.

## Configuration

`backend/config/kyb/kyb-routing.yaml`: the adapters and their status, a route per country (first pass, LEI source, exception suppliers), the policy (validity, name threshold) and `supplier_coverage_proven`, which is empty. The 365-day validity is a proposal taken from ED-04's annual refresh; ED-03 gives no number.

## What ED-03 requires, and where it lives

| ED-03 | Implementation |
|---|---|
| §02 provider architecture: Companies House and GLEIF direct, Creditsafe preferred, D&B qualified, OpenCorporates optional | `infrastructure/kyb_adapters.py` (Companies House, GLEIF, Creditsafe); D&B and OpenCorporates are listed in the routing file as not built |
| §03 country routing by ISO code | `Route` per country in the routing file; a country with no route has no coverage |
| §04 sequence: validate identifier, query the register, supplier only when absent or inadequate, deterministic reconciliation, signed evidence with expiry | `check_registration_number`, `application/kyb.py::decide` (a supplier is called on NOT_FOUND, conflict, name or identifier mismatch or an incomplete record, never on an outage), `reconcile`, `evidence_object` signed by the caller's signer |
| §05 statuses NOT_STARTED, IN_PROGRESS, VERIFIED, NEEDS_REVIEW, REJECTED, UNAVAILABLE, EXPIRED; Zoiko issues the outcome | `KybStatus`; EXPIRED is derived at read time (`effective_status`); REJECTED is reachable only by a reviewer's ruling |
| §06 normalized fields, adapter interface, server-side calls, idempotent jobs, no silent vendor substitution | `CompanyFacts`, `KybAdapter` (`search`, `get`; verify and refresh are the service's), a supplier counts only where its coverage is proven for the country |
| §08 acceptance: fixtures, no verified status from a name, a claim, missing data, an expired check or a timeout; every decision carries source, jurisdiction, time, policy, reason, actor and a trace; reviewer overrides record a rationale | `tests/unit/test_kyb.py` (55) and `tests/component/test_kyb_pg.py` |

## Checked against published sources (30 Oct 2026)

- **Creditsafe Connect** (doc.creditsafe.com): `POST /authenticate` with `username` and `password` returns `token`; `GET /companies` takes a required `countries` and optional `regNo`, `name` and `status` (Active, NonActive, Pending, Other) and answers `{correlationId, messages, totalSize, companies}`; the token is sent as `Authorization: Bearer`; a sandbox host exists. This corrected three things in the first draft: the `Bearer` scheme, `status` read as the documented enum, and `type` (Ltd, NonLtd, NonLtdNonReg) no longer read as a legal form. The field names inside each company item are not on the published page and remain unverified.
- **France:** the Annuaire des Entreprises search API (`recherche-entreprises.api.gouv.fr`, INSEE Sirene and the RNE) is open and keyless; `q` with exactly nine digits is a direct SIREN lookup; results carry `siren`, `nom_complet`, `nom_raison_sociale`, `etat_administratif` (A active, C ceased), `nature_juridique` (a numeric code) and `siege.adresse`. It is built as the `sirene` adapter, so a French company verifies from an official register without any supplier coverage.
- **Ireland:** CRO numbers are all digits with no fixed length (sources disagree on the length), so the format accepts any digit string. The CRO Open Services need a registered account and signed terms, so no adapter was built.
- **Germany:** a Handelsregister number (HRA or HRB, an optional trailing letter) identifies a company only together with its register court, so a German case must name the court. The portal offers no API; no adapter was built.
- **United States:** each state is its own register, so a U.S. case must name the state (`US-XX`); no adapter was built.

An entity keeps one id (jurisdiction, canonical register, number) whichever source reported it, and one German or U.S. number at two courts or states is two companies.

## Also built in this pass

- A request retried or sent twice at once with the same idempotency key is one case (`INSERT ... ON CONFLICT DO NOTHING`); concurrent refreshes take turns on the case and never share a version.
- Every supplier lookup is recorded as a `KYB_LOOKUP` vendor cost (charged or not; an outage is never chargeable), so Finance can measure the cost per completed verification.

## L-4: director, owner and sanctions screening (ZID-ED-04), new `screening` module

**Tables** (in the `verify` schema; the module owns them): `verify.sanction_list_version` and `verify.sanction_list_entry` (public reference data, not tenant-scoped: one active version per list with its own reference, content hash and fetch time); `verify.screening_case`, `screening_party`, `screening_ownership` (tenant-scoped, restricted: a party holds a name and a birth year); `verify.screening_match` (every alert of every run, immutable); `verify.screening_evidence` (append-only, one signed row per decision, refresh or reviewer ruling).

**Event:** `verification.screening.decided` (case id, status, reason codes, evidence hash, policy version, country, alert count; never a name, a date of birth or list entry text).

**Configuration:** `backend/config/screening/screening-policy.yaml`: the required list families, list age limit, validity, match thresholds, country packs and list sources.

| ED-04 | Implementation |
|---|---|
| §02 control matrix: entity, representative, directors, owners, PEPs, ongoing monitoring | `Role` parties; entity and representative are always required (`GATHERING` until present); `rescreen` and `due_for_rescreen` re-screen on a list change, an expiry or a gap |
| §03 country rules kept apart | `CountryPack` per country: the UK PSC test, the OFAC 50 Percent Rule (designated holdings added, an entity blocked by it blocks what it owns) and the OFSI test (a single designated owner above 50 percent or in control, never added together); a country with no pack is a review |
| §04 workflow and §06 state machine | `NOT_STARTED, GATHERING, SCREENING, VERIFIED, REVIEW_REQUIRED, REJECTED, UNAVAILABLE, EXPIRED, SUSPENDED`; REJECTED only by a reviewer; a provider or list failure is UNAVAILABLE |
| §05 list sources | parsers for OFAC SDN CSV, UK CSV, UN XML and EU XML, each refusing a file that is not its list; a file much smaller than the one in use is refused whole |
| §06 fairness | an alert is a possible or strong match with its reasons in words; nothing clears, rejects or approves automatically; a ruling needs an independent reviewer and a rationale |
| §06 access control | the customer sees an outcome and a next step (`safe_view`); the alerts with names are for a designated reviewer (`restricted_detail`) |
| §08 tests | `tests/unit/test_screening.py` (45) and `tests/component/test_screening_pg.py` |

**What each parser rests on:** OFAC (the published data specification), the UK CSV headings (the published format guide) and the UN individual elements (the live file) are confirmed; the UN entity and document shapes and the whole EU file are not. Which download addresses are verified is in [screening-owner-pack.md](../../signoff/screening-owner-pack.md).

**Added after the first L-4 pass:**
- *Lists:* the OFAC consolidated (non-SDN) files load as `OFAC_NON_SDN` (the same layout as the SDN files, per OFAC's consolidated data specification); a country's own list loads as `LOCAL` from a documented normalized CSV (`list_key,source_ref,kind,primary_name,aliases,birth,countries,identifiers,programs`), and a country pack can require it (`local_list_required`) before anyone in that country is screened.
- *Scripts:* Cyrillic, Greek and Arabic names are written in Latin before they are compared (deterministic tables), spellings that differ only by transliteration convention are brought together (-ov, -off, -of), and a consonant comparison is added for names that changed script. Any other script is compared as written.
- *Fetching:* `infrastructure/fetch.py` fetches each list that has a verified address, over https through the platform's outbound guard, following a redirect only to a host the source names, bounded in size; the worker steps `sanctions_lists` and `screening` load them and screen again every case a list change or an expiry has made stale (an UNAVAILABLE case is retried at most hourly, so an outage does not fill the evidence). A new setting, `ZID_COMPANY_EVIDENCE_KMS_KEY_ID`, signs company-verification and screening evidence; with no key nothing is recorded unsigned.
- *Routes:* `onboarding/api/org_claims.py` (WF-13B): `POST /v1/org-claims/search`, `POST /v1/org-claims`, `GET .../status`, `POST .../refresh`, `POST .../screening` for an OWNER or ADMIN, and `GET /v1/reviews/screening-cases/{id}`, `POST /v1/reviews/screening-cases/{id}/rulings`, `POST /v1/reviews/kyb-cases/{id}/rulings` as protected actions (`verification.screening.read_restricted`, `verification.screening.review`, `organization.kyb.review`, added to the action catalogue). The server re-reads the official source and never trusts the client's claim, derives the representative's authority from the authority root the person holds (none means authority stays unproven), treats declared holdings as unverified, and shows the applicant an outcome and a next step but no list or alert. The one outcome is VERIFIED only when all four checks are complete, and the identity check is a separate step this view does not read, so a claim is never VERIFIED here yet.
- *Screens:* `/admin/organization/claim` (find), `/admin/organization/claim/{id}/authority` and `/admin/organization/claim/{id}/status` in the web app.

**Known limits of L-4:** no list has a verified download address except the UN one, and the EU list needs a registered account, so every decision is UNAVAILABLE until an owner supplies them; names in scripts other than Latin, Cyrillic, Greek and Arabic are compared as written; the packs for every country but GB and US do not exist; the approvals inbox and decision screens (WF-13C) and the evidence upload need the approval engine (L-10) and are not built; the identity-assurance check (ED-05) and sign-up (13A) are not built, so the status view cannot reach VERIFIED.

## L-5: identity-proofing suppliers (ZID-EDR-005), `verification` module

- **Migration `verification_0003`** adds `provider_kind` (GENERIC, VERIFF, SUMSUB; default GENERIC, so existing adapters are unchanged) and `provider_config` (secret names and settings) to `verify.provider_adapter`. No new table, event or action.
- **Creation:** `POST /verification-adapters` accepts the two fields. Unknown settings are refused (a secret smuggled in is a 422), secret references must be names, and `uk_certified` is accepted only on a Veriff adapter whose jurisdictions are exactly GB.
- **Start and re-verify** return a one-time `handoff` (link or token) for the applicant; it is not stored and not returned again.
- **Callbacks** are authenticated on the raw body before anything is read: Veriff by `X-AUTH-CLIENT` plus an HMAC-SHA256 over the body; Sumsub by `X-Payload-Digest` with a named algorithm (SHA-1 refused). Veriff and Sumsub callbacks answer 200 (they retry anything else); the existing protocol keeps 202. A Sumsub GREEN fetches the applicant's data; if that fails nothing is recorded and the answer is 503 `APPLICANT_DATA_UNAVAILABLE`.
- **Interpretation:** an unknown status is PENDING, never approval; risk labels always become fraud signals; the UK marker is attached only by a certified adapter.
- **Confirmed from the suppliers' documentation:** Veriff session creation and webhook signing, decision statuses; Sumsub request signing, SDK token, `applicantReviewed` payload. **Unconfirmed:** Sumsub's `X-Payload-Digest` header details, the applicant-data response shape, Sumsub fraud labels and the Veriff base address (configuration).
- **Not built:** polling reconciliation, RESUBMISSION_REQUIRED as a session state, live sandbox runs, supplier region and DPA proof.

## Known limits

- **Creditsafe has never run against an account.** Its path, parameters, authentication and scheme match the published documentation, but the field names inside each returned company do not appear there and are unverified. It is `DISABLED_UNTIL_CONTRACT`, and no country lists it as proven.
- **No national register adapter exists** for the U.S. states, Ireland or Germany (BRIS is a discovery route, not an assumed API); France has one. Those countries rely on the supplier, so they answer UNAVAILABLE / NO_COVERAGE until coverage is proven.
- **Identifier formats:** GB, FR and IE follow the registers' published rules; the exact court and state spellings for DE and the US are for the registry owners to confirm.
- **No HTTP routes yet.** The org-claim API and screens are slice L-12 (WF-13B); this slice delivers the service they call.
- **Director, owner and sanctions checks** are ED-04 (slice L-4); a company-registry match is never proof of an applicant's authority.
