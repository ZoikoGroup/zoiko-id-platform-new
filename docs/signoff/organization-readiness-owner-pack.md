# Organization onboarding and the approvals center: what only the owners can close (ZID-UX-WF-013C; slice L-13, CR-049)

1. **Founder approval** of the 13C wireframe and its sixteen acceptance tests.
2. **What each readiness state unlocks.** Readiness is derived and shown, but nothing consults it yet. Decide what ACTIVE, ACTIVE_LIMITED and RESTRICTED allow or
   refuse (for example, which privileged actions need ACTIVE), so engineering can wire it.
3. **Setup texts and versions** (`backend/config/onboarding/setup-policy.yaml`): the wording of the organizational controls and the region and policy texts, and
   when a new version asks every organization to accept again (Legal, Privacy and Product).
4. **Trust freshness.** How long a verified company, identity, authority and screening check stays fresh before it shows "Needs action" (the sources set expiry
   dates today; the owners set the windows), and what an activated organization may do while one is stale.
5. **Who may ask for changes.** Today any eligible approver of an open request may send it back; decide whether a reason template, a limit or a second person applies.
6. **Evidence attachments (AT-12).** Which documents may be attached, the malware scanner and its contract, expiry of access links, who may open restricted evidence,
   and region-bound storage. None of it exists yet.
7. **Reminders and expiry.** How long a request may wait (3 days default, 14 days maximum are engineering proposals), who is reminded, how often, and to whom.
8. **Approver invitation touchpoint** (13C-01C): how the setup page should let an organization invite approvers or appoint independent reviewers beyond the link to the
   governance page (the role editor itself is Document 13D).
9. **Provider outage wording and support path** ("Explicit safe hold with retry or support path").
10. **Named conflicts:** who reviews a conflicting organization claim ("Authority review required") and what they may see (Trust Operations).
11. **Browser and device evidence:** a screen-reader pass and a mobile device run for the three screens (QA).
12. **Threat model** for self-approval, account linking, compromised reviewers and replay (Security).
