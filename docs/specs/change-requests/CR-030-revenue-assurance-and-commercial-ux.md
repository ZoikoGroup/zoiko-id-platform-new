# CR-030: Revenue assurance, the commercial change log, and the commercial console and pricing page

- **Target:** ZID-COM-001 §19, §22, §24, §25, §26, §31 · **Raised by:** Phase J J-8, J-9, J-10 · **Status:** Open (J-9 and J-10 built; J-8 built; adapters for a real payment processor, tax engine, ERP and suppliers not built)

## Part 1: Revenue assurance and the audit package (J-9)

| Table | Scope | Note |
|---|---|---|
| `commercial.change_log` | not tenant-scoped (account and tenant columns) | **Append-only** hash-chained log of every commercial change: price book, plan, entitlement, discount policy, order, adjustment, invoice issue, credit note, vendor rate, billing state change. Fields: actor, role, timestamp, before and after digests, reason, linked ids (offer, contract, entitlement, meter window, price rule, invoice, payment, ledger posting). UPDATE and DELETE refused by trigger. |
| `commercial.close_run` | tenant-scoped (RLS) and account-scoped | Month-end steps and their status, so a close resumes where it stopped. |
| `commercial.journal_entry` | account-scoped | Ledger-mapped debit and credit lines with period, reversal link and approval; produced by a port to the external GL. The platform records and exports; it does not post to an accounting system itself. |
| `commercial.revenue_schedule` | account-scoped | Performance obligation, recognition start and end, deferrals and allocated price. Monthly subscription is ratable; annual prepaid is a contract liability recognized over the period; usage on rating; migration on acceptance; intercompany flagged for elimination (§30). |

Revenue-assurance checks (§24), each an executable detector that writes to a `commercial.exception` queue with an owner:

| Failure mode | Detector |
|---|---|
| Meter loss or double billing | Billable events versus source rows per window; duplicate dedupe keys (should be impossible) |
| Unapproved entitlement | Entitlement diff against the approved-order set; any entitlement with no order or pilot |
| Quote below cost | Any order whose lines breach the floor table |
| Supplier invoice mismatch | Unmatched vendor lines (CR-029) |
| Revenue leakage | Usage versus entitlement and unbilled-event audit |
| Contract or SKU drift | Plan snapshot digest on the entitlement versus the current plan row |

**Audit pack** (`GET /v1/commercial/audit-exports`): source-event hash totals, rated usage, invoice checksums, approval chain and the exception register for a period, as a signed JSON bundle verified with the existing evidence tooling. A cryptographic claim is only made where the existing offline verifier proves it.

Management KPIs (§22, §25) are **computed views**, not stored numbers: MRR and ARR (excluding one-time and intercompany), consumption revenue, NRR, net gross margin by SKU and vendor, DSO. Each has a definition string and a test against hand-computed data. The illustrative run-rate example in §22 (20 Business, 6 Scale, 2 Enterprise = $84,974/month, $1,019,688/year) is a regression test of the ARR view, not data that ships.

## Part 2: Commercial console and pricing page (J-8)

Built in the existing Next.js apps and `@zoiko/ui` components, with the same loading, empty, error, success, permission-denied, quota-limited and suspended states (§19) and the same Playwright plus axe gate.

**Customer console** (`apps/web`), under a new Billing area: Subscription and entitlements; Usage and included volume (with the 50/80/100 meters); Bill preview and projected total; Invoices and credit notes (download the deterministic document); Budgets; Billing contacts; Billing state banner that says exactly what is and is not restricted. The Usage screen (CR-023) links here but stays operational.

**Operator console** (`apps/operator`): Price books (draft, approve with dual control), Plans and capabilities, Entitlements and overrides, Quotes and approvals, Orders, Rating runs and close, Exceptions, Vendor rates and costs, Audit exports.

**Public pricing page** (served by the web app, public route): five plan columns with explicit unit definitions, the §7 meter glossary, add-ons, a calculator (users, logins, decisions, M2M grants, SSO and SCIM) that calls `GET /v1/catalog` and the **same rating function** through `POST /v1/billing/estimate` and returns an estimate with its assumptions and tax disclaimer, never a binding quote. It shows only `APPROVED` price books and only capabilities with `released = true`; an unreleased capability is not shown as available (§16, §29). Accessible tables, keyboard-operable calculator, mobile accordion, predictable focus.

**Copy rules** (enforced by a test over the page content): no certification, "compliant with" or availability wording; the hero and secondary lines from §19 are the only claims; the Enterprise price appears as "From" with the illustrative footnote only if the price book says so.

## Part 3: Commercial acceptance suite (J-10)

One suite, one test per row of ZID-COM-001 §31 plus the §26 definition of done, mapped in `acceptance-tests.csv` under a new `COM` group:

| Test | Asserts |
|---|---|
| Duplicate or delayed meter event | one charge, original retained, replay logged |
| Identity merge mid-month | documented MAH policy, no silent duplicate |
| Cross-region usage | pricing and residency policy applied, no raw attributes leak |
| IDV supplier 5xx with retry | charge follows retry rule, supplier cost recorded |
| Plan limit exceeded | warning and controlled expansion, security paths intact |
| Declined payment or cancelled order | dunning, grace, safe restriction, recoverable |
| Mid-month amendment | deterministic proration with effective timestamps |
| Disputed invoice | original immutable, linked credit note, ledger reversal |
| Vendor rate spike or unsupported country | route blocked or separately approved, quote cannot promise it |
| Compromised agent credential | grants and sessions revoked, evidence preserved, notice sent |
| Offboarded regulated tenant | export, revocation, deletion and lawful retention evidence |
| SLA request for an unlaunched cell | CPQ blocks the commitment |
| 99.99% replay identity | 100,000 synthetic events rate identically on replay; zero double-charge under duplicate and reordered delivery |
| Customer reproduces own ledger | the tenant export equals the rated lines and excludes other parties' data |

Several of these re-use platform tests that already exist (agent compromise, offboarding); those are linked, not duplicated.

## Actions and events

Actions: `commercial.audit.read`, `commercial.exception.manage`, `commercial.close.run`. Events: `commercial.close.completed`, `commercial.exception.raised`.

## As built (J-9, J-10)

- **Change log:** one append-only hash chain (`commercial.change_log`, appended under a locked head row) with entries for price-book approval, operator plan change, entitlement override, order activation, invoice issue, credit note, billing-state change, vendor rate and usage-adjustment decisions. `GET /v1/commercial/change-log:verify` recomputes every digest and link.
- **Detectors:** per tenant (`POST /v1/commercial/tenants/{t}/assurance:run`): entitlement without an order, entitlement price-book drift against its order, meter loss (a dry run of reconciliation), a frozen month never rated, a computed rating never invoiced. Across the platform (`POST /v1/commercial/assurance:run`): an order line below its floor without CFO acceptance, open supplier disputes. Findings are exceptions, one open per (kind, reference), resolved with an owner.
- **Close:** `POST /v1/commercial/tenants/{t}/close` freezes, rates and drafts, recording each step so it resumes; issuing stays a second person's step.
- **Journals** are derived from issued invoices, credit notes and captured payments and always balance; exported, never posted.
- **Audit pack:** frozen-window hashes, rating hashes, invoice digests and chain hashes, and the tenant's change-log entries, with a digest over the bundle. It is not signed yet; signing with the evidence keyring is the next step.
- **KPIs:** MRR and ARR from activated, non-pilot orders at their contracted price book, intercompany accounts excluded. The proposal's §22 illustration ($84,974 / $1,019,688) is a unit test.
- **J-10:** the Appendix C failure paths are rows `COM-01` to `COM-14` in `acceptance-tests.csv`; twelve link to tests, two (identity merge mid-month, cross-region usage) have no test yet and stay NOT_STARTED.


## As built (J-8, J-10 rows COM-02 and COM-03)

- **Public estimate:** `POST /v1/billing/estimate` (no credentials, no tenant data) runs the invoice's own `rate` function over the approved price book and only released price lines, for a named plan and expected volumes. It returns the lines with their formulas, the assumptions and the price book version, and `UNPRICED` while no book is approved. The web app relays it through `/api/pricing/estimate` (same-origin, known fields only).
- **Public page:** `/pricing` reads `GET /v1/catalog` on the server, shows the five plans, the rates above the included amounts, the meter glossary and the calculator. The only headline claims are the two §19 lines; `features/pricing/copy.test.ts` fails on certification, "compliant with", availability or uptime wording.
- **Customer console:** `/admin/billing` (Billing in the Company navigation): Subscription, Usage (50/80/100 meters and the ledger CSV), Bill preview, Invoices (immutable, with the document and its digest), Budgets, and a banner that says what the billing state restricts and never restricts. Invoice documents and the ledger CSV download through the BFF; the browser holds no price logic.
- **Operator console:** `/commercial` in `apps/operator` (Commercial in the navigation). Reads are typed; every write is rendered from one entry of `features/commercial/actions.ts` (method, path, capability, body fields), asks for a confirmation sentence on the hard-to-undo steps, and shows the cell's answer verbatim. The cell still authorizes and validates everything; a refusal for a missing grant says which capability to request. `actions.test.ts` checks every path placeholder has a field, money stays a decimal string and path values cannot escape their segment. The operator BFF now relays `/v1/commercial/`.
- **Signed audit pack:** `GET .../audit-pack` returns the pack with a compact JWS (`typ zid-commercial-audit-pack+jws`) made with the evidence signing key over the pack plus issuer and time; `POST /v1/commercial/audit-pack:verify` and `verify_audit_pack` check the signature against the trusted evidence public keys (never the key beside the signature), that the body is unaltered, and that the bundle digest matches. Tampering, an untrusted key and an unsigned pack each fail with a distinct code (unit test).
- **Stripe (payments and tax):** `StripePayments` collects an invoice's outstanding amount with an off-session PaymentIntent for the account's Stripe customer (`commercial.payment_customer`, migration `commercial_0010`, set by the operator action "Link the payment processor customer"); the idempotency key is Stripe's `Idempotency-Key`; amounts are converted to minor units per currency and refused if precision would be lost; an outage or 429 is a FAILED attempt (`PROVIDER_UNAVAILABLE`), never an exception; no customer id means `NO_PAYMENT_METHOD` and nothing is sent. `StripeTax` asks Stripe Tax per positive line, exclusive of tax, using the buyer's country from its commercial account; no country or any refusal returns None, which blocks issuing unless an operator records the explicit zero-tax override. The port signatures gained `customer_ref` and `country`. Enabled by `ZID_STRIPE_SECRET_KEY_FILE` (a file, never an inline value).
- **Journal export:** `GET .../journal?format=csv` returns the balanced journal as a file for the finance team to import; the platform exports, never posts.
- **Still to build:** supplier (IDV, KYB, sanctions, messaging) adapters, which need signed tariffs and a chosen supplier per route.
- **Identity merge (COM-02):** `commercial.mah_merge` (insert-only, tenant-scoped) maps a merged-away principal to its survivor. `mark_mah` counts a person once across every merged identity. `record_identity_merge` refuses a merge into itself, twice, or in a loop, and returns the months in which both identities were already counted before the merge was recorded; those events stay (insert-only) and the caller requests an approved usage adjustment. No code path merges principals yet, so nothing calls `record_identity_merge` today: whoever builds the merge flow must.
- **Cross-region (COM-03):** an event records the region it was earned in; the tenant's entitlement and price book, not the region, set the price; the ledger table has no column for a name, email, subject or claim.
