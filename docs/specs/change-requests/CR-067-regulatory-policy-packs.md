# CR-067: Jurisdiction and profession policy packs (ZID-RCG-003 RCG-G01)

Status: open (domain, storage, lifecycle service, operator API and tests built; no console yet; the owners confirm the points below).

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

## Owner decisions (proposed values, none ratified)
1. **Module.** Proposed: stay in `verification` for now. Alternative: a separate `regulatory` module with its own migration stream (a move, not a
   rewrite; needs `roles.PLANES`, a stream and ownership entries).
2. **Approver roles.** Proposed: the four roles of the document (legal, GRC, security, business owner), each a different person from the author,
   each an operator capability. Who in the company holds which capability is a business decision and is not made here.
3. **Source status.** Proposed: a pack needs at least one source that is law or guidance and in force; a proposal never counts (document T04).
4. **API prefix.** The document names `/v2/regulatory/...`; the routes use `/v1/regulatory/...` like the rest of this repository's API. Proposed, not ratified.
5. **Existing registry.** The country capability YAML stays the baseline. How its six approvals map to pack approvals is not decided.

## Not built, and why
- The console screen (WF-02).
- Wiring `resolve` into the verification flow behind the existing country gate.
- An audit event for each change: an event must be declared in the event catalogue first.
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
the maker cannot also be granted an approver capability; a bad request is refused. `tests/unit/test_policy_packs.py` also keeps the API's role
list equal to the domain's.

## Acceptance rows
None linked yet.
