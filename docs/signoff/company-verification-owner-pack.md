# Owner pack: ZID-ED-03 company verification

A checklist for the owners. It approves nothing, and every reference below is blank on purpose. Source: ZID-ED-03 sections 03, 07 and 08 (proposed, awaiting founder approval). Slice: [L-3](../phases/phase-l-second-wireframe-decisions.md); record of what engineering built: [CR-043](../specs/change-requests/CR-043-phase-l-company-verification.md).

A supplier counts toward a verification only for a country listed in `supplier_coverage_proven` in `backend/config/kyb/kyb-routing.yaml`. That list is empty. Adding a country there is how an owner records that the gates below passed; until then the country answers UNAVAILABLE.

## 1. Procurement gates (ED-03 section 07), per supplier

Creditsafe is the preferred supplier; D&B Direct+ is the qualified alternative; OpenCorporates is optional and only with commercial redistribution rights.

| Gate | What ED-03 asks for | Owner | Creditsafe | D&B | OpenCorporates |
|---|---|---|---|---|---|
| Coverage test | Entity accuracy, director and ownership field availability, incorporation states and sparse records in all five wave 1 markets (GB, US, IE, DE, FR) and a sample of wave 2 | Product, Trust Ops | ______ | ______ | ______ |
| Written data rights | SaaS embedding, customer-visible outcomes, permitted caching, evidence retention, sublicensing or resale, geographic processing, data export | Legal | ______ | ______ | ______ |
| Data terms | Data processing agreement and subprocessor terms | Privacy, Legal | ______ | ______ | ______ |
| Committed pricing | Price by country, per lookup and refresh, minimum spend, monitoring, bulk usage, failed checks, overages; a hard budget alert | Finance, Commercial | ______ | ______ | ______ |
| Service terms | SLAs, incident notices, accuracy corrections, audit evidence, sandbox access, exit and export | Procurement | ______ | ______ | ______ |
| Residency | Where the supplier processes and stores, against the tenant's home region | Security, Privacy | ______ | ______ | ______ |
| Fraud and support | Fraud controls and the support playbook for failed checks | Trust Ops | ______ | ______ | ______ |

## 2. Per-country sign-off before a supplier is relied on

Record the reference, then add the supplier to `supplier_coverage_proven.<country>`.

| Country | Official register route in code | Supplier needed | Coverage proof reference |
|---|---|---|---|
| GB | Companies House (key needed) | Exception only | n/a |
| FR | Annuaire des Entreprises (open, keyless; terms to be confirmed by Legal) | Exception only | n/a |
| US | None (each state is its own register) | Yes, per state | ______ |
| IE | None (the CRO Open Services need a registered account and signed terms) | Yes | ______ |
| DE | None (the Handelsregister portal has no API) | Yes | ______ |

A national adapter is added when an owner obtains a lawful, licensed route; the Irish CRO and the German register are the two to ask about.

## 3. Facts only the owners can supply

- **Creditsafe account:** a contract, a sandbox login and a production key. The adapter's authentication, search path, parameters and `Bearer` scheme match the published documentation; the field names inside each returned company have never been seen and must be checked in the sandbox before the supplier is enabled.
- **Evidence validity:** the proposal is 365 days (`policy.validity_days`), from ED-04's annual refresh. ED-03 gives no number.
- **Identifier formats:** GB, FR (SIREN with its check digit) and IE (digits, any length) follow the registers' published rules. DE needs the register court with the number and the US needs the state register; the registry owners confirm the exact court and state spellings accepted.
- **Cost per completed verification (ED-03 section 08):** every supplier lookup is recorded as a `KYB_LOOKUP` vendor cost, chargeable or not; an outage is never chargeable. Finance reads it from the vendor-cost ledger once a signed tariff is entered for the supplier.

## 4. What engineering has proved

| Acceptance test | Evidence |
|---|---|
| EDR03-01 fixtures: exact match, close name, changed name, inactive, absent, no LEI, other entity type, in GB, US, IE, DE, FR | `backend/app/tests/unit/test_kyb.py` |
| EDR03-02 no verified status from a name, a claim, missing data, an expired check or a timeout | `backend/app/tests/unit/test_kyb.py` |
| EDR03-03 every decision has source, jurisdiction, time, policy, reason, actor and an immutable trace; reviewer overrides record a rationale | `backend/app/tests/component/test_kyb_pg.py` |
| EDR03-04 region isolation under cache, retry, webhook and outage | `test_kyb_pg.py`: no cache or global state, tenant isolation, concurrent retries, outage appended not overwritten. No inbound webhook exists for company verification |
| WF13B-AT-08 to AT-12: a single founder claims provisionally and cannot self-approve; another organization learns nothing and reaches nothing; replays and concurrent requests make one decision; a dissolved company drops out of every view and the history stays; proof uploads are judged, scanned, region-checked and encrypted | `backend/app/tests/component/test_org_claims_trust_pg.py`, `test_org_claim_proofs_pg.py`, `test_kyb_refresh_pg.py`, `backend/app/tests/unit/test_kyb_proof.py` |
| EDR03-05 cost per completed verification recorded | `test_kyb_pg.py::test_supplier_lookups_are_costed_and_an_outage_is_never_chargeable`; the sign-off itself is Finance's |

## 5. Decisions for the owners about proof documents and refresh

| Decision | Owner | Recorded |
|---|---|---|
| The malware scanner: product, hosting and where it runs (it must sit in the same data region as the files it scans) | Security | ______ |
| The size limit (10 MB proposed) and the accepted types (PDF, PNG, JPEG proposed) | Security / Legal | ______ |
| How long a proof is kept, and who may open one (a reviewer with the protected action, every read audited, proposed) | Privacy / Legal | ______ |
| Whether an Operator on an assigned case may upload (the matrix says assigned case only; the route admits Owner and Admin) | Product | ______ |
| The refresh lead time (30 days before a verification lapses, proposed): set `refresh_lead_days` in `config/kyb/kyb-routing.yaml` | Trust operations | ______ |
| The proof retention period: set `retention_days` in `config/kyb/proof-policy.yaml` (unset keeps documents until a period is decided) | Privacy / Legal | ______ |
| Where the scanner runs and its address: set `ZID_PROOF_SCANNER_ADDRESS` (a cell without it refuses uploads) | Security / SRE | ______ |
