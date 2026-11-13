# Commercial architecture v3.0: what the owners must decide and where each decision is recorded

Prepared 2026-11-13 for the Founder, CFO, CCO, Legal, Product, Security, Privacy and the Controller (ZID-COM-WF-2026-11, sections 16.1 and 16). It is a decision record. It approves nothing, and nothing in the repository holds a value for any item below unless an owner has entered it.

The document's own rule governs everything here: do not authorize live commercial collection, vendor-mediated checks or new regulated service marketing until country and provider rights, a signed catalogue and the applicable G0 to G6 approvals exist.

## How the system behaves until the owners decide

| Until this exists | The system does |
|---|---|
| An ACTIVE market matrix | Every service answers REQUEST_QUOTE. No price, no availability claim. |
| A rollout to CANARY or PRODUCTION for a market | That market is "designed, not released": not sellable. |
| A signed price book | No price is shown anywhere. The builder estimate is empty. |
| An active FX rate set | Reports total in dollars only; other currencies are named, never converted. |
| Founder and CFO signatures on the finance policy | The finance instruments refuse to open in a deployed environment. |
| Enforcement switched on | The market gate and the verification observer record what they would have refused, and block nothing. |

## Decisions the document reserves (section 16.1)

| # | Decision | Owner | Needs from the owner | Where it is recorded | What engineering has already built |
|---|---|---|---|---|---|
| 1 | Currency-by-country price books | CFO, Legal | Signed rates per currency and country, with the seller entity and effective dates | Operator console, Commercial, Catalogue: price book actions (draft, lines, approve) | Versioned price books, a second approver, immutable history, no default price |
| 2 | Launch SKUs and packaged tiers | Product, CFO | The published names, units and tiers, each approved | Family wording: Commercial, Market release, "Propose new wording" (wording only). SKUs: the price book | Sixteen proposed families, maker-checker wording, a guard against prices and status claims in labels |
| 3 | Provider pass-through and chargeable-failure rules | Finance, Legal | Per provider and service: what is passed through, what a failed or retried check costs the customer | Vendor rates and routes (vendor actions); chargeability policy file | Chargeability table with cautious defaults, retry and failure classification, supplier cost lineage |
| 4 | Authority thresholds | Founder, CFO, CCO | Ratify or change the proposed bands | `backend/config/commercial/finance-policy.yaml`, then the Founder and the CFO sign its hash | Bands, tiers, two-person rules and the signature gate; unsigned means refused |
| 5 | Coverage and regulatory permissions | Legal, Compliance | Per country and trust service: the authoritative right, its reference and its expiry | Market matrix rows: `provider_rights_ref`, `regulated_status_ref` and their expiry dates | Rows, expiry denial, rollout stages, the four-signature release |
| 6 | Reseller margins and settlement | Partnerships, Finance | A signed channel agreement, territory, who is billed, and any settlement arrangement | Partner records: agreement and settlement references; settlement proposals | Partner gate, one live channel per child, reconciliation, settlement entries that pay nothing and need a second person |
| 7 | Trials, credits and renewal | Product, CFO, CCO | Duration, entitlements and what happens at the end | Finance policy (pilot instrument); order terms | Pilots are finite, non-cash, never converted automatically (CAT-26) |
| 8 | Data-residency premium or sovereignty | Product, Legal | An approved deployment, cost model and legal scope before anything called sovereign is sold | Market matrix `data_region` per row | Region is judged per market; a region with no row is refused |

## Filling the market matrix

A row says: for this family, in this country, who sells it, through which merchant route, in which currency and tax class, in which data region, on which signed evidence. A row is a reference to the evidence, not the evidence.

1. Create a draft matrix (Commercial, Catalogue, "New draft market matrix"). Copy the previous one if there is one.
2. Add rows one at a time ("Add or change a row") or paste a JSON list of up to 500 ("Import many rows"). The import is all or nothing and lists every problem by row.
3. Each row needs: family, country, seller entity, merchant route, currency, tax class, data region, the signed offer reference, the legal basis reference, the release evidence reference, whether payment and tax are configured.
4. A family that needs a provider also needs `provider_rights_ref` and its expiry date. A regulated family also needs `regulated_status_ref` and its expiry date. An expired date stops the sale.
5. Four different people sign, each for one role: PRODUCT, FINANCE, LEGAL_PRIVACY, SECURITY_QA. The author signs none of them and one person signs one role.
6. Activate (not the author). The matrix is then immutable; the next one supersedes it.
7. Take each market through its rollout: SANDBOX, STAGING, PILOT, CANARY, PRODUCTION, one stage at a time, each move by a different person. Only CANARY and PRODUCTION are sold. Pause or roll back at any time.

Example row (illustrative references only):

```json
{"family": "API", "country": "DE", "seller_entity": "Zoiko Tech GmbH", "merchant_route": "stripe-eu", "currency": "EUR",
 "tax_class": "B2B-SERVICES", "data_region": "eu-central", "offer_ref": "offer:2026-11/api-de",
 "legal_basis_ref": "legal:memo-114", "release_evidence_ref": "release:G6-2026-11-14",
 "payment_configured": true, "tax_configured": true}
```

## FX for reports

Create a draft FX rate set with a named source and an as-of date, enter each currency's US dollars per unit from that source, and have a second person activate it. Without it, profitability is reported per currency and nothing is added across currencies.

## The enforcement decision

The market gate (`POST /v1/commercial/market-gate:check`) and the verification observer already exist. Enforcement is switched on per deployment, or per company by the operator. Decide first: which operations are paid, where each gets its subject country, and that an ACTIVE matrix with rolled-out rows exists for every market you intend to serve. Turning enforcement on earlier stops operations that have no matrix row.

## Release gates

| Gate | Evidence in the repository | Accountable people |
|---|---|---|
| G0 authority | Finance policy file and its signatures; discount policy | Founder, CFO, CCO, Legal |
| G1 as-built | `docs/qa/commercial-v3-reconciliation.md`, route inventory, database ownership registry | CTO, Product |
| G2 scope and catalogue | Active matrix with rows, price books, family wording | Product, Commercial, Legal, CFO |
| G3 identity and privacy | Privacy and biometric packs, DPIA records (owners), no-profiling tests | Security, Privacy, Legal |
| G4 money execution | Reconciliation, credit, refund and chargeback tests; finance close packet | CFO, Controller, QA |
| G5 QA and UX | `docs/qa/commercial-v3-cat-execution-kit.md` executed, accessibility on real devices | Independent QA, Design, CTO |
| G6 production | A signed activation manifest, canary and rollback evidence, customer claims and contracts aligned | Founder, CTO, CFO, SRE |

Engineering cannot grant any gate. Each is granted by the named people on evidence they review.
