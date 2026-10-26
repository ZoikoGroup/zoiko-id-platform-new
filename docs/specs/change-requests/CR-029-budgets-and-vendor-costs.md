# CR-029: Anti-bill-shock budgets and the vendor-cost ledger

- **Target:** ZID-COM-001 §7 (anti-bill-shock), §8, §23, §24, §31 · **Raised by:** Phase J J-6, J-7 · **Status:** Open (built)

## Part 1: Budgets and warnings (J-6)

The customer sees what they have included, what they have used and a projected month-end bill, and is warned before it surprises them. **No budget setting can switch off a security path** (§7): a budget alert only notifies, and any throttling is a separately governed policy that cannot touch authentication, refresh, revocation or recovery.

| Table | Scope | Note |
|---|---|---|
| `commercial.budget` | tenant-scoped (RLS) | Meter or whole-bill target, monthly amount or quantity, currency, thresholds (default 50, 80, 100 percent), notify target, `version`. |
| `commercial.budget_alert` | tenant-scoped (RLS) | One per (budget, period, threshold) with a unique key so a threshold fires once per period. Links to the alert in the notification centre. |

Mechanics:

- A cheap evaluator runs on the worker every few minutes: current month-to-date from the billable ledger, run through the **same rating function** against the entitlement (CR-026) so the preview and the invoice cannot disagree.
- **Projection**: month-to-date divided by elapsed fraction of the month, with a floor on elapsed time (no projection in the first six hours) and a confidence flag when the window is short. Stated as an estimate with its assumptions, never a quotation.
- Crossing a threshold creates an alert (CR-019) and a webhook event `commercial.budget.threshold_crossed`. Alerts are mandatory kinds for billing contacts, not mutable by the tenant muting setting.
- An included-quota warning is emitted at 50, 80 and 100 percent of each included meter regardless of whether a budget exists.
- Exceeding the plan produces a **controlled-expansion** notice, not a failure: metered usage continues and is billed as overage; only hard-capped counts (connections, environments) refuse new items.

API: `GET/PUT /v1/budgets` (`commercial.budget.manage`, R2), `GET /v1/billing/preview` (month-to-date lines, projected total, assumptions).

Tests: the preview equals the rating function for the same events; a threshold fires once per period and survives a worker restart; a budget never blocks login or revocation (security-path assertion); a budget cannot be set below zero or on another tenant.

## Part 2: Vendor-cost ledger (J-7)

Verification, KYB, risk screening and messaging are supplied by third parties. Each chargeable check has a **cost side** that must be matched to a **revenue side**, per route (§8, §23).

| Table | Scope | Note |
|---|---|---|
| `commercial.vendor` | not tenant-scoped | Supplier, role (`SUBPROCESSOR` or `INDEPENDENT_CONTROLLER`), status, `recognition_basis` (`PRINCIPAL`/`AGENT`/`UNDECIDED`, set by Finance). |
| `commercial.vendor_rate` | not tenant-scoped | Vendor, check type, country, modality, base price, retry price, minimum commitment, currency, validity, `confidence` (`SIGNED_TARIFF`/`QUOTED`/`ESTIMATE`), source reference. Versioned; an unsigned rate cannot back a published price. |
| `commercial.vendor_cost_event` | tenant-scoped (RLS) | The cost of one check: vendor, check, country, outcome, attempt number, cost, cost currency, FX at event date, `billable_event_id` join (nullable when the failure is not chargeable), invoice reference. |
| `commercial.vendor_invoice_line` | not tenant-scoped | Imported supplier invoice lines (CSV or API port) with a match status. |

Rules (§7, §8, App. C):

- **Retry billing.** Provider 5xx and Zoiko technical errors are never charged to the customer; customer-initiated retries and insufficient-data attempts are charged at the check-specific retry rate. The rule is a table per check type, tested for the supplier-5xx case. The platform still records the supplier cost for every attempt, so margin is honest.
- **Unsupported country or rate spike** blocks the route for new quotes (CR-028 rule 4) and raises a review item; existing orders keep their contracted behaviour.
- **Reconciliation** joins `vendor_invoice_line` to `vendor_cost_event` by supplier reference; unmatched lines on either side go to a dispute queue with an owner and an age. A month cannot close with unmatched lines above a threshold without a signed exception.
- **Margin report** per vendor and corridor: billed check revenue minus vendor cost minus processing and manual review, over billed revenue (§8). Reported only; never used to change a price automatically.
- No supplier price is invented. The ledger ships empty; the verification SKUs have no rate until a vendor rate row with `SIGNED_TARIFF` confidence exists.

Actions: `commercial.vendor.manage` (R3), `commercial.vendor.read` (R1). Events: `commercial.vendor_cost.recorded`, `commercial.vendor_route.blocked`.

Tests: supplier 5xx is not charged but costed; retries use the retry rate; an `ESTIMATE` rate cannot back a quote; unsupported country blocks quotes and not existing orders; reconciliation finds a missing and a duplicate supplier line; cross-tenant isolation of cost events; the margin formula against a hand-computed example.

## As built

- **Budgets (J-6):** `GET /v1/billing/preview` (bill so far and projected month-end bill, with assumptions), `GET/PUT /v1/budgets` and `POST /v1/budgets:evaluate` (action `commercial.budget.manage`). A crossing is recorded once per (key, month, threshold) and emits `commercial.budget.threshold_crossed`, which the alerts centre shows as a HIGH, mandatory notice; `commercial.billing_state.changed` is shown the same way. Evaluation runs on request; a scheduled job is not built yet.
- **Projection** scales month-to-date usage by the elapsed share of the month (quantised before rounding up, so 1/3 of a month does not inflate a whole unit) and rates it with the invoice's own function.
- **Vendor costs (J-7):** suppliers, versioned rates, routes, per-attempt cost events, supplier invoice import and reconciliation into disputes, all under the operator's `commercial.catalog.manage` grant. The cost ledger is an operator-plane table (it carries the tenant for attribution only) because a supplier invoice spans tenants.
- **Recorded at the source:** the verification provider callback records each accepted provider result's cost in the same transaction; a provider or platform error is costed but never chargeable; repeat attempts on the same verification use the retry price. Recording never raises.
- **Quoting:** only an ACTIVE route on a SIGNED_TARIFF rate confirms a pass-through SKU for a quote (`confirmed_skus`), so an estimate or an unapproved spike can never back a promise.
- **Margin** is reported as None until verification SKUs carry a price; it is never inferred.

