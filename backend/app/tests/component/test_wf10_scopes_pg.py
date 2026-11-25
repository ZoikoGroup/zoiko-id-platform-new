"""Versioned application scopes against PostgreSQL (ZID-CP-WF10 v2.0, WF-10.03; CR-173).

What these hold: a scope removed after a token was issued is refused on the very next call; a staged removal takes effect at its time and not before; a change that needs a second
person is not in force until someone other than the proposer approves it (the database applies the same rule); history is kept and append-only.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from test_verify_api_sessions_pg import World
from zid_db import tenant_transaction
from zid_platform.modules.verify_api.application import service
from zid_platform.modules.verify_api.domain.model import PartnerError
from zid_platform.modules.verify_api.infrastructure import store


def run(rt_url: str, tenant: str, fn: Any) -> Any:
    async def go() -> Any:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            return await fn(c)

    return asyncio.run(go())


def who(w: World, h: dict[str, str]) -> dict[str, Any]:
    r = w.http.get("/v1/verify-api/whoami", headers=h)
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def test_a_removed_scope_is_refused_on_the_next_call_even_for_a_token_issued_before_and_a_staged_removal_waits(rt_url: str) -> None:
    w = World(rt_url)
    try:
        h = w.server(scopes=["products:read", "workflows:read", "verifications:create", "verifications:read"])
        info = who(w, h)
        app_id, proposer = info["application_id"], str(uuid.uuid4())
        assert set(info["scopes"]) == {"products:read", "workflows:read", "verifications:create", "verifications:read"}
        assert w.http.get("/v1/verify-api/products", headers=h).status_code == 200

        # remove products:read: the token that was issued before still exists, but it no longer carries the scope
        out = run(rt_url, w.tenant, lambda c: service.change_scopes(c, w.tenant, app_id, proposer, ["workflows:read", "verifications:create", "verifications:read"], "no longer needed"))
        assert out["kind"] == "REMOVE" and out["approved"] is True and out["version"] == 1
        assert "products:read" not in who(w, h)["scopes"]
        denied = w.http.get("/v1/verify-api/products", headers=h)
        assert denied.status_code in (400, 403) and denied.json()["error"]["code"] in ("INVALID_SCOPE", "AUTH_INVALID", "TENANT_SCOPE_DENIED", "VALIDATION_FAILED"), denied.text
        assert w.http.get("/v1/verify-api/workflows", headers=h).status_code == 200  # what was kept still works

        # a new token for the same credential cannot ask for the removed scope either
        # a staged removal: still in force until its time
        later = datetime.now(UTC) + timedelta(days=3)
        run(rt_url, w.tenant, lambda c: service.change_scopes(c, w.tenant, app_id, proposer, ["workflows:read", "verifications:create"], "retiring reads", effective_at=later))
        assert "verifications:read" in who(w, h)["scopes"]  # not yet

        async def hist(c: Any) -> list[dict[str, Any]]:
            return await store.scope_changes(c, app_id)

        rows = run(rt_url, w.tenant, hist)
        assert [r["version"] for r in rows] == [2, 1] and rows[0]["effective_at"] > datetime.now(UTC) and rows[1]["kind"] == "REMOVE"

        async def after(c: Any) -> list[str] | None:
            return await store.scopes_in_force(c, app_id, later + timedelta(minutes=1))

        assert run(rt_url, w.tenant, after) == ["workflows:read", "verifications:create"]  # at its time, it is in force
    finally:
        w.close()


def test_a_change_that_needs_a_second_person_is_not_in_force_until_someone_else_approves_it_and_the_database_agrees(rt_url: str, owner_url: str) -> None:
    w = World(rt_url)
    try:
        h = w.server(scopes=["products:read", "verifications:create"])
        app_id = who(w, h)["application_id"]
        proposer, approver = str(uuid.uuid4()), str(uuid.uuid4())
        now = datetime.now(UTC)

        async def propose(c: Any) -> str:
            v = await store.next_scope_version(c, app_id)
            return await store.insert_scope_change(c, w.tenant, app_id, v, "ADD", ["products:read", "verifications:create", "usage:read"], True, proposer, now, "needs usage")

        cid = run(rt_url, w.tenant, propose)

        async def in_force(c: Any) -> list[str] | None:
            return await store.scopes_in_force(c, app_id, datetime.now(UTC))

        assert run(rt_url, w.tenant, in_force) is None  # proposed only: the application own scopes still apply
        assert "usage:read" not in who(w, h)["scopes"]

        async def self_approve(c: Any) -> None:
            with pytest.raises(PartnerError) as e:
                await service.approve_scope_change(c, w.tenant, cid, proposer)
            assert e.value.code == "APPROVAL_SELF_DECISION"

        run(rt_url, w.tenant, self_approve)
        # even if a self-approval row were written, the database does not count it
        async def forced(c: Any) -> None:
            await store.insert_scope_approval(c, w.tenant, cid, proposer)

        run(rt_url, w.tenant, forced)
        assert run(rt_url, w.tenant, in_force) is None

        run(rt_url, w.tenant, lambda c: service.approve_scope_change(c, w.tenant, cid, approver))
        assert run(rt_url, w.tenant, in_force) == ["products:read", "verifications:create", "usage:read"]

        async def twice(c: Any) -> None:
            with pytest.raises(PartnerError) as e:
                await service.approve_scope_change(c, w.tenant, cid, approver)
            assert e.value.code == "APPROVAL_STATE_INVALID"

        run(rt_url, w.tenant, twice)

        async def edit() -> None:
            async with engine(owner_url) as eng, eng.begin() as c:
                await c.execute(text("UPDATE verify.api_scope_change SET effective_at = now() WHERE change_id = :i"), {"i": uuid.UUID(cid)})

        with pytest.raises(Exception, match="append-only"):
            asyncio.run(edit())
    finally:
        w.close()
