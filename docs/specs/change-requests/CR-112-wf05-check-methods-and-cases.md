# CR-112: WF-05 check method lifecycle, eligibility, verification cases and separate evidence assertions (ZID-DTV-WF-05 §02, §04, §05, §06, §19, §21, §23, A01, A09)

Status: open (Step 2 of the WF-05 build: domain, tables, services, operator and tenant APIs, unit and PostgreSQL tests). No adapter ships and no retention
is approved, so every method is unavailable until the owners act.

## What changes
- **Check methods** (`verification/domain/check_methods.py`): EMAIL_CHALLENGE, PHONE_SMS_CHALLENGE, PHONE_VOICE_CHALLENGE, CARRIER_INTELLIGENCE,
  ADDRESS_LOOKUP, ADDRESS_DOCUMENT, ADDRESS_DATA_SOURCE, ADDRESS_POSTAL_CHALLENGE, DEVICE_RISK. Each lists the only assertions it can produce and the
  data classes it creates. Routes are OPEN, LICENSED or MANUAL.
- **Lifecycle**: the WF-04 biometric lifecycle (two people up one step at a time, one person down at once) is now shared code
  (`method_lifecycle.Lifecycle`, `method_store.MethodTables`, `operator_access/api/method_lifecycle_routes.py`). The WF-05 methods use their own
  tables (`verify.check_method_state`, `verify.check_method_proposal`, migration `verification_0021`) and routes `/v1/regulatory/check-methods`
  (capabilities `regulatory.check_method.propose|approve|pause`, event `verification.check_method.state_changed`). Biometric paths, capabilities and
  events are unchanged; the two families cannot share a table (`METHOD_FAMILY_MISMATCH`).
- **Eligibility** (`GET /v1/check-methods/eligibility`): a method is available only with an adapter (`config/verification/check-adapters.yaml`,
  empty), a live lifecycle scope, an APPROVED retention rule for every data class (`config/privacy/check-data-classes.yaml`, all UNSET), an
  entitlement for a licensed route (none wired, so refused), and a signed country privacy overlay. Every refusal is named; the answer has `policy_hash`.
- **Assertions** (`domain/assertions.py`): the six §02 assertions with what each proves and does not prove.
- **Cases** (`domain/check_cases.py`, migration `verification_0022`, RLS): `POST /v1/verification-cases` (Idempotency-Key), `GET .../{id}`,
  `.../requirements`, `.../evidence`, `POST .../{id}:close`. A case opens ELIGIBLE with the methods available then, or CREATED with
  "This step is unavailable". The subject is an opaque reference, never an email or phone. Requirements are re-read live. Evidence shows each
  assertion on its own with what it does not prove; there is no overall result. Assertions are append-only, come only from a method that can produce
  them and that the case was cleared for, never carry a reason code saying fraud, and show EXPIRED after their expiry. The tables repeat these rules.

## Not yet
- No adapter (email, SMS, address, postal, device): WF-05 steps 3 to 5.
- Licensed routes are refused until entitlement and spend caps are wired (step 3).
- Region routing per adapter, reviewer overrides with a second reviewer (A10), customer policy console (A11) and screens: later steps.

## Owner decisions
DEC-01 (markets and journeys), DEC-05 (suppliers and costs), DEC-11 (retention per data class).

## Evidence
`tests/unit/test_check_methods.py`, `tests/unit/test_check_cases.py`, `tests/component/test_check_methods_pg.py`,
`tests/component/test_check_cases_pg.py`; the existing biometric suites cover the shared lifecycle.
