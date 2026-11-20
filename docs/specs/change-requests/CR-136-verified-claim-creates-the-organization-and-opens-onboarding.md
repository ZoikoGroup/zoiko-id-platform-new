# CR-136: a verified claim creates the organization and opens the onboarding by itself

Source: owner decision, 20 Nov 2026. Builds on CR-134. Status: built 20 Nov 2026.

## The decision

When a claim's two checks (the company and the applicant's identity) are both complete, nothing waits for a person, at the company or at Zoiko iD. The organization is created at once and the applicant lands on the company onboarding.

## What was built

- The claim status screen's establish card (`EstablishCard`) now runs the existing `establish` call by itself, once, for any VERIFIED, open claim, and then goes to `/admin/onboarding`. Before, only the local development shortcut did this and everyone else pressed "Create the organization". A failure is shown with the button to try again, and is never retried in a loop.
- The status page no longer takes a verified claim to `/overview` (the old welcome redirect), because it would race the establish call.
- The server is unchanged: `establish` still refuses a claim that is not VERIFIED, is idempotent, and activates only the claimant's pending OWNER role.

Tests: `frontend/apps/web/src/features/onboarding/org-claim-establish.test.tsx`.
