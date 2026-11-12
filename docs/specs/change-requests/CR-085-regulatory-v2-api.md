# CR-085: The ZID-RCG-003 section 09.2 regulatory API at the document's paths

Status: open (built and tested; the owners confirm the points below).

## Why
Section 09.2 names the regulatory API at `/v2/regulatory/...`. CR-067 to CR-082 built the same functions at `/v1/regulatory/...` (the repository's
convention). This change adds the document's paths so integrators can follow the document, without a second implementation.

## What changes
`operator_access/api/regulatory_v2.py`, operator plane (route map: `* /v2/regulatory/*` exempt OPERATOR_PLANE, before the tenant `/v2/*` rules):
- `POST /v2/regulatory/pack-revisions` and `POST /v2/regulatory/pack-revisions/{pack_id:vN}/approve`: the CR-067 revise and approve handlers.
- `GET /v2/regulatory/providers/{id}/services`: only the scopes an official-register attestation shows, each certified or not with its reason and
  snapshot; a suspended scope shows as held; nothing is inferred from a provider's other services.
- `POST /v2/regulatory/provider-attestations/refresh`: the CR-073 attestation record (Regulatory Ops; the source receipt is the proof hash and locator).
- `POST /v2/regulatory/holds`: the CR-078 kill switch.
- `GET /v2/regulatory/evidence-bundles/{claim:<template_id> | run:<run_id>}`: a minimal proof export. A stated purpose (REGULATOR_REQUEST, AUDIT,
  LEGAL_CLAIM, INTERNAL_REVIEW) and a legal basis reference are required, or it is refused with `EXPORT_PURPOSE_REQUIRED`, and the refusal is
  audited. Every export is audited and announced (`evidence.bundle.exported`, the document's `audit.exported`) and carries a digest, signed with the
  platform evidence key when one is configured (otherwise `signature_status: NO_SIGNING_KEY`).
Errors on these paths are RFC 7807 problem details, as on every `/v2` path.

## Not in this change
- `GET /v2/regulatory/coverage`: the verification-and-trust team's `/v2/coverage` serves it for tenants.
- `POST /v2/trust/documents/validate` and `/v2/trust/profession-eligibility/evaluate`: served by that team's `/v2/documents/validate` and
  `/v2/eligibility/evaluate`.
- Step-up for the hold: the operator gate already requires a fresh strong sign-in for every operator route.
