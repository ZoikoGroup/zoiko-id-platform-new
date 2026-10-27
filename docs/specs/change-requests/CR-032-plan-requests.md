# CR-032: A company asks for a different plan

- **Target:** ZID-COM-001 §10 ("Expand"), §12, §19 (customer console: Billing & invoices, Subscription & entitlements) · **Raised by:** Console wiring (billing and subscription pages) · **Status:** Open (built)

## Gap

A company that registers is on the default plan (CR-024). The console showed the plans on offer, but a company had no way to say which one it wanted: a plan comes from a signed order raised by the commercial team (CR-028), so the pages read as "not connected". The spec's lifecycle has the customer ask (§10 Expand) and the team quote.

## Change

| Table | Scope | Note |
|---|---|---|
| `commercial.plan_request` | tenant-scoped (RLS); a second `operator_scope` policy lets the operator plane read and answer across companies | One row per ask: plan, current plan, note, who asked, status (`OPEN`, `ACCEPTED`, `DECLINED`, `WITHDRAWN`), the answer and who gave it. At most one `OPEN` per company. |

| Action | Risk | Note |
|---|---|---|
| `commercial.plan.request` | R2 / E2 | Ask for, or withdraw a request for, a different plan. Part of the tenant-administration grant (owner and administrators). Changes no entitlement. |

API (customer): `GET /v1/billing/plan-requests` (`commercial.read`), `POST /v1/billing/plan-requests`, `POST /v1/billing/plan-requests/{id}:withdraw` (`commercial.plan.request`).
API (operator): `GET /v1/commercial/plan-requests`, `POST /v1/commercial/plan-requests/{id}:answer` (accept or decline, with a note).

## Rules

- A request never moves the entitlement. The move is a signed order (CR-028) or the operator's plan change; **accepting a request says the team will raise it**.
- The plan must be published and different from the company's current plan; one open request at a time.
- Every ask and every answer is appended to the commercial change log (CR-030) as `PLAN_REQUEST_OPENED`, `PLAN_REQUEST_WITHDRAWN`, `PLAN_REQUEST_ACCEPTED` or `PLAN_REQUEST_DECLINED`.
- No outbox event is emitted: nothing downstream reacts to an ask.

Tests: ask, duplicate refused, same plan refused, withdraw and ask again, another company sees nothing, the operator lists across companies and answers once, a closed request cannot be answered again.

## Follow-up (console wiring)

- The request records the company's display name when it is made (`commercial.plan_request.company_name`, migration `commercial_0012`, filled in for earlier requests), because the operator plane cannot read another company's tenant row; the operator list shows the name with the id underneath.
- Answering a request raises an INFO alert in the company's own Alerts, `commercial.plan_request.answered` ("Your request for the Launch plan was accepted" or "declined", with the operator's note), in the company's tenant context. A plan change itself still sends the existing billing email (`commercial.entitlement.changed`, templates ZID-EM-127 and ZID-EM-128) when email is on.
- In direct operator mode (CR-035) the operator can accept and move the company to the plan in one step.
