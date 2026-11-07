# CR-062 — Pay the first month before a plan switches on

**Status:** Built. Additive to the API (`contract_compat`: no breaking change). The operator's activation now behaves differently by default; see "Switch".

## Problem

When an operator activated a signed order, the company's plan changed immediately. The platform fee was billed later, on the month-end invoice, so a company could be switched on without having paid anything.

## Change

- **Awaiting payment.** `commercial.sales_order` gains the state `AWAITING_PAYMENT` between `SIGNED` and `ACTIVATED`. The order itself is never rewritten (the order guard is unchanged); only its state moves.
- **One figure.** The amount is the quote's monthly value, the same figure the commitment approval used (`commitment_service.monthly_value_minor`), so the company approves and pays the same number. Pilots and orders with nothing to pay switch at once.
- **`commercial.activation_payment`** (migration `commercial_0025`): one row per waiting order with amount, currency, the operator who activated, the Stripe Checkout session last opened, the payment reference, and the invoice that took it into account. Tenant-isolated.
- **Company side** (`/v1/billing/activations`): list what waits; open Stripe's hosted page for the amount; confirm. Confirm reads the session back from Stripe and requires this company's customer, a plan-activation payment, paid, and the exact amount and currency. Only then does the next activation switch the plan. A payment made with the browser closed is found again through the stored session.
- **Operator side:** `activate` returns `awaiting_payment` with the amount; `POST /v1/commercial/orders/{id}:cancel-payment-wait` takes an unpaid order back to signed (a paid one cannot be taken back here, since that would be a refund).
- **No double billing.** `invoicing_service.create_draft` takes a paid activation off the first invoice it fits on as a `CREDIT` line (the line kind already existed), and marks it credited. One that does not fit waits for the next invoice.
- **Web:** a banner on every billing tab ("Pay to switch on your plan") with a pay button, a "check now" button, an in-progress state and a clear not-confirmed state. The plan is shown as on only when the cell says so.

## Switch

`ZID_ACTIVATION_PAYMENT` = `REQUIRED` (default) or `OFF` (the old behaviour). `REQUIRED` with no payment provider configured **refuses the activation** rather than switching a plan nobody can pay for; set `OFF` explicitly to activate without payment.

## Follow-up (migration `commercial_0026`)

- **Webhook completes it.** `payment_intent.succeeded` naming an order (metadata `order_id`, `purpose=plan_activation`) records the payment and switches the plan even if the buyer never comes back. The customer on the event must own the order's company; a short payment is flagged `NEEDS_REVIEW`, not applied; a repeat is `IGNORED`. The buyer's return and the webhook can arrive in either order.
- **Part months.** The rating run prorates the platform fee of a part month while the company paid a whole month, so the credit can be larger than the invoice. `commercial.activation_credit` records what each invoice took; what does not fit carries to the next invoice, and voiding a draft invoice releases what it took (marked released, never deleted: the runtime role has no DELETE).
- **Refunds.** `POST /v1/commercial/orders/{id}:refund-activation` (reason required) marks a paid activation payment that no invoice has used as `REFUND_DUE`; `GET /v1/commercial/activation-refunds` lists them with the processor reference. When the processor reports the refund (`charge.refunded`), the payment becomes `REFUNDED` and is never credited. A payment an invoice already used is corrected with a credit note instead. The refund itself is still made at the processor by Finance; this makes it a tracked work item rather than an invisible manual step.
- **Which Stripe account.** `python -m zid_platform.entrypoints.stripe_account` prints the account id, name and test or live mode for the configured key (never the key); with `ZID_STRIPE_EXPECTED_ACCOUNT=acct_...` it exits 1 on a different account, so a deployment check can refuse another product's keys.

## Not done

- The refund is not issued from Zoiko iD: Finance makes it in Stripe (or through the existing refund case) and the webhook closes the item.
- A reversal after the plan has switched on (a downgrade or cancellation of the plan itself) is a commercial decision, not part of this change.

## Verification

`test_activation_payment_pg.py` (wait, mismatched amount refused, idempotent record, switch only after payment, cancel, company isolation, month-end credit applied once). Related suites (orders, invoicing, commitments, payments, webhook) and the full unit suite pass.
