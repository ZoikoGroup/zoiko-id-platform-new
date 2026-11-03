# Phase L, slices L-1 to L-3: what only the owners and suppliers can close

Engineering has built, tested and committed everything in L-1 (country capability registry), L-2 (regional residency packaging) and L-3 (company verification) that code can deliver. What is left is a decision, a contract, a reference or a real piece of infrastructure. None of it can be filled in by engineering without inventing it, so this page lists each item once, says who records it, **where it goes in the repository** and **what engineering does the moment it is recorded**. Every acceptance test stays IN_PROGRESS until an owner accepts it in `docs/traceability/acceptance-tests.csv`.

How to use this page: an owner records the reference in the file named, and tells engineering. The build refuses any state that claims more than the references support (for example an ENABLED country without its six approvals, or a READY cell without its gates and suppliers), so a half-filled entry fails loudly rather than going live.

## L-1 Country capability registry (ZID-EDR-001)

| Item | Owner | Where it is recorded | Engineering follows with |
|---|---|---|---|
| Six go/no-go approvals per country and capability (`legal_assessment`, `supplier_coverage`, `privacy_and_data`, `security_and_resilience`, `local_support`, `signoff`) with `effective_from` | Legal, Product/Trust, Privacy, Security/SRE, Support, Founder | `backend/config/country-capabilities/country-capabilities.yaml` (checklist: [country-capability-owner-pack.md](country-capability-owner-pack.md)) | Nothing for the code: the gates already enforce it. Link the evidence row and rerun the registry tests |
| Live provider per country (named provider, signed contract, keys, countries covered) | Product/Trust, Legal | The same file (`provider`, `legal_status`); keys through the secret broker, never the repository; see [identity-suppliers-owner-pack.md](identity-suppliers-owner-pack.md) | Wire the provider's adapter behind the existing interface; supplier-specific field names are verified against its sandbox first |
| EU and U.S. sign-up rows | Legal/Privacy, after the cell passes its gate and REGISTRATION is approved | `backend/config/registration/country-routing.yaml` (the commented rows) | Uncomment the row, run the registration tests for that country |
| Contact route shown to people in a closed country | Support | `contact` in the registry or the default in `country-routing.yaml` | None |
| Real-device checks of local address, phone and time-zone entry | Product/QA | Not applicable yet: the sign-up screen collects only an email. Do this when the screens that collect them exist | Fix what the checks find |

## L-2 Regional residency packaging (ZID-EDR-002)

| Item | Owner | Where it is recorded | Engineering follows with |
|---|---|---|---|
| Five readiness gates per cell (`architecture`, `security`, `privacy_and_law`, `operations`, `commercial`), each with a recorded reference | Architecture, Security, Legal/Privacy, Operations, Commercial | `backend/config/cells/cell-readiness.yaml` (`gates`) (checklist: [regional-residency-owner-pack.md](regional-residency-owner-pack.md)) | None; the build refuses READY without them |
| Supplier declarations per READY cell (country coverage, data access, storage locations, onward transfers, subcontractors) | Privacy/Legal with each supplier | The same file (`suppliers`) | None; storage outside the cell's legal zone fails the build |
| Confirmed U.S. regions (`us-1`, `us-1-dr` are proposed) | SRE | `backend/config/cells/cells.yaml` | Update the catalogue and the placement tests |
| An approved U.K. recovery cell | SRE, Legal | `approved_recovery_cells` for `uk-1` in `cells.yaml` | Add the cell and extend the Wave 1 matrix test (it currently proves the U.K. cell holds its segment) |
| Region-accurate legal papers (DPA, subprocessor disclosure, location schedule, support-access scope, retention schedule, recovery boundary, transfer assessments) | Legal/Privacy | Outside the repository; record the reference in the `privacy_and_law` gate | None |
| Real deployed cells and measured failover, recovery and deletion drills | SRE | Deployment, then the `operations` gate reference | Replace the sandbox status; rerun the drills against the real cells |

## L-3 Company verification (ZID-ED-03, WF-13B)

| Item | Owner | Where it is recorded | Engineering follows with |
|---|---|---|---|
| Creditsafe contract, sandbox and production key | Product/Trust, Finance | Secret broker (key name set with the adapter); `creditsafe` status in `backend/config/kyb/kyb-routing.yaml` | Verify the returned field names against the sandbox (never seen), correct the adapter, enable it |
| Coverage proof and licensing per country (the five-country proof) | Product/Trust, Legal | `supplier_coverage_proven` in `kyb-routing.yaml` | None; until then the U.S., Ireland and Germany answer UNAVAILABLE by design |
| A lawful, licensed route to the Irish CRO, the German register (with its register court) and each U.S. state register | Legal, Product/Trust | A named adapter once a route exists | Build the national adapter and its tests |
| Exact register-court and state spellings accepted | Registry owners | The identifier rules for DE and US | Encode them and add the cases |
| Evidence validity (365 days proposed) and refresh lead (30 days proposed) | Trust Operations | `validity_days` and `refresh_lead_days` in `kyb-routing.yaml` | None |
| Proof documents: scanner product and hosting, size limit, retention period | Security, Privacy/Legal | `ZID_PROOF_SCANNER_ADDRESS` (deploy), `max_bytes` and `retention_days` in `config/kyb/proof-policy.yaml` | Deploy the scanner address; nothing else |
| Finance sign-off on measured supplier cost per completed verification | Finance | Read from the vendor-cost ledger once a signed tariff is entered | None |
| **WF13B-AT-14:** signed contract, jurisdiction policy, reviewer runbook, audit event schema and data retention | Trust Operations, Legal, Privacy, Security | Drafts are ready for signature: [reviewer runbook](company-verification-reviewer-runbook.md), [audit schema and retention](company-verification-audit-and-retention.md), [owner pack](company-verification-owner-pack.md). The contract and the jurisdiction policy are Legal's documents | Link AT-14 as in progress once the signatures are recorded; engineering amends the drafts for any change the owners ask for |
