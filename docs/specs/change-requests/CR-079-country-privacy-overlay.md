# CR-079: Country privacy, cross-border and biometric overlay (ZID-RCG-003 RCG-G09)

Status: open (domain, configuration, the check at every verification start, operator reads and web wording built and tested; no country overlay is approved, so the check only reports; the owners confirm the points below).

## Why
Document 03.1 makes privacy a separate regulatory layer: controller and processor role, lawful basis, special-category biometrics, localization and
transfers, with purpose-specific approval, a DPIA where required, and vendor and cross-border transfer governance. T31: a biometric path that is
inaccessible or refused presents a compliant alternate route. T32: a cross-border data route that violates residency denies the outbound provider
request. What already exists stays as it is: the privacy module checks the TENANT's activities, purposes, consents and restrictions at every start
(`enforce_processing`), the tenant module governs cross-cell transfers, and a verification profile says where it uses biometrics. None of them knows what
a COUNTRY allows, and a tenant's profile could use biometrics where the law forbids it.

## What changes
- **Domain** (`verification/domain/privacy_overlay.py`, pure). One overlay per country: home regions; other regions each with its lawful transfer
  mechanism; the region each provider processes that country's data in; biometrics ALLOWED, CONSENT_REQUIRED or PROHIBITED; a DPIA flag and reference;
  the lawful basis; two approvals (DPO, Legal) of the overlay hash. `route` refuses, listing every reason: no overlay; a missing or stale approval; a
  required DPIA without a reference; a provider whose region is not recorded (never assumed); a region that is neither home nor a lawful transfer
  (`CROSS_BORDER_ROUTE_DENIED`, T32); biometrics where the country prohibits them, or where consent is required and not given. `alternates` names the
  tenant's ACTIVE profiles that cover the country without biometrics, only when the biometric route is the sole obstacle (T31).
- **Configuration** `backend/config/regulatory/privacy-overlays.yaml`, loaded by `infrastructure/privacy_overlays.py`. Ships with NO country (one example
  is a comment). A malformed row refuses the whole file.
- **The check at every start** (`verification/api/verification.py` `_privacy_gate`, service `application/privacy_gate.py`): after the country and pack
  checks, before any provider is asked, for an ENABLED country only, on `POST /verifications`, each person of `:bulk` (one person's refusal never stops
  another's), and `:reverify`. Biometric use comes from the profile; consent, only when the country requires it, comes from the privacy module's own
  check for purpose `BIOMETRIC_IDENTITY_VERIFICATION`, which denies by default. Refusals: `CROSS_BORDER_ROUTE_DENIED`, `BIOMETRIC_ROUTE_NOT_PERMITTED`
  (the alternate profile ids in the error details), `PRIVACY_OVERLAY_UNAVAILABLE`. Switch `ZID_PRIVACY_OVERLAY`: `shadow` (default, logs), `enforce`,
  `off` (refused outside local, test and dev).
- **Operator reads**: `GET /v1/regulatory/privacy-overlays` (each overlay, the hash to sign, its problems) and `GET /v1/regulatory/privacy-route-check`
  (a preview of the same rule).
- **Web app**: plain-language notices for the three refusals.

## Not in this change
- The provider is the one the country registry names; the adapter finally chosen at start is not re-checked against the overlay.
- Approving an overlay is configuration review (like the country registry), not an API lifecycle.
- Regional KEYS and deletion tests (RG-04 material) and DPIA content itself stay with the privacy and security owners.

## Owner decisions (proposed values, none ratified)
1. Approvers: the DPO and Legal.
2. The consent purpose code `BIOMETRIC_IDENTITY_VERIFICATION`, registered by each tenant like any other purpose.
3. Default `shadow` until the first overlay is approved; then `enforce`.
