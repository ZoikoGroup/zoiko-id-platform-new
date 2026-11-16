# CR-103: A person's decision on ownership candidates, RSA Active Authentication, and the contract for the phone app (builds on CR-102; ZID-GAP-001 DTV-18, DTV-24)

Status: **built on the engineering owner's instruction; unsigned.** The RSA check has no real chip vector yet. Nothing here is certified.

## Why

CR-102 listed what was left of identity verification. This change builds the parts that can be built in this repository, and says plainly which parts cannot and why.

## What was built

- **Ownership candidate decisions (DTV-24)**: `organization_0009` (`org.ownership_confirmation`), `POST /v2/ownership-graphs/{id}/confirmations`, action `org.ownership.confirm`, and the decision form on `/admin/ownership/{id}`. A person decides CONFIRMED, NOT_CONFIRMED or ESCALATED on one candidate of one recorded version, with a reason (at least a sentence) and an evidence reference. The platform never decides. The person who recorded the graph cannot decide its candidates (checked in the service and again by a database trigger), only a candidate of that version can be decided, each candidate is decided once, and a decision never changes or is deleted: a different view is a new graph version and a new decision. The audit record holds the code only.
- **RSA Active Authentication (DTV-18)**: `evidence/application/active_auth.py` now verifies an ISO/IEC 9796-2 scheme 1 signature with message recovery (header 0x6A, recovered part, hash of that part and the challenge, trailer 0xBC or hash id and 0xCC), against the value and its complement. Tested with a signer written from the same reading of the standard.
- **The contract for the phone app**: `docs/specs/self-run-mobile-client-contract.md`: the flow, the payloads, the Active Authentication challenge rules, the reason codes in words, and what the app must never do.

## The rules (each has a test)

1. The author of a graph cannot decide its candidates, and a non-candidate cannot be decided.
2. A candidate is decided once, with a reason and an evidence reference; the decision never changes.
3. An RSA chip that signs this session's challenge holds the key; a wrong key, another challenge, a flipped byte or a wrong length is a suspected clone; an unknown trailer is not accepted.

## Not built, and why

- **The phone app and capture flow.** It is a native app (NFC with ISO 14443 and ICAO 9303 cannot be done from a web page). The contract above is what a mobile team builds against.
- **Chip Authentication and PACE.** They are key agreement between the chip and the phone. A server has no way to check them; only the phone can. The contract tells the app to treat a failure there as a failure.
- **The no-chip path.** A reviewer approving from OCR text alone would mark someone verified with no verified claim, so it needs a product and legal decision first. It is deliberately not built.
- **Phone and email observations for the identity feed.** The platform does not collect phone numbers at all, and the only email hash lives in the registration tenant, so using it for linking would cross tenants. A decision is needed on what to collect and where.
- **Issuer-side credential expiry triggers.** They already exist (GID-058: `credential_recheck`, and proofs under an issuer that lost its status are flagged first). The earlier list was wrong to call them open.
- **Provider adapters** for address, open banking, payments, monitoring, on-chain screening and Travel Rule. Each needs a contract with a provider and its message format.

## Owner decisions still needed

1. A real passport vector, from at least two issuing countries, for the RSA check, before it is relied on.
2. Who decides ownership candidates (the role), and whether a second person must agree before CONFIRMED.
3. What contact data the identity feed may collect, and in which tenant it may be linked.
4. The no-chip path, as above.

## Evidence

`backend/app/tests/component/test_ownership_graphs_pg.py`, `backend/app/tests/unit/test_self_run_extras.py`, `frontend/apps/web/src/features/admin/ownership/ownership.test.tsx`.

## Sign-off (UNSIGNED)

| Role | Name | Date | Decision on the four questions above |
|---|---|---|---|
| Product owner | | | |
| Compliance officer | | | |
| Security owner | | | |
