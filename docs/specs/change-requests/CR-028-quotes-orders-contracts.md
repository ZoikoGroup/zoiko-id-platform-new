# CR-028: Commercial accounts, quotes, orders, contracts and discount authority

- **Target:** ZID-COM-001 §9, §10, §12, §14, §17, §21, §30 · **Raised by:** Phase J J-5 · **Status:** Open (built)

## Commercial account vs tenant

A **commercial account** is the legal buyer. It owns one or more tenants and environments. A tenant's platform identity never equals the account. Reseller and end-customer are separate accounts linked by an explicit `reseller_of` edge (§10).

| Table | Scope | Note |
|---|---|---|
| `commercial.account` | not tenant-scoped (account-scoped RLS) | Legal entity name and registration, tax addresses, parent account, collection owner, risk status, currency, `reseller_of`. |
| `commercial.account_tenant` | account-scoped | Account to tenant, effective interval. A tenant has exactly one ACTIVE account. |
| `commercial.quote` | account-scoped | Immutable offer snapshot: lines, price book version, region, currency, term, discount lines, approvals, valid-until, `status` (`DRAFT`/`VALIDATED`/`APPROVED`/`EXPIRED`/`ACCEPTED`/`REJECTED`). |
| `commercial.order` | account-scoped | Accepted quote plus signer, signed document digest, effective date, term, renewal rule, amendment chain (`amends_order_id`). **A signed order is never repriced** (§12). |
| `commercial.order_line` | account-scoped | SKU, quantity or commitment, price book line, region. |
| `commercial.discount_approval` | account-scoped | Requested percent, band, approvers and their decisions, margin result. |

Account-scoped RLS reuses the tenant GUC pattern with a second GUC `app.account_id`, set from the caller's tenant mapping, so the existing isolation tests extend rather than fork.

## Quote validation (`POST /v1/quotes/validate`)

A quote is rejected, with a reason code, when any of these fail (§12, §27, App. C):

1. A SKU is not `released`, or a region or assurance level is not enabled in the production truth table (`commercial.region_release`: cell, SRE gate passed or not). "SLA request for an unlaunched cell" is blocked here.
2. The price book is not `APPROVED`, or is not the one effective at the quote date.
3. A third-party pass-through line carries a discount (no unallocated blanket discount on pass-through).
4. Vendor-cost confidence is missing for a vendor-matrix SKU, or the country is unsupported or the rate spiked beyond a threshold in the vendor ledger (J-7); the quote becomes `NEEDS_APPROVAL`, never silently accepted.
5. The customer-specific floor is breached: revenue at P90 cost after discount must exceed variable cost plus risk reserve (§23). The floor is a table keyed by SKU and region.
6. An annual prepaid discount exceeds 10% or the margin floor.
7. An add-on is quoted without its parent plan or a compatible deployment entitlement.
8. Legal terms promise uncapped liability or a residency commitment outside the contract pack.

## Discount authority (§12)

| Band | Approver |
|---|---|
| 0 to 5% | the account executive |
| over 5% to 12% | CCO |
| over 12% | CFO and Founder |
| annual prepaid up to 10% | CFO, margin permitting |
| multi-product bundle on pass-through | Group CFO |
| pilot credit | CCO and Finance; recorded as a credit, one extension |

The bands are **data** (`commercial.discount_policy`), so approval changes are a versioned release, not a code change. Approval uses the existing approval-request machinery from the authority module; an approver can never approve their own quote, and each approval binds the exact quote digest (a changed quote needs new approvals).

## Orders and activation

`POST /v1/contracts/activate` takes an `ACCEPTED` order and, in one transaction, creates or updates the tenant's entitlement (J-1) with the order as its source, then emits `commercial.entitlement.changed`. It is idempotent on the order id. **There is no other route that raises an entitlement**; a test enumerates every writer of `commercial.entitlement` and asserts they all go through this function or the operator plan-change action, which itself requires an approved order or a documented pilot (§24 "unapproved entitlements").

Amendments mid-month create a new order with `amends_order_id`; rating prorates per CR-026. Pilots (60 to 90 days, §21) are orders with a `PILOT` flag, a credit and an end date; conversion needs a written order after the readiness and margin review recorded on it.

## Channels (§17)

Partner and reseller accounts are accounts with `reseller_of` and a `partner_policy` row (referral percentage, refund window). A partner cannot issue compliance guarantees: the only writable contract fields on a partner order are commercial ones. OEM sub-accounts map to separate accounts with a volume floor. Cloud marketplace private offers import as orders from a port with marketplace fee lines. Group affiliates are internal accounts whose invoices are flagged `INTERCOMPANY` and **excluded from ARR and flagged for elimination** (§18).

## Actions and events

Actions: `commercial.quote.manage`, `commercial.order.manage`, `commercial.discount.approve`, `commercial.account.manage`. Events: `commercial.order.activated`, `commercial.order.amended`, `commercial.quote.approved`.

## Tests

- Every rejection reason in the list above, each with a quote that triggers only that one.
- Discount bands: boundaries at exactly 5%, 12%, 10% annual; self-approval refused; a changed quote voids earlier approvals.
- A signed order is not repriced by a later price book; amendment prorates deterministically.
- Activation is idempotent; every writer of the entitlement table is enumerated and gated.
- Account isolation: another account cannot read or write; a reseller sees its end-customers only where linked.
- Intercompany invoices never enter ARR.
- Partner cannot set compliance or assurance fields.

## As built

- **Operator-plane records.** Accounts, quotes and orders are not tenant data, so they are plain tables read and written by operators under a new operator capability `commercial.sales.manage`, instead of an account-scoped RLS GUC. A tenant reads its own orders (`GET /v1/billing/orders`) by its server-derived id.
- **Truth tables ship empty:** `region_release` (so no region can be quoted until its SRE gate is recorded), `price_floor` and `commercial_role_holder`. `discount_policy` is seeded from the proposal's §12 bands.
- **Approvals** bind the quote digest; the author never approves; one person never fills two roles; the approver must hold the role.
- **Activation** is the only order-to-entitlement path, needs a different person from the order's author, is idempotent, and turns bought SSO/SCIM connections into `extra_count` overrides. A test enumerates every caller of `set_entitlement`: activation and the operator plan-change route (kept for pilots and support; revenue assurance in J-9 flags an entitlement without an order).
- **Pilots** end after the quote's term (30 days per month of term).
- **Not built here:** vendor-cost confirmation (always "unconfirmed" until J-7), cloud-marketplace import, partner commission and intercompany ARR exclusion (J-9 reporting).

