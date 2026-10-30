# Owner pack: ZID-EDR-005 identity-proofing suppliers

A checklist for the owners. It approves nothing, and every reference below is blank on purpose. Source: ZID-EDR-005 (proposed, awaiting founder approval). Slice: [L-5](../phases/phase-l-second-wireframe-decisions.md); what engineering built: [CR-043](../specs/change-requests/CR-043-phase-l-company-verification.md).

Until an adapter exists with its secrets in the secrets broker, no identity check can start: that is the truthful state, not a fault.

| # | Item | Why engineering cannot do it | Reference |
|---|---|---|---|
| 1 | Veriff contract, API key and shared secret, stored in the broker under the names in `provider_config` | Commercial and credential decision | |
| 2 | Veriff base address (the guides name `stationapi.veriff.com`) | Set per account; confirm before the first call | |
| 3 | Sumsub contract, app token, secret key and webhook secret in the broker; webhook address set in the Sumsub dashboard | Commercial and credential decision | |
| 4 | Sumsub confirmation: `X-Payload-Digest` and `X-Payload-Digest-Alg` header names, the applicant-data response (`info.firstName`, `lastName`, `dob`), and the fraud labels | Written from the published description, not run against an account | |
| 5 | The UK-certified Veriff SKU or flow: proof of the certified scope before any adapter sets `uk_certified` (GB only) | Regulatory claim; only the owner can evidence it | |
| 6 | Supplier region placement, DPA, SLA, incident escalation, deletion requests, price per completed and attempted check, minimums, no-training and subprocessor clauses (EDR05-04) | Legal and commercial | |
| 7 | Live or sandbox runs for the Wave 1 countries, covering local documents, OCR scripts, accessibility, language, retry, low light and poor connection (EDR05-01) | Needs supplier accounts and real devices | |
| 8 | Decision on polling reconciliation cadence and on RESUBMISSION_REQUIRED as its own session state | Design ruling | |
