"""Commercial boundaries on real routes (ZID-COM-CL-002, CL-003, CL-004; ZID-UX-COM-008 to 010).

One payer's invoices are read by that payer's people only, a document stops opening the moment the right to read it is taken away, and no customer
role (Owner, Billing Admin, invoice reader) reaches a single internal finance, discount, credit or operator route, whatever identifier it guesses."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi.testclient import TestClient
from onboarding_fixture import ISS, onboard
from test_invoicing_pg import rated_month, settings
from test_role_matrix_pg import _seed_role
from zid_platform.main import create_app


def _contexts(app: Any) -> Any:
    for r in app.routes:
        if type(r).__name__ == "_IncludedRouter":
            yield from r.effective_route_contexts()
        elif getattr(r, "methods", None) and getattr(r, "dependant", None) is not None:
            yield r


def _issued_invoice(rt_url: str, tenant: str) -> str:
    """An invoice issued to `tenant` by two operators, the way the operator console does it."""
    from operator_fixture import activate_grant, operators
    from test_invoicing_pg import LAST, uid
    from zid_platform.modules.operator_access.domain.model import OperatorRole

    alice, dave, bob, carol = uid(), uid(), uid(), uid()
    ops = operators(rt_url, {alice: {OperatorRole.REQUESTER}, dave: {OperatorRole.REQUESTER}, bob: {OperatorRole.APPROVER}, carol: {OperatorRole.APPROVER}})
    asyncio.run(rated_month(rt_url, tenant))
    op_app = create_app(settings(rt_url, operator_tenant_id=ops.tenant, operator_client_ids=(ops.client,)), keyring=ops.ring)
    with TestClient(op_app, base_url=ISS) as api:
        base = f"/v1/commercial/tenants/{tenant}"
        scope = {"capability": "commercial.billing.manage", "resource_scope": f"tenant:{tenant}", "target_tenant_id": tenant}
        activate_grant(api, ops, alice, (bob, carol), **scope)
        activate_grant(api, ops, dave, (bob, carol), **scope)
        made = api.post(f"{base}/invoices", json={"month": f"{LAST:%Y-%m}"}, headers=ops.bearer(alice))
        assert made.status_code == 201, made.text
        iid = str(made.json()["invoice_id"])
        done = api.post(f"{base}/invoices/{iid}:issue", json={"payment_terms_days": 30, "allow_zero_tax": True}, headers=ops.bearer(dave))
        assert done.status_code == 200, done.text
        return iid


def test_an_invoice_is_read_by_its_own_payer_only_and_a_revoked_reader_loses_the_document(rt_url: str) -> None:
    """CL02-P02: only the organization's own invoices; WF08-UI-07: a document is payer-scoped and stops opening when the reader's right is
    taken away; CL04-C16 and CL03-T15: another organization's invoice, its credit amounts and its document cannot be reached by guessing the id."""
    mine = onboard(rt_url, extra_actions=["commercial.read", "commercial.payment.manage"], extra_resources=["billable:*"])
    other = onboard(rt_url, extra_actions=["commercial.read", "commercial.payment.manage"], extra_resources=["billable:*"])
    _seed_role(rt_url, mine.tenant, mine.second, "ADMIN", create=False)
    iid = _issued_invoice(rt_url, mine.tenant)
    with TestClient(create_app(settings(rt_url), keyring=mine.ring), base_url=ISS) as http, TestClient(create_app(settings(rt_url), keyring=other.ring), base_url=ISS) as foreign:
        own, reader = mine.bearer(mine.owner), mine.bearer(mine.second)
        assert [i["invoice_id"] for i in http.get("/v1/invoices", headers=own).json()["items"]] == [iid]
        assert http.get(f"/v1/invoices/{iid}/document", headers=own).status_code == 200

        # the other organization's owner, who holds every billing key of their own, reaches none of it by naming the id
        theirs = other.bearer(other.owner)
        assert foreign.get("/v1/invoices", headers=theirs).json()["items"] == []
        for method, path in (
            ("get", f"/v1/invoices/{iid}"),
            ("get", f"/v1/invoices/{iid}/document"),
        ):
            r = getattr(foreign, method)(path, headers=theirs)
            assert r.status_code in (404, 409), (path, r.status_code, r.text)
            assert "outstanding" not in r.text and "credited" not in r.text
        assert foreign.post(f"/v1/invoices/{iid}/pay", json={"idempotency_key": "guess-0001"}, headers=theirs).status_code in (404, 501)

        # a person the Owner gave invoice reading to reads the document; when the Owner takes it away the very next request is refused
        grant = http.post("/v1/tenant/billing-grants", json={"grantee_principal_id": mine.second, "bundle": "INVOICE_READER", "reason": "audit"}, headers=own)
        assert grant.status_code == 201, grant.text
        assert http.get(f"/v1/invoices/{iid}/document", headers=reader).status_code == 200
        assert http.post(f"/v1/tenant/billing-grants/{grant.json()['grant_id']}:revoke", headers=own).status_code == 200
        gone = http.get(f"/v1/invoices/{iid}/document", headers=reader)
        assert gone.status_code == 403 and gone.json()["error"]["code"] == "BILLING_PERMISSION_REQUIRED"


def test_no_customer_role_reaches_an_internal_finance_discount_credit_or_operator_route(rt_url: str) -> None:
    """WF09-OP-01 and OP-02: the internal finance routes cannot be reached from the customer's billing, by any customer role, on list, detail, export or
    action; WF10-C00-18 and WF08-UI-16: a customer role grant exposes no Zoiko internal case or other product's ledger; CL03-T04: a customer Owner or Billing
    Admin cannot create or approve a discount; CL02-P16: upgrades, discounts, refunds and credits never leak into a billing role."""
    o = onboard(rt_url, extra_actions=["commercial.read", "commercial.payment.manage"], extra_resources=["billable:*"])
    _seed_role(rt_url, o.tenant, o.second, "ADMIN", create=False)
    app = create_app(settings(rt_url), keyring=o.ring)
    internal = sorted(
        {(m, c.path) for c in _contexts(app) if c.path.startswith(("/v1/commercial/", "/operator/", "/ops/", "/platform/", "/v1/lockbox-requests")) for m in c.methods - {"HEAD", "OPTIONS"}}
    )
    assert len(internal) > 100, "the test is not looking at the internal routes"
    with TestClient(app, base_url=ISS) as api:
        own = o.bearer(o.owner)
        grant = api.post("/v1/tenant/billing-grants", json={"grantee_principal_id": o.second, "bundle": "BILLING_ADMIN", "reason": "billing"}, headers=own)
        assert grant.status_code == 201, grant.text
        bearers = {"owner": own, "billing_admin": o.bearer(o.second)}
        reached: list[str] = []
        for role, headers in bearers.items():
            for method, template in internal:
                path = template
                for part in [p for p in template.split("/") if p.startswith("{")]:
                    path = path.replace(part, str(uuid.uuid4()))
                r = api.request(method, path, headers=headers, json={} if method in ("POST", "PUT", "PATCH") else None)
                if r.status_code < 400 or r.status_code >= 500:
                    reached.append(f"{role} {method} {template} -> {r.status_code}")
        assert not reached, "customer roles reached internal routes:\n" + "\n".join(reached[:20])
