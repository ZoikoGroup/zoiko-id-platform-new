"""Billing access on real routes (ZID-COM-CL-002, ZID-UX-COM-010; slice L-14): an Administrator who passes every other check still has no
billing access; the Owner gives a bundle, and the grant, its revoke and every refusal leave redacted audit records; a revoke is felt on the
next request; grants cannot be edited or deleted, and one organization never sees another's."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_role_matrix_pg import _seed_role
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.settings import Settings


def _app(rt_url: str, o: Any) -> Any:
    return create_app(
        Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test", cell_id="uk-1"),
        keyring=o.ring,
    )  # fmt: skip


def _audit(rt_url: str, tenant: str) -> list[Any]:
    async def rows() -> list[Any]:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            return list(
                (
                    await c.execute(
                        text(
                            "SELECT event_type, outcome, reason_code, evidence_class::text AS ec, body FROM evidence.audit_record "
                            "WHERE tenant_id = :t AND event_type LIKE 'tenant.billing.access.%' ORDER BY created_at"
                        ),
                        {"t": uuid.UUID(tenant)},
                    )
                ).all()
            )

    return asyncio.run(rows())


def _onboard(rt_url: str) -> Any:
    return onboard(
        rt_url,
        extra_actions=["commercial.read", "commercial.payment.manage"],
        extra_resources=["billable:*"],
    )


def test_an_administrator_has_no_billing_access_until_the_owner_gives_it(rt_url: str) -> None:
    """CL02-P01 (an Admin with no billing grant cannot read an invoice or pay), P03 (a delegated reader cannot reach the method or
    checkout), P05 (only the Owner grants, never to themselves, never to a stranger), P13 (a revoke denies on the next request)."""
    o = _onboard(rt_url)
    _seed_role(rt_url, o.tenant, o.second, "ADMIN", create=False)
    stranger = str(uuid.uuid4())
    with TestClient(_app(rt_url, o)) as api:
        own, adm = o.bearer(o.owner), o.bearer(o.second)

        # the Owner has the baseline; the Administrator, who passes the protected-action check, has nothing
        assert api.get("/v1/invoices", headers=own).status_code == 200
        r = api.get("/v1/invoices", headers=adm)
        assert (
            r.status_code == 403 and r.json()["error"]["code"] == "BILLING_PERMISSION_REQUIRED"
        ), r.text
        assert r.json()["error"]["details"][0]["issue"] == "billing.invoice.read"
        for method, path in (
            ("get", "/v1/billing/state"),
            ("get", "/v1/billing/payment"),
            ("post", "/v1/billing/payment/setup"),
            ("post", f"/v1/invoices/{uuid.uuid4()}/pay-online"),
            ("post", f"/v1/invoices/{uuid.uuid4()}/pay"),
        ):
            r = getattr(api, method)(path, headers=adm)
            assert (
                r.status_code == 403 and r.json()["error"]["code"] == "BILLING_PERMISSION_REQUIRED"
            ), (path, r.text)
        assert api.get("/v1/tenant/billing-access/me", headers=adm).json()["permissions"] == []
        assert len(api.get("/v1/tenant/billing-access/me", headers=own).json()["permissions"]) == 10

        # the Administrator cannot give billing access, to anyone, and cannot read who has it
        body = {"grantee_principal_id": o.second, "bundle": "BILLING_ADMIN", "reason": "self"}
        r = api.post("/v1/tenant/billing-grants", json=body, headers=adm)
        assert r.status_code == 403 and r.json()["error"]["code"] == "BILLING_PERMISSION_REQUIRED"
        assert api.get("/v1/tenant/billing-grants", headers=adm).status_code == 403

        # the Owner does not give it to themselves, to a stranger, or a bundle that does not exist
        def give(grantee: str, bundle: str) -> Any:
            return api.post(
                "/v1/tenant/billing-grants",
                json={
                    "grantee_principal_id": grantee,
                    "bundle": bundle,
                    "reason": "monthly invoices",
                },
                headers=own,
            )

        assert give(o.owner, "BILLING_ADMIN").json()["error"]["code"] == "BILLING_SELF_GRANT"
        assert (
            give(stranger, "BILLING_ADMIN").json()["error"]["code"] == "BILLING_GRANTEE_NOT_MEMBER"
        )
        assert give(o.second, "OWNER").json()["error"]["code"] == "BILLING_BUNDLE_UNKNOWN"
        assert (
            api.get("/v1/invoices", headers=adm).status_code == 403
        )  # none of that opened anything

        # an invoice reader reads invoices and nothing else
        made = give(o.second, "INVOICE_READER")
        assert made.status_code == 201, made.text
        grant_id = made.json()["grant_id"]
        assert api.get("/v1/invoices", headers=adm).status_code == 200
        assert api.get("/v1/billing/state", headers=adm).status_code == 403
        assert api.get("/v1/billing/payment", headers=adm).status_code == 403
        assert api.post("/v1/billing/payment/setup", headers=adm).status_code == 403
        assert api.post(f"/v1/invoices/{uuid.uuid4()}/pay", headers=adm).status_code == 403
        assert sorted(
            api.get("/v1/tenant/billing-access/me", headers=adm).json()["permissions"]
        ) == [
            "billing.invoice.download",
            "billing.invoice.read",
        ]
        assert (
            api.post("/v1/tenant/billing-grants", json=body, headers=adm).status_code == 403
        )  # a grant cannot grant

        # the Owner sees the grants; revoking is felt on the very next request
        listed = api.get("/v1/tenant/billing-grants", headers=own).json()["items"]
        assert [g["grant_id"] for g in listed] == [grant_id] and listed[0]["revoked_at"] is None
        assert (
            api.post(f"/v1/tenant/billing-grants/{grant_id}:revoke", headers=adm).status_code == 403
        )
        assert (
            api.post(f"/v1/tenant/billing-grants/{grant_id}:revoke", headers=own).status_code == 200
        )
        assert api.get("/v1/invoices", headers=adm).status_code == 403
        assert (
            api.post(f"/v1/tenant/billing-grants/{grant_id}:revoke", headers=own).json()["error"][
                "code"
            ]
            == "BILLING_GRANT_NOT_FOUND"
        )
        assert api.get("/v1/tenant/billing-grants", headers=own).json()["items"][0]["revoked_at"]

        # a Billing Admin can pay and manage the method, still cannot grant, and the Owner stays the only one who can
        assert give(o.second, "BILLING_ADMIN").status_code == 201
        assert api.get("/v1/billing/state", headers=adm).status_code == 200
        assert api.get("/v1/billing/payment", headers=adm).status_code in (
            200,
            501,
        )  # 501: no processor in the test app
        assert api.post("/v1/tenant/billing-grants", json=body, headers=adm).status_code == 403
        assert (
            api.get("/v1/tenant/billing-grants", headers=adm).status_code == 200
        )  # billing.audit.read

    # the record: each grant, revoke and refusal, E3, ids and codes only
    rows = _audit(rt_url, o.tenant)
    events = [(r.event_type.rsplit(".", 1)[1], r.outcome, r.reason_code) for r in rows]
    assert ("granted", "SUCCESS", None) in events and ("revoked", "SUCCESS", None) in events
    assert ("granted", "DENIED", "BILLING_SELF_GRANT") in events
    assert ("granted", "DENIED", "BILLING_GRANTEE_NOT_MEMBER") in events
    assert ("decision", "DENIED", "NO_BILLING_GRANT") in events
    assert all(r.ec == "E3" for r in rows)
    flat = " ".join(str(r.body) for r in rows)
    assert (
        "monthly invoices" not in flat
    )  # the reason text is kept in the grant, not copied into the evidence


def test_a_grant_is_never_edited_or_deleted_and_never_crosses_organizations(rt_url: str) -> None:
    """The database refuses what the service would never do: widen a grant, delete one, or carry the right to grant; and row-level
    security keeps one organization's grants from another."""
    a, b = _onboard(rt_url), _onboard(rt_url)
    _seed_role(rt_url, a.tenant, a.second, "ADMIN", create=False)
    with TestClient(_app(rt_url, a)) as api:
        gid = api.post(
            "/v1/tenant/billing-grants",
            json={
                "grantee_principal_id": a.second,
                "bundle": "BILLING_VIEWER",
                "reason": "read only",
            },
            headers=a.bearer(a.owner),
        ).json()["grant_id"]

    async def attempt(tenant: str, sql: str) -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await c.execute(text(sql), {"g": uuid.UUID(gid)})

    for sql in (
        "UPDATE platform.billing_grant SET permissions = ARRAY['billing.payment.initiate'] WHERE grant_id = :g",
        "UPDATE platform.billing_grant SET bundle = 'BILLING_ADMIN' WHERE grant_id = :g",
        "DELETE FROM platform.billing_grant WHERE grant_id = :g",
    ):
        with pytest.raises(DBAPIError):
            asyncio.run(attempt(a.tenant, sql))

    async def visible(tenant: str) -> int:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            return len((await c.execute(text("SELECT 1 FROM platform.billing_grant"))).all())

    assert asyncio.run(visible(a.tenant)) == 1 and asyncio.run(visible(b.tenant)) == 0
    # the other organization's Owner cannot revoke it by id
    with TestClient(_app(rt_url, b)) as other:
        r = other.post(f"/v1/tenant/billing-grants/{gid}:revoke", headers=b.bearer(b.owner))
        assert r.status_code == 404 and r.json()["error"]["code"] == "BILLING_GRANT_NOT_FOUND"
    with TestClient(_app(rt_url, a)) as api:
        assert (
            api.post(
                f"/v1/tenant/billing-grants/{gid}:revoke", headers=a.bearer(a.owner)
            ).status_code
            == 200
        )
    with pytest.raises(DBAPIError):  # a revoked grant is final
        asyncio.run(
            attempt(
                a.tenant,
                "UPDATE platform.billing_grant SET revoked_at = NULL, revoked_by = NULL WHERE grant_id = :g",
            )
        )
