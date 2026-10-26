# CR-027: Invoicing, credit notes and the billing state machine

- **Target:** ZID-COM-001 §11, §13, §22, §30, §31 · **Raised by:** Phase J J-4 · **Status:** Open (built)

## Invoices

An invoice is generated **only** from a `COMPUTED` rating run on a frozen window. It is never built from UI counters or vendor dashboards.

| Table | Scope | Note |
|---|---|---|
| `commercial.invoice` | tenant-scoped (RLS) | Sequence number (gap-free per issuing entity, assigned at issue, not at draft), account, tenant alias, period, currency, subtotal, tax total, gross, `status` (`DRAFT`/`ISSUED`/`PAID`/`VOID`), `issued_at`, `rating_run_id`, `audit_batch_hash`, `pdf_digest`. |
| `commercial.invoice_line` | tenant-scoped (RLS) | One per rated line plus fee, true-up, credit, tax and pass-through lines. Carries the §30 minimum metadata: `meter_id`, `included_units`, `chargeable_units`, `unit_rate`, `discount_id`, `region`, `tax_code`, `net`, `tax`, `gross`, `reversal_source`, `vendor_reference` (pseudonymous). |
| `commercial.credit_note` | tenant-scoped (RLS) | Always points at an issued invoice and the lines it reverses. |
| `commercial.invoice_checksum` | tenant-scoped (RLS) | The hash chain of issued invoices per issuing entity, so a missing or altered invoice is detectable. |

Rules:

- **ISSUED is immutable.** A trigger refuses UPDATE and DELETE on issued invoices and their lines. A dispute creates a credit note and a ledger reversal; the original stays (§31 disputed invoice).
- The invoice document is a deterministic render of the rows (fixed ordering, fixed locale, no timestamps inside the body); `pdf_digest` is the digest of those bytes, so re-rendering proves the stored document.
- **Tax is a port.** `TaxPort.lines_for(invoice_draft) -> [TaxLine]` returns code, rate and amount; the platform stores and totals them and never computes tax itself. The default adapter returns no tax and marks the invoice `TAX_NOT_CONFIGURED`, which blocks issue unless the operator explicitly allows a zero-tax invoice. Merchant-of-record and the legal invoice template are decisions outside this build.
- **Gross vs net recognition for supplier checks** is not decided in code. Pass-through lines carry a `recognition_basis` field that Finance sets per supplier; the default is `UNDECIDED` and blocks issue.
- Money columns are `NUMERIC(18,6)` with a currency column; display rounds to the minor unit.

## Billing state machine (§11)

`DRAFT -> CONFIRMED -> PROVISIONING -> ACTIVE -> PAST_DUE -> RESTRICTED -> SUSPENDED -> TERMINATING -> CLOSED`

The account state lives in `commercial.billing_account_state` (account, state, reason, since, version). Transitions are a table in domain code, each with a reason code and an actor; illegal moves are refused and tested exhaustively.

**Restriction never touches security** (§11, §15, App. C). The restriction function is explicit about what each state removes:

| State | Removes | Never removes |
|---|---|---|
| `PAST_DUE` | nothing, notification only (dunning schedule from a policy table) | everything |
| `RESTRICTED` | new paid extras: new connections above plan, new credentials issued, new verification checks, plan upgrades | login, token refresh, revocation, recovery, signal intake, decisions on existing authority, data export |
| `SUSPENDED` | new entities of any kind and metered extras | sign-in and revocation of existing users, administrators' ability to export and to pay, anything required by law or safety |
| `TERMINATING` | as SUSPENDED, plus read-only | export, revocation, deletion and lawful-retention tasks |

A **safety suspension** (abuse, AUP) is a separate reason field on the same state, not a billing transition, so it cannot be cleared by paying and cannot be confused with non-payment (§11).

A test enumerates every endpoint tagged `security_path` and asserts it is reachable in every billing state. Adding a security endpoint without the tag fails the existing API sweep.

## Dunning and payment

Payment is a port (`PaymentPort`: authorize, capture, refund, with an idempotency key) with a recording fake. The platform records `payment_attempt` rows and advances the state machine from outcomes; it never holds card data (PCI boundary: the adapter returns a token and a status only). Dunning is a configurable schedule table (days after due, action, notification kind) so the grace period is data, with a notification through the alerts centre at each step.

## Month-end close (workflow, J-9 completes it)

freeze windows, run rating, check run hashes, generate drafts, require approval for any manual adjustment, issue, post the journal export (port). Each step is idempotent and re-runnable; a half-finished close resumes from the recorded step.

## API

| Route | Action | Notes |
|---|---|---|
| `GET /v1/invoices`, `GET /v1/invoices/{id}` (+ `/document`) | `commercial.read` | Tenant's own. |
| `POST /v1/commercial/invoices/draft` and `/{id}/issue` | `commercial.invoice.manage` (R3, dual control) | Operator surface. |
| `POST /v1/commercial/credit-notes` | `commercial.invoice.manage` (R3) | References an issued invoice. |
| `GET /v1/billing/state` | `commercial.read` | Account state, reason, what is restricted, and what never is. |

## Tests

- Every state transition (legal and illegal) and its reason code.
- The security-path guarantee in every billing state, over the real endpoints.
- ISSUED immutability at the database level; credit note reverses without editing; sequence numbers gap-free under concurrent issue.
- Render determinism: re-render equals `pdf_digest`.
- Invoice totals equal rated usage lines; reconciliation fails loudly on a one-cent drift.
- `TAX_NOT_CONFIGURED` and `UNDECIDED` recognition block issue; explicit override is recorded.
- Declined payment and cancelled order follow the dunning table to a restricted, recoverable state (§31).

## As built

- **Document:** a deterministic plain-text rendering (fixed ordering, UTC dates, LF line ends) stored by its SHA-256 as `document_digest`; `GET /v1/invoices/{id}/document` re-renders it and the digest must match. A PDF rendering can be added on top without changing the digest rule.
- **Numbering and chain:** one gap-free series per issuing entity (`ZID_COMMERCIAL_ISSUING_ENTITY`, default `ZID`), shared by invoices and credit notes. `invoice_chain` holds only numbers and digests, so the whole chain is verified without reading any tenant's invoices (`GET /v1/commercial/invoice-chain:verify`).
- **Payment state is derived, not stored:** an ISSUED invoice never changes; credited, paid and outstanding are computed from credit notes and captured payment attempts.
- **Dual control:** the operator who drafts an invoice cannot issue it.
- **Tax:** `NoTaxConfigured` blocks issue unless an operator records an explicit zero-tax override on that invoice. **Recognition basis** for pass-through lines is not applied yet because no pass-through line can be rated until vendor rates exist (J-7).
- **Restriction in practice:** the billing state feeds the entitlement gate, so a RESTRICTED or worse account (or a safety hold) gets `BILLING_RESTRICTED` on new paid extras when enforcing. A test asserts that no session, authentication, signals, decision, policy-runtime or relationship module calls the gate.
- **Dunning** ships with an empty policy table: nothing moves until the grace periods are decided. The operator route `POST /v1/commercial/tenants/{t}/dunning:run` applies it one recorded step at a time; a scheduled job is not built yet.
- **Payments:** local and test cells use a recording processor; a deployed cell refuses to collect (`PAYMENT_NOT_CONFIGURED`) until a processor adapter is wired.


## As built: Stripe

The payment and tax ports are implemented for Stripe (`StripePayments`, `StripeTax`) and selected by `ZID_STRIPE_SECRET_KEY_FILE`; see CR-030 "As built" for the behaviour. Cards, tokens and bank details never enter the platform: it holds the processor's customer id (`commercial.payment_customer`) only.
