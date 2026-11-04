# CR-052: A company claim over its whole life: authority relationship, submit, withdraw, abandon, outcome labels, events (ZID-UX-WF-013B), slice L-12

Status: open (built). The wireframe is proposed and awaits founder approval; its API names are illustrative, so this CR records the names used.

## What changes
- **How the person acts for the company.** The screening declaration (`POST` and `PUT /v1/org-claims/{id}/screening`) now says `relationship`:
  `DIRECTOR_OR_OFFICER` (the default and today's behaviour: the server finds an authority root the person holds, which exists only when they were
  matched to a current officer of the register), or `DELEGATED_EMPLOYEE` / `EXTERNAL_REPRESENTATIVE`, which act under a mandate: `mandate_proof_id` (a
  letter of authority, board resolution or the new `SIGNING_MATRIX` kind, uploaded to this claim and checked by the server) and `mandate_expires_at`
  (in the future). Anything else is refused (`AUTHORITY_PROOF_REQUIRED`, `MANDATE_EXPIRY_INVALID`); an officer who sends a mandate is refused.
- **A mandate is the applicant's own document, so it proves nothing until a person confirms it.** The decision keeps the case in review
  (`AUTHORITY_MANDATE_REVIEW`); a reviewer who has read the document (an audited read) rules `VERIFIED` with `confirm_mandate: true`, which marks that mandate
  confirmed. The confirmation belongs to that mandate: a new mandate starts unconfirmed, and it is remembered in every later record so a rescreen or an
  appeal never loses it. The clear outcome never outlives the mandate (`bounded_expiry`), and an expired mandate is unproven even if it was confirmed.
  Only a ruling that verifies, and only for a case that rests on a mandate, can confirm one (`MANDATE_NOT_CONFIRMABLE`). This replaces the earlier rule in
  the decision, under which a declared mandate with a future date verified by itself.
- **Submit** `POST /v1/org-claims/{id}/submit` (202, once, a repeat changes nothing): the applicant says the claim is complete. It must already say whom to
  screen (`CLAIM_INCOMPLETE` otherwise). It is accepted for review, not decided; the checks are the same checks.
- **Withdraw** `POST /v1/org-claims/{id}/withdraw`: nothing is deleted, nobody is watched for the claim any more (the screening case is closed; the worker
  skips it), and the applicant may start a new claim. A verified claim is not withdrawn here (`CLAIM_VERIFIED`; that is a governed change). A repeat is the same answer.
- **Abandon:** a claim nobody submits within `draft_days` (30, in `config/kyb/kyb-routing.yaml`; an engineering proposal for the owners) is closed as abandoned by
  the worker, so a half-made claim does not sit there watching people. Submitted claims are not abandoned: they are waiting for a person, not for the clock.
- **A closed claim takes nothing more:** refresh, declaration, appeal, submission and documents are refused with `CLAIM_CLOSED`, and so is a reviewer's ruling.
  Organization migration `0007` (submitted and closed columns), screening migration `0003` (`closed_at`).
- **Two more outcome labels.** A check that was complete and has lapsed is `STALE` (outcome STALE), and a screening a review suspended is `RESTRICTED` (outcome
  RESTRICTED). Precedence, strongest first: DECLINED, RESTRICTED, IN_REVIEW, STALE, NEEDS_EVIDENCE, VERIFIED (all four complete), otherwise PROVISIONAL; a
  closed claim reads WITHDRAWN or ABANDONED. The readiness view maps STALE to "Needs action" and RESTRICTED to "Restricted". Status adds `state` (DRAFT,
  SUBMITTED, WITHDRAWN, ABANDONED), `relationship`, `submitted_at` and `closed_at`.
- **The status carries an ETag** of exactly what it says (a bare 304 for a poll that sends it back) and is never cached. The status and outcome logic moved to
  `onboarding/application/claims.py`; reads change nothing, and write routes remember what they saw.
- **Events** (proposed-events.yaml; ids and coarse states only): `organization.claim.source_verified`, `authority_evaluated`, `risk_assessed` (a check changed
  state), `decision_issued` (VERIFIED, DECLINED or RESTRICTED was reached), `submitted` and `closed`. The last label and checks seen are kept
  (`platform.org_claim_outcome`, onboarding migration `0008`), so a change is announced exactly once whoever caused it (a reviewer, the worker, the applicant,
  the passing of time); the worker follows every open claim each pass (`claim_outcomes`) and closes drafts (`claim_housekeeping`).
- **Web:** the authority page asks how the person acts for the company and, for a delegate, the document and its end date; the status page shows the claim's
  state, a Send for review button, Withdraw (with a confirmation) and the closed state with a way to start again.

## Not built, and why
Creditsafe (no contract), Dun and Bradstreet (not qualified), the EU business register and the national registers of Germany and Ireland (no access agreed), and
proof by a verified legal-entity credential (vLEI role credentials need the GLEIF credential ecosystem to be verified, which is a separate trust service).

## Tests
`test_org_claims_lifecycle_pg.py` (5), `test_screening.py` (the mandate rules), `test_org_claim_outcome.py`, `test_readiness.py`, and the web logic tests.
