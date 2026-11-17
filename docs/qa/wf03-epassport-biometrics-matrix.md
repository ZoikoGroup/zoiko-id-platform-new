# WF03 matrix: ePassport NFC, document authenticity and biometric proofing

| Area | Status | Evidence |
|---|---|---|
| Signal states and decision (T01, T06, T12, T13, T15, T17, T18, T20, T21, T26, T27, T29, T36, T39) | **Done and stored** | `idv_decision.py`, `idv_case.py`, `test_idv_decision.py`, `test_idv_case_pg.py` |
| Chip evidence into signals; no root or no revocation check is never a pass | **Done** | `idv_evidence.chip_signals`, `test_idv_evidence.py` |
| Capture envelope: nonce, freshness, replay by digest, virtual camera, attestation flag (T12, T13, T24, T25) | **Done in rules and store; no device attestation source** | `idv_evidence.capture_integrity`, `test_idv_case_pg.py` |
| Printed-document result handling and printed-versus-chip comparison | **Done in rules; no image engine exists** | `idv_evidence.optical_state`, `compare_printed_to_chip` |
| Biometrics off without basis, notice, consent, independent PAD; withdrawal (T31, T32) | **Done and stored** | `idv_controls.py`, `idv_case.py` |
| Trust-source allowlist and impact reconciliation (T02, T03, T38) | Rules done, not wired to the trust-store import | `idv_controls.py` |
| Retry ceilings (T30) | **Done and enforced per market** | `idv_case._spend`, `test_idv_case_pg.py` |
| Market rules file, strict and hashed, ships empty | **Done** | `idv_policy.py`, `config/idv/market-policies.yaml`, `test_idv_policy.py` |
| Cases API `/v1/idv/cases`, reviewer signature, webhook `verification.idv.case_decided` | **Done** | `idv_cases.py`, CR-110 |
| Operator and reviewer console page (P08, P09) | **Done as one page** | `features/admin/idv-cases` |
| Chip read, PACE, passive authentication, trust store | Exists from WF01 (CR-107, CR-108) | see those records |
| Applicant screens P01 to P07 | NOT built | need native or mobile and a real chip |
| P10 method certification screen | NOT built | |
| Face engine called from the case routes; lab liveness and face evaluation; impact assessment; decisions D01 to D10; sign-offs | People and devices | none claimed |
