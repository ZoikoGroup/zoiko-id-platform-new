# CR-001: Canonical continuity modes

- **Target:** ZID-DOM-001 §6.3; ZID-DATA-001 `authz.authority_grant`
- **Raised by:** DEC-C2 · **Status:** Open

## Problem
ZID-DOM-001 §6.3 names PERSONAL / ROLE-ISSUED / EXTERNAL-CONTRACTUAL. ZID-MANDATE-001 §5 and ZID-UX-001 §12.1 name PERSON_BOUND / ROLE_CONTINUOUS / ORGANIZATION_CONTINUOUS. Neither list is complete, and the Data Model defines no column.

## Proposed change
Add to ZID-DOM-001 §6.3 and to `authz.authority_grant`:

| `continuity_mode` | Replaces | Basis |
|---|---|---|
| `PERSON_BOUND` | PERSONAL | ZID-DOM-001 §6.3, ZID-MANDATE-001 §5 |
| `ROLE_CONTINUOUS` | ROLE-ISSUED | same |
| `ORGANIZATION_CONTINUOUS` | (new to DOM) | ZID-MANDATE-001 §5 |
| `CONTRACT_BOUND` | EXTERNAL/CONTRACTUAL | ZID-DOM-001 §6.3 |

Column: `continuity_mode` constrained enum, NOT NULL, default `PERSON_BOUND`, immutable per grant ID.
