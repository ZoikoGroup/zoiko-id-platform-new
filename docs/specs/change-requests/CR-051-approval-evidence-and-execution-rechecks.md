# CR-051: Documents on an approval request and the execution-time rechecks (ZID-UX-WF-013C AT-12; ZID-ENG-DEC-011 and 012), slices L-10 and L-13

Status: open (built). The records are proposed and await founder approval.

## What changes
- **Documents on an approval request** (`onboarding/api/approval_attachments.py`, `application/approval_attachments.py`, onboarding migration `0006`, table
  `platform.approval_attachment`, `config/approvals/attachment-policy.yaml`):
  `POST`, `GET` (list) and `GET` (open one) under `/v1/organizations/{org}/approvals/{request}/attachments`.
  - Scanned, with the same order as the company-claim proofs: type from the file's own bytes, size, kind, no active content and a limit; then the
    organization's residency BEFORE the scanner or the vault sees the file; then the scan (a deployed environment with no scanner refuses, nothing is stored
    unscanned); then the encrypted evidence vault; then the record and an audit record. A refused file leaves an audit record (hash and reason) and no file.
  - Scoped by the approval service's own object-level answer (`case_access`, a tenant port): the requester attaches while the request is open and no vote has
    been cast; an approving role or someone who voted opens; everyone else is told the request does not exist; another organization's id is "no such
    organization". The approver's opening is an E3 audit record.
  - Expiring: access ends when the request does (`expires_at` is the request's own expiry); a read after it is `410 ATTACHMENT_EXPIRED`. Vault retention is
    `retention_days` in the policy, left unset until Privacy and Legal decide a period.
  - Outside URLs and logs: responses are `no-store`, the document is served with `nosniff` and a sandboxing CSP, no link works for anyone but a signed-in
    approver, and no audit record, event or log carries content or the file name, only the id, kind, type, size, hash and scan result.
  - The rows are append-only (a trigger) and tenant-isolated (row-level security).
- **Execution rechecks the country and data controls** (Decision 12 test 07; Decision 11 order "revalidate at execution"). A customer case now runs only if the
  organization's country still allows enterprise use in the country registry; the check is composed at start-up (`configure_standing_check`) and answers with
  a reason code, recorded as a failed attempt that changes nothing and can be retried when the country allows it again. Platform cases are not affected. A
  deployed environment refuses an organization whose country is unknown (`COUNTRY_NOT_DECLARED`). Policy, approvals, roles and region were already rechecked.
- **More machine-checked gates (Decision 11):** removing an Owner now checks that an Owner remains and that a remaining Owner holds a passkey (`OWNER_REMAINS`,
  `REMAINING_OWNER_STRONG_AUTHENTICATOR`), at request and again at execution; privileged recovery checks that the target is still an active person with a
  privileged role (`RECOVERY_TARGET_ACTIVE_PERSON`, `RECOVERY_TARGET_PRIVILEGED`).
- **Recovery ends the target's sessions** (Decision 11 section 07, "revoke compromised sessions"): running a privileged recovery bumps the account's security
  revision, so every session it holds must sign in again before the new passkey exists.
- **Web:** the decision page's evidence card lists the documents, lets the requester add them before the first vote and lets an approver open them.

## Not changed
No new action in the action catalogue and no new outbox event (the audit record is the evidence). Legal-form rechecks are not built: no record says which legal
forms a country allows, so that is an owner ruling.

## Tests
`test_approval_attachments_pg.py` (5), four new tests in `test_approvals_pg.py`, 4 web logic tests; WF13C-AT-12 is linked as in progress.
