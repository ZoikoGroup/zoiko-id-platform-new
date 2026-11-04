# CR-058: Refunds, disputes, reconciliation and period close (ZID-UX-COM-009 F03 and F04; ZID-COM-CL-004)

Status: open (built; processor and bank connections need owner decisions, see the owner pack). This builds the part of Wireframe 09 that CR-054 and CR-057 left open: money going back to a customer,
card disputes, and matching what the platform says happened with what the processor, the bank and the ledger say.

## What changes
- **Refunds** (commercial migration `0021`, `application/refund_service.py`, `domain/payment_ops.py`). A refund is asked for against one captured payment, never for more than the customer
  overpaid (paid, less what the invoice still asks after its credit notes, less refunds already made or in flight), so a refund and a credit note for the same money cannot both be granted. It is
  placed in a tier by its exposure in USD (the organization's refunds within the window add up) and each role the tier names signs the digest they read, each a different person and never the
  requester. A separate Finance Executor queues it; **a route never calls the processor**. The worker sends each queued refund once, under the case's own idempotency key. A processor that does not
  answer leaves the case UNKNOWN: it is resent only after the processor has been asked what it holds for that case and holds nothing. "Succeeded" needs the processor's recognised, completed answer with the
  amount that was asked for; anything else is pending, partial, failed or UNKNOWN. The screen words are fixed per state, with the words each state must never use ("provider confirmed; reconciliation still
  to come", never "refunded" or "bank settled").
- **Disputes** (`application/dispute_service.py`). A verified processor event opens or moves a case (idempotent by dispute, merchant and live mode). The case has an owner, the processor's own deadline
  with its time zone (a missing deadline is said to be missing, never invented), an evidence checklist, and a decision (accept or contest) by a Controller or the CFO. A Finance Executor records that the
  evidence was handed to the processor's dashboard; that is "submission recorded", and is verified only by asking the processor. **A dispute closes only on the processor's event.** An open dispute blocks a
  refund of that payment, so the money cannot go back twice.
- **Reconciliation and period close** (`application/recon_service.py`). A Controller imports the processor's payout lines, the bank lines and the ledger journals. Each money event of a period shows where
  each leg stands (matched, provisionally matched, unmatched), the exceptions that raises (they stay on the queue until a person explains them), what blocks a close, and the digest a close would sign. Provider
  receipt, payout, bank settlement and the ledger are separate facts; none of the totals is revenue. A period closes only with nothing blocking it, and two different people (the Controller, then the CFO or
  Group CFO) sign the same digest; if anything moved the period goes back to open. A closed period is locked in the database (a leg or an exception dated in it is refused). Reopening needs a reason, an impact
  and two approvals from the reopening roles, neither the person who asked. A closed period has an evidence pack with its own checksum.
- **The Stripe webhook** opens or moves dispute cases and links a `charge.refunded` event to the platform's own refund case when it is one; a refund that nobody here asked for stays in the review queue.
- **The signed finance policy** has three new sections (`refund` tiers, `dispute` roles, `reconciliation` roles and materiality). Its content hash changed: **deployed environments act on none of it until the
  Founder and the CFO sign the new policy** (sandbox environments skip the lock, as before). All numbers are proposed.
- **Operator API** (`operator_access/api/commercial_payment_ops.py`): list, open, decide, execute, cancel for refunds; list, view, assign, evidence, decide, record and verify submission for disputes; the
  reconciliation board, leg import, exceptions, prepare close, sign close, request and approve reopen, evidence pack. All behind `commercial.finance.manage`.
- **Worker** step `refunds` sends queued refunds and asks the processor about the ones still open. A deployed cell with no processor key has no port: refunds stay queued and are not sent to nothing.

## Tests (each names the acceptance rows it supports)
Unit `test_payment_ops.py` (the overpayment rule, every refusal, the provider outcome table, resend only after "what do you hold", dispute close only from the processor, deadlines, matching, materiality,
close signature rules) and component `test_payment_ops_pg.py` (limits, tiers, independent executor, one send under one key, UNKNOWN on no answer, dispute events and the refund block, the two-signature close,
the database lock, reopen with two approvals, the webhook).

## Reading the sources
`domain/recon_import.py` reads a Stripe payout's balance transactions as PAYOUT_LINE legs (gross amount, keyed to the PaymentIntent or refund id) and an ISO 20022 camt.053 statement as BANK_LINE legs (signed, keyed by the payout id in the remittance text; an entry that names none stays unmatched). Operator routes `:import-stripe-payouts` and `:import-bank-statement`; the board has the two import boxes. The XML is read only through `defusedxml`. Tested on documented shapes: the Stripe test account has no bank account, so no live payout was available.

## Run against Stripe's test API
`tools/scripts/stripe_test_mode_proof.py` (opt-in, test keys only) sent a refund twice under one idempotency key and got one refund, found it by the case id in its metadata, read its state and amount, saw an over-refund refused, and read a test dispute with its
reason and deadline (2026-11-04). Live mode and real bank files have not been run.

## Acceptance rows linked
WF09-OP-05 to 10 and CL04-C15, as in progress (none verified).

## Still open, and why
Owner decisions and connections only: see [payment-operations-owner-pack.md](../../signoff/payment-operations-owner-pack.md). The operator screens for refunds, disputes and the reconciliation board are the next slice.
