# CR-113: WF-04 biometric gap closure: release attestation, market retirement, DPIA policy binding, erasure retry, per-class keys, incident data classes, region event, payload scan, rollback, load and chaos, accessibility matrix (ZID-DTV-WF-04)

Status: open (built and tested; the owners confirm the values marked PROVISIONAL). Builds on CR-107 to CR-110. Closes the engineering gaps left in
`docs/traceability/wf04-biometric-register.md` after CR-110.

## What changes

| QA | Change |
|---|---|
| QA-041 | **Release attestation.** Every approved step up of a biometric method scope writes `privacy.biometric_release_attestation` (migration `privacy_0011`, append-only; release governance sits with the DPIA release gate) in the approval's transaction: the decision hash, the running build (`ZID_RELEASE_BUILD`), the capture SDK (`ZID_BIOMETRIC_CAPTURE_SDK`), the approver and `attestation_hash` over all of them. Eligibility reports `release_drift` (RELEASE_NOT_ATTESTED, RELEASE_BUILD_CHANGED, RELEASE_SDK_CHANGED) for a live scope; `ZID_BIOMETRIC_RELEASE_BINDING=enforce` also refuses it. The answer carries the running build and SDK. |
| QA-039 | **Retire a market.** `POST /v1/regulatory/biometric-methods/markets/{country}:retire` (capability `regulatory.biometric_method.pause`, one person): every scope in the country to OFF and its open proposals rejected, in one action; audit `regulatory.biometric_method.market_retired`, event `verification.biometric_method.state_changed`. History and past dispositions are untouched. Operator console: "Retire a market". |
| QA-004 | **DPIA bound to the policy it assessed.** Approving a package stores `policy_hash` (migration `privacy_0011`): the purpose (version, categories, prohibited uses, retention code), the ACTIVE retention policies of the biometric classes, and the named provider (revision, status, locations, transfer mechanism). When any changes, the release gate names `DPIA_POLICY_CHANGED` and the biometric method stops until a new version is approved. |
| QA-023 | **Provider deletion retry and alert.** Worker step `erasure_retry`: an overdue or failed provider task is asked for again (`privacy.erasure.deletion_requested`) after 1 h, 6 h, 1 d, 3 d and 5 d (PROVISIONAL); then it is FAILED with RETRIES_EXHAUSTED and one priority alert `privacy.erasure.deletion_failed` is raised. A failed first-party task is alerted once. A task is never completed without a receipt. Erasure tab shows attempts and the alert. |
| QA-034 | **Incident data classes.** `breach_case.data_classes` (codes only); set when the case is opened, added later with `POST /v2/privacy/breaches/{id}/data-classes`, never removed; in the dossier. Breaches tab shows and adds them. |
| QA-029 | **Region-block event.** A biometric route refused because the provider would process outside the approved regions writes `verification.biometric.region_blocked` (country, provider, provider region, reason codes, overlay hash). |
| QA-021 | **Per-class encryption.** An evidence object of a biometric asset class (`biometric.<name>`) is encrypted under its own key per subject: HKDF-SHA256 of the subject's erasable key, labelled with the tenant and the class; `encryption_key_ref` carries `#class-key-v1`. One class's key opens no other; re-keying keeps the marking; erasing the subject still erases every class. Objects stored before are read as before. |
| QA-035 | **Payload scan.** A unit test parses every WF-04 event and audit call and refuses a payload key that names personal data or media (the ledger's rule plus image, template, embedding, vector, frame, video, score); a PostgreSQL test scans every event the flows wrote. |
| QA-045 | **Rollback consistency.** A PostgreSQL test rolls a provider's model back and shows every gate (version, evaluation, policy hash) follows it. |
| QA-042 | **Load and chaos.** PostgreSQL tests: 72 concurrent eligibility answers over a real pool agree and fail closed; a gate that times out or fails is a plain 5xx with no personal data, stack or internal message in the body or logs; faults during load touch only their own calls and the events written meanwhile stay minimal. |
| QA-044 | **Accessibility matrix.** axe (WCAG 2.2 A/AA structural rules) over the applicant's session page, the readiness answer and the breach screens in en/LTR, ar/RTL and hi/LTR. Contrast, zoom and a screen-reader run on real devices stay a manual pass. |

## Owner decisions (proposed values, none ratified)
1. Whether a build or SDK change must stop a live scope (`enforce`) or only be reported (default `report`).
2. The retry waits and the five attempts before an alert.
3. What a DPIA's policy covers (purpose, biometric retention, named provider).
4. Retiring a market is one person, like a pause.
