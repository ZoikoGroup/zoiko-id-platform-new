# CR-078: Exact-scope provider suspension: the kill switch, its impact and its rollback (ZID-RCG-003 RCG-G08, WF-06)

Status: open (domain, storage, service, operator API, audit, the country gate and the links to releases and claims built and tested; the owners confirm the points below).

## Why
Document 08.2: suspensions are scoped to provider entity, service, scheme, country, subdivision, purpose, profession or credential class; versioned,
centrally enforced and broadcast; one service suspended must not disable the provider's unrelated authorized services. Its table: a withdrawn
certificate blocks new regulated invocations; a breach freezes the affected source or region; a withdrawn data entitlement disables that connector and
its eligibility claims. T08: a register that later revokes a provider gets a scoped kill switch and an impact event, and history is not rewritten. G08's
acceptance is a certification-withdrawal negative test.

## What changes
- **One table**, `verify.provider_suspension` (migration `verification_0009`), append-only versions, platform configuration. The table repeats the rules:
  no update or delete; version 1 is ACTIVE; a provider-wide suspension only for a breach; a lift is the next version, keeps the scope, is by another
  person and takes effect no earlier than the suspension; nothing takes effect in the future.
- **Domain** (`verification/domain/suspension.py`, pure): `matches` (every named field must equal the call's; a field the call cannot state never
  matches a named one, so nothing is stopped by a guess), `check_new`, `check_lift`, `in_force` and `blocking` (the version in force at any moment),
  and `impact` (labels of the markets, entitlements, certification scopes and claims a scope touches).
- **Service** (`application/suspensions.py`): apply (returns the impact), lift (the rollback), list, impact preview, `blocking_for`, and
  `hold_suspended`, which turns a certification decision into HOLD (`suspended:<id>`) when a suspension covers it.
- **Enforced everywhere it matters, with no shadow mode** (it only fires on a suspension someone applied):
  - the country gate refuses a new call in scope with `PROVIDER_SCOPE_SUSPENDED` before the provider is asked, even when every other switch is off;
  - a launched market whose pinned supplier is covered closes (`supplier_hold:...:suspended:<id>`), and a release cannot be frozen on it;
  - a claim template resting on that supplier comes off at once.
  Lifting puts everything back without anyone editing the release or the template.
- **Operator API** (`operator_access/api/regulatory_suspensions.py`): `POST /v1/regulatory/suspensions` (capability `regulatory.suspension.apply`,
  one approver: an emergency authority behind the operator gate's fresh strong sign-in), `POST .../{id}:lift` (`regulatory.suspension.lift`, two
  approvers; the domain and the table also refuse a lift by whoever applied it), `GET /v1/regulatory/suspensions[?active=true]`, `GET .../impact`.
  Audit in the same transaction: `regulatory.suspension.applied` (trigger, scope fields, evidence reference, impact counts), `.lifted`; a refusal as
  `regulatory.suspension.refused`. The reason text stays out of the audit trail.
- **Web app**: a plain-language notice for `PROVIDER_SCOPE_SUSPENDED`.

## Not in this change
- **Broadcast** of `provider.scope_suspended` to consumers: an outbox event needs an event-catalogue (AsyncAPI) entry; the audit record is written.
- **Pending work marked REVIEW** (08.2 "mark existing pending as REVIEW"): pending verifications are tenant data behind row-level security; the impact
  analysis covers platform records only. Re-running a pending case hits the gate and is refused, which holds it.
- The gate names the service by the capability (`INDIVIDUAL_VERIFICATION`); a suspension of a certified service code (`RTR`) acts through the
  release and claims links, not the gate.
- Automatic triggers (a register withdrawal, a stale trust list) applying a suspension by themselves; today a person applies it.

## Owner decisions (proposed values, none ratified)
1. Applying needs one approver (an emergency); lifting needs two and another person.
2. A provider-wide freeze only for `PROVIDER_BREACH`.
3. The kill switch has no shadow mode.
