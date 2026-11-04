# CR-050: Billing permissions and delegated billing access (ZID-COM-CL-002, ZID-UX-COM-010), slice L-14a

Status: open (built). The closure and the wireframe are proposed and await founder approval.

## What changes
- **A billing permission layer in front of every customer billing route.** The ten CL-02 keys (`billing.overview.read`, `billing.invoice.read`,
  `billing.invoice.download`, `billing.payment.initiate`, `billing.payment_method.read_masked`, `billing.payment_method.manage`, `billing.profile.manage`,
  `billing.subscription.request_change`, `billing.access.delegate`, `billing.audit.read`) are decided afresh on every request from the person's roles and
  grants. The Owner holds every key; nobody else holds one without a live grant; an Administrator has none by being one. The existing protected-action
  check still runs behind it, so this only narrows who reaches it.
- **Billing Admin and Billing Viewer are bundles of keys, not tenant roles.** The five roles of ZID-ENG-DEC-007 do not change, so the open ruling on role 11 does
  not block this slice. A third bundle, Invoice reader, is the "narrow invoice-read by delegation" of CL-02.
- **Grants** (`tenant/domain/billing_access.py`, tenant migration `0012`, table `platform.billing_grant`): only an Owner gives or takes away a grant, never to
  themselves, only to an active member; a grant never carries `billing.access.delegate`; the permission list is frozen on the row; the row is immutable except
  to be revoked once (a trigger refuses every other edit and every delete); row-level security keeps one organization from another's.
- **Routes** (`tenant/api/billing_access.py`): `GET /v1/tenant/billing-access/me`, `GET /v1/tenant/billing-grants` (needs `billing.audit.read`),
  `POST /v1/tenant/billing-grants` and `POST /v1/tenant/billing-grants/{id}:revoke` (Owner only; step-up classified as "grant, expand, transfer or revoke
  high-impact delegated authority", S2). Each grant, revoke and refusal writes an E3 audit record (ids, codes and the key; never the reason text).
- **The gate.** Commercial cannot import tenant, so `commercial/application/billing_gate.py` holds a checker that `main` hands in at start-up
  (`_billing_access`); until it is handed one every billing route is refused with 503, never opened.
- **Routes gated:** invoices (read, document), billing state, orders, preview, plan requests (read, request, withdraw), payment (read masked, setup, complete,
  pay, pay online). Usage, budgets and entitlement reads are not in the CL-02 list and are not gated here.
- **Switch:** `backend/config/roles/role-policy.yaml` `billing_access_enforcement`: `ENFORCED` (default; anything but `REPORT_ONLY` enforces) or `REPORT_ONLY`
  (administrators still get through, and each refusal is recorded), for the weeks before the owners rule.
- **Web:** the Billing page shows only the tabs a person's permissions allow and a new Billing access tab (own permissions; for an Owner, grant and revoke).

## Not changed
No new action in the action catalogue, no new outbox event (the audit record is the evidence), no change to the role matrix.

## Tests
`tests/unit/test_billing_access.py` (14), `tests/component/test_billing_access_pg.py` (2); acceptance rows CL02-P01, P03, P05, P06, P13, WF10-C00-02, 03, 04, 08, 09,
14, 16 and WF08-UI-03, 04 are linked as in progress.
