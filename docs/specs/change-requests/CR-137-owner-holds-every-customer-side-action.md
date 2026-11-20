# CR-137: a company's owner holds every action by default

Source: owner decision, 20 Nov 2026. Builds on CR-132 (document-verified companies are rooted at AR3) and the setup-time owner grant of WF-013C. Status: built 20 Nov 2026.

## The decision

Every company's OWNER is granted every customer-side action in the action catalogue, so the owner can also issue and delegate grants (`authority.grant`, `authority.grant.delegate`), which the previous fixed list of 52 administrator actions did not include. **Scope, widened the same day:** first everything except what belongs to Zoiko staff (153 of 168); then the owner asked for the remaining 15 too, so the owner holds ALL 168 actions.

## What was built

- `owner_actions(catalog)` (onboarding domain): every action in the catalogue, sorted (168 today). The standing administrator list is a subset of it. The 15 actions first left out (`assurance.*`, `policy.release.*`, `operator.jit.elevate`, and the KYB reviewer actions `organization.kyb.review`, `.assess`, `.appeal.decide`, `.escalation.decide`) are included after the owner's second instruction.
- `_seed_and_advance` issues the owner's setup grant with `owner_actions` instead of the fixed list, for every path that establishes an organization.
- Worker step `owner_grant_top_up`: for every established company, an active OWNER who lacks some of `owner_actions` is issued ONE more top-level grant with just the missing actions, through the normal issuance gate. A grant's scope is immutable and replacing the standing grant would orphan the administrators delegated from it, so nothing is edited or superseded. Repeating adds nothing; actions added to the catalogue later are picked up the same way.

## Risk accepted

Every company's owner can now hold the actions that review and decide a company's own KYB cases and appeals, publish policy releases and run the platform assurance actions, and can delegate any of it, and (with CR-132 and CR-134) a certificate-verified company's owner has full authority with no authority check and no review.

Tests: `tests/unit/test_owner_actions.py`, `tests/component/test_org_claim_dev_shortcut_pg.py`.
