# CR-035: One operator account can run the platform (direct mode)

- **Target:** DEC-F4 (operator access: just-in-time, dual control), ZID-SEC-001 §11-13, ZID-COM-001 §6, §12 · **Raised by:** Console wiring (operator console, commercial) · **Status:** Open (built)

## Gap

The operator plane assumes a staffed operations team: every capability is a just-in-time grant requested by one operator and approved by others (two approvers for R3), and the commercial steps that commit money (price books, overrides, adjustments, quotes, orders, invoices) each need a second person. A small team that runs the platform with one account could do none of this, and the plan and price screens were unusable for it.

## Change

Setting `ZID_OPERATOR_MODE`: `governed` (default, unchanged) or `direct`.

In direct mode a registered, ACTIVE operator in a NORMAL security state holds every capability that does **not** touch customer data, with no access request and no second approver. A dual-control step is recorded as approved by the fixed `DIRECT_APPROVER` principal (`00000000-0000-4000-8000-0000000000d1`), because the database refuses an author approving their own price book, override, adjustment or invoice; the real operator remains the author and the actor in the commercial change log.

| Still enforced in direct mode | |
|---|---|
| Sign-in and fresh step-up | passkey, AAL-Z3 on operator actions |
| Operator profile and security state | an unregistered or restricted operator gets nothing |
| Customer lockbox | capabilities that read customer data are never opened by this mode |
| Evidence and the change log | every action, under the operator's own name |
| Hard pricing blocks | a quote below its floor still cannot be approved |

| Deployed cells | |
|---|---|
| local, test, dev | `ZID_OPERATOR_MODE=direct` is enough |
| any other environment | also needs `ZID_OPERATOR_DIRECT_ACK=accept-no-dual-control`, or the cell refuses to start |

The cell logs a warning at start-up, `GET /operator/capabilities` reports the `mode`, and the console shows a "Direct mode" banner and mode-aware descriptions.

## Rules

- Direct mode is a decision to give up the second pair of eyes for non-customer-data work: choose it per environment, and revisit it when a second operator exists.
- Customer-data access is never relaxed here: that stays behind the customer's lockbox.
- Changing `DIRECT_APPROVER` or removing the acknowledgement requires a new change request.

Tests: governed mode still needs a grant; direct mode opens every non-customer-data capability and none that touch customer data; an unregistered operator is refused even in direct mode; a deployed cell refuses direct mode without the acknowledgement; the database accepts the direct approver's approval and still refuses the author's.
