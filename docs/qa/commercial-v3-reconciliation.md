# ZID-COM-WF-2026-11 v3.0 (Commercial Architecture and Engineering Wireframes) reconciled against the repository

Written 2026-11-13 from the code, for work package CW-01 ("reconcile commercial as-built"). It decides nothing. The document is an implementation design for commercial, legal and finance approval; its own final rule is: do not authorize live commercial collection, vendor-mediated checks or new regulated service marketing until country and provider rights, a signed catalogue and all applicable G0 to G6 approvals exist. Nothing here changes that.

## Work packages

| Package | Priority | State | Where | What is left |
|---|---|---|---|---|
| CW-01 reconcile as-built | P0 | This file | `docs/phases/phase-j-commercial-estate.md`, CR-030 to CR-062 | Owners sign the as-built (gate G1) |
| CW-02 sixteen-family catalogue | P0 | Structure built, PROPOSED, unpriced | `config/commercial/service-families.yaml`, `domain/service_families.py`, `GET /v1/catalog/families` | Product approves family names, units and launch SKUs |
| CW-03 price and market rules | P0 | Decision rule built; facts now come from the ACTIVE market matrix (none active yet) | `domain/market_availability.py`, `domain/market_matrix.py`; `POST /v1/commercial/configurations:check` | Signed seller entity, merchant route, per-country price books, FX, tax class, data region: CFO and Legal |
| CW-04 metering and cost source | P0 | Idempotent ledger, vendor-cost ledger, reservations built; chargeability table built | `domain/billable.py`, `domain/vendor.py`, `domain/chargeability.py` | The usage-event contract's extra fields (bill-to payer, origin or partner, billable result, finality) and recording verification checks: owners rule the recording point (`bill_on_commit` stays off) |
| CW-05 quote and commitment approvals | P0 | Built | `domain/orders.py` (digest, bands, stacked promotions, floor), `domain/commitment.py` (exposure, tiers, two people) | Ratify the proposed thresholds |
| CW-06 B02, B05, B06 customer UX | P0 | B02 and B05 built; B06 backend check built, no panel | `/admin/billing`, `POST /v1/commercial/configurations:check` | The B06 guided right-side panel (frontend) |
| CW-07 F06 and F02 operator UX | P0 | F02 (finance cases, approvals) and a price-book editor built | `apps/operator/.../commercial` | Country-matrix release editor BUILT (CR-070 area: `commercial.market_matrix*`, four independent roles, immutable once active, operator console actions); a versioned catalogue editor for family text is not |
| CW-08 O01 release eligibility | P0 | Partial | `activation_service`, the provider and market-release gates from `akarsh` | Matrix rows (family x country x seller x merchant route x tax class x region x evidence references) are built and feed the check; a row is a reference, so owners must fill it with real signed evidence; SKU-level tie still waits for launch SKUs |
| CW-09 finance truth | P0 | Built to a file-only ERP export | refunds, disputes, period close, reconciliation, journal CSV | Live ERP/GL, Stripe Tax activation, bank feeds |
| CW-10 independent acceptance suite | P0 | CAT-01 to CAT-40 rows added: 33 linked to real tests, 7 honestly NOT_STARTED | `docs/traceability/acceptance-tests.csv` | Independent execution with evidence (gate G5) |
| CW-11 procurement extensions | P1 | Not built | | Purchase orders, invoice terms, localized documents |
| CW-12 channel and partner allocation | P1 | Not built | | Only if a signed reseller launch makes P01 P0 |
| CW-13 forecasting and profitability | P1 | Not built | | Provider COGS, run-rate, churn |

## Invariants

| ID | State | Note |
|---|---|---|
| INV-01 no paid capability without signed effective rights, price and authorization | Wired into the configuration check through the active matrix; not yet into quote creation | `market_availability.decide` denies on any unrecorded fact; wiring it into quote and activation waits for the facts |
| INV-02 purchase never implies identity or data access | Built | no billing state restricts a security path (`test_no_state_ever_restricts_a_security_path`) |
| INV-03 one operation, one canonical billed event | Built | dedupe keys; `chargeability.chargeable_events` |
| INV-04 an invoice needs seller, payer, currency, tax class, charge basis | Partial | no seller-entity or tax-class model yet |
| INV-05 no self-approval, stale quote hash, expired delegation | Built | approvals bind digest and people; a payer change voids the request |
| INV-06 versioned prices, immutable history | Built | |
| INV-07 every adjustment links source, approval, accounting | Built | |
| INV-08 no racial profiling | Built | name matching never treats an unreadable script as a mismatch |
| INV-09 partner path never bypasses consent | Not built | P01 |
| INV-10 payment, invoice, settlement, contract, accounting stay distinct | Built | match states keep each leg separate |
| INV-11 unsupported regulated status is never advertised or ordered | Partial | regulated families need proven status in the rule; the regulatory claim gates are in the verification module |
| INV-12 sandbox events never become billable | Rule built, recording off | `chargeability.decide` refuses anything not LIVE |

## Acceptance tests CAT-01 to CAT-40

33 are linked to a real test and marked IN_PROGRESS, which in this file means "evidence exists, not independently executed". Seven are NOT_STARTED because no test exists: CAT-07 (zero-decimal currency), CAT-26 (pilot expiry and no automatic conversion), CAT-30 (cross-payer tampering), CAT-34 and CAT-35 (partners), CAT-36 (a paid denied outcome), CAT-40 (rollout and rollback). Several links are partial evidence for a wider claim (for example CAT-13 proves replay is refused, not that out-of-order events are safe); read the test before relying on a row.

## Binding decisions the document reserves for the owners (section 16.1)

Currency-by-country price books; launch SKUs and packaged tiers; provider pass-through and chargeable-failure rules; the proposed authority thresholds; coverage and regulatory permissions; reseller margins and settlement; trials, credits and renewal; data-residency premium. Engineering has built the machinery and defaults; none of these values has been guessed.

## Gates G0 to G6

G0 authority, G1 as-built, G2 scope and catalogue, G3 identity and privacy, G4 money execution, G5 QA and UX, G6 production. Each needs named people (Founder, CFO, CCO, Legal, CTO, Product, Security, Privacy, Independent QA, Controller, SRE). None can be granted by engineering.
