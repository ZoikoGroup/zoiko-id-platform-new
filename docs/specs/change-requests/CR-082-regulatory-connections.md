# CR-082: Connecting the regulatory checks: every entry point, events, the decision's regulatory basis, the EUDI class (ZID-RCG-003)

Status: open (built and tested; the owners confirm the points below).

## Why
CR-067 to CR-081 built each ZID-RCG-003 work package, but several were only partly connected: the regulatory checks ran on identity verification
alone; other systems were never told of a regulatory change (section 09.2 event classes); a verification did not record which pack, release and
provider it went on (section 09.1 RegulatoryDecision); and the PID, QEAA or EAA class of an EUDI presentation was computed but not recorded.

## What changes
- **Every regulated entry point** (`country_gate.enforce_regulated`, and `enforce_tenant` for a tenant's own action): the country registry, then the
  policy pack, market release, provider entitlement and kill switch. Now on company verification (`/onboarding/organization`, sign-up and the registry
  lookup), organization claims (create and search), `/v2/organizations`, `/v2` credential cases, credential issuance and configuration activation, and
  enterprise activation. Account registration and discovery are not regulated decisions and keep the country check only. A request with no tenant
  yet (a company signing up) reads the platform records in a plain transaction. The regulatory checks run for an ENABLED capability only, so nothing
  changes until a country is enabled. `test_country_capabilities.GATED_ENTRY_POINTS` names every site.
- **Events** (`contracts/asyncapi/proposed-events.yaml`, regenerated AsyncAPI): `verification.policy_pack.approved` and `.retired`,
  `verification.market.launched` and `.withdrawn`, `verification.certification.changed`, `verification.provider.entitlement_changed`,
  `verification.provider.scope_suspended` (priority lane) and `.scope_restored`, `credential.issuer_status_changed`. Written on the outbox in the same
  transaction as the change and its audit record, under the operator tenant, with ids, codes and hashes only (`operator_actions.announce`); a refusal
  announces nothing. The document's names map one to one (`policy_pack.approved` is `verification.policy_pack.approved`, and so on).
- **The regulatory basis of a verification** (migration `verification_0010`, column `verify.verification_session.regulatory_basis`): at start, the
  governing pack (id, version, hash), the release (id, version, hash) or why there is none, the provider and its entitlement outcome, whether the kill
  switch was checked, the switch modes, and the privacy route (region, transfer mechanism, reasons). Written once; a trigger refuses any later edit.
  Not part of the public verification response.
- **The EUDI class with a presentation**: the verifier records the credential's own issuance time (`iat`, or the mdoc `signed` time) in the decision
  basis, and for an EUDI type adds the class (PID, QEAA, PuB-EAA, EAA) judged at that date from official-register attestations; without an issuance
  time it is not qualified (`ISSUANCE_TIME_UNKNOWN`). Stored with the presentation, not in its public response.

## Not in this change
- A sync country check inside the organization-claim refresh path (`kyb_refresh`) stays a country check only; the claim's creation runs the full checks.
- Showing the regulatory basis or the EUDI class in a public API response (a contract change for the owning teams).
