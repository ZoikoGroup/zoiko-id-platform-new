# CR-092: conformance behaviour for T20, T30, T40, T41, T42 and T44

Status: open (built and tested; T12 and T43 stay PARTIAL, see "Not in this change").

## Why
ZID-RCG-003 section 12 cases T20, T30, T40, T41, T42 and T44 were PARTIAL because the platform did not yet do part of what the case requires. Each
is built on an existing mechanism (audit, outbox events, the worker passes, the alert store, the ports); nothing parallel was added.

## What changes
- T20, stale proof: `GET /v1/credentials/assertions/{id}` and `GET /v2/credentials/{ref}` judge the stored proof at read time and return
  `proof_status` CURRENT, EXPIRED (its own validity ended) or RECHECK_REQUIRED (the source answer is older than the freshness, 365 days as in
  `claim_checks.Gate`, or its time cannot be read). On /v2 a stale proof's dimension is lowered (VERIFIED becomes EXPIRED or INDETERMINATE) and carries
  the status as a limitation, so a cached VERIFIED is never returned. Pure rule in `credential/domain/lifecycle.py` (`proof_status`).
- T42, an issuer or accreditation body loses its status: the credential worker step compares each issuer's last two ledger statements
  (`issuer_history.status_lost`: revoked as of today, official authority lost, an accreditation in force no longer in force). For each loss it flags
  every current proof issued under that issuer, or under an institution that listed it as its accrediting body, makes the source recheck due now
  (the existing daily `credential.claim.recheck.due` then carries the reason), emits `credential.proof.review_required` once per proof and raises one
  mandatory alert per tenant. One statement flags a proof once (migration `credential_0014`: three nullable columns on `cred.claim_monitor`).
- T40, regional policy change: a verification worker step (`verification_policy_review`, `ZID_REGULATORY_PACKS` off disables it) re-evaluates the
  sessions that had not finished against the pack that governs their recorded market now, and the provider's suspension. A different pack still
  governing: the session carries on and the change is recorded. No pack, or the provider suspended: a session already with a provider goes to MANUAL_REVIEW,
  one not yet started stays and is recorded; both as REVIEW_REQUIRED. One `verification.session.policy_reevaluated` notice per affected session to its
  own organization; finished sessions and other markets are untouched; the same change is evaluated once (migration `verification_0014`:
  `policy_review`). `regulatory_basis` stays write-once.
- T30, consent withdrawal: `withdraw_consent` now opens an ERASURE rights request scoped to the consent's purpose when the data map holds anything of
  the subject kept for it, and fulfils it at once when the session already verifies the subject (otherwise it waits for verification like any erasure).
  It reuses the whole erasure workflow: legal holds, legal-obligation retention, processor acknowledgement, events and the completion record. Data kept
  for another purpose, the vault, the caches and the consent record itself are not touched. Entries whose purpose another live consent or a
  non-consent processing activity still covers are kept and itemised as RETAINED_OTHER_BASIS (the request then ends PARTIALLY_DENIED).
- T41, active content: a PDF carrying script, a launch or open action, an embedded file, rich media, an XFA form or a form submission (names are
  normalised for `#xx` escapes and a header anywhere in the first kilobyte) is refused before the scanner at evidence upload and before the validator at
  `POST /v2/documents/validate`, with a DENIED `evidence.document.quarantined` audit record (path, kinds, digest, size; never the content). Upload
  reject reason `ACTIVE_CONTENT`; validate answers 422 `DOCUMENT_QUARANTINED`. Nothing is kept: quarantine here means refused and recorded.
- T44, disparity check: automated name matches record their cohort (the writing system of the name) and the automated answer in the assertion.
  `check_disparity` (worker step `fairness`, `ZID_FAIRNESS_CHECK`, default off) compares the automated NOT_MATCHED rate per cohort with the lowest
  cohort's and holds, in `cred.fairness_hold` (migration `credential_0015`), a cohort whose gap reaches the threshold. A held cohort's automated
  NOT_MATCHED becomes AMBIGUOUS, the existing route to a person; no other answer changes. The threshold is OWNER content
  (`config/credential-trust/fairness-thresholds.yaml`): it ships PENDING_OWNER_APPROVAL with no numbers, and without an approved one the check fails closed
  and holds every cohort that has outcomes. Cohorts below the approved minimum size are reported, not held. A newly held cohort is announced once
  (`credential.fairness.path_held`) with a mandatory alert.
- New outbox events declared in `contracts/asyncapi/proposed-events.yaml`: `credential.proof.review_required`,
  `verification.session.policy_reevaluated`, `credential.fairness.path_held`. The audit-only name `evidence.document.quarantined` lives in a domain
  file that does not import the outbox. The conformance matrix marks T20, T30, T40, T41, T42 and T44 COVERED with the tests that assert each.

## Not in this change
- T12 stays PARTIAL (owner decision on 403 versus 404, see CR-090).
- T43 stays PARTIAL. What an access export withholds by the requester's role and by the legal basis of each record is a product and legal decision
  that no document in the repository states; building a filter without it would invent the policy. The existing export (readable summary, no hashes
  or keys) is unchanged.
- T40: the provider side is the kill switch (suspension) only; a change to a provider's entitlement or to a market release is not re-evaluated against
  pending sessions.
- T44: the owners must approve a threshold and decide whether to turn `ZID_FAIRNESS_CHECK` on; the metric is the automated adverse rate by name script
  only (no other protected attribute is recorded). The hold clears when a later evaluation no longer finds the disparity and a threshold is approved.
- T41: `/v2/extract` (PDF form reading) does not yet apply the same gate.
