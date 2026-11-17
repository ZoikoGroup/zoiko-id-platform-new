# CR-107: Biometric method lifecycle, kill switch and live DPIA-bound eligibility (ZID-DTV-WF-04 section 06, section 19, BIO-01, BIO-03)

Status: open (domain, storage, service, operator API, tenant eligibility API, audit and the self-run face path built and unit tested; the PostgreSQL
component suite was not run on the authoring machine; the owners confirm the points below).

## Why
Wireframe 04 (Biometric Security, Privacy & Global DPIA Governance) makes DTV-12 a hard release gate: no biometric processing in a market or use case
without an approved DPIA, a lawful basis, an independent evaluation and an approved deployment, and a kill switch always available to Security and
Privacy. Section 06 names the method states (OFF, LAB_ONLY, PILOT_GATED, PRODUCTION_ELIGIBLE, PAUSED: "no default ON, no implicit vendor failover");
section 19 names `GET /v1/biometric-methods/eligibility` ("respond method OFF if legal, DPO, region, provider or version gate fails"). QA-001 (no or
expired DPIA returns the method disabled), QA-015 (a model upgrade without approval is refused) and QA-043 (a disabled method appears nowhere).

Before this change the platform had the pieces but not the joints: the engine gate (CR-095) accepted any well-formed `dpia_ref` string, nothing scoped an
engine to a country, purpose or tenant, and there was no way to pause one method without editing configuration.

## What changes
- **Domain** (`verification/domain/biometric_methods.py`, pure): a state per exact scope (method FACE_MATCH or LIVENESS, engine id and version, country,
  purpose, optionally one tenant). Nothing is on without a record. A step up is two people (proposer and a different approver), one step at a time
  (OFF or PAUSED to LAB_ONLY to PILOT_GATED to PRODUCTION_ELIGIBLE) and only against the version it was proposed on; PILOT_GATED names a tenant;
  PILOT_GATED and PRODUCTION_ELIGIBLE carry a DPIA and an independent evaluation reference. A step down (PAUSED, a lower state, OFF) is one person, at
  once. A resume from PAUSED is a new approval from LAB_ONLY. At run time a PAUSED scope always wins, then the tenant's own scope, then the
  all-tenant one; LAB_ONLY is never live.
- **Two tables** (migration `verification_0019`), platform configuration, no RLS: `verify.biometric_method_state` (append-only versions) and
  `verify.biometric_method_proposal` (decided once). Triggers repeat the rules: no update or delete of a state, sequential versions, one id per exact
  scope, a step up only by one step from an approved proposal, approver differs from proposer, a paused scope only retires or is re-approved,
  nothing in the future, one open proposal per scope.
- **Eligibility** (`verification/application/biometric_eligibility.py`): per method, four gates, every refusal named: the deployment's accepted engine;
  the method state; the TENANT's OWN DPIA read live from the privacy release gate (market = country, product = `biometric.face_match` or
  `biometric.liveness`, version = engine version), so an unapproved, expired, unsigned, unconsulted or model-mismatched package refuses the method and
  its blockers are listed; the country overlay (missing, unsigned or PROHIBITED refuses; CONSENT_REQUIRED is reported and enforced per person at
  session start). The answer carries `policy_hash`.
- **Operator API** (`operator_access/api/regulatory_biometric_methods.py`): `POST /v1/regulatory/biometric-methods/proposals`
  (`regulatory.biometric_method.propose`), `POST .../proposals/{id}:approve` and `:reject` (`regulatory.biometric_method.approve`, a two-person
  grant), `POST /v1/regulatory/biometric-methods/{scope_id}:step-down` (`regulatory.biometric_method.pause`, one approver: an emergency authority),
  `GET /v1/regulatory/biometric-methods`. Audit in the same transaction: `regulatory.biometric_method.proposed`, `.approved`, `.rejected`,
  `.stepped_down`; a refusal as `regulatory.biometric_method.refused`. The request field naming a pilot tenant is `pilot_tenant` (INV-13: no API
  model carries `tenant_id`).
- **Tenant API** (`verification/api/biometric_methods.py`): `GET /v1/biometric-methods/eligibility?country=&purpose=` (`verification:read`; route map
  `b.view-verification-state-expiration`, R).
- **Self-run face path** (`POST /v2/self-run/verifications/{id}/chip`): a face is scored only when FACE_MATCH and LIVENESS are both eligible for the
  session's country and purpose; otherwise the session is judged on the chip alone. The response carries `biometric_eligibility`.

## Not in this change
- **Events** `biometric.policy.blocked.v1`, `biometric.decision.changed.v1` (WF04 section 19): need event-catalogue (AsyncAPI) entries; the audit
  record is written.
- **Breach to pause**: done in CR-108 (a breach case pauses capture for its tenant in its jurisdictions; eligibility gate 5).
- **Provider-hosted biometrics**: the gates apply to the self-run engine. A licensed provider's biometric route is still governed by the country
  overlay, provider suspensions and the privacy gate at session start; binding it to method states is the next change.
- **Region cell**: the method scope has no residency cell; the country overlay decides the processing region.
- **Operator and admin screens** (WF04 B11 release dashboard): the APIs exist; the screens do not.

## Owner decisions (proposed values, none ratified)
1. No step is skipped: production needs a pilot first, including after a pause.
2. Stepping down needs one person; stepping up needs two.
3. An unanswered proposal lapses after 14 days.
4. The DPIA that counts is the tenant's own package in the privacy release gate, named `biometric.<method>` at the engine version; the platform's
   reference in the state record is kept for audit, not trusted at run time.
5. A tenant's own scope overrides the all-tenant scope, except that any PAUSED scope wins.
