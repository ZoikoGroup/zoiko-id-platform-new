# CR-108: Breach containment pauses biometric capture, blank retention keeps a method off, and method state changes are announced (ZID-DTV-WF-04 B05, B09, BIO-08)

Status: open (domain, storage, service, API, eligibility gates, event catalogue entry and unit tests built; the PostgreSQL component suite was not
run on the authoring machine; the owners confirm the points below). Builds on CR-107.

## Why
Wireframe 04 B09: a privacy incident's containment includes "disable capture policy", and "policy change disables new sessions immediately"; QA-033
asks that a provider incident permits an emergency pause. B05 and BIO-08: raw media, templates, results and logs each have a distinct signed retention
period, and "blank retention or unsupported deletion pathway => route OFF"; QA-021. Section 19 names state-change events. CR-107 left breach-to-pause
and the events out.

## What changes
- **Breach containment** (migration `privacy_0006`): `privacy.breach_case` gains `capture_state` (NOT_PAUSED, PAUSED, RELEASED) with who and when.
  A new case pauses capture when the person opening it says so, or by default when its severity is HIGH or CRITICAL
  (`pause_biometric_capture` on `POST /v2/privacy/breaches`). `POST /v2/privacy/breaches/{id}/biometric-capture:pause` pauses an open case later;
  `:release` lifts it, by someone other than whoever paused it, with a reason. Closing the case ends the pause. The table repeats the rules (forward
  only, nothing changes on a closed case, release by another person). Audit: `privacy.breach.capture_paused`, `.capture_released`; the open audit
  records whether capture was paused.
- **Eligibility gate 5**: while an open case of the tenant has capture PAUSED and names the country, every biometric method there is refused with
  `BREACH_CAPTURE_PAUSED` and the case ids. It is tenant-scoped (breach cases are tenant data); a platform-wide stop is still the operator
  `:step-down` of CR-107.
- **Biometric asset classes** (`config/privacy/biometric-asset-classes.yaml`, PROPOSED): `biometric.live_selfie`, `biometric.face_template`,
  `biometric.match_result` (face match) and `biometric.liveness_capture`, `biometric.liveness_result` (liveness). A malformed file is refused whole.
- **Eligibility gate 6**: a method is refused while any class it produces has no ACTIVE retention policy of the tenant for the purpose (object type =
  the class id, purpose = this purpose or none), with `RETENTION_NOT_SET:<class>` per class. A class that is never stored still needs a decision
  (0 days, DELETE).
- **Event** `verification.biometric_method.state_changed` (proposed catalogue, priority lane), published with the audit record on an approved step up
  and on every step down (pause, lower state, retirement): scope id, version, state, scope fields and references; never media or reason text.
  AsyncAPI regenerated.
- **List reads** for the workbenches: `GET /v2/privacy/dpia-packages` (each with its release state, read without locks) and
  `GET /v2/privacy/breaches[?open_only=true]` (with clocks and capture state); `privacy.read`.
- **Screens**:
  - Web admin, Privacy: **DPIA** tab (B03: packages with release state; open a package; risk register with inherent and residual L x I bands; the
    eight gates with their accountable roles, signed with evidence; approve or reject), **Breaches** tab (B09: open a case with the severity
    default for the biometric pause shown and changeable; pause, release by another person, record a regulator notification, close), and
    **Biometric readiness** tab (B11: for a country and purpose, each method's six gates in plain words, the DPIA blockers, missing retention
    classes and pausing cases; read-only).
  - Operator console, Regulatory: **Biometric methods** (CR-107 lifecycle: open proposals with approve and reject, scopes with their state,
    propose the next step with the DPIA and evaluation references a pilot or production needs, pause or step down with a reason and evidence).
  - The API client gains `privacy.v2`, `verification.biometricEligibility` and the operator `regulatory` biometric method calls.

- **Erasure per biometric class (B05; QA-022, QA-023, QA-025)**: each class has a tier (SOURCE raw media, DERIVED templates, RESULT scores). An
  erasure job disposes in that order, then other personal data; providers' copies follow; backups are attested last (`BACKUP_BEFORE_SOURCES`
  while any first-party or provider task is pending or failed). Every task records the classes it covers (`erasure_task.asset_classes`,
  migration `privacy_0007`; a provider task covers every class of the subject's objects it holds), and the job and its proof carry
  `biometric_classes`: per class PURGED only when every covering task and the backups are acknowledged, else HELD, FAILED or PENDING.
  `POST /v2/privacy/erasure-jobs:preview` is a dry run (changes nothing; reads the data map as recorded) and `GET /v2/privacy/erasure-jobs` lists
  jobs. Web admin, Privacy: **Erasure** tab (dry run, open a job, per-class state, attest backups).
- **Applicant screens (B01, B02, B06)**: `GET .../subject-status` now also returns the purpose, region, chosen route and the exact notice (version,
  link, hash, acknowledged). `POST /v2/verification-sessions/{id}/route` lets the person choose a non-biometric alternative, or the camera again
  (`NONE`), with no reason asked and only for their own session (`SESSION_NOT_YOURS`). Web: **Account > Identity verification > session**
  (`/account/verification/session/{ref}`): what is checked and why, the notice to acknowledge, consent ONLY when the session relies on it and
  never pre-ticked, other ways to verify, stop the session. **Account > Privacy**: withdrawing a consent first explains what it does (future
  processing stops, past decisions are not reversed, nothing is deleted, another way remains); each request status says what it means; a person
  can ask for a human review of a decision.

## Not in this change
- **Raw biometric media access control (BIO-14, B10)**: the platform keeps no raw biometric media today (the self-run path keeps none; evidence
  uploads have no content read-back). There is nothing to gate yet; a reviewer media viewer must arrive behind JIT, case-scoped access.
- **Notice text in the session**: the notice is linked by its published address and hash; a notice published without an address shows its
  version only.
- **`biometric.policy.blocked.v1`** per refused session: refusals are visible in the eligibility answer and the session response; an event per
  refusal is not emitted.

## Owner decisions (proposed values, none ratified)
1. HIGH and CRITICAL breaches pause biometric capture by default; the opener may say otherwise.
2. Releasing a pause needs a second person and a reason; closing the case ends it.
3. The five asset classes above, and that a never-stored class still needs a retention decision.
4. State changes go on the priority lane.
5. The deletion tiers of the five classes, and backups attested last.
