"""The sandbox try-it runner against PostgreSQL (ZID-CP-WF10 v2.0, WF-10.05; CR-173): it can run only against a sandbox application, only with the scopes that application holds, and never reaches production."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from test_verify_api_sessions_pg import ORIGIN, World
from zid_db import tenant_transaction
from zid_platform.modules.verify_api.application import service, try_it
from zid_platform.modules.verify_api.domain.model import PartnerError


def run(rt_url: str, tenant: str, fn: Any) -> Any:
    async def go() -> Any:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            return await fn(c)

    return asyncio.run(go())


def test_the_runner_shows_the_exact_request_and_answer_in_the_sandbox_and_refuses_what_the_application_could_not_do(rt_url: str) -> None:
    w = World(rt_url)
    try:
        h = w.server(scopes=["products:read", "workflows:read", "verifications:create"])
        app_id = w.http.get("/v1/verify-api/whoami", headers=h).json()["application_id"]

        def go(op: str, **kw: Any) -> Any:
            return run(rt_url, w.tenant, lambda c: try_it.run(c, w.tenant, app_id, op, body=kw.get("body"), idempotency_key=kw.get("key"), origin=ORIGIN))

        me = go("whoami")
        assert me["response"]["environment"] == "sandbox" and set(me["response"]["scopes"]) == {"products:read", "workflows:read", "verifications:create"} and me["sandbox"] is True
        assert go("products")["response"]["items"] and go("workflows")["request"]["path"] == "/v1/verify-api/workflows"
        created = go("create_session", key="try-it-key-0001")
        assert created["status"] == 201 and created["request"]["method"] == "POST" and created["request"]["headers"]["Idempotency-Key"] == "try-it-key-0001" and created["response"]["mode"] == "sandbox"
        assert created["request"]["headers"]["Authorization"] == "Bearer <your access token>"  # a real token is never shown or used
        again = go("create_session", key="try-it-key-0001")
        assert again["status"] == 200 and again["response"]["session_id"] == created["response"]["session_id"]  # the same key is the same session

        # the application never held verifications:list, so the runner cannot do it either
        with pytest.raises(PartnerError) as e:
            go("list_sessions")
        assert e.value.code in ("INVALID_SCOPE", "TENANT_SCOPE_DENIED", "AUTH_INVALID")
        with pytest.raises(PartnerError) as e2:
            go("delete_everything")
        assert e2.value.code == "VALIDATION_FAILED"
        with pytest.raises(PartnerError) as e3:
            run(rt_url, w.tenant, lambda c: try_it.run(c, w.tenant, str(uuid.uuid4()), "whoami", body=None, idempotency_key=None, origin=ORIGIN))
        assert e3.value.code == "NOT_FOUND"

        # a scope removed after creation narrows the runner as well
        run(rt_url, w.tenant, lambda c: service.change_scopes(c, w.tenant, app_id, str(uuid.uuid4()), ["workflows:read", "verifications:create"], "try-it narrowing"))
        with pytest.raises(PartnerError):
            go("products")
    finally:
        w.close()


def test_a_production_application_is_never_run_from_here(rt_url: str, owner_url: str) -> None:
    tenant, app_id = str(uuid.uuid4()), uuid.uuid4()

    async def seed() -> None:
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text("INSERT INTO verify.api_application (application_id, tenant_id, name, environment, status, scopes, redirect_origins, created_by) VALUES (:a, :t, 'prod-app', 'PRODUCTION', 'ACTIVE', ARRAY['products:read'], ARRAY[]::text[], :u)"),
                {"a": app_id, "t": uuid.UUID(tenant), "u": uuid.uuid4()},
            )

    asyncio.run(seed())

    async def go(c: Any) -> None:
        with pytest.raises(PartnerError) as e:
            await try_it.run(c, tenant, str(app_id), "products", body=None, idempotency_key=None, origin=ORIGIN)
        assert e.value.code == "ENVIRONMENT_MISMATCH" and "never called from here" in e.value.detail

    run(rt_url, tenant, go)
