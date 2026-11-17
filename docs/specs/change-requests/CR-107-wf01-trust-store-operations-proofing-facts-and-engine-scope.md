# CR-107: WF01 reconciliation: a governed CSCA trust store, WF01's assertion vocabulary and result events, engine mode and country scope, attempt caps and a usage report (ZID-DTV-WF-01; ZID-DTV-REG-001 GC-02, GC-05, DTV-06, DTV-18; builds on CR-095, CR-102)

Status: **built on the engineering owner's instruction of 2026-11-17; sign-offs UNSIGNED.** Nothing here makes an identity engine, a chip trust list or a lab result appear: it makes the platform refuse to use them until a person has approved them, and say so in words a relying party cannot misread.

WF01 is RETAINED as the single verification control plane (register section 06). Its route names (`/v1/identity-proofing/...`) are illustrative, so no second router was created: the existing verification module is the implementation, and `docs/qa/wf01-identity-verification-matrix.md` maps every WF01 story and test vector onto it.

## What was built

### G1: the governed CSCA trust store (WF01 section 13, screen O2; WF01-008; T05, T06, T26)

Before this, the trust store was a folder (`config/trust/csca/`) and an operator command-line tool that wrote files. Importing a list was the same act as trusting it.

- **A package** (`evidence.trust_package`, migration `evidence_0010`, row-level security): one imported master list (the bytes, so validation can be repeated), where it came from (URI, SHA-256 of the bytes), the anchors the stager confirmed out of band **and typed back as fingerprints** (the request is refused if the fingerprints do not match the anchors supplied), who staged it, and, after validation, its certificates and a manifest digest over the sorted (country, fingerprint) pairs.
- **Lifecycle** (`evidence/domain/trust_store.py`, pure rules; `evidence/application/trust_store.py`): `STAGED` → validate → `VALIDATED` or `REJECTED` (a list that does not verify against its anchors is never a candidate) → approve → `APPROVED` → activate → `ACTIVE`. Activating another package makes the previous one `SUPERSEDED`. A rollback restores the most recently active superseded package (or a named one) and marks the current one `ROLLED_BACK`; a retire stops trusting the active package.
- **Two-person rule**: the person who staged a package can neither approve nor activate it. The service enforces it and so do two CHECK constraints in the database. A rejection, a rollback and a retire need a reason.
- **At most one ACTIVE package** per tenant (a partial unique index).
- **The store governs once it has been used.** Until any package has ever been activated for a tenant the deployment's csca folder applies exactly as before. After that the active package is the whole truth, and an empty active set (after a retire) trusts nothing: a chip is INDETERMINATE. Both chip routes use this (`POST /v2/documents/chip-verify`, `POST /v2/self-run/verifications/{id}/chip`) and the first now reports which trust it used.
- **Lab fixture**: `POST .../packages/{id}:check-chip` runs a chip against one package, staged or not, without trusting it for anyone (`trusted_for_production` says whether it is active).
- **Routes** (`/v2/trust-store/csca/...`, `evidence/api/v2_trust_store.py`): stage, validate, check-chip, approve, reject, activate, rollback, retire, list, read, active. Responses never carry the list bytes, the anchor bodies or any certificate body.
- **Audit**: every change is an outbox event with `evidence_class` E3 and the actor (`evidence.trust_package.staged|validated|rejected|approved|activated|superseded|rolled_back|retired`), which the evidence module's own ingest turns into a protected audit record.
- **Actions**: `evidence.trust.propose` (stage, validate, check-chip) and `evidence.trust.approve` (approve, reject, activate, rollback, retire), both R3/E3.

### G2: WF01's assertion vocabulary and result events (WF01 sections 7, 14, 15; WF01-001, WF01-013)

A view over the recorded decision (`verification/domain/proofing.py`), not a second state machine. `decide()` still decides.

- Seven of WF01's nine assertions are derived from recorded facts: DOCUMENT_COLLECTED, DOCUMENT_REVIEWED, DOCUMENT_VALIDATED, DOCUMENT_CHIP_CRYPTO_VALID, PRESENCE_CONFIRMED, FACE_MATCH_CONFIRMED, IDENTITY_VERIFIED_FOR_SCOPE. The two COMPANY_* assertions belong to company verification (WF02) and are never derived here.
- **DOCUMENT_CHIP_CRYPTO_VALID only when this platform passively authenticated the chip.** A provider saying it read a chip is not that.
- **IDENTITY_VERIFIED_FOR_SCOPE only when** the session is VERIFIED, every claim the pinned profile requires is verified from accepted evidence, and either the binding and liveness the profile demands are present or distinct reviewers approved it through the profile's review path (also recorded as DOCUMENT_REVIEWED).
- Each check is `pass`, `fail`, `indeterminate` or `not_requested`; `not_requested` only when the profile did not ask. States are mapped to WF01's names (CAPTURE_IN_PROGRESS, PROVIDER_PENDING, RESUBMIT_REQUIRED, MANUAL_REVIEW, VERIFIED_FOR_SCOPE, REJECTED, INDETERMINATE ...).
- **`GET /verifications/{id}/proofing`**: the facts, the pinned policy and the scope, nothing else (no evidence, no claim value, no score).
- **Events** `verification.proofing.completed` (scope, assurance facts, evidence hashes, provider refs, policy hash, region, expiry; only when IDENTITY_VERIFIED_FOR_SCOPE holds) and `verification.proofing.indeterminate` (missing method, failure class, retriable, next action; for a chip nobody can judge, a check that could not be made, a request for the evidence again). They are the existing outcome made explicit: `verification.completed` is unchanged.
- A bug the tests found on the way: "a person reviewed this" is read from an approved review case, not from `pending_result` (self-run sets that too).

### G3: two reviewers for identity cases

Already built and tested before this change (`review_approvals` 1 to 3 on the profile, one decision per reviewer, `REVIEWER_IS_SUBJECT`, `REVIEW_DUPLICATE_REVIEWER`, a reviewer cannot make a missing claim appear). The earlier matrix said otherwise: that was wrong. This change adds the check that the proofing facts agree with it (`test_verification_pg.py`).

### G4: engine mode, attempt cap, usage report (WF01 O1, T02, WF01-011, WF01-014)

- **Engine mode** (`config/self-run-verification/self-run.yaml`): `mode` is `DISABLED` (default), `LAB_ONLY` or `APPROVED_FOR_SCOPE`. A LAB_ONLY engine is never routed to a live decision, even with a complete file and the flag on. APPROVED_FOR_SCOPE needs the whole evaluation record **and `scope.countries`**, and the engine then runs only for sessions in those countries; elsewhere it is off for that session and the reason `SELF_RUN_ENGINE_NOT_APPROVED_FOR_COUNTRY` says why. `enabled: true` with no mode is refused: whoever switches an engine on must say how far it may go. (This tightens the earlier gate; the shipped file is still off.)
- **Attempt cap**: a profile may set `max_attempts` (1 to 20). One person's starts under that profile in 24 hours count (every status); at the cap a start is refused with 429 `VERIFICATION_ATTEMPTS_EXCEEDED`, which is not a verdict about the person.
- **Licence and component register** (WF01-011): `config/self-run-verification/engine-register.yaml` lists each library and pretrained model an engine may be built from (DeepFace, Silent-Face, InsightFace, JMRTD, NFCPassportReader, Tesseract) with its code licence, model licence, commercial-use answer, whether the model licences were read, the date a person verified them and where its dependency list is kept. An APPROVED_FOR_SCOPE engine must name its `engine.components` and every one must be in the register with `commercial_use: YES`, `model_licence_confirmed: true` and a verification date. **The shipped register verifies nothing** (every date is null, InsightFace is marked non-commercial), so no engine can go live from it; the facts in it are what each project's own documentation said and a person confirms them.
- **Monthly spend budget** (WF01-014): `PUT/GET/DELETE /verification-budget` (action `verification.budget.manage`, migration `verification_0021`). One row per company: a limit and a currency. No default amount and no placeholder. Spend is read from the vendor cost events (commercial) in the budget's own currency only; costs in another currency are reported as `other_currencies`, never converted. At or over the limit no new verification starts that month (429 `VERIFICATION_BUDGET_EXCEEDED`); a session already started finishes. A limit of zero starts nothing. `DELETE` switches the budget off (`enabled` false) and keeps the last limit and who set it, because the runtime database role never deletes rows.
- **Failover visibility** (WF01-012): the usage report counts sessions that more than one provider was asked about and how many of them verified. The rule that a failover never lowers assurance already existed and is tested.
- **Usage report**: `GET /verification-usage?since&until` (at most 366 days): sessions by provider, status and outcome, review cases by status, repeat attempts. Aggregates only. What an attempt cost is the vendor cost report's job (commercial, CR-029); the two together let Finance reconcile without seeing an applicant's records.

## The rules (each has a test)

1. A package is trusted only when it is ACTIVE; staged, validated and approved packages are not.
2. A list that does not verify against its anchors is REJECTED and can never be approved.
3. The stager cannot approve or activate; the database refuses it too.
4. After a retire the store trusts nothing; before first use the folder applies as before; another tenant is untouched.
5. A rollback restores the earlier package and the earlier trust is used again; a rolled-back package is never a rollback target.
6. IDENTITY_VERIFIED_FOR_SCOPE is never held by a session that is not VERIFIED, lacks a required claim, or lacks the binding the profile demands without an approved review.
7. DOCUMENT_CHIP_CRYPTO_VALID needs the platform's own passive authentication.
8. An indeterminate or pending check is never `pass` and is announced as `indeterminate`.
9. A LAB_ONLY engine, or an engine outside its approved countries, never binds a holder.
10. A profile's attempt cap refuses at the cap and nothing else.
11. An engine runs live only when every component it is built from is in the register with commercial use and its model licences confirmed by a person.
12. A budget is money as a decimal string, never a float; at or over the limit no new verification starts; another currency is never converted.

## What it does not do

- It does not import or trust a single real country list: `config/trust/csca/` and the store ship empty, so every chip is still INDETERMINATE until a person stages, approves and activates real lists.
- No applicant screens (A1 to A5), no capture SDK and no phone app. The mobile contract is `docs/specs/self-run-mobile-client-contract.md`.
- No provider contract, lab evaluation, DPIA or fairness result. The engine gate still needs them and still cannot check that they are honest.
- The trust store is per tenant, as every evidence table is. A platform-wide default list is still the config folder.
- The usage report has no unit prices; the cost report has them. The budget uses the same cost events, so a verification with no priced rate costs nothing against it until a rate exists.
- The budget's over-the-limit path is proved with a zero limit and with the rule on injected spend; no test seeds a real supplier cost event.
- The register is a record, not a licence check: nobody has read any licence yet.

## Owner decisions still needed

1. Who holds the trust custodian role (`evidence.trust.propose`) and who the independent approver (`evidence.trust.approve`) in each tenant that uses chips (Security PKI owner and Legal; WF01 decision D).
2. Whether the trust store should become a platform-wide record instead of per tenant.
3. The countries a first engine may be approved for, the default `max_attempts` per profile (Identity Risk, Privacy), and each company's monthly budget and currency (Finance). Nobody has set any of these.
5. Who reads each component's licence and model licences and signs the register (Legal).
4. Whether WF01's event names should replace or sit beside `verification.completed` for relying parties.

## Evidence

`backend/app/tests/unit/test_trust_store_rules.py`, `backend/app/tests/component/test_trust_store_pg.py`, `backend/app/tests/unit/test_proofing_facts.py`, `backend/app/tests/component/test_proofing_facts_pg.py`, the engine tests at the end of `backend/app/tests/unit/test_self_run_verification.py`, and the review check added to `backend/app/tests/component/test_verification_pg.py`.

## Sign-off (UNSIGNED)

| Role | Name | Date | Decision |
|---|---|---|---|
| Security / PKI owner | | | |
| Privacy / DPO | | | |
| Identity Risk | | | |
| Legal | | | |
