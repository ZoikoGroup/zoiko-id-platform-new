# CR-109: A biometric method rests on a live independent evaluation, and a reversed decision needs a checker (ZID-DTV-WF-04 BIO-10, BIO-11, B08)

Status: open (domain, storage, service, API, event catalogue entry, screens and unit tests built; the PostgreSQL component suite, including the two
updated review tests, was not run on the authoring machine; the owners confirm the points below). Builds on CR-107 and CR-108.

## Why
Wireframe 04 BIO-10 and BIO-11 and QA-015: a model upgrade without an independent assessment and a signed approval is refused; "supplier marketing
claims do not count". CR-107 kept the evaluation reference of a method approval as a string, the same weakness CR-107 removed for the DPIA. B08 and
QA-020, QA-031, QA-032: a contested biometric decision can be reversed by an authorized reviewer, the operator cannot review their own contested
decision, a two-person control applies to material exceptions, the override is a new immutable entry, and "revoked/updated trust claim cannot leave
stale authorizations". The review service had the reviewer-is-not-the-requester rule, but a single reviewer could reverse a decision, nothing
announced the change to whoever relied on the decision, and there was no reviewer queue or screen.

## What changes
- **Eligibility gate 7, independent evaluation**: for each method, the engine `id@version` must be in the TENANT's model inventory with the kind
  the method needs (FACE_MATCH: `face_match`, LIVENESS: `pad`), ACTIVE, and its latest evaluation must meet the promotion rules (independent, at
  least two cohorts, the accessible alternative confirmed, every measure with a confidence interval, not stale). Otherwise the method is refused
  with `EVALUATION_NOT_READY` and the blockers (`MODEL_NOT_IN_INVENTORY`, `MODEL_KIND_MISMATCH`, `MODEL_HELD`, `EVALUATION_NOT_INDEPENDENT`,
  `EVALUATION_STALE`, ...). The answer carries the evaluation id. `privacy.ports.method_evidence`.
- **Review checker** (migration `privacy_0008`): upholding closes a review as before. Overturning or correcting is only proposed
  (`AWAITING_CHECKER`, `proposed_outcome`); a third person, neither the requester nor the reviewer, confirms it (`POST
  /v2/privacy/reviews/{id}/checks`, `confirm: true`) and it takes effect, or sends it back (`confirm: false`; OPEN again, the proposal on the audit
  record). The table repeats the rules; the "a reversal has its checker" constraint is NOT VALID so earlier reviews are not rewritten.
- **Event** `privacy.decision.changed` (proposed catalogue, priority lane) when a reversal takes effect: review id, decision reference, outcome and
  correction reference only. `privacy.review.decided` is now sent when the outcome takes effect, not when it is proposed.
- **Reviewer queue**: `GET /v2/privacy/reviews[?state=]`, oldest due first.
- **Screens**: web admin, Privacy: **Appeals & reviews** tab (queue with due dates and overdue flags, review dialog with the person's statement,
  uphold or propose a reversal, check dialog with the reviewer's rationale, confirm or send back; the requester and the reviewer are not offered
  actions on their own case). Biometric readiness shows the evaluation gate.
- **Test fixture**: `onboard(..., with_third=True)` adds a third administrator for checker tests; the default is unchanged.

## Not in this change
- **Downstream invalidation itself**: the event tells consumers a decision changed; no verification claim is revoked automatically.
- **The person's own view of their reviews** (B08 "explain outcome to applicant"): the requester can read a review by id; there is no list
  for them yet.
- **Evaluation in the method approval**: the operator-plane approval still records the evaluation reference as given; the tenant's live inventory
  is what counts at run time.
- **B07 fairness evaluation screen** and evaluation states (NOT_RUN, DATA_INSUFFICIENT, PASS_WITH_LIMITS, FAIL).

## Owner decisions (proposed values, none ratified)
1. FACE_MATCH rests on a `face_match` model and LIVENESS on a `pad` model.
2. Every reversal (overturn or correction) needs a checker; upholding does not.
3. `privacy.decision.changed` goes on the priority lane.
