# Acme Widgets Limited: roles and approvals rollout

Written for: the Acme owner and the Zoiko operator setting Acme up.
Based on Decision 07 (roles), Decision 10 (A0/A1/A2), Closure 04 (purchase bands) and Wireframe 10 (delegation). All limits below are the documents' proposals until the Founder, CFO and Legal sign them.

## The rule that drives everything

The person who asks is never the person who approves. A2 needs two independent people. Nobody can be both maker and checker, so Acme needs at least three real people before any critical change can happen.

## Who Acme needs

| Person | Role | Job |
|---|---|---|
| 1 owner | OWNER | Accountable; approves A1; first vote on A2 |
| 1 admin | ADMIN | Invites people, raises requests (cannot approve own) |
| 2 approvers | APPROVER | Independent checkers. Without these, every A1/A2 request is blocked |
| 1 billing person | Billing grant from the owner | Pays invoices; limited spend |
| staff | MEMBER / OPERATOR | Daily work, no approval power |
| 1 auditor | VIEWER | Read-only evidence |

## Steps

1. **Invite people with names.** Team, Invite person, fill the name. Each gets a permanent number (M-0002 ...) and shows as "Name (M-0002)" everywhere.
2. **Each person creates their own passkey** from the single-use link (24 hours). Passkeys are never shared.
3. **Appoint two APPROVERS.** Request the APPROVER role grant. It is A2: it needs two independent approvals. With only the owner, it will show "blocked: no independent approvers". The first approvers are therefore put in by the operator-assisted onboarding route, not by a fake second vote.
4. **Name independent reviewers** (Approvals, Reviewers) for organisation-level decisions.
5. **Give the billing person a spend limit** (owner action, with step-up). Purchases up to USD 2,500 can be approved by the owner or a billing person whose limit covers it. USD 2,500 to 25,000 need the owner or a named Procurement Approver (not the requester). Above USD 25,000 needs two people.
6. **Rehearse in shadow mode.** Leave `route_matrix_enforcement` at REPORT_ONLY for a few weeks and read the "would have been refused" decisions.
7. **Decide enforcement.** Once the approvers exist and the report is clean, the owners rule on `route_matrix_enforcement`, `direct_admin_invitation` and the commitment `enforcement` switch. These are owner decisions; nothing flips automatically.

## Worked example: a ₹1 lakh purchase (about USD 1,200)

1. The billing person requests the upgrade. The system classifies it as a commitment in the lowest band.
2. Their spend limit covers it, or the owner approves. The requester cannot approve their own request unless they are the owner.
3. Approval, then application, then evidence in the audit ledger. If the quote changes, the approval is void.

## Checks before going live

- Every A2 path has two eligible people who are not the requester.
- The last owner cannot be removed (enforced).
- Revoking a grant takes effect on the next privileged request.
- The audit ledger shows maker, checkers, decision times and outcome.
