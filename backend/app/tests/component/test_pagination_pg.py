"""Keyset pagination stays stable while rows are inserted (ZID-API-001 API-17).

The authority-grant list is walked page by page while grants are inserted both ahead of and behind the cursor. Every
grant that existed when the walk began is returned exactly once; a grant inserted ahead of the cursor never shifts
the pages; a grant inserted behind it is seen once, in place. A cursor from another list or a forged one is refused.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from admin_fixture import make_admin
from conftest import CELL, ctx, engine
from fastapi.testclient import TestClient
from sqlalchemy import text
from zid_context import RequestContext
from zid_db import tenant_transaction
from zid_platform.api_auth import Caller, authenticated_caller
from zid_platform.main import create_app
from zid_platform.modules.decision.domain.pdp import AuthnFact
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import create_principal
from zid_platform.settings import Settings

READ = ["authority.grant.read"]
SCOPE = ["authority_grant:*"]


def uid() -> str:
    return str(uuid.uuid4())


def test_paging_never_repeats_or_skips_a_row_that_was_there_when_it_started(rt_url: str) -> None:
    """API-17: 12 grants, pages of 5, with 2 grants inserted ahead of the cursor and 2 behind it between pages."""
    tenant, caller_id = uid(), uid()
    first_batch = [caller_id, *(uid() for _ in range(11))]
    ahead_batch, behind_batch = [uid(), uid()], [uid(), uid()]
    real_now = datetime.now(UTC)

    async def add(
        grantees: list[str], *, now: datetime | None, release: bool, create: bool = True
    ) -> list[str]:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            if create:
                for p in grantees:
                    await create_principal(
                        c, Principal(p, tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
                    )
            await make_admin(c, tenant, grantees, READ, SCOPE, now=now, release=release)
            rows = await c.execute(
                text(
                    "SELECT authority_grant_id::text FROM authz.authority_grant WHERE grantee_principal_id = ANY(:g)"
                ),
                {"g": [uuid.UUID(g) for g in grantees]},
            )
            return sorted(rows.scalars().all())

    original = asyncio.run(add(first_batch, now=None, release=True))
    assert len(original) == 12
    app = create_app(Settings(database_url=rt_url))
    context = RequestContext(
        "req_t", "cor_t", "svc", CELL, tenant_id=tenant, principal_id=caller_id
    )
    app.dependency_overrides[authenticated_caller] = lambda: Caller(
        context, AuthnFact(3, datetime.now(UTC) - timedelta(minutes=1)), security_state="NORMAL"
    )

    def page(client: TestClient, **params: Any) -> dict[str, Any]:
        r = client.get("/v1/authority-grants", params={"limit": 5, **params})
        assert r.status_code == 200, r.text
        return dict(r.json())

    with TestClient(app) as client:
        first = page(client)
        assert len(first["items"]) == 5 and first["next_cursor"]
        seen = [g["authority_grant_id"] for g in first["items"]]
        # between pages: two grants newer than the cursor and two older than it arrive
        ahead = asyncio.run(add(ahead_batch, now=real_now + timedelta(minutes=10), release=False))
        behind = asyncio.run(add(behind_batch, now=real_now - timedelta(days=2), release=False))
        cursor = first["next_cursor"]
        while cursor:
            nxt = page(client, cursor=cursor)
            seen += [g["authority_grant_id"] for g in nxt["items"]]
            cursor = nxt["next_cursor"]
        assert len(seen) == len(set(seen)), "a row was returned twice"
        assert set(original) <= set(seen), "a row that existed when the walk began was skipped"
        assert not set(ahead) & set(seen)  # inserted ahead of the cursor: it cannot shift the pages
        assert set(behind) <= set(seen)  # inserted behind it: seen once, in its place
        # a fresh walk sees everything, newest first, with no repeats
        everything: list[dict[str, Any]] = []
        cursor = None
        while True:
            body = page(client, **({"cursor": cursor} if cursor else {}))
            everything += body["items"]
            cursor = body["next_cursor"]
            if not cursor:
                break
        ids = [g["authority_grant_id"] for g in everything]
        assert len(ids) == len(set(ids)) == 16
        issued = [g["valid_from"] for g in everything]  # moves with the issue time in this fixture
        assert issued == sorted(issued, reverse=True)

        # a cursor belongs to its list; a forged or foreign one is a 400, never a silent wrong page
        other = client.get(
            "/v1/authority-grants",
            params={"limit": 5, "status": "ACTIVE", "cursor": first["next_cursor"]},
        )
        assert other.status_code == 400 and other.json()["error"]["code"] == "INVALID_CURSOR"
        for bad in ("garbage", "e30", "WyJncmFudHN8KnwqIiwgIm5vdC1hLWRhdGUiLCAibm9wZSJd"):
            r = client.get("/v1/authority-grants", params={"cursor": bad})
            assert r.status_code == 400, bad
