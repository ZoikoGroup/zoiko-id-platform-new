"""Custom organization roles on a real database: they only narrow, only on Operator or Viewer, and are enforced where the role matrix decides."""

from __future__ import annotations

import asyncio
import uuid

from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, give_role, onboard
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import create_principal
from zid_platform.settings import Settings

BASE = "/v1/tenant/custom-roles"


def test_a_custom_role_narrows_an_operator_and_can_never_widen_or_reach_admin(rt_url: str) -> None:
    o = onboard(rt_url)
    operator = str(uuid.uuid4())

    async def seed() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            await create_principal(
                c,
                Principal(operator, o.tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1"),
            )

    asyncio.run(seed())
    give_role(rt_url, o, operator, "OPERATOR")
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_id="uk-1",
        ),
        keyring=o.ring,
    )
    owner, op = o.bearer(o.owner), o.bearer(operator)
    with TestClient(app) as api:
        catalogue = api.get(f"{BASE}/actions", params={"base_role": "OPERATOR"}, headers=owner)
        assert catalogue.status_code == 200
        held = {a["action_id"] for a in catalogue.json()["actions"]}
        assert (
            "a.view-organization-profile" in held and "a.approve-privileged-role-grant" not in held
        )
        assert (
            api.get(f"{BASE}/actions", params={"base_role": "ADMIN"}, headers=owner).status_code
            == 422
        )

        # refused: built on an administrative role, or naming what the base role lacks
        admin_based = api.post(
            BASE,
            json={
                "name": "Super",
                "base_role": "ADMIN",
                "actions": ["a.view-organization-profile"],
            },
            headers=owner,
        )
        assert (
            admin_based.status_code == 422
            and admin_based.json()["error"]["code"] == "CUSTOM_ROLE_BASE_REFUSED"
        )
        wider = api.post(
            BASE,
            json={
                "name": "Wider",
                "base_role": "OPERATOR",
                "actions": ["a.approve-privileged-role-grant"],
            },
            headers=owner,
        )
        assert wider.status_code == 422 and wider.json()["error"]["code"] == "CUSTOM_ROLE_WIDENS"

        # an Operator can list roles until a custom role narrows them
        assert api.get(BASE, headers=op).status_code == 200
        made = api.post(
            BASE,
            json={
                "name": "Profile reader",
                "base_role": "OPERATOR",
                "actions": ["a.view-organization-profile"],
            },
            headers=owner,
        )
        assert made.status_code == 201, made.text
        rid = made.json()["role_id"]
        dup = api.post(
            BASE,
            json={
                "name": "profile READER",
                "base_role": "OPERATOR",
                "actions": ["a.view-organization-profile"],
            },
            headers=owner,
        )
        assert dup.status_code == 409

        # the role needs the base role: the owner is not an Operator
        assert (
            api.post(
                f"{BASE}/{rid}/members", json={"principal_id": o.owner}, headers=owner
            ).status_code
            == 422
        )
        given = api.post(f"{BASE}/{rid}/members", json={"principal_id": operator}, headers=owner)
        assert given.status_code == 201, given.text
        assert (
            api.get(f"{BASE}/{rid}/members", headers=owner).json()["items"][0]["principal_id"]
            == operator
        )
        refused = api.get(BASE, headers=op)
        assert refused.status_code == 403
        assert any(
            d["issue"] == "CUSTOM_ROLE_DOES_NOT_INCLUDE" for d in refused.json()["error"]["details"]
        )
        assert api.get(BASE, headers=owner).json()["items"][0]["members"] == 1

        # taking it away, or retiring it, puts the Operator back to the plain role
        assert api.post(f"{BASE}/{rid}/members/{operator}:revoke", headers=owner).status_code == 200
        assert api.get(BASE, headers=op).status_code == 200
        assert (
            api.post(
                f"{BASE}/{rid}/members", json={"principal_id": operator}, headers=owner
            ).status_code
            == 201
        )
        assert api.get(BASE, headers=op).status_code == 403
        assert api.post(f"{BASE}/{rid}:retire", headers=owner).status_code == 200
        assert api.get(BASE, headers=op).status_code == 200


def test_an_extension_adds_reads_and_requests_only_and_never_approval_or_administration(
    rt_url: str,
) -> None:
    o = onboard(rt_url)
    viewer = str(uuid.uuid4())

    async def seed() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            await create_principal(
                c, Principal(viewer, o.tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
            )

    asyncio.run(seed())
    give_role(rt_url, o, viewer, "VIEWER")
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_id="uk-1",
        ),
        keyring=o.ring,
    )
    owner, vw = o.bearer(o.owner), o.bearer(viewer)
    with TestClient(app) as api:
        offered = {
            a["action_id"]
            for a in api.get(
                f"{BASE}/actions", params={"base_role": "VIEWER", "kind": "EXTEND"}, headers=owner
            ).json()["actions"]
        }
        assert (
            "a.list-active-members-and-assigned-roles" in offered
        )  # a Viewer cannot list members; an extension may add it
        assert (
            "a.view-organization-profile" not in offered
        )  # a Viewer already reads this: nothing to add
        assert not any(
            a.startswith(("a.approve", "d.approve")) for a in offered
        )  # approvals are never offered

        body = {"name": "Directory reader", "base_role": "VIEWER", "kind": "EXTEND"}
        approve = api.post(
            BASE, json={**body, "actions": ["a.approve-privileged-role-grant"]}, headers=owner
        )
        assert (
            approve.status_code == 422
            and approve.json()["error"]["code"] == "CUSTOM_ROLE_EXTENSION_REFUSED"
        )
        already = api.post(
            BASE, json={**body, "actions": ["a.view-organization-profile"]}, headers=owner
        )
        assert already.status_code == 422
        assert (
            api.post(
                BASE, json={**body, "actions": ["a.invite-operator-or-viewer"]}, headers=owner
            ).status_code
            == 422
        )  # administration (execute)

        assert api.get(BASE, headers=vw).status_code == 403  # a plain Viewer cannot list people
        made = api.post(
            BASE,
            json={**body, "actions": ["a.list-active-members-and-assigned-roles"]},
            headers=owner,
        )
        assert made.status_code == 201 and made.json()["kind"] == "EXTEND", made.text
        rid = made.json()["role_id"]
        assert (
            api.post(
                f"{BASE}/{rid}/members", json={"principal_id": viewer}, headers=owner
            ).status_code
            == 201
        )
        assert api.get(BASE, headers=vw).status_code == 200  # the extension adds the read
        # ...and only that read: creating a role is an execute, which an extension never adds
        assert (
            api.post(
                BASE,
                json={
                    "name": "Nope",
                    "base_role": "VIEWER",
                    "actions": ["a.view-organization-profile"],
                },
                headers=vw,
            ).status_code
            == 403
        )
        assert api.post(f"{BASE}/{rid}:retire", headers=owner).status_code == 200
        assert (
            api.get(BASE, headers=vw).status_code == 403
        )  # retiring it takes the read away at once
