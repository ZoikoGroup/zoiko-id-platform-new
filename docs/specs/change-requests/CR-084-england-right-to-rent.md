# CR-084: England Right to Rent decision for Zoiko Rooms (ZID-RCG-003 section 10.2; T06, T27, T28, T29)

Status: open (domain, service, operator preview and tests built; the legal points below are for Legal to confirm before any landlord relies on it).

## Why
Section 10.2 and its operational boundary: England Right to Rent applies only to a property in England; a digital Right to Rent provider must be
appropriately registered for the service (from 1 October 2026); the landlord keeps the prescribed checks and retention; Home Office online share-code
checks use the prescribed Home Office service; an intermediary must not misrepresent regulatory approval or statutory excuse; these rules must not be
generalised to UK-wide or global rentals.

## What changes
- **Domain** (`verification/domain/right_to_rent.py`, pure): `decide` returns NOT_APPLICABLE for a property outside England (Scotland, Wales and
  Northern Ireland named; anywhere else too; a GB property with no nation is not assumed to be England) (T27). On the digital route it requires a
  provider certified for exactly the RTR service (T28), on the register for RTR from 1 October 2026, with a current supplementary code (T06),
  entitled and not suspended; otherwise BLOCKED with every reason. The Home Office share-code route is PRESCRIBED_ROUTE: the landlord uses the Home
  Office service and nothing of Zoiko's (identity check, biometric) stands in for it (T29). The landlord's manual check produces no Zoiko evidence.
  `statutory_excuse_by_zoiko` is always false. Retention is the configured local schedule; with none configured the result says so.
- **Service** (`application/right_to_rent.py`): the provider's standing is read, as of now, from official-register attestations (schemes `UK_DIATF`
  for the certified services, `UK_DVS_REGISTER` for the register listing, `UK_DIATF_SUPPLEMENTARY` for the supplementary code, service `RTR`, GB),
  the provider entitlement (GB, RIGHT_TO_RENT, STANDARD) and the kill switch. Never from a flag.
- **Operator preview** `GET /v1/regulatory/right-to-rent/check?country=&subdivision=&route=&provider=`: the same decision Zoiko Rooms asks.

## Not in this change
- Zoiko Rooms itself (property listings, tenancies) does not exist in this codebase; `right_to_rent.decide_for` is the call it makes.
- The Home Office online service is not called: the landlord uses it.
- The statutory retention period: Legal sets it (`retention`); it is not invented here.

## Legal points to confirm (not ratified)
1. The scheme and register names used for the provider's records.
2. That the digital route covers only what the certified provider's RTR service covers (British and Irish passport holders by document validation).
3. The retention schedule and what the landlord must keep.
