# Organization onboarding and the approvals center: what only the owners can close (ZID-UX-WF-013C; slice L-13, CR-049)

1. **Founder approval** of the 13C wireframe and its sixteen acceptance tests.
2. **What each readiness state refuses: built as a proposal in report-only mode (CR-053).** `backend/config/onboarding/readiness-policy.yaml` says that a RESTRICTED
   organization may only recover a privileged account, ask for a trust decision to be looked at again, appoint an independent reviewer, remove an Owner and reinstate
   itself, and that an ACTIVE_LIMITED one takes on no new privilege (no new Owner, no privileged role, no powerful agent). Confirm or change the lists, then set
   `enforcement: ENFORCED`; until you do, the default `REPORT_ONLY` lets everything through and records what it would have refused.
3. **Setup texts and versions** (`backend/config/onboarding/setup-policy.yaml`): the wording of the organizational controls and the region and policy texts, and
   when a new version asks every organization to accept again (Legal, Privacy and Product).
4. **Trust freshness.** How long a verified company, identity, authority and screening check stays fresh before it shows "Needs action" (the sources set expiry
   dates today; the owners set the windows), and what an activated organization may do while one is stale.
5. **Who may ask for changes.** Today any eligible approver of an open request may send it back; decide whether a reason template, a limit or a second person applies.
6. **Evidence attachments (AT-12), now built (CR-051) with engineering proposals in `backend/config/approvals/attachment-policy.yaml`.** Rule on: which kinds of document may be attached (four are proposed), the file types and the 10 MiB limit, how many documents one request may hold (10), the vault retention period (unset until Privacy and Legal decide one; access itself ends when the request does), who may open restricted evidence (today an approving role or someone who voted, each opening audited), and the malware scanner and its contract (the scanner hook speaks the clamd protocol; a deployed environment with no scanner refuses uploads).
7. **Reminders and expiry: built in the app as a proposal (CR-053).** One in-app reminder 24 hours before a request lapses, once per request, and a notice when one
   expires (`backend/config/approvals/reminder-policy.yaml`; the default lifetime is 3 days and the longest 14, also proposals). Decide the windows, who is reminded
   (today the alerts centre, which is organization-wide), and whether an email is wanted; the Communication Estate has no approval notice, so that needs a template.
8. **Approver invitation touchpoint** (13C-01C): how the setup page should let an organization invite approvers or appoint independent reviewers beyond the link to the
   governance page (the role editor itself is Document 13D).
9. **Provider outage wording and support path** ("Explicit safe hold with retry or support path").
10. **Named conflicts:** who reviews a conflicting organization claim ("Authority review required") and what they may see (Trust Operations).
11. **Browser and device evidence:** a screen-reader pass and a mobile device run for the three screens (QA).
12. **Threat model** for self-approval, account linking, compromised reviewers and replay (Security).
