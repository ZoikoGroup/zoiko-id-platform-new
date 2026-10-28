# CR-041: Payment review queue, paying on Stripe's page, and crediting refunds

- **Target:** ZID-COM-001 §11 (payment collection), §31 (credit notes) · **Raised by:** CR-040 follow-up · **Status:** Open (built)

## Gap

CR-040 stored the payment events a rule cannot settle (refunds, disputes, unmatched payments) but showed them to nobody, a refund could not become a credit note, and a charge that the bank wanted confirmed (3-D Secure) simply failed with no way to finish it.

## Change

1. **Review queue.** `commercial.stripe_event` gains the invoice, amount, currency and who resolved the item and how (migration `commercial_0014`). The invoice is stored only after it is shown to belong to the customer's own account.
   - `GET /v1/commercial/payment-events[?resolved=true]` lists open `NEEDS_REVIEW` and `UNMATCHED` items, oldest first (`commercial.catalog.manage`).
   - `POST /v1/commercial/payment-events/{event_id}:resolve` closes an item with a note (`commercial.catalog.manage`). An item closes once.
2. **Refund to credit note.** `POST /v1/commercial/payment-events/{event_id}:credit-note` issues a credit note (reason `CUSTOMER_REFUND`) on the refunded invoice and closes the item in one transaction (`commercial.billing.manage` for that company). Only an open refund that names the company's own invoice qualifies. The operator enters the amount: Stripe reports the *total* refunded on a charge, so a second refund event repeats the first and the platform does not assume what is still to credit. The credit-note rule still caps it at what is left of the invoice. Disputes and unmatched payments are only closed with a note.
3. **Paying on Stripe's page.**
   - `POST /v1/invoices/{id}/pay-online` returns the address of a Stripe Checkout payment page for what is outstanding on one invoice (owner, `commercial.payment.manage`). The bank's confirmation step happens there.
   - `POST /v1/billing/payment/online-complete` verifies the returned session at Stripe (this company's customer, paid, an invoice named) and records the payment through the same path as the webhook.
   - A payment is recorded once whichever arrives first, the return or the webhook: the check is made under the invoice lock.
4. **Billing page.** Each open invoice has Pay on Stripe. After a bank-confirmation failure it becomes the main action and Pay now is hidden.
5. **Operator console.** A Payment events tab lists the queue and offers Credit a refund and Close a payment event.

## Decisions taken, to be confirmed by Finance

- A refund is never credited automatically; a person confirms the amount.
- The credit-note reason code is the fixed `CUSTOMER_REFUND`.
- Disputes are answered in Stripe's dashboard; the platform only records that a person handled them.

## Receipts and invoice emails

Already in place from CR-031: invoice issued (ZID-EM-130), payment received (ZID-EM-131) and payment failed (ZID-EM-132) are sent to the company's billing contacts from the `commercial.payment.recorded` event. Payments recorded by the webhook or on return use the same event, so they send the same emails. They need email switched on and a billing contact set. The copybook has no credit-note or refund email, so none was added.

Tests: `test_stripe_online_payment.py`, `test_online_payment_pg.py`, `test_payment_review_pg.py`, and the extended `test_stripe_webhook_pg.py`.
