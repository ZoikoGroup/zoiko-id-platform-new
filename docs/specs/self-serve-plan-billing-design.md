# Self-serve plan billing: show the price, pay on Stripe, then the plan switches

Status: PROPOSED. Nothing in this document is built. Prepared 2026-11-24 as the product owner's design. Proposed change request: CR-147.

Written for: the Founder, CFO, CCO, Product, and the engineers who will build it.

Sources: ZID-COM-001 (Commercial Estate Architecture, 2026-10-23, sections 5 to 7), ZID-COM-WF-2026-11 v3.0 (commercial architecture), ZID-UX-PRICE-004 v4.0 (pricing page, section WF-P08 and 06.3), `docs/signoff/commercial-v3-owner-pack.md`, CR-024 to CR-034, CR-040, CR-041, CR-054, CR-062, and the code in `backend/app/src/zid_platform/modules/commercial`.

---

## 1. The decision in one paragraph

A company administrator sees each plan with its real monthly price, picks one, reviews exactly what will be charged, pays on Stripe's hosted page, and the plan switches only when Stripe's signed webhook confirms that exact payment. Prices come only from an approved price book: the code never contains a price. Developer is free and automatic. Enterprise, partners and regulated services stay sales-assisted. We reuse the pay-before-switch machinery already built (CR-062) and add the missing front half: a customer-created order.

## 2. Principles (each one comes from a source document)

| # | Principle | Source |
|---|---|---|
| P1 | The price shown is the price charged. Both come from one approved price book version and one rating function. | v4.0 P18, WF-P08 |
| P2 | No price is hard-coded. No approved price book means "Quote required" and no Buy button. | v4.0 publication lock |
| P3 | The Stripe return page is never proof of payment. Payment is proved by the signed webhook plus a read-back of the Stripe object. | WF-P08, CR-062 |
| P4 | Order status, payment status and entitlement activation are three separate facts. | WF-P08 |
| P5 | One authorised purchase gives one payment and at most one activation. A repeat never charges twice or activates twice. | WF-P08, CR-062 |
| P6 | A minimum is a spend floor or a platform fee on top of usage, never both silently. | v4.0 financial control |
| P7 | A material change (price, tax, term) after review forces a new review. No pre-ticked boxes. | WF-P08 |
| P8 | Security and access to your own evidence are never a paid feature, and never restricted by billing state. | Pricing page `exit` disclosure, capability bands |
| P9 | No surprise trial conversion. A pilot never turns into a paid plan by itself. | v4.0, CAT-26 |

## 3. Who can do what

| Person | May |
|---|---|
| Company owner | Buy, change or cancel a plan; save or replace the payment method (`commercial.payment.manage` is owner only today, CR-034). |
| Company administrator with billing permission | Review plans and buy a plan if the owner has delegated `billing.payment.initiate` (CR-050). |
| Other members | See the plan and usage only. |
| Operator | Approve the price book, handle exceptions, refunds and disputes. Cannot buy on a customer's behalf. |

Decision D1 (recommended): keep purchase with the owner and any administrator the owner has explicitly delegated billing to. Step-up authentication (a fresh passkey or MFA check) is required at the "Pay" step. A purchase above the company's own commitment authority (CR-054) needs the company's approver, as it does today.

## 4. Plan catalogue

Prices below are the values already in the DRAFT price book (migration `commercial_0001`, from ZID-COM-001). They are proposals until the owners approve the book (section 11). The price shown to a customer is always read from the approved book, never from this table.

| Plan | Platform fee / month | Included per month | Support | Sold how |
|---|---|---|---|---|
| Developer | $0 | 2,500 active users, 25,000 auth events | Community | Automatic at sign-up, no payment step |
| Launch | $299 | 10,000 active users, 150,000 auth events | Business hours | Self-serve |
| Business | $1,499 | 50,000 active users, 1,000,000 auth events, SSO | Priority | Self-serve |
| Scale | $4,999 | 250,000 active users, 5,000,000 auth events | Priority plus review | Self-serve |
| Enterprise | from $12,500, a spend floor | Contracted | Dedicated | Quote and signed order only |

Overage above the included amount is billed in arrears per unit from the price book (for example $0.025 per extra active user on Launch). Usage of identity and business verification, and the other 16 service families, is quoted until a family rate card is signed (see section 12).

Decision D2 (recommended): Launch, Business and Scale on one self-serve monthly term. Annual prepay is phase 2, with the existing rule that a prepaid discount cannot exceed 10% and a pass-through line is never discounted.

Decision D3 (recommended): the platform fee is billed **in addition to usage** (`fee_is_floor = false`, as seeded for the self-serve plans), and the plan page says so in one plain sentence. Only Enterprise uses a floor.

## 5. The customer journey

```
Plans page  ->  Review  ->  Pay on Stripe  ->  Waiting for confirmation  ->  Plan active
   (see price)   (exact       (hosted page,      (webhook + read-back)         (receipt, new limits,
                  amounts)      3-D Secure)                                      invoice credit)
```

### 5.1 Plans page (`/admin/billing`, "Plans")

- Four cards (Developer, Launch, Business, Scale) plus an Enterprise "Contact sales" card. A "Current plan" badge marks where the company is.
- Each card shows: monthly fee, what is included, overage rates, support level, and one primary button (**Choose Launch**, **Upgrade to Business**, **Switch at period end**). Never two primary buttons.
- Fee disclosure under the cards: "Platform fee is billed in addition to usage. Taxes are extra and shown before you pay."
- If no approved price book exists, or the company's market is not released, the card shows "Quote required" with **Talk to us**, and there is no Buy button (P2).
- A comparison table below the cards. A plan's own page text and the table come from the same data, so they cannot disagree.

### 5.2 Review panel (before any money moves)

Shows, in this order:
1. Seller entity, paying company, plan name and the price book version.
2. Billing period, currency, monthly fee, and **amount due today** (see section 6).
3. Tax line (estimated, exempt, or calculated), and the total.
4. What renews and when, how to cancel, and what happens on failed payment.
5. One required, unticked checkbox: "I authorise this charge and the renewal terms."

A review is valid for 60 minutes and carries a hash of exactly what was priced. If the price book, tax or period changes before payment, the panel says so and asks again (P7).

### 5.3 Pay on Stripe

- **Pay** creates the order and opens Stripe Checkout for the exact amount. Card details are entered on Stripe's page and never reach our servers or logs (CR-034).
- 3-D Secure and other authentication are handled by Stripe. A failed or abandoned session leaves the current plan unchanged.
- Cancel returns to the Plans page with a clear "Nothing was charged" message.

### 5.4 Waiting for confirmation

The return page shows "Confirming your payment with Stripe". It calls the confirm endpoint, which reads the Stripe session and checks the order, company, amount, currency and paid state. It never trusts the browser (P3). The page keeps polling for up to 2 minutes and then says "We will email you as soon as it is confirmed". The webhook finishes the job even if the browser is closed.

### 5.5 Plan active

The company sees a confirmation with the new plan, new limits, the payment receipt link, and the date of the next charge. The fee already paid shows as a credit on the month-end invoice, so it is not billed twice (CR-062).

## 6. Money rules

| Case | Amount due at checkout | Plan changes |
|---|---|---|
| New paid plan from Developer | Full monthly fee of the new plan | On confirmed payment |
| Upgrade mid-period | Fee difference, prorated by days left, computed by the same rating function that builds the invoice | On confirmed payment |
| Downgrade | $0 today | At the end of the current period; the customer can undo it before then |
| Cancel to Developer | $0 | At the end of the current period; data, evidence and sign-in are unaffected (P8) |
| Renewal | Monthly fee charged off-session to the saved card on the period date | Continues |

- **Proration** uses the existing rating function, which already splits a month into stretches per plan. The checkout amount and the invoice therefore agree to the cent (P1). All maths uses decimals, never floats.
- **Currency**: USD only at first, with the currency column kept everywhere (Phase J decision). A price book in one currency never prices a company billed in another.
- **Tax**: Stripe Tax is the source. Until it is active on the account, the review panel shows "Tax calculated at checkout" and the buyer sees the final total on Stripe's page. A tax failure blocks the purchase; it never charges without a tax decision (P1, WF-P08 fallback "tax fail").
- **Refunds**: a refund or dispute follows the existing refund and credit-note flow (CR-041). No automatic refund of unused days after a downgrade, except where the law requires it. This is a policy line for Legal (D6).

## 7. States and transitions

Order states: `DRAFT` -> `AWAITING_PAYMENT` -> `PAID` -> `ACTIVATED`, with `EXPIRED`, `CANCELLED` and `REFUND_DUE` as side exits. (`SIGNED` and `AWAITING_PAYMENT` already exist in `orders_service.activate`; the new front half creates the order already signed by the purchaser's authorisation.)

| From | Event | To | Rules |
|---|---|---|---|
| none | Buyer presses Pay on a valid review | `AWAITING_PAYMENT` | Order and Stripe session created once per review hash. Repeated clicks return the same session. |
| `AWAITING_PAYMENT` | Signed webhook `checkout.session.completed` or `payment_intent.succeeded` for this order, amount and currency match | `PAID` then `ACTIVATED` | Read-back at Stripe first. Wrong amount, wrong customer or wrong order: stay waiting and raise a review item, never activate. |
| `AWAITING_PAYMENT` | Session expires or the buyer cancels | `EXPIRED` | Plan unchanged. The buyer can start again. |
| `AWAITING_PAYMENT` | Price book or tax changed materially | `EXPIRED` and a new review | Old session cannot be paid (P7). |
| `ACTIVATED` | Refund or lost dispute | `REFUND_DUE` | Existing CR-041 flow. Plan change on refund is an operator decision. |

**Failed renewal (dunning).** Day 0 the charge fails: email the owner and show a banner. Retries on day 3 and day 7. On day 14 the plan moves to Developer limits (not suspended, P8). Sign-in, security controls and access to audit evidence continue throughout. Everything on this row needs the owners' confirmation (D5).

## 8. Backend design

### 8.1 New API (customer side, all behind `billing.*` permissions and `protected_action`)

| Route | Purpose |
|---|---|
| `GET /v1/billing/plans` | The plan cards: approved price book lines, included amounts, overage rates, current plan, and per-plan `purchasable` with a reason when not. |
| `POST /v1/billing/plan-checkout:review` | Server-side review: returns amounts, tax state, the review hash, and expiry. Writes nothing. |
| `POST /v1/billing/plan-checkout` | Needs the review hash and the authorisation flag. Creates the order and Stripe session once; returns the Stripe URL. |
| `POST /v1/billing/plan-checkout/{order_id}:confirm` | Existing `activations` confirm path, reused. |
| `POST /v1/billing/plan-change:schedule` and `:undo` | Downgrade and cancel at period end, and undo before it takes effect. |

The existing routes `/v1/billing/activations`, `.../{order_id}/pay` and `.../{order_id}:confirm` stay; the new checkout simply raises the order they already handle.

### 8.2 Reuse and new pieces

- **Reused unchanged**: `activation_service` (pay, confirm, credit on invoice), `orders_service.activate(require_payment=True)`, Stripe adapters, the signed webhook ledger and review queue, `commercial.read`/payment permissions, the commitment approval check.
- **New**: a self-serve order builder that makes a quote and order from the approved price book line for the chosen plan, signed by the purchaser's authorisation (no operator second person is needed for catalogue plans, because the price book was already approved by owners); the review hash; plan-change scheduling; and the renewal scheduler.
- **Where activation stays gated**: the order activator rule ("never the person who entered the order") is satisfied by the system activating on the verified webhook, recorded with the Stripe event as the actor reference.

### 8.3 Data model additions

- `commercial.plan_checkout_review(review_hash, tenant_id, plan_code, price_book_id, amounts, tax_state, expires_at)`: so a review can be verified and expires.
- `commercial.pending_plan_change(tenant_id, from_plan, to_plan, effective_at, created_by, undone_at)`.
- `commercial.sales_order.source` = `SELF_SERVE` or `SALES`, so reports and audits can tell them apart.
- A billing-period anchor per tenant (period start and end), used by proration and renewal.

All new tables are tenant-scoped with row-level security, listed in `database/table-ownership.yaml`, with a migration in `migrations/commercial/versions`.

### 8.4 Guards (all enforced on the server)

- Plan must be `released`, the price book `APPROVED` and effective, the currency must match, and the market gate (`/v1/commercial/market-gate:check`) must allow it.
- Enterprise, partner and any plan that needs a regulated service refuse self-serve with a clear reason.
- Idempotency key per review hash on checkout creation; one Stripe session per order; one activation per order.
- Webhook handling is duplicate-safe (the existing event ledger) and out-of-order safe (activation re-reads the Stripe object).
- Rate limits on review and checkout routes per company.
- Every step is appended to the commercial change log with the purchaser, order, review hash and Stripe references. No card data is ever stored or logged.

## 9. Frontend design

- Replace the plan-request wording in `orders-plans.tsx` with plan cards, a review panel and the confirmation states from section 5, in the existing design system. Reuse `RadioCards`, `Banner`, `Card`, `Button`.
- States to design and test: loading, no approved price book, market not released, not owner, already on this plan, review expired, price changed, Stripe cancelled, confirming, confirmed, failed, webhook delayed, refund due.
- Accessibility: one primary button per card, announced price and period, focus moves to the confirmation heading, errors are text, not colour only.
- Public `/pricing` page: show the plan fees only when the approved price book exists for the visitor's market, otherwise "Quote required" (unchanged rule, P2).
- The "Volumes" step is already removed from the solution builder, so Enterprise and regulated quotes use Services and Coverage, then Review.

## 10. Security and compliance

- Owner or delegated billing permission only, plus step-up authentication on Pay.
- Card data stays on Stripe (PCI scope stays minimal). Existing redaction tests continue to apply.
- The webhook secret and Stripe key stay in gitignored files locally and in the secret store in production; production must refuse to start without them.
- Tax and invoicing follow the seller entity on the market matrix row. No market row means no self-serve sale (P2).
- Privacy: the review stores amounts and references only, no identity data.
- Audit evidence and security features are never gated by plan or billing state (P8).

## 11. Pricing governance (the owners' part)

The code never ships a price. The path to a visible price:

1. Finance enters or confirms the plan fee lines in the price book (the DRAFT book already holds the proposal values).
2. A different operator approves the book (`POST /v1/commercial/price-books/{id}:approve`). In the production operating mode this is a second person; in the local direct mode it is not.
3. The catalogue becomes `PRICED`, and the plan cards read the amounts automatically.
4. Commercial and Finance also confirm the plan request allowances in `plan-throughput.yaml` (currently an engineering default) and set `status: CONFIRMED`.

Until step 2, the system keeps answering "Quote required", which is the safe state.

## 12. What stays out of self-serve (and why)

- **Per-service checks (IDV, KYB, DOC, SCR and the rest)**: no rate exists. The owners must sign a family rate card (three signatures, then activation) and the market matrix must release the market. Until then they are quoted by Sales.
- **Enterprise and partner plans**: contracts, spend floors and channel terms.
- **Regulated services**: need provider and regulatory rights per country.
- **Free trial**: stays off until separately approved (v4.0).

## 13. Observability and operations

- Metrics: reviews started, checkouts created, payments confirmed, time from payment to activation, webhook lag, payments confirmed but not activated (must be zero), activations without a payment (must be zero), failed renewals, refunds.
- Alerts: any "paid but not activated" over 5 minutes, any webhook signature failure burst, any amount mismatch.
- A daily reconciliation compares Stripe payments to orders and credits, and raises a finance review item for any difference (G4).
- Runbook: stuck payment, duplicate webhook, refund after activation, price book rollback.

## 14. Test plan (acceptance)

1. A plan never activates on the browser return alone (P3). Only a verified webhook plus read-back activates.
2. The review amount equals the Stripe session amount equals the invoice credit, to the cent (P1).
3. A repeated Pay click makes one order, one session and one charge (P5).
4. A changed price book or tax after review forces a new review (P7).
5. No approved price book: no price, no Buy button (P2).
6. A non-owner without delegation cannot buy; a member cannot see the Pay button.
7. Wrong amount, wrong currency, wrong customer or wrong order on the webhook: stay waiting and raise a review item.
8. Downgrade and cancel take effect at period end and can be undone; security, sign-in and evidence access never change (P8).
9. Failed renewal follows the dunning ladder and never locks the company out.
10. Cross-tenant: one company cannot read or pay another's order.
11. Duplicate and out-of-order webhooks are harmless.
12. Accessibility checks on every state, with the existing a11y test pattern.

## 15. Build slices

| Slice | Content | Done when |
|---|---|---|
| B1 | Approve the price book locally; `GET /v1/billing/plans`; plan cards with real prices and "Quote required" fallback | Cards show amounts from the book, none from code |
| B2 | Review (`:review`), the review hash and expiry, and the review panel | Review amount verified server-side; price change forces re-review |
| B3 | Self-serve order creation and Stripe session (`plan-checkout`), reuse of CR-062 activation, confirmation page | Paid test-mode purchase switches the plan; unpaid never does |
| B4 | Upgrade proration, downgrade and cancel at period end, undo | Amounts match the invoice; scheduled changes apply on the date |
| B5 | Renewal scheduler and the dunning ladder, with emails | Failed renewal path tested end to end, no lock-out |
| B6 | Reconciliation job, metrics, alerts, runbook | Zero unmatched payments in rehearsal |
| B7 | Owner sign-offs, production price book approval, tax activation, rollout by market | Gates G2, G4 and G6 granted by the named people |

Phase 2: annual prepay with a capped discount, per-service rate cards, and the first priced verification families.

## 16. Decisions for the owners

| # | Decision | Recommended | Owner |
|---|---|---|---|
| D1 | Who may buy | Owner and explicitly delegated administrators, with step-up | Founder, Security |
| D2 | Terms offered | Monthly only for launch; annual in phase 2 | Product, CFO |
| D3 | Fee basis | Platform fee in addition to usage; Enterprise as a floor | CFO |
| D4 | Final plan fees | $299 / $1,499 / $4,999 as proposed, or the owners' numbers | CFO, CCO |
| D5 | Dunning ladder and the day-14 downgrade | As in section 7 | CFO, Legal |
| D6 | Refund of unused days on downgrade | None unless the law requires | Legal, CFO |
| D7 | Currency and markets at launch | USD, markets released on the matrix only | CFO, Legal |
| D8 | Stripe Tax | Activate before production; head-office address needed | Controller |
| D9 | Free trial | Off | Product |

## 17. Risks

| Risk | Control |
|---|---|
| A price shown differs from the charge | One price book version and one rating function; review hash (P1, P7) |
| Plan active without payment | Activation only on verified webhook plus read-back; metric and alert on the invariant |
| Paid but not activated | Idempotent activation, reconciliation job, alert at 5 minutes |
| Customer locked out after a failed payment | P8: never restrict sign-in, security or evidence |
| Unapproved prices shown to the public | The price book gate and the existing pricing-page guard test |
| Stripe Tax inactive | Show "calculated at checkout"; block purchase on a tax error |
| Proposal prices treated as final | Document status stays PROPOSED until D4 is signed; production refuses an unapproved book |
