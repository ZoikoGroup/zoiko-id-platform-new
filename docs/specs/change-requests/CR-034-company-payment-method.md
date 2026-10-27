# CR-034: A company saves the card its invoices are charged to

- **Target:** ZID-COM-001 §11 (payment), §19 (customer console: Billing & invoices) · **Raised by:** Console wiring (billing page) · **Status:** Open (built)

## Gap

Invoices are collected off-session from a customer at the payment processor (CR-027), but a company had no way to put a card on that customer: the customer id was set by the commercial team and no screen showed what was due or which card would be charged.

## Change

| Action | Risk | Note |
|---|---|---|
| `commercial.payment.manage` | R2 / E2 | Save or replace the company's card on the processor's hosted page. Held by the **owner only**: it is in the tenant-administration set but listed in `OWNER_ONLY_ACTIONS`, so it is not handed down to administrators. |

API (customer): `GET /v1/billing/payment` (`commercial.read`: what is due now per currency with its open invoices, the saved card as brand, last four and expiry, the latest ten payment attempts, and `can_manage`), `POST /v1/invoices/{id}/pay` (`commercial.payment.manage`: pay what is outstanding on one issued invoice with the saved card now; idempotent per click, same collection as the operator's), `POST /v1/billing/payment/setup` and `POST /v1/billing/payment/complete` (`commercial.payment.manage`).
Setting: `ZID_BILLING_RETURN_ORIGIN` (default: the origin of `ZID_CONSENT_URL`), where the processor's page returns the buyer.
Operations: `python -m zid_platform.entrypoints.refresh_admin_grants --workspace <name> [--apply]` gives a company registered before this change the current set (a grant never changes after it is issued).

## Rules

- The card number is entered on Stripe's hosted page (Checkout in setup mode) and never reaches the platform or the browser app. The platform stores only the Stripe customer id (`commercial.payment_customer`) and shows brand, last four and expiry.
- A company needs a commercial account first (the commercial team creates and links it with the order); without one, setup answers `NO_BILLING_ACCOUNT`.
- On return, the cell verifies at Stripe that the session belongs to this company's customer and is complete before its card becomes the customer's default, the card `StripePayments` charges. The charge names that card explicitly (Stripe does not use `invoice_settings.default_payment_method` implicitly).
- The return address comes from configuration, never from the request.
- Saving a card is appended to the commercial change log as `PAYMENT_METHOD_SAVED` (brand and last four).
- The page shows the add or replace control only when `can_manage` is true; the cell refuses the call regardless.

Tests: a database test where the owner sees the amount due, pays it once (a repeated click never charges twice), sees the history, and cannot pay a settled or unknown invoice; unit tests with Stripe mocked (customer and setup session carry only references and return addresses, a session of another customer or an unfinished one saves nothing, bad session ids never reach Stripe, outages are coded errors, the owner-only set); the charge test names the saved card.
