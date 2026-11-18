# WF03 matrix: ePassport NFC, document authenticity and biometric proofing

| Area | Status | Evidence |
|---|---|---|
| Signal states and one decision (T01, T06, T12, T13, T15, T17, T18, T20, T21, T26, T27, T29, T36, T39) | **Done and stored** | `idv_decision.py`, `idv_case.py`, `test_idv_decision.py`, `test_idv_case_pg.py` |
| Chip evidence into signals; no root or no revocation check is never a pass | **Done** | `idv_evidence.chip_signals`, `test_idv_evidence.py` |
| Capture envelope: server-computed digest, one-time nonce, freshness, replay, virtual camera | **Done** | `idv_evidence.capture_integrity`, `_envelope` in `idv_cases.py`, `test_idv_case_pg.py` |
| Device attestation | **Apple App Attest verified in code, tested on a synthetic chain only; config empty; Android not built** | `device_attestation.py`, `test_device_attestation.py` |
| Printed-document image quality and re-compression screen | **Done (measured by the sidecar; thresholds unmeasured)** | `idv_image.py`, `tools/ocr-sidecar/server.py`, `test_idv_document.py` |
| Printed-document authenticity (template, security features, photo substitution) (T24, T25) | **NOT built; no result is ever produced, so a market requiring one cannot approve.** The evidence a check must earn before it is switched on is now a gate (ships with no policy; synthetic samples never count): CR-125 | `idv_image.PRODUCED`; `authenticity_evaluation.py`, `tools/scripts/authenticity_eval.py` |
| Printed lines read by the platform, compared with the chip by salted digest | **Done; the client can no longer supply them** | `idv_document.py`, migration 0021, `test_idv_case_pg.py` |
| Face engine called from the chip route, liveness first, behind the engine gate | **Done and tested with a fake engine; no real engine approved or evaluated** | `apply_engine`, `test_idv_case_pg.py` |
| Biometrics off without basis, notice, consent, independent PAD; withdrawal (T31, T32) | **Done and stored** | `idv_controls.py`, `idv_case.py` |
| Trust-source allowlist and impact reconciliation (T02, T03, T38) | Allowlist **wired to the trust-store validation** (CR-119; enforcement ships off until authorities are recorded). Impact reconciliation of cases is still a rule, not wired | `source_policy.py`, `test_trust_source_policy_pg.py`; `idv_controls.impacted_cases` |
| Retry ceilings (T30) | **Done and enforced per market** | `idv_case._spend` |
| Market rules file, strict and hashed, ships empty | **Done** | `idv_policy.py`, `config/idv/market-policies.yaml` |
| Cases API, reviewer signature, webhook `verification.idv.case_decided` | **Done** | `idv_cases.py` |
| Applicant web screens: start, notice, consent, document photo, status (P01 to P03, P06, P07) | **Done** | `features/account/idv` |
| Applicant chip read and selfie (P04, P05) | **Phone app only; no native client for these routes written or run** | native shells exist from WF01 |
| Operator and reviewer console page (P08, P09) | **Done as one page** | `features/admin/idv-cases` |
| P10 method certification | Built as a read-only board (CR-119): six methods, four honest levels, approves nothing | `method_certification.py`, `GET /verification-method-certification`, `test_method_certification_pg.py` |
| Real chip, device and face lab evaluation; impact assessment; decisions D01 to D10; sign-offs | People and devices | none claimed |
