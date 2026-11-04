# Finance authority and customer commitments: what only the owners can close (ZID-COM-CL-003, ZID-COM-CL-004, ZID-COM-RAT-011; slice L-14b to L-14e, CR-054)

1. **Founder approval** of Closures 03 and 04 and Wireframes 08 to 10 and their acceptance tests.
2. **The policy file's numbers.** Everything in `backend/config/commercial/finance-policy.yaml` is a proposal. The Founder and the CFO, two different people, must sign the file's
   content hash before a deployed environment acts on it (the console shows the hash and who has signed). Confirm or amend: credit-note tiers 500, 5,000 and 25,000 USD
   (Finance Ops Lead, Controller, CFO, then CFO and Founder); pilot tiers 1,000, 5,000 and 10,000 USD with the CCO plus a finance role, then CFO and Founder; the 90-day window
   that adds related credits together; the 90-day maximum for a pilot benefit; 14 days before an undecided or an unrun approved request lapses. A change of one character needs a new signature.
3. **Currency.** Only USD is pinned (`fx_usd`). Say which other currencies are allowed and where their rates come from; until then a request in another currency is refused.
4. **Who may open and who may run.** Proposed: Finance Ops Lead and RevOps open; only a Finance Executor runs, and not someone who signed. Appoint the people (the Appointments screen);
   until someone holds each role, nothing can be approved.
5. **Customer commitment thresholds.** Proposed 2,500 USD (one authorised approver) and 25,000 USD (Owner or Procurement Approver), two different people above that, non-standard terms
   after 36 months. Confirm or amend.
6. **The enforcement switch** `commitment.enforcement`. REPORT_ONLY is the default: an order activated without an approved commitment is recorded and let through. Decide when it
   becomes ENFORCED, and for how long REPORT_ONLY may stand in a deployed environment.
7. **Procurement Approver.** Built as an individually named, capped delegation the Owner gives, with the Owner's step-up. Confirm that this is the intended meaning, and whether a cap
   may be unlimited.
8. **Non-standard terms review.** A Zoiko Controller or CFO marks the terms reviewed after contract, legal and finance have read them. Confirm that this stands in for the three separate reviews, or name them.
9. **Credit against a paid invoice.** A credit note on a paid invoice must say whether the money is refunded, held as a customer balance or settled outside the platform. A refund is not
   executed by this slice. Provide Closure 05 (refunds and disputes) so refund execution can be built.
10. **Discount bands (CL-03).** The signed-policy lock applies to discount approvals outside direct mode. The band table itself and the delegated AE limit are not rulings engineering can make.
11. **Reconciliation and period close.** Need a bank and ERP feed and a named owner; not built.
12. **Evidence for Document 11.** Gates G2 and G4 need a named reviewer and an immutable evidence reference; engineering has not filled them.
