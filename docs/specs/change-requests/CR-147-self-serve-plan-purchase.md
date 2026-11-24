# CR-147: A company buys a plan online and it switches only after Stripe confirms the payment

Status: slices B1 to B3 built 24 Nov 2026 (uncommitted). Design: `docs/specs/self-serve-plan-billing-design.md`.

**Problem.** A company could only ask for a plan. A person on our side raised the order, so there was no way to see a price, pay and move to the plan in one visit. Pay-before-switch (CR-062) already held an order until Stripe confirmed the first month, but nothing created that order for the customer.

**Change.** The plan cards on `/admin/billing` now offer **Buy Launch / Business / Scale** for any bigger plan once a price book is approved. The buyer sees the exact amount from the approved price book, tick that they authorise the charge and the monthly renewal, and is sent to Stripe's page. The plan changes only when Stripe confirms that exact payment (the existing `:confirm` read-back or the signed webhook). The browser coming back never switches the plan.

API (company side, in `modules/commercial/api/activations.py`):
- `GET /v1/billing/plan-offers/{plan}`: what buying this plan costs today, and whether the company still needs to give its billing name and country. Reads only.
- `POST /v1/billing/plans/{plan}:purchase`: raises the order for the plan at list price, holds it for payment and returns Stripe's address for the exact amount. It changes no entitlement and charges no card. Needs `billing.payment.initiate` and the owner-held `commercial.payment.manage`.

Operator side (plain-language pricing). The Commercial page now opens on **Plans and prices** (`apps/operator/src/features/commercial/plans-prices.tsx`, logic in `plans-prices-logic.ts`), and the old catalogue tab is renamed "Price books (advanced)". The tab has: three plain sentences on how pricing works; a readiness checklist ("Is it ready for customers?"); a table of what customers pay today (monthly fee by plan, and the usage rates above included amounts); and a three-step form: **1. What do you want to change?** (dropdown: the monthly plan fee or one of eight usage rates), **2. Which plan?** (dropdown, only plans that have that price), **3. New price** (typed by hand, with a field-level error). It shows today's price, a one-sentence preview ("Launch: monthly plan fee changes from $299.00 per month to $349.00 per month"), warns when the new price is more than double or less than half of today's, and needs a tick before saving. `POST /v1/commercial/plan-prices` takes `{plan, item?, amount}` (item defaults to the monthly fee; a monthly fee has at most two decimals, a usage rate up to six; the per-1,000 or per-10,000 unit of a rate is kept). A plan with no such line is refused with `PLAN_LINE_MISSING`.

Service: `application/self_serve_service.py` builds an ordinary quote and signed order from the approved price book and calls `orders_service.activate(require_payment=True)`.

**Rules kept.**
- The price comes only from the approved price book. No approved book means no offer and no Buy button.
- Only Launch, Business and Scale, and only upwards. Developer is automatic, Enterprise is a contract, a downgrade is arranged with the team.
- The buyer sends the amount they reviewed; a price that moved is refused with `PRICE_CHANGED`.
- A second press reuses the order already waiting: one order, one payment.
- No region. A plan's platform fee is the same everywhere, so a plan quote is not held to the regional release gate (`Quote.region_gated = False`, region recorded as `global`) and nothing about a region is configured or chosen. Every other quote (an SLA, a deployment, a residency commitment) keeps the gate unchanged.
- Buying is on when an approved price book exists and card payments are set up on the platform (Stripe and the billing return address). Otherwise the dialog says so and offers "Ask our team instead".

**Tests.** `backend/app/tests/component/test_self_serve_purchase_pg.py` (the book's own figure, non-self-serve plans refused, a changed price refused, billing details required, order held at the exact amount with no plan change, repeat press reuses the order, a wrong amount refused, the plan switching only after an exact payment, same and smaller plans refused, a bigger one offered, the quote recorded with region `global`), and `tests/unit/test_order_rules.py` (a plan fee quote skips only the region gate; every other quote keeps it and every other hard rule still rejects).

**Not built (design slices B4 to B7).** Prorated upgrade charge, downgrade and cancel at period end, the renewal scheduler and failed-payment ladder, the reconciliation job and alerts, a component test of the Buy dialog, and the owners' sign-off of the plan prices for production. This change approves no price book; the operator sets and approves prices with "Set a plan's monthly price".
