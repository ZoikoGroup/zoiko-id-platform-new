# CR-074: Partner entitlement and source authorization (ZID-RCG-003 RCG-G03)

Status: open (domain, storage, operator API, audit and the country-gate check built and tested; no console yet; the owners confirm the points below).

## Why
A provider being certified (CR-073) is not the same as Zoiko being allowed to use it in a market. The contract, the product licence and the
data-use terms decide that, and document 05.2 says access to a source is not a licence for unrestricted use. Today the country registry names a
provider per capability and nothing checks that Zoiko holds a current permission to call it there.

## What changes
- **One table**, `verify.provider_contract_entitlement` (migration `verification_0008`), append-only, platform configuration (no `tenant_id`, no row-level
  security, registered in `backend/database/table-ownership.yaml`). One row per statement for an exact provider, country, capability and purpose:
  GRANTED, SUSPENDED or WITHDRAWN, when it takes effect, its validity dates, and the contract and documented terms it rests on. A trigger refuses
  update, delete, a future statement and one not newer than the previous statement of the same scope.
- **Domain** (`domain/entitlement.py`, pure): `decide` is ENTITLED only when the latest statement effective at the time asked says GRANTED and the
  time is inside its dates. No statement, suspension, withdrawal, not yet valid and expired are DENIED with the reason. A neighbouring country,
  capability or purpose is a different scope. Asked about a past moment, the answer comes from the statement then in force.
- **Store** (`infrastructure/entitlement_store.py`): `record`, `history`, `decide_scope(s)`, `scopes_of`.
- **Operator API** (same router as CR-072): `POST /v1/regulatory/entitlements` (capability `regulatory.entitlement.record`, two approvers) and
  `GET /v1/regulatory/entitlements` (scopes and today's decision). Audit: `regulatory.entitlement.recorded` with status and contract reference.
- **The gate** (`country_gate.check_packs`, `entitlement_mode`). For an ENABLED capability, the provider the registry names must be ENTITLED for the
  country, the capability and the standard purpose, or the request is refused with `PROVIDER_NOT_ENTITLED` before the provider is called (document
  T-series "disallowed scope denied"). A capability with no named provider counts as not entitled. Switch `ZID_PROVIDER_ENTITLEMENTS`: `shadow`
  (default, logs only), `enforce`, `off` (refused outside local, test and dev). Same entry points as CR-067.

## Not in this change
- The event `provider.entitlement_changed` for subscribers (needs an event-catalogue entry and an outbox declaration).
- Per-source entitlement for credential issuers and claim sources (document 05.2 data rights); the record is keyed by provider and could carry them later.

## Owner decisions (proposed values, none ratified)
1. Purpose is the standard purpose for now, as in CR-067 and CR-072.
2. Entitlement statements need two approvers to be recorded, because they decide whether a paid third party is called.
3. Who holds `regulatory.entitlement.record` (vendor management proposed) is a business decision.
