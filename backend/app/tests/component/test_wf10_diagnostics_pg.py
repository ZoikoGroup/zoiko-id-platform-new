"""The partner request log, the trace of one verification and webhook endpoint health, against PostgreSQL (ZID-CP-WF10 v2.0, WF-10.09, WF-10.10; CR-173).

What these hold: each authenticated partner call leaves one row with a route template, status, error code and trace id and nothing else; a company sees only its own rows; the trace
follows one real session and lists the links it does not have; endpoint health is read from real delivery rows; the log is append-only.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from test_verify_api_sessions_pg import World, uid
from zid_db import tenant_transaction
from zid_platform.modules.verify_api.application import diagnostics_service as svc
from zid_platform.modules.verify_api.domain.model import PartnerError


def run(rt_url: str, tenant: str, fn: Any) -> Any:
    async def go() -> Any:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            return await fn(c)

    return asyncio.run(go())


async def delivery(
    owner_url: str,
    tenant: str,
    endpoint: uuid.UUID,
    *,
    status: str,
    code: int | None = None,
    error: str | None = None,
    session_id: str | None = None,
) -> str:
    event = uuid.uuid4()
    payload = f'{{"data": {{"session_id": "{session_id}"}}}}' if session_id else "{}"
    async with engine(owner_url) as eng, eng.begin() as c:
        await c.execute(
            text(
                "INSERT INTO events.webhook_delivery (delivery_id, tenant_id, endpoint_id, event_id, event_type, payload, payload_digest, status, last_status_code, last_error, attempts) VALUES (:d, :t, :e, :ev, 'verification.api.session_decided', CAST(:p AS jsonb), 'x', :s, :c, :er, 1)"
            ),
            {
                "d": uuid.uuid4(),
                "t": uuid.UUID(tenant),
                "e": endpoint,
                "ev": event,
                "p": payload,
                "s": status,
                "c": code,
                "er": error,
            },
        )
    return str(event)


async def endpoint(owner_url: str, tenant: str) -> uuid.UUID:
    eid = uuid.uuid4()
    async with engine(owner_url) as eng, eng.begin() as c:
        await c.execute(
            text(
                "INSERT INTO events.webhook_endpoint (endpoint_id, tenant_id, url, event_types, status, signing_key_id, created_by) VALUES (:e, :t, 'https://hooks.example.com/private/path?token=abc', ARRAY['verification.api.session_decided'], 'ACTIVE', 'k1', :u)"
            ),
            {"e": eid, "t": uuid.UUID(tenant), "u": uuid.uuid4()},
        )
    return eid


def test_each_partner_call_leaves_one_redacted_row_a_company_sees_only_its_own_and_the_log_is_append_only(
    rt_url: str, owner_url: str
) -> None:
    w = World(rt_url)
    try:
        h = w.server()
        info = w.http.get("/v1/verify-api/whoami", headers=h).json()
        app_id = info["application_id"]
        created = w.create(h)
        assert created.status_code == 201
        sid = created.json()["session_id"]
        assert (
            w.http.get(f"/v1/verify-api/verification-sessions/{sid}", headers=h).status_code == 200
        )
        missing = w.http.get("/v1/verify-api/verification-sessions/vfs_does_not_exist", headers=h)
        assert missing.status_code == 404
        assert (
            w.http.get(
                "/v1/verify-api/whoami", headers={"Authorization": "Bearer zat_wrong"}
            ).status_code
            == 401
        )  # no application is known: nothing is logged

        out = run(
            rt_url,
            w.tenant,
            lambda c: svc.requests(c, app_id, status=None, route=None, before=None, limit=50),
        )
        rows = out["items"]
        routes = {(r["method"], r["route"], r["status"]) for r in rows}
        assert ("GET", "/v1/verify-api/whoami", 200) in routes and (
            "POST",
            "/v1/verify-api/verification-sessions",
            201,
        ) in routes
        assert any(
            r["route"].endswith("{session_id}") and r["status"] == 200 for r in rows
        )  # a template, never the path with the real id
        not_found = next(r for r in rows if r["status"] == 404)
        assert not_found["error_code"] == "NOT_FOUND" and not_found["trace_id"]
        for r in rows:
            assert (
                sid not in r["route"] and "Bearer" not in str(r) and "customer-48201" not in str(r)
            ), (
                r
            )  # the route is a template, and no token or customer reference reaches the log (the opaque session id is a column, on purpose, so the trace can find it)
        assert out["items"] == sorted(out["items"], key=lambda r: r["occurred_at"], reverse=True)
        only404 = run(
            rt_url,
            w.tenant,
            lambda c: svc.requests(c, app_id, status=404, route=None, before=None, limit=50),
        )["items"]
        assert only404 and all(r["status"] == 404 for r in only404)
        assert run(
            rt_url,
            w.tenant,
            lambda c: svc.requests(
                c, app_id, status=None, route="/v1/verify-api/whoami", before=None, limit=50
            ),
        )["items"]

        async def bad(c: Any) -> None:
            with pytest.raises(PartnerError) as e:
                await svc.requests(c, app_id, status=700, route=None, before=None, limit=50)
            assert e.value.code == "VALIDATION_FAILED"
            with pytest.raises(PartnerError):
                await svc.requests(
                    c, str(uuid.uuid4()), status=None, route=None, before=None, limit=50
                )

        run(rt_url, w.tenant, bad)

        # another company sees none of it
        async def other(c: Any) -> int:
            return int(
                (await c.execute(text("SELECT count(*) FROM verify.api_request_log"))).scalar_one()
            )

        assert run(rt_url, uid(), other) == 0

        async def edit() -> None:
            async with engine(owner_url) as eng, eng.begin() as c:
                await c.execute(text("UPDATE verify.api_request_log SET status = 200"))

        with pytest.raises(Exception, match="append-only"):
            asyncio.run(edit())
    finally:
        w.close()


def test_the_trace_follows_one_session_through_requests_and_deliveries_and_lists_what_is_missing(
    rt_url: str, owner_url: str
) -> None:
    w = World(rt_url)
    try:
        h = w.server()
        sid = w.create(h).json()["session_id"]
        eid = asyncio.run(endpoint(owner_url, w.tenant))
        event = asyncio.run(
            delivery(owner_url, w.tenant, eid, status="DELIVERED", code=200, session_id=sid)
        )
        t = run(rt_url, w.tenant, lambda c: svc.trace(c, sid))
        kinds = [s["kind"] for s in t["steps"]]
        assert (
            t["found"] is True
            and "API_REQUEST" in kinds
            and "WEBHOOK_DELIVERY" in kinds
            and t["missing_links"] == ["ROUTE_DECISION", "DECISION"]
        )
        assert next(s for s in t["steps"] if s["kind"] == "WEBHOOK_DELIVERY")["id"] == event
        assert "customer-48201" not in str(t)

        async def unknown(c: Any) -> None:
            with pytest.raises(PartnerError) as e:
                await svc.trace(c, "vfs_nope")
            assert e.value.code == "NOT_FOUND"

        run(rt_url, w.tenant, unknown)

        async def foreign(c: Any) -> None:
            with pytest.raises(PartnerError):
                await svc.trace(c, sid)  # another company cannot trace this session

        run(rt_url, uid(), foreign)
    finally:
        w.close()


def test_endpoint_health_is_read_from_real_deliveries_shows_the_host_only_and_never_calls_an_untried_endpoint_healthy(
    rt_url: str, owner_url: str
) -> None:
    tenant = uid()
    good, bad, fresh = (asyncio.run(endpoint(owner_url, tenant)) for _ in range(3))

    async def seed() -> None:
        for _ in range(4):
            await delivery(owner_url, tenant, good, status="DELIVERED", code=200)
        for _ in range(5):
            await delivery(owner_url, tenant, bad, status="RETRY_SCHEDULED", code=401)
        await delivery(owner_url, tenant, fresh, status="QUEUED")

    asyncio.run(seed())
    out = run(rt_url, tenant, lambda c: svc.endpoints(c))
    by = {i["endpoint_id"]: i for i in out["items"]}
    assert (
        by[str(good)]["verdict"] == "HEALTHY"
        and by[str(bad)]["verdict"] == "FAILING"
        and by[str(bad)]["main_problem"] == "REJECTED_BY_ENDPOINT"
        and by[str(fresh)]["verdict"] == "UNKNOWN"
    )
    assert (
        by[str(good)]["host"] == "hooks.example.com"
        and "token=abc" not in str(out)
        and "/private" not in str(out)
    )  # the secret-bearing path and query never leave the platform here
    assert (
        run(rt_url, uid(), lambda c: svc.endpoints(c))["items"] == []
    )  # another company sees none of these
    _ = datetime
