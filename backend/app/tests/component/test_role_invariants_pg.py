"""Role and billing-grant invariants on real routes (ZID-UX-COM-010 C00; ZID-ENG-DEC-007, ZID-ENG-DEC-009).

A sensitive grant needs a strong proof and a failed proof activates nothing; a repeated grant is one grant; a role is read afresh on every request, so a
replayed token or a claim the client invents never creates authority, and a revoked member's token does nothing on its next use."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from sqlalchemy import text
from test_role_matrix_pg import _seed_role
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.settings import Settings


def _app(rt_url: str, o: Any) -> Any:
    return create_app(
        Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test", cell_id="uk-1"),
        keyring=o.ring,
    )  # fmt: skip


def _grants(rt_url: str, tenant: str) -> int:
    async def run() -> int:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            return int(
                (await c.execute(text("SELECT count(*) FROM platform.billing_grant"))).scalar_one()
            )

    return asyncio.run(run())


def test_a_sensitive_grant_needs_a_strong_proof_and_a_failed_proof_activates_nothing(
    rt_url: str,
) -> None:
    """C00-10: where step-up is enforced a billing grant without a proof, or with one that is not valid, is refused and no grant exists afterwards."""
    o = onboard(
        rt_url,
        extra_actions=["commercial.read", "commercial.payment.manage"],
        extra_resources=["billable:*"],
    )
    _seed_role(rt_url, o.tenant, o.second, "ADMIN", create=False)
    app = _app(rt_url, o)
    app.state.step_up_enforcement = "enforce"
    with TestClient(app) as api:
        own = o.bearer(o.owner)
        body = {"grantee_principal_id": o.second, "bundle": "BILLING_ADMIN", "reason": "billing"}
        refused = api.post("/v1/tenant/billing-grants", json=body, headers=own)
        assert (
            refused.status_code == 403 and refused.json()["error"]["code"] == "STEP_UP_REQUIRED"
        ), refused.text
        for bad in ("not-a-proof", str(uuid.uuid4())):
            r = api.post(
                "/v1/tenant/billing-grants", json=body, headers={**own, "x-step-up-proof": bad}
            )
            assert r.status_code == 403 and r.json()["error"]["code"].startswith("STEP_UP"), r.text
        assert _grants(rt_url, o.tenant) == 0, "a refused grant must leave nothing behind"
        assert (
            api.get("/v1/invoices", headers=o.bearer(o.second)).status_code == 403
        )  # and the person gained no access


def test_a_repeated_grant_is_one_grant_and_a_different_end_date_is_a_conflict(rt_url: str) -> None:
    """C00-13: a double click, a retry or two administrators at once do not create two grants; changing an end date is explicit."""
    o = onboard(
        rt_url,
        extra_actions=["commercial.read", "commercial.payment.manage"],
        extra_resources=["billable:*"],
    )
    _seed_role(rt_url, o.tenant, o.second, "ADMIN", create=False)
    with TestClient(_app(rt_url, o)) as api:
        own = o.bearer(o.owner)
        body = {"grantee_principal_id": o.second, "bundle": "INVOICE_READER", "reason": "audit"}
        first = api.post("/v1/tenant/billing-grants", json=body, headers=own)
        again = api.post("/v1/tenant/billing-grants", json=body, headers=own)
        assert first.status_code == 201 and again.status_code == 201, (first.text, again.text)
        assert first.json()["grant_id"] == again.json()["grant_id"]
        assert _grants(rt_url, o.tenant) == 1

        different = api.post(
            "/v1/tenant/billing-grants",
            json={**body, "expires_at": "2099-01-01T00:00:00+00:00"},
            headers=own,
        )
        assert (
            different.status_code == 409
            and different.json()["error"]["code"] == "BILLING_GRANT_EXISTS"
        ), different.text
        assert _grants(rt_url, o.tenant) == 1

        # taking it away makes the next grant a new one, not the old one
        assert (
            api.post(
                f"/v1/tenant/billing-grants/{first.json()['grant_id']}:revoke", headers=own
            ).status_code
            == 200
        )
        renewed = api.post(
            "/v1/tenant/billing-grants",
            json={**body, "expires_at": "2099-01-01T00:00:00+00:00"},
            headers=own,
        )
        assert renewed.status_code == 201 and renewed.json()["grant_id"] != first.json()["grant_id"]
        assert _grants(rt_url, o.tenant) == 2


def test_a_replayed_or_forged_role_never_creates_authority(rt_url: str) -> None:
    """C00-15 and C00-06: a role is read from the tenant's records on every request. A token that carries claims nobody issued as roles, a token for a
    person with no role, and a revoked member's own token replayed all do nothing."""
    o = onboard(rt_url, extra_actions=["commercial.read"], extra_resources=["billable:*"])
    _seed_role(rt_url, o.tenant, o.second, "ADMIN", create=False)
    stranger = str(uuid.uuid4())
    with TestClient(_app(rt_url, o)) as api:
        own, adm = o.bearer(o.owner), o.bearer(o.second)
        assert (
            api.post("/v1/tenant/members", json={"role": "VIEWER"}, headers=adm).status_code == 201
        )  # the Administrator may invite a Viewer

        # a stranger holding a token for this tenant, whose scope claims every role by name, is still nobody: roles are not read from the token
        forged = {
            "Authorization": "Bearer "
            + issue_access_token(
                o.ring,
                issuer=ISS,
                audience=AUD,
                subject=stranger,
                tenant_id=o.tenant,
                client_id="onboarding",
                now=datetime.now(UTC),
                scope=frozenset(
                    {"owner", "admin", "tenant.member.manage", "role:OWNER", "tenant:admin"}
                ),
            )
        }
        r = api.post("/v1/tenant/members", json={"role": "VIEWER"}, headers=forged)
        assert r.status_code in (401, 403), r.text
        assert api.post(
            "/v1/tenant/members", json={"role": "ADMIN"}, headers=forged
        ).status_code in (401, 403)

        # the Administrator is removed; the same token, replayed, no longer does anything privileged
        members = api.get("/v1/tenant/members", headers=own).json()["items"]
        assignment = next(
            m["assignment_id"]
            for m in members
            if m["principal_id"] == o.second and m["status"] == "ACTIVE"
        )
        assert api.post(f"/v1/tenant/members/{assignment}:revoke", headers=own).status_code == 200
        replay = api.post("/v1/tenant/members", json={"role": "VIEWER"}, headers=adm)
        assert replay.status_code == 403, replay.text
