# CR-036: The owner completes onboarding without a second administrator

- **Target:** ZID-OVERLAY-001 §4, §25-26 (onboarding plans, pilot and activation need a second administrator) · **Raised by:** Console wiring (onboarding page) · **Status:** Open (built)

## Gap

Four onboarding steps each need an independent second administrator: approving a plan (`tenant.onboarding.manage`), starting a pilot, activating the company (`tenant.onboarding.activate`) and activating a pilot scope. A company with one person, its owner, could not complete onboarding at all.

## Change

Setting `ZID_ONBOARDING_APPROVAL`: `second_admin` (default, unchanged) or `owner`.

In owner mode the company's active OWNER approves those four steps alone: the page shows one button instead of the request-and-wait flow, and the API accepts the step without an approval request. Anyone else, including an administrator who is not the owner, still needs the independent second administrator. The owner's own authority is checked as always (`protected_action`), so the steps still require the grants and a fresh strong sign-in; only the second person is removed.

The database refuses an approver equal to the author, so an owner-approved step is recorded as approved by the fixed principal `OWNER_APPROVER` (`00000000-0000-4000-8000-0000000000d2`); the real owner is the actor in the audit record.

`GET /v1/onboarding/mode` returns `{approval, self_approve}` for the caller, which is how the page decides what to show.

| Deployed cells | |
|---|---|
| local, test, dev | `ZID_ONBOARDING_APPROVAL=owner` is enough |
| any other environment | also needs `ZID_ONBOARDING_OWNER_ACK=accept-no-second-admin`, or the cell refuses to start |

## Rules

- The setting is cell-wide, a decision to give up the second pair of eyes for onboarding; revisit it when a company has more than one administrator.
- Nothing else is relaxed: rollback, recovery, evidence holds, owner changes and every other approval keep their rules.

Tests: the owner approves a plan alone and it is recorded as `OWNER_APPROVER`; a non-owner is still refused; the default mode still needs an independent approval; a deployed cell refuses owner approval without the acknowledgement.
