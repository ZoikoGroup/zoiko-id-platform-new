# CR-137: a company's owner holds every customer-side action by default

Source: owner decision, 20 Nov 2026. Builds on CR-132 (document-verified companies are rooted at AR3) and the setup-time owner grant of WF-013C. Status: built 20 Nov 2026.

## The decision

Every company's OWNER is granted every customer-side action in the action catalogue, so the owner can also issue and delegate grants (`authority.grant`, `authority.grant.delegate`), which the previous fixed list of 52 administrator actions did not include. Chosen scope: everything except what belongs to Zoiko staff.

## What was built

- `owner_actions(catalog)` (onboarding domain): the standing administrator list plus every other catalogue action, minus the staff-only ones: `assurance.*`, `policy.release.*`, `operator.jit.elevate` and the KYB reviewer actions `organization.kyb.review`, `.assess`, `.appeal.decide`, `.escalation.decide`. Today that is 153 of 168 actions. The administrator list is never reduced.
- `_seed_and_advance` issues the owner's setup grant with `owner_actions` instead of the fixed list, for every path that establishes an organization.
- Worker step `owner_grant_top_up`: for every established company, an active OWNER who lacks some of `owner_actions` is issued ONE more top-level grant with just the missing actions, through the normal issuance gate. A grant's scope is immutable and replacing the standing grant would orphan the administrators delegated from it, so nothing is edited or superseded. Repeating adds nothing; actions added to the catalogue later are picked up the same way.

## Risk accepted

An owner can now delegate any customer-side authority, and (with CR-132 and CR-134) a certificate-verified company's owner has full authority with no authority check and no review.

Tests: `tests/unit/test_owner_actions.py`, `tests/component/test_org_claim_dev_shortcut_pg.py`.
