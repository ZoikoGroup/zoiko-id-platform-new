# CR-081: Sector, transaction and crypto monitoring scope (ZID-RCG-003 RCG-G12)

Status: open (domain, configuration, the check in the scheduled re-screen, the operator preview and tests built; no approval is listed, so the check only reports; the owners confirm the points below). RCG-G12 is P2 and not part of the Zoikorum launch: this change builds the GATE every such feature must pass, not the features.

## Why
Document 03.1, Fraud/AML layer: sanctions, PEP, adverse media, transactions, bank ownership and crypto rule scope are enabled only when a local legal
and commercial entitlement is demonstrated; no global background monitoring by default. (In the screening code this is called the legal basis and
the authorizing contract: the screening module never mentions plans or entitlements, so that what a customer pays for can never change a screening
outcome; `test_kyb_register.py` guards it.) Today the scheduled sanctions re-screen screens every open
case in every open country again whenever a list changes or a clear outcome lapses: that is ongoing monitoring, and nothing asked whether it was
authorized. PEP, adverse media, transaction, bank-ownership and crypto monitoring do not exist yet; when they are built they need the same gate.

## What changes
- **Domain** (`screening/domain/monitoring_scope.py`, pure). An approval for one country, one kind (SANCTIONS_ONGOING, PEP, ADVERSE_MEDIA,
  TRANSACTION_MONITORING, BANK_OWNERSHIP, CRYPTO_TRAVEL_RULE, CRYPTO_WALLET_SCREENING) and named sectors (or `ANY`), with the legal basis, the authorizing
  contract, dates, and two signatures (Legal, Compliance) over its hash. A crypto kind also needs its crypto-specific authorization (a MiCA CASP
  licence, a VASP registration). `decide` allows monitoring only under a usable approval; otherwise `MONITORING_NOT_APPROVED`, `SECTOR_NOT_COVERED`,
  or every problem of the covering approvals. An approval for one kind never covers another; a general AML approval never covers crypto.
- **Configuration** `backend/config/screening/monitoring-approvals.yaml`, loaded by `screening/infrastructure/monitoring_approvals.py`. Ships with NO
  approval (one example is a comment). A malformed row refuses the whole file.
- **The gate** (`screening/application/monitoring_gate.py`, exported by `screening.ports`): `require_monitoring` and `monitoring_allowed`. Switch
  `ZID_MONITORING_SCOPE`: `shadow` (default, logs), `enforce` (holds), `off` (refused outside local, test and dev); the API process and the worker
  both configure it.
- **Applied** to the scheduled sanctions re-screen (`passes.rescreen_pass`, new optional `monitoring_gate`; the worker passes it with kind
  SANCTIONS_ONGOING). A case outside an approval is held exactly like a case in a closed country: nothing screened, nothing appended, its last decision
  ages out, reported as `held_by_monitoring_scope`. Screening a company at onboarding is not background monitoring and is unchanged.
- **Operator preview** `GET /v1/regulatory/monitoring-check?country=&kind=&sector=`.

## Not in this change
- PEP, adverse media, transaction, bank-ownership and crypto monitoring themselves (P2, separately regulated). Each must call `require_monitoring`
  with its kind before it runs.
- The sector of a screening case: cases carry a country, not a sector, so the re-screen asks for `ANY`.

## Owner decisions (proposed values, none ratified)
1. Approvers: Legal and Compliance (the MLRO).
2. In `enforce`, an unapproved market's cases are held rather than screened, so their clear outcome lapses; that is the intended consequence of "no
   global background monitoring by default".
3. Default `shadow` until the owners list the approved markets; then `enforce`.
