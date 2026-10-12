# CR-005: Master-sequence reconciliation for duplicate document slots

- **Target:** Build Documentation Guideline §2; Docs 18, 19, 23 · **Status:** Open (resolves architecture open question Q2)

## Evidence from the specs
- **ZID-SEC-001 §33 "Master-Sequence Reconciliation":** *"The previously produced cryptography specification maps to Document 18 … should be treated as the basis for canonical remapping/review."* and *"The deployment/cell and observability documents will be reconciled against the original Documents 19 and 23."*
- **ZID-CRYPTO-MI-001 §24:** calls itself *"this canonical Document 18"*; title matches Guideline Doc 18 exactly; hands off to Doc 19.
- **ZID-CRYPTO-001 §34:** hands off to "Document 18 — Deployment Topology" — i.e. written under the earlier, drifted sequence.
- **ZID-SRE-001 §32:** hands off to Document 24 → it is Doc 23; title matches Guideline Doc 23 exactly.
- **ZID-RESILIENCE-001 §36** hands off to "Document 19 — Observability…", and **ZID-OPS-001 §35** hands off to "Remaining Engineering Specifications" — both from the drifted sequence.

## Proposed change (record in the Guideline and the spec register)

| Slot | Canonical | Non-canonical | Treatment |
|---|---|---|---|
| 18 | ZID-CRYPTO-MI-001 | ZID-CRYPTO-001 | Superseded; retained for history only |
| 19 | ZID-TENANCY-001 | ZID-RESILIENCE-001 (topology/HA/DR) | Supplementary until its content is reconciled into Doc 19/23; canonical spec wins on conflict |
| 23 | ZID-SRE-001 | ZID-OPS-001 (incident response/readiness) | Supplementary until reconciled into Doc 23; canonical spec wins on conflict |

Generated registries (`contracts/error-codes`, `contracts/asyncapi/event-catalogue.yaml`, `docs/traceability/acceptance-tests.csv`) already apply this precedence and label each entry's `source_status`.
