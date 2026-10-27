# CR-039: Who may change a mandate, and a Member's own view of their limits

- **Target:** ZID-MANDATE-001 §9-10, §14 (mandate lifecycle and ledger); ZID-UX-001 (role-appropriate console) · **Raised by:** Console wiring (role review of the mandates pages) · **Status:** Open (built)

## Gap

Activating, pausing and revoking a mandate were open to anyone signed in to the company who knew the mandate's id: the calls
ran no permission check, and the page hid nothing by role. A Member who created a mandate for their own agent also had no
page to see it, because listing mandates is an administrator permission (`mandate.read`).

## Change

| Action | Risk | Note |
|---|---|---|
| `mandate.manage` | R2 / E2 | Activate, pause or revoke a mandate that is not the caller's own. Part of the tenant-administration grant (owner and administrators). Handed down to an Administrator like the other console actions. |

Rules for `POST /v1/mandates/{id}:activate|:pause|:revoke`:

- The **executor** may pause the mandate; the **creator** may pause or revoke it. They need no company-wide permission: it is their own authority.
- **Activating** a mandate, and stopping anyone else's, needs `mandate.manage`. An executor never activates their own mandate.
- Any other caller is refused (403) by the decision point, like every protected action.

New read: `GET /v1/mandates/mine` returns the mandates the caller executes or created, each with `i_am` (`executor`, `creator`) and
`my_moves` (the moves open to them right now). It needs no permission; row-level security already limits it to the company.

No table, no outbox event: lifecycle events are unchanged.

## Existing companies

Companies seeded before this change need the action added to their administrators' grants:
`python -m zid_platform.entrypoints.refresh_admin_grants --apply` (dry run by default).

## Tests

Unit: `own_moves` for executor, creator, both, a bystander and no principal. Component (`test_rest_api_pg`): a bystander cannot pause,
the executor cannot revoke or activate, an administrator holding `mandate.manage` can reach the grant check, the creator can revoke,
and `mine` lists each person's own mandates only.
