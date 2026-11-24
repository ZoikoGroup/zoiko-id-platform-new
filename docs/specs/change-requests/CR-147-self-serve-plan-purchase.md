# CR-147: A company buys a plan online and it switches only after Stripe confirms the payment

Status: slices B1 to B3 built 24 Nov 2026 (uncommitted). Design: `docs/specs/self-serve-plan-billing-design.md`.

**Problem.** A company could only ask for a plan. A person on our side raised the order, so there was no way to see a price, pay and move to the plan in one visit. Pay-before-switch (CR-062) already held an order until Stripe confirmed the first month, but nothing created that order for the customer.

**Change.** The plan cards on `/admin/billing` now offer **Buy Launch / Business / Scale** for any bigger plan once a price book is approved. The buyer sees the exact amount from the approved price book, tick that they authorise the charge and the monthly renewal, and is sent to Stripe's page. The plan changes only when Stripe confirms that exact payment (the existing `:confirm` read-back or the signed webhook). The browser coming back never switches the plan.

API (company side, in `modules/commercial/api/activations.py`):
- `GET /v1/billing/plan-offers/{plan}`: what buying this plan costs today, and whether the company still needs to give its billing name and country. Reads only.
- `POST /v1/billing/plans/{plan}:purchase`: raises the order for the plan at list price, holds it for payment and returns Stripe's address for the exact amount. It changes no entitlement and charges no card. Needs `billing.payment.initiate` and the owner-held `commercial.payment.manage`.

Service: `application/self_serve_service.py` builds an ordinary quote and signed order from the approved price book and calls `orders_service.activate(require_payment=True)`.

**Rules kept.**
- The price comes only from the approved price book. No approved book means no offer and no Buy button.
- Only Launch, Business and Scale, and only upwards. Developer is automatic, Enterprise is a contract, a downgrade is arranged with the team.
- The buyer sends the amount they reviewed; a price that moved is refused with `PRICE_CHANGED`.
- A second press reuses the order already waiting: one order, one payment.
- A region that has not passed its release gate refuses the quote, so no order is raised.
- Off by default: self-serve purchase is on only when `ZID_SELF_SERVE_REGION` names a released region (`commercial.region_release`). Until then the dialog offers "Ask our team instead".

**Tests.** `backend/app/tests/component/test_self_serve_purchase_pg.py` (the book's own figure, non-self-serve plans refused, a changed price refused, billing details required, order held at the exact amount with no plan change, repeat press reuses the order, a wrong amount refused, the plan switching only after an exact payment, same and smaller plans refused, a bigger one offered, an unreleased region refusing the quote). Guard tests (routes, contracts, table ownership, module cycles) pass.

**Not built (design slices B4 to B7).** Prorated upgrade charge, downgrade and cancel at period end, the renewal scheduler and failed-payment ladder, the reconciliation job and alerts, a component test of the Buy dialog, and the owners' price-book approval and region release. No price book is approved and no region released by this change.
