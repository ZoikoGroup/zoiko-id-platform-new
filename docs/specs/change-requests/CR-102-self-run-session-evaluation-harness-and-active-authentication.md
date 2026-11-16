# CR-102: Self-run verification, evaluation harness, engine adapters, Active Authentication, re-verification sweep, identity feed and ownership chains (builds on CR-095 and CR-101; ZID-GAP-001 DTV-06, DTV-12, DTV-18, DTV-20 to DTV-22, DTV-24, DTV-29, DTV-30)

Status: **built on the engineering owner's instruction; the self-run routes are OFF by default (`ZID_SELF_RUN_ENABLED`) and the biometric engine is still OFF and cannot be switched on without an evaluation.** Nothing here is a regulated service, and no regulator, lab or owner has accepted this path. The sign-off table is UNSIGNED.

## Why

CR-095 built the rules for verifying a person with no Veriff and no Sumsub, but nothing could start such a check: no route, no session state, nothing to measure an engine with, no engine adapter and no clone detection. This change builds the code that can be built without a vendor, and leaves the people's work (evaluation, DPIA, legal acceptance) where it is.

## What was built

- **Self-run session** (`verification/application/service.py` `start_self_run`, `submit_self_run`; `verification/api/v2_self_run.py`):
  - `POST /v2/self-run/verifications` starts a session through the same gates as a provider session (country, policy pack, privacy, processing purpose). No provider is chosen or called; the session waits IN_PROGRESS with method `self-run`.
  - `POST /v2/self-run/verifications/{id}/chip` takes the chip's signed object and data groups, passively authenticates them, and lets the profile decide exactly as for a provider's result. A chip that cannot be judged (no country signing CA configured) leaves the session waiting. A chip alone never binds the holder, so a profile that needs binding is not satisfied. A decided session never changes, and a session is judged once.
  - Nothing from the chip is stored or echoed. Both routes answer 404 `SELF_RUN_NOT_ENABLED` unless `ZID_SELF_RUN_ENABLED` is set. No migration.
- **Evaluation harness** (`verification/domain/biometric_evaluation.py`): false accept, false reject, APCER (worst attack species) and BPCER from labelled trials, each as the Wilson 95% upper bound, so a small sample cannot look good (about 3,000 clean impostor trials are needed to show 0.1%). Rates per cohort with a disparity flag and an INSUFFICIENT_DATA state, never averaged away. It drafts the numbers part of `self-run.yaml`, never `enabled`, the evaluation id, the DPIA reference or the licence statement.
- **Engine adapters** (`verification/infrastructure/engine_adapters.py`): DeepFace face match, a Silent-Face liveness wrapper, and a composed engine with one identity. Libraries are imported at call time; a missing library or any failure raises (PENDING), never a score. No InsightFace adapter is shipped (non-commercial models).
- **Active Authentication, ECDSA** (`evidence/application/active_auth.py`): verifies a chip's signature of a server challenge against the DG15 public key, so a perfect copy of the data is a suspected clone. It counts only if DG15 was covered by the signed object. RSA keys answer NOT_ASSESSED.

## The rules (each has a test)

1. The routes are off by default and answer 404 when off.
2. A chip that cannot be judged keeps the session waiting; it is never an approval.
3. A chip alone does not verify a profile that needs binding.
4. A small sample never shows an error rate under the ceiling; an invalid trial refuses the whole evaluation; the order of trials never matters.
5. A cohort disparity is a reason to look, never a verdict.
6. An adapter raises rather than invent a score; a result carries one engine identity.
7. A signature that does not verify is CLONE_SUSPECTED, never a pass, and never a decision about the person.

## Added in the second slice

- **Active Authentication is wired.** Starting a self-run session returns `aa_challenge` (8 random bytes in hex, stored with the session). The chip route accepts `active_authentication` (the chip's signed answer, plain r||s) and DG15 among the data groups. A genuine answer adds `SELF_RUN_CLONE_CHECKED`; an answer that does not verify adds a fraud signal and `SELF_RUN_CLONE_SUSPECTED`, so the session fails or goes to review (a clone signal never auto-passes); without the answer the reason is `SELF_RUN_CLONE_NOT_CHECKED`. Every attempt that leaves the session waiting replaces the challenge, so a captured answer cannot be replayed.
- **The face and liveness path is wired.** The chip route accepts `face` (a live image and a liveness capture). They are used only when the deployment has an accepted engine (`app.state.biometric_engine` and a complete evaluation record) and the chip photo, found in DG2 by `evidence/application/dg2.py`, came from a data group the signed object covered. With no accepted engine the media is ignored and the reason is `SELF_RUN_BIOMETRICS_OFF`. Nothing is kept.
- **Re-verification sweep** (`verification/application/reverification.py`, worker step `verification_reverification`): an assertion past its date becomes EXPIRED (`verification.assertion.expired`), one resting on a quarantined or retired provider becomes STALE (`verification.assertion.stale`, reason `PROVIDER_NO_LONGER_TRUSTED`), and one alert per person and claim is raised when a verification lapses within 30 days. Before this, `expire_due` was never called. It never revokes, verifies or starts a verification.
- **Identity feed hook**: `verify.identity_observation` (`verification_0018`) keeps a keyed digest of the device a verification started from (the `x-zid-device` header) against the person, only when the cell has a pepper. The worker step `identity_signal_feed` runs the CR-098 link rules over the recent observations and opens review items; observations older than 90 days (PROPOSED) are dropped. A link of one kind of identifier alone (WEAK) opens nothing: a household is not a signal.
- **Ownership chains**: `org.ownership_graph` (`organization_0008`, append-only, row-level security), `/v2/ownership-graphs:analyze`, `/v2/ownership-graphs` (record the next version, refused if it loops or is over-owned), list and read, actions `org.ownership.manage` and `org.ownership.read`, and the console `/admin/ownership`. Entities are opaque ids and a kind, never names. Findings are candidates and gaps worked out again from the version each time.
- **Workflow run screens**: `/admin/workflows/runs` and `/admin/workflows/runs/{id}` (what a run waits for, recording the awaited check, a review by someone other than the applicant, the results as outcome words and the trace).

## What it does not do

- **No phone app and no capture flow.** Nothing reads the chip or captures a face; the routes only judge what a client sends.
- **RSA Active Authentication, Chip Authentication and PACE are not built.** Only the ECDSA verifier exists.
- **No no-chip path.** A document with no chip is not accepted on this route. What a reviewer may approve from text alone is a product and legal decision, because a review approval with no verified claim would otherwise mark someone verified.
- **No evaluation.** The harness is arithmetic. The trials, the independent lab, the DPIA and the licence check are people's work.
- **The identity feed sees devices only.** No phone or email is collected at verification start, so those kinds are not observed yet.
- **Issuer-side credential expiry is not part of this sweep**; it covers verification assertions.

## Owner decisions still needed

0. Retention of identity observations (90 days is PROPOSED) and which further events may be observed (Privacy).
1. Switch on `ZID_SELF_RUN_ENABLED` for which environments and countries (Legal).
2. Which countries' master lists to import and trust (`config/trust/csca/` ships empty, so every chip is INDETERMINATE).
3. Whether a document with no chip may go to review, and what a reviewer may approve from it.
4. The cohort labels and the disparity ratio (provisionally 2.0) for the evaluation.
5. Who commissions the evaluation and the presentation-attack test.

## Evidence

`backend/app/tests/component/test_self_run_session_pg.py`, `test_reverification_pg.py`, `test_identity_observation_pg.py`, `test_ownership_graphs_pg.py`, `backend/app/tests/unit/test_self_run_extras.py`, and the screens' tests under `frontend/apps/web/src/features/admin/workflows/` and `.../ownership/`.

## Sign-off (UNSIGNED)

| Role | Name | Date | Decision on the five questions above |
|---|---|---|---|
| Product owner | | | |
| Security owner | | | |
| Privacy owner | | | |
| Legal | | | |
