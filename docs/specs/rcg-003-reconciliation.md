# ZID-RCG-003 reconciliation (Step 0: Inspect)

Source: *Document 03 v2.0, Global Regulatory Certification, Qualified Trust Services & Provider Accountability* (control
ZID-RCG-003, 10 Nov 2026). The document's own rule (section 11.1 and delivery step 0): reconcile every work package to the
current repository first, mark equivalent verified work **REUSE** with a code pointer, and build only verified gaps.

**Status of this file.** A first pass from reading the country registry and keyword searches of the repository. It is not an
audit. A row marked "not found" means the search found nothing, not that a proof of absence exists. Nothing here claims a
feature is verified, certified or production-tested; the document's status vocabulary (VERIFIED_IN_CODE and so on) is not
granted by this file.

| Package | Deliverable (priority) | Reconciliation | Code pointer / note |
|---|---|---|---|
| RCG-G01 | Jurisdiction / profession applicability registry; signed policy lifecycle (P0) | **EXTEND** | Country and capability gating exists: `modules/verification/domain/countries.py`, `infrastructure/country_registry.py`, `config/country-capabilities/country-capabilities.yaml`, test `tests/unit/test_country_capabilities.py`. Missing: subdivision, profession, product, purpose; legal sources; versioned packs; approvals bound to a pack hash; maker is not approver. **Step 1 builds this** (`domain/policy_packs.py`). |
| RCG-G02 | Legal entity / exact service qualification and license registry (P0) | **BUILD** | Only a `UK_DVS_CERTIFIED_FLOW` marker in `modules/verification/domain/providers.py`. No register-backed service record found. |
| RCG-G03 | Partner entitlement and source authorization enforcement (P0) | **BUILD / EXTEND** | Provider adapter layer in `modules/verification`. No per-purpose, per-country entitlement record found. |
| RCG-G04 | Professional issuer and accreditation history registry (P0) | **BUILD** | `modules/credential` is a partial base. No issuer-at-award-time record found. |
| RCG-G05 | Practice restriction and insurance source checks (P0) | **BUILD** | Not found. |
| RCG-G06 | Document signature and qualified trust validator adapters (P0) | **BUILD** | Not found in code (one mention in docs only). |
| RCG-G07 | Global evidence manifest and regulated claims publisher (P0) | **EXTEND** | `modules/evidence` (ledger, receipts, bulk export); screening case replay route. Claims publisher and per-dimension manifest not found. |
| RCG-G08 | Exact-service kill switch, impact analysis, rollback (P0) | **BUILD** | Kill switch and quarantine exist for agents and workloads (`modules/machine`), not for provider service scope. |
| RCG-G09 | Country privacy, cross-border and biometric overlay (P0) | **EXTEND** | `config/jurisdiction-packs/` (eu-gdpr, uk-gdpr, us-ca-ccpa) and the residency code in `modules/tenant`. |
| RCG-G10 | Regulatory release workflow and independent conformance tests (P0) | **EXTEND** | `tools/scripts/go_live_board.py`, `tools/scripts/release_gate.py` (software supply chain only), approvals in `modules/tenant`. |
| RCG-G11 | EUDI wallet / attribute extensions (P1) | not assessed | Later. |
| RCG-G12 | Sector / transaction monitoring, crypto approvals (P2) | not assessed | Separately regulated extension; not part of the Zoikorum launch. |

## Not part of this file
- Zoikorum, Zoiko Rooms and England Right to Rent: no mention found anywhere in the repository (zero hits).
- The `/v2/regulatory/...` and `/v2/trust/...` routes: not checked.
- The 48 acceptance tests (T01 to T48): only those listed under Step 1 below are implemented.

## Step 1 progress (Define, G01)
Implemented, with no console screen yet:

- Domain (pure, no I/O): `modules/verification/domain/policy_packs.py`, tests `tests/unit/test_policy_packs.py`. Covers document
  tests T01, T02, T03, T04, T36 and T37 at the domain level.
- Storage: migration `verification_0005` (tables `verify.policy_pack` and `verify.policy_pack_approval`, registered in
  `database/table-ownership.yaml` as proposed, change request CR-067), store
  `modules/verification/infrastructure/policy_pack_store.py`, tests `tests/component/test_policy_packs_pg.py`. The database itself
  refuses changing a published version, deleting one, editing or deleting an approval, the author approving, one person holding two
  roles, an approval that signs another hash, and publishing without the four approvals of the current hash. A pack read back is
  re-hashed and refused if it no longer matches what was stored.
- The packs are platform configuration, not tenant data: no `tenant_id`, no row-level security. Runtime roles have no DELETE grant.
- Service `modules/verification/application/policy_packs.py` (draft, revise, sign, publish, retire, governing pack; tests
  `tests/component/test_policy_pack_service_pg.py`).
- Operator API `modules/operator_access/api/regulatory.py` under `/v1/regulatory/...`: six new operator capabilities (author, four approver
  roles, publish) and a separation-of-duties set so the maker is never a checker; every change is audited in the platform partition (a refused attempt too); tests `tests/component/test_regulatory_api_pg.py`.
- The gate: `country_gate.check_packs` and `enforce_packs`, rule `gate_decision`, switch `ZID_REGULATORY_PACKS` (shadow by default). The country registry
  stays the baseline; an ENABLED capability also needs an approved pack or it is refused before the provider is asked (T01, T02). Wired at the three
  entry points of individual verification; the other entry points are one call each once the product and purpose of their capability are agreed.
  The provider callback is held, not applied, for a missing pack as for a closed country (the provider retries); the web app explains both refusals.
  Not done: a pack-aware public country list (public, no tenant; the authenticated coverage read is its home) and the other entry points.
- Kept inside the existing `verification` module and migration stream. A separate `regulatory` module is still open and would be
  a move, not a rewrite.

Open human decisions the document reserves for the business (section 13.1) are not decided here: legal entity per offering,
named local legal reviewer and Privacy Officer per market, confirmed provider contracts, and the first markets and professions.

## Refresh (2026-11-13): reconciliation against the code as it stands

The table above predates most of the work. Re-read from the code, not from the earlier claims. Nothing here is VERIFIED_IN_CODE in the document's sense (that needs a deployed environment and an independent owner).

| Package | Now | Pointer | Still open |
|---|---|---|---|
| RCG-G01 policy packs | BUILT (gate in shadow by default) | `verification/domain/policy_packs.py`, `operator_access/api/regulatory.py`, `country_gate.py` | other entry points of the gate; approvals by named people |
| RCG-G02 legal entity and exact-service qualification | BUILT as a rule; no register connected | `verification/domain/certification.py` (`reconcile`, `RegisterEntry`, `SuspensionProof`), `config/certifications/certifications.yaml` (DTV-07/14/15), tests in `test_certification.py` | A fetcher for the real registers (needs access and a parser per register) and a persisted history of proofs. `reconcile` only ever downgrades: a record the register does not list, or lists as suspended or withdrawn, stops counting at once with a proof (source, snapshot hash, time); a fetch never reinstates |
| RCG-G03 partner entitlement | BUILT | `verify.provider_entitlement`, `verification/api/v2_providers.py`, `domain/provider_adapter.py` | real contracts |
| RCG-G04 issuer and accreditation history | BUILT (sample issuers) | `credential/domain/trust_graph.py` (GID-038) | real issuer data |
| RCG-G05 practice restriction and insurance | BUILT against simulator sources | `credential/domain/standing.py`, disciplinary route (GID-044/046) | real sources |
| RCG-G06 signature and qualified-trust validators | BUILT on open-source crypto | `evidence/application/{cms,trust,signatures,xades,cades}.py` | anchors, trusted lists and TSA roots (config ships empty) |
| RCG-G07 evidence manifest and claims publisher | BUILT | `evidence` module, `/v2/evidence/{ref}/manifest`, signed assertion | |
| RCG-G08 service kill switch, impact analysis, rollback | BUILT as of this refresh | suspend and revoke existed; `GET /v2/providers/{id}/entitlements/{service}/impact` and `provider_adapter.withdrawal_impact` added (LOST, DEGRADED, REROUTED per scope) | cert-withdrawal fixture against a real register |
| RCG-G09 privacy and cross-border overlay | BUILT | Document 04 control plane (`docs/qa/doc04-privacy-matrix.md`) | counsel content |
| RCG-G10 release workflow and conformance | BUILT as a platform | `assurance` module (CR-074) | independent runs: 0 of 104 cases |
| RCG-G11 EUDI | PARTLY | OpenID4VC/vc-jwt and Data Integrity verification | wallet conformance runs |
| RCG-G12 | not in scope for launch | | |


## Refresh after merging `origin/akarsh` (2026-11-13)

`origin/akarsh` (PR #55 and the commits after it) built the Document 03 packages as full modules: G02 official-register attestations (`verification/domain/attestation.py`, `certification_attestation`, CR-073), G03 partner entitlement statements (`verification/domain/entitlement`, table `verify.provider_entitlement`, CR-074), G08 exact-scope provider suspensions with rollback (`verification/domain/suspension.py`, CR-078), G10 market releases with the seven evidence gates and four independent approvals (`market_release.py`, CR-072), conformance runs, the `/v2` regulatory API, operator console screens and a readiness report. Treat the earlier refresh table above as superseded for G02, G03, G08 and G10; read those modules and their CRs for the current state.

Two things of mine overlap and were kept apart on purpose:
- `certification.reconcile` (a pure rule that downgrades a configured record the register does not support) is still in `certification.py` and does not conflict with the attestation records; whether to keep both is a design question for the owners.
- My tenant-scoped runtime routing registry (`/v2/providers/*`, `provider_adapter.withdrawal_impact`) used the table name `verify.provider_entitlement`, which `akarsh` also uses for a different, platform-level append-only table. Mine is now `verify.provider_route_entitlement` (migration `verification_0013`). The two serve different purposes (routing a request vs. recording what a contract allows) and could be joined later.
