# CR-148: Buy now or ask for a price for your company, and no paid plan without payment

Status: built 24 Nov 2026 (uncommitted). Builds on CR-147 (self-serve purchase) and CR-062 (pay before the plan switches).

**Problem.** A company asked for the Scale plan; an operator accepted the request and then used "Move a tenant to a plan". Scale became active with no order, no invoice and no payment. Two things allowed it:
the operator action `PUT /v1/commercial/tenants/{id}/entitlement` switched any plan on directly, and in single-operator mode the plan request screen had an "Accept and move to {plan}" button that did the same
in one click. The revenue-assurance check that finds "an active plan with no order behind it" only runs when someone starts a scan, so nothing flagged it.

**Change.** Every plan card now offers two ways forward, and neither switches a plan on without payment.
1. **Buy**: the company pays the list price now on Stripe (CR-147).
2. **Ask for a different price**: the company says what it would like to pay (optional) and why. The team answers with an **offer**: a monthly amount for that one company and that one plan, never above the list price,
   with a last day (1 to 60 days, default 14). The company sees "Price agreed for your company" on the card and in a banner, and buys at that amount: same order, same Stripe page, same rule that the plan switches only
   after Stripe confirms that exact payment. Paying the list price, or any other amount, does not satisfy an order raised at the agreed price. The offer is spent when the plan is switched on.

**Price authority.** A price below the list price is a discount, so it is held to the existing discount bands (`commercial.discount_policy`):
- direct mode: the one operator is the authority and the offer is open at once;
- governed mode: the offer waits (`PENDING_APPROVAL`) for a different operator who holds every role the reduction needs. A reduction whose band needs more than one role is refused rather than approved with less
  authority than the band asks for;
- in a deployed environment a reduction also needs the finance policy to be signed.
The agreed amount is carried on the quote as the exact discount from the list price, and the approval given when the offer was made is recorded on that quote. The order's value is the stored net price, so it equals the
amount the company reviewed to the cent.

**Closing the free route.** `PUT /v1/commercial/tenants/{id}/entitlement` now refuses a plan that has a price with `PAYMENT_REQUIRED` and points to Buy and to Make an offer. A free grant of a paid plan is possible only with
`complimentary_reason` (at least 10 characters); it is recorded in the change log as `COMPLIMENTARY_PLAN_CHANGE` with that reason. The "Accept and move" button is gone from the operator console.

API:
- company: `GET /v1/billing/plan-offers` (each plan's price for this company, with the agreed one when there is one); `GET /v1/billing/plan-offers/{plan}` now also returns `list_amount`, `negotiated`, `offer_expires_at`;
  `POST /v1/billing/plan-requests` takes an optional `desired_amount`; a request's view carries its `offer`.
- operator (`commercial.catalog.manage`): `POST /v1/commercial/plan-requests/{id}:offer` `{amount, valid_days, note}`, `:approve-offer`, `:withdraw-offer`, and `GET /v1/commercial/plan-offers`.
Data: migration `commercial_0044` adds the offer columns to `commercial.plan_request` (all-or-nothing, never above list, an open offer is approved). The company already sees only its own rows (row-level security).

Screens: company Billing, plan cards show Buy and "Ask for a different price", the agreed price struck against the list price, and a banner with "Buy at your price"; the ask dialog has an optional price field. Operator
Commercial, Plans and accounts: Plan requests now has **Make an offer** (list price, what they asked for, a typed price, how long it stays open, a preview of what the company will read) and Decline; below it, "Prices made for one company".

**Tests.** `test_plan_offers_pg.py` (buying at the agreed price and paying exactly that, other amounts refused, the offer is for one company, never above list, a contract plan has none, an ended or withdrawn offer is ignored,
governed approval by a different person with the role, several-role reductions refused, a request must carry a real amount); `test_commercial_pg.py` (a paid plan is refused without payment or a reason; a short reason is refused;
a reason is recorded); operator logic tests for the wording and validation.

**Not built.** Prorated upgrades and period-end downgrades (CR-147 B4), the renewal scheduler and failed-payment ladder (B5), reconciliation and alerts (B6). A render test of the plan cards. Revenue assurance still runs only when a
scan is started; a free grant is flagged by that scan, not at the moment it is made. The earlier test company on Scale with no order is left as it is until the owner decides what to do with it.
