# CR-061 — Names and permanent member numbers for people

**Status:** Built. Additive and non-breaking (`contract_compat`: 0 breaking changes).

## Problem

Approvers, requesters, members and reviewers were shown only as UUIDs. Nobody could tell who approved what without looking ids up.

## Change

- **Member number.** `platform.tenant_member_no` gives each person a permanent number inside their company, shown as `M-0007`. It is assigned by a trigger on first role assignment (serialised per company by an advisory lock, so concurrent invites get distinct numbers with no gaps), is never reused, and cannot be updated or deleted (`member_no_guard`). Migration `tenant_0018` backfills existing people in order of first appearance. Row-level security applies as for other tenant tables.
- **Name.** An optional display name, stored in `core.principal.display_label` (cleared by erasure). Set at invite (`display_name`), by an owner or admin (`POST /v1/tenant/members/{id}:rename`) or by the person (`POST /v1/tenant/members/me:name`). Whitespace is collapsed; blank clears; control characters or more than 120 characters are refused.
- **Display.** Responses carry a `person` object (`principal_id`, `member_no`, `member_code`, `name`, `label`). `label` is `Name (M-0007)`, or `M-0007`, or a short id if the person has no number. It appears on the member list, invitations, reviewers, and approval requester, beneficiary and each voter.
- **Permissions.** `GET /v1/tenant/me/permissions` now reports the real `enforcement` value instead of a constant `false`.

## Not changed

The audit ledger stays pseudonymous. Names appear only on administration screens; audit re-identification remains case-bound. The role-enforcement switches (`route_matrix_enforcement`, `direct_admin_invitation`) are owner decisions and are untouched.

## Verification

Unit tests (`test_people.py`) and Postgres component tests (`test_member_numbers_pg.py`: numbering, names, per-company numbers, 8 concurrent invites, permanence, approval enrichment). Web typecheck and 265 unit tests pass.
