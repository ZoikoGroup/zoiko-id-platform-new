# CR-067: Jurisdiction and profession policy packs (ZID-RCG-003 RCG-G01)

Status: open (domain, storage and tests built; no API or console yet; the owners confirm the points below).

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

## Owner decisions (proposed values, none ratified)
1. **Module.** Proposed: stay in `verification` for now. Alternative: a separate `regulatory` module with its own migration stream (a move, not a
   rewrite; needs `roles.PLANES`, a stream and ownership entries).
2. **Approver roles.** Proposed: the four roles of the document (legal, GRC, security, business owner), each a different person from the author.
   Mapping them to platform authority (new catalogue actions and step-up) is not built.
3. **Source status.** Proposed: a pack needs at least one source that is law or guidance and in force; a proposal never counts (document T04).
4. **API prefix.** The document names `/v2/regulatory/...`; this repository's API is `/v1`. Not decided.
5. **Existing registry.** The country capability YAML stays the baseline. How its six approvals map to pack approvals is not decided.

## Not built, and why
- Service layer, API routes and the console screen (WF-02): they depend on decisions 2 and 4.
- Wiring `resolve` into the verification flow behind the existing country gate.
- Registers, entitlements and the other packages (RCG-G02 to G12): see `docs/specs/rcg-003-reconciliation.md`.

## Tests
`tests/unit/test_policy_packs.py` (22 tests): the document's T01, T02, T03, T04, T36 and T37 at the domain level.
`tests/component/test_policy_packs_pg.py` (7 tests, PostgreSQL): the lifecycle through the store, the four-approval rule, the author, a repeat
approver and a wrong hash refused by the database, a published version that cannot change, move back or be deleted, a draft that cannot be
retired, a row edited behind the application's back refused on read, and a decision stamp that stays valid across a new version. The downgrade
and re-upgrade of `verification_0005` were run on a fresh database.

## Acceptance rows
None linked yet.
