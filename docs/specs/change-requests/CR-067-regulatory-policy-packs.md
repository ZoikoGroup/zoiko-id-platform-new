# CR-067: Jurisdiction and profession policy packs (ZID-RCG-003 RCG-G01)

Status: open (domain, storage, lifecycle service, operator API, audit and the individual-verification gate built and tested; no console yet; the owners confirm the points below).

## Why
Document 03 (ZID-RCG-003, v2.0) requires every regulated decision to be scoped to country, subdivision, product, purpose, profession and a
policy version, with no generic global pass. The country capability registry (ZID-EDR-001) decides by country and capability only, and its six
approvals are free-text references that are not tied to a version of the rules and do not stop the person who made a change from approving it.
A policy pack is the missing record: one immutable, versioned set of rules for one market, approved by four independent people against the exact
pack hash.

## What changes
- **Two tables** in the `verify` schema, owned by the `verification` module (migration `verification_0005`), registered in
  `backend/database/table-ownership.yaml` as proposed:
  - `verify.policy_pack`: one row per pack version. Market (country, optional subdivision and profession, product, purpose), status
    (`DRAFT`, `APPROVED`, `RETIRED`), legal sources and rules as JSON, author, pack hash. Platform configuration, not tenant data: no
    `tenant_id`, no row-level security. The runtime role has no DELETE grant.
  - `verify.policy_pack_approval`: who signed which pack hash in which role (`legal`, `grc`, `security`, `business_owner`). Append-only.
- **Table rules** that hold even for a caller that skips the domain code: a version that has left DRAFT can never change and can only move from
  APPROVED to RETIRED; it is never deleted; an approval is never changed or deleted; the author cannot approve; one person holds one role; an
  approval must sign the stored hash of a draft; a draft becomes APPROVED only with the four approvals of its current hash.
- **Domain** (`modules/verification/domain/policy_packs.py`, pure): the pack model, the hash an approval signs, `approve`, `publish`, `revise`
  (a change is always a new draft version with no approvals), `retire`, and `resolve`, which fails closed (`UNAVAILABLE` for a market no approved
  pack names or whose sources are not in force, `REVIEW` with `policy_missing` or `require_manual_jurisdiction` for too little precision, never a
  default pass). A decision stores `pack_id`, `version` and `pack_hash` so a later change never rewrites it.
- **Store** (`infrastructure/policy_pack_store.py`): a pack read back is re-hashed and refused (`PACK_STORED_HASH_MISMATCH`) if it no longer matches
  the hash stored with it.
- **Service** (`application/policy_packs.py`): open a draft, revise (a new draft version; one open draft per pack; no empty revision), sign,
  publish, retire (rolling back is retiring the newer version), and `governing_pack`.
- **Operator API** (`modules/operator_access/api/regulatory.py`, `/v1/regulatory/...`, route map rule `OPERATOR_PLANE`). Packs are platform
  configuration, so a customer tenant's roles grant nothing here. The operator gate comes first, then one operator capability per action, added to
  the capability catalogue: `regulatory.pack.author`, `regulatory.pack.approve_legal`, `.approve_grc`, `.approve_security`,
  `.approve_business_owner` and `regulatory.pack.publish` (the approver roles and publishing need two approvers; authoring needs one). One
  separation-of-duties set (`regulatory-pack-maker-vs-checker`) means nobody holds the author capability and an approver capability, or two approver
  roles, at once. Routes: list, get, `resolution` (a preview, not a verification result), create a draft, `:revise`, `:approve`, `:publish`, `:retire`.
  No published OpenAPI contract changes (operator routes are not in them).
- **The gate** (`country_gate.check_packs`, `enforce_packs`; rule `gate_decision` in the domain). Besides being ENABLED in the country registry, a
  regulated capability needs an approved pack for the market, or it is refused with `REGULATORY_POLICY_UNAVAILABLE` (no pack, no source in force)
  or `REGULATORY_REVIEW_REQUIRED` (too little precision) before any provider is asked (document T01, T02). The country registry stays the baseline:
  a capability that is not ENABLED there is left to the existing country gate and no pack is read for it, so SANDBOX (the development stand-in) and
  DISABLED are unchanged. The product is the capability name and the purpose is `STANDARD`. A pack that no longer matches its stored hash counts as no
  pack, and the reason is logged. Switch `ZID_REGULATORY_PACKS`: `shadow` (default; a would-be refusal is only logged), `enforce`, `off` (a deployed
  environment refuses it at start-up). Wired at the three entry points of individual verification: start, bulk start and reverify. Today no country
  has a regulated capability ENABLED, so nothing changes until one is.
- **Audit.** Every change writes one protected audit record in the platform partition (`evidence.record_audit`, `tenant_id` NULL) in the same
  transaction as the change: `regulatory.pack.drafted`, `.revised`, `.approved` (with the role), `.published` and `.retired`, each with the pack hash.
  A refused attempt (no capability, a rule broken, the database refusing) is recorded afterwards as `regulatory.pack.refused` with its reason code,
  because the attempt itself rolled back. Records hold ids, codes and the hash, never the rules or the legal text; reads are not audited.

## Owner decisions (proposed values, none ratified)
1. **Module.** Proposed: stay in `verification` for now. Alternative: a separate `regulatory` module with its own migration stream (a move, not a
   rewrite; needs `roles.PLANES`, a stream and ownership entries).
2. **Approver roles.** Proposed: the four roles of the document (legal, GRC, security, business owner), each a different person from the author,
   each an operator capability. Who in the company holds which capability is a business decision and is not made here.
3. **Source status.** Proposed: a pack needs at least one source that is law or guidance and in force; a proposal never counts (document T04).
4. **API prefix.** The document names `/v2/regulatory/...`; the routes use `/v1/regulatory/...` like the rest of this repository's API. Proposed, not ratified.
5. **Gate defaults.** Proposed: purpose `STANDARD` for the capabilities that exist today; default mode `shadow` everywhere; a country-level pack
   (no subdivision, no profession) per capability and country is what opens an ENABLED market. Whether to default to `enforce` in deployed
   environments once packs exist is for the owners.
6. **Existing registry.** The country capability YAML stays the baseline. How its six approvals map to pack approvals is not decided.

## Not built, and why
- The console screen (WF-02).
- The gate at the other entry points (business verification and sign-up, company claims, credential issuance, enterprise use): each is one call to
  `enforce_packs` once the owners agree the product and purpose of its capability. Individual verification is wired.
- An outbox event for other systems when a pack is approved or retired (`policy_pack.approved` and so on in the document): it must be declared in
  the event catalogue first. The audit records below need no catalogue entry and are built.
- A public, tenant-facing coverage read (`GET /coverage` in the document).
- Registers, entitlements and the other packages (RCG-G02 to G12): see `docs/specs/rcg-003-reconciliation.md`.

## Tests
`tests/unit/test_policy_packs.py` (22 tests): the document's T01, T02, T03, T04, T36 and T37 at the domain level.
`tests/component/test_policy_packs_pg.py` (7 tests, PostgreSQL): the lifecycle through the store, the four-approval rule, the author, a repeat
approver and a wrong hash refused by the database, a published version that cannot change, move back or be deleted, a draft that cannot be
retired, a row edited behind the application's back refused on read, and a decision stamp that stays valid across a new version. The downgrade
and re-upgrade of `verification_0005` were run on a fresh database.

`tests/component/test_policy_pack_service_pg.py` (6 tests): the lifecycle service end to end, refusals that leave no trace, revise and roll back,
one open draft per pack, a tampered pack stopping the decision, and the database refusing a bad publish when the service is skipped.
`tests/component/test_regulatory_api_pg.py` (3 tests, real operator sessions): nobody without a token or the capability gets in; the author and
each approver role are separate capabilities; a draft is approved by four people and published by a fifth capability; a revision and a rollback;
the maker cannot also be granted an approver capability; a bad request is refused; and the audit trail in the database (one record per change, none for
an attempt that rolled back, a refusal record with its reason code for every refusal, no rules or legal text in any record). `tests/unit/test_policy_gate.py` (11 tests): the decision rule (no pack, too little precision, sandbox and disabled left to the country gate, off, a
missing resolution is no pack, an unknown mode is an error), the start-up refusal of `off` in a deployed environment, and a guard that start, bulk start and
reverify each call the pack gate after the country gate. `tests/component/test_policy_gate_pg.py` (10 tests, PostgreSQL): enforce and shadow, an approved
country pack, retiring it, a draft that never opens a market, a profession-only pack, sandbox, disabled, off, a pack edited behind the application's back, and
the clock. `tests/unit/test_policy_packs.py` also keeps the API's role
list equal to the domain's.

## Acceptance rows
None linked yet.
