# Payment operations: owner pack (refunds, disputes, reconciliation and period close)

For: the Founder, the CFO, the Controller and the Group CFO. Built in [CR-058](../specs/change-requests/CR-058-refunds-disputes-reconciliation-and-period-close.md). Every number below is a proposal in
`backend/config/commercial/finance-policy.yaml`. Nothing in a deployed environment moves money until the first item is done.

## 1. Sign the policy again (Founder and CFO)
The finance policy gained `refund`, `dispute` and `reconciliation` sections, so its content hash changed. Until the Founder and the CFO sign the new hash (Finance console, Policy and roles), a deployed
environment refuses to request or approve a refund and to decide a customer commitment. Local and test environments are not locked.

## 2. Rulings we need
| # | Decision | Proposed | Who decides |
|---|---|---|---|
| 1 | Refund tiers by USD exposure (the organization's refunds within 90 days add up) | up to 500 Controller; up to 5,000 CFO; up to 25,000 Controller and CFO; above, CFO and Founder | CFO, Founder |
| 2 | Who owns and who decides a dispute | owner: Disputes Specialist; decide accept or contest: Controller or CFO; record the hand-off to the processor's dashboard: Finance Executor | CFO |
| 3 | Who closes a period and who reopens it | Controller makes, CFO or Group CFO checks, never one person; reopen: two approvals from CFO and Founder, neither the requester | CFO, Founder |
| 4 | The line below which an unmatched difference does not stop a close by itself (a missing leg always does) | 1 USD | Controller, CFO |
| 5 | Refund categories that need linked evidence | duplicate payment, correction, cancellation, contract right (goodwill needs none) | CFO |

## 3. Connections only you can make
- **Processor.** The Stripe account and API version for Refunds and the Payments dispute workflow (not Issuing). Confirm the merchant account the platform may refund from, and that the restricted key allows
  refund and dispute reads. The platform sends a refund under the case's own idempotency key and carries the case id as metadata so the processor can be asked what it holds.
- **Webhook events.** Subscribe the endpoint to `charge.dispute.created`, `charge.dispute.updated`, `charge.dispute.closed`, `charge.dispute.funds_withdrawn`, `charge.dispute.funds_reinstated` and
  `charge.refunded`.
- **Bank and ledger evidence.** The import takes lines of `leg_type` (PAYOUT_LINE, BANK_LINE, GL_JOURNAL), `ref`, `link_ref`, `amount_minor`, `currency` and `occurred_on`. A payout line links to a payment or a
  refund by the processor's reference; a bank line to a payout by the payout's reference; a journal by the payout, the invoice number or the processor reference. The platform now reads two sources directly: the processor's own payout detail (a Controller gives the payout ids and the processor is asked) and a bank statement in ISO 20022 camt.053 (paste the XML). Tell us if your bank
  uses another format (MT940, a CSV layout) or if your ERP export differs, so an importer can be added; until then a Controller pastes the lines. The Stripe test account has no bank account, so the payout read has only been run against Stripe's documented shapes, not a live payout.
- **Statutory ledger.** This is controlled finance evidence, not the general ledger. It does not post revenue and no total on the board is revenue.

## 4. What was run, and what was not
Refund, dispute and close tests pass on the recording processor and a real database. **Stripe TEST mode (2026-11-04, `tools/scripts/stripe_test_mode_proof.py`):** a refund sent twice under one key is one refund, it is found again by the case id carried as metadata,
the processor's own state and amount are read back, an over-refund is refused by the processor, and a test dispute is read with its reason and its own deadline. **Not run:** live money, a dispute contested at the processor, and any period closed against real bank
files. Acceptance rows WF09-OP-05 to 10 and CL04-C15 stay in progress until you have run them in a deployed environment.
