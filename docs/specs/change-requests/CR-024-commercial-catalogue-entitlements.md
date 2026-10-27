# CR-024: Commercial catalogue, plans and entitlements

- **Target:** ZID-COM-001 §2, §5, §6, §12, §26, §29; ZID-DATA-001 §1.1; action catalogue · **Raised by:** Phase J J-1 · **Status:** Open (built)

## Why

The platform has no notion of a plan. ZID-COM-001 requires a versioned SKU catalogue, five plans, and a server-side entitlement decision that no frontend value can override.

## Tables

| Table | Module | Scope | Note |
|---|---|---|---|
| `commercial.sku` | commercial | not tenant-scoped | Stable id (`ZID-HUM`), family, unit, meter id, consumption type (`FLAT`, `METERED`, `PASSTHROUGH`), `released` flag, tax category. Append-only; retiring a SKU sets `retired_at`. |
| `commercial.price_book` | commercial | not tenant-scoped | Version number, currency, status `DRAFT`/`APPROVED`/`SUPERSEDED`, `effective_from`, approved by and at. An `APPROVED` book is immutable. |
| `commercial.price_book_line` | commercial | not tenant-scoped | Price book, SKU, plan, included quantity, tier boundaries and unit rates as `NUMERIC(18,6)`, minimum, currency. |
| `commercial.plan` | commercial | not tenant-scoped | `DEVELOPER`, `LAUNCH`, `BUSINESS`, `SCALE`, `ENTERPRISE`; platform fee; limits (production environments, SSO included); `published` flag. |
| `commercial.capability` | commercial | not tenant-scoped | Capability key, label, `released` flag and the SKU that prices it. Only the capabilities the platform implements are marked released; verification, dedicated region and operational SLA are not. |
| `commercial.plan_capability` | commercial | not tenant-scoped | Plan, capability key (for example `saml_sso`, `scim`, `extended_audit`, `dedicated_region`), mode `INCLUDED`/`ADD_ON`/`METERED`/`CONTRACT`/`NONE`, and the SKU that prices it. |
| `commercial.entitlement` | commercial | tenant-scoped (RLS) | Tenant, plan, price book version, state `PENDING`/`ACTIVE`/`SUSPENDED`/`ENDED`, effective interval, source order (nullable until J-5), `version`. One ACTIVE row per tenant, enforced by a partial unique index. |
| `commercial.entitlement_override` | commercial | tenant-scoped (RLS) | Contracted additions to a plan (extra connections, higher quota), each with reason, approver and expiry. |

Retired and superseded rows stay. A tenant's entitlement history is the audit trail.

## The gate

`commercial.ports.entitled(conn, tenant_id, capability, *, quantity=1) -> Entitlement` returns an explicit result with `allowed`, `mode`, `limit`, `used` and a reason code (`NO_ENTITLEMENT`, `NOT_IN_PLAN`, `QUOTA_EXHAUSTED`, `SUSPENDED`, `NOT_RELEASED`). Callers branch on it; there is no exception path that grants.

Rules:

- **Fail closed for paid extras only.** If the entitlement cannot be read, paid extras are denied and the failure is logged. Security paths (authentication, token refresh, session revoke, recovery, signal intake, decision evaluation for already-issued authority) never call the gate.
- A capability with `released = false` is never granted, whatever the plan says.
- `QUOTA_EXHAUSTED` applies to hard-capped counts (connections, environments). Metered volumes are never capped here; they go to rating in J-3.
- Entitlement changes are R2 and appear in the evidence ledger.

First callers (small, real): adding a SAML or OIDC federation connection, adding a SCIM source, and creating an environment check the gate. Everything else stays ungated until its slice.

## API

| Route | Action | Notes |
|---|---|---|
| `GET /v1/catalog` | none (public, cached) | Published plans and APPROVED price book lines only. Drafts are never visible. |
| `GET /v1/tenants/{id}/entitlements` | `commercial.read` (R1) | The tenant's plan, limits, used counts and capabilities. The id must equal the caller's tenant. |
| `POST /v1/commercial/price-books` and `/{id}/approve` | `commercial.catalog.manage` (R3, dual control) | Operator surface. Approval freezes the book. |
| `PUT /v1/commercial/tenants/{id}/entitlement` | `commercial.entitlement.manage` (R3) | Operator surface. Moves a tenant to a plan with an effective date; not retroactive. |

Client-supplied tenant, price or plan values are ignored everywhere (the TEN-03 rule).

## Events

`commercial.entitlement.changed` (tenant, plan, price book version, state, effective interval) and `commercial.price_book.approved`. Both are added to the event catalogue.

## Seed data

A migration inserts the SKU registry and the five plans from ZID-COM-001 §5, §6 and §29 plus one **DRAFT** price book from §6 and §7, marked unapproved. Nothing is priced in `GET /v1/catalog` until a second operator approves a book. `ZID-AUTH` is added to the §5 registry because §7 prices an authentication-event meter that §5 does not list.

**No backfill.** A tenant with no entitlement row is on the Developer plan (`source: DEFAULT`), so every existing and future tenant is covered without touching tenant bootstrap, and nothing changes for them.

## Enforcement mode

`ZID_COMMERCIAL_ENFORCEMENT` is `off`, `shadow` (the default) or `enforce`. In `shadow` the gate computes and logs what it would refuse but blocks nothing, so the catalogue can ship before any price is approved. `enforce` is switched on per cell once a price book is approved. The two first callers are creating a federation connection (`saml_sso`) and a SCIM source (`scim`); both return `402 ENTITLEMENT_REQUIRED` when enforcing and over the plan. Their usage counts are wired at the composition root so the commercial module never imports the modules it counts.

## Operator authority

Price books and plan changes use the existing operator plane: the operator gate (operator client, operator tenant, AAL-Z3) plus a live JIT grant. A new platform capability `commercial.catalog.manage` (two-person) covers price books; moving a tenant or adding an override uses `tenant.config.change` for that tenant. An approver must be a different principal from the author (also a database check), and an override needs a second active operator as approver.

## Tests

- Plan limits and capability modes match §6 and §29 exactly (data-driven from one table).
- An unreleased capability is never granted; a DRAFT price book is invisible to `GET /v1/catalog`.
- An approved price book is immutable; a new version never reprices an existing entitlement.
- One ACTIVE entitlement per tenant under concurrent change; cross-tenant read and write refused (RLS and API).
- Fail closed for paid extras when the entitlement cannot be read, and fail open for the security paths in the same condition.
- A tenant cannot name another tenant, plan or price in any request.
- The gate on federation connections and SCIM sources denies above quota and allows within it.

## Out of scope here

Rating, invoices and orders (J-3 to J-5). The entitlement's `source order` column exists and stays null until orders exist.
