# CR-025: Billable metering ledger

- **Target:** ZID-COM-001 §7 (meter specification), §11, §26 (event envelope), §31 · **Raised by:** Phase J J-2 · **Status:** Open (built)

## Why

`metering.usage_event` (CR-023) is operational telemetry: hourly call counts, batched in memory, lossy by design. Billing needs the opposite: one durable, idempotent, replayable event per chargeable operation. The two are separate and stay separate; the usage page never reads the billing ledger and an invoice never reads telemetry.

## Meters (ZID-COM-001 §7)

| Meter | One event is | Emitted where | Dedupe key |
|---|---|---|---|
| `MAH` | One principal's first qualifying activity in a UTC month, per billed account | derived, see below | `mah:{account}:{yyyymm}:{principal}` |
| `AUTH_EVENT` | One successful authentication completion (not redirects, factors, refreshes, failures or retries) | `session` module when a session is created | `auth:{session_id}` |
| `POLICY_DECISION` | One final PDP result, allow or deny; not a cache hit, not a retry | `decision` module, after the receipt is written | `pdp:{decision_id}` |
| `M2M_GRANT` | One token issued to a non-human principal in production | `session` module, client-credentials and agent token issue | `m2m:{token_jti}` |
| `VC_ISSUED` | One credential issued successfully | `credential` issuer, at the status change to issued | `vci:{credential_id}` |
| `VC_PRESENTED` | One presentation cryptographically checked | `credential` verifier, at a terminal verdict | `vcp:{presentation_id}` |
| `SSO_CONN`, `SCIM_CONN` | Not events: a count of active connections at the billing cut-off, read from the registries | rating (J-3) | none |

Refresh tokens, failed attempts, cached decisions, provider 5xx and Zoiko technical errors are never billable. A verification SKU (IDV, KYB, risk, messaging) is a vendor-matrix meter and arrives with J-7.

## Tables

| Table | Scope | Note |
|---|---|---|
| `commercial.billable_event` | tenant-scoped (RLS) | Event UUID, `deduplication_key`, `meter_id`, `quantity` (decimal), `occurred_at`, `received_at`, `tenant_id`, `commercial_account_id` (nullable until J-5), `environment_id`, `principal_class`, `region`, `status` (`RECORDED`/`ADJUSTED`/`VOID`), `source_request_id`, `sku_version`, `agreement_version` (nullable), `privacy_classification`, `row_hash`. **Unique (tenant_id, deduplication_key).** Insert-only; a trigger refuses UPDATE and DELETE. |
| `commercial.billable_adjustment` | tenant-scoped (RLS) | An adjustment points at the event it corrects (`adjusts_event_id`), carries a signed quantity delta, a reason code, requester and approver. Corrections never edit the original. |
| `commercial.meter_window` | tenant-scoped (RLS) | One row per (tenant, meter, UTC month): `OPEN` or `FROZEN`, freeze time, event count and a hash total. A frozen window refuses new events with a past `occurred_at`; late events land in the next window as a flagged adjustment. |
| `commercial.mah_activity` | tenant-scoped (RLS) | Unique (tenant, yyyymm, principal). The first qualifying action inserts here and emits the `MAH` event once; a conflict means already counted. |

The event stores a pseudonymous principal id only where the meter needs it (MAH); no names, emails or claim values (§26).

## Exactly-once economic effect

The emit is `INSERT ... ON CONFLICT (tenant_id, deduplication_key) DO NOTHING` **in the same transaction as the business effect**. If the business transaction rolls back, no billable event exists; if it commits, exactly one does, however often the request is retried or the consumer redelivers. There is no async hop that can lose it and no second path that can double it.

Identity merge midway through a month (§31): MAH keys on the canonical principal id after the approved mapping, so a merge that happens later cannot produce a second MAH for the same person in that month; a merge of two already-counted principals leaves two events and raises a reviewable `MAH_MERGE_ADVISORY` adjustment candidate rather than silently deleting one.

## Never on the security path

The recorder is called after the effect inside the same transaction and writes one row. Its failure follows one rule: **a billing write failure never blocks login, refresh, revocation or a decision.** The emit runs in a savepoint; if the insert fails, the savepoint rolls back, the business operation continues, a `METERING_WRITE_FAILED` counter and an operations alert fire, and the event is recovered by the nightly reconciliation (next section). Billing accuracy is repaired after the fact; availability of identity is never traded for it.

## Reconciliation and replay

- A reconciliation job compares billable events against the authoritative source rows (sessions, decisions, tokens, credentials) for the window and writes missing events with `source_request_id` and a `RECOVERED` marker. It never invents an event without a source row.
- `meter_window` freeze produces the hash total used in the audit pack (J-9). Replaying the window from `billable_event` must give the same count and hash.

## API

| Route | Action | Notes |
|---|---|---|
| `GET /v1/usage/billable?month=&meter=` | `commercial.read` (R1) | The tenant's own ledger for reproducing its bill (§26). Event ids and counts, no principal ids. |
| `GET /v1/usage/billable/export` | `commercial.read` (R1) | CSV or JSON of the ledger for a month. |
| `POST /v1/commercial/adjustments/request` and `/approve` | `commercial.adjustment.manage` (R3, dual control) | Operator surface. The approver is not the requester. |

## Events and actions

Catalogue additions: actions `commercial.read`, `commercial.adjustment.manage`; events `commercial.billable_event.adjusted`, `commercial.meter_window.frozen`. Billable events themselves are **not** published to Kafka: they are rows, not messages.

## Tests

- Duplicate and re-ordered emits produce one row (§31 first test); a rolled-back business transaction leaves none.
- Refresh, failed auth, cached decision and retry never emit.
- A failing billing insert does not fail login, refresh or a decision, raises the counter and is repaired by reconciliation.
- Insert-only: UPDATE and DELETE refused for the runtime role.
- Cross-tenant isolation and a client that tries to name another tenant.
- Frozen window refuses a back-dated event; a late event becomes a flagged adjustment.
- 100,000 synthetic events across 8 workers: no duplicate keys, hash total stable on replay.
- Merge mid-month: documented MAH behaviour above, no silent duplicate.

## As built

- **Where each event is recorded:** `AUTH_EVENT` and the `MAH` mark at passkey login and at federated (OIDC/SAML) login, right after the session is created; `POLICY_DECISION` after a decision on the AuthZEN evaluation endpoint (an unknown action is never a decision and bills nothing; internal protected-action decisions are platform administration and are not metered); `M2M_GRANT` for each `client_credentials` token (keyed by the token's own id) and each workload token; `VC_ISSUED` when a credential is issued; `VC_PRESENTED` at a presentation's terminal verdict, accepted or rejected for a credential reason.
- **Never on the security path:** the recorder runs in a savepoint and never raises; a failure increments `STATS["write_failed"]` and the sign-in, decision or token continues.
- **Late events:** a frozen window refuses new events at the database. A late event is recorded in the next open window with `late = true`. Reconciliation (`POST /v1/commercial/tenants/{t}/reconcile/{month}`, operator, `commercial.billing.manage`) compares sessions and credentials with the ledger and records what is missing with `recovered = true`; decisions are not reconciled here because their authoritative record is the evidence receipt (J-9).
- **Operator routes** live in `operator_access/api/commercial_admin.py` so the commercial module imports no other module and the dependency graph stays acyclic. A new two-person operator capability `commercial.billing.manage` covers freezing windows, adjustments and reconciliation.
- **Not built here:** a scheduled reconciliation job (the endpoint is the unit it will call), and `commercial_account_id`, `sku_version` and `agreement_version` stay null until orders exist (J-5).
