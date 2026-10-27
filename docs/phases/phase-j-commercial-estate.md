# Phase J: Commercial estate (design)

> **Source:** ZID-COM-001 v1.0 (Identity Infrastructure Commercial Architecture, 23 Oct 2026). **Status of the source: PROPOSED.** Every price, quota, discount rule, SLA figure and margin in it is a draft approval candidate. This phase builds the *machinery* (catalogue, entitlements, metering, rating, invoicing); the numbers are **data** loaded as a draft price book version, never constants in code.

Back to the [phase index](README.md).

## Principles taken from the source (and enforced in code)

1. **Backend is the only authority.** No frontend price or flag grants access (ZID-COM-001 §2). Entitlement checks run server-side and fail closed for paid extras only.
2. **Security paths are never plan-gated.** Login, token refresh, revocation, recovery and signal intake keep working when a quota is exceeded or a payment fails (§6, §11, App. C). Only metered extras, new paid capabilities and volume above a hard cap are restricted.
3. **One billable event per chargeable operation.** Source events are immutable and idempotent by a deduplication key; invoices are reproducible from them (§7, §11, §26).
4. **Versioned and dated.** Price books, SKUs and plan snapshots are versioned; a signed order is never repriced retroactively (§12).
5. **Money is fixed-precision decimal with an explicit currency** (§30). No floats anywhere in rating.
6. **No data is sold; verification is a pass-through** (§2, §8). Supplier checks are separate SKUs and are not part of the base plans.
7. **Nothing is marketed that is not enabled** (§16, §29). A plan row lists a capability only if its release gate passed; the catalogue carries a `released` flag per capability.

## Slices

| ID | Slice | Depends on | Core deliverable | Source |
|---|---|---|---|---|
| J-1 | **Catalogue and entitlements** | Phase H | SKU registry, price book versions, five plans, per-tenant entitlement with effective interval, server-side gate, `GET /v1/catalog`, `GET /v1/tenants/{id}/entitlements` | §5, §6, §29 |
| J-2 | **Billable metering** | J-1, metering | Billable event ledger (event id, dedupe key, meter, quantity, SKU version, agreement version, adjustment lineage); MAH, auth event, policy decision, M2M grant, VC issued, VC presented | §7, §26 |
| J-3 | **Rating** | J-1, J-2 | Included quota, graduated overage, extra SSO/SCIM connections, proration, credits; the §13 worked example as a test | §7, §13 |
| J-4 | **Invoicing and billing state** | J-3 | Immutable invoice and credit note with line lineage and checksum; state machine Draft to Closed; restriction rules that never touch security paths | §11, §30 |
| J-5 | **Quotes, orders, contracts** | J-1, J-4 | Quote validation, order and amendment, commercial account over many tenants, discount authority and margin-floor check | §10, §12, §14 |
| J-6 | **Anti-bill-shock and budgets** | J-2, J-3 | 50/80/100% warnings, projected month-end bill, budget alerts through the alerts centre | §7 |
| J-7 | **Vendor-cost ledger** | J-2 | Supplier check cost per event, retry rule, reconciliation join | §8, §23 |
| J-8 | **Console and pricing UX** | J-1 to J-6 | Billing, subscription, usage and budget screens; public pricing page with a non-binding calculator | §19 |
| J-9 | **Revenue assurance** | J-4 | Append-only commercial change log, month-end close, audit export, journal export | §11, §24 |
| J-10 | **Commercial acceptance suite** | all | The eleven App. C failure paths plus replay determinism, as executable tests | §31 |

Designs: J-1 [CR-024](../specs/change-requests/CR-024-commercial-catalogue-entitlements.md); J-2 [CR-025](../specs/change-requests/CR-025-billable-metering.md); J-3 [CR-026](../specs/change-requests/CR-026-rating.md); J-4 [CR-027](../specs/change-requests/CR-027-invoicing-billing-state.md); J-5 [CR-028](../specs/change-requests/CR-028-quotes-orders-contracts.md); J-6 and J-7 [CR-029](../specs/change-requests/CR-029-budgets-and-vendor-costs.md); J-8 to J-10 [CR-030](../specs/change-requests/CR-030-revenue-assurance-and-commercial-ux.md). Each design is revisited at the start of its slice, against what the previous slice actually built.

## Status

| Slice | Status | Evidence |
|---|---|---|
| J-1 Catalogue and entitlements | Built | `test_commercial_catalogue.py` (14 rule tests), `test_commercial_pg.py` (seed equals the proposal, DRAFT invisible, approved book frozen by the database, entitlement history and one-active rule, tenant isolation, fail-closed gate, 402 on plan limits, operator grants and second approver) |
| J-2 Billable metering | Built | `test_billable_ledger.py` (11 rule tests), `test_billable_pg.py` (one event per operation, rollback leaves none, tenant isolation, insert-only, write failure never blocks, MAH once a month, freeze with replayable hash, frozen window refused by the database, late events, reconciliation, 8-way parallel recording, tenant ledger and CSV, operator freeze and two-person adjustments); billing assertions added to the passkey login, AuthZEN, OAuth client-credentials and credential tests |
| J-3 Rating | Built | `test_rating.py` (16 rule tests, starting with the proposal worked bill at $2,053.00), `test_rating_pg.py` (the worked bill from the ledger, replay returns the same run, a credit supersedes and keeps history, mid-month upgrade prorated from entitlement history, default plan, open month refused, operator and tenant HTTP) |
| J-4 Invoicing and billing state | Built | `test_invoicing_rules.py` (state machine exhaustively, restrictions never touch security, dunning, deterministic document, hash chain, credit limits), `test_invoicing_pg.py` (second-person issue, tax port and zero-tax override, digest-proved document, gap-free numbers across tenants, chain verification, credit notes, idempotent payments, dunning from policy, billing restriction on the gate, safety hold, database immutability, HTTP) |
| J-5 Quotes, orders, contracts | Built | `test_order_rules.py` (each rejection reason alone, exact discount band boundaries, approvals bind digest and people), `test_orders_pg.py` (unlaunched region refused, band approvals, signed order only for the account tenant, two-person idempotent activation into the entitlement, signed orders immutable, writers of entitlements enumerated, HTTP) |
| J-6 Budgets and bill preview | Built | `test_budget_rules.py`, `test_budgets_pg.py` (preview equals the rating function, thresholds fire once a month, quota warnings, a budget never throttles, tenant isolation, HTTP) |
| J-7 Vendor-cost ledger | Built | `test_vendor_rules.py`, `test_vendor_costs_pg.py` (provider errors costed never charged, retry pricing, spike blocks the route, reconciliation into disputes, recording never blocks verification, the verification callback records cost) |
| J-8 Console and pricing UX | Built (customer console, public page and operator console) | Customer `Billing` area in `apps/web` (subscription, usage with 50/80/100 meters, bill preview, invoices, budgets, billing-state banner); public `/pricing` page with the §19 hero lines, five plan columns, meter glossary and a calculator over `POST /v1/billing/estimate` (the invoice's own rating function); `copy.test.ts` scans the page copy for certification, compliance and availability wording; `test_public_estimate_reproduces_the_worked_bill_and_never_quotes` ($2,053.00). Operator `Commercial` area in `apps/operator`: price books (list, lines, draft, line edit, dual-control approve), plans and accounts, quotes and orders, billing runs, suppliers, assurance exceptions and reports, all rendered from one action catalogue (`actions.ts`, 7 tests) so each route has one description of its body. |
| J-9 Revenue assurance | Built (audit pack signed with the evidence key) | `test_assurance_rules.py` (the §22 run-rate illustration, balanced journals, change-log chain), `test_assurance_pg.py` (resumable close, detectors, journals, deterministic audit pack, change-log integrity, MRR excluding intercompany, platform detectors) |
| J-10 Commercial acceptance suite | Built as traceability | `acceptance-tests.csv` rows COM-01 to COM-14, all linked to tests. COM-02 and COM-03 are `test_commercial_acceptance_pg.py`: a merged person counts once as MAH (migration `commercial_0009`, `record_identity_merge`), a duplicate that predates the merge is reported for an approved adjustment and never erased, and a region never changes the price or puts identity attributes in the ledger |

## What this phase does not do

- It does not publish prices or promise SLAs. Rates ship as a **DRAFT** price book that Finance must approve (gate C1) before any customer sees it.
- Payment and tax go through Stripe (`StripePayments`, `StripeTax`) behind the same ports, enabled only when `ZID_STRIPE_SECRET_KEY_FILE` names a key file; without it tax answers "not configured" and a deployed cell refuses to collect. There is no live ERP integration: the journal is exported as CSV or JSON for the finance team to import and is never posted. Tax is a line-level code and amount supplied by the port, never computed in the platform.
- No supplier pricing: IDV, KYB, risk and messaging are SKUs with a `vendor_matrix` basis and no rate until a vendor cost row exists.
- Legal documents (MSA, DPA, SLA, AUP), the jurisdiction matrix, merchant-of-record and revenue-recognition treatment are people decisions (gates C3, C4).

## Decisions I need from you before J-1 is built

| # | Question | Recommendation |
|---|---|---|
| 1 | Where do catalogue rows live? | The cell database, in a `commercial` schema, not tenant-scoped, writable only by a commercial-operator action. One VM and one cell today; a global database can take them later without an API change. |
| 2 | Is billing in scope for the demo? | Build J-1 to J-4 and J-8 for the demo; defer J-5, J-7 and J-9 until pricing is approved. |
| 3 | Currency | USD only at first, with the currency column present everywhere (§7 localisation is quarterly price cards, not live FX). |
| 4 | Plan for existing tenants | Every current tenant gets the **Developer** plan on migration, with no behaviour change, so nothing breaks. |
