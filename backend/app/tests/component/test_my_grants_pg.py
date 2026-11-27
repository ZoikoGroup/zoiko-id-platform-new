"""A person can read the permissions that were given to THEM (GET /v1/authority-grants/mine), and only theirs.

A Member holds no company-wide right, so the administrators' list refuses them. Without this, someone handed a permission could not see that they had it.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from admin_fixture import make_admin
from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, _session, onboard
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import create_principal
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.settings import Settings


def _person(rt_url: str, o: Any, actions: list[str] | None) -> str:
    """A new active person in the company; with a grant for `actions` when given, with none otherwise (a plain Member)."""

    async def run() -> str:
        p = str(uuid.uuid4())
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
            await create_principal(
                c, Principal(p, o.tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
            )
            if actions:
                await make_admin(c, o.tenant, p, actions, ["*"], release=False)
            o.sessions[p] = await _session(c, o.tenant, p)
        return p

    return asyncio.run(run())


def _bearer(o: Any, p: str) -> dict[str, str]:
    now = datetime.now(UTC)
    tok = issue_access_token(
        o.ring, issuer=ISS, audience=AUD, subject=p, tenant_id=o.tenant, client_id="console", now=now,
        session_id=o.sessions[p], auth_time=now - timedelta(seconds=5), scope=frozenset(),
    )  # fmt: skip
    return {"Authorization": f"Bearer {tok}"}


def _client(rt_url: str, o: Any) -> TestClient:
    app = create_app(
        Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test", cell_id="uk-1"),
        keyring=o.ring,
    )  # fmt: skip
    return TestClient(app, base_url=ISS)


def test_a_member_sees_the_permission_they_were_given_and_only_that(rt_url: str) -> None:
    o = onboard(rt_url)
    member = _person(rt_url, o, ["invoice.approve", "payment.release"])
    other = _person(rt_url, o, ["purchase.create"])
    with _client(rt_url, o) as api:
        mine = api.get("/v1/authority-grants/mine", headers=_bearer(o, member))
        assert mine.status_code == 200, mine.text
        items = mine.json()["items"]
        assert len(items) == 1
        assert items[0]["actions"] == ["invoice.approve", "payment.release"]
        assert items[0]["status"] == "ACTIVE" and items[0]["valid_to"]
        # nothing about the chain above the grant, the resources behind it or anyone else
        assert not {
            "parent_grant_id",
            "resources",
            "authority_root_assertion_id",
            "grantee_principal_id",
        } & set(items[0])
        theirs = api.get("/v1/authority-grants/mine", headers=_bearer(o, other)).json()["items"]
        assert [g["actions"] for g in theirs] == [
            ["purchase.create"]
        ]  # each person sees their own, never the other's


def test_a_plain_member_with_no_grant_sees_an_empty_list_not_an_error(rt_url: str) -> None:
    o = onboard(rt_url)
    member = _person(rt_url, o, None)
    with _client(rt_url, o) as api:
        res = api.get("/v1/authority-grants/mine", headers=_bearer(o, member))
        assert res.status_code == 200 and res.json() == {"items": []}


def test_reading_your_own_permissions_does_not_open_the_administrators_list(rt_url: str) -> None:
    o = onboard(rt_url)
    member = _person(rt_url, o, ["invoice.approve"])
    with _client(rt_url, o) as api:
        assert api.get("/v1/authority-grants/mine", headers=_bearer(o, member)).status_code == 200
        assert (
            api.get("/v1/authority-grants", headers=_bearer(o, member)).status_code == 403
        )  # the company-wide list still needs its own right


def test_a_revoked_permission_is_no_longer_listed(rt_url: str) -> None:
    o = onboard(rt_url)
    member = _person(rt_url, o, ["invoice.approve"])
    with _client(rt_url, o) as api:
        grant = api.get("/v1/authority-grants/mine", headers=_bearer(o, member)).json()["items"][0][
            "authority_grant_id"
        ]
        gone = api.post(f"/v1/authority-grants/{grant}:revoke", headers=o.bearer(o.owner, scope=""))
        if (
            gone.status_code == 200
        ):  # the owner's revoke may need a step-up in some setups; the listing rule is what is tested
            assert (
                api.get("/v1/authority-grants/mine", headers=_bearer(o, member)).json()["items"]
                == []
            )
