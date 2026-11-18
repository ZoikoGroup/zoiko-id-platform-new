# CR-116: WF-05 device risk observations, attestation nonces and tenant-keyed pseudonyms (ZID-DTV-WF-05 DTV-22, §02, §19, §22; QA-030 to QA-035)

Status: open (Step 5 of the WF-05 build). No attestation verifier ships and the device method has no adapter or approved retention, so no observation is
taken until the owners act (DEC-08, DEC-11).

## What changes
- **Rules** (`verification/domain/device_risk.py`): EMULATOR, ROOTED_OR_JAILBROKEN, APP_INTEGRITY_FAILED and AUTOMATION give REVIEW with a step-up or
  review recommended (QA-031); VPN_OR_PROXY alone is only noted (QA-033); a mobile observation without a valid attestation is INDETERMINATE
  (DEVICE_SIGNAL_INDETERMINATE); nothing is ever FAIL or "fraud" (§02).
- **Pseudonyms**: the device reference is HMAC-keyed by tenant and a rotation period (`config/verification/device-risk.yaml`, 30 days SAMPLE) and
  dropped: two tenants never share a pseudonym for one device (QA-034), and it changes every period.
- **Nonces** (`verify.check_device_nonce`, migration `verification_0026`): `POST /v1/verification-cases/{id}/device-nonces` issues a single-use,
  5-minute nonce bound to the case and session; an attestation must answer one, and a replay is refused (QA-032).
- **Observations** (`POST /v1/device-observations`, Idempotency-Key, `verify.check_device_observation`, append-only): only with DEVICE_RISK cleared
  for the case, otherwise refused and nothing written (QA-030). The attestation goes to the deployment's `attestation_verifier` (Play Integrity, App
  Attest; none ships, so UNAVAILABLE); a verifier that fails is UNAVAILABLE. `DEVICE_RISK_OBSERVED` is recorded on the case.

## Not yet
- Play Integrity and App Attest verifiers (Google and Apple credentials, DEC-08); browser signals and any fingerprint SDK (DEC-08).
- The web device cookie (`X-Zid-Device`) is still set before any purpose or notice gate: changing it waits for DEC-08.
- Fairness analysis of device step-ups across cohorts (ENG-18, QA-038) and the screens.

## Evidence
`tests/unit/test_device_risk.py`, `tests/component/test_device_checks_pg.py`.
