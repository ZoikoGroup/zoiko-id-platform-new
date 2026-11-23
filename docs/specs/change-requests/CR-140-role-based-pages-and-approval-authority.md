# CR-140: role-based pages, and the way to an approving role and an approval limit

Source: owner request, 20 Nov 2026 ("design the role-based pages properly, keep it safe, based on what exists"). Builds on ZID-ENG-DEC-007 (role matrix), the approval engine (A2 privileged role grant) and ZID-COM-CL-004 (commitment approval limits). Status: built 20 Nov 2026. UI only; no server rule changed.

## The design, in three layers (unchanged, now stated)

1. **Role** (Owner, Administrator, Approver, Operator, Viewer, Member) decides which pages appear, from the role matrix (`PAGE_RIGHTS` in `nav-access.ts`). An Approver sees 24 of the 51 menu pages, an Operator 21, a Viewer 13. Hiding a page is courtesy; the server decides every call.
2. **Authority** (a commitment approval limit, a mandate, a grant) decides what a person may sign, and how much. Given by the Owner with a fresh strong sign-in.
3. **Billing access** (a Billing bundle) decides what a person sees in Billing.

Administrators cannot approve (the matrix gives them no approval right), so the approver is always independent.

## What was built (all UI)

- **Team page:** the invite dialog now says that an Approver, or an Administrator where the owners require approval, is granted by a request that one independent person approves, and links to it. Approving amounts and voting on requests needs the Approver role, and the console had no pointer to the existing "Grant a privileged role" request.
- **Approvals page:** `?new=<action id>` opens the New request dialog already on that kind of request (used by the Team page link).
- **Billing, Commitments tab:** the limits table has a "Sees the requests" column and a note. A limit does not show a person the commitment requests: listing them needs the billing overview permission, which comes from the Owner role or a live Billing grant. The Owner is told when a person holding a limit would find nothing to approve.

## Considered and not done

- Hiding the Mandates page actions by role: the page deliberately lets a mandate's creator and executor act on it (an object-level right the role matrix cannot express), so a role-only hide would hide actions people may take. The server still decides.
- A new "my commitment authority" read endpoint that shows Billing to limit holders: it would widen read access to billing data; the Owner giving a Billing bundle is the safer, explicit step.
- Promoting an existing member to Approver or Administrator: the server grants privileged roles only at invitation; it would be a new operation with its own approval.

Tests: `commitment-logic.test.ts` (who sees the requests).
