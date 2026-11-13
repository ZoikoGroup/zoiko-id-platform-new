# ZID-COM-WF-2026-11 v3.0 (Commercial Architecture and Engineering Wireframes) reconciled against the repository

Written 2026-11-13 from the code, for work package CW-01 ("reconcile commercial as-built"). It decides nothing. The document is an implementation design for commercial, legal and finance approval; its own final rule is: do not authorize live commercial collection, vendor-mediated checks or new regulated service marketing until country and provider rights, a signed catalogue and all applicable G0 to G6 approvals exist. Nothing here changes that.

## Work packages

| Package | Priority | State | Where | What is left |
|---|---|---|---|---|
| CW-01 reconcile as-built | P0 | This file | `docs/phases/phase-j-commercial-estate.md`, CR-030 to CR-062 | Owners sign the as-built (gate G1) |
| CW-02 sixteen-family catalogue | P0 | Structure built, PROPOSED, unpriced | `config/commercial/service-families.yaml`, `domain/service_families.py`, `GET /v1/catalog/families` | Product approves family names, units and launch SKUs |
| CW-03 price and market rules | P0 | Decision rule built; facts now come from the ACTIVE market matrix (none active yet) | `domain/market_availability.py`, `domain/market_matrix.py`; `POST /v1/commercial/configurations:check` | Signed seller entity, merchant route, per-country price books, FX, tax class, data region: CFO and Legal |
| CW-04 metering and cost source | P0 | Built: idempotent ledger, vendor-cost ledger, reservations, chargeability table, and the usage-event contract's extra fields (bill-to payer, origin and partner, transaction type, billable result, finality, market policy, supplier rate version; commercial_0028, nullable, outside `row_hash`) | `domain/billable.py`, `domain/vendor.py`, `domain/chargeability.py` | Owners rule the recording point (`bill_on_commit` stays off) and which callers fill the new fields |
| CW-05 quote and commitment approvals | P0 | Built | `domain/orders.py` (digest, bands, stacked promotions, floor), `domain/commitment.py` (exposure, tiers, two people) | Ratify the proposed thresholds |
| CW-06 B02, B05, B06 customer UX | P0 | B02 and B05 built; B06 built as the Configure a solution tab (purpose, services, coverage, volumes, review; no price; saved drafts; quote request) | `/admin/billing`, `solution-builder*.ts(x)`, `GET /v1/commercial/offers`, `POST /v1/commercial/quotes/preview`, `POST /v1/commercial/configurations`, `GET /v1/commercial/capability-dependencies` | Design review, real-device screen-reader run, and a deal-desk process that answers the requests |
| CW-07 F06 and F02 operator UX | P0 | F02 and the price-book editor built; F06 built as the country-matrix release (four independent roles, immutable once active) with a Market release view and console actions | `apps/operator/.../commercial` (`market-views.tsx`, `actions.ts`) | A versioned editor for family names and units is deliberately not built: that text lives in the reviewed `service-families.yaml`, where code review is the maker-checker |
| CW-08 O01 release eligibility | P0 | Built to the limit of what engineering can record: matrix rows with evidence references and expiry dates, a staged rollout log (sandbox to production, pause, rollback), and the market gate a platform service asks before a paid call (`POST /v1/commercial/market-gate:check`, scope `usage:write`; shadow reports, enforce blocks; the company mode can be stricter than the deployment) | `domain/market_matrix.py`, `domain/rollout.py`, `application/market_gate.py` | Real signed evidence in the rows; the platform's own services must be changed to call the gate (no live billed call path exists in the code yet: the provider route is a dry run); SKU-level tie waits for launch SKUs |
| CW-09 finance truth | P0 | Built to a file-only ERP export | refunds, disputes, period close, reconciliation, journal CSV | Live ERP/GL, Stripe Tax activation, bank feeds |
| CW-10 independent acceptance suite | P0 | CAT-01 to CAT-40 rows: all 40 linked to a real test and marked IN_PROGRESS | `docs/traceability/acceptance-tests.csv` | Independent execution with evidence (gate G5) |
| CW-11 procurement extensions | P1 | Purchase orders built: recorded by the operator plane, invoices of a company that has POs are issued only against one that fits (currency, validity, value left), drawn down in an append-only row; the invoice document and hash chain are untouched | `domain/purchase_order.py`, `purchase_order_store.py`, commercial_0033 | Invoice terms beyond the existing payment-terms days, localized documents, credit-note effect on a PO draw |
| CW-12 channel and partner allocation | P1 | Built: partner profile with a reference to its signed agreement, territory and bill-to; permitted children with downstream-consent reference, one live partner per child; a gate before every partner call; usage reconciliation; settlement proposals needing a signed arrangement and a second person (pays nothing) | `domain/partner.py`, `partner_store.py`, `partner_service.py`, commercial_0032 | A signed reseller agreement, margins and settlement terms (owner decisions); partner API keys are not issued by this module |
| CW-13 forecasting and profitability | P1 | Built: margin by SKU and currency (inferred costs flagged), run-rate, revenue retention, and totals in US dollars at the active FX rate set (a named source, activated by a second person, no default rates; a currency it does not list is named, not converted) | `domain/profitability.py`, `domain/fx.py`, `GET /v1/commercial/profitability`, `fx-rate-sets` | The owners supply the FX rates and source; expansion forecasts and an owner-approved cost model |

## Invariants

| ID | State | Note |
|---|---|---|
| INV-01 no paid capability without signed effective rights, price and authorization | Wired into the configuration check, the offers, the quote request and the market gate (matrix, rights expiry, rollout stage) | the gate blocks only in enforce mode and only for services that call it |
| INV-02 purchase never implies identity or data access | Built | no billing state restricts a security path (`test_no_state_ever_restricts_a_security_path`) |
| INV-03 one operation, one canonical billed event | Built | dedupe keys; `chargeability.chargeable_events` |
| INV-04 an invoice needs seller, payer, currency, tax class, charge basis | Built | an issued invoice pins the issuing entity (seller), the payer tenant, the currency, the per-line tax code and charge basis, and now the merchant route and market matrix version it was issued under (only when the active matrix has exactly one route for that seller, country and currency; otherwise empty, never a guess; commercial_0036) |
| INV-05 no self-approval, stale quote hash, expired delegation | Built | approvals bind digest and people; a payer change voids the request |
| INV-06 versioned prices, immutable history | Built | |
| INV-07 every adjustment links source, approval, accounting | Built | |
| INV-08 no racial profiling | Built | name matching never treats an unreadable script as a mismatch |
| INV-09 partner path never bypasses consent | Built | `partner_service.authorise` is an added gate; consent, country and term are checked before a partner operation is recorded (`test_cat35_...`) |
| INV-10 payment, invoice, settlement, contract, accounting stay distinct | Built | match states keep each leg separate |
| INV-11 unsupported regulated status is never advertised or ordered | Partial | regulated families need proven status in the rule; the regulatory claim gates are in the verification module |
| INV-12 sandbox events never become billable | Rule built, recording off | `chargeability.decide` refuses anything not LIVE |

## Acceptance tests CAT-01 to CAT-40

All 40 are linked to a real test and marked IN_PROGRESS, which in this file means "evidence exists, not independently executed". The last seven links (CAT-07, 26, 30, 34, 35, 36, 40) were added with the partner channel, the rollout log and `test_commercial_cat_gaps*.py`. Several links are partial evidence for a wider claim (for example CAT-13 proves replay is refused, not that out-of-order events are safe, and CAT-36 proves the billing records have no input that can change a verdict, not that no other part of the platform can); read the test before relying on a row.

## Binding decisions the document reserves for the owners (section 16.1)

Currency-by-country price books; launch SKUs and packaged tiers; provider pass-through and chargeable-failure rules; the proposed authority thresholds; coverage and regulatory permissions; reseller margins and settlement; trials, credits and renewal; data-residency premium. Engineering has built the machinery and defaults; none of these values has been guessed.

## Gates G0 to G6

G0 authority, G1 as-built, G2 scope and catalogue, G3 identity and privacy, G4 money execution, G5 QA and UX, G6 production. Each needs named people (Founder, CFO, CCO, Legal, CTO, Product, Security, Privacy, Independent QA, Controller, SRE). None can be granted by engineering.
