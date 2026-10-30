# Platform configuration

Versioned, reviewed configuration that services load at runtime. **Not** tenant data (tenant policies, mappings and grants live in the database).

| Folder | Contents |
|---|---|
| `jurisdiction-packs/` | Counsel-approved jurisdiction packs (DEC-F3). Versioned; Legal/DPO approve every change. |
| `crypto-profiles/` | Crypto Policy Registry profiles: algorithm allowlists, key purposes, PQ mode (DEC-F6, DEC-F5). Security approves. |
| `residency-profiles/` | SRP-0..SRP-3 residency/sovereignty profiles and data-class placement rules (TENANCY-001 §4-6). |
| `cells/` | Cell catalog: cell IDs, jurisdiction metadata, approved recovery pairs, capability flags (DEC-G1). |
| `cells/cell-readiness.yaml` | Home-cell readiness gate: five recorded gates per cell before the Regional offer is sold there (EDR-002 §8). |
| `residency-offers/` | The four residency offers (Shared Global, Regional, Sovereign, Dedicated) over SRP-0..SRP-3, the customer wording and the claims never to make (EDR-002). |
| `country-capabilities/` | Country × capability availability, default disabled, six go/no-go approvals per country (EDR-001). |
| `kyb/` | Company-verification routing and policy: adapters, per-country routes, supplier coverage proven per country, evidence validity (ED-03). |
| `entitlements/` | The capability bands C1 to C5 (40 features, generated from the record's tables), the band-to-plan mapping, the REPORT_ONLY or ENFORCE switch, and the eight-meter register (EDR-008). |
| `step-up/` | The step-up tiers S0 to S2 (freshness, single use, forbidden methods), the 24 actions that need them (generated from the record's two trigger tables) and the risk signals that raise a tier (EDR-009). |
| `roles/` | The role by feature permission matrices A to F, generated from the decision record's tables and validated on load (EDR-007). |
| `screening/` | Screening policy and country packs: required sanctions lists, list age limit, match thresholds, per-country ownership rules and list sources (ED-04). |

Every file carries a version and approval reference. Releases are immutable; a change is a new version.
