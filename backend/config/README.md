# Platform configuration

Versioned, reviewed configuration that services load at runtime. **Not** tenant data (tenant policies, mappings and grants live in the database).

| Folder | Contents |
|---|---|
| `jurisdiction-packs/` | Counsel-approved jurisdiction packs (DEC-F3). Versioned; Legal/DPO approve every change. |
| `crypto-profiles/` | Crypto Policy Registry profiles: algorithm allowlists, key purposes, PQ mode (DEC-F6, DEC-F5). Security approves. |
| `residency-profiles/` | SRP-0..SRP-3 residency/sovereignty profiles and data-class placement rules (TENANCY-001 §4-6). |
| `cells/` | Cell catalog: cell IDs, jurisdiction metadata, approved recovery pairs, capability flags (DEC-G1). |

Every file carries a version and approval reference. Releases are immutable; a change is a new version.
