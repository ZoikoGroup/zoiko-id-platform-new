# CR-077: Regulated claims publisher (ZID-RCG-003 RCG-G07, WF-07; T05, T33)

Status: open (domain, configuration, service, operator API, proof export and audit built and tested; no template is approved, so nothing is claimed; the owners confirm the points below).

## Why
Document 05.2: sales copy, product labels and trust marks may reference only an approved claims template and a current applicable attestation; no
white-label misrepresentation. T05: a certified company with an unrelated service gets no certified claim. T33: a website showing global
certification is blocked. Section 12: claims stay as narrow as the licence, certified service, approved policy, contracted source and validation
evidence allow. The evidence manifest and replay (WF-07's other half) already exist in the evidence module; nothing decided what may be CLAIMED.
`certification.refuse_unbacked_claim` refuses a claim no configured certification backs; this adds the approved wording, the market, the dates,
and the proof.

## What changes
- **Domain** (`verification/domain/regulated_claims.py`, pure). A `ClaimTemplate` has exact text, audiences, the countries it speaks for, and its
  basis: exact certification scopes (official-register attestations, CR-073) and markets (launched releases, CR-072). `decide` blocks a template that:
  uses global wording or names no country or a wildcard (`TEMPLATE_GLOBAL_CLAIM`); names a country no basis covers, or a basis outside its countries;
  has no basis; lacks either approval (legal, claims owner) or was edited after they signed its hash; is outside its dates or its audience; relies on a
  certification on hold (stale, withdrawn, another service) or a market that is not open. Every reason is listed.
  `proof_bundle` exports the template and its hash, each attestation snapshot and proof hash, each release and its hash, the outcome and reasons, and a
  sha256 digest over all of it; `verify_bundle` detects any change.
- **Configuration** `backend/config/regulatory/claim-templates.yaml`, loaded by `infrastructure/claim_templates.py`. It ships with NO template (one example
  is a comment). A malformed row refuses the whole file; a merely unpublishable template still loads so operators can see why it is blocked.
- **Service** (`application/regulated_claims.py`): `judge`, `publishable` (only what may be shown for one audience, with its proof digest), `proof`.
- **Operator API** (`operator_access/api/regulatory_releases.py`): `GET /v1/regulatory/claims` (every template, publishable or not and why;
  `publishable_only=true&audience=...` returns only what may be shown) and `GET /v1/regulatory/claims/{template_id}/proof?at=` (the bundle).
  Any regulatory capability may read. Each proof export writes an audit record `regulatory.claim.proof_exported` with the digest.

## Not in this change
- **A public, unauthenticated route** for the website. It would change a published API contract and the generated web client; the website or CMS
  reads the operator route until that change is made.
- Replaying a MARKET at a past moment: certifications are judged as of `at` from their history; markets are judged on the releases as they stand now.
- A template lifecycle in the database (draft, approve, publish through the API): templates are reviewed configuration, like the country registry.

## Owner decisions (proposed values, none ratified)
1. Two approvals: legal and the claims owner, each signing the template hash.
2. The wording blocked as global: global, worldwide, all countries, every country, everywhere, internationally certified, universal.
3. Audiences: public website, product label, sales, support, API.
