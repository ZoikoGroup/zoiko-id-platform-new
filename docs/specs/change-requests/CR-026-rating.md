# CR-026: Rating (included quota, overage, proration, credits)

- **Target:** ZID-COM-001 §6, §7, §11, §13, §22, §26, §30 · **Raised by:** Phase J J-3 · **Status:** Open (built)

## Why

Turn frozen billable events, the tenant's entitlement and the price book line into **rated usage**: deterministic, replayable and explainable line by line.

## Pure rating core

Rating is a pure function with no I/O, in `commercial/domain/rating.py`:

```
rate(entitlement, price_book, usage_by_meter, connections, period, adjustments) -> RatedPeriod
```

- All amounts are `decimal.Decimal` with an explicit currency; quantities are `Decimal`. Rounding happens once, per line, `ROUND_HALF_EVEN` to the currency's minor unit, never mid-calculation. A float anywhere is a test failure.
- **Overage** = `max(0, observed - included) x unit_rate` (§13). Included usage is allocated once per meter and per billing account, never stacked across tenants of the same account in a way that doubles the allowance.
- **Unit conversion** is per line: per 1,000 events, per 10,000 decisions, per MAH, per connection-month, with the divisor in the price book line (not in code). Partial units round up to the next whole unit only where the line says `ROUND_UP_UNITS`; otherwise the quantity is exact.
- **Graduated tiers** (Launch, Business, Scale overage columns) are read from the entitlement's price book version, not the current one. A new price book never changes a past or signed period.
- **Connections** (`SSO_CONN`, `SCIM_CONN`) are counted from the registries at the billing cut-off: active connections minus plan-included minus override additions, times the monthly rate.
- **Minimum and committed spend** produce an explicit `MINIMUM_TRUE_UP` line, never a hidden adjustment.
- **Platform fee** is the flat plan fee for the entitlement's active days in the period.

## Proration (§13, §31)

A plan change or amendment mid-month splits the period at the effective timestamp. Base fee and included allowances are prorated by **days of the period each entitlement was active** (UTC, effective instants to the second, converted to a day fraction using the period length). Usage is rated per segment against that segment's allowance, so a mid-month upgrade cannot double the included volume. The result is deterministic for the same inputs, and the effective timestamps appear on the line.

## Credits

A credit line references the original rated line (`reversal_source`), carries an approver, and can never push an invoice below zero (§13: "no unapproved negative invoices"); an excess becomes a credit balance on the account, not a negative invoice. SLA credits (§15) use the same mechanism with the 5/10/20% stepped table as data and the 20% aggregate cap enforced in code.

## Tables

| Table | Scope | Note |
|---|---|---|
| `commercial.rated_usage` | tenant-scoped (RLS) | One row per (tenant, period, meter, segment): included, chargeable units, unit rate, SKU, price book version, currency, net amount, `correction_of` (nullable), `rating_run_id`. Insert-only. |
| `commercial.rating_run` | tenant-scoped (RLS) | One per (tenant, period, attempt): inputs hash (window hashes, entitlement version, price book version), outputs hash, state `COMPUTED`/`SUPERSEDED`, actor. Re-running with identical inputs yields the identical outputs hash, which is the replay proof. |

## Determinism and replay

`inputs_hash` covers the frozen window hash totals, the entitlement and override versions, the price book id and the connection counts. A second run on the same hash must match `outputs_hash`; a mismatch is raised as an incident, not overwritten. A run is only allowed on a `FROZEN` window; there is no way to rate an open window into an invoice.

## API

`GET /v1/usage/rated?period=` (tenant's own rated lines, with the formula string for each line, e.g. `18,000 x $0.015`) under `commercial.read`; `POST /v1/commercial/rating-runs` (operator, `commercial.rating.run`, R3) to compute and `.../{id}/supersede` for a corrected rerun.

## Tests

- The §13 worked example produces exactly $1,499.00 + $270.00 + $50.00 + $67.50 + $17.50 + $149.00 = **$2,053.00**, line by line.
- Property tests: rating is monotonic in usage, never negative, and splitting events across any partition gives the same total.
- Replay: the same inputs give an identical outputs hash across processes; a changed price book does not change a past run.
- Mid-month amendment: prorated base and allowances to the second, no doubled allowance.
- Rounding is once per line; no float reaches a rating function (static guard plus runtime type check).
- Credit cannot create a negative invoice; SLA credit cap enforced.
- A run on an open window is refused.

## As built

- **Pure core** in `commercial/domain/rating.py`; the proposal's worked bill is the first unit test and is also reproduced end to end from billable events in the database ($2,053.00, six lines).
- **Partial units are not rounded up:** the price book carries no `ROUND_UP_UNITS` flag yet, so a quantity is divided exactly by the line's divisor (the §13 example needs this: 1,500,000 / 10,000 x $0.45 = $67.50).
- **Segment attribution:** usage is assigned to the plan segment its event occurred in; a late event recorded in the month counts in the first segment and approved adjustments count in the last.
- **No price, no charge:** a meter above its allowance with no price-book line (the Developer plan's fair-use usage) is not charged and is listed in `warnings`. A negotiated plan (Enterprise) bills only its floor fee here.
- **SLA credits:** `sla_credit()` takes the stepped table as data from the signed SLA schedule. The proposal names 5/10/20% candidates and a 20% cap but no shortfall thresholds, so none is assumed.
- **Runs** are created by the operator route `POST /v1/commercial/tenants/{t}/rating-runs` (`commercial.billing.manage`); the tenant reads `GET /v1/usage/rated?month=`. Connections are counted when the run is created, which is the cut-off.
