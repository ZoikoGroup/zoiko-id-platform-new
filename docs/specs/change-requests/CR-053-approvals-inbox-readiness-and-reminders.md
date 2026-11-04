# CR-053: Starting a request from the approvals inbox, what readiness refuses, and reminders about requests about to expire (ZID-UX-WF-013C), slice L-13

Status: open (built). The wireframe is proposed and awaits founder approval; its API names are illustrative, so this CR records the names used.

## What changes
- **"New request" in the inbox.** `GET /v1/organizations/{id}/approvals/forms` returns, for each governed action a customer can ask for, the form behind it
  (`backend/config/approvals/request-forms.yaml`): its fields (a member, an Owner, a short text, a few lines, or a choice), how the request names what it
  is about, and how many independent people will have to approve. The existing `POST /v1/organizations/{id}/approvals` is what the form sends, so the
  server still classifies the action, checks who may ask, validates the payload against the action's executor and chooses the approvers; the form only collects.
  The file is checked against the approval registry when the process starts: a form for something a customer cannot request, or a requestable action with
  neither a form nor a stated reason it is started elsewhere (the reviewer appointment, which the governance page opens), stops the process.
- **What readiness refuses** (`backend/config/onboarding/readiness-policy.yaml`, an engineering proposal for the owners). Until now the readiness states were
  shown and enforced nowhere. Now a governed customer action is checked against the organization's state, when it is requested and again when an approved
  change runs:
  - RESTRICTED (a check was declined or a review suspended it) refuses everything except the ways back: recovering a privileged account, asking for a trust
    decision to be looked at again, appointing an independent reviewer, removing an Owner (taking authority away is never refused) and reinstating a
    suspended organization. ACTIVE_LIMITED (a fact activation rested on has lapsed) refuses new privilege only: adding an Owner, granting a privileged role and
    activating a powerful agent. Every other state refuses nothing. Emergency containment is not a governed action here, so no state can refuse it.
  - Three modes: OFF, REPORT_ONLY (the default: the action goes through and an audit record `governance.approval.readiness_report` says what it would have
    refused) and ENFORCED (a request is refused with `ORGANIZATION_NOT_READY`; a run of an approved change is recorded as a failed attempt that changes
    nothing, and the same approval runs once the organization is ready). A typo in the file stops the process.
  - The state is the organization's, read for the person who made the claim, from the facts the setup screen shows (`claims.organization_state`); the approval
    service gets it by composition and never imports the onboarding module. An organization that has started no claim is DRAFT and is refused nothing.
- **Reminders and expiry notices, in the app** (`backend/config/approvals/reminder-policy.yaml`, a proposal). A request that is undecided, or approved and
  waiting to be applied, raises one event `governance.approval.reminder_due` when it enters a reminder window (24 hours before it lapses, once per request and
  window, recorded in `platform.approval_reminder`, tenant migration `0013`); the worker step `approval_reminders` does it for every organization. The alerts
  centre shows it as a task, and an expired request (`governance.approval.expired`, already emitted) as a notice. Neither names a person, a payload or a reason.
  No email goes out: the Communication Estate has no approval notice, and adding one is for the owners.
- **Web:** a New request button and dialog in the inbox (choose the change, fill in its form, see how many people must approve, send), and plain words for the
  new refusals.

## Not changed, and why
- A request is created already submitted. The design's DRAFT and SUBMITTED states are not modelled: a draft request that nobody can see or approve has no use.
- The design's `approval.requested`, `approval.decided` and `approval.executed` are the existing `governance.approval.case_created`, `vote_recorded` and
  `executed`, which carry the same facts.

## Tests
`test_approval_inbox_rules.py` (20: forms and policy against the registry, every drift refused), `test_approval_inbox_pg.py` (6: forms and the requests made from
them, restricted and limited organizations at request and at execution, the default only reporting, a held run that goes ahead once ready, the state of an
organization with no claim, reminders once and never for an expired request), and the web logic tests.
