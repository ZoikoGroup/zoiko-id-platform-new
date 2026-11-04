# CR-057: Commercial assurance closure (ZID-COM-CL-002 to CL-004, ZID-UX-COM-008 to 010)

Status: open (built). This closes the rows of the commercial closures and wireframes 08 to 10 that engineering can close, by tests, and fixes what the tests found.

## What changes
- **Stacked promotions are judged together** (commercial migration `0020`, `domain/orders.effective_discount`): a quote line can carry promotions on top of its quoted discount. Approval authority
  follows the effective discount (1 minus the product of what is kept), so two 5% promotions are 9.75% and need the CCO, and splitting one concession into parts never moves it to a band that approves
  less. The promotions are part of the digest an approval binds; the quote lines stay immutable. Operator quote lines accept `promotions` (at most five, each 0 to under 1); a 100% promotion or one on a
  pass-through line is refused.
- **Billing grants are idempotent** (`tenant/application/billing_access.give`): a repeat of a grant still in force is the same grant (a double click, a retry, two administrators at once); the same bundle with a
  different end date is a 409 `BILLING_GRANT_EXISTS` the Owner resolves by taking the first away.
- **A customer commitment cannot be decided on an unsigned policy in a deployed environment** (`commitment_service.decide`): the lock already existed but raised a finance error the API did not catch, so
  a deployed environment would have answered 500. It is now the coded refusal `POLICY_NOT_SIGNED`; sandbox environments skip the lock, as before.
- **The web proxy allows the role gallery and the verification level** (`/v1/roles/`, `/v1/verification/`). Found by the browser run: the screens showed "This page isn't available" on a real stack while
  every mocked test passed. The allowlist test now pins them with the billing and billing-grant prefixes.
- **The team page's People tab** no longer puts a text node directly inside an ordered list (an axe WCAG 1.3.1 failure found by the browser run).
- **Reconciliation can be limited to one tenant** (`reconcile_delayed(only_tenant=...)`), which keeps the polling test independent of other tests' leftovers.

## Tests added (each names the acceptance rows it supports)
`test_commercial_boundaries_pg.py` (invoices belong to one payer; no customer role reaches any of the 100-plus internal routes), `test_role_invariants_pg.py` (step-up on grants, idempotent grants,
replayed and forged role claims), `test_discount_authority_pg.py` (who may release which band, revoked appointments, expired and spent approvals, append-only approvals, one signature per quote),
`test_commitment_lock_pg.py` (the policy lock; step-up on commitment approvals and delegations), `test_finance_classification_pg.py` (credit note, pilot credit and refund are separate), unit
`test_billing_no_card_data.py` (no request the cell accepts has a field for a card or an amount), `test_order_rules.py` (stacking, rounding, floors), and the browser spec
`frontend/tests/e2e/billing-team-a11y.spec.ts` (WCAG 2.2 AA automated checks at four viewports on the billing, team and verification screens). The browser spec is automated evidence, not a screen-reader run.

## Acceptance rows linked
About 50 rows across CL02, CL03, CL04, WF08 to WF10 and EDR09, all as in progress (none verified).

## Still open, and why
Refund execution, disputes and reconciliation and period close were built afterwards in [CR-058](CR-058-refunds-disputes-reconciliation-and-period-close.md) from Wireframe 09; keyboard and screen-reader runs of the operator finance
console and of the step-up dialog on real devices (CL03-T18, CL04-C18, WF09-OP-12, EDR09-T12); supplier and counsel items (EDR04-07, EDR05-01, EDR05-04, EDR06-07).
