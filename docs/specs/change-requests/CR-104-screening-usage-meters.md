# CR-104: usage meters for screening checks and monitoring periods (ZID-UX-PRICE-004 P23)

Status: **applied as usage facts only.** No SKU, price, quota or invoice line is added. It was made on the engineering owner's instruction of 2026-11-13, on the pattern of CR-070.

## Why

The public pricing page document (ZID-UX-PRICE-004, acceptance row P23) requires a screening check and a monitoring period to be separate meter definitions, so that a buyer is never charged for a period as if it were a check, or the reverse. Until now the sanctions and risk family (SCR) had no meter at all.

## What changed

| Where | Change |
|---|---|
| `backend/config/entitlements/meter-register.yaml` | Two definitions: `screening.check` and `screening.monitor_period`. Both `priced: false`, no quota, `bill_on_commit: false`. |
| `commercial_0042` | Widens the closed meter set in the check constraints of `billable_event`, `billable_adjustment`, `meter_window` and `budget` to carry `SCREEN_CHECK` and `SCREEN_MONITOR_PERIOD`. Existing rows are untouched. |
| `domain/billable.py`, `commercial/ports.py` | The two meters and their deduplication keys: `scrchk:<case>:<version>` and `scrmon:<case>:<YYYYMM>`. |
| `screening/application/screening.py` | An automated screening decision records one `SCREEN_CHECK`, in the same transaction. A reviewer's decision on it records none. A rescreen records its check and one `SCREEN_MONITOR_PERIOD` for the case and UTC month, however many rescreens run in it. |

## What it deliberately does not do

- It adds no SKU row and no entry in the rating map (`rating.METER_SKU`), so these events are never rated and never reach an invoice. They are usage facts for the owners to price.
- It does not change any of the eight ratified meters.
- A recording failure never blocks the screening (the existing recorder rule).

## Owner decisions still needed before anything is billed

The unit (per decision, per subject, or per case-month), the price per market (CFO, CCO), provider pass-through treatment, whether a monitoring rescreen is also billed as a check or only as its period, and the recording point. Until then the events only accumulate as usage.

## Evidence

`backend/app/tests/component/test_screening_meters_pg.py`, `backend/app/tests/unit/test_capability_bands.py` (the register).
