# CR-070 (proposal): a meter for credential claim checks (ZID-GAP-001 v2.0 GID-064)

Status: **usage metering applied on the engineering owner's instruction of 2026-11-16; billing is NOT applied and still needs the owners.** Two USAGE-ONLY meters now stand behind the two register definitions, exactly as CR-090 did for screening: `CREDENTIAL_CHECK` (one completed claim check that reached a source, or an issuer outreach that concluded) and `CREDENTIAL_SENSITIVE_CHECK` (one completed sensitive-category check). They are recorded in the same transaction as the check (key derived from the check id, so a repeat is one event) and are never rated or invoiced: no SKU row and no price exists (`commercial_0043` widens the closed meter set; `credential.claim_checks.record_usage`; the composition root wires `commercial.credential_usage_sink`). A check refused before a source is asked, or whose source was unavailable, is not counted. Tests: `test_credential_meters_pg.py`, the register pin in `test_capability_bands.py`. Still owner decisions: whether checks are billed at all, the unit, the price per market (CFO, CCO), provider pass-through, and the recording point; steps 2 and 3 below stay proposals.

## Why this is a proposal and not code

`backend/config/entitlements/meter-register.yaml` is the commercial meter register, and
`test_the_meter_register_is_the_records_eight_meters_and_prices_none_of_them` pins it to the eight meters of the approved record. Every
meter is defined but unpriced (`priced: false`), and recording is off (`bill_on_commit: false`) until the owners rule that a given API is
the recording point. The billable meters are also a closed set in the database (check constraints in `commercial_0001`, `0002`, `0006`),
in `billable.METERS`, in the SKU map in `rating.py`, and in the price book lines. Adding a ninth meter is therefore a decision about the
record and the price book, not a refactor. GID-064 also says "no UK-only quote as default global price" and GLB-35 requires a missing
signed price book, FX or territory to block activation; the existing quote rules already reject a line with no price (`PRICE_MISSING`).

## Proposed register entries (for the owners to approve or change)

```yaml
  credential.check:
    example_unit: "Claim check against a source"
    counts: "One completed check of one claim (academic, license, certificate, CPD, employment, insurance) that reached a source or an issuer outreach concluded; counted once per logical check, not per retry"
    guardrail: "No charge for a check refused before a source is asked (no pack, no contract, bad claim) or when the source was unavailable; provider pass-through shown separately, never discounted"
    existing_meter: null
    existing_sku: null
    hard_quota: false
    bill_on_commit: false
    priced: false
  credential.sensitive_check:
    example_unit: "Regulator adverse-finding check"
    counts: "One completed sensitive-category check"
    guardrail: "Only under a recorded basis, authorized user and reviewer; priced separately from standard checks"
    existing_meter: null
    existing_sku: null
    hard_quota: false
    bill_on_commit: false
    priced: false
```

## What approval would make engineering do

1. Add the two entries and update the register test's expected set (a deliberate, reviewed change).
2. If the owners want them billable: a new billable meter and SKU (migrations for the three constraints, `METERS`, `METER_SKU`, the
   catalogue SKU rows), recorded through `record_billable` with the key derived from the check id, in the same transaction as the check
   (the pattern `credential.issue` uses), and a price book line per market.
3. Decide the retry and failure treatment already stated in the guardrails.

## Owner decisions needed

Whether credential checks are metered at all and at what unit; the price per market (CFO, CCO); provider pass-through treatment;
whether the recording point is the check or an outreach conclusion; whether sensitive checks are a separate meter.
