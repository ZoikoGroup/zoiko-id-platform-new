# CR-054: Finance authority, customer commitment approval and the billing workspace (ZID-COM-CL-003, ZID-COM-CL-004, ZID-UX-COM-008 to 010), slice L-14b to L-14e

Status: open (built). The closures and wireframes are proposed and await founder approval. Every number in `config/commercial/finance-policy.yaml` is a proposal
and has no authority in a deployed environment until the Founder and the CFO sign the file.

## What changes
- **Zoiko internal roles are dated appointments** (commercial migration `0017`, `commercial.commercial_role_holder`): a person holds a role for a time, never
  appoints themselves, and one person cannot fill two slots of the same approval. Roles: AE, CCO, CFO, FOUNDER, GROUP_CFO, REVOPS, FINANCE_OPS_LEAD, CONTROLLER,
  DISPUTES_SPECIALIST, FINANCE_EXECUTOR. Customer-side authority is never held here.
- **Credit notes and pilot credits are requests, not edits** (`commercial/domain/finance.py`, `application/finance_service.py`). The facts are frozen under a hash.
  The tier comes from the exposure in USD minor units (credits for one organization add up over 90 days; a currency is converted at a pinned rate and rounded up).
  Each role the tier names signs that exact hash, each a different person and never the requester. A separate Finance Executor runs it once. At execution the
  signers' roles, the policy hash, the approval expiry and the legal payer are checked again; a change of any of them voids the approval.
- **A credit note** names its invoice, its evidence (a correction must) and what becomes of money already taken (refund, customer balance or settled outside the
  platform). A refund is a separate decision (Closure 05 is not in the supplied documents) and is not executed here. **A pilot credit** names its program and sponsor,
  has an end date inside the policy maximum, and never renews itself.
- **The signed policy lock.** In a deployed environment nothing is opened, approved or run until the Founder and the CFO, two different people, have signed the policy
  file's content hash (`finance_policy_signature`). `local` and `test` skip the lock. Changing one character makes the file unsigned again. Discount approvals
  (`approve_quote`) take the same lock outside direct mode.
- **Customer commitments** (migration `0018`, `commercial/domain/commitment.py`, `application/commitment_service.py`): the server derives a signed order's exposure
  (the higher of the first 12 months and the whole term, less what is already committed), places it in a tier (proposed 2,500 and 25,000 USD) and says who signs:
  the Owner or a Billing Admin with a delegated limit (tier 1); the Owner or a Procurement Approver with a limit (tier 2); two different people above that.
  Non-standard terms (uncapped liability, a residency commitment, a term over 36 months) wait for a Zoiko Controller or CFO review mark whatever the amount. Authority
  is read again at each signature and at activation. The Owner check is handed in from the tenant module (commercial cannot import tenant).
- **Billing workspace** (migration `0019`): `GET /v1/billing/overview` (state, one next action, payer, recent activity), a versioned billing profile with append-only
  history (`GET/PUT /v1/billing/profile`, `GET /v1/billing/profile/history`; the legal payer and tax country are never edited there), credit-note references on invoices, and the
  operator reads for eligibility (O01) and the finance overview (F01).
- **Routes.** Customer: `/v1/billing/commitment-requests` (list, ask, `:approve`, `:reject`, `:cancel`) and `/v1/billing/commitment-delegates` (list, give, `:revoke`);
  approve, reject, give and revoke each take a step-up proof bound to exactly that change. Operator (capability `commercial.finance.manage`): `/v1/commercial/finance/*`
  (overview, policy, `policy:sign`, appointments, requests, `:decide`, `:cancel`, `:execute`, commitment `:review`, eligibility). Request bodies use `target_tenant_id`, not `tenant_id`.
- **Switch:** `commitment.enforcement` in `finance-policy.yaml`: `OFF`, `REPORT_ONLY` (default; an activation without an approved commitment is recorded and let through)
  or `ENFORCED` (refused). The owners decide.
- **Web, customer** (Billing page): Overview, Commitments (request, sign with a passkey proof, delegates and their limits) and Billing profile tabs, and credit notes in the invoice drawer.
  **Web, team** (Team page): Roles and permissions (read from the published role matrix, `GET /v1/roles/matrix` and `/v1/tenant/me/permissions`) and Delegations (one register of
  billing grants and commitment limits, with revoke). **Operator console** (Commercial, Finance): overview F01, credit and pilot requests F02 and F03, policy and roles F04, eligibility O01.

## Not changed
No new action in the action catalogue, no change to the role matrix or the five roles. No money moves outside the existing credit-note posting path.

## Not built, and why
Refund execution through the provider (Closure 05 is not in the supplied documents); reconciliation and period close (no bank or ERP feed); dispute handling;
the hosted-session and webhook proofs P07 to P12; discount-band tests for CL03 beyond the existing lock. See the owner pack for the rulings that remain.

## Tests
`tests/unit/test_finance_rules.py` (7), `tests/unit/test_commitment_rules.py` (6), `tests/component/test_finance_authority_pg.py` (9), `tests/component/test_commitment_pg.py` (3),
`tests/component/test_billing_workspace_pg.py` (3); web `profile-logic.test.ts`, `commitment-logic.test.ts`, `roles-logic.test.ts`; operator `finance-logic.test.ts`.
Acceptance rows CL04-C01, C03 to C08, C10, C12 to C14, WF08-UI-01, WF09-OP-03, 04, 11 and WF10-C00-01, 05, 11, 12 are linked as in progress.
