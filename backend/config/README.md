# Platform configuration

Versioned, reviewed configuration that services load at runtime. **Not** tenant data (tenant policies, mappings and grants live in the database).

| Folder | Contents |
|---|---|
| `jurisdiction-packs/` | Counsel-approved jurisdiction packs (ADR-0007). Versioned; Legal/DPO approve every change. |
| `crypto-profiles/` | Crypto Policy Registry profiles: algorithm allowlists, key purposes, PQ mode (ADR-0009, ADR-0026). Security approves. |
| `residency-profiles/` | SRP-0..SRP-3 residency/sovereignty profiles and data-class placement rules (TENANCY-001 §4-6). |
| `cells/` | Cell catalog: cell IDs, jurisdiction metadata, approved recovery pairs, capability flags (ADR-0011). |

Every file carries a version and approval reference. Releases are immutable; a change is a new version.
