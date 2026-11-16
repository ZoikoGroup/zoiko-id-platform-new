# CR-097: Crypto Travel Rule shell: IVMS101 validation and the counterparty VASP check (ZID-GAP-001 v2.0 DTV-33; CR-073 section D)

Status: **engineering shell built; the product line stays DESIGN, so the route refuses every call until the owners record a legal basis and a partner contract.** No provider and no threshold is invented: the registry ships empty.

## Why

The Travel Rule moves originator and beneficiary information between two virtual asset service providers over a protocol network, which needs a licensed partner. CR-073 D listed what is ours and buildable without one: validate the IVMS101 payload, check the counterparty VASP against a registry the operators control, and report the threshold question honestly.

## What was built

| Part | Where | What it does |
|---|---|---|
| Rules | `verification/domain/travel_rule.py` | Shape checks of the IVMS101 model (originator and beneficiary persons, names, accounts, both VASPs, the transfer). A counterparty is KNOWN only when its LEI passes ISO 17442, it is listed, and the listing is ACTIVE for the transfer's jurisdiction; otherwise UNVERIFIED_COUNTERPARTY, NOT_ACTIVE_FOR_JURISDICTION, LEI_INVALID or NOT_PROVIDED. The threshold is AT_OR_ABOVE, BELOW or THRESHOLD_UNKNOWN (no value configured, or another currency: never assumed below). |
| Registry | `config/product-lines/vasp-registry.yaml`, `verification/infrastructure/vasp_registry.py` | Shipped empty. Each entry needs a valid LEI, jurisdictions, a status and a source reference; each threshold needs a legal basis reference. A malformed file stops the service. |
| Route | `POST /v2/travel-rule/validate`, action `verification.travel_rule.validate` (R2, E2) | Stateless. Behind the product line gate (`travel_rule` is DESIGN). Findings carry a code and a path, never a value. |

## The rules (each has a test)

1. A well-formed payload proves nothing about the people: the answer says so.
2. An unlisted provider is never assumed fine.
3. No threshold ships; with none configured the answer is THRESHOLD_UNKNOWN.
4. Nothing is sent to a partner, nothing is stored, and the route never decides to release or hold a transfer.
5. The shipped DESIGN line refuses the route (409 `service_unavailable`).

## What it does not do

- It does not move a message between providers. That needs the partner's protocol network.
- It keeps no message record. A record of messages sent and received belongs with the partner adapter, once there is one.
- It does not check that a wallet address belongs to a person.

## Owner decisions still needed

1. Legal chooses the jurisdictions and records the legal basis for each threshold.
2. CTO and Legal choose the protocol partner and sign the contract; the line needs `partner_contract_ref` to leave DESIGN.
3. Operators fill the VASP registry from real sources, each with a source reference.

## Evidence

`backend/app/tests/unit/test_travel_rule.py`, `backend/app/tests/component/test_travel_rule_pg.py`.

## Sign-off (UNSIGNED)

| Role | Name | Date | Decision on the three questions above |
|---|---|---|---|
| Legal | | | |
| CTO | | | |
