# CR-118: A company's WF-05 verification policy (ZID-DTV-WF-05 A11)

Status: open (Step 7 of the WF-05 build).

## What changes
- **Policy per country and purpose** (`verification/domain/check_policy.py`, `verify.check_policy`, migration `verification_0027`): the checks every
  case must ask for, and the proof of address documents accepted with their maximum ages. DRAFT, SUBMITTED by its author, then APPROVED or REJECTED
  by a different person; an approval supersedes the earlier approved policy; at most one approved per country and purpose; the table refuses edits.
- **Only narrows**: a document type must be one the platform accepts for that market, and its age can only be shorter. Requiring a check never
  switches it on: activation stays with the method lifecycle (A11 "request, not unilaterally activate").
- **Effect**: a new case asks for the approved policy's required checks; a proof of address document is limited to the policy's types and ages.
- **API**: `GET/POST /v1/check-policies`, `POST .../{id}:submit` (verification:execute), `:approve` and `:reject` (verification:review).
- **Web**: a "Verification policy" card on Admin -> Verification checks.

## Not yet
- Quotas, budget caps and staged rollout per policy (A11 finance and change control): no commercial cap store for WF-05 methods yet.

## Evidence
`tests/unit/test_check_policy.py`, `tests/component/test_check_policy_pg.py`.
