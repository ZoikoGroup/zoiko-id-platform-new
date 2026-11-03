# CR-049: Organization onboarding readiness and the approvals center (ZID-UX-WF-013C), slice L-13

Status: open (built). The wireframe is proposed and awaits founder approval; its API names are illustrative, so this CR records the names used.

## What changes
- **Approvals, organization-scoped** (`tenant/api/org_approvals.py`; the service is still `tenant/application/approvals.py` of CR-047):
  `GET /v1/organizations/{id}/approvals?scope=review|mine|history` with filters (level, state, created range, request id), a cursor and counts;
  `POST` to create; `GET /{requestId}`; `POST /{requestId}/decisions` with `APPROVE`, `REJECT` or `REQUEST_CHANGES`. The organization in the path is the
  caller's own or "no such organization" (the organization is the tenant).
- **Authorization first (AT-04, AT-10).** The inbox is built object by object: the requester sees their own requests, anyone who voted sees theirs,
  and the organization's approving roles (Owner, Approver, active reviewer) see the organization's. Everyone else gets no rows and zero counts, and a
  single request they may not see is "not found". Filters, cursors and counts are applied afterwards, so none can reveal what the caller may not open.
  The earlier `/v1/approval-cases` read routes apply the same rule. Tabs: *To review* (open, voteable, not yours, not already voted, you are eligible),
  *Submitted by me* (any state), *History* (ended; yours, voted on, or for approving roles any).
- **Decisions (AT-07, AT-11).** `If-Match` (the request's immutable digest) is required (428 without it); a stale one is 409 `VERSION_CONFLICT` and records
  nothing. An `Idempotency-Key` makes a retry return what was already done; a second vote by the same person is refused (and the database is unique on the
  person). Approve and Reject need the fresh action-bound step-up of CR-046/047 (S1, S2 for A2); Reject and Request changes need a reason.
- **Request changes (13C-03D).** An eligible approver ends the current version (new state `NEEDS_CHANGES`, tenant migration `0011`), every vote on it is void
  and the requester revises into a new version; no earlier vote or proof is reused. It needs a reason and no step-up (it grants nothing).
- **Execution hold (13C-03E, AT-09).** For an approved request that cannot run (policy version changed, region changed, organization not serving, or a gate
  the platform checks no longer holds) the case view carries a derived `execution_hold`; the votes stay and nothing runs.
- **Readiness and activation** (`onboarding/api/readiness.py`, `application/readiness.py`, `domain/readiness.py`; onboarding migration `0005`; `config/onboarding/setup-policy.yaml`):
  `GET /v1/organizations/{id}/onboarding`, `POST .../onboarding/acknowledgments`, `POST .../activation`. Readiness is **derived each time and never stored**:
  four read-only trust assertions (legal entity, your identity, representative authority, screening) from the company-verification claim and the person's
  identity assertion; three required items checked by the server (organizational controls accepted at the current version, every Owner and Administrator holds a
  passkey, region and policy confirmed); and approver readiness, which is recommended and never blocks ordinary use. States: DRAFT, LIMITED_SETUP,
  READY_FOR_ACTIVATION, ACTIVE, and the derived ACTIVE_LIMITED (a fact activation rested on no longer holds, or a new policy text is unaccepted) and RESTRICTED (a
  check was declined). A held or stale check is "In review" or "Needs action", never "Verified". Activation passes only when every trust check is verified and
  fresh and every required item is done; there is no payment, plan or override input. A refusal is audited and returns the reasons and next actions.
- **Plain words (13C section 13):** Required, In review, Verified, Needs action, Restricted; Pending approval, Approved (waiting to be applied), Executed,
  Denied, Needs changes. "Approved" is never used for a register match.
- **Events** (declared in `proposed-events.yaml`): `governance.approval.changes_requested`, `onboarding.organization.acknowledged`, `onboarding.organization.activated`.
- **A defect found by running it live:** the web proxy (`/api/bff`) allowlists the cell paths a screen may reach, and it did not include `/v1/organizations/`,
  `/v1/approval-cases`, `/v1/approval-policy`, `/v1/governance/`, `/v1/step-up/`, `/v1/org-claims` or `/v1/entitlements`. The Phase L screens built earlier
  (approvals, step-up proofs, company claims, plan features) passed their tests, which mock the client, but could not have worked for a real signed-in person.
  The prefixes are added, with a test that pins them (`bff-allowlist.test.ts`). Reviewer, operator and sign-up relays are deliberately not on the list.
- **Screens:** `/organizations/{id}/setup`, `/organizations/{id}/approvals`, `/organizations/{id}/approvals/{requestId}` (`current` for your own), and a
  navigation entry for Owners and Administrators; the earlier `/admin/approvals` pages now lead to the same inbox.

## Conflicts between the record and what was built
- **Names.** The record's `approval.requested/decided/executed` events are `governance.approval.*`; `APPROVED_PENDING_EXECUTION` is `APPROVED_READY`; `DENIED` is
  `REJECTED`; `CANCELED` is `CANCELLED`; `EXECUTION_HELD` is a derived hold, not a stored state.
- **"Request changes" versus "reject".** Both end a version; only request-changes lets the requester revise it into the next version.

## Known limits, stated plainly
- **Readiness does not gate any other capability yet.** Activation records that the organization passed its gate and the readiness state is derived from it,
  but no other route consults ACTIVE, ACTIVE_LIMITED or RESTRICTED. What each state unlocks or limits is an owner ruling and a follow-up.
- **Activation cannot pass for a real organization today.** It needs a verified person identity, and the progressive-verification slice (L-6) was set aside at
  the owners' request; only an identity assertion from the identity-proofing flow (L-5) counts. The tests supply the facts directly.
- **Evidence attachments (AT-12) are not built.** There is an evidence reference on a request and a statement that restricted evidence is separately authorized;
  there is no upload, malware scan, expiring link or viewer.
- **Reminders** are not sent (a request expires; no reminder runs). Expiry is applied when a request is next touched.
- **The trust header shows the state word, the next action and the setup policy version**, not each assertion's own policy version and freshness (the claim
  service does not expose them per check); a stale check does show as Needs action.
- **Re-checks at execution** cover policy version, region, tenant state and the gates of CR-047, not a live sanctions or legal-rule lookup.
- **Browser evidence** for the three screens is a Chromium run (axe WCAG 2.2 AA at desktop, 320 px, landscape and 200 percent zoom, with a real signed-in
  session); there is no screen-reader run on a real device.
